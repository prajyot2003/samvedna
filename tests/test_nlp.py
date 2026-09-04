"""
NLP layer tests.

The redaction tests are privacy tests: each one is a category of identifier
that must not reach storage. The lexicon tests are safety tests: each one
covers a path that can escalate an interaction to CRITICAL. They are written
in that spirit rather than as coverage.
"""

from __future__ import annotations

import pytest

from core.events import Confidence, FactSource, Language, Tier, tier_rank
from core.rules.hard_rules import TriageState, apply_hard_rules
from core.svi.engine import MAX_C_DELTA, ModelSignals, compute_svi
from core.svi.factors import CORE_COVERAGE_KEYS, FACTORS_BY_KEY, OFFENCE_SEVERITY, ContextFacts
from core.svi.instruments import CSSRSScreen, Screeners
from services.audio.vad import ConversationalFeatures
from services.nlp import distress, facts, redaction
from services.nlp.lexicon import LEXICON_DIR as LEXICON_SOURCE
from services.nlp.lexicon import (RULE_CATEGORIES, analyse, load_lexicon, normalise,
                                  production_ready, tokenise)

ASKED = set(CORE_COVERAGE_KEYS)


# ------------------------------------------------- redaction

@pytest.mark.parametrize("text,label", [
    ("मेरा नंबर 9876543210 है", "PHONE"),
    ("फोन +91 9876543210 पर", "PHONE"),
    ("आधार 2345 6789 0123", "AADHAAR"),
    ("मेल ram@example.org पर", "EMAIL"),
    ("FIR संख्या 145/2026", "FIR"),
    ("case number 4471", "CASE"),
    ("PAN ABCDE1234F", "PAN"),
    ("खाता 123456789012345", "ACCOUNT"),
])
def test_structured_identifiers_are_redacted(text, label):
    result = redaction.redact(text)
    assert label in result.counts_by_label()
    assert f"[{label}]" in result.text


def test_devanagari_numerals_are_caught_too():
    """A phone number written in Hindi numerals is still a phone number."""
    result = redaction.redact("मेरा नंबर ९८७६५४३२१० है")
    assert "PHONE" in result.counts_by_label()


@pytest.mark.parametrize("text,label", [
    ("मेरा नाम रामप्रसाद है", "NAME"),
    ("हमार नाम सुनीता बा", "NAME"),
    ("my name is Ramesh", "NAME"),
    ("गाँव बरहेटा में", "VILLAGE"),
    ("जिला गया से", "DISTRICT"),
    ("थाना कोतवाली में", "POLICE_STATION"),
])
def test_cue_introduced_entities_are_redacted(text, label):
    assert label in redaction.redact(text).counts_by_label()


def test_a_cue_with_no_entity_after_it_redacts_nothing():
    """'गाँव में' names no village. Redacting the postposition would both lose
    meaning and record a redaction that never happened."""
    result = redaction.redact("गाँव में मुझे धमकी दी जा रही है")
    assert result.clean


def test_redaction_preserves_readability():
    """A record a counsellor cannot read is not a record. Placeholders are
    typed, and the cue word survives so the sentence still parses."""
    result = redaction.redact("मैं गाँव बरहेटा से बोल रहा हूँ")
    assert "गाँव [VILLAGE]" in result.text
    assert "बोल रहा हूँ" in result.text


def test_overlapping_identifiers_resolve_to_the_longest():
    """A 12-digit Aadhaar must not be left as a redacted 10-digit phone plus
    two loose digits. A partial redaction of an identifier is not a redaction."""
    result = redaction.redact("आधार 987654321012 है")
    assert len(result.redactions) == 1
    assert not any(ch.isdigit() for ch in result.text)


def test_nothing_identifying_survives_a_realistic_disclosure():
    text = ("मेरा नाम सुनीता है, गाँव बरहेटा जिला गया, थाना कोतवाली। "
            "मेरा नंबर 9876543210 है और FIR संख्या 145/2026 दर्ज नहीं हुई।")
    result = redaction.redact(text)
    labels = set(result.counts_by_label())
    assert {"NAME", "VILLAGE", "DISTRICT", "POLICE_STATION", "PHONE", "FIR"} <= labels
    assert not any(ch.isdigit() for ch in result.text)


def test_the_persistence_guard_refuses_unredacted_text():
    """Called at the storage boundary. Unredacted text arriving there is a
    pipeline defect, not something to quietly fix at the point of writing."""
    with pytest.raises(ValueError):
        redaction.assert_redacted("मेरा नंबर 9876543210 है")
    redaction.assert_redacted(redaction.redact("मेरा नंबर 9876543210 है").text)


