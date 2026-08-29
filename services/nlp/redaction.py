"""
PII redaction, applied before anything reaches storage or a model.

Under the DPDP Act 2023 this is the most sensitive category of personal data
there is: identifiable accounts of caste atrocities, given by victims who are
often still living beside the accused. A leaked transcript is not an
embarrassment here, it is a safety incident.

WHAT IS REDACTED DETERMINISTICALLY. Structured identifiers — phone numbers,
Aadhaar-shaped 12-digit numbers, FIR and case numbers, email addresses, PAN,
vehicle registrations, bank accounts, and long digit runs generally. These have
strong surface patterns and are caught with high precision.

WHAT IS REDACTED BY CUE. Names of people and places following the markers that
reliably introduce them in Hindi and Bhojpuri — "मेरा नाम", "गाँव", "थाना",
"जिला", "श्री". High precision, deliberately incomplete recall.

WHAT THIS DOES NOT DO. It is not a named-entity recogniser. A name appearing
with no introducing cue will survive it. `NamedEntityRedactor` is the interface
where a MuRIL-based NER is plugged in when one is trained and evaluated; until
then the gap is declared here and in the DPIA rather than assumed away. The
retention policy is what carries the remaining risk: raw audio is purged on a
fixed schedule and transcripts on a longer one, so the exposure window for
anything redaction missed is bounded.

A REDACTION THAT LOSES MEANING IS A BUG. "मेरे [REDACTED] में" is useless to a
counsellor reviewing the case. Replacements are typed — [PHONE], [VILLAGE],
[NAME], [FIR] — so the record still reads as an account of what happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern, Protocol, Tuple

# --------------------------------------------------------------------------
# Structured identifiers
# --------------------------------------------------------------------------

# Ordering matters: longer, more specific patterns run first so that a 12-digit
# Aadhaar is not first partially consumed by the 10-digit phone pattern.
STRUCTURED_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("EMAIL", r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    ("AADHAAR", r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    ("PAN", r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    ("VEHICLE", r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}\b"),
    ("FIR", r"(?i)\b(?:fir|f\.i\.r\.?|एफ\.?आई\.?आर\.?|प्राथमिकी)\s*"
            r"(?:no\.?|number|संख्या|नंबर)?\s*[:\-]?\s*\d+\s*/?\s*\d*"),
    ("CASE", r"(?i)\b(?:case|केस|मुकदमा)\s*(?:no\.?|number|संख्या|नंबर)\s*[:\-]?\s*\d+"),
    ("PHONE", r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b"),
    ("ACCOUNT", r"\b\d{11,18}\b"),
)

# Devanagari digits, so a number written in Hindi numerals is not missed.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# --------------------------------------------------------------------------
# Cue-introduced names and places
# --------------------------------------------------------------------------

# Each cue captures the tokens that follow it. Recall is deliberately partial;
# precision is what matters, because a redactor that eats ordinary words
# destroys the counsellor's ability to read the case.
CUE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("NAME", r"(?:मेरा\s+नाम|मेरो\s+नाम|हमार\s+नाम|नाम\s+बा|नाम\s+है)\s+"
             r"([ऀ-ॿ]+(?:\s+[ऀ-ॿ]+)?)"),
    ("NAME", r"(?:श्री|श्रीमती|कुमारी)\s+([ऀ-ॿ]+(?:\s+[ऀ-ॿ]+)?)"),
    ("NAME", r"(?i)\bmy\s+name\s+is\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)"),
    ("VILLAGE", r"(?:गाँव|गांव|ग्राम|गाँव\s+के|गंवा)\s+([ऀ-ॿ]+)"),
    ("DISTRICT", r"(?:जिला|जिले|ज़िला)\s+([ऀ-ॿ]+)"),
    ("POLICE_STATION", r"(?:थाना|थाने)\s+([ऀ-ॿ]+)"),
    ("BLOCK", r"(?:तहसील|प्रखंड|ब्लॉक)\s+([ऀ-ॿ]+)"),
)

# Words that follow a cue but are not the thing being named. Without this,
# "गाँव में" redacts the postposition and reads as though a village name was
# captured when none was given.
CUE_STOPWORDS = {
    "में", "से", "का", "की", "के", "को", "पर", "है", "हैं", "था", "थी", "थे",
    "और", "भी", "ही", "तो", "बा", "बाड़े", "रहे", "गइल", "नइखे", "वाला", "वालों",
    "जाकर", "आकर", "नहीं", "कोई", "मेरा", "मेरे", "हमार", "अपना",
}


@dataclass(frozen=True)
class Redaction:
    label: str
    start: int
    end: int
    length: int


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redactions: Tuple[Redaction, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.redactions)

    def counts_by_label(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.redactions:
            out[r.label] = out.get(r.label, 0) + 1
        return out

    @property
    def clean(self) -> bool:
        return not self.redactions


class NamedEntityRedactor(Protocol):
    """Where a trained NER is plugged in. Until one is trained and evaluated on
    Hindi and Bhojpuri, no implementation is registered and the gap is declared
    in the DPIA rather than silently assumed away."""

    def spans(self, text: str) -> List[Tuple[int, int, str]]:
        ...


_COMPILED_STRUCTURED = [(label, re.compile(pattern))
                        for label, pattern in STRUCTURED_PATTERNS]
_COMPILED_CUES = [(label, re.compile(pattern)) for label, pattern in CUE_PATTERNS]


def normalise_digits(text: str) -> str:
    """Devanagari numerals to ASCII, so a phone number written in Hindi
    numerals is caught by the same pattern as one written in Latin."""
    return text.translate(_DEVANAGARI_DIGITS)


def redact(text: str, ner: Optional[NamedEntityRedactor] = None) -> RedactionResult:
    if not text:
        return RedactionResult(text="")

    working = normalise_digits(text)
    spans: List[Tuple[int, int, str]] = []

    for label, pattern in _COMPILED_STRUCTURED:
        for match in pattern.finditer(working):
            spans.append((match.start(), match.end(), label))

    for label, pattern in _COMPILED_CUES:
        for match in pattern.finditer(working):
            captured = match.group(1)
            if not captured:
                continue
            head = captured.split()[0]
            if head in CUE_STOPWORDS:
                continue
            # Redact only the captured entity, never the cue word itself:
            # "गाँव [VILLAGE] में" stays readable, "[REDACTED] में" does not.
            start = match.start(1)
            end = start + len(head) if head != captured else match.end(1)
            spans.append((start, end, label))

    if ner is not None:
        spans.extend(ner.spans(working))

    return _apply(working, spans)


def _apply(text: str, spans: List[Tuple[int, int, str]]) -> RedactionResult:
    """Overlapping matches are resolved in favour of the longest span, so a
    12-digit Aadhaar is not left as a redacted 10-digit phone plus two loose
    digits — a partial redaction of an identifier is not a redaction."""
    if not spans:
        return RedactionResult(text=text)

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    merged: List[Tuple[int, int, str]] = []
    for start, end, label in spans:
        if merged and start < merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end, merged[-1][2])
            continue
        merged.append((start, end, label))

    out: List[str] = []
    redactions: List[Redaction] = []
    cursor = 0
    for start, end, label in merged:
        out.append(text[cursor:start])
        placeholder = f"[{label}]"
        redactions.append(Redaction(label=label, start=len("".join(out)),
                                    end=len("".join(out)) + len(placeholder),
                                    length=end - start))
        out.append(placeholder)
        cursor = end
    out.append(text[cursor:])

    return RedactionResult(text="".join(out), redactions=tuple(redactions))


def assert_redacted(text: str) -> None:
    """Guard for the persistence boundary.

    Called before a transcript is written. If redaction would still find
    something, the text never reached the redactor and that is a defect in the
    pipeline, not something to fix silently at the point of writing.
    """
    result = redact(text)
    if not result.clean:
        raise ValueError(
            f"unredacted PII reaching persistence: {sorted(result.counts_by_label())}")
