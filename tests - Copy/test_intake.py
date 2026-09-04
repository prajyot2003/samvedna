"""
Intake agent tests.

The first four sections correspond one-to-one with the four non-negotiable
rules in the agent's docstring. If any of them goes red, a safety property this
system claims in front of a panel has stopped being true.
"""

from __future__ import annotations

import pytest

from core.events import (ConsentDecision, ConsentScope, Instrument, Language, Tier,
                         tier_rank)
from core.rules.hard_rules import TriageState, apply_hard_rules
from core.svi.engine import ModelSignals, compute_svi
from services.intake import schedule as S
from services.intake.agent import ActionKind, IntakeAgent, IntakeState, Phase
from services.nlp.facts import extract
from services.nlp.lexicon import analyse


@pytest.fixture()
def agent():
    return IntakeAgent()


def consented(agent, language=Language.HINDI) -> IntakeState:
    state = IntakeState(language=language)
    for scope in IntakeAgent.CONSENT_ORDER:
        agent.record_consent(state, scope, ConsentDecision.GRANTED)
    return state


def answer_everything(agent, state, *, phq=0, gad=0, ptsd=0, cssrs=False,
                      slots_present=False, phq9_item9=0, limit=200):
    """Drive the interview to completion, answering whatever is asked.

    PHQ-9 item 9 is answered separately and defaults to 0. It asks about
    thoughts of self-harm, so answering it positively is a safety disclosure,
    not just another score — sweeping it into a blanket value is exactly the
    carelessness the system is built to catch.
    """
    for _ in range(limit):
        action = agent.next_action(state)
        if action.kind in (ActionKind.CLOSE, ActionKind.ACKNOWLEDGE,
                           ActionKind.CRISIS_HANDOVER):
            return action
        if action.kind is ActionKind.OPEN_NARRATIVE:
            state.narrative_given = True
        elif action.kind is ActionKind.ASK_SLOT:
            agent.record_slot(state, action.slot_key, slots_present)
        elif action.kind is ActionKind.CONFIRM_FACT:
            agent.record_slot(state, action.slot_key, True, confirmed=True)
        elif action.kind is ActionKind.ASK_SCREENER:
            value = {Instrument.PHQ9: phq, Instrument.GAD7: gad,
                     Instrument.PC_PTSD5: ptsd, Instrument.CSSRS: cssrs,
                     Instrument.IMPAIRMENT: 0}[action.instrument]
            if action.instrument is Instrument.PHQ9 and action.item_index == 8:
                value = phq9_item9
            agent.record_screener(state, action.instrument, action.item_index, value)
    raise AssertionError("interview did not terminate")


# ---------------------------------------- RULE 1: consent precedes analysis

def test_the_first_thing_asked_is_consent_to_analysis(agent):
    action = agent.next_action(IntakeState())
    assert action.kind is ActionKind.ASK_CONSENT
    assert action.scope is ConsentScope.ANALYSIS


def test_no_other_scope_is_sought_before_analysis_is_granted(agent):
    state = IntakeState()
    agent.record_consent(state, ConsentScope.ANALYSIS, ConsentDecision.DECLINED)
    assert agent.next_action(state).kind is ActionKind.ACKNOWLEDGE


def test_declining_analysis_costs_the_caller_nothing(agent):
    """Passive mode: full human handling, no scoring, and the acknowledgement
    says the complaint is still recorded."""
    state = IntakeState()
    agent.record_consent(state, ConsentScope.ANALYSIS, ConsentDecision.DECLINED)
    assert state.phase is Phase.PASSIVE
    action = agent.next_action(state)
    assert action.kind is ActionKind.ACKNOWLEDGE
    assert "passive mode" in action.rationale
    assert action.prompt == S.CONSENT_DECLINED_ACKNOWLEDGEMENT[state.language.value]


def test_a_declined_interaction_never_advances_to_questions(agent):
    state = IntakeState()
    agent.record_consent(state, ConsentScope.ANALYSIS, ConsentDecision.DECLINED)
    for _ in range(5):
        assert agent.next_action(state).kind is ActionKind.ACKNOWLEDGE


# ---------------------------------------- RULE 2: the C-SSRS is always administered

def test_the_suicide_screener_runs_even_when_every_other_answer_is_zero(agent):
    """The case where a system tuned for efficiency would skip it."""
    state = consented(agent)
    answer_everything(agent, state, phq=0, gad=0, ptsd=0, cssrs=False)
    assert state.to_screeners().cssrs.administered
    assert len(state.cssrs_answers) == len(S.CSSRS_ITEMS)