def test_empty_and_clean_text_are_handled():
    assert redaction.redact("").text == ""
    assert redaction.redact("मुझे मदद चाहिए").clean


# ------------------------------------------------- crisis lexicons

def test_every_language_has_a_lexicon_file():
    """A language with no lexicon FILE is not a supported language (D1). A file
    with no terms is a different, declared state — see the Santali test."""
    for language in Language:
        assert load_lexicon(language) is not None


def test_an_unauthored_lexicon_is_allowed_to_be_empty_and_says_so():
    """Santali. Nobody on this team speaks it, and inventing crisis terms in a
    language you do not know produces silent false negatives that look exactly
    like coverage. Empty and declared beats fabricated and confident."""
    lexicon = load_lexicon(Language.SANTALI)
    assert lexicon.term_count == 0
    assert not lexicon.authored
    warning = lexicon.review_warning()
    assert warning is not None
    assert "suicide screener is still administered" in warning


def test_an_empty_lexicon_cannot_pretend_to_be_authored(tmp_path):
    """The one way this design fails is a file that carries terms while still
    claiming nobody wrote it, so the loader refuses that combination."""
    import json
    from services.nlp.lexicon import load_lexicon as load
    doc = json.loads((LEXICON_SOURCE / "sat.json").read_text(encoding="utf-8"))
    doc["categories"]["self_harm"]["terms"] = ["invented"]
    (tmp_path / "sat.json").write_text(json.dumps(doc, ensure_ascii=False),
                                       encoding="utf-8")
    with pytest.raises(ValueError, match="NOT_AUTHORED"):
        load(Language.SANTALI, directory=str(tmp_path))


def test_a_machine_drafted_lexicon_is_never_reported_as_reviewed():
    """The added languages were drafted by a model. That is a weaker guarantee
    than the hand-assembled seeds, and it must not read as a stronger one."""
    for language in (Language.MARATHI, Language.TELUGU, Language.ODIA):
        lexicon = load_lexicon(language)
        assert lexicon.authored
        assert not lexicon.reviewed
        assert lexicon.review_warning() is not None


def test_normalisation_makes_equivalent_devanagari_match():
    """Text from different keyboards and ASR backends decomposes differently
    and carries zero-width joiners. Two strings that look identical must match,
    or detection depends on which recogniser produced the transcript."""
    assert normalise("आत्महत्या‍") == normalise("आत्महत्या")
    assert tokenise("मर  जाऊँगा") == ["मर", "जाऊँगा"]


@pytest.mark.parametrize("text,language,category", [
    ("अब और नहीं सह सकता, मैं मर जाऊँगा", Language.HINDI, "self_harm"),
    ("जीना नहीं चाहता", Language.HINDI, "self_harm"),
    ("जिये के मन नइखे", Language.BHOJPURI, "self_harm"),
    ("अब ना सहल जाला", Language.BHOJPURI, "self_harm"),
    ("वो लोग हथियार लेकर घर के बाहर खड़े हैं", Language.HINDI, "imminent_violence"),
    ("मारे आ रहल बाड़े", Language.BHOJPURI, "imminent_violence"),
    ("हुक्का पानी बंद कर दिया", Language.HINDI, "isolation"),
    ("शिकायत वापस लेने का दबाव डाल रहे हैं", Language.HINDI, "coercion"),
])
def test_crisis_language_is_detected_in_both_languages(text, language, category):
    assert category in analyse(text, language).categories


def test_ordinary_language_does_not_trigger_the_lexicon():
    for text in ("मुझे कल अस्पताल जाना है", "मेरा बेटा स्कूल जाता है"):
        assert analyse(text, Language.HINDI).hits == ()


def test_rule_categories_match_what_the_safety_layer_consumes():
    """Renaming a category here without updating core.rules.hard_rules would
    silently disconnect a safety path."""
    assert RULE_CATEGORIES == {"self_harm", "imminent_violence"}
    state = TriageState(
        facts=ContextFacts(offence_category="unspecified", asked=ASKED),
        screeners=Screeners(cssrs=CSSRSScreen(administered=True)),
        lexicon_hits={"self_harm"})
    assert apply_hard_rules(Tier.LOW, state).tier is Tier.CRITICAL


