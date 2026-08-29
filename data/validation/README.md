# Validation recordings

Real recorded speech, used by `scripts/validate_asr.py` and by the
`needs_model` tests. Synthetic signals prove the signal processing; only
recordings of real people can tell you whether the recogniser understands a
Bhojpuri speaker from Gaya.

**Naming:** `<language>_<speaker>_<n>.wav`, language `hi` or `bho`
(e.g. `bho_speaker03_01.wav`). Speaker labels are arbitrary and must not be
names.

**Recording:** use a phone, not a good microphone — the point is to match what
the helpline receives. A short fixed passage plus two or three free-form
sentences per speaker is enough to measure the gap.

**Consent and handling:** recordings are made with the speaker's informed
consent, are not linked to any identity, must not be committed to this
repository, and must not leave the machine they were recorded on. `.gitignore`
excludes `*.wav` in this directory for that reason.