def test_an_interview_cannot_close_without_the_suicide_screener(agent):
    state = consented(agent)
    state.narrative_given = True
    for slot in S.SLOTS:
        agent.record_slot(state, slot.key, False)
    for i in range(len(S.PC_PTSD5)):
        agent.record_screener(state, Instrument.PC_PTSD5, i, 0)
    for i in range(2):
        agent.record_screener(state, Instrument.PHQ9, i, 0)
        agent.record_screener(state, Instrument.GAD7, i, 0)

    action = agent.next_action(state)
    assert action.kind is ActionKind.ASK_SCREENER
    assert action.instrument is Instrument.CSSRS
    assert "without exception" in action.rationale


def test_an_incomplete_suicide_screen_keeps_the_tier_up(agent):
    """The link to the safety layer: a missing C-SSRS forces at least HIGH."""
    state = consented(agent)
    state.narrative_given = True
    for slot in S.SLOTS:
        agent.record_slot(state, slot.key, False)
    facts, screeners = state.to_context_facts(), state.to_screeners()
    assert not screeners.cssrs.administered
    outcome = apply_hard_rules(compute_svi(facts, screeners).tier,
                               TriageState(facts, screeners))
    assert tier_rank(outcome.tier) >= tier_rank(Tier.HIGH)
    assert "cssrs_not_administered" in outcome.triggered


# ---------------------------------------- RULE 3: crisis language interrupts

def test_self_harm_language_jumps_straight_to_the_suicide_screener(agent):
    """Asking about land records while someone discloses suicidal intent is
    its own kind of harm."""
    state = consented(agent)
    text = "अब और नहीं सह सकता, मैं मर जाऊँगा"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))
    assert state.crisis_flag

    action = agent.next_action(state)
    assert action.kind is ActionKind.ASK_SCREENER
    assert action.instrument is Instrument.CSSRS
    assert "brought forward" in action.rationale


def test_the_crisis_interrupt_outranks_pending_confirmations_and_slots(agent):
    state = consented(agent)
    narrative = "गाँव वालों ने बहिष्कार कर दिया"
    agent.ingest_narrative(state, extract(narrative, Language.HINDI),
                           analyse(narrative, Language.HINDI))
    assert agent.next_action(state).kind is ActionKind.CONFIRM_FACT

    crisis = "जीना नहीं चाहता"
    agent.ingest_narrative(state, extract(crisis, Language.HINDI),
                           analyse(crisis, Language.HINDI))
    assert agent.next_action(state).instrument is Instrument.CSSRS


def test_a_crisis_interaction_ends_in_handover_not_a_normal_close(agent):
    state = consented(agent)
    text = "जिये के मन नइखे"
    state.language = Language.BHOJPURI
    agent.ingest_narrative(state, extract(text, Language.BHOJPURI),
                           analyse(text, Language.BHOJPURI))
    final = answer_everything(agent, state, cssrs=True)
    assert final.kind is ActionKind.CRISIS_HANDOVER
    assert "काउंसलर" in final.prompt


def test_phq9_item_nine_also_raises_the_crisis_flag(agent):
    """Self-harm can surface through the depression screener rather than the
    narrative, and must be treated the same."""
    state = consented(agent)
    state.narrative_given = True
    agent.record_screener(state, Instrument.PHQ9, 8, 2)
    assert state.crisis_flag
    assert agent.next_action(state).instrument is Instrument.CSSRS


# ---------------------------------------- RULE 4: read back before it counts

def test_extracted_facts_are_read_back_before_they_count(agent):
    state = consented(agent)
    text = "गाँव वालों ने बहिष्कार कर दिया, हुक्का पानी बंद है"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))

    assert "social_boycott_active" in state.unconfirmed
    assert "social_boycott_active" not in state.confirmed

    action = agent.next_action(state)
    assert action.kind is ActionKind.CONFIRM_FACT
    assert action.slot_key == "social_boycott_active"
    assert "क्या यह सही है" in action.prompt


def test_confirmation_moves_a_fact_from_provisional_to_established(agent):
    state = consented(agent)
    text = "आरोपी अभी भी गाँव में ही है"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))
    before = state.to_context_facts().score()

    agent.record_slot(state, "accused_at_large_nearby", True, confirmed=True)
    after = state.to_context_facts().score()

    assert "accused_at_large_nearby" in state.confirmed
    assert after > before


