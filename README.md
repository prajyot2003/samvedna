# SAMVEDNA

**S**peech **A**nd **M**ultilingual **V**ulnerability **E**valuation for **D**istress & **N**eed **A**ssessment

An AI-assisted structured triage system for the National Helpline Against Atrocities
(NHAA, 14566) and the Integrated Portal of the Department of Social Justice &
Empowerment.

> Smart India Hackathon — Problem Statement **26093**
> Ministry of Social Justice and Empowerment (MoSJE)

---

## 1. What this system is

SAMVEDNA assesses the psychological stress, trauma, fear, anxiety and vulnerability
of victims and complainants at their **first point of contact** with the helpline
ecosystem, and converts that assessment into a **specific, statutorily-grounded
action packet** that a counsellor, district nodal officer or police officer can act on.

It produces a **Stress Vulnerability Index (SVI)** on a 0–100 scale, places every
interaction into one of four risk tiers — **Low / Moderate / High / Critical** — and
emits the referrals that tier warrants under the SC/ST (Prevention of Atrocities)
Act 1989 and its 1995 Rules.

### What this system is NOT

This is stated first, deliberately, because it governs every design decision below.

- **It does not diagnose.** It performs screening and triage. No output of this
  system is a clinical diagnosis, and no output may be recorded as one.
- **It does not decide.** Every recommendation is advisory. A human counsellor
  can override any score, and the override is what enters the record.
- **It does not replace counselling.** It is a triage and routing layer. Actual
  mental health support is handed off to Tele-MANAS (14416); legal aid to
  NALSA/DLSA; protection to the machinery of the PoA Act.
- **It never uses caste as a model feature.** Caste appears in the case record
  because the statute requires it. It is walled off from every scoring pathway.

The formal framing is **AI-assisted structured professional judgement**: the system
converts an unstructured first contact into a structured, auditable risk profile so
that a human decides faster and misses fewer critical cases.

---

## 2. The core design principle

The SVI is **not** a single model output. It is a three-channel composite in which
the machine-learned component is deliberately the *least* authoritative channel.

| Channel | Source | Role | Weight |
|---|---|---|---|
| **A — Structured context** | Objective risk factors established during the interaction (offence category, accused proximity, prior threats, social boycott, displacement, sole earner lost, minor/pregnant/disabled victim, FIR status…) | Deterministic scored checklist | 0.55 of base |
| **B — Clinical micro-screen** | Validated instruments administered conversationally: PC-PTSD-5, PHQ-2→PHQ-9, GAD-2→GAD-7, C-SSRS screener | Deterministic instrument scoring | 0.45 of base |
| **C — Passive AI signals** | Acoustic prosody (eGeMAPS), conversational dynamics, multilingual text classification of the narrative | **Modulator only** — bounded, confidence-gated | 0 to +25 points |

Three invariants are enforced in code and covered by tests:

1. **Fail-safe monotonicity.** Channel C can only ever *raise* the score. It can
   never pull an interaction below the floor established by Channels A and B.
2. **Asymmetric abstention.** When ASR confidence or audio quality is low, Channel C
   is zeroed *and* the floor is raised, routing the case to human review. Uncertainty
   escalates; it never de-escalates.
3. **Rules override models.** A separate deterministic safety layer runs *after*
   the SVI and can force `CRITICAL` regardless of any score — for C-SSRS positives
   on ideation with intent, explicit self-harm language, imminent-violence
   indicators, or sexual offences against a minor. No ML model is consulted for
   these decisions.

### Why this matters

ASR word-error-rate is systematically worse for rural, low-resource dialects —
Gondi, Santali, Bhojpuri, Chhattisgarhi. Those are disproportionately the speakers
this Act exists to protect. A naive pipeline gives the most marginalised victims the
least accurate transcripts and therefore *under-triages* them. That is algorithmic
caste bias, and it is the specific failure mode of this problem domain.

Asymmetric abstention is the mitigation. Per-language, per-dialect recall on the
Critical class is reported as a first-class metric — in the product, not only in
the paperwork.

---

## 3. Architecture

One interaction bus, four front doors, one triage brain. Every channel normalises
into the same typed event stream; the triage engine is channel-agnostic.

