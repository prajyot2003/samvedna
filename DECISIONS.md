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

## D28 — Redaction runs before persistence, and the boundary is guarded
`assert_redacted` is called at the storage boundary and raises if redaction
would still find something. Unredacted text arriving there is a pipeline defect,
not something to fix quietly at the point of writing.

Structured identifiers (phone, Aadhaar-shaped, PAN, vehicle, FIR, case, email,
long digit runs, Devanagari numerals) are caught deterministically. Names and
places are caught by the cues that introduce them in Hindi and Bhojpuri. This is
NOT a named-entity recogniser: a name with no introducing cue survives it. That
gap is declared here and in the DPIA rather than assumed away, and the retention
policy bounds the exposure it leaves.

Replacements are typed — `[PHONE]`, `[VILLAGE]`, `[NAME]` — and the cue word is
preserved, because a record a counsellor cannot read is not a record.

## D29 — Crisis lexicons carry their review status as data (2026-08-29)
A lexicon hit can escalate an interaction to CRITICAL with no model consulted,
so who confirmed the word list is part of the word list. Both lexicons are
currently `UNREVIEWED` seed lists.

They are still loaded and still used: for a crisis lexicon, matching on an
unconfirmed term is safer than not matching, because every error it can make
escalates and escalation is the safe direction. But `reviewed` is False, the
counsellor console says so, and `production_ready()` returns False with named
blockers. A system that escalates suicide risk from a word list nobody qualified
has read is not ready to take live calls, and the code says so out loud.

The Bhojpuri list is thinner than the Hindi one. That is not an oversight to be
tidied by translating the Hindi file — the whole reason they are separate files
is that the idioms differ, and a mistranslated idiom of suicidal intent is a
missed case. Native Bhojpuri speakers, ideally counsellors who take these calls,
must build it out.

## D30 — Extraction proposes, confirmation decides
Everything `services/nlp/facts.py` produces is `FactSource.EXTRACTED` and counts
at a discount in Channel A until the intake agent reads it back and the caller
confirms. Channel A is the heaviest-weighted channel; a factor inferred from a
misheard phrase would otherwise move a tier on the strength of an ASR error.
Extraction confidence is capped below 1.0 so nothing inferred is ever asserted
as certainly as something confirmed.

Cue-based rather than learned, for the same reasons as the VAD: no labelled
corpus exists, a rule that fired can be shown to a counsellor and argued with,
and a rule that misfires can be fixed by the person who noticed.
`MODEL_EXTRACTORS` is where a learned extractor joins after the pilot, and it
will be evaluated against these rules rather than assumed to beat them.

## D31 — Channel C is a documented baseline, and its confidence is capped at 0.6
There is no labelled corpus of NHAA interactions and cannot be one until the
shadow-mode pilot produces gold labels. Training on acted emotion corpora and
deploying on real victim calls would produce a model with real reported accuracy
and unknown field behaviour, which is the exact failure this project is arranged
to avoid.

So Channel C combines a small number of features whose direction is defensible
without our own data — lexical severity, conversational timing, and four eGeMAPS
parameters — with weights chosen a priori and written down. Features whose
direction is genuinely uncertain are excluded rather than included with a
guessed weight.

`BASELINE_CONFIDENCE_CAP = 0.6` is the structural safeguard. The engine computes
the Channel C contribution as `25 * probability * confidence`, so an untrained
component can move a score by at most 15 points of 100 — never enough on its own
to cross more than one tier boundary. Lifting the cap requires replacing the
baseline with something validated against pilot labels, and editing a test that
asserts the bound. That friction is intended.

Confidence is additionally discounted when the lexicon that contributed is
unreviewed, so the discount lands on the language whose speakers are worst
served.

## D32 — The system reports indicators, never emotions
`DistressAssessment.explain()` names what was observed — "long pause before
answering", "reduced variation in pitch", "distress language (fear, isolation)"
— and never an emotion label. Claiming to detect that a caller *is* afraid is
both scientifically contested and outside what this system is for. A test
asserts no emotion word appears in the explanation.