def test_a_caller_can_correct_an_extraction(agent):
    """The read-back exists so a mis-extraction can be denied, not merely
    acknowledged."""
    state = consented(agent)
    text = "आरोपी अभी भी गाँव में ही है"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))
    agent.record_slot(state, "accused_at_large_nearby", False)
    assert "accused_at_large_nearby" not in state.confirmed
    assert "accused_at_large_nearby" not in state.unconfirmed
    assert "accused_at_large_nearby" in state.asked_slots     # coverage still counts


# ---------------------------------------- ordering and escalation

def test_the_open_question_comes_before_any_checklist_item(agent):
    state = consented(agent)
    assert agent.next_action(state).kind is ActionKind.OPEN_NARRATIVE


def test_slots_are_asked_in_priority_order(agent):
    state = consented(agent)
    state.narrative_given = True
    asked = []
    for _ in range(len(S.SLOTS)):
        action = agent.next_action(state)
        if action.kind is not ActionKind.ASK_SLOT:
            break
        asked.append(S.SLOTS_BY_KEY[action.slot_key].priority)
        agent.record_slot(state, action.slot_key, False)
    assert asked == sorted(asked)
    assert asked[0] == 1


def test_the_depression_screener_escalates_from_the_two_item_stem(agent):
    state = consented(agent)
    state.narrative_given = True
    for slot in S.SLOTS:
        agent.record_slot(state, slot.key, False)
    for i in range(len(S.PC_PTSD5)):
        agent.record_screener(state, Instrument.PC_PTSD5, i, 0)

    agent.record_screener(state, Instrument.PHQ9, 0, 2)
    agent.record_screener(state, Instrument.PHQ9, 1, 2)          # stem total 4
    action = agent.next_action(state)
    assert action.instrument is Instrument.PHQ9
    assert action.item_index == 2
    assert "escalated" in action.rationale


def test_a_negative_stem_does_not_escalate(agent):
    state = consented(agent)
    state.narrative_given = True
    for slot in S.SLOTS:
        agent.record_slot(state, slot.key, False)
    for i in range(len(S.PC_PTSD5)):
        agent.record_screener(state, Instrument.PC_PTSD5, i, 0)
    agent.record_screener(state, Instrument.PHQ9, 0, 0)
    agent.record_screener(state, Instrument.PHQ9, 1, 1)          # stem total 1
    assert agent.next_action(state).instrument is Instrument.GAD7


def test_asking_a_question_counts_for_coverage_whatever_the_answer(agent):
    """A thin assessment must not read as a reassuring one, so coverage counts
    questions put, not factors found."""
    state = consented(agent)
    state.narrative_given = True
    assert state.to_context_facts().coverage() == 0.0
    for slot in S.SLOTS:
        agent.record_slot(state, slot.key, False)
    assert state.to_context_facts().coverage() == 1.0


def test_a_completed_interview_produces_a_scoreable_assessment(agent):
    state = consented(agent)
    text = ("मेरे पति की हत्या कर दी, अब कमाने वाला कोई नहीं, "
            "आरोपी अभी भी गाँव में ही है")
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))
    final = answer_everything(agent, state, phq=2, gad=2, ptsd=1, slots_present=True)

    assert final.kind is ActionKind.CLOSE
    facts, screeners = state.to_context_facts(), state.to_screeners()
    assert facts.offence_category == "murder"
    assert facts.coverage() == 1.0
    assert screeners.cssrs.administered

    result = compute_svi(facts, screeners, ModelSignals(0.5, 0.5))
    assert not result.abstained
    outcome = apply_hard_rules(result.tier, TriageState(facts, screeners))
    assert tier_rank(outcome.tier) >= tier_rank(Tier.HIGH)


# ---------------------------------------- the script itself

def test_every_prompt_exists_in_every_supported_language():
    """A language missing a prompt would fall back to another language mid-call,
    which is worse than not supporting it."""
    items = (list(S.SLOTS) + list(S.PC_PTSD5) + list(S.PHQ_ITEMS)
             + list(S.GAD_ITEMS) + list(S.CSSRS_ITEMS) + [S.IMPAIRMENT_ITEM])
    for language in Language:
        for item in items:
            assert item.prompt(language).strip()
        for scope in S.CONSENT_PROMPTS.values():
            assert scope[language.value].strip()
        for statement in S.CONFIRMATION_STATEMENTS.values():
            assert statement[language.value].strip()


def test_every_slot_names_a_real_risk_factor():
    from core.svi.factors import FACTORS_BY_KEY
    assert set(S.SLOTS_BY_KEY) <= set(FACTORS_BY_KEY)


