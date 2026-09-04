"""
Local Whisper backend (faster-whisper / CTranslate2).

The offline path. It exists for three reasons that all matter for this
deployment: a district office may have no usable internet, caller audio ideally
never leaves the premises at all, and a demonstration must not depend on a
conference-hall network.

CONFIDENCE. Whisper does not emit a calibrated confidence, so one is derived:

    confidence = exp(avg_logprob) * (1 - no_speech_prob)

`avg_logprob` is the mean token log-probability for the segment, so its
exponential is the geometric mean token probability — a reasonable proxy for
how sure the decoder was. Multiplying by `1 - no_speech_prob` suppresses
segments the model itself suspects contain no speech, which is where Whisper's
well-known hallucinated text appears. The result is a proxy, not a calibrated
probability, and it is documented as such in the model card; it is used only to
compare against a threshold, never reported to a counsellor as a percentage.

Bhojpuri is not a language Whisper was trained on. It is decoded as Hindi,
which is the closest available and which is precisely why word-error rate is
worse for those callers — the exact asymmetry the abstention path exists to
handle. `LANGUAGE_CODES` records that substitution explicitly rather than
letting it happen silently.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from core.events import Language
import core.languages  # noqa: F401 - the table LANGUAGE_CODES is built from
from services.asr.base import ASRUnavailable, Transcript, TranscriptSegment

# What we ask Whisper to decode as, derived from the one language table rather
# than restated here. Several languages this helpline serves have no Whisper
# token at all — Odia and Santali among them — and those must raise rather than
# quietly decode as something else.
LANGUAGE_CODES: Dict[Language, str] = {
    language: language.profile.whisper_token
    for language in Language
    if language.profile.whisper_token is not None
}

# Languages Whisper decodes as a DIFFERENT language because it has no model for
# them. Usable and measurably worse; always flagged on the provenance line.
SUBSTITUTED_LANGUAGES = {
    language for language in Language if language.profile.whisper_substitutes
}

UNSUPPORTED_LANGUAGES = {
    language for language in Language if language.profile.whisper_token is None
}

DEFAULT_MODEL = os.environ.get("SAMVEDNA_WHISPER_MODEL", "small")
DEFAULT_DEVICE = os.environ.get("SAMVEDNA_WHISPER_DEVICE", "cpu")
DEFAULT_COMPUTE = os.environ.get("SAMVEDNA_WHISPER_COMPUTE", "int8")
WHISPER_RATE = 16000

# Whisper expects 16 kHz. Telephony audio arrives at 8 kHz and must be
# upsampled — which restores no information, but the model requires the rate.
from services.audio.telephony import resample_to  # noqa: E402


@dataclass
class WhisperBackend:
    model_size: str = DEFAULT_MODEL
    device: str = DEFAULT_DEVICE
    compute_type: str = DEFAULT_COMPUTE
    name: str = "whisper-local"
    _model: Optional[object] = None

    def load(self) -> None:
        """Loaded lazily and kept. Model load dominates the cost of a short
        utterance, so a per-request load would make streaming unusable."""
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:                     # pragma: no cover
            raise ASRUnavailable("faster-whisper is not installed") from exc
        self._model = WhisperModel(self.model_size, device=self.device,
                                   compute_type=self.compute_type)

    def weights_cached(self) -> bool:
        """Whether the model is already on disk, checked without touching the
        network.

        This is the operationally correct question. A district office may have
        no usable uplink, and `available()` is called on every routing decision:
        answering it by attempting a download would stall a live call behind a
        model fetch, and would report a backend as available on the strength of
        an internet connection the office does not have. Weights are fetched
        deliberately, by `scripts/fetch_models.py`, not incidentally during a
        call.
        """
        if self._model is not None:
            return True
        cache = Path(os.environ.get(
            "HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        if not cache.exists():
            return False
        needle = f"models--Systran--faster-whisper-{self.model_size}"
        return any(child.name == needle and any(child.rglob("model.bin"))
                   for child in cache.iterdir() if child.is_dir())

    def available(self) -> bool:
        if not self.weights_cached():
            return False
        try:
            self.load()
            return True
        except Exception:                              # pragma: no cover
            return False

    @staticmethod
    def segment_confidence(avg_logprob: float, no_speech_prob: float) -> float:
        """See the module docstring. Clamped, because a proxy that escapes
        0..1 would silently violate the `TranscriptSegment` contract."""
        try:
            token_prob = math.exp(avg_logprob)
        except OverflowError:                          # pragma: no cover
            token_prob = 1.0
        return float(min(1.0, max(0.0, token_prob * (1.0 - no_speech_prob))))

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   language: Language) -> Transcript:
        if language in UNSUPPORTED_LANGUAGES:
            # Refusing is the whole point. Whisper will happily decode Santali
            # audio as whatever it thinks it hears and return fluent nonsense
            # with a respectable confidence, and that string would then be read
            # by the extraction layer and feed Channel A. A declared gap that
            # falls through to the next backend, and failing that reaches the
            # counsellor as "type it instead", is the honest behaviour.
            raise ASRUnavailable(
                f"Whisper has no model for {language.profile.english_name}. "
                f"This is a declared coverage gap, not a fault: see "
                f"core/languages.py.")
        self.load()
        audio = np.asarray(audio, dtype=np.float32)
        if sample_rate != WHISPER_RATE:
            audio = resample_to(audio, sample_rate, WHISPER_RATE).astype(np.float32)

        segments, _info = self._model.transcribe(
            audio,
            language=LANGUAGE_CODES[language],
            beam_size=5,
            vad_filter=False,          # our own VAD already ran; two disagreeing
                                       # gates would make timings hard to reconcile
            word_timestamps=False,
        )

        out: List[TranscriptSegment] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(TranscriptSegment(
                text=text,
                start=float(seg.start),
                end=float(seg.end),
                confidence=self.segment_confidence(
                    float(getattr(seg, "avg_logprob", -1.0)),
                    float(getattr(seg, "no_speech_prob", 0.0))),
                language=language,
            ))

        return Transcript(segments=tuple(out), language=language,
                          backend=self.name, model=self.model_size)