def test_self_harm_language_escalates_an_otherwise_calm_interaction():
    """End to end, in Bhojpuri: nothing else about this case is alarming."""
    text = "जिये के मन नइखे"
    hits = analyse(text, Language.BHOJPURI).rule_categories

    facts_ = ContextFacts(offence_category="verbal_abuse_caste_slur", asked=ASKED)
    screeners = Screeners(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5, impairment=0,
                          cssrs=CSSRSScreen(administered=True))
    computed = compute_svi(facts_, screeners, ModelSignals(0.0, 0.0))
    assert computed.tier is Tier.LOW

    outcome = apply_hard_rules(computed.tier, TriageState(facts_, screeners, set(hits)))
    assert outcome.tier is Tier.CRITICAL
    assert outcome.model_bypassed


def test_unreviewed_lexicons_are_used_but_declared():
    """For a crisis lexicon, matching on an unconfirmed term is safer than not
    matching — every error it can make escalates. But the gap must be visible."""
    lexicon = load_lexicon(Language.BHOJPURI)
    assert not lexicon.reviewed
    assert lexicon.review_warning() is not None
    assert analyse("जिये के मन नइखे", Language.BHOJPURI).hits      # still matches


def test_production_readiness_is_blocked_until_lexicons_are_reviewed():
    """A system that escalates suicide risk from a word list nobody qualified
    has read is not ready for live calls. Expected to fail until sign-off, and
    every language must account for itself separately — a gate that reports one
    blocker for twelve languages hides eleven of them."""
    ready, blockers = production_ready()
    assert not ready
    assert len(blockers) == len(Language)
    assert all("speaker" in b for b in blockers)


def test_the_readiness_gate_names_the_language_with_no_lexicon_differently():
    """'Nobody has checked this list' and 'this list does not exist' are
    different problems needing different people, so they get different words."""
    _, blockers = production_ready()
    santali = [b for b in blockers if "Santali" in b]
    assert len(santali) == 1
    assert "no crisis lexicon at all" in santali[0]


# ------------------------------------------------- fact extraction

@pytest.mark.parametrize("text,language,key", [
    ("गाँव वालों ने बहिष्कार कर दिया", Language.HINDI, "social_boycott_active"),
    ("पुलिस ने रिपोर्ट लिखने से मना कर दिया", Language.HINDI, "police_refused_registration"),
    ("आरोपी अभी भी गाँव में ही है", Language.HINDI, "accused_at_large_nearby"),
    ("अब कमाने वाला कोई नहीं", Language.HINDI, "sole_earner_lost"),
    ("गवाही मत देना कह रहे हैं", Language.HINDI, "witness_pressure"),
    ("केहू बात ना करे", Language.BHOJPURI, "social_boycott_active"),
    ("थाना से भगा देलस", Language.BHOJPURI, "police_refused_registration"),
])
def test_risk_factors_are_extracted_from_narrative(text, language, key):
    assert key in facts.extract(text, language).keys


def test_every_extracted_key_is_a_real_risk_factor():
    """A cue naming a factor that does not exist would be silently discarded by
    ContextFacts, so the mapping is asserted rather than assumed."""
    for language, cues in facts.FACT_CUES.items():
        assert set(cues) <= set(FACTORS_BY_KEY), f"{language} names unknown factors"


def test_every_offence_cue_maps_to_a_real_offence_category():
    assert set(facts.OFFENCE_CUES) <= set(OFFENCE_SEVERITY)
    assert set(facts.OFFENCE_PRIORITY) == set(facts.OFFENCE_CUES)


def test_the_most_severe_offence_wins_when_several_match():
    """A caller describing a murder who also mentions caste abuse must not be
    filed under abuse."""
    text = "उन्होंने जातिसूचक गाली दी और फिर मेरे पति की हत्या कर दी"
    assert facts.extract(text, Language.HINDI).offence_category == "murder"


def test_corroborated_factors_are_more_confident_than_single_cues():
    single = facts.extract("बहिष्कार कर दिया", Language.HINDI).facts[0]
    both = facts.extract("बहिष्कार कर दिया, हुक्का पानी बंद है",
                         Language.HINDI).facts[0]
    assert both.confidence > single.confidence
    assert both.corroborated and not single.corroborated


def test_extraction_confidence_never_reaches_certainty():
    """Extracted facts are proposals. Nothing inferred from a transcript is
    ever asserted with the confidence of something the caller confirmed."""
    text = " ".join(["बहिष्कार", "हुक्का पानी बंद", "कोई बात नहीं करता",
                     "दुकान से सामान नहीं"])
    for fact in facts.extract(text, Language.HINDI).facts:
        assert fact.confidence <= facts.MAX_CONFIDENCE < 1.0


