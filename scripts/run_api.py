#!/usr/bin/env python3
"""
Start the SAMVEDNA service.

    python3 scripts/run_api.py            # SQLite, in-process bus, no services
    SAMVEDNA_DATABASE_URL=postgresql+psycopg://... python3 scripts/run_api.py

Prints the readiness verdict at startup. It is expected to be NOT READY until
the crisis lexicons are reviewed, and printing that on every boot is
deliberate: a deployment blocker visible only in a document nobody opens is not
a blocker.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn                                          # noqa: E402

from services.asr.router import ASRRouter               # noqa: E402
from services.audio.prosody import ProsodyExtractor     # noqa: E402
from services.api.app import create_app                 # noqa: E402
from services.nlp.lexicon import production_ready       # noqa: E402


def main() -> int:
    ready, blockers = production_ready()
    print("SAMVEDNA — AI-assisted structured triage for NHAA 14566")
    print("Screening and decision support. Not a diagnostic service.\n")
    print(f"production ready: {ready}")
    for blocker in blockers:
        print(f"  BLOCKER  {blocker}")
    print()

    try:
        prosody = ProsodyExtractor()
        print("prosody: openSMILE eGeMAPSv02 loaded")
    except Exception as exc:                             # noqa: BLE001
        prosody = None
        print(f"prosody: unavailable ({exc})")

    router = ASRRouter()
    available = [b.name for b in router.backends if b.available()]
    if available:
        print(f"asr: {', '.join(available)}")
    else:
        print("asr: no backend configured — run scripts/fetch_models.py "
              "or set Bhashini credentials")
    print()

    app = create_app(asr_router=router if available else None,
                     prosody_extractor=prosody)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
