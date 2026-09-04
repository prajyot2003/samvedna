"""
The language table, and the guarantees that stop it drifting.

Adding a language is a declaration about who this helpline can serve. These
tests exist so that the declaration cannot quietly become false — every failure
mode here degrades a caller's experience without any screen reporting it.
"""

from __future__ import annotations

import pytest

from core.events import Language
from core.languages import ASRSupport, PROFILES, coverage_summary, with_asr_support
from services.asr.router import ASRRouter
from services.asr.whisper_local import (LANGUAGE_CODES as WHISPER_CODES,
                                        SUBSTITUTED_LANGUAGES, UNSUPPORTED_LANGUAGES)
from services.asr.bhashini import LANGUAGE_CODES as BHASHINI_CODES
from services.intake.schedule import (CONSENT_PROMPTS, OPENING_PROMPT, PHQ_ITEMS,
                                      SLOTS, translated)
from services.nlp.lexicon import load_lexicon


def test_the_enum_and_the_table_name_the_same_languages():
    """A language in one and not the other fails silently: the enum member is
    constructible, every lookup against the table raises, and nothing says so
    until a caller is on the line."""
    assert {l.value for l in Language} == set(PROFILES)


def test_every_language_has_a_prompt_in_every_prompt_table():
    """Falling back to Hindi is allowed. A KeyError on a live call is not."""
    for language in Language:
        assert OPENING_PROMPT[language.value]
        for scope in CONSENT_PROMPTS.values():
            assert scope[language.value]
        for slot in SLOTS:
            assert slot.prompt(language)
        for item in PHQ_ITEMS:
            assert item.prompt(language)


def test_an_untranslated_language_falls_back_to_hindi_and_admits_it():
    """The console shows which language the caller is actually hearing. A
    counsellor reading a Hindi item to a Tamil speaker is a visible limitation;
    a fabricated Tamil item scoring as PHQ-9 is an invisible one."""
    assert translated(OPENING_PROMPT, Language.HINDI)
    assert not translated(OPENING_PROMPT, Language.TAMIL)
    assert OPENING_PROMPT["ta"] == OPENING_PROMPT["hi"]


def test_clinical_instruments_are_never_machine_translated():
    """PHQ-9's psychometric properties belong to specific validated wordings.
    A translated item is a NEW question wearing a validated one's name, and
    scoring it as the instrument would report a result we cannot support."""
    for item in PHQ_ITEMS:
        for language in Language:
            if not item.is_translated(language):
                assert item.prompt(language) == item.prompts["hi"]


def test_whisper_refuses_the_languages_it_has_no_model_for():
    """Whisper will decode Santali audio as something and return fluent
    nonsense at a respectable confidence. That string would reach the extractor
    and feed Channel A, which is the heaviest channel in the score."""
    assert Language.SANTALI in UNSUPPORTED_LANGUAGES
    assert Language.ODIA in UNSUPPORTED_LANGUAGES
    assert Language.SANTALI not in WHISPER_CODES
    assert Language.ODIA not in WHISPER_CODES


def test_substitution_is_recorded_not_hidden():
    """Bhojpuri and Maithili are decoded as Hindi. That is the single largest
    source of word-error rate in the system and it must never be silent."""
    assert SUBSTITUTED_LANGUAGES == {Language.BHOJPURI, Language.MAITHILI}
    for language in SUBSTITUTED_LANGUAGES:
        assert WHISPER_CODES[language] != language.value


def test_bhashini_carries_languages_whisper_cannot():
    """The sovereign-path argument, as a test rather than a claim."""
    only_bhashini = set(BHASHINI_CODES) - set(WHISPER_CODES)
    assert Language.ODIA in only_bhashini
    for language in SUBSTITUTED_LANGUAGES:
        assert BHASHINI_CODES[language] == language.value


def test_a_language_with_no_recogniser_reports_a_gap_not_an_outage():
    """'Nobody built a recogniser for your language' is permanent and should be
    said once. 'The recogniser is down' is transient. Same empty transcript."""
    from services.asr.base import ASRUnavailable
    router = ASRRouter(backends=())
    router.backends = ()
    import numpy as np
    with pytest.raises(ASRUnavailable, match="declared coverage gap"):
        router.transcribe(np.zeros(16000, dtype=np.float32), 16000, Language.SANTALI)


def test_the_coverage_gap_falls_on_the_callers_the_act_protects():
    """Not a style assertion. The languages with the worst recognition support
    are Adivasi and rural languages of the districts that generate the heaviest
    SC/ST (PoA) Act caseload, and the fairness argument depends on the system
    knowing that about itself rather than on a slide claiming it."""
    unserved = with_asr_support(ASRSupport.NONE) + with_asr_support(ASRSupport.SUBSTITUTED)
    states = {state for profile in unserved for state in profile.states}
    assert {"Bihar", "Jharkhand", "Odisha"} <= states

    summary = coverage_summary()
    assert summary["none"] + summary["substituted"] + summary["declared"] >= 3


def test_every_language_can_be_carried_end_to_end():
    """The integration guarantee. A language registered here must survive a
    lexicon load, a prompt lookup and an ASR capability query without raising —
    whatever the answers turn out to be."""
    router = ASRRouter(backends=())
    router.backends = ()
    for language in Language:
        assert load_lexicon(language) is not None
        assert language.profile.english_name
        assert language.profile.script
        assert language.profile.states
        capability = router.capability(language)
        assert capability["support"] in {s.value for s in ASRSupport}
