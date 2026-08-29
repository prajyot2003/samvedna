"""
ASR layer tests.

Split by what can be honestly verified where.

Everything that does not need a downloaded acoustic model runs everywhere: the
confidence derivation, the `TranscriptSegment` contract, duration weighting,
router fallback and provenance, and the full Bhashini client exercised over
real HTTP against the local ULCA reference server — real sockets, real
serialisation, real retry and error handling on the client side.

Tests that need Whisper weights are marked `needs_model` and skip when the
model is absent. They are not decorative: they run on a developer machine with
normal internet, and `make test-asr` exists to run exactly them. The
organisation egress policy in this build environment blocks huggingface.co, so
they skip here and must be run locally before any claim about recognition
accuracy is made.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import numpy as np
import pytest

from core.events import Confidence, Language
from services.asr.base import ASRUnavailable, Transcript, TranscriptSegment
from services.asr.bhashini import BhashiniBackend, UNKNOWN_CONFIDENCE
from services.asr.reference_server import REFERENCE_KEY, ULCAReferenceServer
from services.asr.router import ASRRouter
from services.asr.whisper_local import (LANGUAGE_CODES, SUBSTITUTED_LANGUAGES,
                                        WhisperBackend)
from services.audio import quality

SR = 16000


def tone(duration: float = 2.0, f0: float = 150.0, sample_rate: int = SR) -> np.ndarray:
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    sig = sum(0.3 / (k + 1) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(8))
    return (sig * np.hanning(n) ** 0.2).astype(np.float32)


def segment(text="ठीक है", start=0.0, end=1.0, conf=0.8, lang=Language.HINDI):
    return TranscriptSegment(text=text, start=start, end=end,
                             confidence=conf, language=lang)


# ------------------------------------------------- the transcript contract

def test_confidence_outside_the_unit_interval_is_rejected():
    for bad in (-0.1, 1.4):
        with pytest.raises(ValueError):
            segment(conf=bad)


def test_a_segment_cannot_end_before_it_starts():
    with pytest.raises(ValueError):
        segment(start=2.0, end=1.0)


def test_transcript_text_joins_segments_and_skips_blanks():
    t = Transcript(segments=(segment("मुझे", 0, 1), segment("  ", 1, 2),
                             segment("डर लगता है", 2, 3)),
                   language=Language.HINDI, backend="test")
    assert t.text == "मुझे डर लगता है"


def test_weighted_confidence_follows_duration_not_count():
    """Three short confident interjections must not outweigh one long, badly
    recognised passage — which is usually where the disclosure sits."""
    t = Transcript(
        segments=(segment("जी", 0.0, 0.3, 0.95), segment("हाँ", 0.3, 0.6, 0.95),
                  segment("जी", 0.6, 0.9, 0.95),
                  segment("...", 0.9, 12.0, 0.30)),
        language=Language.HINDI, backend="test")
    assert t.weighted_confidence() < 0.45
    assert t.weighted_confidence() < float(np.mean(t.confidences))


def test_empty_transcript_reports_no_confidence_rather_than_zero():
    """None and 0.0 are different claims. Zero would read as 'certainly wrong'
    instead of 'nothing was measured'."""
    t = Transcript(segments=(), language=Language.HINDI, backend="test")
    assert t.weighted_confidence() is None
    assert t.text == ""


# ------------------------------------------------- Whisper confidence proxy

def test_confidence_proxy_is_monotone_in_token_probability():
    b = WhisperBackend()
    values = [b.segment_confidence(lp, 0.0) for lp in (-3.0, -1.5, -0.5, -0.05)]
    assert values == sorted(values)


def test_confidence_proxy_is_suppressed_by_no_speech_probability():
    """Whisper's hallucinated text appears in segments it simultaneously
    believes contain no speech. Those must not arrive as confident."""
    b = WhisperBackend()
    assert b.segment_confidence(-0.1, 0.9) < b.segment_confidence(-0.1, 0.0)
    assert b.segment_confidence(-0.1, 0.95) < quality.MIN_ASR_CONFIDENCE


def test_confidence_proxy_stays_within_the_unit_interval():
    b = WhisperBackend()
    for lp in (-20.0, -1.0, 0.0, 0.5):
        for nsp in (0.0, 0.5, 1.0):
            assert 0.0 <= b.segment_confidence(lp, nsp) <= 1.0


def test_bhojpuri_language_substitution_is_declared_not_silent():
    """Whisper has no Bhojpuri token, so it is decoded as Hindi. That is the
    exact source of the dialect accuracy gap and must be visible."""
    assert LANGUAGE_CODES[Language.BHOJPURI] == "hi"
    assert Language.BHOJPURI in SUBSTITUTED_LANGUAGES
    assert Language.HINDI not in SUBSTITUTED_LANGUAGES


# ------------------------------------------------- Bhashini over real HTTP

@pytest.fixture()
def fast_backoff(monkeypatch):
    """Retry timing is real behaviour and covered by its own test. Paying for
    it in every test that happens to hit a failure path just makes the suite
    slow enough that people stop running it."""
    monkeypatch.setattr("services.asr.bhashini.BACKOFF_SECONDS", 0.01)


@pytest.fixture()
def reference():
    with ULCAReferenceServer() as server:
        yield server


def bhashini_for(server, **kw) -> BhashiniBackend:
    return BhashiniBackend(user_id="test-user", api_key="test-key",
                           pipeline_id="test-pipeline", inference_url="",
                           inference_key="", **kw)


def test_backend_is_unavailable_without_credentials():
    assert not BhashiniBackend(user_id="", api_key="", pipeline_id="",
                               inference_url="", inference_key="").available()


def test_backend_is_available_with_a_pinned_endpoint(reference):
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    assert b.available()


def test_pipeline_resolution_reads_the_ulca_envelope(reference, monkeypatch):
    monkeypatch.setattr("services.asr.bhashini.PIPELINE_CONFIG_URL", reference.config_url)
    resolved = bhashini_for(reference).resolve_pipeline(Language.HINDI)
    assert resolved["url"] == reference.inference_url
    assert resolved["value"] == REFERENCE_KEY
    assert resolved["service_id"] == "local/reference/asr"


def test_pipeline_resolution_is_cached_per_language(reference, monkeypatch):
    monkeypatch.setattr("services.asr.bhashini.PIPELINE_CONFIG_URL", reference.config_url)
    b = bhashini_for(reference)
    first = b.resolve_pipeline(Language.HINDI)
    assert b.resolve_pipeline(Language.HINDI) == first
    assert Language.HINDI.value in b._resolved


def test_missing_credentials_are_rejected_by_the_contract(reference, monkeypatch):
    """A 401 must surface, not be retried into a timeout."""
    monkeypatch.setattr("services.asr.bhashini.PIPELINE_CONFIG_URL", reference.config_url)
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="p",
                        inference_url="", inference_key="")
    b.user_id, b.api_key = "", ""
    with pytest.raises(ASRUnavailable) as exc:
        b.resolve_pipeline(Language.HINDI)
    assert "401" in str(exc.value)


def test_the_request_sent_matches_the_ulca_asr_contract(reference, fast_backoff):
    """Inspect what actually went over the socket, not what we meant to send."""
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    try:
        b.transcribe(tone(), SR, Language.BHOJPURI)
    except ASRUnavailable:
        pass                               # no local ASR service; the request still went

    assert reference.requests, "no request reached the reference server"
    sent = reference.requests[-1]
    task = sent["pipelineTasks"][0]
    assert task["taskType"] == "asr"
    assert task["config"]["language"]["sourceLanguage"] == "bho"
    assert task["config"]["audioFormat"] == "wav"
    assert task["config"]["samplingRate"] == SR
    assert sent["inputData"]["audio"][0]["audioContent"]


def test_bhojpuri_is_sent_natively_to_bhashini(reference, fast_backoff):
    """Unlike Whisper, Bhashini carries Bhojpuri as its own language. This is
    why it is the preferred backend for exactly the callers the fairness
    argument is about."""
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    try:
        b.transcribe(tone(), SR, Language.BHOJPURI)
    except ASRUnavailable:
        pass
    code = reference.requests[-1]["pipelineTasks"][0]["config"]["language"]["sourceLanguage"]
    assert code == "bho"


def test_a_service_outage_raises_rather_than_returning_silence(reference, fast_backoff):
    """An empty transcript is indistinguishable from a silent caller and would
    be scored as one. An outage must raise."""
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    with pytest.raises(ASRUnavailable):
        b.transcribe(tone(), SR, Language.HINDI)


def test_server_errors_are_retried_and_then_surfaced(reference, fast_backoff):
    reference.force_status = 503
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    with pytest.raises(ASRUnavailable) as exc:
        b.transcribe(tone(), SR, Language.HINDI)
    assert "unreachable" in str(exc.value)


def test_client_errors_are_not_retried(reference, fast_backoff):
    reference.force_status = 400
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    with pytest.raises(ASRUnavailable) as exc:
        b.transcribe(tone(), SR, Language.HINDI)
    assert "rejected" in str(exc.value)


def test_a_malformed_envelope_yields_an_empty_transcript_not_a_crash(reference):
    reference.malformed_response = True
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    result = b.transcribe(tone(), SR, Language.HINDI)
    assert result.segments == ()
    assert result.weighted_confidence() is None


def test_absent_confidence_defaults_below_the_quality_threshold():
    """Bhashini's ASR response carries no per-segment confidence. Assuming a
    high value would silently disable abstention for the production backend, so
    the default must sit below the gate."""
    assert UNKNOWN_CONFIDENCE < quality.MIN_ASR_CONFIDENCE


# ------------------------------------------------- routing

class _StubBackend:
    """A backend that is genuinely unavailable or genuinely fails — used to
    drive the router's fallback paths, not to fake a transcription."""

    def __init__(self, name, ok=True, raises=False):
        self.name, self._ok, self._raises = name, ok, raises

    def available(self):
        return self._ok

    def transcribe(self, audio, sample_rate, language):
        if self._raises:
            raise ASRUnavailable(f"{self.name} is down")
        return Transcript(segments=(segment(lang=language),), language=language,
                          backend=self.name)


