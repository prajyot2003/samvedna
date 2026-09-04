"""
Persistence and ledger-integration tests.

Run against a real SQLite database, not a mock, because the properties being
tested — unique-constraint contention, timezone round-tripping, transactional
pairing of a domain write with its ledger entry — are properties of the
database, and a mock would assert only that we called the functions we wrote.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.audit import AuditEventType
from core.events import Tier
from core.actions.orchestrator import resolve_actions
from core.rules.hard_rules import TriageState, apply_hard_rules
from core.svi.engine import ModelSignals, compute_svi
from core.svi.factors import CORE_COVERAGE_KEYS, ContextFacts
from core.svi.instruments import CSSRSScreen, Screeners
from services.store import models
from services.store.repo import ChainAppendError, Repository

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
ASKED = set(CORE_COVERAGE_KEYS)


@pytest.fixture()
def repo(tmp_path):
    r = Repository(f"sqlite:///{tmp_path/'test.db'}")
    r.create_all()
    return r


def a_full_screen(**kw):
    base = dict(phq9=[2] * 9, gad7=[2] * 7, pc_ptsd5=[1, 1, 0, 0, 0], impairment=2,
                cssrs=CSSRSScreen(administered=True))
    base.update(kw)
    return Screeners(**base)


# ------------------------------------------------- ledger integration

def test_starting_an_interaction_writes_the_first_ledger_entry(repo):
    repo.start_interaction("int-1", "ivrs", "hi", district="Gaya")
    chain = repo.load_chain()
    assert len(chain) == 1
    assert chain[0].event_type is AuditEventType.INTERACTION_STARTED
    assert repo.verify().ok


def test_timestamps_survive_the_round_trip_and_the_chain_still_verifies(repo):
    """SQLite drops tzinfo. If reads did not reattach UTC, every chain would
    fail verification after a restart — silently, and only in production."""
    repo.start_interaction("int-1", "ivrs", "hi")
    repo.record_consent("int-1", "analysis", "granted", "hi", "v1", "spoken")
    reloaded = Repository(str(repo.engine.url))
    for record in reloaded.load_chain():
        assert record.ts.tzinfo is not None
    assert reloaded.verify().ok


def test_declining_analysis_consent_puts_the_interaction_in_passive_mode(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    repo.record_consent("int-1", "analysis", "declined", "hi", "v1", "spoken")
    with repo.session() as s:
        assert s.get(models.Interaction, "int-1").passive_mode is True
    assert repo.verify().ok


def test_withdrawn_consent_is_recorded_as_its_own_event_type(repo):
    repo.start_interaction("int-1", "portal", "bho")
    repo.record_consent("int-1", "analysis", "granted", "bho", "v1", "web_checkbox")
    repo.record_consent("int-1", "analysis", "withdrawn", "bho", "v1", "web_checkbox")
    types = [r.event_type for r in repo.load_chain()]
    assert AuditEventType.CONSENT_WITHDRAWN in types
    with repo.session() as s:
        assert s.get(models.Interaction, "int-1").passive_mode is True


def test_a_snapshot_with_rules_writes_two_linked_ledger_entries(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    facts = ContextFacts(offence_category="verbal_abuse_caste_slur", asked=ASKED)
    screeners = a_full_screen(phq9=[0] * 9, gad7=[0] * 7, pc_ptsd5=[0] * 5,
                              impairment=0,
                              cssrs=CSSRSScreen(administered=True, q4=True))
    result = compute_svi(facts, screeners, ModelSignals(0.0, 0.0))
    outcome = apply_hard_rules(result.tier, TriageState(facts, screeners))
    row = repo.save_snapshot("int-1", result, outcome)

    assert row.tier == Tier.LOW.value
    assert row.final_tier == Tier.CRITICAL.value
    assert row.model_bypassed is True
    types = [r.event_type for r in repo.load_chain()]
    assert types.count(AuditEventType.RULE_TRIGGERED) == 1
    assert repo.verify().ok


def test_an_override_without_a_reason_is_refused(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    facts = ContextFacts(offence_category="intimidation_threat", asked=ASKED)
    screeners = a_full_screen()
    result = compute_svi(facts, screeners)
    outcome = apply_hard_rules(result.tier, TriageState(facts, screeners))
    snap = repo.save_snapshot("int-1", result, outcome)
    for empty in ("", "   "):
        with pytest.raises(ValueError):
            repo.record_override("int-1", snap.id, "HIGH", "MODERATE", "c-1", empty)


def test_an_override_is_attributed_to_the_counsellor_in_the_ledger(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    facts = ContextFacts(offence_category="intimidation_threat", asked=ASKED)
    screeners = a_full_screen()
    result = compute_svi(facts, screeners)
    outcome = apply_hard_rules(result.tier, TriageState(facts, screeners))
    snap = repo.save_snapshot("int-1", result, outcome)
    repo.record_override("int-1", snap.id, outcome.tier.value, "MODERATE",
                         "counsellor-17", "Caller has family support present; "
                                          "verified in-person follow-up already arranged.")
    entry = [r for r in repo.load_chain()
             if r.event_type is AuditEventType.TIER_OVERRIDDEN][0]
    assert entry.actor == "counsellor-17"
    assert "family support" in entry.payload["reason"]
    assert repo.verify().ok


def test_actions_persist_with_deadlines_and_a_ledger_entry_each(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    actions = resolve_actions(Tier.CRITICAL, facts={"victim_minor"},
                              offence_category="rape", now=NOW)
    rows = repo.raise_actions("int-1", actions)
    assert len(rows) == len(actions)
    raised = [r for r in repo.load_chain()
              if r.event_type is AuditEventType.ACTION_RAISED]
    assert len(raised) == len(actions)
    assert repo.verify().ok


def test_actions_without_a_deadline_are_refused(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    with pytest.raises(ValueError):
        repo.raise_actions("int-1", resolve_actions(Tier.HIGH, facts=set()))


# ------------------------------------------------- tamper detection on real storage

def test_editing_a_stored_row_breaks_verification(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    repo.record_consent("int-1", "analysis", "granted", "hi", "v1", "spoken")
    repo.record_consent("int-1", "retention", "granted", "hi", "v1", "spoken")
    assert repo.verify().ok

    with repo.session() as s:
        row = s.get(models.AuditEntry, 1)
        row.payload = {**row.payload, "decision": "declined"}

    result = repo.verify()
    assert not result.ok
    assert "BROKEN" in result.summary()


def test_deleting_a_stored_row_breaks_verification(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    for scope in ("analysis", "retention", "referral"):
        repo.record_consent("int-1", scope, "granted", "hi", "v1", "spoken")
    with repo.session() as s:
        s.delete(s.get(models.AuditEntry, 2))
    assert not repo.verify().ok


# ------------------------------------------------- concurrency

def test_concurrent_appends_do_not_fork_the_chain(repo):
    """The property the UNIQUE constraint on seq exists to guarantee. Without
    the retry loop this produces duplicate sequence numbers and a chain that
    cannot be verified."""
    repo.start_interaction("int-1", "ivrs", "hi")
    errors = []

    def worker(n: int):
        try:
            for i in range(6):
                repo.append_audit(AuditEventType.ACTION_COMPLETED, "int-1",
                                  f"worker-{n}", {"i": i})
        except Exception as exc:            # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"append failures: {errors}"
    chain = repo.load_chain()
    assert len(chain) == 25                      # 1 start + 4 workers x 6
    assert [r.seq for r in chain] == list(range(25))
    assert repo.verify().ok


# ------------------------------------------------- retention

def test_expired_audio_is_purged_and_the_purge_is_itself_audited(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    with repo.session() as s:
        s.add(models.AudioBlob(interaction_id="int-1", data=b"old", sample_rate=8000,
                               created_at=NOW - timedelta(days=45)))
        s.add(models.AudioBlob(interaction_id="int-1", data=b"recent", sample_rate=8000,
                               created_at=NOW - timedelta(days=3)))

    deleted = repo.purge_expired_audio(now=NOW, retention_days=30)
    assert deleted == 1

    with repo.session() as s:
        assert s.query(models.AudioBlob).count() == 1

    purge = [r for r in repo.load_chain()
             if r.event_type is AuditEventType.RETENTION_PURGE][0]
    assert purge.payload["deleted_blobs"] == 1
    assert purge.actor == "retention-job"
    assert repo.verify().ok


def test_a_purge_with_nothing_to_delete_writes_no_ledger_noise(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    before = len(repo.load_chain())
    assert repo.purge_expired_audio(now=NOW, retention_days=30) == 0
    assert len(repo.load_chain()) == before


# ------------------------------------------------- dashboard queries

def test_tier_distribution_counts_each_interaction_once_at_its_latest_snapshot(repo):
    facts = ContextFacts(offence_category="social_boycott",
                         present={"social_boycott_active"}, asked=ASKED)
    screeners = a_full_screen()
    for iid in ("int-1", "int-2"):
        repo.start_interaction(iid, "ivrs", "hi")
        for signal in (ModelSignals(0.1, 0.5), ModelSignals(0.9, 0.9)):
            result = compute_svi(facts, screeners, signal)
            outcome = apply_hard_rules(result.tier, TriageState(facts, screeners))
            repo.save_snapshot(iid, result, outcome)

    dist = repo.tier_distribution()
    assert sum(dist.values()) == 2


def test_overdue_actions_are_returned_worst_first(repo):
    repo.start_interaction("int-1", "ivrs", "hi")
    repo.raise_actions("int-1", resolve_actions(Tier.CRITICAL, facts=set(), now=NOW))
    overdue = repo.overdue_actions(now=NOW + timedelta(hours=6))
    assert overdue
    dues = [d.due_at for d in overdue]
    assert dues == sorted(dues)
    assert all(a.status == "open" for a in overdue)
