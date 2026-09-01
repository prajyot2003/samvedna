# Clinical basis

**Status:** DRAFT — awaiting review by a qualified clinician and by native
speakers of each supported language. No item in this document has been
clinically validated in its translated form.

---

## 1. Position

SAMVEDNA administers **screeners**, not diagnostic instruments. No output is a
diagnosis, and none may be recorded as one. The purpose is triage: identifying
who needs attention sooner, not establishing what anyone has.

Items are adapted from instruments validated for brief and telephonic screening
because those are the ones with published evidence for use in the conditions a
helpline actually operates in — short contact, no visual cues, a distressed
caller, a non-clinician on the other end.

## 2. Instruments used

| Instrument | Domain | Items | Role in the system |
|---|---|---|---|
| **PC-PTSD-5** | Post-traumatic stress | 5 | Channel B, weight 0.30 |
| **PHQ-2 → PHQ-9** | Depression | 2, escalating to 9 | Channel B, weight 0.30 |
| **GAD-2 → GAD-7** | Anxiety | 2, escalating to 7 | Channel B, weight 0.25 |
| **C-SSRS screener** | Suicidal ideation and behaviour | 6 | **Rules layer only — never scored** |
| Single-item functional impairment | Day-to-day functioning | 1 | Channel B, weight 0.15 |

### Escalation

PHQ-2 and GAD-2 are administered first; the full instrument follows only when
the two-item stem scores ≥ 3, the standard published cut-point. This keeps the
interview short for callers who do not need the long form, without discarding
depth for those who do. A domain screened by stem only is flagged as **coarse**
on the console, so a short screen is never mistaken for a complete one.

### The C-SSRS is treated differently on purpose

It is administered **unconditionally in every interaction** — never gated on a
score, a model, or how a caller sounds — and it **does not enter the continuous
score**. Averaging suicidality into a composite would let active intent be
diluted by low scores elsewhere: a caller with intent but flat affect could land
in Moderate. Items 4, 5 and 6 (intent, plan with intent, behaviour) force
Critical through the rules layer with no model consulted.

PHQ-9 item 9 is treated the same way. It asks about thoughts of self-harm, so a
positive answer raises the crisis flag and routes immediately to the C-SSRS.
Self-harm surfaces through the depression screener at least as often as through
the narrative.

## 3. What has NOT been done

Stated plainly because a reviewer will ask, and because the gap is the point.

- **The translations are working drafts.** They have not been back-translated,
  cognitively tested with speakers, or validated against the source instruments.
  A screener item that means something subtly different in Bhojpuri is not the
  same screener.
- **No psychometric validation** has been performed on the adapted items in
  either language.
- **The Channel B weights** (0.30 / 0.30 / 0.25 / 0.15) are a considered
  a priori allocation, not a fitted result. They reflect the relative weight the
  literature gives these domains in post-atrocity presentations; they have not
  been optimised against outcomes, because no outcome data exists.
- **The tier boundaries** (25 / 50 / 75) are chosen, not derived. They will be
  recalibrated against counsellor decisions during the shadow-mode pilot.

## 4. Required sign-off before live use

| Item | Reviewer | Status |
|---|---|---|
| Hindi screener translations | Clinical psychologist fluent in Hindi | Not started |
| Bhojpuri screener translations | Clinical psychologist fluent in Bhojpuri | Not started |
| Hindi crisis lexicon | Native speaker + crisis-line counsellor | Not started |
| Bhojpuri crisis lexicon | Native speaker + crisis-line counsellor | Not started |
| C-SSRS administration protocol | Clinician | Not started |
| Escalation and handover protocol | Tele-MANAS liaison | Not started |

`services.nlp.lexicon.production_ready()` reports the lexicon rows as
deployment blockers. The others are tracked here and in `PILOT_PROTOCOL.md`.

## 5. Duty of care in the interaction itself

- The interview opens with an invitation to describe what happened, not a
  checklist. People disclose more in their own account.
- Questions are neutral in direction. A leading question manufactures a fact the
  caller never asserted, in the heaviest-weighted channel of the score.
- A crisis disclosure interrupts everything. Asking about land records while
  someone discloses suicidal intent is its own kind of harm.
- A caller who discloses suicidal intent is **not released from the line**
  because a transfer was attempted. The handover repeats until a counsellor
  accepts it.
- Declining analysis costs the caller nothing.

## 6. Citations

Full references for PC-PTSD-5, PHQ-2/9, GAD-2/7 and the C-SSRS screener are to
be recorded here by the reviewing clinician, together with the specific version
and adaptation of each item used. Populating this section is part of sign-off:
citing instruments by name without pinning the version is how adaptations drift.
