"""
What the intake agent asks, in what order, in each language.

Separated from the dialog policy so that the wording — which a counsellor or a
translator must be able to correct without reading Python — lives in one place.

THREE PRINCIPLES BEHIND THE PHRASING.

Open before closed. The first prompt is an invitation to describe what
happened, not a checklist item. People disclose more in their own narrative
than in answers to yes/no questions, and the extraction layer harvests the
narrative for candidate facts so the agent can then confirm rather than
interrogate.

Ask, do not assume. Every core factor is asked explicitly even when extraction
already proposed it, because an unasked question leaves `coverage` low and a
thin assessment must not read as a reassuring one.

Never lead. Questions are neutral in the direction of the answer. "Is the
person who did this still nearby?" and not "They are still nearby, aren't
they?" A leading question produces a fact the caller did not assert, in the
heaviest-weighted channel of the score.

The screener items are adapted from published instruments and are marked with
their source. Translations here are working drafts and carry the same
requirement as the crisis lexicons: a native speaker and a clinician must sign
them off before live use. `services.nlp.lexicon.production_ready` is the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from core.events import Instrument, Language


@dataclass(frozen=True)
class Slot:
    """One Channel A fact the agent tries to establish."""
    key: str
    priority: int                 # 1 is asked first
    confirm: bool                 # read back before it counts as confirmed
    prompts: Dict[str, str]

    def prompt(self, language: Language) -> str:
        return self.prompts[language.value]

    def is_translated(self, language: Language) -> bool:
        return translated(self.prompts, language)


@dataclass(frozen=True)
class ScreenerItem:
    instrument: Instrument
    index: int
    prompts: Dict[str, str]
    scale: str                    # "0-3" | "yes-no" | "0-4"
    source: str

    def prompt(self, language: Language) -> str:
        return self.prompts[language.value]

    def is_translated(self, language: Language) -> bool:
        """False means the caller is hearing this validated instrument in
        Hindi because their language has no authored translation yet."""
        return translated(self.prompts, language)


# Languages whose prompts have been authored. A language absent from this set
# is served in Hindi and the console says so — see `_p` below.
AUTHORED: Dict[str, set] = {}


def _p(hi: str, bho: str, **others: str) -> Dict[str, str]:
    """One prompt in every language, falling back to Hindi where unauthored.

    THIS FUNCTION REFUSES TO INVENT CLINICAL LANGUAGE, and that refusal is the
    design. PHQ-9, GAD-7, PC-PTSD-5 and the C-SSRS are validated instruments;
    their psychometric properties belong to specific wordings, and a machine
    translation of "little interest or pleasure in doing things" into Odia is
    not the Odia PHQ-9 — it is a new, unvalidated question wearing the name of
    a validated one. Presenting it as the instrument would let us report a
    screening result we have no basis for.

    So an unauthored language falls back to Hindi, `translated()` reports
    False for it, and the counsellor console shows which language the caller is
    actually hearing. A counsellor reading a Hindi item to a Tamil speaker is a
    visible, correctable limitation. A fabricated Tamil item that scores as
    PHQ-9 is an invisible, uncorrectable one.

    Officially translated and validated instruments exist for several of these
    languages. Obtaining them, and having a clinician confirm each against the
    source, is a task in evidence/PILOT_PROTOCOL.md — not something this file
    may shortcut.
    """
    prompts = {"hi": hi, "bho": bho, **others}
    return {code: prompts.get(code, hi) for code in _ALL_CODES} | {
        _AUTHORED_KEY: frozenset(prompts)}


_AUTHORED_KEY = "__authored__"


def _codes() -> Tuple[str, ...]:
    from core.languages import PROFILES
    return tuple(PROFILES)


_ALL_CODES = _codes()


def translated(prompts: Dict[str, str], language: Language) -> bool:
    """Whether this prompt was authored in the caller's language, or is Hindi
    standing in for it."""
    return language.value in prompts.get(_AUTHORED_KEY, frozenset())


# --------------------------------------------------------------------------
# Consent — before anything else, and before any analysis
# --------------------------------------------------------------------------

CONSENT_SCRIPT_VERSION = "v1.0-draft"

CONSENT_PROMPTS: Dict[str, Dict[str, str]] = {
    "analysis": _p(
        "इस बातचीत के दौरान एक कंप्यूटर प्रणाली आपकी बात सुनकर यह समझने में "
        "मदद करती है कि आपको कितनी तत्काल सहायता चाहिए। इससे आपकी मदद जल्दी "
        "पहुँच सकती है। क्या आप इसकी अनुमति देते हैं? यदि आप मना करते हैं तो "
        "भी आपकी शिकायत पूरी तरह दर्ज होगी और आपको कोई नुकसान नहीं होगा।",
        "ई बातचीत के दौरान एगो कंप्यूटर सिस्टम रउरा बात सुन के ई समझे में मदद "
        "करेला कि रउरा केतना जल्दी मदद चाहीं। एसे रउरा मदद जल्दी पहुँच सकेला। "
        "का रउआ एकर इजाजत देत बानी? जदि रउआ मना करब त भी रउरा शिकायत पूरा दर्ज "
        "होई आ रउरा कवनो नुकसान ना होई।"),
    "retention": _p(
        "क्या हम इस बातचीत से निकली जानकारी आपकी शिकायत के साथ सुरक्षित रख सकते हैं?",
        "का हम ई बातचीत से निकलल जानकारी रउरा शिकायत के साथे सुरक्षित रख सकीले?"),
    "referral": _p(
        "क्या हम आपकी जानकारी परामर्श, कानूनी सहायता या सुरक्षा के लिए संबंधित "
        "अधिकारियों तक भेज सकते हैं?",
        "का हम रउरा जानकारी सलाह, कानूनी मदद भा सुरक्षा खातिर संबंधित अधिकारी "
        "लोग के भेज सकीले?"),
}

CONSENT_DECLINED_ACKNOWLEDGEMENT = _p(
    "ठीक है, कोई बात नहीं। आपकी शिकायत सामान्य रूप से दर्ज की जाएगी और एक "
    "काउंसलर आपसे बात करेंगे।",
    "ठीक बा, कवनो बात ना। रउरा शिकायत सामान्य रूप से दर्ज होई आ एगो काउंसलर "
    "रउरा से बात करीहें।")

OPENING_PROMPT = _p(
    "मैं सुन रही हूँ। आप अपने शब्दों में बताइए कि क्या हुआ।",
    "हम सुनत बानी। रउआ अपना शब्दन में बताईं कि का भइल।")


# --------------------------------------------------------------------------
# Slots
# --------------------------------------------------------------------------

SLOTS: Tuple[Slot, ...] = (
    Slot("threat_imminent", 1, True, _p(
        "क्या आपको इस समय कोई तत्काल खतरा है? क्या वे लोग अभी आपके पास हैं?",
        "का रउरा एह घरी कवनो तुरंत खतरा बा? का ऊ लोग अबहीं रउरा लगे बा?")),
    Slot("accused_at_large_nearby", 2, True, _p(
        "जिन लोगों ने यह किया, क्या वे अभी भी आपके गाँव या आस-पास रहते हैं?",
        "जे लोग ई कइलस, का ऊ अबहियो रउरा गाँव भा आस-पास रहेला?")),
    Slot("prior_threats", 3, False, _p(
        "क्या इससे पहले भी आपको धमकी मिली है?",
        "का एसे पहिलहूँ रउरा धमकी मिलल बा?")),
    Slot("fir_not_registered", 4, False, _p(
        "क्या इस मामले में पुलिस रिपोर्ट दर्ज हो चुकी है?",
        "का एह मामला में पुलिस रिपोर्ट दर्ज हो चुकल बा?")),
    Slot("police_refused_registration", 5, True, _p(
        "जब आप थाने गए, तो पुलिस ने क्या कहा?",
        "जब रउआ थाना गइनी, त पुलिस का कहलस?")),
    Slot("social_boycott_active", 6, True, _p(
        "क्या गाँव के लोगों ने आपसे बोलना, या दुकान-पानी देना बंद किया है?",
        "का गाँव के लोग रउरा से बोलल, भा दुकान-पानी देवल बंद कइले बा?")),
    Slot("victim_minor", 7, True, _p(
        "जिनके साथ यह हुआ, उनकी उम्र क्या है?",
        "जेकरा साथे ई भइल, ओकर उमिर का बा?")),
    Slot("displaced_from_home", 8, True, _p(
        "क्या आपको अपना घर या गाँव छोड़ना पड़ा है?",
        "का रउरा अपना घर भा गाँव छोड़े के पड़ल बा?")),
    Slot("sole_earner_lost", 9, True, _p(
        "क्या घर में कमाने वाले किसी सदस्य को नुकसान पहुँचा है?",
        "का घर में कमाए वाला केहू सदस्य के नुकसान पहुँचल बा?")),
    Slot("witness_pressure", 10, False, _p(
        "क्या कोई आप पर शिकायत वापस लेने या गवाही न देने का दबाव डाल रहा है?",
        "का केहू रउरा पर शिकायत वापस लेवे भा गवाही ना देवे के दबाव डालत बा?")),
    Slot("no_family_support", 11, False, _p(
        "इस समय आपके साथ कौन है? क्या परिवार या कोई और आपके साथ है?",
        "एह घरी रउरा साथे के बा? का परिवार भा केहू आउर रउरा साथे बा?")),
    Slot("victim_pregnant", 12, False, _p(
        "क्या कोई स्वास्थ्य स्थिति है जो हमें जाननी चाहिए, जैसे गर्भावस्था?",
        "का कवनो सेहत के बात बा जे हमनी के जानल चाहीं, जइसे गरभ?")),
    Slot("victim_disabled", 13, False, _p(
        "क्या आपको चलने, देखने या सुनने में कोई कठिनाई है?",
        "का रउरा चले, देखे भा सुने में कवनो दिक्कत बा?")),
)

SLOTS_BY_KEY: Dict[str, Slot] = {s.key: s for s in SLOTS}

CONFIRMATION_TEMPLATE = _p(
    "मैंने यह समझा — {statement}। क्या यह सही है?",
    "हम ई समझनी — {statement}। का ई ठीक बा?")

CONFIRMATION_STATEMENTS: Dict[str, Dict[str, str]] = {
    "threat_imminent": _p("आपको अभी तत्काल खतरा है",
                          "रउरा अबहीं तुरंत खतरा बा"),
    "accused_at_large_nearby": _p("आरोपी अभी भी आपके पास ही रहते हैं",
                                  "आरोपी अबहियो रउरा लगे रहेला"),
    "police_refused_registration": _p("पुलिस ने रिपोर्ट दर्ज करने से मना किया",
                                      "पुलिस रिपोर्ट दर्ज करे से मना कइलस"),
    "social_boycott_active": _p("गाँव में आपका बहिष्कार किया जा रहा है",
                                "गाँव में रउरा बहिष्कार कइल जात बा"),
    "victim_minor": _p("पीड़ित की उम्र अठारह वर्ष से कम है",
                       "पीड़ित के उमिर अठारह बरिस से कम बा"),
    "displaced_from_home": _p("आपको अपना घर छोड़ना पड़ा है",
                              "रउरा अपना घर छोड़े के पड़ल बा"),
    "sole_earner_lost": _p("घर के कमाने वाले सदस्य को नुकसान पहुँचा है",
                           "घर के कमाए वाला सदस्य के नुकसान पहुँचल बा"),
}


# --------------------------------------------------------------------------
# Screener items
# --------------------------------------------------------------------------

PC_PTSD5: Tuple[ScreenerItem, ...] = tuple(
    ScreenerItem(Instrument.PC_PTSD5, i, prompts, "yes-no", "PC-PTSD-5")
    for i, prompts in enumerate((
        _p("पिछले महीने में, क्या आपको इस घटना के सपने आए या यह बार-बार याद आया?",
           "पिछला महीना में, का रउरा एह घटना के सपना आइल भा बेर-बेर याद आइल?"),
        _p("क्या आपने इसके बारे में सोचने से बचने की कोशिश की?",
           "का रउआ एकरा बारे में सोचे से बचे के कोशिश कइनी?"),
        _p("क्या आप लगातार सतर्क या चौंकने वाले रहे?",
           "का रउआ लगातार सतर्क भा चउंके वाला रहनी?"),
        _p("क्या आप लोगों और गतिविधियों से कटा हुआ महसूस करते हैं?",
           "का रउआ लोग आ काम-काज से कटल महसूस करत बानी?"),
        _p("क्या आप इसके लिए खुद को या किसी और को दोषी मानते हैं?",
           "का रउआ एकरा खातिर अपना के भा केहू आउर के दोषी मानत बानी?"),
    )))

PHQ_ITEMS: Tuple[ScreenerItem, ...] = tuple(
    ScreenerItem(Instrument.PHQ9, i, prompts, "0-3", "PHQ-9")
    for i, prompts in enumerate((
        _p("पिछले दो हफ्तों में, किसी काम में मन लगने में कितनी परेशानी हुई?",
           "पिछला दू हफ्ता में, कवनो काम में मन लागे में केतना दिक्कत भइल?"),
        _p("उदास, निराश या बेसहारा महसूस करना?",
           "उदास, निराश भा बेसहारा महसूस कइल?"),
        _p("नींद आने या बहुत ज्यादा सोने में परेशानी?",
           "नींद आवे भा बहुत जादा सुते में दिक्कत?"),
        _p("थकान या ऊर्जा की कमी?", "थकान भा ताकत के कमी?"),
        _p("भूख कम लगना या ज्यादा खाना?", "भूख कम लागल भा जादा खाइल?"),
        _p("अपने बारे में बुरा महसूस करना?", "अपना बारे में खराब महसूस कइल?"),
        _p("ध्यान लगाने में कठिनाई?", "ध्यान लगावे में दिक्कत?"),
        _p("बहुत धीरे चलना-बोलना, या बहुत बेचैनी?",
           "बहुत धीरे चलल-बोलल, भा बहुत बेचैनी?"),
        _p("क्या आपके मन में यह विचार आया कि आप न होते तो बेहतर होता, "
           "या खुद को नुकसान पहुँचाने का?",
           "का रउरा मन में ई बिचार आइल कि रउआ ना रहतीं त बेहतर रहित, "
           "भा अपना के नुकसान पहुँचावे के?"),
    )))

GAD_ITEMS: Tuple[ScreenerItem, ...] = tuple(
    ScreenerItem(Instrument.GAD7, i, prompts, "0-3", "GAD-7")
    for i, prompts in enumerate((
        _p("पिछले दो हफ्तों में, घबराहट या बेचैनी महसूस करना?",
           "पिछला दू हफ्ता में, घबराहट भा बेचैनी महसूस कइल?"),
        _p("चिंता को रोक न पाना?", "चिंता के रोक ना पावल?"),
        _p("अलग-अलग बातों की बहुत चिंता?", "अलग-अलग बात के बहुत चिंता?"),
        _p("आराम करने में कठिनाई?", "आराम करे में दिक्कत?"),
        _p("इतनी बेचैनी कि एक जगह बैठना मुश्किल?",
           "अतना बेचैनी कि एक जगह बइठल मुश्किल?"),
        _p("आसानी से चिढ़ जाना या गुस्सा आना?", "असानी से चिढ़ जाइल भा गुस्सा आइल?"),
        _p("यह डर कि कुछ बुरा होने वाला है?", "ई डर कि कुछ खराब होखे वाला बा?"),
    )))

CSSRS_ITEMS: Tuple[ScreenerItem, ...] = tuple(
    ScreenerItem(Instrument.CSSRS, i, prompts, "yes-no", "C-SSRS screener")
    for i, prompts in enumerate((
        _p("क्या आपने कभी यह चाहा है कि आप सो जाएँ और फिर न उठें?",
           "का रउआ कबो ई चहनी कि रउआ सुत जाईं आ फेर ना उठीं?"),
        _p("क्या आपने वास्तव में खुद को खत्म करने के बारे में सोचा है?",
           "का रउआ सचहूँ अपना के खतम करे के बारे में सोचनी?"),
        _p("क्या आपने सोचा है कि यह कैसे कर सकते हैं?",
           "का रउआ सोचनी कि ई कइसे कर सकीले?"),
        _p("क्या आपका इरादा इसे करने का रहा है?",
           "का रउरा एकरा करे के इरादा रहल बा?"),
        _p("क्या आपने इसके लिए कोई योजना बनाई है?",
           "का रउआ एकरा खातिर कवनो योजना बनवनी?"),
        _p("क्या आपने कभी खुद को नुकसान पहुँचाने के लिए कुछ किया है?",
           "का रउआ कबो अपना के नुकसान पहुँचावे खातिर कुछ कइनी?"),
    )))

IMPAIRMENT_ITEM = ScreenerItem(
    Instrument.IMPAIRMENT, 0,
    _p("इन सब के कारण रोज़मर्रा के काम करने में आपको कितनी कठिनाई हो रही है?",
       "ई सब के चलते रोज के काम करे में रउरा केतना दिक्कत होत बा?"),
    "0-4", "WHODAS-style single-item functional impairment")

CLOSING_PROMPT = _p(
    "आपने जो बताया उसके लिए धन्यवाद। मैं अब एक काउंसलर से आपकी बात कराती हूँ।",
    "रउआ जे बतवनी ओकरा खातिर धन्यवाद। हम अब एगो काउंसलर से रउरा बात करावत बानी।")

CRISIS_HANDOVER_PROMPT = _p(
    "आपने जो कहा वह बहुत महत्वपूर्ण है। मैं अभी आपको एक प्रशिक्षित काउंसलर से "
    "जोड़ रही हूँ। कृपया फोन मत रखिए, मैं आपके साथ हूँ।",
    "रउआ जे कहनी ऊ बहुत जरूरी बा। हम अबहीं रउरा के एगो ट्रेन्ड काउंसलर से जोड़त "
    "बानी। किरपा क के फोन मत राखीं, हम रउरा साथे बानी।")
