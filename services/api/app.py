"""
The HTTP and WebSocket surface.

Thin by design. Every endpoint validates its input, calls one pipeline method,
and returns the same `public_state` payload the console renders. No triage
logic lives here — if a decision is being made in this file, it is in the wrong
file.

WHAT IS AND IS NOT EXPOSED. There is no endpoint that returns a raw transcript,
raw audio, or a caller identifier, because nothing in the console needs them
and an API that can serve them is an API that can leak them. There is no
endpoint that sets a tier directly: a tier can only be reached by assessment or
by a recorded override with a stated reason.

AUTHENTICATION is a deployment concern and is deliberately not invented here.
This runs behind the ministry's existing gateway and identity provider; a
hand-rolled auth scheme in a hackathon repository would be worse than none,
because it would look like the problem was solved. `require_operator` is the
single place an integration hooks in, and it is documented in
evidence/INTEGRATION.md.
"""

from __future__ import annotations

import asyncio
import io
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import (Depends, FastAPI, File, HTTPException, Header, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from core.events import (Channel, ConsentDecision, ConsentScope, Instrument, Language,
                         Tier)
from services.bus import InProcessBus
from services import config as _config
from services.config import SETTINGS
from services.nlp.lexicon import load_lexicon, production_ready
from services.pipeline import TriagePipeline
from services.store.repo import Repository

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------

class StartInteraction(BaseModel):
    channel: Channel = Channel.IVRS
    language: Language = Language.HINDI
    district: Optional[str] = Field(default=None, max_length=64)


class ConsentIn(BaseModel):
    scope: ConsentScope
    decision: ConsentDecision
    method: str = Field(default="spoken", max_length=24)


class UtteranceIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    speaker: str = Field(default="caller", max_length=16)


class SlotAnswer(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    present: bool


class ScreenerAnswer(BaseModel):
    instrument: Instrument
    item_index: int = Field(ge=0, le=20)
    value: int = Field(ge=0, le=4)


class OverrideIn(BaseModel):
    to_tier: Tier
    counsellor_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_say_something(cls, value: str) -> str:
        """A ten-character floor is not bureaucracy. An override reversing a
        risk assessment with 'ok' recorded against it produces a record that
        looks considered and is not."""
        if len(value.strip()) < 10:
            raise ValueError("an override reason must explain the decision")
        return value.strip()


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

def create_app(repo: Optional[Repository] = None, bus=None,
               asr_router=None, prosody_extractor=None) -> FastAPI:
    repo = repo or Repository()
    bus = bus or InProcessBus()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        repo.create_all()
        ready, blockers = production_ready()
        if not ready:
            # Logged loudly at every start. A deployment blocker that is only
            # visible in a report nobody opens is not a blocker.
            log.warning("NOT PRODUCTION READY — %d blocker(s):", len(blockers))
            for blocker in blockers:
                log.warning("  %s", blocker)
        yield

    app = FastAPI(title="SAMVEDNA", version="0.8.0", lifespan=lifespan,
                  description="AI-assisted structured triage for NHAA 14566. "
                              "Screening and decision support; not a diagnostic "
                              "service.")
    # Named origins only. `allow_credentials` stays off: the operator identity
    # arrives in a header set by the gateway, not in a cookie, so there is
    # nothing for a cross-site request to ride on.
    app.add_middleware(CORSMiddleware,
                       allow_origins=list(SETTINGS.allowed_origins),
                       allow_credentials=False,
                       allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-Operator-Id"])

    pipeline = TriagePipeline(repo, bus, asr_router=asr_router,
                              prosody_extractor=prosody_extractor)
    app.state.pipeline = pipeline
    app.state.repo = repo
    app.state.bus = bus

    def get_session(interaction_id: str):
        try:
            return pipeline.get(interaction_id)
        except KeyError:
            raise HTTPException(404, f"no live interaction {interaction_id}")

    def require_operator(x_operator_id: Optional[str] = Header(default=None)) -> str:
        """The single integration point for the ministry's identity provider.

        Deliberately not a hand-rolled auth scheme: inventing one here would
        look like the problem was solved. In deployment this runs behind the
        existing gateway, which sets the header after authenticating.

        `SAMVEDNA_ALLOWED_OPERATORS` narrows it to a fixed list. That is not a
        substitute for the gateway and is not claimed to be — it exists so a
        demonstration instance with nothing in front of it is not an open
        triage endpoint.
        """
        if not x_operator_id:
            raise HTTPException(401, "operator identity required")
        # Read through the module rather than the name bound at import, so the
        # setting survives a config reload and is actually testable. Binding it
        # at import meant the allowlist silently did nothing.
        allowed = _config.SETTINGS.allowed_operators
        if allowed and x_operator_id not in allowed:
            # Constant message either way: an attacker learning which operator
            # identities exist is a step toward impersonating one in the audit
            # ledger, where attribution is the whole point.
            raise HTTPException(401, "operator identity required")
        return x_operator_id

    # ---------------------------------------------------------------- meta

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        ready, blockers = production_ready()
        return {
            "status": "ok",
            "production_ready": ready,
            "blockers": blockers,
            "database": SETTINGS.database_url.split("://", 1)[0],
            "asr_configured": asr_router is not None,
            "prosody_configured": prosody_extractor is not None,
            "demo_banner": SETTINGS.demo_banner,
        }

    @app.get("/readiness")
    async def readiness() -> Dict[str, Any]:
        """Whether this build may take live calls, and why not.

        Exposed as an endpoint rather than buried in a document so that a
        deployment check can assert it and a reviewer can query it.
        """
        ready, blockers = production_ready()
        lexicons = {}
        for language in Language:
            lexicon = load_lexicon(language)
            lexicons[language.value] = {
                "name": lexicon.language_name, "version": lexicon.version,
                "terms": lexicon.term_count, "reviewed": lexicon.reviewed,
                "warning": lexicon.review_warning(),
            }
        return {"production_ready": ready, "blockers": blockers,
                "lexicons": lexicons}

    # -------------------------------------------------------- interactions

    @app.post("/interactions", status_code=201)
    async def start_interaction(body: StartInteraction,
                                operator: str = Depends(require_operator)):
        session = await pipeline.start(body.channel, body.language, body.district)
        return session.public_state(pipeline.agent)

    @app.get("/interactions/{interaction_id}")
    async def read_interaction(interaction_id: str,
                               operator: str = Depends(require_operator)):
        return get_session(interaction_id).public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/consent")
    async def record_consent(interaction_id: str, body: ConsentIn,
                             operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        await pipeline.record_consent(session, body.scope, body.decision, body.method)
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/utterance")
    async def add_utterance(interaction_id: str, body: UtteranceIn,
                            operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        await pipeline.ingest_text(session, body.text, body.speaker)
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/audio")
    async def add_audio(interaction_id: str, file: UploadFile = File(...),
                        operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        try:
            import soundfile as sf
            audio, sample_rate = sf.read(io.BytesIO(await file.read()), dtype="float32")
        except Exception as exc:                              # noqa: BLE001
            raise HTTPException(400, f"unreadable audio: {exc}")
        audio = np.asarray(audio)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        await pipeline.ingest_audio(session, audio, int(sample_rate))
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/slot")
    async def answer_slot(interaction_id: str, body: SlotAnswer,
                          operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        from services.intake.schedule import SLOTS_BY_KEY
        if body.key not in SLOTS_BY_KEY:
            raise HTTPException(422, f"unknown slot '{body.key}'")
        await pipeline.answer_slot(session, body.key, body.present)
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/screener")
    async def answer_screener(interaction_id: str, body: ScreenerAnswer,
                              operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        await pipeline.answer_screener(session, body.instrument, body.item_index,
                                       body.value)
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/override")
    async def override_tier(interaction_id: str, body: OverrideIn,
                            operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        try:
            await pipeline.override_tier(session, body.to_tier, body.counsellor_id,
                                         body.reason)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return session.public_state(pipeline.agent)

    @app.post("/interactions/{interaction_id}/close")
    async def close_interaction(interaction_id: str,
                                operator: str = Depends(require_operator)):
        session = get_session(interaction_id)
        await pipeline.close(session)
        return session.public_state(pipeline.agent)

    # ----------------------------------------------------------- live feed

    @app.websocket("/ws/interactions/{interaction_id}")
    async def interaction_feed(websocket: WebSocket, interaction_id: str):
        await websocket.accept()
        subscription = bus.subscribe(interaction_id)
        try:
            # Replay first: a console connecting mid-call sees the turns it
            # missed rather than starting from a blank screen.
            for event in getattr(bus, "replay", lambda _i: [])(interaction_id):
                await websocket.send_json(event)
            while True:
                event = await subscription.queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception as exc:                              # noqa: BLE001
            log.warning("websocket closed for %s: %s", interaction_id, exc)
        finally:
            bus.unsubscribe(subscription)

    # ----------------------------------------------------------- dashboard

    @app.get("/dashboard/summary")
    async def dashboard_summary(operator: str = Depends(require_operator)):
        overdue = repo.overdue_actions()
        return {
            "tier_distribution": repo.tier_distribution(),
            "live_interactions": len([s for s in pipeline.sessions.values()
                                      if not s.closed]),
            "overdue_actions": [
                {"interaction_id": a.interaction_id, "action_id": a.action_id,
                 "label": a.label, "owner": a.owner,
                 "due_at": a.due_at.isoformat(), "basis": a.statutory_basis}
                for a in overdue[:50]
            ],
            "overdue_count": len(overdue),
        }

    @app.get("/audit/verify")
    async def verify_audit(operator: str = Depends(require_operator)):
        """Re-walks the ledger and recomputes every hash. Exposed so the check
        can be run by anyone with access, not only from a shell."""
        result = repo.verify()
        return {"ok": result.ok, "records": result.length, "head": result.head,
                "summary": result.summary(),
                "failures": [dict(f) for f in result.failures]}

    return app


app = create_app()
