"""
Local ULCA reference server.

Bhashini's live endpoints need credentials issued to a registered government
organisation. Rather than pretend that gap away, this implements the same
contract locally: the two-step pipeline resolution, the same request shape, the
same response envelope. `BhashiniBackend` talks to it over real HTTP with its
real transport, retry and parsing code — nothing is stubbed out on the client
side, which is the entire point.

It is used for three things: integration-testing the client, letting the team
develop against the contract before credentials arrive, and reproducing
failure behaviour (5xx, timeout, malformed envelope) that a live service will
not produce on demand.

Transcription is delegated to whichever local ASR backend is configured, so
what comes back is a real recognition result, not a canned string. When no
backend is available the server answers with a service error — the same thing
the real endpoint does when its model pool is down, and a case the client must
handle correctly.
"""

from __future__ import annotations

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple

import numpy as np

from core.events import Language

REFERENCE_KEY = "local-reference-key"
_CODE_TO_LANGUAGE = {"hi": Language.HINDI, "bho": Language.BHOJPURI}


class _Handler(BaseHTTPRequestHandler):
    server_version = "SamvednaULCAReference/1.0"

    def log_message(self, *_args):        # keep test output readable
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:                                  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"message": "malformed JSON"})

        server: "ULCAReferenceServer" = self.server.controller     # type: ignore[attr-defined]

        if server.force_status:
            return self._json(server.force_status, {"message": "forced failure"})

        if self.path.rstrip("/").endswith("getModelsPipeline"):
            return self._handle_pipeline(server)
        return self._handle_inference(server, request)

    def _handle_pipeline(self, server: "ULCAReferenceServer") -> None:
        if not self.headers.get("userID") or not self.headers.get("ulcaApiKey"):
            return self._json(401, {"message": "missing ULCA credentials"})
        self._json(200, {
            "pipelineResponseConfig": [
                {"taskType": "asr", "config": [{"serviceId": "local/reference/asr"}]}
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": f"{server.base_url}/inference",
                "inferenceApiKey": {"name": "Authorization", "value": REFERENCE_KEY},
            },
        })

    def _handle_inference(self, server: "ULCAReferenceServer", request: dict) -> None:
        if self.headers.get("Authorization") != REFERENCE_KEY:
            return self._json(401, {"message": "invalid inference key"})

        server.requests.append(request)

        try:
            task = request["pipelineTasks"][0]
            code = task["config"]["language"]["sourceLanguage"]
            encoded = request["inputData"]["audio"][0]["audioContent"]
        except (KeyError, IndexError, TypeError):
            return self._json(400, {"message": "malformed ASR request"})

        if server.malformed_response:
            return self._json(200, {"unexpected": "envelope"})

        audio, sample_rate = _decode_wav(base64.b64decode(encoded))
        language = _CODE_TO_LANGUAGE.get(code, Language.HINDI)

        if server.backend is None or not server.backend.available():
            return self._json(503, {"message": "no ASR service available"})

        transcript = server.backend.transcribe(audio, sample_rate, language)
        output = [{"source": transcript.text}]
        if transcript.segments:
            output[0]["confidence"] = round(transcript.weighted_confidence() or 0.0, 4)

        self._json(200, {"pipelineResponse": [{"taskType": "asr", "output": output}]})


def _decode_wav(data: bytes) -> Tuple[np.ndarray, int]:
    import soundfile as sf
    audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
    return np.asarray(audio), int(sample_rate)


class ULCAReferenceServer:
    """Context manager around a real HTTP server on a real socket."""

    def __init__(self, backend=None, port: int = 0):
        self.backend = backend
        self.requests: list = []
        self.force_status: Optional[int] = None
        self.malformed_response = False
        self._http = HTTPServer(("127.0.0.1", port), _Handler)
        self._http.controller = self                     # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._http.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def config_url(self) -> str:
        return f"{self.base_url}/ulca/apis/v0/model/getModelsPipeline"

    @property
    def inference_url(self) -> str:
        return f"{self.base_url}/inference"

    def __enter__(self) -> "ULCAReferenceServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join(timeout=5)


if __name__ == "__main__":                                       # pragma: no cover
    from services.asr.whisper_local import WhisperBackend

    backend = WhisperBackend()
    with ULCAReferenceServer(backend=backend if backend.available() else None,
                             port=8088) as server:
        print(f"ULCA reference server on {server.base_url}")
        print(f"  config    {server.config_url}")
        print(f"  inference {server.inference_url}")
        print(f"  key       {REFERENCE_KEY}")
        print("Ctrl-C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
