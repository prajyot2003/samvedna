"""
The triage pipeline — every module built so far, wired into one path.

This is where an utterance becomes a score becomes an action packet. It is
deliberately thin: all the judgement lives in `core`, which is pure and tested
in isolation, and this file's job is to call things in the right order and make
sure nothing is skipped.

THE ORDER MATTERS, and each step is here for a reason that is not sequencing:

  consent  -> nothing is analysed before the analysis scope is granted, and a
              declined interaction never reaches a scoring call at all
  redact   -> PII is removed before text touches storage or any model
  lexicon  -> crisis language is detected before anything else looks at content
  extract  -> candidate Channel A facts, all unconfirmed
  agent    -> folds them into state; decides the next question
  score    -> compute_svi over confirmed + provisional facts and screeners
  rules    -> escalate-only safety layer, after the score, never inside it
  actions  -> statutory packet resolved from the final tier
  persist  -> snapshot and ledger entries in one transaction
  publish  -> the console sees it

RECOMPUTATION IS CHEAP AND CONSTANT. The SVI is recomputed after every piece of
new information rather than once at the end, because the score moving during an
interaction is information a counsellor needs — a caller who becomes more
distressed as they describe what happened looks different from one who was
distressed from the first word, and only a trajectory shows that.

ACTIONS ARE RAISED ON TIER CHANGE, NOT ON EVERY RECOMPUTE. Otherwise a
twenty-minute call generates the same DySP intimation forty times.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.actions.orchestrator import ResolvedAction, resolve_actions
from core.events import (Channel, ConsentDecision, ConsentScope, Confidence, Instrument,
                         Language, Tier, tier_rank)
from core.rules.hard_rules import RuleOutcome, TriageState, apply_hard_rules
from core.svi.engine import SVIResult, compute_svi
from services.audio import quality
from services.audio.prosody import ProsodyExtractor
from services.audio.vad import ConversationalFeatures, conversational_features, detect_speech
from services.intake.agent import ActionKind, AgentAction, IntakeAgent, IntakeState
from services.intake.schedule import CONSENT_SCRIPT_VERSION
from services.nlp import distress, redaction
from services.nlp.facts import extract
from services.nlp.lexicon import analyse
from services.store.repo import Repository

log = logging.getLogger(__name__)


class ConsentRequired(RuntimeError):
    """Audio reached the pipeline before the analysis scope was granted.

    Raised rather than silently ignored: a caller who has not consented and a
    caller whose recording was empty must not produce the same result, or the
    console cannot tell a consent failure from a microphone failure.
    """


@dataclass
class Session:
    """Live state for one interaction. Everything the console renders comes
    from here or from the repository."""
    interaction_id: str
    channel: Channel
    language: Language
    district: Optional[str] = None
    state: IntakeState = field(default_factory=IntakeState)

    transcript: List[Dict[str, Any]] = field(default_factory=list)
    lexicon_categories: set = field(default_factory=set)
    timing: Optional[ConversationalFeatures] = None
    prosody: Dict[str, float] = field(default_factory=dict)
    signal_confidence: Confidence = Confidence.OK
    quality_reasons: tuple = ()

    last_result: Optional[SVIResult] = None
    last_outcome: Optional[RuleOutcome] = None
    last_tier: Optional[Tier] = None
    actions: List[ResolvedAction] = field(default_factory=list)
    closed: bool = False

    def public_state(self, agent: IntakeAgent) -> Dict[str, Any]:
        """The payload the counsellor console renders."""
        next_action = None if self.closed else agent.next_action(self.state)
        return {
            "interaction_id": self.interaction_id,
            "channel": self.channel.value,
            "language": self.language.value,
            "district": self.district,
            "passive_mode": not self.state.analysis_granted(),
            "transcript": self.transcript,
            "svi": _svi_payload(self.last_result, self.last_outcome),
            "coverage": agent.coverage_report(self.state),
            "signal": {"confidence": self.signal_confidence.value,
                       "reasons": list(self.quality_reasons)},
            "actions": [_action_payload(a) for a in self.actions],
            "next_action": _agent_action_payload(next_action),
            "closed": self.closed,
        }


def _svi_payload(result: Optional[SVIResult],
                 outcome: Optional[RuleOutcome]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "score": result.score,
        "computed_tier": result.tier.value,
        "tier": outcome.tier.value if outcome else result.tier.value,
        "channel_a": result.channel_a,
        "channel_b": result.channel_b,
        "channel_c_delta": result.channel_c_delta,
        "base": result.base,
        "abstained": result.abstained,
        "abstention_reasons": list(result.abstention_reasons),
        "coarse_domains": list(result.coarse_domains),
        "contributions": dict(result.explain(8)),
        "rules_triggered": list(outcome.triggered) if outcome else [],
        "rule_bases": list(outcome.bases) if outcome else [],
        "model_bypassed": bool(outcome and outcome.model_bypassed),
    }


def _action_payload(action: ResolvedAction) -> Dict[str, Any]:
    return {
        "action_id": action.action_id, "label": action.label, "type": action.type,
        "owner": action.owner, "owner_label": action.owner_label,
        "statutory_basis": action.basis, "sla_minutes": action.sla_minutes,
        "due_at": action.due_at.isoformat() if action.due_at else None,
        "immediate": action.immediate, "triggered_by": list(action.triggered_by),
    }


def _agent_action_payload(action: Optional[AgentAction]) -> Optional[Dict[str, Any]]:
    if action is None:
        return None
    return {
        "kind": action.kind.value, "prompt": action.prompt,
        "language": action.language.value, "slot_key": action.slot_key,
        "instrument": action.instrument.value if action.instrument else None,
        "item_index": action.item_index, "scale": action.scale,
        "scope": action.scope.value if action.scope else None,
        "rationale": action.rationale,
    }


class TriagePipeline:
    def __init__(self, repo: Repository, bus, asr_router=None,
                 prosody_extractor: Optional[ProsodyExtractor] = None):
        self.repo = repo
        self.bus = bus
        self.agent = IntakeAgent()
        self.asr = asr_router
        self._prosody = prosody_extractor
        self.sessions: Dict[str, Session] = {}

    # ------------------------------------------------------------------

    async def start(self, channel: Channel, language: Language,
                    district: Optional[str] = None,
                    interaction_id: Optional[str] = None) -> Session:
        interaction_id = interaction_id or f"NHAA-{uuid.uuid4().hex[:12]}"
        self.repo.start_interaction(interaction_id, channel.value, language.value,
                                    district)
        session = Session(interaction_id=interaction_id, channel=channel,
                          language=language, district=district,
                          state=IntakeState(language=language))
        self.sessions[interaction_id] = session
        await self._publish(session, "interaction_started",
                            {"channel": channel.value, "language": language.value})
        return session

    def get(self, interaction_id: str) -> Session:
        session = self.sessions.get(interaction_id)
        if session is None:
            raise KeyError(interaction_id)
        return session

    # ------------------------------------------------------------------

    async def record_consent(self, session: Session, scope: ConsentScope,
                             decision: ConsentDecision, method: str = "spoken") -> None:
        self.repo.record_consent(session.interaction_id, scope.value, decision.value,
                                 session.language.value, CONSENT_SCRIPT_VERSION, method)
        self.agent.record_consent(session.state, scope, decision)
        await self._publish(session, "consent_recorded",
                            {"scope": scope.value, "decision": decision.value})

        if scope is ConsentScope.ANALYSIS and decision is not ConsentDecision.GRANTED:
            await self._publish(session, "passive_mode", {
                "reason": "caller declined analysis",
                "note": "no scoring; full human handling; complaint recorded normally"})

    async def ingest_text(self, session: Session, text: str,
                          speaker: str = "caller") -> Dict[str, Any]:
        """Text from any channel: portal, chatbot, or an ASR transcript."""
        redacted = redaction.redact(text)
        entry = {"speaker": speaker, "text": redacted.text,
                 "redactions": redacted.counts_by_label(),
                 "at": datetime.now(timezone.utc).isoformat()}
        session.transcript.append(entry)
        await self._publish(session, "utterance", entry)

        if not session.state.analysis_granted():
            # Rule 1. A declined interaction is never scored, not even
            # incidentally, so we stop before the lexicon runs.
            return entry

        lexicon = analyse(redacted.text, session.language)
        session.lexicon_categories |= set(lexicon.categories)
        self.agent.ingest_narrative(session.state,
                                    extract(redacted.text, session.language), lexicon)

        if lexicon.hits:
            await self._publish(session, "lexicon_hit", lexicon.summary())

        await self.recompute(session)
        return entry

    async def _analyse_audio(self, session: Session, audio: np.ndarray,
                             sample_rate: int) -> str:
        """Everything acoustic, and nothing conversational.

        Runs voice activity detection, recognition, the signal quality gate and
        prosody, updating the session's acoustic state. Returns the recognised
        text and does NOT append it to the transcript — the caller decides
        whether the words become part of the record.

        Split out of `ingest_audio` so that dictation can run the identical
        analysis. A dictation path that skipped the quality gate would report a
        confident transcript from audio the gate would have rejected, and the
        abstention logic depends on that verdict being set from the same audio
        the words came from.

        CONSENT IS CHECKED HERE, not in `ingest_text` downstream. A voice is
        personal data before it is words: running the quality gate and eGeMAPS
        over a caller's audio is processing under the DPDP Act whether or not a
        recogniser ever turns it into text. Checking further down the path —
        which is what this file did until a dictation test caught it — meant
        every acoustic feature was extracted from a caller who had not agreed
        to be assessed, and only the transcript was withheld.
        """
        if not session.state.analysis_granted():
            raise ConsentRequired(
                "analysis consent has not been granted for this interaction")

        vad = detect_speech(audio, sample_rate)
        session.timing = conversational_features(vad)

        transcript_text, confidences, durations = "", [], []
        if self.asr is not None:
            try:
                routed = self.asr.transcribe(audio, sample_rate, session.language)
                transcript_text = routed.transcript.text
                confidences = routed.transcript.confidences
                durations = routed.transcript.durations
                if routed.language_substituted:
                    await self._publish(session, "asr_provenance",
                                        {"note": routed.provenance_note})
            except Exception as exc:                          # noqa: BLE001
                log.warning("ASR failed for %s: %s", session.interaction_id, exc)
                await self._publish(session, "asr_unavailable", {"detail": str(exc)})

        report = quality.assess(audio, sample_rate, asr_confidences=confidences,
                                asr_durations=durations, vad=vad)
        session.signal_confidence = report.confidence
        session.quality_reasons = report.reasons
        await self._publish(session, "signal_quality",
                            {**report.as_dict(), "explanation": report.explain()})

        if self._prosody is not None and report.speech_seconds > 0:
            try:
                windows = self._prosody.extract_windows(audio, sample_rate)
                if windows:
                    session.prosody = windows[-1].features
            except Exception as exc:                          # noqa: BLE001
                log.warning("prosody extraction failed: %s", exc)

        return transcript_text

    async def dictate(self, session: Session, audio: np.ndarray,
                      sample_rate: int) -> Dict[str, Any]:
        """Speech to text for a counsellor to read, correct and then submit.

        The recording is analysed exactly as ingested audio is — the quality
        gate and eGeMAPS prosody run, so Channel C and the abstention path get
        the signal they would otherwise never see from a typed console. What
        does not happen is the transcript entering the record: recognition
        error rates on Bhojpuri are the worst in the system, and an unreviewed
        ASR string becoming a permanent part of a victim's case file is exactly
        the failure the read-back rule exists to prevent.

        So the words come back as a proposal. Nothing is scored from them until
        the counsellor submits them through the ordinary text path.
        """
        text = await self._analyse_audio(session, audio, sample_rate)
        await self.recompute(session)
        return {
            "text": text,
            "recognised": bool(text),
            "asr_configured": self.asr is not None,
            "signal_confidence": session.signal_confidence,
            "quality_reasons": list(session.quality_reasons),
        }

    async def ingest_audio(self, session: Session, audio: np.ndarray,
                           sample_rate: int) -> Dict[str, Any]:
        """Audio from IVRS or a browser capture.

        The quality gate runs before recognition, because its verdict governs
        whether the model channel may contribute at all — and it must be
        recorded even when recognition itself fails.
        """
        transcript_text = await self._analyse_audio(session, audio, sample_rate)

        if transcript_text:
            return await self.ingest_text(session, transcript_text)

        await self.recompute(session)
        return {"speaker": "caller", "text": "", "redactions": {}}

    async def answer_slot(self, session: Session, key: str, present: bool) -> None:
        self.agent.record_slot(session.state, key, present)
        await self._publish(session, "slot_answered", {"key": key, "present": present})
        await self.recompute(session)

    async def answer_screener(self, session: Session, instrument: Instrument,
                              item_index: int, value: int) -> None:
        self.agent.record_screener(session.state, instrument, item_index, value)
        await self._publish(session, "screener_answered",
                            {"instrument": instrument.value, "item": item_index,
                             "value": value})
        await self.recompute(session)

    # ------------------------------------------------------------------

    async def recompute(self, session: Session) -> Optional[SVIResult]:
        if not session.state.analysis_granted():
            return None

        facts = session.state.to_context_facts()
        screeners = session.state.to_screeners()

        lexicon_analysis = None
        if session.lexicon_categories:
            from services.nlp.lexicon import LexiconAnalysis, load_lexicon
            lexicon_analysis = _synthetic_analysis(session)

        assessment = distress.assess(lexicon=lexicon_analysis, timing=session.timing,
                                     prosody=session.prosody or None)
        signals = assessment.to_signals(session.signal_confidence)

        result = compute_svi(facts, screeners, signals)
        outcome = apply_hard_rules(
            result.tier,
            TriageState(facts, screeners, set(session.lexicon_categories)))

        session.last_result, session.last_outcome = result, outcome
        self.repo.save_snapshot(session.interaction_id, result, outcome)

        await self._publish(session, "svi_computed", {
            **_svi_payload(result, outcome),
            "distress": assessment.as_dict(),
            "distress_explanation": assessment.explain(),
        })

        if session.last_tier is None or outcome.tier is not session.last_tier:
            await self._raise_actions(session, outcome)
        session.last_tier = outcome.tier

        return result

    async def _raise_actions(self, session: Session, outcome: RuleOutcome) -> None:
        """Raised on tier change only. A twenty-minute call must not generate
        the same DySP intimation forty times."""
        now = datetime.now(timezone.utc)
        existing = {a.action_id for a in session.actions}
        resolved = resolve_actions(outcome.tier,
                                   session.state.to_context_facts().present,
                                   session.state.offence_category,
                                   outcome.triggered, now=now)
        fresh = [a for a in resolved if a.action_id not in existing]
        if not fresh:
            return

        self.repo.raise_actions(session.interaction_id, fresh)
        session.actions.extend(fresh)
        await self._publish(session, "actions_raised", {
            "tier": outcome.tier.value,
            "actions": [_action_payload(a) for a in fresh]})

    async def override_tier(self, session: Session, to_tier: Tier,
                            counsellor_id: str, reason: str) -> None:
        """A human overruling the system. Always available, always recorded,
        always with a reason — the repository refuses one without."""
        snapshot = self.repo.latest_snapshot(session.interaction_id)
        if snapshot is None:
            raise ValueError("nothing has been assessed yet for this interaction")
        from_tier = session.last_tier or Tier.LOW
        self.repo.record_override(session.interaction_id, snapshot.id,
                                  from_tier.value, to_tier.value, counsellor_id, reason)
        session.last_tier = to_tier
        await self._publish(session, "tier_overridden", {
            "from_tier": from_tier.value, "to_tier": to_tier.value,
            "counsellor_id": counsellor_id, "reason": reason})
        await self._raise_actions(session, RuleOutcome(to_tier, (), (), False))

    async def close(self, session: Session) -> None:
        session.closed = True
        await self._publish(session, "interaction_closed", {})

    # ------------------------------------------------------------------

    async def _publish(self, session: Session, event_type: str,
                       payload: Dict[str, Any]) -> None:
        await self.bus.publish(session.interaction_id, {
            "type": event_type,
            "interaction_id": session.interaction_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        })


def _synthetic_analysis(session: Session):
    """Rebuild a LexiconAnalysis from the categories seen so far in the call.

    Distress is assessed over the whole interaction, not just the most recent
    utterance: a caller who said something alarming three minutes ago is still
    the person on the line.
    """
    from services.nlp.lexicon import LexiconAnalysis, LexiconHit, load_lexicon
    lexicon = load_lexicon(session.language)
    hits = []
    for category in session.lexicon_categories:
        spec = lexicon.categories.get(category, {})
        hits.append(LexiconHit(category=category,
                               severity=str(spec.get("severity", "moderate")),
                               term="", position=0))
    return LexiconAnalysis(hits=tuple(hits), language=session.language,
                           lexicon_reviewed=lexicon.reviewed)