def test_router_prefers_the_first_available_backend():
    r = ASRRouter(backends=(_StubBackend("bhashini"), _StubBackend("whisper-local")))
    out = r.transcribe(tone(), SR, Language.HINDI)
    assert out.backend_used == "bhashini"
    assert not out.fell_back


def test_router_falls_back_when_the_primary_is_down():
    r = ASRRouter(backends=(_StubBackend("bhashini", raises=True),
                            _StubBackend("whisper-local")))
    out = r.transcribe(tone(), SR, Language.HINDI)
    assert out.backend_used == "whisper-local"
    assert out.fell_back
    assert out.attempts == ("bhashini:failed", "whisper-local:ok")


def test_router_skips_unconfigured_backends_without_calling_them():
    r = ASRRouter(backends=(_StubBackend("bhashini", ok=False),
                            _StubBackend("whisper-local")))
    out = r.transcribe(tone(), SR, Language.HINDI)
    assert out.attempts == ("bhashini:unavailable", "whisper-local:ok")
    assert not out.fell_back


def test_router_raises_when_nothing_can_serve_the_request():
    r = ASRRouter(backends=(_StubBackend("a", ok=False), _StubBackend("b", raises=True)))
    with pytest.raises(ASRUnavailable):
        r.transcribe(tone(), SR, Language.HINDI)