## D33 — The intake agent is a dialog policy, not a generative model (2026-08-29)
It decides the next question from explicit, inspectable state. A generative
model deciding for itself whether to administer a suicide screener is not
something this system will do. Four rules are enforced in `next_action` and
covered one-to-one by tests:

  1. Consent precedes analysis. Declining costs the caller nothing and the
     interaction proceeds in passive mode with full human handling.
  2. The C-SSRS is always administered — never conditional on a score, a model,
     or how the caller sounds.
  3. Crisis language interrupts everything. A self-harm indicator at any point
     jumps straight to the suicide screener, ahead of pending confirmations and
     all remaining risk-factor questions. Asking about land records while
     someone discloses suicidal intent is its own kind of harm.
  4. Facts that move a tier are read back before they count in full.

The agent suggests; on live channels a counsellor asks. Every action carries a
`rationale` shown on the console, because a prompt with no stated reason trains
counsellors to click through without reading.

## D34 — Questions are open before closed, and never leading
The first prompt invites a narrative rather than starting a checklist: people
disclose more in their own account, and extraction harvests it so the agent can
confirm rather than interrogate. Prompts are neutral in the direction of the
answer — "Is the person who did this still nearby?" and never "They are still
nearby, aren't they?" A leading question manufactures a fact the caller did not
assert, in the heaviest-weighted channel of the score.

## D35 — Coverage counts questions put, not factors found
`record_slot` marks a slot asked whether the answer was yes or no. A caller who
answers "no" to everything has been thoroughly assessed; a caller who was never
asked has not, and the abstention path must be able to tell those apart.

## D36 — A crisis handover repeats until a person takes the call
`crisis_handover_done` is set only when a counsellor has actually accepted the
transfer, never on dialling. Until then the agent keeps returning the handover
action. A caller who has disclosed suicidal intent is not released from the line
because a transfer was attempted. Once accepted, the interview resumes any
screening the crisis interrupt jumped ahead of — with the counsellor now
present.

Found while testing: nothing set that flag, so the agent would have looped.

## D37 — PHQ-9 item 9 is a safety disclosure, not a score
It asks about thoughts of self-harm, so a positive answer raises the crisis flag
and routes to the C-SSRS exactly as narrative self-harm language does. Self-harm
surfaces through the depression screener at least as often as through the
narrative.

A test helper originally answered every PHQ item with one blanket value,
including item 9, and the interview correctly ended in crisis handover. The test
data was careless and the system caught it — which is the behaviour wanted, but
worth recording as the reason item 9 is now answered separately everywhere.

## D38 — One bus, four front doors; subscribers never block publishers
IVRS, portal, chatbot and mobile normalise into the same typed event stream, so
the triage engine never learns which channel an interaction arrived through.
`InProcessBus` is the default and needs nothing installed; a Redis Streams
backend swaps in by environment variable. A slow or broken console must not
stall a live call, so a subscriber whose queue is full loses its oldest events
rather than applying backpressure to the triage path. Drops are counted and
logged; a console that has fallen behind shows a gap rather than stale state.

`RedisBus` is deliberately not unit-tested: this environment has no Redis, and
a test that mocks the client would assert only that we call our own functions.
It is integration-tested against a real server at deployment.

## D39 — The SVI is recomputed after every new piece of information
Not once at the end. A caller who becomes more distressed as they describe what
happened looks different from one who was distressed from the first word, and
only a trajectory shows that. Snapshots are append-only so the console can
render the movement.

Actions, by contrast, are raised on tier change only. A twenty-minute call must
not generate the same DySP intimation forty times.

## D40 — The API exposes no raw audio, transcript or caller identifier
Nothing in the console needs them, and an endpoint that can serve them is an
endpoint that can leak them. There is also no endpoint that sets a tier
directly: a tier is reached by assessment or by a recorded override carrying a
stated reason of at least ten characters. Both absences are asserted by tests,
because an absence nobody checks tends not to stay absent.

Authentication is deliberately not invented here. This runs behind the
ministry's existing gateway and identity provider; a hand-rolled auth scheme in
a hackathon repository would be worse than none because it would look like the
problem was solved. `require_operator` is the single integration point.