```
  IVRS / telephony ─┐
  Web portal (text) ─┤
  Chatbot           ─┼──▶ Consent Gate ──▶ Interaction Bus (events)
  Mobile app        ─┘                            │
                                                  ▼
              ┌───────────────┬───────────────┬───────────────┐
              │ ASR + VAD     │ Acoustic FE   │ Intake Agent  │
              │ (Bhashini /   │ (openSMILE    │ (slot &       │
              │  Whisper)     │  eGeMAPS)     │  screener DM) │
              └───────┬───────┴───────┬───────┴───────┬───────┘
                      ▼               ▼               ▼
                 Lexical FE      Channel C        Channels A + B
                 (MuRIL)         signals          facts + screeners
                      └───────────────┴───────────────┘
                                      ▼
                         ┌────────────────────────┐
                         │   SVI ENGINE           │  pure function, no I/O
                         └───────────┬────────────┘
                                     ▼
                         ┌────────────────────────┐
                         │   SAFETY RULES         │  escalate-only
                         └───────────┬────────────┘
                                     ▼
                     Action Orchestrator (entitlement map)
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
  Counsellor Console         District Dashboard          Audit Ledger
   (live, WebSocket)         (caseload, SLA, fairness)   (hash-chained)
```

### Repository layout

```
samvedna/
├── core/                      no I/O, fully unit-tested, the auditable heart
│   ├── events.py              typed event schema crossing every boundary
│   ├── svi/
│   │   ├── factors.py         Channel A risk-factor table and weights
│   │   ├── instruments.py     PHQ / GAD / PC-PTSD-5 / C-SSRS scoring
│   │   └── engine.py          compute_svi() — pure, deterministic
│   ├── rules/
│   │   ├── hard_rules.py      escalate-only safety overrides
│   │   └── lexicons/          per-language crisis lexicons (human-reviewed)
│   └── actions/
│       ├── entitlements.json  statute → action → owner → SLA
│       └── orchestrator.py
├── services/
│   ├── ingest/                channel adapters, consent gate
│   ├── asr/                   Bhashini client + local Whisper backend
│   ├── acoustic/              openSMILE eGeMAPS + VAD + quality gate
│   ├── nlp/                   fact extraction, distress classifier, PII redaction
│   ├── intake/                dialog manager, slot schedule
│   └── api/                   FastAPI app, WebSocket, REST
├── web/                       React counsellor console + district dashboard
├── evidence/                  model card, DPIA, fairness report, pilot protocol
├── data/                      corpora build scripts (no PII, no raw audio in git)
└── tests/
```

---

## 4. What is real, and what is an integration boundary

This project contains no mock data paths and no simulated scoring. Audio capture,
ASR, feature extraction, NLP, the SVI engine, the safety rules, persistence, the
audit chain and both consoles are fully implemented and run end to end.

Three interfaces cannot be connected from outside the ministry because they require
credentials and network access that are issued to government operators only:

| Interface | Status | How it is handled |
|---|---|---|
| **Tele-MANAS (14416) warm transfer** | Requires NHM operator credentials | Real client implementing the documented SIP REFER / REST handoff contract, pointed at a local reference endpoint that implements the same interface |
| **NHAA 14566 telephony trunk** | Requires a licensed SIP trunk | Asterisk/ARI adapter is real and works against any SIP provider; a WebRTC browser source is provided for local operation |
| **Integrated Portal case API** | Requires NIC credentials | Real HTTP client against the published case-creation schema, with a local reference server |

These are integration boundaries, not stubs: the request payloads, retry semantics
and error handling are production code. Swapping the base URL and credentials is
the entire remaining work. Each is documented in `evidence/INTEGRATION.md`.

---

## 5. Privacy, consent and ethics

Handling data about victims of caste atrocities is the most sensitive category of
personal data recognised under the **Digital Personal Data Protection Act, 2023**.
The following are architectural, not optional.

- **Spoken informed consent before any analysis.** Three separate scopes —
  `analysis`, `retention`, `referral` — captured in the caller's own language and
  recorded as a signed consent artefact. Declining `analysis` puts the interaction
  in passive mode: full human handling, no scoring, no penalty to the complainant.
- **PII redaction before persistence.** Names, villages, FIR numbers and phone
  numbers are stripped from transcripts before they reach storage or any model.
- **Purpose limitation and retention.** Raw audio is purged on a fixed schedule;
  only derived features and the SVI are retained. The purge itself is written to
  the audit ledger.
- **Tamper-evident audit ledger.** Every SVI snapshot, every override, every
  action, every consent decision and every deletion is hash-chained. The chain is
  independently verifiable via `GET /audit/verify`.
- **Human override is always available** and always recorded with a reason.
- **No third-party egress.** The system is designed to run entirely within NIC /
  MeghRaj or on-premise infrastructure. Language services use Bhashini (MeitY);
  models run locally. No caller data leaves government infrastructure.

Governing artefacts live in `evidence/`: model card, DPIA, fairness report, clinical
basis, and the shadow-mode pilot protocol.

---

