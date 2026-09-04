"""
Audio front-end tests.

Signals are synthesised deterministically rather than loaded from fixtures, so
the suite runs anywhere and each test states exactly what acoustic condition it
is asserting about. Every test operates on a real waveform through the real
filters and the real openSMILE extractor.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.events import Confidence, Tier, tier_rank
from core.svi.engine import ModelSignals, compute_svi
from core.svi.factors import CORE_COVERAGE_KEYS, ContextFacts
from core.svi.instruments import CSSRSScreen, Screeners
from services.audio import quality
from services.audio.prosody import CONSOLE_FEATURES, ProsodyExtractor, summarise_trajectory
from services.audio.telephony import (TELEPHONY_RATE, band_limit, mu_law_decode,
                                      mu_law_encode, resample_to, simulate_telephony)
from services.audio.vad import conversational_features, detect_speech

SR = 16000


def _rng(seed: int = 11):
    return np.random.default_rng(seed)


def voiced(duration: float, f0: float = 150.0, amp: float = 0.3,
           sample_rate: int = SR) -> np.ndarray:
    """A harmonic-rich periodic signal: a crude but honest stand-in for voiced
    speech, with a fundamental and eight harmonics."""
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    sig = sum(amp / (k + 1) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(8))
    return sig * np.hanning(n) ** 0.2


def silence(duration: float, level: float = 0.002, seed: int = 11,
            sample_rate: int = SR) -> np.ndarray:
    return _rng(seed).normal(0, level, int(sample_rate * duration))


def three_turn_clip() -> np.ndarray:
    return np.concatenate([silence(0.9), voiced(0.8), silence(0.5),
                           voiced(1.2, 170), silence(0.7), voiced(0.5, 140),
                           silence(0.4)])


# ------------------------------------------------- telephony channel

def test_band_limiting_removes_energy_outside_the_telephone_band():
    sr = SR
    t = np.arange(sr) / sr
    low = np.sin(2 * np.pi * 80 * t)        # below the passband
    mid = np.sin(2 * np.pi * 1000 * t)      # inside it
    high = np.sin(2 * np.pi * 6000 * t)     # above it

    assert np.std(band_limit(low, sr)) < 0.25 * np.std(low)
    assert np.std(band_limit(high, sr)) < 0.25 * np.std(high)
    assert np.std(band_limit(mid, sr)) > 0.7 * np.std(mid)


def test_resampling_to_8k_produces_the_expected_length():
    audio = voiced(2.0)
    out = resample_to(audio, SR, TELEPHONY_RATE)
    assert abs(len(out) - TELEPHONY_RATE * 2) <= 1


def test_downsampling_is_antialiased():
    """A 5 kHz tone folds down to 3 kHz if decimation happens without filtering
    first, and that alias would appear in eGeMAPS as genuine spectral energy.
    Compared here against naive decimation, which is exactly what the
    anti-aliasing filter exists to prevent."""
    sr = SR
    t = np.arange(sr) / sr
    # Tapered, so what is measured is aliasing and not the wideband click of a
    # tone that starts abruptly — that energy is genuinely in the passband and
    # no anti-alias filter can or should remove it.
    tone = np.sin(2 * np.pi * 5000 * t) * np.hanning(sr)

    naive = tone[::2]                       # decimate with no filter
    filtered = resample_to(tone, sr, TELEPHONY_RATE)

    def alias_energy(sig):
        spectrum = np.abs(np.fft.rfft(sig))
        freqs = np.fft.rfftfreq(len(sig), 1 / TELEPHONY_RATE)
        band = (freqs > 2500) & (freqs < 3500)
        return float(spectrum[band].sum())

    assert alias_energy(filtered) < 0.01 * alias_energy(naive)


def test_mu_law_round_trip_is_lossy_but_faithful():
    audio = voiced(0.5) * 0.8
    out = mu_law_decode(mu_law_encode(audio))
    assert not np.allclose(out, audio)                       # quantisation is real
    assert np.corrcoef(out, audio)[0, 1] > 0.99              # but the shape survives


def test_mu_law_resolves_quiet_signals_better_than_loud_ones():
    """The defining property of mu-law: logarithmic steps, fine near silence.
    A linear 8-bit quantiser would show the opposite."""
    quiet, loud = voiced(0.4, amp=0.02), voiced(0.4, amp=0.9)
    q_err = np.mean(np.abs(mu_law_decode(mu_law_encode(quiet)) - quiet)) / np.mean(np.abs(quiet))
    l_err = np.mean(np.abs(mu_law_decode(mu_law_encode(loud)) - loud)) / np.mean(np.abs(loud))
    assert q_err < l_err


def test_full_telephony_simulation_changes_the_signal_but_keeps_the_speech():
    clip = three_turn_clip()
    result = simulate_telephony(clip, SR)
    assert result.sample_rate == TELEPHONY_RATE
    before, after = detect_speech(clip, SR), detect_speech(result.audio, TELEPHONY_RATE)
    assert len(after.segments) == len(before.segments)
    assert abs(after.speech_ratio - before.speech_ratio) < 0.15


# ------------------------------------------------- voice activity detection

def test_vad_finds_each_speech_burst():
    vad = detect_speech(three_turn_clip(), SR)
    assert len(vad.segments) == 3
    assert 0.4 < vad.speech_ratio < 0.7


def test_vad_reports_no_speech_on_silence():
    assert detect_speech(silence(3.0), SR).segments == ()


def test_vad_adapts_its_noise_floor_to_the_line():
    """A village landline and an office VoIP line have nothing in common
    acoustically. A fixed threshold would be wrong for both."""
    quiet_line = np.concatenate([silence(0.5, 0.001), voiced(1.0), silence(0.5, 0.001)])
    noisy_line = np.concatenate([silence(0.5, 0.02), voiced(1.0), silence(0.5, 0.02)])
    q, n = detect_speech(quiet_line, SR), detect_speech(noisy_line, SR)
    assert n.noise_floor_db > q.noise_floor_db + 10
    assert len(q.segments) == len(n.segments) == 1


def test_short_gaps_within_a_word_do_not_become_pauses():
    """A 60 ms stop closure is articulation, not pausing. Counting it would
    inflate the pause statistics that feed the distress signal."""
    clip = np.concatenate([silence(0.4), voiced(0.5), silence(0.06),
                           voiced(0.5), silence(0.4)])
    assert len(detect_speech(clip, SR).segments) == 1


def test_conversational_features_measure_onset_latency_from_the_prompt():
    clip = np.concatenate([silence(1.4), voiced(1.0), silence(0.3)])
    feats = conversational_features(detect_speech(clip, SR), prompt_end=0.4)
    assert 0.8 < feats.onset_latency < 1.2


def test_conversational_features_are_finite_when_nobody_speaks():
    feats = conversational_features(detect_speech(silence(3.0), SR))
    assert feats.speech_ratio == 0.0 and feats.pause_ratio == 1.0
    assert all(np.isfinite(v) for v in feats.as_dict().values()
               if isinstance(v, (int, float)))


def test_pause_structure_is_measured():
    vad = detect_speech(three_turn_clip(), SR)
    feats = conversational_features(vad)
    assert feats.pause_count == 2
    assert feats.longest_pause > 0.4
    assert feats.segment_count == 3


# ------------------------------------------------- quality gate

def test_clean_audio_passes_the_gate():
    clip = np.concatenate([silence(0.4), voiced(2.0), silence(0.3), voiced(2.0), silence(0.3)])
    report = quality.assess(clip, SR, asr_confidences=[0.9, 0.88], asr_durations=[2.0, 2.0])
    assert report.confidence is Confidence.OK
    assert report.usable and report.reasons == ()


def test_heavy_background_noise_fails_the_gate():
    clip = np.concatenate([silence(0.4), voiced(2.0), silence(0.4)])
    noisy = clip + _rng(5).normal(0, 0.3, len(clip))
    report = quality.assess(noisy, SR)
    assert report.confidence is Confidence.LOW
    assert "low_snr" in report.reasons


def test_low_asr_confidence_alone_fails_the_gate():
    """The dialect case. Audio can be acoustically clean and still be
    transcribed badly, and that must withhold the model rather than be ignored."""
    clip = np.concatenate([silence(0.4), voiced(3.0), silence(0.3)])
    report = quality.assess(clip, SR, asr_confidences=[0.35], asr_durations=[3.0])
    assert report.confidence is Confidence.LOW
    assert report.reasons == ("low_asr_confidence",)


def test_a_near_silent_call_fails_the_gate():
    report = quality.assess(silence(5.0), SR)
    assert report.confidence is Confidence.LOW
    assert "insufficient_speech_ratio" in report.reasons


def test_clipped_audio_fails_the_gate():
    clip = np.clip(np.concatenate([silence(0.4), voiced(3.0, amp=4.0), silence(0.3)]),
                   -1.0, 1.0)
    assert "clipping" in quality.assess(clip, SR).reasons


def test_asr_confidence_is_weighted_by_duration():
    """Short confident interjections must not mask a long badly-recognised
    passage, which is usually where the disclosure is."""
    unweighted = quality.aggregate_asr_confidence([0.95, 0.95, 0.30])
    weighted = quality.aggregate_asr_confidence([0.95, 0.95, 0.30], [0.3, 0.3, 8.0])
    assert weighted < unweighted
    assert weighted < quality.MIN_ASR_CONFIDENCE


def test_every_failure_reason_has_operator_facing_text():
    clip = np.concatenate([silence(0.4), voiced(0.6), silence(0.4)])
    noisy = clip + _rng(2).normal(0, 0.3, len(clip))
    report = quality.assess(noisy, SR, asr_confidences=[0.2], asr_durations=[0.6])
    assert report.reasons
    assert "Model input withheld" in report.explain()
    assert "human review" in report.explain()
    for reason in report.reasons:
        assert reason in quality._REASON_TEXT


# ------------------------------------------------- prosody

@pytest.fixture(scope="module")
def extractor():
    return ProsodyExtractor()


def test_egemaps_produces_the_full_standard_parameter_set(extractor):
    assert len(extractor.feature_names) == 88


def test_windows_tile_the_call_with_overlap(extractor):
    windows = extractor.extract_windows(voiced(5.0), SR, window_s=2.0, hop_s=0.5)
    assert len(windows) >= 7
    assert windows[1].t_start - windows[0].t_start == pytest.approx(0.5)
    assert windows[0].t_end - windows[0].t_start == pytest.approx(2.0)


def test_very_short_audio_yields_no_windows(extractor):
    assert extractor.extract_windows(voiced(0.2), SR) == []


def test_console_features_are_present_and_finite(extractor):
    window = extractor.extract_windows(voiced(3.0), SR)[0]
    view = window.console_view()
    assert set(view) <= set(CONSOLE_FEATURES)
    assert view and all(np.isfinite(v) for v in view.values())


def test_pitch_tracks_the_actual_fundamental(extractor):
    """Sanity that we are measuring the signal and not the window function."""
    key = "F0semitoneFrom27.5Hz_sma3nz_amean"
    low = extractor.extract_one(voiced(2.0, f0=110), SR)[key]
    high = extractor.extract_one(voiced(2.0, f0=220), SR)[key]
    assert high > low + 8          # an octave is 12 semitones


def test_features_survive_the_telephony_channel(extractor):
    """The features must still be extractable from 8 kHz companded audio,
    because that is the only kind the helpline will ever receive."""
    clip = voiced(3.0)
    degraded = simulate_telephony(clip, SR)
    features = extractor.extract_one(degraded.audio, degraded.sample_rate)
    assert len(features) > 60
    assert all(np.isfinite(v) for v in features.values())


def test_unmeasured_voicing_is_dropped_not_reported_as_zero(extractor):
    """openSMILE reports 0.0, not NaN, for pitch and jitter in a window with no
    voiced frames. Left alone that is indistinguishable from a perfectly steady,
    perfectly calm voice — so silence or a dropped line would read to a
    downstream model as the calmest possible caller. They must be absent."""
    from services.audio.prosody import VOICING_PRESENCE_KEY

    silent = extractor.extract_one(silence(2.0), SR)
    assert silent.get(VOICING_PRESENCE_KEY, 0.0) == 0.0
    assert "F0semitoneFrom27.5Hz_sma3nz_amean" not in silent
    assert "jitterLocal_sma3nz_amean" not in silent
    assert len(silent) < 88

    spoken = extractor.extract_one(voiced(2.0), SR)
    assert "F0semitoneFrom27.5Hz_sma3nz_amean" in spoken
    assert len(spoken) == 88


def test_trajectory_summary_detects_a_rising_pitch_contour(extractor):
    key = "F0semitoneFrom27.5Hz_sma3nz_amean"
    rising = np.concatenate([voiced(1.5, 120), voiced(1.5, 180), voiced(1.5, 240)])
    traj = summarise_trajectory(extractor.extract_windows(rising, SR), key)
    assert traj is not None and traj["slope"] > 0
    assert traj["max"] > traj["min"]


def test_audio_shorter_than_a_window_yields_exactly_one(extractor):
    """And therefore no trajectory. A trailing window that merely re-analyses
    the end of the previous one would double-count that audio in every
    trajectory statistic."""
    windows = extractor.extract_windows(voiced(1.0), SR, window_s=2.0, hop_s=0.5)
    assert len(windows) == 1
    assert summarise_trajectory(windows, "loudness_sma3_amean") is None


def test_windows_never_overlap_completely(extractor):
    windows = extractor.extract_windows(voiced(3.2), SR, window_s=2.0, hop_s=0.5)
    ends = [w.t_end for w in windows]
    assert len(ends) == len(set(ends)), "a window re-analyses material already covered"


# ------------------------------------------------- the fairness claim, end to end

def test_degraded_audio_escalates_the_assessment_rather_than_calming_it():
    """The claim the whole abstention design exists to support, exercised on
    real waveforms: identical case facts, identical model output, the only
    difference is line quality. The poor line must never produce the calmer
    assessment.
    """
    facts = ContextFacts(offence_category="grievous_hurt",
                         present={"prior_threats", "accused_at_large_nearby"},
                         asked=set(CORE_COVERAGE_KEYS))
    screeners = Screeners(phq9=[2] * 9, gad7=[2] * 7, pc_ptsd5=[1, 1, 1, 0, 0],
                          impairment=2, cssrs=CSSRSScreen(administered=True))

    clean_clip = np.concatenate([silence(0.4), voiced(2.5), silence(0.3),
                                 voiced(2.5, 170), silence(0.3)])
    poor_line = simulate_telephony(clean_clip, SR).audio + \
        _rng(9).normal(0, 0.25, len(simulate_telephony(clean_clip, SR).audio))

    clean_q = quality.assess(clean_clip, SR, asr_confidences=[0.92], asr_durations=[5.0])
    poor_q = quality.assess(poor_line, TELEPHONY_RATE,
                            asr_confidences=[0.38], asr_durations=[5.0])

    assert clean_q.confidence is Confidence.OK
    assert poor_q.confidence is Confidence.LOW

    signal = ModelSignals(0.7, 0.85, clean_q.confidence)
    degraded = ModelSignals(0.7, 0.85, poor_q.confidence)

    clean_result = compute_svi(facts, screeners, signal)
    poor_result = compute_svi(facts, screeners, degraded)

    assert tier_rank(poor_result.tier) >= tier_rank(clean_result.tier)
    assert poor_result.abstained
    assert "low_signal_confidence" in poor_result.abstention_reasons
    assert poor_result.channel_c_delta == 0.0
