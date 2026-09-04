"""
End-to-end path: facts and screeners -> SVI -> safety rules -> action packet.

These are the scenarios that get demonstrated. If one of them changes shape,
the demo script changes with it, so they are pinned here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.events import Confidence, Tier
from core.svi.factors import ContextFacts, CORE_COVERAGE_KEYS
from core.svi.instruments import CSSRSScreen, Screeners
from core.svi.engine import ModelSignals, compute_svi
from core.rules.hard_rules import TriageState, apply_hard_rules
from core.actions.orchestrator import resolve_actions

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
ASKED = set(CORE_COVERAGE_KEYS)


def triage(facts, screeners, signals=None, lexicon=()):
    svi = compute_svi(facts, screeners, signals or ModelSignals())
    outcome = apply_hard_rules(svi.tier, TriageState(facts, screeners, set(lexicon)))
    actions = resolve_actions(outcome.tier, facts.present, facts.offence_category,
                              outcome.triggered, now=NOW)
    return svi, outcome, actions


def test_social_boycott_case_reaches_high_with_the_right_referrals():
    facts = ContextFacts(
        offence_category="social_boycott",
        present={"social_boycott_active", "prior_threats", "fir_not_registered"},
        asked=ASKED)
    screeners = Screeners(phq9=[2, 2, 2, 2, 1, 1, 1, 2, 0], gad7=[2, 2, 2, 1, 2, 1, 1],
                          pc_ptsd5=[1, 1, 1, 0, 0], impairment=2,
                          cssrs=CSSRSScreen(administered=True))
    svi, outcome, actions = triage(facts, screeners, ModelSignals(0.6, 0.7))

    assert svi.tier is Tier.HIGH
    ids = {a.action_id for a in actions}
    assert "boycott_intervention" in ids
    assert "fir_registration_escalation" in ids
    assert "dlsa_intimation" in ids


def test_calm_presentation_with_suicidal_intent_still_gets_the_crisis_packet():
    """The scenario the whole architecture exists for."""
    facts = ContextFacts(offence_category="verbal_abuse_caste_slur", asked=ASKED)
    screeners = Screeners(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5, impairment=0,
                          cssrs=CSSRSScreen(administered=True, q4=True))
    svi, outcome, actions = triage(facts, screeners, ModelSignals(0.05, 0.9))

    assert svi.tier is Tier.LOW                  # the score is calm
    assert outcome.tier is Tier.CRITICAL         # the rule is not
    assert outcome.model_bypassed
    transfer = next(a for a in actions if a.action_id == "telemanas_warm_transfer")
    assert transfer.immediate
    assert {"crisis_safety_plan", "no_auto_close"} <= {a.action_id for a in actions}


def test_poor_audio_from_a_low_resource_dialect_does_not_soften_the_outcome():
    facts = ContextFacts(offence_category="grievous_hurt",
                         present={"prior_threats", "accused_at_large_nearby"},
                         asked=ASKED)
    screeners = Screeners(phq9=[2] * 9, gad7=[2] * 7, pc_ptsd5=[1, 1, 1, 0, 0],
                          impairment=2, cssrs=CSSRSScreen(administered=True))

    clean, _, clean_actions = triage(facts, screeners, ModelSignals(0.7, 0.8, Confidence.OK))
    noisy, _, noisy_actions = triage(facts, screeners, ModelSignals(0.7, 0.8, Confidence.LOW))

    assert noisy.tier.value == clean.tier.value or noisy.score >= clean.base
    assert len(noisy_actions) >= len(clean_actions)
    assert noisy.abstained


def test_murder_of_a_family_member_raises_relief_and_a_dysp_investigation():
    facts = ContextFacts(offence_category="murder",
                         present={"sole_earner_lost", "threat_imminent", "witness_pressure"},
                         asked=ASKED)
    screeners = Screeners(phq9=[3] * 9, gad7=[3] * 7, pc_ptsd5=[1] * 5, impairment=4,
                          cssrs=CSSRSScreen(administered=True))
    svi, outcome, actions = triage(facts, screeners, ModelSignals(0.85, 0.9))

    assert outcome.tier is Tier.CRITICAL
    ids = {a.action_id for a in actions}
    assert {"dysp_intimation", "dependent_relief", "emergency_relief",
            "witness_protection", "protection_assessment"} <= ids
    assert all(a.basis for a in actions if a.type in
               {"ENTITLEMENT", "PROTECTION", "INTIMATION", "ESCALATION", "MEDICAL"})


def test_an_interaction_that_never_screened_for_suicide_cannot_close_low():
    facts = ContextFacts(offence_category="denial_of_access", asked=ASKED)
    screeners = Screeners(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5, impairment=0,
                          cssrs=CSSRSScreen(administered=False))
    svi, outcome, actions = triage(facts, screeners)
    assert svi.tier is Tier.LOW
    assert outcome.tier is Tier.HIGH
    assert "cssrs_not_administered" in outcome.triggered
