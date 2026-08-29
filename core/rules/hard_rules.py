"""
The safety layer.

Deterministic rules that run AFTER the SVI engine and can raise the tier. They
can never lower it. No machine-learned value is consulted by any rule in this
file — that is the entire reason the file exists separately from the engine.

The governing case: a caller reports active suicidal intent while otherwise
presenting flatly. Any composite score would dilute that. A rule does not.

Every rule carries a `basis` string naming its clinical or statutory grounding,
which is written into the audit ledger whenever the rule fires, so that a
reviewer months later can see not just that the system escalated but on what
authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Set, Tuple

from core.events import Tier, tier_rank
from core.svi.factors import ContextFacts
from core.svi.instruments import Screeners

SEXUAL_OFFENCES = {"rape", "gang_rape", "sexual_assault"}


@dataclass
class TriageState:
    """Everything the safety layer is allowed to look at."""
    facts: ContextFacts
    screeners: Screeners
    lexicon_hits: Set[str] = field(default_factory=set)
    # Keys such as "self_harm", "hopelessness", "imminent_violence" produced by
    # the per-language crisis lexicons. Hand-curated with native speakers and
    # never machine-translated: a mistranslated suicide idiom is a missed case.


@dataclass(frozen=True)
class Rule:
    name: str
    predicate: Callable[[TriageState], bool]
    min_tier: Tier
    basis: str

    def matches(self, state: TriageState) -> bool:
        return bool(self.predicate(state))


HARD_RULES: Tuple[Rule, ...] = (
    Rule(
        "cssrs_intent_or_behaviour",
        lambda s: s.screeners.cssrs.high_risk,
        Tier.CRITICAL,
        "C-SSRS screener items 4, 5 or 6 positive — active intent, plan with "
        "intent, or suicidal behaviour",
    ),
    Rule(
        "explicit_self_harm_language",
        lambda s: "self_harm" in s.lexicon_hits,
        Tier.CRITICAL,
        "Explicit self-harm or suicide language detected by the curated "
        "crisis lexicon for the caller's language",
    ),
    Rule(
        "imminent_threat_to_life",
        lambda s: "threat_imminent" in s.facts.present
                  or "imminent_violence" in s.lexicon_hits,
        Tier.CRITICAL,
        "Imminent threat to life — SC/ST (PoA) Act s.15A(11), duty to provide "
        "protection",
    ),
    Rule(
        "sexual_offence_against_minor",
        lambda s: "victim_minor" in s.facts.present
                  and s.facts.offence_category in SEXUAL_OFFENCES,
        Tier.CRITICAL,
        "Sexual offence against a minor — POCSO Act read with SC/ST (PoA) Act",
    ),
    Rule(
        "cssrs_any_ideation",
        lambda s: s.screeners.cssrs.any_ideation,
        Tier.HIGH,
        "C-SSRS screener positive for passive or active ideation without "
        "stated intent",
    ),
    Rule(
        "phq9_item9_positive",
        lambda s: s.screeners.phq9_item9_positive,
        Tier.HIGH,
        "PHQ-9 item 9 positive — thoughts of self-harm reported",
    ),
    Rule(
        "homicide_offence",
        lambda s: s.facts.offence_category == "murder",
        Tier.HIGH,
        "Homicide — SC/ST (PoA) Rules 1995, Rule 12(4) immediate relief and "
        "Rule 7 DySP-rank investigation",
    ),
    Rule(
        "cssrs_not_administered",
        lambda s: not s.screeners.cssrs.administered,
        Tier.HIGH,
        "Mandatory suicide screener not administered — interaction cannot be "
        "closed at a low tier without it",
    ),
)


@dataclass(frozen=True)
class RuleOutcome:
    tier: Tier
    triggered: Tuple[str, ...]
    bases: Tuple[str, ...]
    escalated: bool

    @property
    def model_bypassed(self) -> bool:
        """True when the final tier was set by rule rather than by score. The
        counsellor console displays this, and the pitch says it out loud."""
        return self.escalated


def apply_hard_rules(computed_tier: Tier, state: TriageState) -> RuleOutcome:
    """Escalate-only by construction: the result is the maximum of the computed
    tier and every matched rule's minimum tier."""
    matched: List[Rule] = [r for r in HARD_RULES if r.matches(state)]

    final = computed_tier
    for rule in matched:
        if tier_rank(rule.min_tier) > tier_rank(final):
            final = rule.min_tier

    # Report only the rules that actually did work — a rule whose minimum tier
    # sits at or below the computed tier changed nothing and should not appear
    # in the record as though it had.
    effective = tuple(r for r in matched
                      if tier_rank(r.min_tier) > tier_rank(computed_tier))

    return RuleOutcome(
        tier=final,
        triggered=tuple(r.name for r in effective),
        bases=tuple(r.basis for r in effective),
        escalated=tier_rank(final) > tier_rank(computed_tier),
    )
