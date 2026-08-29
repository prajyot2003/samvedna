"""
Voice activity detection and conversational timing.

Energy-and-zero-crossing VAD with an adaptive noise floor. Deliberately not a
neural VAD: this runs on every window of every concurrent call on commodity
district-office hardware, it must behave identically on a laptop and a server,
and its decisions feed a risk score, so it needs to be inspectable. A learned
VAD can be swapped in behind `detect_speech` without touching anything above.

The conversational features derived here — pause structure, response latency,
speech rate — matter more than they look. They are largely language-independent,
which is what lets the system say something useful about a caller speaking a
dialect the ASR handles badly. Long pre-speech latency, fragmented turns and a
high pause ratio are among the more robust correlates of distress across
languages, where pitch statistics are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

FRAME_MS = 25.0
HOP_MS = 10.0
NOISE_PERCENTILE = 10.0
ENERGY_MARGIN_DB = 8.0
HANGOVER_FRAMES = 8          # ~80 ms; bridges stop consonants within a word
MIN_SPEECH_MS = 120.0
MIN_PAUSE_MS = 150.0         # shorter gaps are articulation, not pausing


@dataclass(frozen=True)
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class VADResult:
    segments: Tuple[Segment, ...]
    frame_energy_db: np.ndarray
    noise_floor_db: float
    threshold_db: float
    duration: float

    @property
    def speech_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def speech_ratio(self) -> float:
        return self.speech_duration / self.duration if self.duration else 0.0


def _frame(audio: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, int, int]:
    frame_len = max(1, int(sample_rate * FRAME_MS / 1000))
    hop = max(1, int(sample_rate * HOP_MS / 1000))
    if len(audio) < frame_len:
        audio = np.pad(audio, (0, frame_len - len(audio)))
    n_frames = 1 + (len(audio) - frame_len) // hop
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    return audio[idx], frame_len, hop


def detect_speech(audio: np.ndarray, sample_rate: int) -> VADResult:
    audio = np.asarray(audio, dtype=np.float64)
    duration = len(audio) / sample_rate
    frames, frame_len, hop = _frame(audio, sample_rate)

    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    energy_db = 20 * np.log10(rms + 1e-12)

    # The noise floor is estimated from the quietest tenth of the call rather
    # than assumed, because a village call and an office call have nothing in
    # common acoustically and a fixed threshold would be wrong for both.
    noise_floor = float(np.percentile(energy_db, NOISE_PERCENTILE))
    threshold = noise_floor + ENERGY_MARGIN_DB

    voiced = energy_db > threshold

    # Zero-crossing rate rescues unvoiced fricatives, which carry little energy
    # but are speech; without this, /s/ and /sh/ read as pauses and the pause
    # statistics drift.
    zcr = np.mean(np.abs(np.diff(np.signbit(frames).astype(np.int8), axis=1)), axis=1)
    high_zcr = zcr > 0.25
    near_threshold = energy_db > (threshold - 6.0)
    voiced |= (high_zcr & near_threshold)

    voiced = _apply_hangover(voiced, HANGOVER_FRAMES)
    segments = _to_segments(voiced, hop, frame_len, sample_rate, duration)
    segments = _merge_short_gaps(segments, MIN_PAUSE_MS / 1000)
    segments = tuple(s for s in segments if s.duration * 1000 >= MIN_SPEECH_MS)

    return VADResult(segments=segments, frame_energy_db=energy_db,
                     noise_floor_db=noise_floor, threshold_db=threshold,
                     duration=duration)


def _apply_hangover(voiced: np.ndarray, frames: int) -> np.ndarray:
    out = voiced.copy()
    countdown = 0
    for i, v in enumerate(voiced):
        if v:
            countdown = frames
        elif countdown > 0:
            out[i] = True
            countdown -= 1
    return out


def _to_segments(voiced: np.ndarray, hop: int, frame_len: int,
                 sample_rate: int, duration: float) -> List[Segment]:
    segments: List[Segment] = []
    start: Optional[int] = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segments.append(Segment(start * hop / sample_rate,
                                    min(duration, (i * hop + frame_len) / sample_rate)))
            start = None
    if start is not None:
        segments.append(Segment(start * hop / sample_rate, duration))
    return segments


def _merge_short_gaps(segments: Sequence[Segment], min_gap: float) -> List[Segment]:
    if not segments:
        return []
    merged = [segments[0]]
    for seg in segments[1:]:
        if seg.start - merged[-1].end < min_gap:
            merged[-1] = Segment(merged[-1].start, seg.end)
        else:
            merged.append(seg)
    return merged


@dataclass(frozen=True)
class ConversationalFeatures:
    """Timing structure of a turn. Language-independent by construction."""
    speech_ratio: float
    pause_count: int
    mean_pause: float
    longest_pause: float
    pause_ratio: float
    onset_latency: float          # silence before the caller starts speaking
    segment_count: int
    mean_segment: float
    fragmentation: float          # segments per second of speech

    def as_dict(self) -> dict:
        return {
            "speech_ratio": round(self.speech_ratio, 4),
            "pause_count": self.pause_count,
            "mean_pause": round(self.mean_pause, 4),
            "longest_pause": round(self.longest_pause, 4),
            "pause_ratio": round(self.pause_ratio, 4),
            "onset_latency": round(self.onset_latency, 4),
            "segment_count": self.segment_count,
            "mean_segment": round(self.mean_segment, 4),
            "fragmentation": round(self.fragmentation, 4),
        }


def conversational_features(vad: VADResult,
                            prompt_end: float = 0.0) -> ConversationalFeatures:
    """`prompt_end` is when the system or counsellor stopped speaking, so
    onset latency measures how long the caller took to begin answering."""
    segs = vad.segments
    if not segs:
        return ConversationalFeatures(0.0, 0, 0.0, vad.duration, 1.0,
                                      max(0.0, vad.duration - prompt_end),
                                      0, 0.0, 0.0)

    gaps = [segs[i + 1].start - segs[i].end for i in range(len(segs) - 1)]
    gaps = [g for g in gaps if g >= MIN_PAUSE_MS / 1000]
    speech = sum(s.duration for s in segs)

    return ConversationalFeatures(
        speech_ratio=vad.speech_ratio,
        pause_count=len(gaps),
        mean_pause=float(np.mean(gaps)) if gaps else 0.0,
        longest_pause=float(max(gaps)) if gaps else 0.0,
        pause_ratio=sum(gaps) / vad.duration if vad.duration else 0.0,
        onset_latency=max(0.0, segs[0].start - prompt_end),
        segment_count=len(segs),
        mean_segment=speech / len(segs),
        fragmentation=len(segs) / speech if speech else 0.0,
    )
