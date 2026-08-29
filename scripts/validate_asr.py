#!/usr/bin/env python3
"""
Validate recognition against real recorded speech.

Synthetic signals prove the DSP; they cannot tell you whether the recogniser
understands a Bhojpuri speaker from Gaya. Only recordings of real people can,
and this is the script that measures it.

WHAT TO RECORD. Ask team members and, where they consent, community
volunteers to read a short fixed passage plus two or three free-form
sentences, in Hindi and in Bhojpuri, on a phone rather than a good microphone.
Name each file `<language>_<speaker>_<n>.wav` — for example
`bho_speaker03_01.wav`. Nothing here is stored with a name attached, and the
recordings must never leave the machine.

WHAT IT REPORTS. For every clip: the transcript, the duration-weighted
confidence, the quality gate's verdict, and whether the language was
substituted. Then per-language aggregates. The gap between the Hindi and
Bhojpuri columns is the dialect accuracy gap, measured on your own data, and it
is the number to put in the fairness report rather than a claim.

Optionally re-runs every clip through the telephony channel with `--telephony`,
which is the condition the helpline actually receives.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                    # noqa: E402

from core.events import Language                      # noqa: E402
from services.asr.router import ASRRouter             # noqa: E402
from services.asr.base import ASRUnavailable          # noqa: E402
from services.audio import quality                    # noqa: E402
from services.audio.telephony import simulate_telephony  # noqa: E402

LANGUAGE_BY_PREFIX = {"hi": Language.HINDI, "bho": Language.BHOJPURI}


def language_of(path: Path) -> Language:
    prefix = path.stem.split("_", 1)[0].lower()
    if prefix not in LANGUAGE_BY_PREFIX:
        raise SystemExit(
            f"{path.name}: cannot tell the language from the filename. "
            f"Name clips <language>_<speaker>_<n>.wav, language one of "
            f"{sorted(LANGUAGE_BY_PREFIX)}.")
    return LANGUAGE_BY_PREFIX[prefix]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="data/validation")
    parser.add_argument("--telephony", action="store_true",
                        help="also run every clip through the 8 kHz G.711 channel")
    args = parser.parse_args()

    import soundfile as sf

    clips = sorted(Path(args.dir).glob("*.wav"))
    if not clips:
        print(f"No .wav files in {args.dir}/. See this script's docstring for what "
              f"to record.")
        return 1

    router = ASRRouter()
    by_language = defaultdict(list)
    gate_failures = defaultdict(int)

    for clip in clips:
        language = language_of(clip)
        audio, sample_rate = sf.read(clip, dtype="float32")
        audio = np.asarray(audio)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        if args.telephony:
            degraded = simulate_telephony(audio, int(sample_rate))
            audio, sample_rate = degraded.audio, degraded.sample_rate

        try:
            routed = router.transcribe(audio, int(sample_rate), language)
        except ASRUnavailable as exc:
            print(f"{clip.name}: {exc}")
            print("Run scripts/fetch_models.py first, or configure Bhashini credentials.")
            return 1

        transcript = routed.transcript
        confidence = transcript.weighted_confidence() or 0.0
        report = quality.assess(audio, int(sample_rate),
                                asr_confidences=transcript.confidences,
                                asr_durations=transcript.durations)

        by_language[language.value].append(confidence)
        if not report.usable:
            gate_failures[language.value] += 1

        print(f"\n{clip.name}  [{language.value}]  {len(audio)/sample_rate:.1f}s")
        print(f"  {transcript.text or '(nothing recognised)'}")
        print(f"  confidence {confidence:.3f}   gate {report.confidence.value}")
        if not report.usable:
            print(f"  {report.explain()}")
        if routed.language_substituted:
            print(f"  {routed.provenance_note}")

    print("\n" + "=" * 68)
    print(f"{'language':<10}{'clips':>7}{'mean conf':>12}{'median':>10}"
          f"{'min':>8}{'gate withheld':>16}")
    for code, values in sorted(by_language.items()):
        print(f"{code:<10}{len(values):>7}{statistics.mean(values):>12.3f}"
              f"{statistics.median(values):>10.3f}{min(values):>8.3f}"
              f"{gate_failures[code]:>16}")

    if len(by_language) > 1:
        means = {k: statistics.mean(v) for k, v in by_language.items()}
        best, worst = max(means, key=means.get), min(means, key=means.get)
        print(f"\nDialect confidence gap: {means[best] - means[worst]:.3f} "
              f"({best} over {worst}).")
        print("This is the number the fairness report should carry. Where it is "
              "large,\nthe abstention path is what protects the worse-served "
              "speakers — verify it\nfires by checking the 'gate withheld' column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
