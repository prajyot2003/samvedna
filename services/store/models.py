"""
Persistence schema.

Two properties drive the shape of these tables:

  * `svi_snapshot` is append-only and timestamped. The SVI evolves during an
    interaction as facts are established and screeners administered; we keep
    every step, not just the final value. A counsellor reviewing a case later
    needs to see how the assessment moved and what moved it.

  * `audit` is the ledger from `core.audit`, with a UNIQUE constraint on `seq`.
    That constraint is the concurrency control for the hash chain: two writers
    racing to append will both compute the same next sequence number, and
    exactly one insert survives. The loser retries against the new head rather
    than silently forking the chain.

Raw audio is not stored in the primary tables. `audio_blob` holds it separately
so retention purges can drop it without touching the case record, and every
purge writes its own ledger entry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        LargeBinary, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Interaction(Base):
    __tablename__ = "interaction"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="open")
    # Set when the caller declines analysis consent: full human handling, no
    # scoring, and the console shows why.
    passive_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    district: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    consents: Mapped[list["Consent"]] = relationship(back_populates="interaction")
    snapshots: Mapped[list["SVISnapshot"]] = relationship(back_populates="interaction")
    actions: Mapped[list["Action"]] = relationship(back_populates="interaction")


class Consent(Base):
    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(8))
    script_version: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(24))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interaction: Mapped[Interaction] = relationship(back_populates="consents")


class Utterance(Base):
    __tablename__ = "utterance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    speaker: Mapped[str] = mapped_column(String(16))
    # Redacted before it reaches this column. PII redaction is upstream, in the
    # NLP layer; nothing unredacted is ever written here.
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8))
    asr_confidence: Mapped[float] = mapped_column(Float)
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AcousticWindow(Base):
    __tablename__ = "acoustic_window"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    t_start: Mapped[float] = mapped_column(Float)
    t_end: Mapped[float] = mapped_column(Float)
    features: Mapped[Dict[str, Any]] = mapped_column(JSON)
    snr_db: Mapped[float] = mapped_column(Float)
    speech_ratio: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AudioBlob(Base):
    """Raw audio, isolated so retention purges never touch the case record."""
    __tablename__ = "audio_blob"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    sample_rate: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)


class Fact(Base):
    __tablename__ = "fact"
    __table_args__ = (UniqueConstraint("interaction_id", "key", name="uq_fact_per_interaction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    asked: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreenerResponse(Base):
    __tablename__ = "screener_response"
    __table_args__ = (UniqueConstraint("interaction_id", "instrument", "item",
                                       name="uq_screener_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    instrument: Mapped[str] = mapped_column(String(16))
    item: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SVISnapshot(Base):
    """Append-only. One row per recomputation, so the console can show the
    score moving and a reviewer can see what moved it."""
    __tablename__ = "svi_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(16))
    final_tier: Mapped[str] = mapped_column(String(16))
    channel_a: Mapped[float] = mapped_column(Float)
    channel_b: Mapped[float] = mapped_column(Float)
    channel_c_delta: Mapped[float] = mapped_column(Float)
    base: Mapped[float] = mapped_column(Float)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    rules_triggered: Mapped[Dict[str, Any]] = mapped_column(JSON, default=list)
    model_bypassed: Mapped[bool] = mapped_column(Boolean, default=False)
    contributions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, index=True)

    interaction: Mapped[Interaction] = relationship(back_populates="snapshots")


class Override(Base):
    __tablename__ = "override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("svi_snapshot.id"))
    from_tier: Mapped[str] = mapped_column(String(16))
    to_tier: Mapped[str] = mapped_column(String(16))
    counsellor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Action(Base):
    __tablename__ = "action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(ForeignKey("interaction.id"), index=True)
    action_id: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(24))
    owner: Mapped[str] = mapped_column(String(32), index=True)
    statutory_basis: Mapped[str] = mapped_column(Text, default="")
    triggered_by: Mapped[Dict[str, Any]] = mapped_column(JSON, default=list)
    sla_minutes: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interaction: Mapped[Interaction] = relationship(back_populates="actions")


class AuditEntry(Base):
    """The hash chain. UNIQUE on seq is the concurrency control — see the
    module docstring and `Repository.append_audit`."""
    __tablename__ = "audit"
    __table_args__ = (
        UniqueConstraint("seq", name="uq_audit_seq"),
        Index("ix_audit_interaction", "interaction_id"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    event_type: Mapped[str] = mapped_column(String(32))
    interaction_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), index=True)