def test_every_confirmable_slot_has_a_read_back_statement():
    """A slot marked confirm=True with no statement would be silently skipped
    at read-back and quietly counted as provisional forever."""
    for slot in S.SLOTS:
        if slot.confirm:
            assert slot.key in S.CONFIRMATION_STATEMENTS, slot.key


def test_the_screener_items_match_the_published_instrument_lengths():
    assert len(S.PC_PTSD5) == 5
    assert len(S.PHQ_ITEMS) == 9
    assert len(S.GAD_ITEMS) == 7
    assert len(S.CSSRS_ITEMS) == 6


def test_every_action_carries_a_rationale_for_the_counsellor(agent):
    """The console shows why each question is being suggested. A prompt with no
    stated reason trains counsellors to click through without reading."""
    state = consented(agent)
    seen = 0
    for _ in range(60):
        action = agent.next_action(state)
        assert action.rationale.strip(), action.kind
        seen += 1
        if action.kind is ActionKind.CLOSE:
            break
        if action.kind is ActionKind.OPEN_NARRATIVE:
            state.narrative_given = True
        elif action.kind is ActionKind.ASK_SLOT:
            agent.record_slot(state, action.slot_key, False)
        elif action.kind is ActionKind.ASK_SCREENER:
            agent.record_screener(state, action.instrument, action.item_index, 0)
    assert seen > 20


def test_coverage_report_surfaces_what_is_missing(agent):
    state = consented(agent)
    report = agent.coverage_report(state)
    assert report["cssrs_administered"] is False
    assert report["context_coverage"] == 0.0
    assert report["slots_total"] == len(S.SLOTS)


def test_a_disclosure_on_phq9_item_nine_ends_in_handover(agent):
    """Answering item 9 positively is a safety disclosure, not just a score,
    and it must change how the interaction ends."""
    state = consented(agent)
    final = answer_everything(agent, state, phq=2, phq9_item9=2)
    assert final.kind is ActionKind.CRISIS_HANDOVER
    assert state.crisis_flag
    assert state.to_screeners().phq9_item9_positive


def test_the_handover_repeats_until_a_counsellor_actually_takes_the_call(agent):
    """A caller who has disclosed suicidal intent is not released from the line
    because a transfer was merely attempted."""
    state = consented(agent)
    answer_everything(agent, state, phq=2, phq9_item9=2)

    assert agent.next_action(state).kind is ActionKind.CRISIS_HANDOVER
    assert agent.next_action(state).kind is ActionKind.CRISIS_HANDOVER

    agent.record_crisis_handover(state)
    resumed = agent.next_action(state)
    assert resumed.kind is not ActionKind.CRISIS_HANDOVER

    # The interview is not over: the crisis interrupt jumped ahead of the
    # remaining screeners, and they still need administering — now with a
    # counsellor on the line.
    final = answer_everything(agent, state, phq=2, phq9_item9=2)
    assert final.kind is ActionKind.CLOSE
    assert state.to_screeners().coverage() == 1.0


def test_handover_cannot_be_recorded_when_no_crisis_is_in_progress(agent):
    with pytest.raises(ValueError):
        agent.record_crisis_handover(consented(agent))


def test_a_crisis_disclosure_outranks_the_remaining_consent_questions(agent):
    """Analysis consent cannot wait — nothing may be assessed without it. But
    asking someone who has just disclosed suicidal intent whether we may retain
    their data, before administering the suicide screener, is indefensible."""
    state = IntakeState()
    agent.record_consent(state, ConsentScope.ANALYSIS, ConsentDecision.GRANTED)
    assert agent.next_action(state).scope is ConsentScope.RETENTION

    text = "अब और नहीं सह सकता, मैं मर जाऊँगा"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))

    action = agent.next_action(state)
    assert action.kind is ActionKind.ASK_SCREENER
    assert action.instrument is Instrument.CSSRS


def test_remaining_consent_is_still_sought_once_the_crisis_path_clears(agent):
    state = IntakeState()
    agent.record_consent(state, ConsentScope.ANALYSIS, ConsentDecision.GRANTED)
    text = "मैं मर जाऊँगा"
    agent.ingest_narrative(state, extract(text, Language.HINDI),
                           analyse(text, Language.HINDI))
    for i in range(len(S.CSSRS_ITEMS)):
        agent.record_screener(state, Instrument.CSSRS, i, False)
    agent.record_crisis_handover(state)
    assert agent.next_action(state).scope is ConsentScope.RETENTION