def test_extracted_facts_count_less_than_confirmed_ones_in_the_score():
    """The link to Channel A: extraction proposes, confirmation decides."""
    extracted = facts.extract("गाँव वालों ने बहिष्कार कर दिया", Language.HINDI)
    proposed = ContextFacts(offence_category="social_boycott",
                            unconfirmed=set(extracted.keys), asked=ASKED)
    confirmed = ContextFacts(offence_category="social_boycott",
                             present=set(extracted.keys), asked=ASKED)
    assert proposed.score() < confirmed.score()


def test_empty_narrative_extracts_nothing():
    result = facts.extract("", Language.HINDI)
    assert result.facts == () and result.offence_category is None


# ------------------------------------------------- the distress baseline

def full_evidence():
    lex = analyse("कोई रास्ता नहीं, बहुत डर लग रहा है", Language.HINDI)
    timing = ConversationalFeatures(speech_ratio=0.4, pause_count=7, mean_pause=1.1,
                                    longest_pause=3.2, pause_ratio=0.55,
                                    onset_latency=3.4, segment_count=9,
                                    mean_segment=0.9, fragmentation=1.05)
    prosody = {"F0semitoneFrom27.5Hz_sma3nz_stddevNorm": 0.09,
               "loudness_sma3_stddevNorm": 0.22,
               "jitterLocal_sma3nz_amean": 0.034,
               "shimmerLocaldB_sma3nz_amean": 1.4}
    return lex, timing, prosody


def test_the_baseline_declares_itself_a_baseline():
    assert distress.assess(*full_evidence()).is_baseline


def test_untrained_confidence_is_structurally_capped():
    """The safeguard: because the engine computes 25 * probability *
    confidence, capping confidence at 0.6 means an untrained component can move
    a score by at most 15 of 100 points — never enough on its own to cross more
    than one tier boundary."""
    assessment = distress.assess(*full_evidence())
    assert assessment.model_confidence <= distress.BASELINE_CONFIDENCE_CAP
    max_delta = MAX_C_DELTA * 1.0 * distress.BASELINE_CONFIDENCE_CAP
    assert max_delta <= 15.0


def test_more_corroborating_evidence_raises_confidence():
    lex, timing, prosody = full_evidence()
    one = distress.assess(lexicon=lex)
    three = distress.assess(lex, timing, prosody)
    assert three.model_confidence > one.model_confidence


def test_an_unreviewed_lexicon_discounts_confidence():
    """The discount lands on the language whose lexicon is thinnest, which is
    the language whose speakers are worst served."""
    hindi = analyse("कोई रास्ता नहीं", Language.HINDI)
    assert not load_lexicon(Language.HINDI).reviewed
    assert distress.assess(lexicon=hindi).model_confidence < 0.35 + 0.45 / 3 + 1e-9


def test_no_evidence_produces_no_signal():
    assessment = distress.assess()
    assert assessment.distress_probability == 0.0
    assert assessment.model_confidence == 0.0
    assert "No distress indicators" in assessment.explain()


def test_the_explanation_reports_indicators_never_emotions():
    """The system reports what it observed. It does not tell a counsellor what
    a caller feels."""
    text = distress.assess(*full_evidence()).explain().lower()
    assert "indicators observed" in text
    for emotion in ("sad", "angry", "depressed", "afraid", "distressed person"):
        assert emotion not in text


def test_signals_flow_into_the_engine_within_the_capped_bound():
    assessment = distress.assess(*full_evidence())
    signals = assessment.to_signals(Confidence.OK)

    facts_ = ContextFacts(offence_category="social_boycott",
                          present={"social_boycott_active"}, asked=ASKED)
    screeners = Screeners(phq9=[1] * 9, gad7=[1] * 7, pc_ptsd5=[1, 0, 0, 0, 0],
                          impairment=1, cssrs=CSSRSScreen(administered=True))
    with_signal = compute_svi(facts_, screeners, signals)
    without = compute_svi(facts_, screeners, ModelSignals())

    assert with_signal.score >= without.score
    assert with_signal.channel_c_delta <= 15.0


def test_a_poor_line_still_zeroes_the_baseline_entirely():
    """Whatever the baseline concluded, low signal confidence withholds it."""
    assessment = distress.assess(*full_evidence())
    signals = assessment.to_signals(Confidence.LOW)
    facts_ = ContextFacts(offence_category="social_boycott", asked=ASKED)
    screeners = Screeners(phq9=[1] * 9, gad7=[1] * 7, pc_ptsd5=[1, 0, 0, 0, 0],
                          impairment=1, cssrs=CSSRSScreen(administered=True))
    result = compute_svi(facts_, screeners, signals)
    assert result.channel_c_delta == 0.0
    assert result.abstained
