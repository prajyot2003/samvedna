"""
Channel C — the passive distress signal.

THIS IS A BASELINE, NOT A TRAINED MODEL, AND THAT IS A DELIBERATE POSITION.

There is no labelled corpus of NHAA interactions. There cannot be one until the
shadow-mode pilot has run and counsellor decisions have accumulated as gold
labels. Every alternative available today is worse than a transparent baseline:

  * Training on acted emotion corpora and deploying on real victim calls would
    produce a model whose reported accuracy is real and whose field behaviour
    is unknown, which is the failure this whole project is arranged to avoid.
  * Reporting a number with no evidence behind it is what a demo does.

So this combines a small number of directionally-motivated features with
weights chosen a priori and written down, producing a distress probability that
can be argued with line by line. It is documented as a baseline in the model
card, and `scripts/` has no path that describes it otherwise.

THE STRUCTURAL SAFEGUARD. `BASELINE_CONFIDENCE_CAP` limits the confidence this
component may report to 0.6. Because the SVI engine computes the Channel C
contribution as `25 * distress_probability * model_confidence`, an untrained
component can move a score by at most 15 points rather than 25 — under a fifth
of the scale, and never enough on its own to cross more than one tier boundary.
The cap is lifted only by replacing this with something validated against pilot
labels, and lifting it is a code change with a test that has to be edited,
which is the intended amount of friction.

FEATURE DIRECTIONS, and why each is here rather than a longer list. Every
feature below is one whose direction is defensible from the paralinguistics and
crisis-line literature without needing our own data to establish it. Features
whose direction is genuinely uncertain are excluded rather than included with a
guessed weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence, Tuple

from core.events import Confidence
from core.svi.engine import ModelSignals
from services.audio.vad import ConversationalFeatures
from services.nlp.lexicon import LexiconAnalysis

BASELINE_CONFIDENCE_CAP = 0.6

# Lexical severity contributions. Crisis categories dominate, which is
# intentional: they are also the categories the hard-rules layer acts on, so
# this channel agreeing with a rule costs nothing, while this channel being the
# only thing that noticed still raises the score.
SEVERITY_WEIGHT = {"moderate": 0.20, "high": 0.45, "critical": 0.75}
CATEGORY_BREADTH_BONUS = 0.08          # per additional distinct category
LEXICAL_CEILING = 1.0

# Conversational timing. Directions taken from crisis-line and clinical
# interview practice: distressed callers hesitate longer before answering,
# pause more within an account, and fragment their turns.
TIMING_REFERENCE = {
    "onset_latency": (0.8, 4.0),       # seconds; below the first is unremarkable
    "pause_ratio": (0.20, 0.65),
    "fragmentation": (0.35, 1.20),     # speech segments per second of speech
}

# Prosodic markers, all from eGeMAPS. Reduced pitch variability (flattened
# affect) and reduced loudness variability are the two with the most consistent
# support; voice-quality instability is included at a lower weight.
PROSODY_REFERENCE = {
    "F0semitoneFrom27.5Hz_sma3nz_stddevNorm": (0.35, 0.08, "low_is_distress"),
    "loudness_sma3_stddevNorm": (0.60, 0.20, "low_is_distress"),
    "jitterLocal_sma3nz_amean": (0.010, 0.045, "high_is_distress"),
    "shimmerLocaldB_sma3nz_amean": (0.60, 1.60, "high_is_distress"),
}

CHANNEL_WEIGHTS = {"lexical": 0.50, "timing": 0.30, "prosody": 0.20}


def _ramp(value: float, low: float, high: float) -> float:
    """Linear 0..1 between two reference points, clamped. Works in either
    direction so a feature where low values indicate distress is expressed by
    passing low > high rather than by a separate code path."""
    if low == high:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


@dataclass(frozen=True)
class DistressAssessment:
    distress_probability: float
    model_confidence: float
    components: Dict[str, float]
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    is_baseline: bool = True

    def to_signals(self, signal_confidence: Confidence) -> ModelSignals:
        return ModelSignals(distress_probability=self.distress_probability,
                            model_confidence=self.model_confidence,
                            signal_confidence=signal_confidence)

    def as_dict(self) -> Dict[str, object]:
        return {
            "distress_probability": round(self.distress_probability, 4),
            "model_confidence": round(self.model_confidence, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "evidence": list(self.evidence),
            "is_baseline": self.is_baseline,
        }

    def explain(self) -> str:
        """Counsellor-facing. Names what was observed, never an emotion label —
        the system reports acoustic and linguistic indicators, it does not tell
        a counsellor what a caller feels."""
        if not self.evidence:
            return "No distress indicators detected in speech or language."
        return "Indicators observed: " + "; ".join(self.evidence) + "."


def _lexical(analysis: Optional[LexiconAnalysis]) -> Tuple[float, Tuple[str, ...]]:
    if analysis is None or not analysis.hits:
        return 0.0, ()
    severity = analysis.max_severity or "moderate"
    score = SEVERITY_WEIGHT[severity]
    extra = max(0, len(analysis.categories) - 1)
    score = min(LEXICAL_CEILING, score + CATEGORY_BREADTH_BONUS * extra)
    labels = ", ".join(sorted(analysis.categories))
    return score, (f"distress language ({labels})",)


def _timing(features: Optional[ConversationalFeatures]) -> Tuple[float, Tuple[str, ...]]:
    if features is None:
        return 0.0, ()
    parts, evidence = [], []

    low, high = TIMING_REFERENCE["onset_latency"]
    onset = _ramp(features.onset_latency, low, high)
    parts.append(onset)
    if onset > 0.5:
        evidence.append(f"long pause before answering ({features.onset_latency:.1f}s)")

    low, high = TIMING_REFERENCE["pause_ratio"]
    pauses = _ramp(features.pause_ratio, low, high)
    parts.append(pauses)
    if pauses > 0.5:
        evidence.append(f"frequent pausing ({features.pause_ratio:.0%} of the turn)")

    low, high = TIMING_REFERENCE["fragmentation"]
    fragmented = _ramp(features.fragmentation, low, high)
    parts.append(fragmented)
    if fragmented > 0.5:
        evidence.append("speech broken into short fragments")

    return sum(parts) / len(parts), tuple(evidence)


def _prosody(features: Optional[Mapping[str, float]]) -> Tuple[float, Tuple[str, ...]]:
    if not features:
        return 0.0, ()
    parts, evidence = [], []

    for name, (low, high, direction) in PROSODY_REFERENCE.items():
        if name not in features:
            continue                    # dropped as unmeasured; see D17
        score = _ramp(features[name], low, high)
        parts.append(score)
        if score > 0.6:
            if direction == "low_is_distress":
                evidence.append("reduced variation in "
                                + ("pitch" if "F0" in name else "loudness"))
            else:
                evidence.append("voice instability")

    if not parts:
        return 0.0, ()
    return sum(parts) / len(parts), tuple(dict.fromkeys(evidence))


def assess(lexicon: Optional[LexiconAnalysis] = None,
           timing: Optional[ConversationalFeatures] = None,
           prosody: Optional[Mapping[str, float]] = None) -> DistressAssessment:
    """Combine the available evidence into a Channel C signal.

    Confidence scales with how many independent families of evidence were
    actually present, then is capped. An assessment resting on one channel is
    reported as less certain than one where language, timing and voice agree —
    and none of them is trusted very far while this is a baseline.
    """
    lexical_score, lexical_evidence = _lexical(lexicon)
    timing_score, timing_evidence = _timing(timing)
    prosody_score, prosody_evidence = _prosody(prosody)

    present = {
        "lexical": lexicon is not None and bool(lexicon.hits),
        "timing": timing is not None,
        "prosody": bool(prosody),
    }
    available = [name for name, ok in present.items() if ok]

    if not available:
        return DistressAssessment(0.0, 0.0, {"lexical": 0.0, "timing": 0.0,
                                             "prosody": 0.0}, (), True)

    total_weight = sum(CHANNEL_WEIGHTS[name] for name in available)
    scores = {"lexical": lexical_score, "timing": timing_score,
              "prosody": prosody_score}
    probability = sum(CHANNEL_WEIGHTS[name] * scores[name]
                      for name in available) / total_weight

    # One family of evidence is thin, three corroborate each other.
    coverage = len(available) / len(CHANNEL_WEIGHTS)
    confidence = min(BASELINE_CONFIDENCE_CAP, 0.35 + 0.45 * coverage)

    # An unreviewed lexicon is weaker evidence than a reviewed one, and the
    # language whose lexicon is thinnest is the one whose speakers are worst
    # served — so the discount lands where the uncertainty actually is.
    if lexicon is not None and lexicon.hits and not lexicon.lexicon_reviewed:
        confidence *= 0.85

    return DistressAssessment(
        distress_probability=max(0.0, min(1.0, probability)),
        model_confidence=max(0.0, min(BASELINE_CONFIDENCE_CAP, confidence)),
        components=scores,
        evidence=lexical_evidence + timing_evidence + prosody_evidence,
        is_baseline=True,
    )
