"""
Backend selection.

Bhashini first where it is configured — it is the sovereign path and it carries
Bhojpuri natively, which Whisper does not. Local Whisper otherwise, and as the
fallback when Bhashini is unreachable, so a district office with a dead uplink
still gets triage rather than nothing.

Every fallback is recorded on the transcript's provenance and surfaced to the
counsellor. Which recogniser produced a transcript changes how much weight it
deserves, and a silent downgrade from the language-native backend to one
decoding Bhojpuri as Hindi is exactly the kind of thing that must not happen
invisibly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from core.events import Language
from services.asr.base import ASRBackend, ASRUnavailable, Transcript
from services.asr.bhashini import BhashiniBackend
from services.asr.whisper_local import SUBSTITUTED_LANGUAGES, WhisperBackend

log = logging.getLogger(__name__)


@dataclass
class RoutedTranscript:
    transcript: Transcript
    backend_used: str
    fell_back: bool
    language_substituted: bool
    attempts: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def provenance_note(self) -> str:
        """Shown in the counsellor console beneath the transcript."""
        parts = [f"Recognised by {self.backend_used}"]
        if self.fell_back:
            parts.append("after the primary recogniser was unavailable")
        if self.language_substituted:
            parts.append("decoded using the closest supported language; "
                         "accuracy is reduced for this dialect")
        return "; ".join(parts) + "."


@dataclass
class ASRRouter:
    backends: Sequence[ASRBackend] = ()

    def __post_init__(self) -> None:
        if not self.backends:
            self.backends = (BhashiniBackend(), WhisperBackend())

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   language: Language) -> RoutedTranscript:
        attempts: List[str] = []
        errors: List[str] = []

        for index, backend in enumerate(self.backends):
            if not backend.available():
                attempts.append(f"{backend.name}:unavailable")
                continue
            try:
                transcript = backend.transcribe(audio, sample_rate, language)
            except ASRUnavailable as exc:
                attempts.append(f"{backend.name}:failed")
                errors.append(f"{backend.name}: {exc}")
                log.warning("ASR backend %s failed: %s", backend.name, exc)
                continue

            attempts.append(f"{backend.name}:ok")
            substituted = (isinstance(backend, WhisperBackend)
                           and language in SUBSTITUTED_LANGUAGES)
            return RoutedTranscript(
                transcript=transcript,
                backend_used=backend.name,
                fell_back=index > 0 and any(a.endswith(":failed") for a in attempts),
                language_substituted=substituted,
                attempts=tuple(attempts),
            )

        raise ASRUnavailable(
            "no ASR backend could serve this request: " + "; ".join(errors or attempts))
