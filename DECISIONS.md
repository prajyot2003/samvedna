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

## D10 — The action table is JSON, not YAML (2026-08-29)
The README originally specified `entitlements.yaml`. PyYAML would be a
third-party dependency inside `core/`, which D3 forbids. The table is now
`core/actions/entitlements.json`, loaded with the standard library.

JSON has no comments, so anything that would have been a comment is an
explicit `note` field on the action or policy. It is still one flat,
reviewable file that a nodal officer or lawyer can read without reading Python,
which was the actual requirement.

The table is validated at import, not at request time: an unknown owner, a
dangling action id, a misspelled fact key or a policy that resolves to nothing
raises `PolicyTableError` and fails the build. A typo in a statutory reference
must never become a silently missing referral on a live call.

## D11 — Every action that places a duty must cite its basis (2026-08-29)
Enforced by `test_every_enforcement_action_cites_a_basis`. Information,
follow-up and internal control actions are exempt; everything that places a
duty on an officer or claims an entitlement for a complainant must name the
provision it rests on. The test caught two actions missing one on first run,
which is the point of having it.

## D12 — Audit payloads are normalised before hashing (2026-08-29)
`core.audit.normalise_payload` flattens a payload to plain JSON types once, and
the digest is taken over the result. The hash therefore covers exactly the
bytes the JSON column stores.

Found by `test_actions_persist_with_deadlines_and_a_ledger_entry_each`: an
action payload carries a `due_at` datetime, which `canonical_json` encodes
happily but SQLAlchemy's JSON column rejects. The dangerous version of this bug
is not the crash — it is the near miss where a payload hashes in one shape and
persists in another, and the chain fails verification weeks later with no way
to tell tampering from a serialisation quirk.

## D13 — Ledger concurrency is enforced by a UNIQUE constraint on seq
Appending to a hash chain is a read-modify-write on the head. Concurrent
writers both compute the same next sequence number; the UNIQUE constraint means
one insert survives and the other retries against the new head. Retries are
bounded at 8, after which `ChainAppendError` is raised rather than swallowed —
a system that cannot record what it did must not carry on doing it.

Standalone appends retry internally. An append that joins a caller's
transaction does not: there the conflict must surface to the caller, who owns
the transaction and therefore the retry.

## D14 — Timestamps are reattached as UTC on read
SQLite has no timezone-aware type, so `DateTime(timezone=True)` round-trips
naive. `core.audit` refuses naive datetimes by design, which would make every
chain unverifiable after a restart. All stored timestamps are UTC by
construction and `_as_utc` reattaches it on read. Covered by
`test_timestamps_survive_the_round_trip_and_the_chain_still_verifies`.

## D15 — An override without a stated reason is refused
Not warned about, not defaulted — refused at the repository boundary. An
unexplained reversal of a risk assessment is worse than no override at all,
because it leaves a record that looks considered and is not.

## D21 — ASR confidence is mandatory, not optional (2026-08-29)
`TranscriptSegment` has no way to express a missing confidence. A backend that
cannot report how sure it was cannot be used for triage here, because
confidence is what drives the quality gate, which drives abstention, which is
the mechanism protecting callers whose dialect the recogniser handles badly.

Whisper emits no calibrated confidence, so one is derived:
`exp(avg_logprob) * (1 - no_speech_prob)`. The exponential of the mean token
log-probability is the geometric mean token probability; the second factor
suppresses segments the model itself believes contain no speech, which is where
Whisper's hallucinated text appears. It is a proxy, documented as such in the
model card, used only against a threshold and never shown to a counsellor as a
percentage.

## D22 — Bhojpuri language substitution is declared, never silent
Whisper has no Bhojpuri token, so Bhojpuri is decoded as Hindi. That
substitution is the direct cause of the dialect accuracy gap, so
`SUBSTITUTED_LANGUAGES` records it and the counsellor console shows
"decoded using the closest supported language; accuracy is reduced for this
dialect". Bhashini carries Bhojpuri natively, which is why it is the preferred
backend for precisely the callers the fairness argument concerns.

## D23 — Bhashini's absent confidence defaults below the gate threshold
The ULCA ASR response carries no per-segment confidence. Assuming a high value
would silently disable the abstention path for the production backend — the
worst possible place for it to be disabled. `UNKNOWN_CONFIDENCE = 0.5` sits
below `MIN_ASR_CONFIDENCE`, so a transcript with no confidence information is
treated as unreliable until the field is available.

## D24 — available() never touches the network
It is called on every routing decision. A version that attempted a download
would stall a live call behind a model fetch and would report a backend as
usable on the strength of an uplink a district office may not have. Weights are
fetched deliberately by `scripts/fetch_models.py`; `weights_cached()` then
answers from disk.

## D25 — An outage raises; it never returns an empty transcript
An empty transcript is indistinguishable from a silent caller and would be
scored as one. Every backend raises `ASRUnavailable` instead, and the router
raises when nothing can serve the request.

## D26 — The ULCA reference server is a deliverable, not a test fixture
`services/asr/reference_server.py` implements the Bhashini contract locally over
real HTTP. The client talks to it with its real transport, retry and parsing
code — nothing is stubbed client-side. It exists so the team can build against
the contract before credentials arrive, and so failure modes a live service will
not produce on demand (5xx, timeout, malformed envelope) are actually tested.

## D27 — Recognition accuracy is measured on real recordings only
Synthetic signals prove the DSP and nothing about recognition.
`scripts/validate_asr.py` measures duration-weighted confidence and gate
outcomes per language over real recorded speech, and reports the Hindi-Bhojpuri
gap. That measured number goes in the fairness report; no accuracy claim is made
without it. Recordings are consented, unnamed, git-ignored and never leave the
machine.

Note on this environment: huggingface.co is blocked by organisation egress
policy in both the build container and the desktop workspace, so Whisper weights
cannot be fetched here and the `needs_model` tests skip. They run on a developer
machine via `make test-asr`.
