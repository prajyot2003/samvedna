"""
Telephony channel simulation.

Models what a real NHAA call does to audio before our features ever see it.
Training a prosody model on clean studio recordings and deploying it against
8 kHz G.711 telephony is one of the standard ways an emotion-from-voice system
quietly stops working in production: jitter, shimmer and harmonics-to-noise
ratio all degrade badly under band-limiting and companding, and a model that
never saw that degradation reads the artefacts as affect.

Three effects, applied in the order a phone network applies them:

  1. BAND-LIMITING to roughly 300-3400 Hz. The telephone band removes the
     fundamental of most adult male voices outright — F0 has to be inferred
     from harmonics, which is exactly where pitch trackers get unreliable.
  2. DOWNSAMPLING to 8 kHz.
  3. mu-LAW COMPANDING (G.711), 8 bits per sample with logarithmic
     quantisation: fine steps near silence, coarse steps at high amplitude.

Implemented with numpy alone. Python's `audioop` module would give mu-law
directly but was removed in Python 3.13, and this system has to keep running
on whatever interpreter a district office has.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TELEPHONE_LOW_HZ = 300.0
TELEPHONE_HIGH_HZ = 3400.0
TELEPHONY_RATE = 8000
MU = 255.0


def _sinc_lowpass(cutoff_hz: float, sample_rate: int, taps: int = 129) -> np.ndarray:
    """Windowed-sinc FIR. Odd tap count keeps the delay an integer number of
    samples, so filtering does not smear segment boundaries."""
    if taps % 2 == 0:
        taps += 1
    n = np.arange(taps) - (taps - 1) / 2
    fc = cutoff_hz / sample_rate
    h = 2 * fc * np.sinc(2 * fc * n)
    h *= np.hamming(taps)
    return h / np.sum(h)


def _apply_fir(audio: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Convolve with reflective padding.

    A plain `mode="same"` convolution treats the signal as if silence preceded
    and followed it. When a 2-second analysis window is cut out of the middle of
    a live call that silence is fictional, and the filter sees an abrupt
    speech-from-nothing transient at each window edge — a click that was never
    on the line. Reflecting the signal removes the fiction.

    What this does NOT do is remove energy that is genuinely in the passband.
    A signal that really does start abruptly at its own boundary contains
    wideband energy at that instant, and no amount of padding will filter out
    something that was actually there. Measuring anti-alias performance
    therefore requires a tapered probe signal; see
    `test_downsampling_is_antialiased`.
    """
    pad = len(taps) // 2
    if pad == 0 or len(audio) <= 1:
        return np.convolve(audio, taps, mode="same")
    pad = min(pad, len(audio) - 1)
    padded = np.concatenate([audio[pad:0:-1], audio, audio[-2:-pad - 2:-1]])
    return np.convolve(padded, taps, mode="same")[pad:pad + len(audio)]


def band_limit(audio: np.ndarray, sample_rate: int,
               low_hz: float = TELEPHONE_LOW_HZ,
               high_hz: float = TELEPHONE_HIGH_HZ) -> np.ndarray:
    """Restrict to the telephone passband. The high-pass is built by spectral
    inversion of a low-pass rather than a second filter design, which keeps the
    two stages phase-consistent."""
    nyquist = sample_rate / 2
    out = audio.astype(np.float64, copy=True)

    if high_hz < nyquist:
        out = _apply_fir(out, _sinc_lowpass(high_hz, sample_rate))

    if low_hz > 0:
        lp = _sinc_lowpass(low_hz, sample_rate)
        hp = -lp
        hp[(len(hp) - 1) // 2] += 1.0            # spectral inversion
        out = _apply_fir(out, hp)

    return out


def resample_to(audio: np.ndarray, sample_rate: int, target_rate: int) -> np.ndarray:
    """Anti-aliased resampling. Decimating without filtering first folds
    everything above the new Nyquist back into the speech band as noise, which
    would show up in eGeMAPS as spurious high-frequency energy."""
    if sample_rate == target_rate:
        return audio.astype(np.float64, copy=True)

    work = audio.astype(np.float64, copy=True)
    if target_rate < sample_rate:
        work = _apply_fir(work, _sinc_lowpass(target_rate / 2 * 0.95, sample_rate))

    duration = len(work) / sample_rate
    n_out = int(round(duration * target_rate))
    src = np.arange(len(work)) / sample_rate
    dst = np.arange(n_out) / target_rate
    return np.interp(dst, src, work)


def mu_law_encode(audio: np.ndarray) -> np.ndarray:
    """G.711 mu-law compression to 8-bit codes (0..255)."""
    x = np.clip(audio, -1.0, 1.0)
    compressed = np.sign(x) * np.log1p(MU * np.abs(x)) / np.log1p(MU)
    return np.round((compressed + 1.0) * 127.5).astype(np.uint8)


def mu_law_decode(codes: np.ndarray) -> np.ndarray:
    """Expand 8-bit mu-law codes back to a float waveform."""
    y = codes.astype(np.float64) / 127.5 - 1.0
    return np.sign(y) * ((1.0 + MU) ** np.abs(y) - 1.0) / MU


@dataclass(frozen=True)
class TelephonyResult:
    audio: np.ndarray
    sample_rate: int


def simulate_telephony(audio: np.ndarray, sample_rate: int,
                       target_rate: int = TELEPHONY_RATE,
                       companding: bool = True) -> TelephonyResult:
    """Apply the full channel. Used on every training clip so the features the
    model learns are the features it will actually be given, and available as a
    diagnostic for comparing clean and degraded behaviour on the same input."""
    work = band_limit(audio, sample_rate)
    work = resample_to(work, sample_rate, target_rate)
    if companding:
        work = mu_law_decode(mu_law_encode(work))
    return TelephonyResult(audio=work, sample_rate=target_rate)
