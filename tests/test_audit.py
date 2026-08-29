"""
Audit ledger tests.

The claim being defended is: "any alteration to this record is detectable."
These tests are the evidence for that claim, so each one performs an actual
tampering attempt and asserts that verification catches it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from core.audit import (GENESIS_HASH, AuditEventType, AuditRecord, ChainVerification,
                        canonical_json, compute_hash, make_record, verify_chain)

T0 = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def build_chain(n: int = 6):
    records = []
    prev = None
    for i in range(n):
        prev = make_record(
            prev,
            AuditEventType.SVI_COMPUTED,
            interaction_id="int-001",
            actor="counsellor-17",
            payload={"score": 40 + i, "tier": "MODERATE"},
            ts=T0 + timedelta(minutes=i),
        )
        records.append(prev)
    return records


# ------------------------------------------------- construction

def test_first_record_chains_from_genesis():
    r = make_record(None, AuditEventType.INTERACTION_STARTED, "int-001", "system",
                    {"channel": "ivrs"}, ts=T0)
    assert r.prev_hash == GENESIS_HASH
    assert r.seq == 0
    assert r.intact


def test_a_valid_chain_verifies():
    result = verify_chain(build_chain())
    assert result.ok
    assert result.length == 6
    assert "intact" in result.summary()


def test_empty_chain_is_trivially_valid():
    result = verify_chain([])
    assert result.ok and result.head == GENESIS_HASH


# ------------------------------------------------- tamper detection

def test_editing_a_payload_is_detected():
    records = build_chain()
    records[2] = replace(records[2], payload={"score": 5, "tier": "LOW"})
    result = verify_chain(records)
    assert not result.ok
    assert any(f["seq"] == 2 and "stored hash" in f["reason"] for f in result.failures)


def test_reattributing_a_record_to_another_counsellor_is_detected():
    records = build_chain()
    records[3] = replace(records[3], actor="counsellor-99")
    assert not verify_chain(records).ok


def test_backdating_a_record_is_detected():
    records = build_chain()
    records[1] = replace(records[1], ts=T0 - timedelta(days=2))
    assert not verify_chain(records).ok


def test_moving_a_record_to_another_interaction_is_detected():
    records = build_chain()
    records[4] = replace(records[4], interaction_id="int-999")
    assert not verify_chain(records).ok


def test_deleting_a_record_from_the_middle_is_detected():
    records = build_chain()
    del records[3]
    result = verify_chain(records)
    assert not result.ok
    assert any("prev_hash" in f["reason"] for f in result.failures)


def test_truncating_the_tail_is_detected_by_the_head_hash():
    """Dropping trailing records leaves a self-consistent chain, which is why
    the head hash has to be published or anchored separately."""
    full = build_chain()
    truncated = full[:-2]
    assert verify_chain(truncated).ok            # internally consistent
    assert verify_chain(truncated).head != verify_chain(full).head


def test_reordering_records_is_detected():
    records = build_chain()
    records[2], records[3] = records[3], records[2]
    assert not verify_chain(records).ok


def test_a_forged_record_cannot_be_spliced_in():
    """An attacker who can write to the table but recomputes only their own
    record's hash still breaks the link to the next one."""
    records = build_chain()
    forged = make_record(records[1], AuditEventType.TIER_OVERRIDDEN, "int-001",
                         "counsellor-17", {"to_tier": "LOW"}, ts=T0)
    records[2] = forged
    assert not verify_chain(records).ok


def test_all_failures_are_reported_not_just_the_first():
    records = build_chain()
    records[1] = replace(records[1], payload={"score": 0})
    records[4] = replace(records[4], actor="ghost")
    assert len({f["seq"] for f in verify_chain(records).failures}) >= 2


# ------------------------------------------------- canonicalisation

def test_key_order_does_not_change_the_hash():
    a = {"alpha": 1, "beta": {"x": 1, "y": 2}}
    b = {"beta": {"y": 2, "x": 1}, "alpha": 1}
    assert canonical_json(a) == canonical_json(b)


def test_hash_is_stable_across_runs():
    args = (GENESIS_HASH, 0, AuditEventType.CONSENT_RECORDED, "int-001",
            "system", T0, {"scope": "analysis", "decision": "granted"})
    assert len({compute_hash(*args) for _ in range(20)}) == 1


def test_sets_and_enums_serialise_deterministically():
    payload = {"rules": {"b_rule", "a_rule"}, "type": AuditEventType.RULE_TRIGGERED}
    assert canonical_json(payload) == canonical_json(
        {"rules": {"a_rule", "b_rule"}, "type": AuditEventType.RULE_TRIGGERED})


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        compute_hash(GENESIS_HASH, 0, AuditEventType.SVI_COMPUTED, "i", "a",
                     datetime(2026, 8, 29, 9, 0), {})


def test_non_finite_floats_are_rejected():
    with pytest.raises(ValueError):
        canonical_json({"score": float("nan")})


def test_unserialisable_objects_are_rejected_loudly():
    with pytest.raises(TypeError):
        canonical_json({"obj": object()})


def test_payloads_are_normalised_to_json_native_types():
    """A payload carrying a datetime, a set or an enum must be flattened before
    it is hashed, so the digest covers exactly what a JSON column can store."""
    from core.audit import normalise_payload

    flat = normalise_payload({
        "due_at": T0,
        "rules": {"b", "a"},
        "type": AuditEventType.ACTION_RAISED,
        "nested": {"when": T0 + timedelta(hours=1)},
    })
    import json as _json
    _json.dumps(flat)                       # must not raise
    assert flat["due_at"] == T0.isoformat()
    assert flat["rules"] == ["a", "b"]
    assert flat["nested"]["when"].endswith("+00:00")


def test_a_record_built_from_rich_types_verifies_after_flattening():
    r = make_record(None, AuditEventType.ACTION_RAISED, "int-1", "system",
                    {"due_at": T0, "owners": {"SP", "DM"}}, ts=T0)
    assert r.intact
    assert verify_chain([r]).ok
