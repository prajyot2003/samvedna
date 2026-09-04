"""
Bhashini backend (MeitY, National Language Translation Mission).

The sovereign path, and the one intended for production. Caller audio from a
government helpline about caste atrocities should be processed on Indian
government language infrastructure, not sent to a foreign commercial API. That
is a procurement and data-protection position as much as a technical one, and
it is worth stating plainly in front of a ministry panel.

INTEGRATION BOUNDARY. Bhashini's inference endpoints require a ULCA user ID and
API key issued to a registered organisation. Those are not obtainable outside
the ministry, so this client cannot be exercised against the live service from
here. What is implemented is the real two-step ULCA flow — resolve a pipeline,
then call the returned inference endpoint — with real request construction,
real timeout and retry behaviour, and real response parsing.

The response field names below follow the ULCA pipeline schema. Confirm them
against the current Bhashini documentation when credentials are issued: this
client is written to the published contract, but a contract read from
documentation is not the same as one exercised against a live endpoint, and
saying otherwise would be exactly the kind of claim this project refuses to
make. `SAMVEDNA_BHASHINI_INFERENCE_URL` allows pinning a resolved endpoint
directly, which is also how the local reference server is targeted in
integration testing.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from core.events import Language
from services.asr.base import ASRUnavailable, Transcript, TranscriptSegment

PIPELINE_CONFIG_URL = os.environ.get(
    "SAMVEDNA_BHASHINI_CONFIG_URL",
    "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline")
INFERENCE_URL = os.environ.get("SAMVEDNA_BHASHINI_INFERENCE_URL", "")
USER_ID = os.environ.get("SAMVEDNA_BHASHINI_USER_ID", "")
API_KEY = os.environ.get("SAMVEDNA_BHASHINI_API_KEY", "")
INFERENCE_KEY = os.environ.get("SAMVEDNA_BHASHINI_INFERENCE_KEY", "")
PIPELINE_ID = os.environ.get("SAMVEDNA_BHASHINI_PIPELINE_ID", "")

TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.5
TARGET_RATE = 16000

# Bhashini carries Bhojpuri as its own language, which Whisper does not. When
# credentials exist this is the better backend for exactly the callers the
# fairness argument is about.
LANGUAGE_CODES: Dict[Language, str] = {
    language: language.profile.bhashini_code
    for language in Language
    if language.profile.bhashini_code is not None
}

# Codes read from the ULCA catalogue but never exercised against a live
# pipeline. They are attempted; an unavailable model surfaces as a normal
# backend failure and the router falls through. Confirming each is a task in
# the pilot protocol — a catalogue entry is not a capability.
UNVERIFIED_LANGUAGES = {
    language for language in Language
    if language.profile.bhashini_code and not language.profile.bhashini_verified
}


def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf
    buffer = io.BytesIO()
    sf.write(buffer, np.asarray(audio, dtype=np.float32), sample_rate, format="WAV",
             subtype="PCM_16")
    return buffer.getvalue()


@dataclass
class BhashiniBackend:
    user_id: str = USER_ID
    api_key: str = API_KEY
    inference_key: str = INFERENCE_KEY
    inference_url: str = INFERENCE_URL
    pipeline_id: str = PIPELINE_ID
    name: str = "bhashini"
    _resolved: Dict[str, str] = field(default_factory=dict)

    def available(self) -> bool:
        """Credentials configured, or an endpoint pinned directly."""
        return bool(self.inference_url and self.inference_key) or bool(
            self.user_id and self.api_key and self.pipeline_id)

    # -- transport ------------------------------------------------------

    def _post(self, url: str, payload: dict, headers: Dict[str, str]) -> dict:
        """POST with bounded retries on transport and 5xx failures.

        A 4xx is not retried: a rejected request will be rejected again, and
        retrying it only delays surfacing a credential or schema problem.
        """
        body = json.dumps(payload).encode("utf-8")
        last: Optional[Exception] = None

        for attempt in range(MAX_ATTEMPTS):
            request = urllib.request.Request(url, data=body, method="POST",
                                             headers={"Content-Type": "application/json",
                                                      **headers})
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code < 500:
                    raise ASRUnavailable(
                        f"Bhashini rejected the request ({exc.code}): {detail}") from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS * (2 ** attempt))

        raise ASRUnavailable(f"Bhashini unreachable after {MAX_ATTEMPTS} attempts: {last}")

    def resolve_pipeline(self, language: Language) -> Dict[str, str]:
        """Step one of the ULCA flow: exchange a pipeline id for a concrete
        inference endpoint, its authorisation header, and a service id."""
        if self.inference_url and self.inference_key:
            return {"url": self.inference_url, "header": "Authorization",
                    "value": self.inference_key, "service_id": ""}

        if language not in LANGUAGE_CODES:
            raise ASRUnavailable(
                f"Bhashini has no configured pipeline for "
                f"{language.profile.english_name}. Declared gap, see "
                f"core/languages.py.")

        cached = self._resolved.get(language.value)
        if cached:
            return json.loads(cached)

        payload = {
            "pipelineTasks": [{
                "taskType": "asr",
                "config": {"language": {"sourceLanguage": LANGUAGE_CODES[language]}},
            }],
            "pipelineRequestConfig": {"pipelineId": self.pipeline_id},
        }
        data = self._post(PIPELINE_CONFIG_URL, payload,
                          {"userID": self.user_id, "ulcaApiKey": self.api_key})

        endpoint = data.get("pipelineInferenceAPIEndPoint", {})
        scheme = endpoint.get("inferenceApiKey", {})
        tasks = data.get("pipelineResponseConfig", [])
        service_id = ""
        if tasks and tasks[0].get("config"):
            service_id = tasks[0]["config"][0].get("serviceId", "")

        resolved = {
            "url": endpoint.get("callbackUrl", ""),
            "header": scheme.get("name", "Authorization"),
            "value": scheme.get("value", ""),
            "service_id": service_id,
        }
        if not resolved["url"] or not resolved["value"]:
            raise ASRUnavailable(
                "Bhashini pipeline resolution returned no usable inference endpoint")
        self._resolved[language.value] = json.dumps(resolved)
        return resolved

    # -- inference ------------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   language: Language) -> Transcript:
        if not self.available():
            raise ASRUnavailable("Bhashini credentials are not configured")

        pipeline = self.resolve_pipeline(language)
        wav = _to_wav_bytes(audio, sample_rate)

        payload = {
            "pipelineTasks": [{
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": LANGUAGE_CODES[language]},
                    "serviceId": pipeline["service_id"],
                    "audioFormat": "wav",
                    "samplingRate": sample_rate,
                },
            }],
            "inputData": {
                "audio": [{"audioContent": base64.b64encode(wav).decode("ascii")}]
            },
        }
        data = self._post(pipeline["url"], payload,
                          {pipeline["header"]: pipeline["value"]})
        return self._parse(data, language, len(audio) / sample_rate)

    def _parse(self, data: dict, language: Language, duration: float) -> Transcript:
        """Bhashini's ASR response carries no per-segment confidence.

        That is a genuine gap, and it is handled by declaring it rather than
        inventing a number: `UNKNOWN_CONFIDENCE` sits just below the quality
        gate's threshold, so a transcript with no confidence information is
        treated as unreliable and the model contribution is withheld. Making
        the safe assumption explicit is the only defensible option — assuming
        high confidence would silently disable the abstention path for exactly
        the production backend.
        """
        outputs: List[dict] = []
        for task in data.get("pipelineResponse", []):
            if task.get("taskType") == "asr":
                outputs.extend(task.get("output", []))

        segments = []
        for item in outputs:
            text = (item.get("source") or "").strip()
            if not text:
                continue
            segments.append(TranscriptSegment(
                text=text, start=0.0, end=duration,
                confidence=float(item.get("confidence", UNKNOWN_CONFIDENCE)),
                language=language))

        return Transcript(segments=tuple(segments), language=language,
                          backend=self.name, model=self.pipeline_id or "pinned-endpoint")


# Deliberately below services.audio.quality.MIN_ASR_CONFIDENCE.
UNKNOWN_CONFIDENCE = 0.5
