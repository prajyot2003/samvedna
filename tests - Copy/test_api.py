"""
API and pipeline tests.

Run against the real application with a real database and a real event bus.
The whole point of this layer is that the modules below it actually connect, so
substituting any of them would test nothing.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from core.events import Instrument, Language, Tier
from services.api.app import create_app
from services.bus import InProcessBus
from services.store.repo import Repository

OPERATOR = {"X-Operator-Id": "counsellor-17"}


@pytest.fixture()
def client(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path/'api.db'}")
    bus = InProcessBus()
    app = create_app(repo=repo, bus=bus)
    with TestClient(app) as c:
        c.repo, c.bus = repo, bus
        yield c


def start(client, language="hi", channel="ivrs"):
    response = client.post("/interactions", headers=OPERATOR,
                           json={"channel": channel, "language": language,
                                 "district": "Gaya"})
    assert response.status_code == 201
    return response.json()["interaction_id"]


def consent(client, iid, decision="granted", scope="analysis"):
    return client.post(f"/interactions/{iid}/consent", headers=OPERATOR,
                       json={"scope": scope, "decision": decision,
                             "method": "spoken"})


# ------------------------------------------------- meta

def test_health_reports_readiness_honestly(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["production_ready"] is False
    assert body["blockers"]


def test_readiness_names_every_blocker_and_lexicon(client):
    body = client.get("/readiness").json()
    assert body["production_ready"] is False
    assert set(body["lexicons"]) == {l.value for l in Language}
    for entry in body["lexicons"].values():
        assert entry["reviewed"] is False
        assert entry["warning"]


# ------------------------------------------------- access

def test_every_operational_endpoint_requires_an_operator_identity(client):
    iid = start(client)
    unauthenticated = [
        ("post", "/interactions", {"json": {}}),
        ("get", f"/interactions/{iid}", {}),
        ("post", f"/interactions/{iid}/utterance", {"json": {"text": "x"}}),
        ("post", f"/interactions/{iid}/override", {"json": {}}),
        ("get", "/dashboard/summary", {}),
        ("get", "/audit/verify", {}),
    ]
    for method, path, kwargs in unauthenticated:
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


def test_unknown_interaction_is_a_404(client):
    assert client.get("/interactions/nope", headers=OPERATOR).status_code == 404


# ------------------------------------------------- consent

def test_nothing_is_scored_before_consent_is_granted(client):
    iid = start(client)
    body = client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                       json={"text": "मुझे धमकी दी जा रही है"}).json()
    assert body["svi"] is None
    assert body["next_action"]["kind"] == "ask_consent"


def test_declining_analysis_leaves_the_interaction_unscored(client):
    """Passive mode end to end: the transcript is kept, nothing is scored, and
    the console is told why."""
    iid = start(client)
    consent(client, iid, decision="declined")
    body = client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                       json={"text": "गाँव वालों ने बहिष्कार कर दिया"}).json()

    assert body["passive_mode"] is True
    assert body["svi"] is None
    assert body["transcript"]
    assert body["next_action"]["kind"] == "acknowledge"


def test_a_declined_interaction_writes_no_snapshot(client):
    iid = start(client)
    consent(client, iid, decision="declined")
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "मैं मर जाऊँगा"})
    assert client.repo.latest_snapshot(iid) is None


# ------------------------------------------------- the pipeline

def test_a_narrative_produces_a_score_actions_and_a_redacted_transcript(client):
    iid = start(client)
    consent(client, iid)
    body = client.post(f"/interactions/{iid}/utterance", headers=OPERATOR, json={
        "text": "मेरे पति की हत्या कर दी, आरोपी अभी भी गाँव में ही है, "
                "मेरा नंबर 9876543210 है"}).json()

    assert "[PHONE]" in body["transcript"][0]["text"]
    assert not any(ch.isdigit() for ch in body["transcript"][0]["text"])
    assert body["svi"]["score"] > 0
    assert body["actions"]
    assert all(a["owner_label"] for a in body["actions"])


def test_crisis_language_forces_critical_through_the_api(client):
    """The demo moment, over HTTP."""
    iid = start(client)
    consent(client, iid)
    body = client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                       json={"text": "अब और नहीं सह सकता, मैं मर जाऊँगा"}).json()

    assert body["svi"]["tier"] == Tier.CRITICAL.value
    assert body["svi"]["model_bypassed"] is True
    assert "explicit_self_harm_language" in body["svi"]["rules_triggered"]
    assert any(a["action_id"] == "telemanas_warm_transfer" and a["immediate"]
               for a in body["actions"])
    assert body["next_action"]["instrument"] == Instrument.CSSRS.value


def test_bhojpuri_runs_the_same_path(client):
    iid = start(client, language="bho")
    consent(client, iid)
    body = client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                       json={"text": "केहू बात ना करे, बहिष्कार बा"}).json()
    assert body["language"] == "bho"
    assert body["svi"] is not None
    assert body["next_action"]["language"] == "bho"


def test_actions_are_not_re_raised_on_every_recompute(client):
    """A twenty-minute call must not generate the same DySP intimation forty
    times."""
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "मेरे पति की हत्या कर दी"})
    first = client.get(f"/interactions/{iid}", headers=OPERATOR).json()["actions"]

    for _ in range(4):
        client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                    json={"text": "वो लोग गाँव में ही रहते हैं"})
    later = client.get(f"/interactions/{iid}", headers=OPERATOR).json()["actions"]

    ids = [a["action_id"] for a in later]
    assert len(ids) == len(set(ids)), "an action was raised twice"
    assert len(later) >= len(first)


def test_answering_slots_and_screeners_moves_coverage(client):
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "गाँव वालों ने बहिष्कार कर दिया"})

    before = client.get(f"/interactions/{iid}", headers=OPERATOR).json()
    client.post(f"/interactions/{iid}/slot", headers=OPERATOR,
                json={"key": "threat_imminent", "present": False})
    client.post(f"/interactions/{iid}/screener", headers=OPERATOR,
                json={"instrument": "pc_ptsd5", "item_index": 0, "value": 1})
    after = client.get(f"/interactions/{iid}", headers=OPERATOR).json()

    assert after["coverage"]["slots_asked"] > before["coverage"]["slots_asked"]


def test_an_unknown_slot_is_rejected(client):
    iid = start(client)
    consent(client, iid)
    response = client.post(f"/interactions/{iid}/slot", headers=OPERATOR,
                           json={"key": "not_a_real_factor", "present": True})
    assert response.status_code == 422


def test_unreadable_audio_is_a_client_error_not_a_crash(client):
    iid = start(client)
    consent(client, iid)
    response = client.post(f"/interactions/{iid}/audio", headers=OPERATOR,
                           files={"file": ("x.wav", b"not audio", "audio/wav")})
    assert response.status_code == 400


def test_real_audio_runs_the_quality_gate(client):
    import io
    import soundfile as sf

    sr = 16000
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 0.35, sr * 3).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, noise, sr, format="WAV", subtype="PCM_16")

    iid = start(client)
    consent(client, iid)
    body = client.post(f"/interactions/{iid}/audio", headers=OPERATOR,
                       files={"file": ("call.wav", buffer.getvalue(), "audio/wav")}).json()
    assert body["signal"]["confidence"] == "low"
    assert body["signal"]["reasons"]


# ------------------------------------------------- override

def test_an_override_needs_a_reason_that_says_something(client):
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "गाँव वालों ने बहिष्कार कर दिया"})
    for reason in ("", "ok", "   fine   "):
        response = client.post(f"/interactions/{iid}/override", headers=OPERATOR,
                               json={"to_tier": "MODERATE",
                                     "counsellor_id": "c-1", "reason": reason})
        assert response.status_code == 422


def test_an_override_is_recorded_and_attributed(client):
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "गाँव वालों ने बहिष्कार कर दिया"})
    response = client.post(f"/interactions/{iid}/override", headers=OPERATOR, json={
        "to_tier": "CRITICAL", "counsellor_id": "counsellor-17",
        "reason": "Caller disclosed an immediate threat after the assessment closed."})
    assert response.status_code == 200
    assert response.json()["svi"] is not None

    from core.audit import AuditEventType
    entries = [r for r in client.repo.load_chain()
               if r.event_type is AuditEventType.TIER_OVERRIDDEN]
    assert len(entries) == 1
    assert entries[0].actor == "counsellor-17"
    assert client.repo.verify().ok


def test_overriding_before_anything_is_assessed_is_a_conflict(client):
    iid = start(client)
    consent(client, iid)
    response = client.post(f"/interactions/{iid}/override", headers=OPERATOR, json={
        "to_tier": "HIGH", "counsellor_id": "c-1",
        "reason": "Escalating on the basis of the caller's demeanour."})
    assert response.status_code == 409


# ------------------------------------------------- live feed

def test_the_websocket_streams_pipeline_events(client):
    iid = start(client)
    consent(client, iid)
    with client.websocket_connect(f"/ws/interactions/{iid}") as ws:
        seen = {ws.receive_json()["type"] for _ in range(2)}
        client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                    json={"text": "मैं मर जाऊँगा"})
        for _ in range(8):
            seen.add(ws.receive_json()["type"])
            if {"utterance", "svi_computed", "actions_raised"} <= seen:
                break
    assert {"utterance", "svi_computed", "actions_raised"} <= seen


def test_a_console_connecting_late_receives_the_replay(client):
    """A counsellor opening the console mid-call sees the turns already taken
    rather than a blank screen."""
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "गाँव वालों ने बहिष्कार कर दिया"})

    with client.websocket_connect(f"/ws/interactions/{iid}") as ws:
        replayed = [ws.receive_json()["type"] for _ in range(4)]
    assert "interaction_started" in replayed
    assert "utterance" in replayed


# ------------------------------------------------- dashboard and audit

def test_dashboard_summarises_caseload_and_breaches(client):
    for text in ("मेरे पति की हत्या कर दी", "गाँव वालों ने बहिष्कार कर दिया"):
        iid = start(client)
        consent(client, iid)
        client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                    json={"text": text})

    body = client.get("/dashboard/summary", headers=OPERATOR).json()
    assert sum(body["tier_distribution"].values()) == 2
    assert body["live_interactions"] == 2
    assert isinstance(body["overdue_count"], int)


def test_audit_verification_is_exposed_and_green(client):
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "मैं मर जाऊँगा"})
    body = client.get("/audit/verify", headers=OPERATOR).json()
    assert body["ok"] is True
    assert body["records"] > 3
    assert body["failures"] == []


def test_audit_verification_reports_tampering_through_the_api(client):
    iid = start(client)
    consent(client, iid)
    client.post(f"/interactions/{iid}/utterance", headers=OPERATOR,
                json={"text": "मैं मर जाऊँगा"})

    from services.store import models
    with client.repo.session() as s:
        row = s.get(models.AuditEntry, 2)
        row.payload = {**row.payload, "decision": "declined"}

    body = client.get("/audit/verify", headers=OPERATOR).json()
    assert body["ok"] is False
    assert body["failures"]


# ------------------------------------------------- what is deliberately absent

def test_no_endpoint_serves_raw_audio_or_caller_identifiers(client):
    """An API that can serve them is an API that can leak them."""
    paths = [r.path for r in client.app.routes]
    for forbidden in ("/audio/raw", "/recording", "/caller", "/transcript/raw"):
        assert not any(forbidden in p for p in paths)


def test_a_tier_cannot_be_set_without_an_override_record(client):
    """There is no endpoint that assigns a tier directly. A tier is reached by
    assessment or by a recorded override with a stated reason."""
    paths = [r.path for r in client.app.routes]
    assert not any(p.endswith("/tier") for p in paths)
