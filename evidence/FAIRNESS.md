# Fairness report

**Generated:** 2026-09-01 08:48 UTC by `scripts/fairness_report.py`
**Do not edit by hand.** This file is regenerated from the database; a hand-edited figure here would be a claim nobody measured.

---

## 1. What is measured, and against what

The gold label is the counsellor's tier — the system's tier where it was left alone, the overridden tier where a counsellor changed it. The question is not whether the model agrees with itself; it is whether it agrees with the trained human who took the call.

**Critical-class recall** is the headline. Precision is deliberately not optimised: a false negative is a life, a false positive is ten minutes of a counsellor's time.

**Abstention rate** is reported beside it, because abstention is the mechanism protecting speakers the recogniser serves worst. A language with lower recall and a correspondingly higher abstention rate is behaving as designed; one with lower recall and no lift in abstention is failing those callers silently.

## 2. Result

**NO DATA. Nothing has been measured.**

This database contains no completed assessments, so there is nothing to report and no accuracy figure of any kind is claimed. This is the expected state before the shadow-mode pilot; see `evidence/PILOT_PROTOCOL.md`.

To produce a real report:

1. Close the preparation blockers listed below.
2. Run phase 1 (silent) until at least 500 interactions carry a counsellor tier.
3. Re-run this script.

## 5. Deployment readiness

`production_ready()` reports **False**.

- Hindi crisis lexicon (71 terms, version 2026-08-29.seed) has not been reviewed by a native speaker
- Bhojpuri crisis lexicon (43 terms, version 2026-08-29.seed) has not been reviewed by a native speaker

Detection quality is not equal across languages, and the languages served worst are those whose speakers this Act exists to protect. That asymmetry is the reason the abstention path exists, and the reason this report is generated rather than written.
