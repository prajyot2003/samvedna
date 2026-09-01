# Data Protection Impact Assessment

**System:** SAMVEDNA — triage support for NHAA 14566 and the Integrated Portal
**Assessed:** 2026-08-29 · **Version:** 0.9.0
**Legal basis:** Digital Personal Data Protection Act 2023; SC/ST (Prevention of
Atrocities) Act 1989 and Rules 1995

---

## 1. Why this assessment is necessary

The data is about victims of caste atrocities, given at the moment of first
disclosure, frequently by people still living beside the accused. It combines
three of the most sensitive categories recognised in Indian law: caste
identity, criminal victimisation, and mental health.

A leaked transcript here is not an embarrassment. It is a safety incident that
can expose a witness, enable retaliation, or end a prosecution.

## 2. Data processed

| Category | Purpose | Retention | Basis |
|---|---|---|---|
| Raw call audio | ASR and acoustic features | 30 days, then purged | Consent (analysis) |
| Redacted transcript | Fact extraction, crisis detection | 180 days | Consent (analysis + retention) |
| Derived acoustic features | Channel C | Life of the case | Consent (retention) |
| Risk factors, screener responses | Channels A and B | Life of the case | Statutory duty + consent |
| SVI snapshots, actions, overrides | The case record | Life of the case | Statutory duty |
| Audit ledger | Accountability | Life of the case | Statutory duty |
| Consent records | Proof of lawful basis | Life of the case | Legal obligation |

Caste is recorded because the statute requires it and is **excluded from every
scoring pathway**. No output is conditioned on it.

## 3. Consent

Three separate scopes, sought in the caller's own language and recorded as
artefacts in the audit ledger:

- **analysis** — may this interaction be assessed at all
- **retention** — may derived features be kept after the call
- **referral** — may information be shared with Tele-MANAS, DLSA or officers

Declining `analysis` places the interaction in **passive mode**: full human
handling, no scoring, no snapshot written, and the complaint recorded exactly as
it would be otherwise. The console displays why. Tested in
`test_a_declined_interaction_writes_no_snapshot`.

Only `analysis` consent is sought before the interview begins. Retention and
referral are sought later, and never ahead of the crisis path: asking someone
who has just disclosed suicidal intent whether their data may be kept, before
administering the suicide screener, is indefensible (DECISIONS.md D41).

Consent may be withdrawn at any point; withdrawal is its own audited event and
flips the interaction to passive mode.

## 4. Minimisation

- PII is redacted **before** persistence, and `assert_redacted` guards the
  storage boundary — unredacted text arriving there raises rather than being
  quietly cleaned.
- Raw audio is isolated in its own table so retention purges never touch the
  case record.
- No endpoint serves raw audio, raw transcripts, or caller identifiers. Nothing
  in the console needs them, and an endpoint that can serve them can leak them.
- Derived features are retained, not the audio they came from.

## 5. Declared limitation: redaction is not a named-entity recogniser

Structured identifiers are removed deterministically: phone numbers (Latin and
Devanagari numerals), Aadhaar-shaped numbers, PAN, vehicle registrations, FIR
and case numbers, email addresses, long digit runs. Names and places are removed
where an introducing cue is present — "मेरा नाम", "गाँव", "थाना", "जिला".

**A personal name with no introducing cue survives redaction.** This is a real
residual risk, stated rather than assumed away. Three things bound it: the
retention schedule limits the exposure window; no API surface returns
transcripts; and `NamedEntityRedactor` is the interface where a trained NER for
Hindi and Bhojpuri is registered once one has been evaluated. Closing this gap
is tracked as a pilot deliverable.

## 6. Accountability

Every SVI snapshot, rule trigger, override, action, consent decision and
retention purge is written to a hash-chained ledger. Each record's digest covers
its sequence number, timestamp, actor and interaction id, so a record cannot be
silently edited, reordered, reattributed to another counsellor, or moved between
cases.

Verification is available to anyone with access (`GET /audit/verify`,
`make verify-audit`) and returns non-zero on a break. This does not prevent
someone with database access from rewriting history; it makes rewritten history
**detectable**, which for an accountability record is the property that matters.

## 7. Data location and transfers

Designed to run entirely within NIC / MeghRaj or on-premise infrastructure. Language
services use Bhashini (MeitY); acoustic and text models run locally. **No caller
data leaves government infrastructure.** No third-party analytics, telemetry, or
error reporting is present, and the console loads nothing over the network.

## 8. Rights of the data principal

| Right | How it is served |
|---|---|
| To be informed | Spoken consent script states what is analysed and why |
| To withdraw consent | At any point; audited; flips to passive mode |
| To correction | The read-back flow exists so a caller can deny a mis-extraction |
| To grievance redress | Through the existing NHAA and district mechanism |
| Not to be subject to an automated decision | No decision is automated; every recommendation is advisory and a counsellor can override any tier |

## 9. Residual risks

| Risk | Severity | Status |
|---|---|---|
| Uncued personal name survives redaction | High | Declared; bounded by retention; NER tracked |
| Crisis lexicons unreviewed | High | **Blocks deployment**; readiness endpoint reports it |
| Dialect ASR gap under-serves Bhojpuri speakers | High | Mitigated by abstention; must be measured, not assumed |
| Counsellor defers to the score (automation bias) | Medium | Contributions always shown; override one click away; to be monitored during the pilot |
| Database-level tampering | Medium | Detectable, not preventable; verification exposed |
| Re-identification from a redacted transcript | Medium | Retention-bounded; access-controlled |

## 10. Conclusion

The design is proportionate to the purpose, and the two most serious residual
risks — unreviewed crisis lexicons and an unmeasured dialect gap — are treated
as deployment blockers rather than caveats. The system reports itself as **not
ready for live calls** and will continue to do so until they are closed.
