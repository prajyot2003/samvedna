"""
The languages this system speaks, and exactly how well it speaks each one.

WHY THIS FILE EXISTS. Language support was previously three facts in three
places: an enum in `core.events`, a Whisper token map in `services.asr`, and a
ULCA code map in the Bhashini client. Adding a language meant remembering all
three, and nothing failed if you forgot — the language simply worked worse, in
a way no test and no screen reported.

WHAT A LANGUAGE COSTS. Registering one here is a declaration, not a switch.
`core.events.Language` must carry it, a crisis lexicon must exist for it, and
the intake schedule must either translate its prompts or fall back and say so.
The readiness gate reads this table and refuses to certify a deployment whose
languages are not actually served.

THE ASYMMETRY THIS TABLE MAKES VISIBLE, and the reason it is worth reading:
the languages that need this helpline most are the ones speech recognition
serves worst. Whisper has tokens for Hindi, Bengali, Marathi, Telugu, Tamil,
Kannada, Gujarati and Punjabi — the languages of settled, urban, majority
populations. It has none for Odia, none for Maithili, none for Bhojpuri, and
none for Santali. Santali is an Adivasi language with over seven million
speakers, written in its own script, spoken across exactly the districts that
generate the heaviest SC/ST (PoA) Act caseload.

So the callers whose complaints this helpline exists to receive are the callers
our recognisers understand least well. That is not a limitation to be worked
around quietly; it is the central fairness fact about the system, and it is why
`ASRSupport` is a declared property of every language rather than something a
caller discovers when nothing appears on the counsellor's screen. The
abstention path in the SVI engine is the mitigation: thin or absent recognition
lowers confidence, and lowered confidence escalates rather than reassures.

Standard library only. `core` stays dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ASRSupport(str, Enum):
    """How well speech recognition serves a language. Ordered worst to best is
    deliberate: code that compares these is asking a fairness question."""

    NONE = "none"
    """No backend transcribes this language. The counsellor types; the acoustic
    channels (quality gate, prosody) still run, because they are language
    independent and are the only passive signal these callers can contribute."""

    SUBSTITUTED = "substituted"
    """Decoded as a related language because no model exists for this one.
    Usable and measurably worse. Always recorded on the transcript's provenance
    — a silent relabel would hide the error rate in the one place it matters."""

    DECLARED = "declared"
    """A backend documents support that nobody here has run. Distinct from
    NATIVE on purpose: 'the ULCA catalogue lists Odia' and 'we have transcribed
    Odia' are different claims, and a system that reports them identically is
    reporting a hope as a capability."""

    NATIVE = "native"
    """A backend transcribes this language directly, and it has been run."""


@dataclass(frozen=True)
class LanguageProfile:
    """Everything the system knows about how well it serves one language."""

    code: str
    english_name: str
    endonym: str
    script: str

    states: Tuple[str, ...]
    """Where the speakers this helpline serves actually are. Present so that a
    reviewer can check the language list against the caseload rather than
    against a population table."""

    whisper_token: Optional[str] = None
    """The token passed to Whisper. None where Whisper has no model at all."""

    whisper_substitutes: bool = False
    """True when `whisper_token` names a DIFFERENT language than this one."""

    bhashini_code: Optional[str] = None
    """ULCA source language code. Bhashini is the sovereign path and covers
    several languages Whisper does not."""

    bhashini_verified: bool = False
    """Whether this code has been confirmed against a live ULCA pipeline rather
    than read from documentation. Unverified codes are declared, not trusted:
    an unavailable model must surface as a capability gap, and the pilot
    protocol carries verifying these as a task."""

    note: str = ""

    @property
    def asr_support(self) -> ASRSupport:
        """The best a caller in this language can currently expect.

        Ordered by what actually happens on a call, not by what is configured:
        a verified backend beats a native Whisper token beats a substitution
        beats a catalogue entry nobody has exercised.
        """
        if self.bhashini_code and self.bhashini_verified:
            return ASRSupport.NATIVE
        if self.whisper_token and not self.whisper_substitutes:
            return ASRSupport.NATIVE
        if self.whisper_token and self.whisper_substitutes:
            return ASRSupport.SUBSTITUTED
        if self.bhashini_code:
            return ASRSupport.DECLARED
        return ASRSupport.NONE


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------
# Selected by SC/ST (PoA) Act caseload rather than by speaker count. The states
# that generate the most cases are Uttar Pradesh, Rajasthan, Madhya Pradesh,
# Bihar, Odisha, Maharashtra, Andhra Pradesh, Telangana, Karnataka and Gujarat,
# and this list follows them. Maithili and Santali earn their places the same
# way: they are spoken across the Bihar–Jharkhand–Odisha belt by exactly the
# populations the Act protects, and no recogniser serves either of them.

PROFILES: Dict[str, LanguageProfile] = {
    "hi": LanguageProfile(
        code="hi", english_name="Hindi", endonym="हिन्दी", script="Devanagari",
        states=("Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Bihar",
                "Haryana", "Delhi", "Jharkhand", "Chhattisgarh"),
        whisper_token="hi", bhashini_code="hi", bhashini_verified=False,
        note="The reference language. Every fallback in the intake schedule "
             "resolves here, so its prompts carry the most review weight."),

    "bho": LanguageProfile(
        code="bho", english_name="Bhojpuri", endonym="भोजपुरी", script="Devanagari",
        states=("Bihar", "Uttar Pradesh", "Jharkhand"),
        whisper_token="hi", whisper_substitutes=True,
        bhashini_code="bho", bhashini_verified=False,
        note="Decoded as Hindi by Whisper. Bhashini carries it natively, which "
             "is the clearest single argument for the sovereign path."),

    "mai": LanguageProfile(
        code="mai", english_name="Maithili", endonym="मैथिली", script="Devanagari",
        states=("Bihar", "Jharkhand"),
        whisper_token="hi", whisper_substitutes=True,
        bhashini_code="mai", bhashini_verified=False,
        note="Eighth Schedule language, roughly thirty million speakers, no "
             "Whisper model. Substituted to Hindi with the same penalty "
             "Bhojpuri carries."),

    "mr": LanguageProfile(
        code="mr", english_name="Marathi", endonym="मराठी", script="Devanagari",
        states=("Maharashtra", "Goa"),
        whisper_token="mr", bhashini_code="mr", bhashini_verified=False),

    "bn": LanguageProfile(
        code="bn", english_name="Bengali", endonym="বাংলা", script="Bengali",
        states=("West Bengal", "Tripura", "Assam", "Jharkhand"),
        whisper_token="bn", bhashini_code="bn", bhashini_verified=False),

    "te": LanguageProfile(
        code="te", english_name="Telugu", endonym="తెలుగు", script="Telugu",
        states=("Andhra Pradesh", "Telangana"),
        whisper_token="te", bhashini_code="te", bhashini_verified=False,
        note="Andhra Pradesh and Telangana carry a heavy caseload under the "
             "Act and a large Scheduled Caste population."),

    "ta": LanguageProfile(
        code="ta", english_name="Tamil", endonym="தமிழ்", script="Tamil",
        states=("Tamil Nadu", "Puducherry"),
        whisper_token="ta", bhashini_code="ta", bhashini_verified=False),

    "kn": LanguageProfile(
        code="kn", english_name="Kannada", endonym="ಕನ್ನಡ", script="Kannada",
        states=("Karnataka",),
        whisper_token="kn", bhashini_code="kn", bhashini_verified=False),

    "gu": LanguageProfile(
        code="gu", english_name="Gujarati", endonym="ગુજરાતી", script="Gujarati",
        states=("Gujarat", "Dadra and Nagar Haveli"),
        whisper_token="gu", bhashini_code="gu", bhashini_verified=False),

    "pa": LanguageProfile(
        code="pa", english_name="Punjabi", endonym="ਪੰਜਾਬੀ", script="Gurmukhi",
        states=("Punjab", "Haryana", "Chandigarh"),
        whisper_token="pa", bhashini_code="pa", bhashini_verified=False,
        note="Punjab has the highest Scheduled Caste share of any state in "
             "India, near a third of its population."),

    "or": LanguageProfile(
        code="or", english_name="Odia", endonym="ଓଡ଼ିଆ", script="Odia",
        states=("Odisha", "Jharkhand", "West Bengal"),
        whisper_token=None, bhashini_code="or", bhashini_verified=False,
        note="No Whisper model exists. Odisha has one of the largest Scheduled "
             "Tribe populations in the country, so this gap falls squarely on "
             "the callers the Act is written for. Bhashini is the only path."),

    "sat": LanguageProfile(
        code="sat", english_name="Santali", endonym="ᱥᱟᱱᱛᱟᱲᱤ", script="Ol Chiki",
        states=("Jharkhand", "Odisha", "West Bengal", "Bihar", "Assam"),
        whisper_token=None, bhashini_code=None,
        note="An Eighth Schedule Adivasi language with over seven million "
             "speakers and NO speech recognition of any kind in this system. "
             "Registered anyway: a Santali speaker reaching 14566 must be "
             "served by a counsellor typing, with the acoustic channels still "
             "measured, rather than turned away or silently handled in a "
             "language they did not choose. This row is the fairness argument "
             "in its plainest form."),
}


def profile(code: str) -> LanguageProfile:
    return PROFILES[code]


def codes() -> List[str]:
    return list(PROFILES)


def with_asr_support(support: ASRSupport) -> List[LanguageProfile]:
    return [p for p in PROFILES.values() if p.asr_support is support]


def coverage_summary() -> Dict[str, int]:
    """How many languages sit at each level of recognition support. Reported by
    the readiness endpoint so the gap is a number someone has to look at."""
    counts = {support.value: 0 for support in ASRSupport}
    for p in PROFILES.values():
        counts[p.asr_support.value] += 1
    return counts
