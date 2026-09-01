# Shadow-mode pilot protocol

**Purpose:** to earn the right to make an accuracy claim, and to calibrate the
system against real decisions before it influences any.

---

## 1. Why shadow mode

There is no labelled corpus of NHAA interactions. Every number this system could
report today about triage accuracy would be either unmeasured or measured on the
wrong population. Deploying first and validating later inverts the order in which
a safety-relevant system should earn trust.

In shadow mode the system runs on live interactions and its output is **not
shown to the counsellor**. Counsellor decisions become the gold labels. Only
after calibration against those labels does the SVI surface.

## 2. Phases

| Phase | Duration | System visibility | Exit criterion |
|---|---|---|---|
| **0 — Preparation** | 4 weeks | none | Lexicons and screeners signed off; validation recordings collected; blockers closed |
| **1 — Silent** | 8 weeks | none | ≥ 500 interactions with counsellor tier recorded |
| **2 — Calibration** | 2 weeks | none | Thresholds recalibrated; fairness report produced and accepted |
| **3 — Assisted** | 8 weeks | visible, advisory | Override rate stable; no unexplained tier disagreement pattern |
| **4 — Operational** | ongoing | visible | Quarterly re-audit |

The system does not advance a phase because time has passed. Each exit criterion
is a decision made on evidence by the district nodal officer and the clinical
reviewer jointly.

## 3. What is collected

For every interaction: the counsellor's own tier assignment and their stated
reason; the system's SVI and tier; channel-level values; abstention and its
reasons; ASR confidence and language; whether the C-SSRS was completed; and the
outcome at 30 days where the case record supports it.

Nothing additional about the caller is collected for research purposes. The
pilot uses the record the interaction already produces.

## 4. Metrics that gate advancement

**Sensitivity on the Critical class is the headline.** Target ≥ 0.95 against
counsellor-assigned Critical. Precision is explicitly allowed to be low: a false
negative is a life, a false positive is ten minutes of a counsellor's time.

Also reported:
- AUC-ROC for ranking quality
- Calibration — Brier score, expected calibration error, reliability curve
- **Per-language Critical-class recall and abstention rate.** The Hindi–Bhojpuri
  gap is reported as a first-class number, in the product as well as the report.
  A system that performs worse for the speakers it exists to protect has failed
  even at high aggregate accuracy.
- Override rate, and the direction of overrides. Persistent one-directional
  override is a calibration fault, not counsellor error.

## 5. Stopping rules

The pilot halts and the system is withdrawn if any of these occur:

1. Critical-class sensitivity below 0.90 on any 100-interaction window.
2. A missed Critical case where the system's abstention path did not fire.
3. Per-language Critical recall differing by more than 0.10 between Hindi and
   Bhojpuri without the abstention rate compensating.
4. Evidence of automation bias: counsellors' independent judgement measurably
   converging on the displayed tier during phase 3.
5. Any incident in which a caller was harmed and the system's output contributed.

Stopping is not a failure of the pilot. It is the pilot working.

## 6. Governance

Reviewed monthly by the District Vigilance and Monitoring Committee, with the
clinical reviewer present. The fairness report is produced by
`scripts/fairness_report.py` from the pilot data — not written by hand — so that
what is reported is what was measured.

## 7. Before any of this

Preparation-phase blockers, all currently open:

- [ ] Hindi crisis lexicon reviewed by a native speaker and a crisis counsellor
- [ ] Bhojpuri crisis lexicon reviewed by a native speaker and a crisis counsellor
- [ ] Screener translations back-translated and cognitively tested
- [ ] Clinician sign-off on the C-SSRS administration protocol
- [ ] Validation recordings collected; `make validate-asr` run; dialect gap measured
- [ ] Tele-MANAS transfer leg implemented and tested end to end
- [ ] Named-entity redaction for Hindi and Bhojpuri evaluated and registered
- [ ] Penetration test and access-control review of the deployed service
