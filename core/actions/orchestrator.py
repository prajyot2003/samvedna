"""
The action orchestrator.

Turns a tier plus a set of established facts into a concrete list of actions,
each with a named owner, a deadline, and the statutory or clinical provision it
rests on. This is the module that makes the difference between a system that
produces a number and one that produces something a District Magistrate can
sign.

The policy table lives in `entitlements.json` — one reviewable file, no logic
embedded in it. A lawyer or nodal officer can read that file and check the
mapping without reading any Python, which is the point.

Pure, like the rest of `core`: `now` is passed in rather than read from the
clock, so a resolution is reproducible and testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from core.events import Tier, tier_rank
from core.svi.factors import FACTORS_BY_KEY, OFFENCE_SEVERITY

TABLE_PATH = Path(__file__).with_name("entitlements.json")


# --------------------------------------------------------------------------
# Table loading and validation
# --------------------------------------------------------------------------

class PolicyTableError(ValueError):
    """The action table is malformed. Raised at import, never at request time:
    a broken policy table must fail the build, not a live call."""


@dataclass(frozen=True)
class ActionSpec:
    id: str
    label: str
    type: str
    owner: str
    sla_minutes: int
    basis: str = ""
    note: str = ""


@dataclass(frozen=True)
class Policy:
    id: str
    tiers: FrozenSet[str]
    tier_at_least: Optional[str]
    facts_any: FrozenSet[str]
    facts_all: FrozenSet[str]
    offence_any: FrozenSet[str]
    rules_any: FrozenSet[str]
    then: Tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class PolicyTable:
    version: str
    owners: Dict[str, str]
    actions: Dict[str, ActionSpec]
    policies: Tuple[Policy, ...]


def load_table(path: Path = TABLE_PATH) -> PolicyTable:
    raw = json.loads(path.read_text(encoding="utf-8"))

    owners = raw.get("owners", {})
    actions: Dict[str, ActionSpec] = {}
    for aid, spec in raw.get("actions", {}).items():
        try:
            actions[aid] = ActionSpec(
                id=aid,
                label=spec["label"],
                type=spec["type"],
                owner=spec["owner"],
                sla_minutes=int(spec["sla_minutes"]),
                basis=spec.get("basis", ""),
                note=spec.get("note", ""),
            )
        except KeyError as exc:
            raise PolicyTableError(f"action '{aid}' is missing field {exc}") from exc
        if actions[aid].owner not in owners:
            raise PolicyTableError(
                f"action '{aid}' names unknown owner '{actions[aid].owner}'")
        if actions[aid].sla_minutes < 0:
            raise PolicyTableError(f"action '{aid}' has a negative SLA")

    valid_tiers = {t.value for t in Tier}
    valid_facts = set(FACTORS_BY_KEY)
    valid_offences = set(OFFENCE_SEVERITY)

    policies: List[Policy] = []
    for p in raw.get("policies", []):
        when = p.get("when", {})
        pol = Policy(
            id=p["id"],
            tiers=frozenset(when.get("tier", ())),
            tier_at_least=when.get("tier_at_least"),
            facts_any=frozenset(when.get("facts_any", ())),
            facts_all=frozenset(when.get("facts_all", ())),
            offence_any=frozenset(when.get("offence_any", ())),
            rules_any=frozenset(when.get("rule_triggered_any", ())),
            then=tuple(p.get("then", ())),
            note=p.get("note", ""),
        )
        # Every reference is checked here so a typo in the policy table is a
        # build failure rather than a silently missing referral.
        unknown_tiers = (pol.tiers | ({pol.tier_at_least} if pol.tier_at_least else set())) - valid_tiers
        if unknown_tiers:
            raise PolicyTableError(f"policy '{pol.id}' names unknown tier(s) {sorted(unknown_tiers)}")
        unknown_facts = (pol.facts_any | pol.facts_all) - valid_facts
        if unknown_facts:
            raise PolicyTableError(f"policy '{pol.id}' names unknown fact(s) {sorted(unknown_facts)}")
        unknown_offences = pol.offence_any - valid_offences
        if unknown_offences:
            raise PolicyTableError(f"policy '{pol.id}' names unknown offence(s) {sorted(unknown_offences)}")
        missing = set(pol.then) - set(actions)
        if missing:
            raise PolicyTableError(f"policy '{pol.id}' refers to undefined action(s) {sorted(missing)}")
        if not pol.then:
            raise PolicyTableError(f"policy '{pol.id}' resolves to no actions")
        policies.append(pol)

    if not policies:
        raise PolicyTableError("policy table contains no policies")

    return PolicyTable(
        version=raw.get("version", "unknown"),
        owners=owners,
        actions=actions,
        policies=tuple(policies),
    )


# Loaded once, at import. A malformed table breaks the build immediately.
TABLE: PolicyTable = load_table()


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedAction:
    action_id: str
    label: str
    type: str
    owner: str
    owner_label: str
    sla_minutes: int
    due_at: Optional[datetime]
    basis: str
    triggered_by: Tuple[str, ...]      # policy ids that produced this action
    note: str = ""

    @property
    def immediate(self) -> bool:
        return self.sla_minutes == 0


def _matches(policy: Policy,
             tier: Tier,
             facts: Set[str],
             offence: str,
             rules: Set[str]) -> bool:
    """A policy matches when every condition it states is satisfied. Conditions
    it does not state are not constraints."""
    if policy.tiers and tier.value not in policy.tiers:
        return False
    if policy.tier_at_least and tier_rank(tier) < tier_rank(Tier(policy.tier_at_least)):
        return False
    if policy.facts_any and not (policy.facts_any & facts):
        return False
    if policy.facts_all and not policy.facts_all.issubset(facts):
        return False
    if policy.offence_any and offence not in policy.offence_any:
        return False
    if policy.rules_any and not (policy.rules_any & rules):
        return False
    return True


def resolve_actions(tier: Tier,
                    facts: Iterable[str],
                    offence_category: str = "unspecified",
                    rules_triggered: Iterable[str] = (),
                    now: Optional[datetime] = None,
                    table: PolicyTable = TABLE) -> List[ResolvedAction]:
    """Resolve the action packet for one interaction.

    Actions are de-duplicated across policies. Where two policies raise the
    same action, the tighter SLA wins and both policy ids are recorded, so the
    audit trail shows every reason an action was raised rather than only the
    first one matched.

    Ordering is by deadline, then by owner, then by id — deterministic, and it
    puts what must happen now at the top of the counsellor's screen.
    """
    fact_set = set(facts)
    rule_set = set(rules_triggered)

    chosen: Dict[str, Tuple[ActionSpec, List[str]]] = {}
    for policy in table.policies:
        if not _matches(policy, tier, fact_set, offence_category, rule_set):
            continue
        for action_id in policy.then:
            spec = table.actions[action_id]
            if action_id in chosen:
                chosen[action_id][1].append(policy.id)
            else:
                chosen[action_id] = (spec, [policy.id])

    resolved = [
        ResolvedAction(
            action_id=spec.id,
            label=spec.label,
            type=spec.type,
            owner=spec.owner,
            owner_label=table.owners[spec.owner],
            sla_minutes=spec.sla_minutes,
            due_at=(now + timedelta(minutes=spec.sla_minutes)) if now else None,
            basis=spec.basis,
            triggered_by=tuple(sorted(set(policies))),
            note=spec.note,
        )
        for spec, policies in chosen.values()
    ]

    resolved.sort(key=lambda a: (a.sla_minutes, a.owner, a.action_id))
    return resolved


def summarise(actions: Sequence[ResolvedAction]) -> Dict[str, int]:
    """Counts by owner, for the district dashboard's workload view."""
    out: Dict[str, int] = {}
    for a in actions:
        out[a.owner] = out.get(a.owner, 0) + 1
    return out
