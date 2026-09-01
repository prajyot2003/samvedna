# SAMVEDNA

**S**peech **A**nd **M**ultilingual **V**ulnerability **E**valuation for **D**istress & **N**eed **A**ssessment

AI-assisted structured triage for the National Helpline Against Atrocities
(NHAA, 14566) and the Integrated Portal of the Department of Social Justice &
Empowerment.

> Smart India Hackathon — Problem Statement **26093**
> Ministry of Social Justice and Empowerment (MoSJE)

**Status: not cleared for live calls.** The system reports this itself at
`GET /readiness` and logs it at every start. See [Readiness](#readiness).

---

## What this is

It converts an unstructured first contact into a structured, auditable risk
profile, and maps that profile onto the entitlements the SC/ST (Prevention of
Atrocities) Act already provides.

It produces a **Stress Vulnerability Index** on 0–100, places the interaction in
one of four tiers — Low, Moderate, High, Critical — and emits an action packet
in which every recommendation names its owner, its deadline, and the statutory
provision it rests on.

## What this is not

Stated first, deliberately, because it governs every design decision below.

- **It does not diagnose.** It screens and triages. No output is a clinical
  diagnosis and none may be recorded as one.
- **It does not decide.** Every recommendation is advisory. A counsellor can
  override any tier, and the override is what enters the record.
- **It does not detect emotions.** It reports acoustic and linguistic
  *indicators* — pauses, pitch variability, distress vocabulary — and never
  asserts that a caller feels a particular way.
- **It does not use caste as a feature.** Caste is in the case record because
  the statute requires it. It is walled off from every scoring path.

The formal framing is **AI-assisted structured professional judgement**: the
system makes a human decide faster and miss fewer critical cases. It does not
decide for them.

---

## Quick start

Requires Python 3.10+ and Node 18+. Development runs on SQLite with an
in-process event bus, so the whole pipeline works on a laptop with no services
installed.

```bash
pip install -r requirements.txt
make dev                       # API on :8000, docs at /docs

cd web && npm install && npm run dev    # console on :5173
```

Useful targets:

| Command | What it does |
|---|---|
| `make test` | 266 tests |
| `make readiness` | whether this build may take live calls, and why not |
| `make verify-audit` | re-walks the audit ledger, recomputing every hash |
| `make evidence` | regenerates the fairness report, prints the blockers |
| `make fetch-models` | downloads Whisper weights (needs internet) |
| `make validate-asr` | measures the dialect gap on real recordings |
| `make reference-server` | serves the Bhashini ULCA contract locally |

Production swaps SQLite and the in-process bus for PostgreSQL and Redis by
environment variable. The application code is identical.

---

## How the score works

The SVI is **not** a single model output. It is a three-channel composite in
which the machine-learned component is deliberately the *least* authoritative.

| Channel | Source | Nature | Weight |
|---|---|---|---|
| **A — Context** | 18 weighted risk factors + graded offence severity | Deterministic checklist | 0.55 of base |
| **B — Screening** | PC-PTSD-5, PHQ-2→9, GAD-2→7, functional impairment | Deterministic instrument scoring | 0.45 of base |
| **C — Speech & language** | eGeMAPS prosody, conversational timing, crisis lexicon | **Baseline, not trained** | 0 to +25, capped |

```
SVI = clamp(100 × (0.55·A + 0.45·B) + C_delta, floor = base)
```

### Three invariants, enforced in code and covered by tests

1. **Fail-safe monotonicity.** Channel C can raise a score. It can never lower
   one below the floor set by A and B.
2. **Asymmetric abstention.** Poor audio, low recognition confidence, or thin
   coverage zeroes Channel C *and* raises the tier by one. Uncertainty
   escalates; it never de-escalates.
3. **Rules override models.** A separate deterministic layer runs *after* the
   score and can force Critical with no model consulted.

### The C-SSRS never enters the score

Suicidality is handled categorically by the rules layer, never averaged.
Averaging would let active intent be diluted by low scores elsewhere — a caller
with intent but otherwise flat affect could land in Moderate. The C-SSRS
screener is administered in **every** interaction, unconditionally.

### Why any of this matters

Recognition accuracy is systematically worse for rural, low-resource dialects —
Gondi, Santali, Bhojpuri, Chhattisgarhi. Those are disproportionately the
speakers this Act exists to protect. A naive pipeline gives the most
marginalised victims the least accurate transcripts and therefore
*under-triages* them.

Asymmetric abstention is the mitigation, and per-language Critical-class recall
is reported in the product, not only in the paperwork.

---

## Architecture

One interaction bus, four front doors. Every channel normalises into the same
typed event stream, so the triage engine never learns which door an interaction
came through.

```
  IVRS / telephony ─┐
  Web portal        ─┤
  Chatbot           ─┼──▶ Consent Gate ──▶ Interaction Bus
  Mobile app        ─┘                            │
                                                  ▼
              ┌───────────────┬───────────────┬───────────────┐
              │ ASR + VAD     │ Acoustic FE   │ Intake Agent  │
              │ Bhashini /    │ openSMILE     │ slots &       │
              │ Whisper       │ eGeMAPS       │ screeners     │
              └───────┬───────┴───────┬───────┴───────┬───────┘
                      ▼               ▼               ▼
                 Lexical FE      Channel C        Channels A + B
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

### Layout

```
core/            no I/O, standard library only, fully unit-tested
  events.py      typed event schema crossing every boundary
  svi/           Channel A factors, Channel B instruments, the engine
  rules/         escalate-only safety layer + per-language crisis rules
  actions/       statute → action → owner → SLA, as reviewable data
  audit.py       the hash chain
services/
  audio/         telephony simulation, VAD, quality gate, eGeMAPS
  asr/           Bhashini client, local Whisper, router, reference server
  nlp/           redaction, crisis lexicons, fact extraction, distress baseline
  intake/        the interview: slot schedule and dialog policy
  store/         SQLAlchemy models and the repository
  api/           FastAPI, REST + WebSocket
web/             React console and district dashboard
evidence/        model card, DPIA, clinical basis, fairness, pilot protocol
```

`core/` has **no third-party dependencies at all**. The part of the system that
decides a risk tier should be auditable without trusting our dependency tree,
and no library upgrade should be able to change a score.

---

## Languages

| Language | ASR | Crisis lexicon | Screeners | Consent script |
|---|---|---|---|---|
| Hindi (`hi`) | Bhashini native · Whisper native | 71 terms, **unreviewed** | drafted | drafted |
| Bhojpuri (`bho`) | Bhashini native · **Whisper substitutes Hindi** | 43 terms, **unreviewed** | drafted | drafted |

Bhojpuri is supported deliberately as the harder case. Whisper has no Bhojpuri
token and decodes it as Hindi; that substitution is surfaced to the counsellor
rather than hidden, and it is the direct cause of the dialect accuracy gap.

---

## Readiness

`GET /readiness` returns this live. `make readiness` prints it.

```
production ready: False
  BLOCKER  Hindi crisis lexicon (71 terms) has not been reviewed by a native speaker
  BLOCKER  Bhojpuri crisis lexicon (43 terms) has not been reviewed by a native speaker
```

A system that escalates suicide risk from a word list nobody qualified has read
is not ready to take live calls, and the code says so rather than hoping nobody
asks. The lexicons are still *used* — for a crisis lexicon, matching on an
unconfirmed term is safer than not matching, because every error it can make
escalates — but the gap is visible in the console, the API and this README.

**No recognition or triage accuracy is claimed anywhere in this repository.**
Neither dataset exists yet. What is claimed is what is true: 266 automated tests
over the scoring invariants, the safety layer, redaction, the policy table, the
audit chain and the API.

---

## What is real, and what is a boundary

Every part of the pipeline runs: audio processing, recognition routing,
redaction, crisis detection, extraction, the interview, scoring, the safety
layer, the entitlement mapping, persistence, the audit chain, both consoles.

Three interfaces need credentials issued only to government operators, and are
documented in [`evidence/INTEGRATION.md`](evidence/INTEGRATION.md) with what is
built and what remains:

| Interface | Status |
|---|---|
| Tele-MANAS (14416) warm transfer | Action, SLA and repeat-until-accepted logic built; telephony leg needs NHM credentials |
| NHAA 14566 SIP trunk | Full 8 kHz audio path built; trunk registration needs a licensed carrier |
| Integrated Portal case API | Action packet built and auditable; write needs NIC credentials |
| Bhashini ASR | **Real ULCA client**, tested over real HTTP against a local reference server; needs a ULCA key |

Nothing in this repository simulates a government system and presents the
simulation as a connection.

---

## Privacy

Handling data about victims of caste atrocities is the most sensitive category
recognised under the **DPDP Act 2023**. The following are architectural, not
optional:

- **Spoken consent in three separate scopes** — analysis, retention, referral —
  captured in the caller's language and recorded in the ledger. Declining
  analysis puts the interaction in passive mode: full human handling, no
  scoring, and no penalty to the complainant.
- **PII redaction before persistence**, with a guard at the storage boundary
  that raises rather than quietly cleaning.
- **Bounded retention.** Raw audio is isolated so purges never touch the case
  record, and each purge is itself an audited event.
- **A tamper-evident ledger.** Every snapshot, rule trigger, override, action,
  consent decision and deletion is hash-chained and independently verifiable.
- **No third-party egress.** Language services use Bhashini (MeitY); models run
  locally; the console loads nothing over the network.

Full assessment, including the one declared residual gap in redaction:
[`evidence/DPIA.md`](evidence/DPIA.md).

---

## Documentation

| Document | What it answers |
|---|---|
| [`evidence/MODEL_CARD.md`](evidence/MODEL_CARD.md) | What it does, how the score is made, what it cannot do, what is unmeasured |
| [`evidence/DPIA.md`](evidence/DPIA.md) | Lawful basis, consent, minimisation, residual risks |
| [`evidence/CLINICAL_BASIS.md`](evidence/CLINICAL_BASIS.md) | Which instruments, why, and what has **not** been validated |
| [`evidence/FAIRNESS.md`](evidence/FAIRNESS.md) | Generated from the database. Currently: no data, no claim |
| [`evidence/PILOT_PROTOCOL.md`](evidence/PILOT_PROTOCOL.md) | How the system earns an accuracy claim, and the rules that halt it |
| [`evidence/INTEGRATION.md`](evidence/INTEGRATION.md) | The government interfaces, built and remaining |
| [`DECISIONS.md`](DECISIONS.md) | 50 frozen decisions with their reasoning |

`DECISIONS.md` is the most useful file for a reviewer. Several entries record
defects that failing tests found rather than choices made in advance — a feature
extractor reporting `0.0` where silence should have been absent, so silence
looked like a perfectly calm voice; an agent that would have asked *"may we keep
your data?"* before the suicide screener after a self-harm disclosure.

---

## Testing

```bash
make test          # 266 pass, 2 skip pending downloaded ASR weights
```

Tests are written as the claims the project makes, not as coverage. If one goes
red, a claim has stopped being true. The ones that carry the most weight:

- `test_cssrs_intent_forces_critical_from_the_lowest_possible_case` — every
  other answer is as calm as it can be, the score says Low, the rule says
  Critical.
- `test_degraded_audio_escalates_the_assessment_rather_than_calming_it` — real
  waveforms through the real telephony channel; identical facts, worse line,
  and the assessment escalates.
- `test_a_gap_without_compensating_abstention_halts_the_pilot` — the fairness
  failure the whole design exists to catch.
- `test_deleting_a_stored_row_breaks_verification` — tamper detection against a
  real database.

---

## What still needs doing

Contributions most useful, in order:

1. **Native-speaker review of the crisis lexicons** (`services/nlp/lexicons/`).
   The top deployment blocker. Ideally counsellors who take these calls. Do not
   extend the Bhojpuri file by translating the Hindi one — the whole reason they
   are separate is that the idioms differ, and a mistranslated idiom of suicidal
   intent is a missed case.
2. **Real recorded speech** in `data/validation/`, then `make validate-asr`, to
   measure the Hindi–Bhojpuri gap. No accuracy claim exists until it does.
3. **Clinician sign-off** on the screener translations and the C-SSRS protocol.
4. **Named-entity redaction** for Hindi and Bhojpuri, registered at the
   `NamedEntityRedactor` interface.

---

## Provenance and use

Built for the Ministry of Social Justice and Empowerment under Smart India
Hackathon PS 26093.

This system is intended for operation by trained helpline counsellors and
authorised officers. It must not be deployed in an autonomous configuration, and
must not be represented to any complainant as a clinical or diagnostic service.

No open-source licence is attached, deliberately: a permissive licence would sit
awkwardly beside those conditions while the clinical review is still outstanding.
