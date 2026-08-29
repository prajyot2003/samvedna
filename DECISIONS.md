# Frozen decisions

Changing anything here mid-build costs hours across every other module.
Amend only with the whole team present, and append rather than rewrite.

## D1 — Languages (2026-08-29)
Full pipeline support for **Hindi (hi)** and **Bhojpuri (bho)**.
"Full support" means all four of: ASR, crisis lexicon, screener translation,
consent script. A language without all four is not supported.

Bhojpuri is chosen deliberately as a low-resource dialect. It is where ASR is
weakest, which is precisely where the fairness argument has to be demonstrated
rather than asserted. Per-language Critical-class recall is reported for both.

## D2 — Compute (2026-08-29)
Training on Colab/Kaggle GPUs; inference on an Apple Silicon laptop, CPU/Metal.
Every model shipped must run at interactive speed on the laptop. Anything that
needs a GPU at inference time is out of scope.

## D3 — `core/` is standard library only
No pydantic, numpy, torch or any third party inside `core/`. The scoring heart
must be auditable without trusting our dependency tree, and a library upgrade
must never be able to change a risk score. Third-party packages start at the
service layer.

## D4 — SVI channel weights
Channel A 0.55, Channel B 0.45 of the deterministic base. Channel C bounded to
+25 points maximum: enough to escalate a case by one tier on its own, never
enough to dominate.

## D5 — Channel A normalisation
The aggravating-factor denominator saturates at the sum of the six heaviest
weights (26.0), not the sum of all eighteen. Summing all of them would mean no
real case ever exceeds the Low band. This is a documented design choice, not a
fitted parameter.

## D6 — C-SSRS never enters the continuous score
Suicidality is handled categorically by the hard-rules layer, never averaged.
A composite would let active intent be diluted by low scores elsewhere. The
C-SSRS is administered unconditionally in every interaction.

## D7 — No imputation of missing screeners
A domain that was not administered contributes nothing and reduces coverage.
We never fill in a score we did not collect. Thin coverage escalates the tier
by one rather than reading as reassurance.

## D8 — Tier boundaries
LOW 0–24 · MODERATE 25–49 · HIGH 50–74 · CRITICAL 75–100
Abstention escalates by exactly one tier, never more.

## D9 — Dev runs with zero external services
SQLite and an in-process bus by default; Postgres and Redis by environment
variable in production. Identical application code. A system that needs
infrastructure to demonstrate fails when the venue network does.
