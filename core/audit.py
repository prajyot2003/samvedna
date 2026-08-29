"""
The tamper-evident audit ledger.

Every SVI snapshot, every human override, every consent decision, every action
raised and every scheduled deletion is written here as a link in a hash chain.
Each record commits to the one before it, so altering or removing anything in
the history invalidates every link after it.

This is not encryption and it is not a blockchain. It is a Merkle-style linked
hash chain: it does not prevent a person with database access from rewriting
history, it makes rewritten history *detectable* by anyone who re-walks the
chain. For an accountability record about how a victim was triaged, detection
is the property that matters.

Standard library only, deliberately. Persistence lives in the service layer;
what is here is the part a reviewer must be able to re-implement from the
docstring and use to check our stored chain independently.

CANONICALISATION
The hash is taken over a canonical JSON encoding: keys sorted, no insignificant
whitespace, UTF-8, no non-finite floats. Two processes must produce byte-identical
input for the same logical record or verification becomes a coin flip, so the
encoder is fixed here and never inferred from a library default.

WHAT GOES IN A PAYLOAD
Redacted content only. PII redaction happens upstream, before persistence; the
ledger records that an event occurred, its decision-relevant content, and who
was responsible. It is not a transcript store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GENESIS_HASH = "0" * 64
HASH_NAME = "sha256"


class AuditEventType(str, Enum):
    """Closed set. A new kind of auditable event is a deliberate schema change,
    not a free-text string someone typed at a call site."""
    INTERACTION_STARTED = "interaction_started"
    CONSENT_RECORDED = "consent_recorded"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    SVI_COMPUTED = "svi_computed"
    RULE_TRIGGERED = "rule_triggered"
    TIER_OVERRIDDEN = "tier_overridden"
    ACTION_RAISED = "action_raised"
    ACTION_COMPLETED = "action_completed"
    INTERACTION_CLOSED = "interaction_closed"
    RETENTION_PURGE = "retention_purge"
    ACCESS_GRANTED = "access_granted"


def canonical_json(payload: Any) -> bytes:
    """The one encoding the chain is defined over. Changing this function
    invalidates every stored chain, so it is versioned with the ledger format
    and must never be 'tidied'."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_encode_unknown,
    ).encode("utf-8")


def _encode_unknown(obj: Any) -> Any:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ValueError("naive datetime in audit payload; use timezone-aware UTC")
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"{type(obj).__name__} is not serialisable into an audit payload")


def normalise_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a payload to plain JSON types once, before it is either hashed
    or stored.

    Without this the two diverge: `canonical_json` knows how to encode a
    datetime or a set, but the database's JSON column does not, so a payload
    could hash cleanly and then fail to persist — or worse, persist in a
    slightly different shape and fail verification later. Normalising first
    means the digest is taken over exactly the bytes that end up stored.
    """
    return json.loads(canonical_json(payload).decode("utf-8"))


def compute_hash(prev_hash: str,
                 seq: int,
                 event_type: AuditEventType,
                 interaction_id: str,
                 actor: str,
                 ts: datetime,
                 payload: Dict[str, Any]) -> str:
    """Hash over the full record, not the payload alone.

    Sequence number, timestamp, actor and interaction id are inside the digest,
    so a record cannot be silently moved between interactions, reordered, or
    reattributed to a different counsellor while keeping its hash valid.
    """
    if ts.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    body = canonical_json({
        "prev": prev_hash,
        "seq": seq,
        "type": event_type.value,
        "interaction_id": interaction_id,
        "actor": actor,
        "ts": ts.astimezone(timezone.utc).isoformat(),
        "payload": payload,
    })
    return hashlib.new(HASH_NAME, body).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    event_type: AuditEventType
    interaction_id: str
    actor: str
    ts: datetime
    payload: Dict[str, Any]
    prev_hash: str
    record_hash: str

    def recompute(self) -> str:
        return compute_hash(self.prev_hash, self.seq, self.event_type,
                            self.interaction_id, self.actor, self.ts, self.payload)

    @property
    def intact(self) -> bool:
        return self.record_hash == self.recompute()


def make_record(prev: Optional[AuditRecord],
                event_type: AuditEventType,
                interaction_id: str,
                actor: str,
                payload: Dict[str, Any],
                ts: Optional[datetime] = None) -> AuditRecord:
    """Build the next link. `prev` is None only for the very first record in
    the ledger, which chains from the genesis hash."""
    prev_hash = prev.record_hash if prev is not None else GENESIS_HASH
    seq = (prev.seq + 1) if prev is not None else 0
    ts = ts or datetime.now(timezone.utc)
    payload = normalise_payload(payload)
    return AuditRecord(
        seq=seq,
        event_type=event_type,
        interaction_id=interaction_id,
        actor=actor,
        ts=ts,
        payload=payload,
        prev_hash=prev_hash,
        record_hash=compute_hash(prev_hash, seq, event_type, interaction_id,
                                 actor, ts, payload),
    )


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    length: int
    head: str
    failures: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    def summary(self) -> str:
        if self.ok:
            return f"chain intact — {self.length} records, head {self.head[:12]}…"
        first = self.failures[0]
        return (f"CHAIN BROKEN at seq {first['seq']}: {first['reason']} "
                f"({len(self.failures)} problem(s) across {self.length} records)")


def verify_chain(records: Sequence[AuditRecord]) -> ChainVerification:
    """Re-walk the whole ledger.

    Reports every problem rather than stopping at the first, because a reviewer
    needs the full extent of a discrepancy, not its earliest symptom. Three
    distinct failures are distinguished: a record whose own hash does not match
    its contents (edited), a record whose prev_hash does not match its
    predecessor (spliced or removed), and a sequence discontinuity (truncated
    or reordered).
    """
    failures: List[Dict[str, Any]] = []
    expected_prev = GENESIS_HASH
    expected_seq = 0

    for record in records:
        if record.seq != expected_seq:
            failures.append({"seq": record.seq, "reason":
                             f"sequence discontinuity, expected {expected_seq}"})
        if record.prev_hash != expected_prev:
            failures.append({"seq": record.seq, "reason":
                             "prev_hash does not match the preceding record"})
        if not record.intact:
            failures.append({"seq": record.seq, "reason":
                             "record contents do not match its stored hash"})
        expected_prev = record.record_hash
        expected_seq = record.seq + 1

    return ChainVerification(
        ok=not failures,
        length=len(records),
        head=records[-1].record_hash if records else GENESIS_HASH,
        failures=tuple(failures),
    )
