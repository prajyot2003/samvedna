"""
Crisis lexicon matching.

Feeds the hard-rules layer, which means a hit here can escalate an interaction
to CRITICAL with no model consulted. That places two obligations on this module.

REVIEW STATUS IS PART OF THE DATA. Each lexicon carries a review block naming
who confirmed it and when. An unreviewed lexicon is still loaded and still used
— for a crisis lexicon, matching on an unconfirmed term is safer than not
matching at all, because every error it can make escalates, and escalation is
the safe direction. But `reviewed` is False, the counsellor console says so,
and `production_ready()` refuses to certify a deployment whose crisis lexicons
have never been read by a native speaker. Making that visible is the difference
between a known gap and a hidden one.

MATCHING IS CONSERVATIVE. Devanagari has no casing and Hindi is heavily
inflected, so exact substring matching would both over- and under-fire.
Matching is on normalised text at token boundaries, and multi-word phrases are
matched as contiguous token sequences. Recall is knowingly incomplete; the
acoustic and screener channels are what cover expressions the lexicon misses,
and the C-SSRS is administered unconditionally precisely so that no lexicon is
load-bearing on its own.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from core.events import Language

LEXICON_DIR = Path(__file__).with_name("lexicons")

SEVERITY_ORDER = {"moderate": 0, "high": 1, "critical": 2}

# Review status values, worst to best. MACHINE_DRAFTED is deliberately distinct
# from the older UNREVIEWED: "a person assembled this and nobody checked it" and
# "a language model wrote this" are different risks and deserve different words.
NOT_AUTHORED = "NOT_AUTHORED"
MACHINE_DRAFTED = "MACHINE_DRAFTED"
UNREVIEWED = "UNREVIEWED"
REVIEWED = "REVIEWED"

# Categories that the hard-rules layer consumes by name. Renaming one here
# without updating core.rules.hard_rules would silently disconnect a safety
# path, so the link is asserted by a test.
RULE_CATEGORIES: FrozenSet[str] = frozenset({"self_harm", "imminent_violence"})

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))


def normalise(text: str) -> str:
    """NFC, zero-width characters stripped, whitespace collapsed.

    Devanagari text arriving from different keyboards and ASR backends is
    frequently decomposed differently or carries zero-width joiners. Two
    strings that look identical on screen must match, or the lexicon silently
    misses terms depending on which recogniser produced the transcript.
    """
    text = unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", text).strip()


def tokenise(text: str) -> List[str]:
    return [t for t in re.split(r"[^\wऀ-ॿ]+", normalise(text)) if t]


@dataclass(frozen=True)
class LexiconHit:
    category: str
    severity: str
    term: str
    position: int

    @property
    def triggers_rule(self) -> bool:
        return self.category in RULE_CATEGORIES


@dataclass(frozen=True)
class Lexicon:
    language: Language
    language_name: str
    version: str
    reviewed: bool
    authored: bool
    reviewed_by: Optional[str]
    review_note: str
    categories: Dict[str, Dict[str, object]]
    _index: Tuple[Tuple[Tuple[str, ...], str, str, str], ...] = field(default_factory=tuple)

    @property
    def term_count(self) -> int:
        return sum(len(c["terms"]) for c in self.categories.values())

    def review_warning(self) -> Optional[str]:
        """Shown in the counsellor console and the model card."""
        if self.reviewed:
            return None
        if not self.authored:
            return (f"There is no {self.language_name} crisis lexicon. No distress "
                    f"language will be detected automatically in this language at "
                    f"all. The suicide screener is still administered in full, as "
                    f"it is on every call — it is the safeguard you are relying on "
                    f"here, so do not skip it.")
        return (f"The {self.language_name} crisis lexicon has not been reviewed by a "
                f"native speaker. Detection of distress language in this language is "
                f"incomplete; rely on the screeners and on your own judgement.")

    def match(self, text: str) -> List[LexiconHit]:
        tokens = tokenise(text)
        if not tokens:
            return []
        lowered = [t.lower() for t in tokens]
        hits: List[LexiconHit] = []

        for term_tokens, term, category, severity in self._index:
            n = len(term_tokens)
            for i in range(len(lowered) - n + 1):
                if tuple(lowered[i:i + n]) == term_tokens:
                    hits.append(LexiconHit(category=category, severity=severity,
                                           term=term, position=i))
        return hits


def _build_index(categories: Dict[str, Dict[str, object]]):
    index = []
    for name, spec in categories.items():
        severity = str(spec.get("severity", "moderate"))
        for term in spec["terms"]:                      # type: ignore[index]
            tokens = tuple(t.lower() for t in tokenise(str(term)))
            if tokens:
                index.append((tokens, str(term), name, severity))
    # Longest phrases first: "जान दे दूँगा" should be reported rather than a
    # shorter fragment of it that happens to also be listed.
    index.sort(key=lambda entry: -len(entry[0]))
    return tuple(index)


@lru_cache(maxsize=8)
def load_lexicon(language: Language, directory: Optional[str] = None) -> Lexicon:
    path = Path(directory or LEXICON_DIR) / f"{language.value}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no crisis lexicon for {language.value}. A language without a crisis "
            f"lexicon is not a supported language in this system (DECISIONS.md D1).")

    raw = json.loads(path.read_text(encoding="utf-8"))
    review = raw.get("review", {})
    status = str(review.get("status", "")).upper()
    categories = raw["categories"]

    # An unauthored lexicon is allowed to be empty, and only an unauthored one.
    # The distinction has to live in the loader: without it, the only way to
    # register a language nobody on the team speaks is to invent terms for it,
    # and inventing crisis terms in a language you do not know produces silent
    # false negatives that look exactly like coverage.
    empty_permitted = status == NOT_AUTHORED

    for name, spec in categories.items():
        if not spec.get("terms") and not empty_permitted:
            raise ValueError(f"{path.name}: category '{name}' has no terms")
        if spec.get("severity") not in SEVERITY_ORDER:
            raise ValueError(f"{path.name}: category '{name}' has an unknown severity")

    if empty_permitted and any(spec.get("terms") for spec in categories.values()):
        raise ValueError(
            f"{path.name}: marked {NOT_AUTHORED} but carries terms. Once someone "
            f"has written terms the file is authored — set the status to "
            f"MACHINE_DRAFTED or REVIEWED so the readiness gate reports it correctly.")

    return Lexicon(
        language=language,
        language_name=raw.get("language_name", language.value),
        version=raw.get("version", "unknown"),
        reviewed=status == REVIEWED,
        authored=status != NOT_AUTHORED,
        reviewed_by=review.get("reviewed_by"),
        review_note=review.get("note", ""),
        categories=categories,
        _index=_build_index(categories),
    )


@dataclass(frozen=True)
class LexiconAnalysis:
    hits: Tuple[LexiconHit, ...]
    language: Language
    lexicon_reviewed: bool

    @property
    def categories(self) -> FrozenSet[str]:
        return frozenset(h.category for h in self.hits)

    @property
    def rule_categories(self) -> FrozenSet[str]:
        """Passed to `core.rules.hard_rules` as `lexicon_hits`."""
        return frozenset(h.category for h in self.hits if h.triggers_rule)

    @property
    def max_severity(self) -> Optional[str]:
        if not self.hits:
            return None
        return max((h.severity for h in self.hits), key=lambda s: SEVERITY_ORDER[s])

    def summary(self) -> Dict[str, object]:
        return {
            "categories": sorted(self.categories),
            "max_severity": self.max_severity,
            "hit_count": len(self.hits),
            "lexicon_reviewed": self.lexicon_reviewed,
        }


def analyse(text: str, language: Language) -> LexiconAnalysis:
    lexicon = load_lexicon(language)
    return LexiconAnalysis(hits=tuple(lexicon.match(text)), language=language,
                           lexicon_reviewed=lexicon.reviewed)


def production_ready(languages: Sequence[Language] = tuple(Language)) -> Tuple[bool, List[str]]:
    """Deployment gate.

    A system that escalates suicide risk from a word list nobody qualified has
    read is not ready to take live calls, however well the rest of it works.
    This is checked in CI and reported in the evidence pack; it is expected to
    be False until the lexicons are signed off, and saying so plainly is the
    point.
    """
    blockers: List[str] = []
    for language in languages:
        try:
            lexicon = load_lexicon(language)
        except FileNotFoundError as exc:
            blockers.append(str(exc))
            continue
        if not lexicon.authored:
            blockers.append(
                f"{lexicon.language_name} has no crisis lexicon at all "
                f"(version {lexicon.version}) — it must be written by a speaker "
                f"of the language before callers are offered it")
        elif not lexicon.reviewed:
            blockers.append(
                f"{lexicon.language_name} crisis lexicon ({lexicon.term_count} terms, "
                f"version {lexicon.version}) has not been reviewed by a native speaker")
    return not blockers, blockers
