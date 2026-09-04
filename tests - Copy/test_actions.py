"""
Policy table and orchestrator tests.

The table is data a non-programmer is expected to review and amend, so most of
these tests guard the table itself rather than the resolver. A typo in a
statutory reference or a dangling action id must fail the build, not a call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.events import Tier
from core.svi.factors import FACTORS_BY_KEY, OFFENCE_SEVERITY
from core.actions.orchestrator import (TABLE, PolicyTableError, load_table,
                                       resolve_actions, summarise)

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------- table integrity

def test_every_action_is_reachable_by_some_policy():
    """An action no policy can raise is dead weight in a document people are
    asked to trust. Either wire it up or delete it."""
    reachable = {a for p in TABLE.policies for a in p.then}
    assert set(TABLE.actions) == reachable


def test_every_action_names_an_owner_that_exists():
    for action in TABLE.actions.values():
        assert action.owner in TABLE.owners


def test_every_enforcement_action_cites_a_basis():
    """Information, follow-up and internal control actions may stand on
    practice alone. Anything that places a duty on an officer or claims an
    entitlement must name the provision it rests on."""
    exempt = {"FOLLOW_UP", "CONTROL"}
    for action in TABLE.actions.values():
        if action.type not in exempt:
            assert action.basis, f"{action.id} places a duty but cites no basis"


def test_policy_conditions_reference_real_facts_and_offences():
    for policy in TABLE.policies:
        assert (policy.facts_any | policy.facts_all) <= set(FACTORS_BY_KEY)
        assert policy.offence_any <= set(OFFENCE_SEVERITY)


def test_every_tier_has_a_baseline_policy():
    covered = {t for p in TABLE.policies for t in p.tiers}
    assert covered == {t.value for t in Tier}


def test_malformed_tables_are_rejected_at_load(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"owners":{},"actions":{},"policies":['
                   '{"id":"x","when":{},"then":["nope"]}]}', encoding="utf-8")
    with pytest.raises(PolicyTableError):
        load_table(bad)

    bad.write_text('{"owners":{"A":"a"},"actions":{"x":{"label":"L","type":"T",'
                   '"owner":"GHOST","sla_minutes":0}},"policies":['
                   '{"id":"p","when":{},"then":["x"]}]}', encoding="utf-8")
    with pytest.raises(PolicyTableError):
        load_table(bad)


# ------------------------------------------------- resolution behaviour

def test_every_tier_resolves_to_at_least_one_action():
    for tier in Tier:
        assert resolve_actions(tier, facts=set()) != []


def test_higher_tiers_never_produce_fewer_actions():
    counts = [len(resolve_actions(t, facts=set())) for t in Tier]
    assert counts == sorted(counts)


def test_critical_always_includes_an_immediate_warm_transfer():
    actions = resolve_actions(Tier.CRITICAL, facts=set())
    transfer = next(a for a in actions if a.action_id == "telemanas_warm_transfer")
    assert transfer.sla_minutes == 0 and transfer.immediate


def test_critical_can_never_be_auto_closed():
    ids = {a.action_id for a in resolve_actions(Tier.CRITICAL, facts=set())}
    assert "no_auto_close" in ids


def test_suicide_risk_fires_on_the_rule_not_the_tier():
    """The safety path must not depend on the score. A LOW-tier interaction
    with a positive C-SSRS still gets the crisis packet."""
    actions = resolve_actions(Tier.LOW, facts=set(),
                              rules_triggered={"cssrs_intent_or_behaviour"})
    ids = {a.action_id for a in actions}
    assert {"telemanas_warm_transfer", "crisis_safety_plan", "no_auto_close"} <= ids


def test_police_refusal_reaches_the_superintendent():
    actions = resolve_actions(Tier.MODERATE, facts={"police_refused_registration"})
    sp = next(a for a in actions if a.action_id == "sp_complaint_refusal")
    assert sp.owner == "SP"
    assert "s.4" in sp.basis


def test_minor_victim_notifies_the_child_welfare_committee():
    ids = {a.action_id for a in resolve_actions(Tier.HIGH, facts={"victim_minor"})}
    assert "cwc_notification" in ids


def test_homicide_raises_dependent_relief_regardless_of_tier():
    for tier in Tier:
        ids = {a.action_id for a in resolve_actions(tier, facts=set(),
                                                    offence_category="murder")}
        assert "dependent_relief" in ids


def test_actions_are_deduplicated_and_record_every_reason():
    """protection_assessment is raised by the critical tier, by imminent
    threat and by sexual offence. It must appear once, citing all three."""
    actions = resolve_actions(Tier.CRITICAL, facts={"threat_imminent"},
                              offence_category="rape")
    matches = [a for a in actions if a.action_id == "protection_assessment"]
    assert len(matches) == 1
    assert len(matches[0].triggered_by) >= 3


def test_deadlines_are_computed_from_the_supplied_clock():
    actions = resolve_actions(Tier.HIGH, facts=set(), now=NOW)
    for a in actions:
        assert a.due_at == NOW + timedelta(minutes=a.sla_minutes)


def test_resolution_without_a_clock_leaves_deadlines_unset():
    assert all(a.due_at is None for a in resolve_actions(Tier.HIGH, facts=set()))


def test_ordering_is_by_deadline_then_stable():
    actions = resolve_actions(Tier.CRITICAL, facts={"witness_pressure"}, now=NOW)
    assert [a.sla_minutes for a in actions] == sorted(a.sla_minutes for a in actions)
    assert actions == resolve_actions(Tier.CRITICAL, facts={"witness_pressure"}, now=NOW)


def test_summarise_counts_by_owner():
    actions = resolve_actions(Tier.CRITICAL, facts={"victim_minor"})
    counts = summarise(actions)
    assert sum(counts.values()) == len(actions)
    assert "TELEMANAS" in counts