## D41 — The crisis path outranks retention and referral consent (2026-08-29)
Analysis consent cannot wait: without it nothing may be assessed at all. The
remaining scopes can. Asking someone who has just disclosed suicidal intent
whether we may keep their data — before administering the suicide screener — is
indefensible, and the original ordering did exactly that.

Found by an API test whose `next_action` was asking for retention consent
immediately after a self-harm disclosure. The test was written to check
something else; the ordering flaw it exposed is more important than the
assertion that found it.

## D42 — Readiness is an endpoint, not a paragraph
`GET /readiness` reports whether this build may take live calls and names every
blocker, and the service logs the same verdict loudly at every start. A
deployment blocker visible only in a document nobody opens is not a blocker.

## D43 — No web fonts, no CDN, nothing over the network in the console (2026-08-29)
A district office may have no usable internet, and a console whose typography,
icons or scripts arrive over the network degrades exactly where it is needed
most. Everything ships in the bundle. Asserted by inspection: the only external
URLs in the source are XML namespace identifiers.

Devanagari gets an explicit font stack (`--deva`), because the default system
font on some Linux desktops has no coverage and silently renders tofu — a
counsellor reading boxes instead of a transcript is a failure mode nobody
notices in testing on a Mac.

## D44 — Tier is carried by shape as well as colour
A filled dot and a left stripe accompany every tier colour. Roughly one man in
twelve has a colour vision deficiency, and a helpline console is not a place to
encode critical state in hue alone. Semantic tier colour is also kept separate
from the interface accent so the four tiers never compete with ordinary chrome
for attention.

## D45 — The console renders one server-owned payload
State comes from the REST response after each action; the WebSocket exists to
pick up changes this console did not cause — another operator's override, a
transcript arriving from the IVRS leg — and triggers a re-read rather than
merging events client-side. Two sources of truth would let the screen drift out
of step with the record, and the record is what the audit ledger holds.

## D46 — Every number on screen is shown with its reason
The SVI never appears without the contribution list beneath it, the abstention
banner names what was incomplete, the rule banner says which provision fired,
and every action row carries its statutory basis. A number with no explanation
trains a counsellor either to obey it or to ignore it, and both are failures.

## D47 — node_modules is not rebuilt in the Linux workspace
The desktop workspace mounts the developer's macOS `node_modules`, whose native
Vite/rolldown bindings cannot load on Linux. Running `npm install` there would
replace them with Linux binaries and break the build on the developer's own
machine. Type-checking (`tsc -b`) runs fine in either place and is what CI uses
here; `npm run build` runs on the developer machine.

## D48 — The fairness report is generated, never written (2026-08-29)
`scripts/fairness_report.py` produces `evidence/FAIRNESS.md` from the database.
A report composed by hand reports what its author believed; this one reports
what was measured, and when nothing has been measured it says
"NO DATA. Nothing has been measured." rather than producing plausible-looking
zeroes. A test asserts that the empty report contains no numeric rate at all.

Three properties it enforces:

- **The gold label is the counsellor's tier**, not the system's. Where a
  counsellor overrode, the override is the label. The question is not whether
  the model agrees with itself.
- **Samples below 30 are flagged and excluded from the gap calculation.** A rate
  from four cases is noise wearing a percentage sign.
- **Abstention is reported beside recall.** A language with lower recall and a
  correspondingly higher abstention rate is behaving as designed; one with lower
  recall and no lift in abstention is failing those callers silently, and the
  report says the pilot halts.

## D49 — No accuracy is claimed anywhere in the evidence pack
The model card states plainly that no recognition or triage accuracy has been
measured, because neither dataset exists yet. What is claimed is what is true:
256 automated tests covering the scoring invariants, the safety layer,
redaction, the policy table, the audit chain and the API.

The headline metric, when it exists, will be sensitivity on the Critical class
rather than accuracy, with precision explicitly not optimised.

## D50 — Integration boundaries are documented as scope lines, not discovered
`evidence/INTEGRATION.md` names each of the three interfaces that need
government credentials, and for each one states what is built, what is not, and
what remains. Nothing in this repository simulates a government system and
presents the simulation as a connection.

