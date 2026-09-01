#!/usr/bin/env python3
"""
Generate evidence/FAIRNESS.md from measured data.

Written as a generator rather than a document on purpose. A fairness report
composed by hand reports what its author believed; this one reports what the
database contains, and when the database contains nothing it says so in those
words rather than producing plausible-looking numbers.

The gold label is the counsellor's tier: the system's tier where it was left
alone, the overridden tier where a counsellor changed it. That is the correct
label for triage — the question is not whether the model agrees with itself, it
is whether it agrees with the trained human who took the call.

    python3 scripts/fairness_report.py                 # writes evidence/FAIRNESS.md
    python3 scripts/fairness_report.py --stdout        # prints instead
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select                              # noqa: E402

from core.events import Language, Tier, tier_rank          # noqa: E402
from services.nlp.lexicon import load_lexicon, production_ready  # noqa: E402
from services.store import models                          # noqa: E402
from services.store.repo import Repository                 # noqa: E402

OUTPUT = Path(__file__).resolve().parent.parent / "evidence" / "FAIRNESS.md"
MIN_SAMPLE = 30          # below this, a rate is noise wearing a percentage sign
GAP_THRESHOLD = 0.10     # stopping rule 3 in PILOT_PROTOCOL.md


@dataclass
class LanguageStats:
    language: str
    total: int = 0
    counsellor_critical: int = 0
    system_critical_when_counsellor_critical: int = 0
    abstained: int = 0
    overridden: int = 0
    override_up: int = 0
    override_down: int = 0
    scores: List[float] = field(default_factory=list)

    @property
    def critical_recall(self) -> Optional[float]:
        if self.counsellor_critical == 0:
            return None
        return self.system_critical_when_counsellor_critical / self.counsellor_critical

    @property
    def abstention_rate(self) -> Optional[float]:
        return self.abstained / self.total if self.total else None

    @property
    def override_rate(self) -> Optional[float]:
        return self.overridden / self.total if self.total else None

    @property
    def sufficient(self) -> bool:
        return self.total >= MIN_SAMPLE


def collect(repo: Repository) -> Dict[str, LanguageStats]:
    stats: Dict[str, LanguageStats] = defaultdict(lambda: LanguageStats(""))

    with repo.session() as session:
        interactions = {
            row.id: row.language for row in
            session.execute(select(models.Interaction)).scalars()
        }
        overrides: Dict[str, models.Override] = {}
        for row in session.execute(select(models.Override)).scalars():
            overrides[row.interaction_id] = row      # last write wins

        latest: Dict[str, models.SVISnapshot] = {}
        for row in session.execute(
                select(models.SVISnapshot).order_by(models.SVISnapshot.id)).scalars():
            latest[row.interaction_id] = row

    for interaction_id, snapshot in latest.items():
        language = interactions.get(interaction_id, "unknown")
        entry = stats[language]
        entry.language = language
        entry.total += 1
        entry.scores.append(snapshot.score)
        if snapshot.abstained:
            entry.abstained += 1

        system_tier = Tier(snapshot.final_tier)
        override = overrides.get(interaction_id)
        if override is None:
            gold = system_tier
        else:
            gold = Tier(override.to_tier)
            entry.overridden += 1
            if tier_rank(gold) > tier_rank(Tier(override.from_tier)):
                entry.override_up += 1
            else:
                entry.override_down += 1

        if gold is Tier.CRITICAL:
            entry.counsellor_critical += 1
            if system_tier is Tier.CRITICAL:
                entry.system_critical_when_counsellor_critical += 1

    return dict(stats)


def fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3f}"


def render(stats: Dict[str, LanguageStats]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ready, blockers = production_ready()

    lines = [
        "# Fairness report",
        "",
        f"**Generated:** {now} by `scripts/fairness_report.py`",
        "**Do not edit by hand.** This file is regenerated from the database; a "
        "hand-edited figure here would be a claim nobody measured.",
        "",
        "---",
        "",
        "## 1. What is measured, and against what",
        "",
        "The gold label is the counsellor's tier — the system's tier where it was "
        "left alone, the overridden tier where a counsellor changed it. The "
        "question is not whether the model agrees with itself; it is whether it "
        "agrees with the trained human who took the call.",
        "",
        "**Critical-class recall** is the headline. Precision is deliberately not "
        "optimised: a false negative is a life, a false positive is ten minutes "
        "of a counsellor's time.",
        "",
        "**Abstention rate** is reported beside it, because abstention is the "
        "mechanism protecting speakers the recogniser serves worst. A language "
        "with lower recall and a correspondingly higher abstention rate is "
        "behaving as designed; one with lower recall and no lift in abstention is "
        "failing those callers silently.",
        "",
    ]

    total = sum(s.total for s in stats.values())
    if total == 0:
        lines += [
            "## 2. Result",
            "",
            "**NO DATA. Nothing has been measured.**",
            "",
            "This database contains no completed assessments, so there is nothing "
            "to report and no accuracy figure of any kind is claimed. This is the "
            "expected state before the shadow-mode pilot; see "
            "`evidence/PILOT_PROTOCOL.md`.",
            "",
            "To produce a real report:",
            "",
            "1. Close the preparation blockers listed below.",
            "2. Run phase 1 (silent) until at least 500 interactions carry a "
            "counsellor tier.",
            "3. Re-run this script.",
            "",
        ]
    else:
        lines += ["## 2. By language", "",
                  "| Language | n | Counsellor Critical | Critical recall | "
                  "Abstention rate | Override rate |",
                  "|---|---:|---:|---:|---:|---:|"]
        for code in sorted(stats):
            s = stats[code]
            name = code
            try:
                name = load_lexicon(Language(code)).language_name
            except Exception:                                  # noqa: BLE001
                pass
            flag = "" if s.sufficient else " ⚠"
            lines.append(
                f"| {name}{flag} | {s.total} | {s.counsellor_critical} | "
                f"{fmt(s.critical_recall)} | {fmt(s.abstention_rate)} | "
                f"{fmt(s.override_rate)} |")

        lines += ["", f"⚠ marks a sample below {MIN_SAMPLE}, where a rate is "
                      "noise wearing a percentage sign. Those rows are shown for "
                      "completeness and must not be quoted.", ""]

        measured = {c: s for c, s in stats.items()
                    if s.sufficient and s.critical_recall is not None}
        lines += ["## 3. The dialect gap", ""]
        if len(measured) < 2:
            lines += ["Not enough languages have a sufficient sample to compare. "
                      "The gap is the number this report exists to produce, and it "
                      "is not yet available.", ""]
        else:
            best = max(measured, key=lambda c: measured[c].critical_recall)
            worst = min(measured, key=lambda c: measured[c].critical_recall)
            gap = measured[best].critical_recall - measured[worst].critical_recall
            lines += [f"Critical-class recall differs by **{gap:.3f}** "
                      f"({best} over {worst}).", ""]
            if gap > GAP_THRESHOLD:
                comp = ((measured[worst].abstention_rate or 0)
                        > (measured[best].abstention_rate or 0))
                lines += [
                    f"> **This exceeds the {GAP_THRESHOLD:.2f} threshold in "
                    f"stopping rule 3.**",
                    ">",
                    "> " + ("Abstention is higher for the worse-served language, "
                            "which is the mitigation working as designed — but the "
                            "gap must still be reported and reduced."
                            if comp else
                            "Abstention is **not** higher for the worse-served "
                            "language. Those callers are being under-triaged "
                            "silently. Under the protocol the pilot halts."),
                    ""]

        lines += ["## 4. Override direction", "",
                  "Persistent one-directional override is a calibration fault, not "
                  "counsellor error.", "",
                  "| Language | Overrides up | Overrides down |", "|---|---:|---:|"]
        for code in sorted(stats):
            s = stats[code]
            lines.append(f"| {code} | {s.override_up} | {s.override_down} |")
        lines.append("")

    lines += ["## 5. Deployment readiness", "",
              f"`production_ready()` reports **{ready}**."]
    if blockers:
        lines += [""] + [f"- {b}" for b in blockers]
    lines += ["",
              "Detection quality is not equal across languages, and the languages "
              "served worst are those whose speakers this Act exists to protect. "
              "That asymmetry is the reason the abstention path exists, and the "
              "reason this report is generated rather than written.",
              ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    repo = Repository(args.database_url) if args.database_url else Repository()
    repo.create_all()
    report = render(collect(repo))

    if args.stdout:
        print(report)
    else:
        OUTPUT.write_text(report, encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
