"""
The invariant suite.

These tests are not incidental coverage. Each one corresponds to a claim the
system makes in front of a reviewer, and if any of them goes red the claim is
no longer true. They are the specification.
"""

from __future__ import annotations

import pytest

from core.events import Confidence, Tier, tier_rank
from core.svi.factors import (ContextFacts, CORE_COVERAGE_KEYS, FACTORS_BY_KEY,
                              SATURATION_DENOMINATOR)
from core.svi.instruments import CSSRSScreen, Screeners
from core.svi.engine import (MAX_C_DELTA, TIER_FLOOR, ModelSignals, compute_svi,
                             tier_of)
from core.rules.hard_rules import TriageState, apply_hard_rules


# ---------------------------------------------------------------- helpers

FULL_COVERAGE = set(CORE_COVERAGE_KEYS)


def facts(**kw) -> ContextFacts:
    kw.setdefault("asked", FULL_COVERAGE)
    kw.setdefault("offence_category", "intimidation_threat")
    return ContextFacts(**kw)


def full_screen(**kw) -> Screeners:
    base = dict(phq9=[1] * 9, gad7=[1] * 7, pc_ptsd5=[1, 0, 0, 0, 0], impairment=1,
                cssrs=CSSRSScreen(administered=True))
    base.update(kw)
    return Screeners(**base)


def state(f: ContextFacts, s: Screeners, hits=None) -> TriageState:
    return TriageState(facts=f, screeners=s, lexicon_hits=set(hits or ()))


# ------------------------------------------- INVARIANT 1: monotonicity

def test_channel_c_never_lowers_the_score():
    f, s = facts(), full_screen()
    without = compute_svi(f, s, ModelSignals(0.0, 0.0))
    for p in (0.1, 0.5, 0.9, 1.0):
        for c in (0.1, 0.5, 1.0):
            with_signal = compute_svi(f, s, ModelSignals(p, c))
            assert with_signal.score >= without.score
            assert tier_rank(with_signal.tier) >= tier_rank(without.tier)


def test_channel_c_never_lowers_the_tier_across_the_whole_range():
    """Sweep the base band by band and confirm no combination of model output
    can move a case downward."""
    for n_factors in range(0, 7):
        keys = list(FACTORS_BY_KEY)[:n_factors]
        f = facts(present=set(keys))
        for phq in (0, 1, 2, 3):
            s = full_screen(phq9=[phq] * 9)
            base_tier = compute_svi(f, s, ModelSignals(0.0, 0.0)).tier
            worst = compute_svi(f, s, ModelSignals(1.0, 1.0)).tier
            assert tier_rank(worst) >= tier_rank(base_tier)


def test_channel_c_is_bounded():
    f, s = facts(), full_screen()
    r_none = compute_svi(f, s, ModelSignals(0.0, 0.0))
    r_max = compute_svi(f, s, ModelSignals(1.0, 1.0))
    assert r_max.channel_c_delta == pytest.approx(MAX_C_DELTA)
    assert r_max.score - r_none.score <= MAX_C_DELTA + 1e-9


def test_score_is_floored_at_the_deterministic_base():
    f, s = facts(present={"social_boycott_active"}), full_screen()
    r = compute_svi(f, s, ModelSignals(0.0, 0.0))
    assert r.score >= r.base


# ------------------------------------------- INVARIANT 2: abstention

def test_low_signal_confidence_zeroes_channel_c():
    f, s = facts(), full_screen()
    r = compute_svi(f, s, ModelSignals(1.0, 1.0, Confidence.LOW))
    assert r.channel_c_delta == 0.0
    assert "low_signal_confidence" in r.abstention_reasons


def test_low_signal_confidence_never_reduces_the_tier():
    """The case that matters: poor audio from a rural dialect speaker must not
    produce a calmer assessment than the same case with clean audio."""
    f = facts(offence_category="grievous_hurt", present={"prior_threats"})
    s = full_screen()
    clean = compute_svi(f, s, ModelSignals(0.8, 0.9, Confidence.OK))
    noisy = compute_svi(f, s, ModelSignals(0.8, 0.9, Confidence.LOW))
    assert tier_rank(noisy.tier) >= tier_rank(clean.tier)


def test_thin_context_coverage_escalates_one_tier():
    thin = ContextFacts(offence_category="intimidation_threat", asked=set())
    r = compute_svi(thin, full_screen(), ModelSignals())
    assert r.abstained
    assert "insufficient_context_coverage" in r.abstention_reasons
    unabstained = compute_svi(facts(), full_screen(), ModelSignals())
    assert tier_rank(r.tier) > tier_rank(unabstained.tier)


def test_thin_screening_coverage_escalates():
    r = compute_svi(facts(), Screeners(phq2=[0, 0], cssrs=CSSRSScreen(administered=True)))
    assert r.abstained
    assert "insufficient_screening_coverage" in r.abstention_reasons


def test_abstention_never_exceeds_one_tier():
    thin = ContextFacts(offence_category="unspecified", asked=set())
    r = compute_svi(thin, Screeners(cssrs=CSSRSScreen(administered=True)),
                    ModelSignals(0.0, 0.0, Confidence.LOW))
    assert r.tier is Tier.MODERATE     # LOW + exactly one step