def test_provenance_note_warns_when_a_dialect_was_substituted():
    r = ASRRouter(backends=(WhisperBackend(),))
    out = ASRRouter(backends=(_StubBackend("bhashini"),)).transcribe(
        tone(), SR, Language.BHOJPURI)
    assert not out.language_substituted        # Bhashini carries Bhojpuri natively

    from services.asr.router import RoutedTranscript
    substituted = RoutedTranscript(
        transcript=Transcript((), Language.BHOJPURI, "whisper-local"),
        backend_used="whisper-local", fell_back=True, language_substituted=True)
    note = substituted.provenance_note
    assert "accuracy is reduced for this dialect" in note
    assert "whisper-local" in note


# ------------------------------------------------- needs downloaded weights

needs_model = pytest.mark.skipif(
    not WhisperBackend(model_size="tiny").weights_cached(),
    reason="Whisper weights unavailable (huggingface.co blocked here); "
           "run `make test-asr` on a machine with normal internet")


@needs_model
def test_whisper_transcribes_real_speech_and_reports_usable_confidence():
    """Runs on a developer machine. Uses whatever recordings are in
    data/validation/, so the assertion is about real speech, not a synthetic
    signal that no recogniser should be expected to transcribe."""
    import soundfile as sf
    from pathlib import Path

    clips = sorted(Path("data/validation").glob("*.wav"))
    if not clips:
        pytest.skip("no clips in data/validation/ — see scripts/validate_asr.py")

    backend = WhisperBackend(model_size="tiny")
    audio, sr = sf.read(clips[0], dtype="float32")
    result = backend.transcribe(np.asarray(audio), int(sr), Language.HINDI)
    assert result.segments
    assert all(0.0 <= s.confidence <= 1.0 for s in result.segments)
    assert result.weighted_confidence() is not None


