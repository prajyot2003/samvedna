#!/usr/bin/env python3
"""
Fetch the acoustic models this system needs, deliberately and up front.

Nothing downloads a model during a call. A district office may have no usable
uplink, and a triage system that stalls a live interaction behind a model fetch
is worse than one that refuses to start. Weights are pulled here, once, and
`WhisperBackend.available()` then answers from disk.

Run before first use, and before `make test-asr`:

    python3 scripts/fetch_models.py --model small

Sizes are approximate download sizes for the CTranslate2 int8 builds:

    tiny    ~ 75 MB    only for smoke-testing the pipeline
    base    ~145 MB
    small   ~490 MB    the recommended default for Hindi
    medium  ~1.5 GB    better, if the demo machine can carry it

On an Apple Silicon laptop, `small` with int8 compute runs comfortably faster
than real time on CPU, which is the constraint that decided the default
(DECISIONS.md D2).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.asr.whisper_local import DEFAULT_COMPUTE, DEFAULT_DEVICE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="small",
                        choices=["tiny", "base", "small", "medium", "large-v3"])
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--compute", default=DEFAULT_COMPUTE)
    args = parser.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed. Run: pip install -r requirements.txt")
        return 1

    print(f"Fetching Whisper '{args.model}' ({args.compute} on {args.device}).")
    print("This downloads once and is cached under ~/.cache/huggingface/hub.\n")
    try:
        WhisperModel(args.model, device=args.device, compute_type=args.compute)
    except Exception as exc:
        print(f"Download failed: {exc}\n")
        print("If this is a network or proxy error, the model can be fetched on any")
        print("machine with normal internet and the cache directory copied across.")
        return 1

    from services.asr.whisper_local import WhisperBackend
    cached = WhisperBackend(model_size=args.model).weights_cached()
    print(f"\nDone. weights_cached() reports: {cached}")
    print("Next: record a few clips into data/validation/, then")
    print("      python3 scripts/validate_asr.py")
    return 0 if cached else 1


if __name__ == "__main__":
    sys.exit(main())