def test_missing_instruments_are_never_imputed():
    """A domain we did not screen contributes nothing and reduces coverage. It
    must never be filled in with an assumed value."""
    partial = Screeners(phq9=[3] * 9, cssrs=CSSRSScreen(administered=True))
    assert partial.anxiety() is None
    assert partial.ptsd() is None
    assert partial.coverage() == 0.25


# ------------------------------------------- INVARIANT 3: rules over models

def test_cssrs_intent_forces_critical_from_the_lowest_possible_case():
    """The governing case. Everything else is as calm as it can be."""
    f = ContextFacts(offence_category="unspecified", asked=FULL_COVERAGE)
    s = full_screen(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5, impairment=0,
                    cssrs=CSSRSScreen(administered=True, q4=True))
    computed = compute_svi(f, s, ModelSignals(0.0, 0.0))
    assert computed.tier is Tier.LOW              # the score says Low
    outcome = apply_hard_rules(computed.tier, state(f, s))
    assert outcome.tier is Tier.CRITICAL          # the rule says Critical
    assert "cssrs_intent_or_behaviour" in outcome.triggered
    assert outcome.model_bypassed


def test_self_harm_lexicon_forces_critical():
    f, s = facts(), full_screen()
    computed = compute_svi(f, s, ModelSignals(0.0, 0.0))
    outcome = apply_hard_rules(computed.tier, state(f, s, {"self_harm"}))
    assert outcome.tier is Tier.CRITICAL


def test_imminent_threat_forces_critical():
    f = facts(present={"threat_imminent"})
    s = full_screen()
    outcome = apply_hard_rules(compute_svi(f, s).tier, state(f, s))
    assert outcome.tier is Tier.CRITICAL
    assert "imminent_threat_to_life" in outcome.triggered


def test_sexual_offence_against_minor_forces_critical():
    f = facts(offence_category="rape", present={"victim_minor"})
    s = full_screen()
    outcome = apply_hard_rules(compute_svi(f, s).tier, state(f, s))
    assert outcome.tier is Tier.CRITICAL


def test_missing_cssrs_prevents_a_low_close():
    f = facts()
    s = Screeners(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5, impairment=0,
                  cssrs=CSSRSScreen(administered=False))
    outcome = apply_hard_rules(compute_svi(f, s).tier, state(f, s))
    assert tier_rank(outcome.tier) >= tier_rank(Tier.HIGH)
    assert "cssrs_not_administered" in outcome.triggered


def test_rules_can_only_escalate():
    """Exhaustive: no rule combination may reduce a computed tier."""
    f = facts(offence_category="murder", present={"threat_imminent", "victim_minor"})
    s = full_screen(phq9=[3] * 9, gad7=[3] * 7, pc_ptsd5=[1] * 5, impairment=4,
                    cssrs=CSSRSScreen(administered=True, q5=True))
    for computed in Tier:
        outcome = apply_hard_rules(computed, state(f, s, {"self_harm"}))
        assert tier_rank(outcome.tier) >= tier_rank(computed)


def test_rules_that_change_nothing_are_not_recorded_as_triggered():
    f = facts(offence_category="murder")
    s = full_screen()
    outcome = apply_hard_rules(Tier.CRITICAL, state(f, s))
    assert outcome.triggered == ()
    assert not outcome.escalated


# ------------------------------------------- explainability and hygiene

def test_contributions_sum_to_the_score():
    f = facts(offence_category="grievous_hurt",
              present={"prior_threats", "fir_not_registered", "victim_alone"})
    s = full_screen(phq9=[2] * 9, gad7=[2] * 7, pc_ptsd5=[1, 1, 1, 0, 0], impairment=2)
    r = compute_svi(f, s, ModelSignals(0.5, 0.6))
    assert sum(r.contributions.values()) == pytest.approx(r.score, abs=0.05)


def test_unconfirmed_facts_count_less_than_confirmed_ones():
    confirmed = compute_svi(facts(present={"displaced_from_home"}), full_screen())
    provisional = compute_svi(facts(unconfirmed={"displaced_from_home"}), full_screen())
    assert provisional.score < confirmed.score


def test_engine_is_deterministic():
    f, s, m = facts(present={"prior_threats"}), full_screen(), ModelSignals(0.4, 0.7)
    results = {compute_svi(f, s, m).score for _ in range(50)}
    assert len(results) == 1


def test_tier_boundaries_are_exact():
    for tier, floor in TIER_FLOOR.items():
        assert tier_of(floor) is tier
        if floor > 0:
            assert tier_rank(tier_of(floor - 0.01)) == tier_rank(tier) - 1


def test_saturation_denominator_is_reachable():
    """A case carrying the six heaviest factors saturates Channel A's
    aggravating component at exactly 1.0."""
    heaviest = sorted(FACTORS_BY_KEY.values(), key=lambda f: -f.weight)[:6]
    f = facts(present={x.key for x in heaviest})
    assert f.aggravating_component() == pytest.approx(1.0)
    assert sum(x.weight for x in heaviest) == SATURATION_DENOMINATOR


def test_invalid_inputs_are_rejected_loudly():
    with pytest.raises(ValueError):
        ContextFacts(offence_category="not_a_real_offence")
    with pytest.raises(ValueError):
        ContextFacts(present={"not_a_real_factor"})
    with pytest.raises(ValueError):
        Screeners(phq9=[1, 2, 3])
    with pytest.raises(ValueError):
        Screeners(phq9=[9] * 9)
    with pytest.raises(ValueError):
        ModelSignals(distress_probability=1.4)
