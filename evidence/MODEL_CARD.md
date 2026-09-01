# Model card — SAMVEDNA Stress Vulnerability Index

**System:** AI-assisted structured triage for the National Helpline Against
Atrocities (14566) and the Integrated Portal
**Version:** 0.9.0 · **Date:** 2026-08-29
**Owner:** Ministry of Social Justice and Empowerment (MoSJE)
**Status:** NOT CLEARED FOR LIVE USE — see *Deployment blockers* below

---

## 1. What this system does

It converts an unstructured first contact into a structured, auditable risk
profile, and maps that profile to the entitlements the SC/ST (Prevention of
Atrocities) Act already provides. It outputs a Stress Vulnerability Index on
0–100 and one of four tiers: Low, Moderate, High, Critical.

## 2. What it does not do

- **It does not diagnose.** No output is a clinical diagnosis and none may be
  recorded as one. The instruments used are screeners.
- **It does not decide.** Every recommendation is advisory. A counsellor can
  override any tier, and the override is what enters the record.
- **It does not detect emotions.** It reports acoustic and linguistic
  *indicators* — pauses, pitch variability, distress vocabulary. It never
  asserts that a caller feels a particular way.
- **It does not use caste as a feature.** Caste appears in the case record
  because the statute requires it; it is walled off from every scoring path.

## 3. Intended users and setting

Trained NHAA counsellors and authorised district officers, operating the
helpline and the Integrated Portal. The system must not be deployed in an
autonomous configuration, must not be represented to any complainant as a
clinical service, and must not be used to deny or delay any entitlement.

## 4. How the score is produced

Three channels. The machine-learned component is deliberately the least
authoritative.

| Channel | Content | Nature | Weight |
|---|---|---|---|
| **A — Context** | 18 weighted risk factors + graded offence severity | Deterministic checklist | 0.55 of base |
| **B — Screening** | PC-PTSD-5, PHQ-2→9, GAD-2→7, functional impairment | Deterministic instrument scoring | 0.45 of base |
| **C — Speech & language** | eGeMAPS prosody, conversational timing, crisis lexicon | **Baseline, not trained** | 0 to +25, capped |

`SVI = clamp(100 × (0.55·A + 0.45·B) + C_delta, floor = base)`

**Channel C is a baseline, not a trained model.** There is no labelled corpus of
NHAA interactions and cannot be one until the shadow-mode pilot produces gold
labels. Training on acted emotion corpora and deploying on real victim calls
would yield real reported accuracy and unknown field behaviour. Channel C
therefore combines features whose direction is defensible from published
paralinguistics without our own data, with weights chosen a priori and recorded
in `services/nlp/distress.py`. Features whose direction is genuinely uncertain
are excluded rather than included with a guessed weight.

Its confidence is capped at **0.6**, so it can move a score by at most 15 points
of 100 — never enough on its own to cross more than one tier boundary. Lifting
the cap requires replacing the baseline and editing a test that asserts the
bound.

### The C-SSRS does not enter the score

Suicidality is handled categorically by the rules layer, never averaged.
Averaging would let active intent be diluted by low scores elsewhere: a caller
with intent but otherwise flat affect could land in Moderate. The C-SSRS
screener is administered in **every** interaction, unconditionally.

## 5. Enforced invariants

Each is covered by tests that fail the build if it stops holding.

1. **Fail-safe monotonicity.** Channel C can raise a score; it can never lower
   one below the A+B floor.
2. **Asymmetric abstention.** Low ASR confidence, poor audio, or thin coverage
   zeroes Channel C *and* raises the tier by one. Uncertainty escalates.
3. **Rules override models.** A separate deterministic layer runs after the
   score and can force Critical with no model consulted.

## 6. Inputs

Audio (8 kHz telephony or 16 kHz web capture), text from the portal or chatbot,
counsellor-entered answers. All text is PII-redacted before storage or
modelling. Raw audio is purged on a fixed schedule; the purge is itself audited.

## 7. Languages

| Language | ASR | Crisis lexicon | Screeners | Consent script |
|---|---|---|---|---|
| Hindi (hi) | Bhashini native; Whisper native | 71 terms, **unreviewed** | drafted | drafted |
| Bhojpuri (bho) | Bhashini native; **Whisper substitutes Hindi** | 43 terms, **unreviewed** | drafted | drafted |

Bhojpuri is supported deliberately as the harder case. Whisper has no Bhojpuri
token, so it decodes Bhojpuri as Hindi; that substitution is surfaced to the
counsellor and is the direct cause of the dialect accuracy gap. Bhashini carries
Bhojpuri natively and is the preferred backend for exactly these callers.

## 8. Performance

**No recognition or triage accuracy is claimed.** Nothing has been measured
against real recorded speech or real triage outcomes, because neither dataset
exists yet and asserting a figure without one would be the failure this project
is arranged to avoid.

What is measured today: 256 automated tests covering the scoring invariants,
the safety layer, redaction, the policy table, the audit chain, and the API.

What must be measured before any accuracy claim:
- Duration-weighted ASR confidence and gate outcomes per language on real
  recordings (`make validate-asr`) — this yields the Hindi–Bhojpuri gap.
- Critical-class recall, AUC, and calibration against counsellor decisions
  from the shadow-mode pilot (`evidence/PILOT_PROTOCOL.md`).

The headline metric when it exists will be **sensitivity on the Critical
class**, not accuracy. The system is deliberately tuned for high sensitivity and
accepts low precision at Critical: a false negative is a life, a false positive
is ten minutes of a counsellor's time.

## 9. Known failure modes

| Failure | Consequence | Mitigation |
|---|---|---|
| ASR worse for rural dialects | The most marginalised callers are under-triaged | Asymmetric abstention; per-language reporting; Bhashini preferred |
| Crisis lexicon incomplete | Distress language missed | C-SSRS always administered; screeners and acoustics are independent paths |
| Cue-based extraction misses a factor | Coverage drops | The agent asks every core factor explicitly regardless |
| Extraction mishears a factor | A tier moves on an ASR error | Tier-relevant facts are read back and confirmed |
| Redaction misses an uncued name | Identifiable data persists | Bounded retention; declared in the DPIA |
| Whisper hallucinates on silence | Fabricated transcript | `no_speech_prob` suppresses confidence; gate withholds Channel C |
| Counsellor defers to the score | Automation bias | Contributions always shown; override one click away; rationale on every prompt |

## 10. Deployment blockers

`GET /readiness` returns these live, and the service logs them at every start.

1. **Hindi crisis lexicon has not been reviewed by a native speaker.**
2. **Bhojpuri crisis lexicon has not been reviewed by a native speaker.**

Additionally required before live use, tracked in `PILOT_PROTOCOL.md`:
clinician sign-off on the screener translations; three months of shadow-mode
operation; a measured fairness report; and the three integration boundaries
closed (`INTEGRATION.md`).

## 11. Ethical and legal basis

DPDP Act 2023 (sensitive personal data; consent, purpose limitation,
retention); SC/ST (PoA) Act 1989 and Rules 1995 for the entitlement mapping.
Consent is sought in three separate scopes and declining costs the complainant
nothing. See `DPIA.md`.

## 12. Contact and change control

Substantive changes to weights, thresholds, the rules layer or the action table
are recorded in `DECISIONS.md` with their reasoning, and this card is updated in
the same commit.
