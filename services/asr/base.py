"""
ASR backend interface.

Every backend returns the same thing: segments with text, timing, and a
per-segment confidence in 0..1. Confidence is not optional and not a nicety.
It feeds the quality gate, which decides whether Channel C is allowed to
contribute at all, which is the mechanism protecting callers whose dialect the
recogniser handles badly. A backend that cannot report its own confidence
cannot be used for triage in this system, and `Transcript` has no way to
express its absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence, Tuple

import numpy as np

from core.events import Language


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float
    end: float
    confidence: float
    language: Language

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within 0..1, got {self.confidence}")
        if self.end < self.start:
            raise ValueError("segment ends before it starts")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Transcript:
    segments: Tuple[TranscriptSegment, ...]
    language: Language
    backend: str
    model: str = ""

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def confidences(self) -> List[float]:
        return [s.confidence for s in self.segments]

    @property
    def durations(self) -> List[float]:
        return [s.duration for s in self.segments]

    @property
    def speech_duration(self) -> float:
        return sum(self.durations)

    def weighted_confidence(self) -> Optional[float]:
        """Duration-weighted, matching the quality gate. A run of short
        confident interjections must not mask the long, badly-recognised
        passage where the disclosure usually sits."""
        if not self.segments:
            return None
        total = self.speech_duration
        if total <= 0:
            return float(np.mean(self.confidences))
        return float(sum(s.confidence * s.duration for s in self.segments) / total)


class ASRBackend(Protocol):
    """Implemented by `WhisperBackend` and `BhashiniBackend`."""

    name: str

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   language: Language) -> Transcript:
        ...

    def available(self) -> bool:
        """Whether this backend can actually serve a request right now —
        model present on disk, credentials configured, endpoint reachable."""
        ...


class ASRUnavailable(RuntimeError):
    """No configured backend can serve this request. Raised rather than
    returning an empty transcript, because an empty transcript is
    indistinguishable from a silent caller and would be scored as one."""