@needs_model
def test_telephony_degradation_lowers_recognition_confidence():
    """The premise behind codec-realistic training, measured rather than
    assumed: the same utterance through the phone channel should not come back
    more confidently recognised than the clean original."""
    import soundfile as sf
    from pathlib import Path
    from services.audio.telephony import simulate_telephony

    clips = sorted(Path("data/validation").glob("*.wav"))
    if not clips:
        pytest.skip("no clips in data/validation/")

    backend = WhisperBackend(model_size="tiny")
    audio, sr = sf.read(clips[0], dtype="float32")
    clean = backend.transcribe(np.asarray(audio), int(sr), Language.HINDI)
    degraded_audio = simulate_telephony(np.asarray(audio), int(sr))
    degraded = backend.transcribe(degraded_audio.audio, degraded_audio.sample_rate,
                                  Language.HINDI)
    assert (degraded.weighted_confidence() or 0) <= (clean.weighted_confidence() or 0) + 0.05


def test_transport_failures_are_retried_exactly_max_attempts_times(reference, monkeypatch):
    """The retry budget is bounded. An unbounded one would hold a live call
    open indefinitely while a dead endpoint is re-dialled."""
    monkeypatch.setattr("services.asr.bhashini.BACKOFF_SECONDS", 0.01)
    calls = {"n": 0}
    real_urlopen = urllib.request.urlopen

    def counting_urlopen(*args, **kwargs):
        calls["n"] += 1
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("services.asr.bhashini.urllib.request.urlopen", counting_urlopen)
    b = BhashiniBackend(user_id="", api_key="", pipeline_id="",
                        inference_url=reference.inference_url,
                        inference_key=REFERENCE_KEY)
    with pytest.raises(ASRUnavailable):
        b.transcribe(tone(), SR, Language.HINDI)

    from services.asr.bhashini import MAX_ATTEMPTS
    assert calls["n"] == MAX_ATTEMPTS


def test_availability_check_does_not_touch_the_network(monkeypatch):
    """available() runs on every routing decision. If it could trigger a model
    download it would stall a live call behind a fetch, and would report a
    backend as usable on the strength of an uplink the office may not have."""
    def explode(*_a, **_k):
        raise AssertionError("available() attempted a network call")

    monkeypatch.setattr("services.asr.whisper_local.WhisperBackend.load", explode)
    backend = WhisperBackend(model_size="definitely-not-a-real-model")
    assert backend.available() is False
