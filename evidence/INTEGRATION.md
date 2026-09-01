# Integration boundaries

Three interfaces cannot be connected from outside the ministry because they
require credentials and network access issued only to government operators.

This document exists so that the boundary is a **known scope line** rather than
a discovered surprise. For each one: what is built, what is not, and exactly
what remains.

---

## 1. Tele-MANAS (14416) warm transfer

**Why it matters.** The Critical tier's first action is a live warm transfer to
a crisis counsellor — not a callback. The caller is not released from the line
until a counsellor accepts.

**Built:** the action is raised with a zero-minute SLA, appears immediately on
the console, is written to the ledger, and the intake agent repeats the handover
action until `record_crisis_handover` is called. The repeat behaviour is tested.

**Not built:** the telephony leg itself. A live transfer is a SIP REFER (or the
equivalent operator API call) against Tele-MANAS infrastructure under an
NHM-issued operator identity.

**Remaining work:** implement `TeleManasClient.transfer(interaction_id, context)`
against the operator API; call it from `TriagePipeline._raise_actions` when the
`telemanas_warm_transfer` action is raised; call `record_crisis_handover` on
acceptance, not on dial. Estimated: days, not weeks, once credentials exist.

---

## 2. NHAA 14566 telephony trunk

**Why it matters.** It is how calls arrive.

**Built:** the entire audio path. `services/audio/` handles 8 kHz narrowband
G.711 audio as its primary case, and the pipeline accepts audio from any source
through `POST /interactions/{id}/audio`. Browser capture works today.

**Not built:** the SIP trunk registration. It needs a licensed trunk and a
carrier arrangement.

**Remaining work:** an Asterisk ARI or FreeSWITCH adapter that pushes call audio
into `TriagePipeline.ingest_audio` and exposes the call leg for transfer. The
adapter is thin because the pipeline is already channel-agnostic — that is what
the single event bus buys.

---

## 3. Integrated Portal case API

**Why it matters.** The action packet is only useful if it reaches the case file
that district officers actually work from.

**Built:** the action packet with owner, SLA deadline and statutory basis per
action, persisted and auditable.

**Not built:** the write to the Integrated Portal, which needs NIC credentials
and the current case-creation schema.

**Remaining work:** an HTTP client against the published schema, invoked after
`raise_actions`, with idempotency on `(interaction_id, action_id)` so a retry
cannot duplicate a DySP intimation.

---

## 4. Bhashini ASR — partially closed

Unlike the three above, this one has a **real client written against the real
ULCA contract**: two-step pipeline resolution, correct request construction,
bounded retries, 4xx surfaced rather than retried, response parsing.

It is exercised over real HTTP against `services/asr/reference_server.py`, which
serves the same contract locally. Nothing is stubbed on the client side — the
tests inspect what actually went over the socket.

**Remaining work:** a ULCA user ID, API key and pipeline ID; then confirm the
response field names against current documentation. This client is written to a
published contract, and a contract read from documentation is not the same as
one exercised against a live endpoint. Saying otherwise would be the kind of
claim this project refuses to make.

Two environment variables (`SAMVEDNA_BHASHINI_USER_ID`,
`SAMVEDNA_BHASHINI_API_KEY`) plus a pipeline ID switch it on.

---

## 5. Authentication

Deliberately not invented. The service requires an operator identity on every
operational endpoint and expects the ministry's existing gateway to set it after
authenticating. `require_operator` in `services/api/app.py` is the single
integration point.

A hand-rolled auth scheme in a hackathon repository would be worse than none,
because it would look like the problem was solved.

---

## 6. What this means for a reviewer

Everything below these lines is real and runs: audio processing, recognition
routing, redaction, crisis detection, extraction, the interview, scoring, the
safety layer, the entitlement mapping, persistence, the audit chain, both
consoles.

Nothing in this repository simulates a government system and presents the
simulation as a connection. Where a connection cannot be made, the client is
written against the real contract and the gap is named here.