## 6. Clinical basis

Channel B administers items adapted from instruments validated for brief and
telephonic screening. They are used as **screeners**, never as diagnostic tests.

| Instrument | Domain | Items used |
|---|---|---|
| **PC-PTSD-5** | Post-traumatic stress | 5 |
| **PHQ-2 → PHQ-9** | Depression (escalating) | 2, then 9 |
| **GAD-2 → GAD-7** | Anxiety (escalating) | 2, then 7 |
| **C-SSRS screener** | Suicidal ideation and behaviour | 6 |

The C-SSRS screener is administered **unconditionally** in every interaction. It is
never gated on a model's judgement that the caller appears well.

Full citations, adaptation notes, translation provenance and clinical review
sign-off are recorded in `evidence/CLINICAL_BASIS.md`.

---

## 7. Statutory grounding

Recommendations map to entitlements, not to generic advice. The action table in
`core/actions/entitlements.json` encodes:

- **Section 15A**, SC/ST (PoA) Act — victim and witness rights: protection, legal
  aid, travel and maintenance allowance, right to be heard.
- **Rule 12 and the compensation schedule**, SC/ST (PoA) Rules 1995 — relief
  entitlement by offence category, payable in tranches at FIR, chargesheet and
  conviction.
- **Rule 7** — investigation by an officer not below the rank of DySP; the 60-day
  chargesheet timeline.
- **Section 4** — escalation where a public servant wilfully neglects duty,
  including refusal to register an FIR.
- **Special and Exclusive Special Courts**; District-level Vigilance and
  Monitoring Committee.

---

## 8. Running the system

### Requirements

- Python 3.10+
- Node 18+
- Optional: PostgreSQL 14+ and Redis 7+ for production configuration

Development runs on SQLite and an in-process event bus with no external services,
so the full pipeline works on a laptop with nothing installed. Production
configuration swaps both via environment variables — the application code is
identical.

### Setup

```bash
git clone <repo> && cd samvedna
make setup          # venv, python deps, node deps, model downloads
cp .env.example .env
make dev            # API on :8000, web console on :5173
```

### Verifying the install

```bash
make test           # unit + integration suite
make verify-audit   # walks and verifies the hash chain
make fairness       # regenerates evidence/FAIRNESS.md from the eval set
```

---

## 9. Build order

The system is built core-outward: the auditable, dependency-free heart first, then
the machinery that feeds it. Each phase leaves the system in a working state.

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Decisions, schema, scaffold | `core/events.py` frozen and reviewed |
| 1 | SVI engine + instruments | `pytest tests/test_svi.py` green, invariants covered |
| 2 | Safety rules + lexicons | C-SSRS positive forces CRITICAL with model bypassed |
| 3 | Action orchestrator | Tier + facts resolve to owner, SLA, statutory basis |
| 4 | Persistence + audit chain | `make verify-audit` returns green |
| 5 | ASR + acoustic pipeline | Live microphone produces transcript and prosody |
| 6 | NLP: extraction, classifier, redaction | Narrative populates Channel A facts |
| 7 | Intake agent | Unscripted interaction reaches a defensible tier |
| 8 | Counsellor console | Live SVI, contribution panel, override flow |
| 9 | District dashboard | Caseload, SLA clocks, per-language fairness |
| 10 | Evidence pack | Model card, DPIA, fairness, pilot protocol complete |

All ten phases are complete. 266 automated tests pass; two skip pending
downloaded ASR weights (see §4).

## 9a. What a reviewer should read, in order

| Document | What it answers |
|---|---|
| `evidence/MODEL_CARD.md` | What the system does, how the score is made, what it cannot do, what is unmeasured |
| `evidence/DPIA.md` | Lawful basis, consent, minimisation, and the one declared residual gap in redaction |
| `evidence/CLINICAL_BASIS.md` | Which instruments, why, and what has *not* been validated |
| `evidence/FAIRNESS.md` | Generated from the database. Currently: no data, no claim |
| `evidence/PILOT_PROTOCOL.md` | How the system earns the right to make an accuracy claim, and the rules that halt it |
| `evidence/INTEGRATION.md` | The three government interfaces, what is built and what remains |
| `DECISIONS.md` | Fifty frozen decisions with the reasoning, including the ones found by failing tests |

`make evidence` regenerates the fairness report and prints the readiness verdict.

---

## 10. Licence and use

Built for the Ministry of Social Justice and Empowerment under Smart India
Hackathon PS 26093.

This system is intended for operation by trained helpline counsellors and
authorised officers. It must not be deployed in an autonomous configuration, and
must not be represented to any complainant as a clinical or diagnostic service.
