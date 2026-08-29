"""
Fact extraction into Channel A.

Turns what a caller said into candidate risk factors. Everything produced here
is a PROPOSAL, marked `FactSource.EXTRACTED`, and it counts at a discount until
the intake agent reads it back and the caller confirms it. That is not
timidity: Channel A is the heaviest-weighted channel in the SVI, and a factor
inferred from a misheard phrase would move a risk tier on the strength of an
ASR error.

Extraction is cue-based rather than learned, for the same reasons the VAD is:
there is no labelled corpus of NHAA calls to train on, a rule that fired can be
shown to a counsellor and argued with, and a rule that misfires can be fixed in
an afternoon by the person who noticed. When labelled data exists from the
shadow-mode pilot, `MODEL_EXTRACTORS` is where a learned extractor joins — and
it will be evaluated against these rules, not assumed to beat them.

Recall is deliberately incomplete. The intake agent asks about every core
factor explicitly regardless of what was extracted, so a missed cue costs a
question, not a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from core.events import Language
from services.nlp.lexicon import normalise, tokenise

# Cue patterns per factor. Written as alternations of phrases people actually
# use rather than the statutory vocabulary, because callers describe what
# happened, not which sub-clause it falls under.
FACT_CUES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "hi": {
        "accused_at_large_nearby": (
            r"अभी\s+भी\s+(?:गाँव|गांव|यहीं|आस\s*पास)",
            r"(?:गाँव|गांव)\s+में\s+ही\s+(?:है|हैं|रहत)",
            r"खुले\s+आम\s+घूम", r"जमानत\s+पर\s+(?:है|हैं|छूट)",
            r"पास\s+ही\s+रहत", r"बगल\s+में\s+ही",
        ),
        "prior_threats": (
            r"पहले\s+भी\s+धमक", r"कई\s+बार\s+धमक", r"बार\s+बार\s+धमक",
            r"लगातार\s+धमक", r"पहले\s+से\s+धमक",
        ),
        "threat_imminent": (
            r"अभी\s+मार", r"आज\s+ही\s+मार", r"जान\s+से\s+मार\s+देंगे",
            r"अभी\s+आ\s+रहे", r"घर\s+के\s+बाहर\s+खड़े", r"घेर\s+लिया",
        ),
        "fir_not_registered": (
            r"(?:एफ\.?आई\.?आर|प्राथमिकी|रिपोर्ट)\s+(?:दर्ज\s+)?नहीं",
            r"मामला\s+दर्ज\s+नहीं", r"कोई\s+कार्रवाई\s+नहीं",
        ),
        "police_refused_registration": (
            r"पुलिस\s+ने\s+(?:मना|इनकार|भगा)", r"थाने\s+से\s+(?:भगा|लौटा)",
            r"रिपोर्ट\s+लिखने\s+से\s+(?:मना|इनकार)", r"पुलिस\s+सुनती\s+नहीं",
        ),
        "social_boycott_active": (
            r"बहिष्कार", r"हुक्का\s+पानी\s+बंद", r"कोई\s+बात\s+नहीं\s+करता",
            r"दुकान\s+से\s+सामान\s+नहीं", r"कुएँ\s+से\s+पानी\s+नहीं",
            r"गाँव\s+से\s+अलग",
        ),
        "economic_blockade": (
            r"काम\s+पर\s+नहीं\s+(?:बुला|रख)", r"मजदूरी\s+नहीं\s+दे",
            r"मज़दूरी\s+नहीं\s+दे", r"खेत\s+में\s+काम\s+नहीं",
            r"रोज़गार\s+छीन", r"रोजगार\s+छीन",
        ),
        "displaced_from_home": (
            r"घर\s+छोड़", r"गाँव\s+छोड़", r"भाग\s+कर\s+(?:आ|रह)",
            r"रिश्तेदार\s+के\s+यहाँ\s+रह", r"बेघर",
        ),
        "sole_earner_lost": (
            r"कमाने\s+वाला\s+(?:कोई\s+)?नहीं", r"अकेला\s+कमाने\s+वाला",
            r"पति\s+की\s+(?:मौत|हत्या)", r"बेटे\s+की\s+(?:मौत|हत्या)",
        ),
        "victim_minor": (
            r"नाबालिग", r"बच्ची\s+है", r"बच्चा\s+है",
            r"(?:उम्र|साल)\s+(?:1[0-7]|[1-9])\s*साल",
        ),
        "victim_pregnant": (r"गर्भवती", r"पेट\s+से\s+है", r"उम्मीद\s+से\s+है"),
        "victim_disabled": (r"दिव्यांग", r"विकलांग", r"चल\s+नहीं\s+सकत",
                            r"देख\s+नहीं\s+सकत", r"सुन\s+नहीं\s+सकत"),
        "witness_pressure": (
            r"गवाही\s+(?:मत|ना|नहीं)\s+दे", r"गवाह\s+को\s+धमक",
            r"शिकायत\s+वापस", r"केस\s+वापस\s+ले", r"समझौता\s+कर\s+लो",
        ),
        "no_family_support": (
            r"कोई\s+साथ\s+नहीं", r"परिवार\s+भी\s+नहीं", r"अकेल[ाी]\s+(?:हूँ|पड़)",
        ),
        "multiple_victims": (
            r"हम\s+सब", r"कई\s+लोग(?:ों)?\s+(?:को|के\s+साथ)", r"पूरे\s+परिवार",
        ),
        "prior_complaint_same_accused": (
            r"पहले\s+भी\s+शिकायत", r"पिछली\s+बार\s+भी", r"पहले\s+भी\s+केस",
        ),
    },
    "bho": {
        "accused_at_large_nearby": (
            r"अबहियो\s+(?:गाँव|गांव)\s+में", r"इहँ?ई\s+बा", r"खुलेआम\s+घूमत",
            r"लगे\s+ही\s+रहेला",
        ),
        "prior_threats": (r"पहिलहूँ\s+धमक", r"कई\s+बेर\s+धमक", r"बेर\s+बेर\s+धमक"),
        "threat_imminent": (
            r"अभहीं\s+मार", r"जान\s+से\s+मार\s+देब", r"मारे\s+आ\s+रहल",
            r"घर\s+के\s+बाहर\s+खड़ा\s+बाड़े",
        ),
        "fir_not_registered": (
            r"(?:एफ\.?आई\.?आर|रिपोर्ट)\s+ना\s+(?:लिखल|दर्ज)",
            r"मुकदमा\s+ना\s+दर्ज", r"कवनो\s+कार्रवाई\s+ना",
        ),
        "police_refused_registration": (
            r"पुलिस\s+मना\s+क", r"थाना\s+से\s+भगा", r"रिपोर्ट\s+ना\s+लिखलस",
        ),
        "social_boycott_active": (
            r"बहिष्कार", r"हुक्का\s+पानी\s+बंद", r"केहू\s+बात\s+ना\s+करे",
            r"दुकान\s+से\s+समान\s+ना",
        ),
        "economic_blockade": (r"काम\s+पर\s+ना\s+बोलावे", r"मजूरी\s+ना\s+देले",
                              r"खेत\s+में\s+काम\s+ना"),
        "displaced_from_home": (r"घर\s+छोड़", r"गाँव\s+छोड़", r"भाग\s+के\s+आइल"),
        "sole_earner_lost": (r"कमाए\s+वाला\s+केहू\s+नइखे", r"मरद\s+के\s+मौत"),
        "victim_minor": (r"नाबालिग", r"लइकी\s+बिया", r"लइका\s+बा"),
        "victim_pregnant": (r"गरभवती", r"पेट\s+से\s+बिया"),
        "victim_disabled": (r"दिव्यांग", r"विकलांग", r"चल\s+ना\s+सकेला"),
        "witness_pressure": (r"गवाही\s+मत\s+दिह", r"शिकायत\s+वापस",
                             r"समझौता\s+कर\s+ल"),
        "no_family_support": (r"केहू\s+साथ\s+नइखे", r"अकेले\s+पड़"),
        "multiple_victims": (r"हम\s+सब", r"पूरा\s+परिवार"),
        "prior_complaint_same_accused": (r"पहिलहूँ\s+शिकायत", r"पहिले\s+भी\s+केस"),
    },
}

# Offence category cues. Exactly one category applies, so the most severe match
# wins rather than the first — a caller describing a murder and also mentioning
# abuse must not be filed under abuse.
OFFENCE_CUES: Dict[str, Tuple[str, ...]] = {
    "murder": (r"हत्या", r"मार\s+दिया", r"मार\s+दिहल", r"जान\s+ले\s+लिया", r"क़त्ल"),
    "gang_rape": (r"सामूहिक\s+बलात्कार", r"कई\s+लोगों\s+ने\s+बलात्कार"),
    "rape": (r"बलात्कार", r"रेप", r"इज्जत\s+लूट", r"इज़्ज़त\s+लूट"),
    "sexual_assault": (r"छेड़छाड़", r"गलत\s+नीयत\s+से\s+छुआ", r"यौन\s+उत्पीड़न"),
    "grievous_hurt": (r"गंभीर\s+चोट", r"हड्डी\s+तोड़", r"बुरी\s+तरह\s+पीट",
                      r"अस्पताल\s+में\s+भर्ती"),
    "arson_property_destruction": (r"आग\s+लगा", r"घर\s+जला", r"फसल\s+जला",
                                   r"तोड़\s+फोड़"),
    "land_dispossession": (r"जमीन\s+पर\s+कब्ज", r"ज़मीन\s+पर\s+कब्ज़",
                           r"खेत\s+पर\s+कब्ज"),
    "social_boycott": (r"बहिष्कार", r"हुक्का\s+पानी\s+बंद"),
    "wrongful_confinement": (r"बंधक\s+बना", r"बंद\s+कर\s+दिया", r"कैद\s+कर"),
    "intimidation_threat": (r"धमक", r"डरा\s+रहे", r"धमकावत"),
    "public_humiliation": (r"सबके\s+सामने\s+अपमान", r"बेइज्जत", r"जुलूस\s+निकाल"),
    "verbal_abuse_caste_slur": (r"जाति\s+सूचक", r"जातिसूचक", r"गाली\s+दिया",
                                r"जात\s+के\s+नाम\s+पर"),
    "denial_of_access": (r"मंदिर\s+में\s+नहीं\s+घुसने", r"पानी\s+नहीं\s+लेने",
                         r"अंदर\s+नहीं\s+आने"),
}

# Severity ordering for offence resolution, mirroring core.svi.factors.
OFFENCE_PRIORITY = (
    "murder", "gang_rape", "rape", "grievous_hurt", "sexual_assault",
    "arson_property_destruction", "land_dispossession", "social_boycott",
    "wrongful_confinement", "intimidation_threat", "public_humiliation",
    "verbal_abuse_caste_slur", "denial_of_access",
)

# A single cue match is evidence, not proof. Confidence rises with corroboration
# because two independent phrasings of the same factor are much less likely to
# both be ASR errors than one is.
BASE_CONFIDENCE = 0.55
CORROBORATION_BONUS = 0.2
MAX_CONFIDENCE = 0.9

MODEL_EXTRACTORS: Tuple = ()      # where a learned extractor joins, post-pilot


@dataclass(frozen=True)
class ExtractedFact:
    key: str
    confidence: float
    evidence: Tuple[str, ...]

    @property
    def corroborated(self) -> bool:
        return len(self.evidence) > 1


@dataclass(frozen=True)
class Extraction:
    facts: Tuple[ExtractedFact, ...]
    offence_category: Optional[str]
    offence_evidence: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.facts)

    def as_dict(self) -> Dict[str, object]:
        return {
            "facts": {f.key: round(f.confidence, 3) for f in self.facts},
            "offence_category": self.offence_category,
        }


def _compile(cues: Dict[str, Tuple[str, ...]]) -> Dict[str, Tuple[re.Pattern, ...]]:
    return {key: tuple(re.compile(p) for p in patterns) for key, patterns in cues.items()}


_COMPILED_FACTS = {lang: _compile(cues) for lang, cues in FACT_CUES.items()}
_COMPILED_OFFENCES = _compile(OFFENCE_CUES)


def extract(text: str, language: Language) -> Extraction:
    """Propose Channel A facts from a caller's narrative.

    Everything returned is unconfirmed. `ContextFacts` counts unconfirmed
    factors at a discount, and the intake agent reads the tier-relevant ones
    back to the caller before they count fully.
    """
    normalised = normalise(text)
    if not normalised:
        return Extraction(facts=(), offence_category=None)

    facts: List[ExtractedFact] = []
    for key, patterns in _COMPILED_FACTS.get(language.value, {}).items():
        matches = [p.pattern for p in patterns if p.search(normalised)]
        if not matches:
            continue
        confidence = min(MAX_CONFIDENCE,
                         BASE_CONFIDENCE + CORROBORATION_BONUS * (len(matches) - 1))
        facts.append(ExtractedFact(key=key, confidence=confidence,
                                   evidence=tuple(matches)))

    offence, evidence = _resolve_offence(normalised)
    return Extraction(facts=tuple(sorted(facts, key=lambda f: -f.confidence)),
                      offence_category=offence, offence_evidence=evidence)


def _resolve_offence(text: str) -> Tuple[Optional[str], Tuple[str, ...]]:
    """Exactly one offence category applies. Where several match, the most
    severe wins: a caller describing a murder who also mentions caste abuse
    must not be filed under abuse."""
    matched: Dict[str, List[str]] = {}
    for category, patterns in _COMPILED_OFFENCES.items():
        hits = [p.pattern for p in patterns if p.search(text)]
        if hits:
            matched[category] = hits
    if not matched:
        return None, ()
    for category in OFFENCE_PRIORITY:
        if category in matched:
            return category, tuple(matched[category])
    return None, ()
