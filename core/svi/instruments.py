"""
Channel B — clinical micro-screen.

Items adapted from instruments validated for brief and telephonic screening.
They are used as SCREENERS, never as diagnostic tests, and no output of this
module is a diagnosis.

  PC-PTSD-5   post-traumatic stress          5 items, positive at >= 3
  PHQ-2 -> 9  depression, escalating          2 items, escalate at >= 3
  GAD-2 -> 7  anxiety, escalating             2 items, escalate at >= 3
  C-SSRS      suicidal ideation & behaviour   6 items
  Impairment  functional impairment           1 item, 0..4

TWO DESIGN DECISIONS THAT MATTER:

1. C-SSRS DOES NOT ENTER THE CONTINUOUS SCORE. It is an input to the hard-rules
   layer only. Averaging suicidality into a composite would let it be diluted by
   low scores elsewhere — a caller with active intent but otherwise flat affect
   could land in Moderate. Suicide risk is handled categorically, by rule, and
   never by arithmetic.

2. NON-ADMINISTERED INSTRUMENTS ARE NOT IMPUTED. If GAD was never asked, its
   weight is redistributed across what WAS asked and the interaction's coverage
   drops, which feeds the abstention path. We never guess a score we did not
   collect, and a thin screen never reads as a reassuring one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Maximum raw totals
PHQ2_MAX, PHQ9_MAX = 6, 27
GAD2_MAX, GAD7_MAX = 6, 21
PC_PTSD5_MAX = 5
IMPAIRMENT_MAX = 4

# Escalation thresholds (standard published cut-points)
PHQ2_ESCALATE = 3
GAD2_ESCALATE = 3
PC_PTSD5_POSITIVE = 3

# Relative weights within Channel B. Renormalised over administered instruments.
WEIGHTS = {
    "depression":  0.30,
    "anxiety":     0.25,
    "ptsd":        0.30,
    "impairment":  0.15,
}


@dataclass
class CSSRSScreen:
    """Columbia Suicide Severity Rating Scale — screener version.

    q1  wish to be dead
    q2  non-specific active suicidal thoughts
    q3  active ideation with any method (no plan, no intent)
    q4  active ideation with some intent to act
    q5  active ideation with a specific plan and intent
    q6  suicidal behaviour (lifetime / past 3 months)

    Administered UNCONDITIONALLY in every interaction. It is never gated on a
    model's judgement that the caller appears well.
    """
    administered: bool = False
    q1: bool = False
    q2: bool = False
    q3: bool = False
    q4: bool = False
    q5: bool = False
    q6: bool = False

    @property
    def high_risk(self) -> bool:
        """q4/q5 (intent, or plan with intent) or recent behaviour. Forces
        CRITICAL through the hard-rules layer with no model consulted."""
        return self.q4 or self.q5 or self.q6

    @property
    def any_ideation(self) -> bool:
        return self.q1 or self.q2 or self.q3 or self.high_risk


@dataclass
class Screeners:
    """Administered Channel B state for one interaction."""
    phq2: Optional[List[int]] = None          # 2 items, each 0..3
    phq9: Optional[List[int]] = None          # 9 items, each 0..3
    gad2: Optional[List[int]] = None          # 2 items, each 0..3
    gad7: Optional[List[int]] = None          # 7 items, each 0..3
    pc_ptsd5: Optional[List[int]] = None      # 5 items, each 0 or 1
    impairment: Optional[int] = None          # single item 0..4
    cssrs: CSSRSScreen = field(default_factory=CSSRSScreen)

    def __post_init__(self) -> None:
        self._check("phq2", self.phq2, 2, 3)
        self._check("phq9", self.phq9, 9, 3)
        self._check("gad2", self.gad2, 2, 3)
        self._check("gad7", self.gad7, 7, 3)
        self._check("pc_ptsd5", self.pc_ptsd5, 5, 1)
        if self.impairment is not None and not 0 <= self.impairment <= IMPAIRMENT_MAX:
            raise ValueError("impairment out of range 0..4")

    @staticmethod
    def _check(name: str, items: Optional[List[int]], n: int, hi: int) -> None:
        if items is None:
            return
        if len(items) != n:
            raise ValueError(f"{name} expects {n} items, got {len(items)}")
        if any(not isinstance(v, int) or v < 0 or v > hi for v in items):
            raise ValueError(f"{name} items must be integers in 0..{hi}")

    # -- raw totals -------------------------------------------------------

    @property
    def phq2_total(self) -> Optional[int]:
        return sum(self.phq2) if self.phq2 else None

    @property
    def phq9_total(self) -> Optional[int]:
        return sum(self.phq9) if self.phq9 else None

    @property
    def gad2_total(self) -> Optional[int]:
        return sum(self.gad2) if self.gad2 else None

    @property
    def gad7_total(self) -> Optional[int]:
        return sum(self.gad7) if self.gad7 else None

    @property
    def pc_ptsd5_total(self) -> Optional[int]:
        return sum(self.pc_ptsd5) if self.pc_ptsd5 else None

    # -- escalation logic -------------------------------------------------

    def should_escalate_phq(self) -> bool:
        t = self.phq2_total
        return t is not None and t >= PHQ2_ESCALATE and self.phq9 is None

    def should_escalate_gad(self) -> bool:
        t = self.gad2_total
        return t is not None and t >= GAD2_ESCALATE and self.gad7 is None

    @property
    def phq9_item9_positive(self) -> bool:
        """PHQ-9 item 9 asks about thoughts of self-harm. A positive here is a
        safety signal in its own right, independent of the C-SSRS."""
        return bool(self.phq9) and self.phq9[8] > 0

    # -- normalised subscores (0..1), None if not administered -------------

    def depression(self) -> Optional[float]:
        if self.phq9 is not None:
            return self.phq9_total / PHQ9_MAX
        if self.phq2 is not None:
            return self.phq2_total / PHQ2_MAX      # coarse, flagged below
        return None

    def anxiety(self) -> Optional[float]:
        if self.gad7 is not None:
            return self.gad7_total / GAD7_MAX
        if self.gad2 is not None:
            return self.gad2_total / GAD2_MAX
        return None

    def ptsd(self) -> Optional[float]:
        if self.pc_ptsd5 is None:
            return None
        return self.pc_ptsd5_total / PC_PTSD5_MAX

    def impairment_score(self) -> Optional[float]:
        if self.impairment is None:
            return None
        return self.impairment / IMPAIRMENT_MAX

    def coarse_domains(self) -> List[str]:
        """Domains scored from a 2-item stem rather than the full instrument.
        Surfaced to the counsellor so a coarse screen is never mistaken for a
        complete one."""
        out = []
        if self.phq9 is None and self.phq2 is not None:
            out.append("depression")
        if self.gad7 is None and self.gad2 is not None:
            out.append("anxiety")
        return out

    # -- Channel B --------------------------------------------------------

    def score(self) -> float:
        """Channel B on 0..1, renormalised over administered instruments.
        Returns 0.0 if nothing was administered — coverage, not the score,
        is what signals that absence."""
        parts = {
            "depression": self.depression(),
            "anxiety":    self.anxiety(),
            "ptsd":       self.ptsd(),
            "impairment": self.impairment_score(),
        }
        available = {k: v for k, v in parts.items() if v is not None}
        if not available:
            return 0.0
        total_w = sum(WEIGHTS[k] for k in available)
        return sum(WEIGHTS[k] * v for k, v in available.items()) / total_w

    def coverage(self) -> float:
        """Fraction of the four scored domains actually administered. The
        C-SSRS is excluded here because it is mandatory and categorical, and
        its absence is an error condition rather than reduced coverage."""
        parts = [self.depression(), self.anxiety(), self.ptsd(), self.impairment_score()]
        return sum(1 for p in parts if p is not None) / len(parts)

    def contributions(self) -> Dict[str, float]:
        parts = {
            "depression": self.depression(),
            "anxiety":    self.anxiety(),
            "ptsd":       self.ptsd(),
            "impairment": self.impairment_score(),
        }
        available = {k: v for k, v in parts.items() if v is not None}
        if not available:
            return {}
        total_w = sum(WEIGHTS[k] for k in available)
        return {f"screen:{k}": WEIGHTS[k] * v / total_w for k, v in available.items()}
