"""
The SVI engine.

A pure function. No I/O, no network, no models, no global state, no clock.
Given the same three channels it returns the same result, forever. This is the
part of the system that has to survive being read aloud to a sceptical panel,
so it is written to be read.

    SVI = clamp( 100 * (0.55*A + 0.45*B) + C_delta , floor = base )

Three invariants, each enforced here and covered by tests:

  1. FAIL-SAFE MONOTONICITY
     Channel C is bounded to [0, MAX_C_DELTA] and the result is floored at the
     A+B base. The learned component can raise a score; it can never lower one.

  2. ASYMMETRIC ABSTENTION
     Low signal confidence zeroes Channel C. Thin coverage in A or B escalates
     the tier by one. Uncertainty always moves toward more attention, never
     less — because the cases where our ASR is worst are systematically the
     cases involving the most marginalised callers.

  3. RULES OVERRIDE MODELS
     Not implemented here. The hard-rules layer in core.rules runs AFTER this
     function and can force CRITICAL regardless of anything computed here.
     Keeping it in a separate module is the point: it is auditable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.events import Confidence, Tier, escalate_one_tier
from core.svi.factors import ContextFacts
from core.svi.instruments import Screeners

# Channel weights in the deterministic base.
W_CHANNEL_A = 0.55
W_CHANNEL_B = 0.45

# Channel C is capped at a quarter of the scale. It can escalate a case by at
# most one tier on its own — enough to be useful, never enough to dominate.
MAX_C_DELTA = 25.0

# Tier boundaries on the 0..100 scale.
TIER_FLOOR: Dict[Tier, float] = {
    Tier.LOW: 0.0,
    Tier.MODERATE: 25.0,
    Tier.HIGH: 50.0,
    Tier.CRITICAL: 75.0,
}

# Coverage below which an assessment is too thin to stand on its own.
MIN_COVERAGE_A = 0.60
MIN_COVERAGE_B = 0.50


def tier_of(score: float) -> Tier:
    if score >= TIER_FLOOR[Tier.CRITICAL]:
        return Tier.CRITICAL
    if score >= TIER_FLOOR[Tier.HIGH]:
        return Tier.HIGH
    if score >= TIER_FLOOR[Tier.MODERATE]:
        return Tier.MODERATE
    return Tier.LOW


@dataclass(frozen=True)
class ModelSignals:
    """Channel C. `signal_confidence` is set by the acoustic quality gate and
    the ASR confidence aggregator, not by the model itself — a model is not a
    reliable judge of whether it was given usable input."""
    distress_probability: float = 0.0      # 0..1
    model_confidence: float = 0.0          # 0..1
    signal_confidence: Confidence = Confidence.OK

    def __post_init__(self) -> None:
        for name in ("distress_probability", "model_confidence"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be within 0..1, got {v}")


@dataclass(frozen=True)
class SVIResult:
    score: float
    tier: Tier
    channel_a: float
    channel_b: float
    channel_c_delta: float
    base: float
    abstained: bool
    abstention_reasons: tuple
    coverage_a: float
    coverage_b: float
    coarse_domains: tuple
    contributions: Dict[str, float] = field(default_factory=dict)

    def explain(self, n: int = 6):
        """Top contributors in points on the 0..100 scale, for the console."""
        return sorted(self.contributions.items(), key=lambda kv: -kv[1])[:n]


def compute_svi(facts: ContextFacts,
                screeners: Screeners,
                signals: Optional[ModelSignals] = None) -> SVIResult:
    signals = signals or ModelSignals()

    # --- deterministic base ------------------------------------------------
    a = facts.score()
    b = screeners.score()
    base = 100.0 * (W_CHANNEL_A * a + W_CHANNEL_B * b)

    # --- Channel C, gated and bounded --------------------------------------
    gate = 0.0 if signals.signal_confidence is Confidence.LOW else 1.0
    delta = MAX_C_DELTA * signals.distress_probability * signals.model_confidence * gate
    delta = max(0.0, min(MAX_C_DELTA, delta))

    score = min(100.0, base + delta)
    score = max(score, base)          # invariant 1, stated explicitly
    tier = tier_of(score)

    # --- abstention --------------------------------------------------------
    cov_a, cov_b = facts.coverage(), screeners.coverage()
    reasons = []
    if cov_a < MIN_COVERAGE_A:
        reasons.append("insufficient_context_coverage")
    if cov_b < MIN_COVERAGE_B:
        reasons.append("insufficient_screening_coverage")
    if signals.signal_confidence is Confidence.LOW:
        reasons.append("low_signal_confidence")

    abstained = bool(reasons)
    if abstained:
        tier = escalate_one_tier(tier)
        score = max(score, TIER_FLOOR[tier])

    # --- explainability, in points on the 0..100 scale ---------------------
    contributions: Dict[str, float] = {}
    for k, v in facts.contributions().items():
        contributions[k] = 100.0 * W_CHANNEL_A * v
    for k, v in screeners.contributions().items():
        contributions[k] = 100.0 * W_CHANNEL_B * v
    if delta > 0:
        contributions["model:distress_signal"] = delta

    return SVIResult(
        score=round(score, 2),
        tier=tier,
        channel_a=round(a, 4),
        channel_b=round(b, 4),
        channel_c_delta=round(delta, 2),
        base=round(base, 2),
        abstained=abstained,
        abstention_reasons=tuple(reasons),
        coverage_a=round(cov_a, 3),
        coverage_b=round(cov_b, 3),
        coarse_domains=tuple(screeners.coarse_domains()),
        contributions={k: round(v, 3) for k, v in contributions.items()},
    )
