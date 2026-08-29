"""
Acoustic feature extraction.

eGeMAPSv02 functionals via openSMILE: 88 standardised parameters covering
pitch, loudness, voice quality (jitter, shimmer, harmonics-to-noise ratio),
spectral balance and temporal structure.

eGeMAPS rather than a learned embedding as the interpretable base, for two
reasons that matter to this project specifically. It is a published, citable
standard in computational paralinguistics, so a reviewer can check what we are
measuring instead of taking our word for it. And it is largely
language-independent, which is what allows a claim of multilingual coverage
that does not silently mean "the languages our training data happened to have".

Windowed rather than whole-call: the SVI moves during an interaction, and a
single set of functionals over a ten-minute call would average a moment of
acute distress into an unremarkable mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

WINDOW_SECONDS = 2.0
HOP_SECONDS = 0.5
MIN_WINDOW_SECONDS = 0.5

# The subset surfaced in the counsellor console. The model consumes all 88;
# these are the ones with a defensible plain-language reading, and they are
# shown as "acoustic indicators", never as an emotion label.
CONSOLE_FEATURES: Tuple[str, ...] = (
    "F0semitoneFrom27.5Hz_sma3nz_amean",
    "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
    "loudness_sma3_amean",
    "loudness_sma3_stddevNorm",
    "jitterLocal_sma3nz_amean",
    "shimmerLocaldB_sma3nz_amean",
    "HNRdBACF_sma3nz_amean",
    "MeanVoicedSegmentLengthSec",
    "MeanUnvoicedSegmentLength",
    "VoicedSegmentsPerSec",
)


# openSMILE reports 0.0 — not NaN — for voicing-dependent parameters in a window
# that contains no voiced frames at all. That default is dangerous here: a pitch
# mean of 0 and a jitter of 0 are what a perfectly steady, perfectly calm voice
# would look like, so silence or a dropped line would read to a downstream model
# as the calmest possible caller. The two claims "not measured" and "measured as
# zero" must not be conflated in a system whose failure mode is under-triage, so
# voicing-dependent parameters are removed outright when nothing was voiced.
VOICING_DEPENDENT_MARKERS: Tuple[str, ...] = ("sma3nz", "Voiced", "voiced")
VOICING_PRESENCE_KEY = "VoicedSegmentsPerSec"


def _is_voicing_dependent(name: str) -> bool:
    return any(marker in name for marker in VOICING_DEPENDENT_MARKERS)


def _drop_unmeasured_voicing(features: Dict[str, float]) -> Dict[str, float]:
    if features.get(VOICING_PRESENCE_KEY, 0.0) > 0.0:
        return features
    return {k: v for k, v in features.items() if not _is_voicing_dependent(k)}


@dataclass(frozen=True)
class ProsodyWindow:
    t_start: float
    t_end: float
    features: Dict[str, float]

    def console_view(self) -> Dict[str, float]:
        return {k: self.features[k] for k in CONSOLE_FEATURES if k in self.features}


class ProsodyExtractor:
    """Wraps openSMILE. Constructed once and reused — instantiating the
    feature set per window costs more than the extraction itself."""

    def __init__(self) -> None:
        import opensmile                     # imported lazily; heavy at import time
        self._smile = opensmile.Smile(
            feature_set=opensmile.FeatureSet.eGeMAPSv02,
            feature_level=opensmile.FeatureLevel.Functionals,
        )

    @property
    def feature_names(self) -> List[str]:
        return list(self._smile.feature_names)

    def extract_one(self, audio: np.ndarray, sample_rate: int) -> Dict[str, float]:
        frame = self._smile.process_signal(
            np.asarray(audio, dtype=np.float64), sample_rate)
        row = frame.iloc[0].to_dict()
        features = {k: float(v) for k, v in row.items() if np.isfinite(v)}
        return _drop_unmeasured_voicing(features)

    def extract_windows(self, audio: np.ndarray, sample_rate: int,
                        window_s: float = WINDOW_SECONDS,
                        hop_s: float = HOP_SECONDS) -> List[ProsodyWindow]:
        audio = np.asarray(audio, dtype=np.float64)
        duration = len(audio) / sample_rate
        if duration < MIN_WINDOW_SECONDS:
            return []

        # Full-length windows only, plus at most one shorter tail window when
        # it covers material no full window reached. Emitting a trailing window
        # that merely re-analyses the end of the previous one would double-count
        # that audio in every trajectory statistic.
        bounds: List[Tuple[float, float]] = []
        start = 0.0
        while start + window_s <= duration + 1e-9:
            bounds.append((start, start + window_s))
            start += hop_s

        if not bounds:
            bounds.append((0.0, duration))
        elif duration - bounds[-1][1] >= MIN_WINDOW_SECONDS:
            bounds.append((bounds[-1][1], duration))

        windows: List[ProsodyWindow] = []
        for w_start, w_end in bounds:
            chunk = audio[int(w_start * sample_rate):int(w_end * sample_rate)]
            features = self.extract_one(chunk, sample_rate)
            if features:
                windows.append(ProsodyWindow(round(w_start, 3), round(w_end, 3), features))
        return windows


def summarise_trajectory(windows: Sequence[ProsodyWindow],
                         feature: str) -> Optional[Dict[str, float]]:
    """How one parameter moved across the call.

    Trajectory carries information a mean discards. A caller whose pitch
    variability collapses partway through an account is showing something a
    whole-call average would erase entirely.
    """
    values = [w.features[feature] for w in windows if feature in w.features]
    if len(values) < 2:
        return None
    arr = np.asarray(values, dtype=np.float64)
    times = np.arange(len(arr), dtype=np.float64)
    slope = float(np.polyfit(times, arr, 1)[0])
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "slope": slope,
        "n_windows": len(arr),
    }
