"""
Repository layer.

Everything that writes to the ledger goes through `append_audit`, and every
domain write that matters is paired with a ledger entry in the same
transaction. If the case record says an override happened, the ledger says so
too, or neither is committed.

TWO THINGS WORTH READING CLOSELY:

1. CHAIN CONCURRENCY. Appending to a hash chain is a read-modify-write on the
   head, and it must be serialised.

   Where the backend offers a lock, appenders take one: on PostgreSQL a
   transaction-scoped advisory lock, released automatically at commit or
   rollback. Writers then queue instead of colliding.

   The UNIQUE constraint on `seq` remains as the backstop that makes a fork
   impossible rather than merely unlikely — a second process on a backend
   without advisory locks, or a bug in this file, still cannot produce two
   records with the same sequence number. A loser re-reads the new head and
   recomputes against it, with bounded, jittered retries.

   THIS WAS FOUND BY RUNNING THE SUITE AGAINST REAL POSTGRES. On SQLite the
   single-writer lock serialised appenders and hid the contention entirely;
   four threads on PostgreSQL exhausted the retry budget and raised. A test
   that only ever ran against SQLite would have shipped this.

2. TIMEZONES ON SQLITE. SQLite has no native timezone-aware type, so
   `DateTime(timezone=True)` round-trips as a naive value. `core.audit` refuses
   naive datetimes by design, which would make every chain unverifiable after
   a restart. All stored timestamps are UTC by construction, so reads reattach
   UTC explicitly rather than letting a silent naive value poison the hash.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from core.audit import (GENESIS_HASH, AuditEventType, AuditRecord, ChainVerification,
                        compute_hash, normalise_payload, verify_chain)
from services.config import SETTINGS, normalise_database_url
from services.store import models
from services.store.models import Base

MAX_APPEND_RETRIES = 8
RETRY_BASE_SECONDS = 0.01

# Any stable 64-bit constant. Every appender takes the same lock, which is the
# point: the chain has one head, so it has one queue.
AUDIT_CHAIN_LOCK_KEY = 0x5A4D5645444E41       # "ZMVEDNA"


def _jittered_backoff(attempt: int) -> float:
    """Exponential with jitter. Without the jitter, writers that collided once
    wake at the same instant and collide again — which is how a retry loop
    becomes a thundering herd and exhausts its budget under exactly the load it
    was meant to survive."""
    import random
    return RETRY_BASE_SECONDS * (2 ** attempt) * (0.5 + random.random())


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Stored timestamps are UTC by construction. Backends that drop tzinfo
    (SQLite) get it reattached here rather than silently producing a naive
    datetime that `core.audit` would reject."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class ChainAppendError(RuntimeError):
    """The ledger could not be extended after repeated contention. Loud by
    design: a system that cannot record what it did must not carry on doing it."""


class Repository:
    def __init__(self, database_url: Optional[str] = None, echo: Optional[bool] = None):
        # Normalise here, not only in settings. Scripts take a --database-url
        # and a person pasting a provider's `postgres://` string into one would
        # otherwise get "No module named 'psycopg2'" — an error that sends you
        # looking for a missing package when the problem is a missing driver
        # name in a URL you were handed.
        url = normalise_database_url(database_url or SETTINGS.database_url)
        kwargs: Dict[str, Any] = {"echo": SETTINGS.echo_sql if echo is None else echo}
        if url.startswith("sqlite"):
            # Serialise writers; a hash chain has no meaningful partial order.
            kwargs["connect_args"] = {"timeout": 30}
        self.engine = create_engine(url, **kwargs)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    def _head(self, s: Session) -> Optional[models.AuditEntry]:
        return s.execute(
            select(models.AuditEntry).order_by(models.AuditEntry.seq.desc()).limit(1)
        ).scalar_one_or_none()

    def append_audit(self,
                     event_type: AuditEventType,
                     interaction_id: str,
                     actor: str,
                     payload: Dict[str, Any],
                     ts: Optional[datetime] = None,
                     session: Optional[Session] = None) -> models.AuditEntry:
        """Extend the chain by one record.

        When `session` is supplied the entry joins that transaction, so a
        domain write and its ledger entry commit or fail together. Retry on
        contention is only available for standalone appends — inside a caller's
        transaction a conflict must surface to the caller, who owns the retry.
        """
        ts = ts or datetime.now(timezone.utc)

        if session is not None:
            return self._append_in(session, event_type, interaction_id, actor, payload, ts)

        last_error: Optional[Exception] = None
        for attempt in range(MAX_APPEND_RETRIES):
            try:
                with self.session() as s:
                    return self._append_in(s, event_type, interaction_id, actor, payload, ts)
            except IntegrityError as exc:
                last_error = exc          # another writer took our sequence number
                time.sleep(_jittered_backoff(attempt))
                continue
        raise ChainAppendError(
            f"could not append to the audit chain after {MAX_APPEND_RETRIES} attempts"
        ) from last_error

    def _lock_chain(self, s: Session) -> None:
        """Serialise appenders where the backend can. Transaction-scoped, so it
        is released on commit or rollback without a code path that could leak
        it."""
        if s.bind is not None and s.bind.dialect.name == "postgresql":
            s.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                      {"key": AUDIT_CHAIN_LOCK_KEY})

    def _append_in(self, s: Session, event_type, interaction_id, actor, payload, ts):
        # Flatten to JSON-native types before hashing, so the digest covers
        # exactly what the JSON column will store. See core.audit.normalise_payload.
        payload = normalise_payload(payload)
        self._lock_chain(s)
        head = self._head(s)
        prev_hash = head.record_hash if head else GENESIS_HASH
        seq = (head.seq + 1) if head else 0
        entry = models.AuditEntry(
            seq=seq,
            event_type=event_type.value,
            interaction_id=interaction_id,
            actor=actor,
            ts=ts,
            payload=payload,
            prev_hash=prev_hash,
            record_hash=compute_hash(prev_hash, seq, event_type, interaction_id,
                                     actor, ts, payload),
        )
        s.add(entry)
        s.flush()
        return entry

    def load_chain(self, session: Optional[Session] = None) -> List[AuditRecord]:
        def _load(s: Session) -> List[AuditRecord]:
            rows = s.execute(
                select(models.AuditEntry).order_by(models.AuditEntry.seq)
            ).scalars().all()
            return [
                AuditRecord(
                    seq=r.seq,
                    event_type=AuditEventType(r.event_type),
                    interaction_id=r.interaction_id,
                    actor=r.actor,
                    ts=_as_utc(r.ts),
                    payload=r.payload,
                    prev_hash=r.prev_hash,
                    record_hash=r.record_hash,
                )
                for r in rows
            ]
        if session is not None:
            return _load(session)
        with self.session() as s:
            return _load(s)

    def verify(self) -> ChainVerification:
        """Re-walk the stored ledger. This is what `GET /audit/verify` calls
        and what `make verify-audit` runs."""
        return verify_chain(self.load_chain())

    # ------------------------------------------------------------------
    # Domain writes — each paired with its ledger entry, one transaction
    # ------------------------------------------------------------------

    def start_interaction(self, interaction_id: str, channel: str, language: str,
                          district: Optional[str] = None) -> models.Interaction:
        with self.session() as s:
            row = models.Interaction(id=interaction_id, channel=channel,
                                     language=language, district=district)
            s.add(row)
            s.flush()
            self._append_in(s, AuditEventType.INTERACTION_STARTED, interaction_id,
                            "system", {"channel": channel, "language": language,
                                       "district": district},
                            datetime.now(timezone.utc))
            return row

    def record_consent(self, interaction_id: str, scope: str, decision: str,
                       language: str, script_version: str, method: str) -> models.Consent:
        """Declining analysis consent flips the interaction into passive mode in
        the same transaction that records the decision, so there is no window in
        which the system could score someone who has just refused."""
        with self.session() as s:
            row = models.Consent(interaction_id=interaction_id, scope=scope,
                                 decision=decision, language=language,
                                 script_version=script_version, method=method)
            s.add(row)
            if scope == "analysis" and decision in {"declined", "withdrawn"}:
                interaction = s.get(models.Interaction, interaction_id)
                if interaction is not None:
                    interaction.passive_mode = True
            event = (AuditEventType.CONSENT_WITHDRAWN if decision == "withdrawn"
                     else AuditEventType.CONSENT_RECORDED)
            s.flush()
            self._append_in(s, event, interaction_id, "system",
                            {"scope": scope, "decision": decision,
                             "script_version": script_version, "method": method},
                            datetime.now(timezone.utc))
            return row

    def save_snapshot(self, interaction_id: str, result, outcome,
                      actor: str = "system") -> models.SVISnapshot:
        """Persist one SVI computation plus its rule outcome. `result` is a
        core.svi.engine.SVIResult; `outcome` a core.rules.hard_rules.RuleOutcome."""
        with self.session() as s:
            row = models.SVISnapshot(
                interaction_id=interaction_id,
                score=result.score,
                tier=result.tier.value,
                final_tier=outcome.tier.value,
                channel_a=result.channel_a,
                channel_b=result.channel_b,
                channel_c_delta=result.channel_c_delta,
                base=result.base,
                abstained=result.abstained,
                rules_triggered=list(outcome.triggered),
                model_bypassed=outcome.model_bypassed,
                contributions=result.contributions,
            )
            s.add(row)
            s.flush()
            now = datetime.now(timezone.utc)
            self._append_in(s, AuditEventType.SVI_COMPUTED, interaction_id, actor,
                            {"score": result.score, "tier": result.tier.value,
                             "final_tier": outcome.tier.value,
                             "abstained": result.abstained,
                             "abstention_reasons": list(result.abstention_reasons)}, now)
            if outcome.triggered:
                self._append_in(s, AuditEventType.RULE_TRIGGERED, interaction_id, actor,
                                {"rules": list(outcome.triggered),
                                 "bases": list(outcome.bases),
                                 "resulting_tier": outcome.tier.value,
                                 "model_bypassed": outcome.model_bypassed}, now)
            return row

    def record_override(self, interaction_id: str, snapshot_id: int,
                        from_tier: str, to_tier: str, counsellor_id: str,
                        reason: str) -> models.Override:
        """A reason is mandatory. An override without one is not recorded at
        all, because an unexplained reversal is worse than none."""
        if not reason or not reason.strip():
            raise ValueError("an override requires a stated reason")
        with self.session() as s:
            row = models.Override(interaction_id=interaction_id, snapshot_id=snapshot_id,
                                  from_tier=from_tier, to_tier=to_tier,
                                  counsellor_id=counsellor_id, reason=reason.strip())
            s.add(row)
            s.flush()
            self._append_in(s, AuditEventType.TIER_OVERRIDDEN, interaction_id,
                            counsellor_id,
                            {"from_tier": from_tier, "to_tier": to_tier,
                             "snapshot_id": snapshot_id, "reason": reason.strip()},
                            datetime.now(timezone.utc))
            return row

    def raise_actions(self, interaction_id: str, actions: Sequence,
                      actor: str = "system") -> List[models.Action]:
        """`actions` is a sequence of core.actions.orchestrator.ResolvedAction
        with deadlines already computed."""
        with self.session() as s:
            rows = []
            now = datetime.now(timezone.utc)
            for a in actions:
                if a.due_at is None:
                    raise ValueError(f"action {a.action_id} has no deadline; "
                                     "resolve with an explicit clock before persisting")
                row = models.Action(
                    interaction_id=interaction_id, action_id=a.action_id,
                    label=a.label, type=a.type, owner=a.owner,
                    statutory_basis=a.basis, triggered_by=list(a.triggered_by),
                    sla_minutes=a.sla_minutes, due_at=a.due_at,
                )
                s.add(row)
                rows.append(row)
            s.flush()
            for a in actions:
                self._append_in(s, AuditEventType.ACTION_RAISED, interaction_id, actor,
                                {"action_id": a.action_id, "owner": a.owner,
                                 "basis": a.basis, "sla_minutes": a.sla_minutes,
                                 "due_at": a.due_at, "triggered_by": list(a.triggered_by)},
                                now)
            return rows

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def latest_snapshot(self, interaction_id: str) -> Optional[models.SVISnapshot]:
        with self.session() as s:
            return s.execute(
                select(models.SVISnapshot)
                .where(models.SVISnapshot.interaction_id == interaction_id)
                .order_by(models.SVISnapshot.computed_at.desc(),
                          models.SVISnapshot.id.desc())
                .limit(1)
            ).scalar_one_or_none()

    def snapshot_history(self, interaction_id: str, limit: int = 200) -> List[models.SVISnapshot]:
        """Every recomputation for one interaction, oldest first.

        The table is append-only for exactly this reason: a caller who becomes
        more distressed as they describe what happened looks different from one
        who was distressed from the first word, and only the trajectory shows
        that. Storing it and never displaying it was a waste of the decision.
        """
        with self.session() as s:
            return list(s.execute(
                select(models.SVISnapshot)
                .where(models.SVISnapshot.interaction_id == interaction_id)
                .order_by(models.SVISnapshot.id)
                .limit(limit)
            ).scalars().all())

    def overdue_actions(self, now: Optional[datetime] = None) -> List[models.Action]:
        """SLA breaches, worst first. This drives the district dashboard."""
        now = now or datetime.now(timezone.utc)
        with self.session() as s:
            rows = s.execute(
                select(models.Action)
                .where(models.Action.status == "open")
                .order_by(models.Action.due_at)
            ).scalars().all()
            return [r for r in rows if _as_utc(r.due_at) < now]

    def tier_distribution(self) -> Dict[str, int]:
        """Current caseload by final tier, counting each interaction once at
        its most recent snapshot."""
        with self.session() as s:
            latest = (
                select(models.SVISnapshot.interaction_id,
                       func.max(models.SVISnapshot.id).label("max_id"))
                .group_by(models.SVISnapshot.interaction_id).subquery()
            )
            rows = s.execute(
                select(models.SVISnapshot.final_tier, func.count())
                .join(latest, models.SVISnapshot.id == latest.c.max_id)
                .group_by(models.SVISnapshot.final_tier)
            ).all()
            return {tier: count for tier, count in rows}

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def purge_expired_audio(self, now: Optional[datetime] = None,
                            retention_days: Optional[int] = None) -> int:
        """Delete raw audio past its retention window and record the deletion.

        The purge itself is an auditable event. A retention policy nobody can
        verify was applied is not a retention policy.
        """
        now = now or datetime.now(timezone.utc)
        days = SETTINGS.audio_retention_days if retention_days is None else retention_days
        cutoff = now - timedelta(days=days)

        with self.session() as s:
            doomed = s.execute(
                select(models.AudioBlob.id, models.AudioBlob.interaction_id)
                .where(models.AudioBlob.created_at < cutoff)
            ).all()
            if not doomed:
                return 0
            ids = [row[0] for row in doomed]
            affected = sorted({row[1] for row in doomed})
            s.execute(delete(models.AudioBlob).where(models.AudioBlob.id.in_(ids)))
            s.flush()
            self._append_in(s, AuditEventType.RETENTION_PURGE, "-", "retention-job",
                            {"deleted_blobs": len(ids),
                             "interactions_affected": affected,
                             "cutoff": cutoff, "retention_days": days}, now)
            return len(ids)
