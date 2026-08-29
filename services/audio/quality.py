"""
The signal quality gate.

This is the module that decides whether Channel C is allowed to speak. When it
returns LOW, the SVI engine zeroes the model contribution and raises the tier
floor — uncertainty escalates, it never de-escalates.

It matters more than its size suggests. ASR and prosody both degrade worst on
exactly the calls this Act exists to protect: rural landlines, 2G handsets,
low-resource dialects, background noise from a shared courtyard. If poor signal
quietly produced a calmer score, the system would systematically under-triage
the most marginalised callers. The gate exists so that poor signal produces
*more* human attention instead.

Every verdict carries its reasons, and they are surfaced in the counsellor
console rather than buried: an operator seeing "audio too quiet, model input
withheld" understands the system far better than one seeing a number that
silently means less than it appears to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.events import Confidence
from services.audio.vad import VADResult, detect_speech

# Thresholds. Set from telephony practice, not fitted: 10 dB SNR is roughly
# where narrowband ASR word-error rate starts climbing steeply, and a call with
# under 15% speech is usually a wrong number, a dropped line or an open mic.
MIN_SNR_DB = 10.0
MIN_SPEECH_RATIO = 0.15
MIN_SPEECH_SECONDS = 3.0
MIN_ASR_CONFIDENCE = 0.55
MAX_CLIPPING_RATIO = 0.01
CLIPPING_LEVEL = 0.99


@dataclass(frozen=True)
class QualityReport:
    confidence: Confidence
    snr_db: float
    speech_ratio: float
    speech_seconds: float
    clipping_ratio: float
    mean_asr_confidence: Optional[float]
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.confidence is Confidence.OK

    def as_dict(self) -> Dict[str, object]:
        return {
            "confidence": self.confidence.value,
            "snr_db": round(self.snr_db, 2),
            "speech_ratio": round(self.speech_ratio, 4),
            "speech_seconds": round(self.speech_seconds, 2),
            "clipping_ratio": round(self.clipping_ratio, 5),
            "mean_asr_confidence": (round(self.mean_asr_confidence, 4)
                                    if self.mean_asr_confidence is not None else None),
            "reasons": list(self.reasons),
        }

    def explain(self) -> str:
        """Operator-facing. Says what happened and what follows from it."""
        if self.usable:
            return f"Signal usable — {self.snr_db:.0f} dB SNR, speech {self.speech_ratio:.0%}"
        detail = "; ".join(_REASON_TEXT.get(r, r) for r in self.reasons)
        return f"Model input withheld — {detail}. Assessment escalated for human review."


_REASON_TEXT = {
    "low_snr": "background noise too high",
    "insufficient_speech_ratio": "very little speech on the line",
    "insufficient_speech_duration": "not enough speech to assess",
    "low_asr_confidence": "speech recognition unreliable for this audio",
    "clipping": "audio distorted by clipping",
}


def estimate_snr(audio: np.ndarray, vad: VADResult, sample_rate: int) -> float:
    """Speech power against non-speech power, both measured on this call.

    An absolute level would be meaningless: line gain varies by an order of
    magnitude between a landline and a VoIP softphone, and what matters for
    intelligibility is the ratio, not the volume.
    """
    audio = np.asarray(audio, dtype=np.float64)
    mask = np.zeros(len(audio), dtype=bool)
    for seg in vad.segments:
        mask[int(seg.start * sample_rate):int(seg.end * sample_rate)] = True

    speech, noise = audio[mask], audio[~mask]
    if speech.size == 0:
        return -np.inf
    if noise.size == 0:
        return np.inf                      # no measurable noise floor
    speech_power = float(np.mean(speech ** 2))
    noise_power = float(np.mean(noise ** 2))
    if noise_power <= 0:
        return np.inf
    return 10 * np.log10((speech_power + 1e-12) / (noise_power + 1e-12))


def aggregate_asr_confidence(confidences: Sequence[float],
                             weights: Optional[Sequence[float]] = None) -> Optional[float]:
    """Duration-weighted mean.

    An unweighted mean lets a burst of short, confidently-recognised
    interjections ("ji", "haan") mask a long, badly-recognised passage that is
    where the actual disclosure happened.
    """
    if not confidences:
        return None
    values = np.asarray(confidences, dtype=np.float64)
    if weights is None:
        return float(np.mean(values))
    w = np.asarray(weights, dtype=np.float64)
    if w.sum() <= 0:
        return float(np.mean(values))
    return float(np.sum(values * w) / np.sum(w))


def assess(audio: np.ndarray,
           sample_rate: int,
           asr_confidences: Sequence[float] = (),
           asr_durations: Optional[Sequence[float]] = None,
           vad: Optional[VADResult] = None) -> QualityReport:
    audio = np.asarray(audio, dtype=np.float64)
    vad = vad or detect_speech(audio, sample_rate)

    snr = estimate_snr(audio, vad, sample_rate)
    clipping = float(np.mean(np.abs(audio) >= CLIPPING_LEVEL)) if audio.size else 0.0
    mean_conf = aggregate_asr_confidence(asr_confidences, asr_durations)

    reasons: List[str] = []
    if snr < MIN_SNR_DB:
        reasons.append("low_snr")
    if vad.speech_ratio < MIN_SPEECH_RATIO:
        reasons.append("insufficient_speech_ratio")
    if vad.speech_duration < MIN_SPEECH_SECONDS:
        reasons.append("insufficient_speech_duration")
    if mean_conf is not None and mean_conf < MIN_ASR_CONFIDENCE:
        reasons.append("low_asr_confidence")
    if clipping > MAX_CLIPPING_RATIO:
        reasons.append("clipping")

    return QualityReport(
        confidence=Confidence.LOW if reasons else Confidence.OK,
        snr_db=float(snr),
        speech_ratio=vad.speech_ratio,
        speech_seconds=vad.speech_duration,
        clipping_ratio=clipping,
        mean_asr_confidence=mean_conf,
        reasons=tuple(reasons),
    )