## D51 — The console could not record what a caller said after the first question
The narrative box only rendered while the agent's next action was
`open_narrative`. Once the interview moved to slots it disappeared, so anything
the caller said from that point on could not be entered at all — no extraction,
no crisis detection, no trace in the case record.

Calls are continuous speech. People disclose the thing that matters twenty
minutes in, halfway through an unrelated question. `NarrativeBox` is now always
present, independent of what the agent is asking.

This was a functional hole, not a design preference, and it survived because
the tests exercised the API rather than the operator's path through the screen.

## D52 — Keyboard-first, and nothing destructive is bound
A counsellor has a phone in one hand and a distressed caller on the other end.
Y/N answers consent, slots and confirmations; number keys answer screener
scales in the order displayed; `U` reviews answers; `?` shows the list.

Two constraints keep it safe. Nothing fires while focus is in a text field, so
recording what the caller said can never answer a question by accident. And no
destructive action is bound — a mistyped key can only record a visible,
correctable answer, never close a case or raise a referral.

## D53 — Corrections are re-answers, never erasures
A caller correcting themselves mid-call is normal, and mis-clicks happen. The
review dialog re-answers through the same endpoint, so the original answer stays
in the audit ledger and the new one is appended after it. The record shows that
a counsellor changed their mind and when, which is the point of having a ledger.

## D54 — The trajectory was stored from the first schema and never drawn
`svi_snapshot` has been append-only since Phase 4 specifically so the score's
movement could be shown, and nothing used it. `GET /interactions/{id}/history`
now serves it and the console draws a sparkline with tier bands behind the line,
marking the points where a safety rule set the tier rather than the score.

The endpoint carries scores and nothing else — no transcript, no facts, no
identifiers — because a second endpoint that returned them would be a second way
to leak them. Asserted by a test that checks the exact key set.

## D55 — Dark mode re-picks the tier colours rather than inverting them
Helpline shifts run overnight. The light-mode critical red goes muddy on a dark
ground, and the one colour on this screen that must never be ambiguous is the
one that means someone is in danger.

## D56 — A tier change is announced, not only coloured
It is the thing a counsellor most needs to notice and the easiest to miss while
listening to someone. A polite live region states the new tier and whether a
safety rule set it. Tier is already carried by shape as well as hue (D44); this
extends the same reasoning to people not looking at the screen at all.

## D57 — Connection strings are normalised, not documented
Managed Postgres providers hand out `postgres://…` URLs. SQLAlchemy 2.0 reads
that scheme as the long-dead psycopg2 driver and fails with
`ModuleNotFoundError: No module named 'psycopg2'` — an error that says nothing
about what is actually wrong. `normalise_database_url()` rewrites the scheme to
`postgresql+psycopg` and adds `sslmode=require` when the host is a known managed
provider. It never downgrades an `sslmode` the operator set explicitly, and it
leaves localhost and SQLite alone, because forcing TLS on a local socket only
breaks the developer loop.

Normalisation happens inside `Repository.__init__`, not only on the settings
object. A URL passed directly — `verify_audit.py --database-url …` — is the
likeliest place an operator meets this, and that path was broken until a test
covered it.

`redact_database_url()` strips the password *and* the query string before any
log line. Providers put credentials in query parameters too.

## D58 — The audit chain needed a real lock, and only real Postgres showed it
`test_concurrent_appends_do_not_fork_the_chain` passed on SQLite for ten phases
and failed the first time it ran against PostgreSQL: four threads exhausted the
eight-attempt retry budget and raised `ChainAppendError`. SQLite's single-writer
lock had been serialising the appenders and hiding the contention completely.
A suite that only ever ran on SQLite would have shipped this, and it would have
appeared in production as dropped audit events under load — the one class of
failure this ledger exists to prevent.

The fix is a transaction-scoped advisory lock (`pg_advisory_xact_lock`) taken
before the chain head is read, so the read-hash-append sequence is atomic against
other appenders, plus jittered exponential backoff on the retry path so the
losers of a race do not resynchronise and collide again. The lock is a no-op on
SQLite, which does not need it. Verified at 12 threads × 25 appends against a
real PostgreSQL 16: no failures, 301 records, no duplicate sequence numbers,
chain verifies.
