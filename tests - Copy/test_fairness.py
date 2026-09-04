"""
Fairness report generator tests.

This script produces the number the project's central claim rests on, so its
failure modes matter more than most: silently reporting a rate from four
samples, or failing to notice that one language is being under-triaged without
compensating abstention.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from core.events import Tier
from core.rules.hard_rules import RuleOutcome
from core.svi.engine import SVIResult
from services.store.repo import Repository

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "fairness_report", ROOT / "scripts" / "fairness_report.py")
fairness = importlib.util.module_from_spec(spec)
sys.modules["fairness_report"] = fairness
spec.loader.exec_module(fairness)


def result(score: float, tier: Tier, abstained: bool = False) -> SVIResult:
    return SVIResult(score=score, tier=tier, channel_a=0.5, channel_b=0.4,
                     channel_c_delta=0.0, base=score, abstained=abstained,
                     abstention_reasons=("low_signal_confidence",) if abstained else (),
                     coverage_a=1.0, coverage_b=1.0, coarse_domains=(),
                     contributions={})


def outcome(tier: Tier) -> RuleOutcome:
    return RuleOutcome(tier=tier, triggered=(), bases=(), escalated=False)


@pytest.fixture()
def repo(tmp_path):
    r = Repository(f"sqlite:///{tmp_path/'fair.db'}")
    r.create_all()
    return r


def seed(repo, language: str, n: int, *, tier: Tier, abstained=False,
         override_to: Tier | None = None, prefix="x"):
    for i in range(n):
        iid = f"{prefix}-{language}-{i}"
        repo.start_interaction(iid, "ivrs", language)
        snapshot = repo.save_snapshot(iid, result(70.0, tier, abstained), outcome(tier))
        if override_to is not None:
            repo.record_override(iid, snapshot.id, tier.value, override_to.value,
                                 "c-1", "Documented clinical reason for the change.")


# ------------------------------------------------- the empty case

def test_an_empty_database_reports_no_data_rather_than_numbers(repo):
    report = fairness.render(fairness.collect(repo))
    assert "NO DATA. Nothing has been measured." in report
    assert "no accuracy figure of any kind is claimed" in report
    assert "0.000" not in report          # never dress absence up as a measurement


# ------------------------------------------------- sampling honesty

def test_small_samples_are_flagged_and_excluded_from_the_gap(repo):
    seed(repo, "hi", 5, tier=Tier.CRITICAL)
    seed(repo, "bho", 4, tier=Tier.LOW)
    report = fairness.render(fairness.collect(repo))
    assert "⚠" in report
    assert "noise wearing a percentage sign" in report
    assert "Not enough languages have a sufficient sample" in report


def test_the_minimum_sample_is_enforced_not_advisory(repo):
    seed(repo, "hi", fairness.MIN_SAMPLE - 1, tier=Tier.CRITICAL)
    stats = fairness.collect(repo)
    assert not stats["hi"].sufficient
    seed(repo, "hi", 1, tier=Tier.CRITICAL, prefix="y")
    assert fairness.collect(repo)["hi"].sufficient


# ------------------------------------------------- the gold label

def test_an_override_becomes_the_gold_label(repo):
    """The question is whether the system agrees with the counsellor, not with
    itself."""
    seed(repo, "hi", 40, tier=Tier.LOW, override_to=Tier.CRITICAL)
    stats = fairness.collect(repo)["hi"]
    assert stats.counsellor_critical == 40
    assert stats.system_critical_when_counsellor_critical == 0
    assert stats.critical_recall == 0.0          # the system missed every one


def test_override_direction_is_reported(repo):
    seed(repo, "hi", 30, tier=Tier.LOW, override_to=Tier.HIGH)
    seed(repo, "hi", 30, tier=Tier.CRITICAL, override_to=Tier.MODERATE, prefix="d")
    stats = fairness.collect(repo)["hi"]
    assert stats.override_up == 30 and stats.override_down == 30
    assert "calibration fault, not counsellor error" in fairness.render(
        fairness.collect(repo))


# ------------------------------------------------- the stopping rule

def test_a_gap_with_compensating_abstention_reads_as_the_mitigation_working(repo):
    seed(repo, "hi", 40, tier=Tier.CRITICAL)
    seed(repo, "bho", 40, tier=Tier.HIGH, abstained=True, override_to=Tier.CRITICAL)
    report = fairness.render(fairness.collect(repo))
    assert "exceeds the 0.10 threshold" in report
    assert "mitigation working as designed" in report


def test_a_gap_without_compensating_abstention_halts_the_pilot(repo):
    """The failure this report exists to catch: one language under-triaged, and
    the abstention path not firing to compensate."""
    seed(repo, "hi", 40, tier=Tier.CRITICAL)
    seed(repo, "bho", 40, tier=Tier.HIGH, abstained=False, override_to=Tier.CRITICAL)
    report = fairness.render(fairness.collect(repo))
    assert "exceeds the 0.10 threshold" in report
    assert "under-triaged" in report
    assert "the pilot halts" in report


def test_equal_performance_raises_no_warning(repo):
    seed(repo, "hi", 40, tier=Tier.CRITICAL)
    seed(repo, "bho", 40, tier=Tier.CRITICAL)
    report = fairness.render(fairness.collect(repo))
    assert "exceeds" not in report
    assert "differs by **0.000**" in report


# ------------------------------------------------- the report itself

def test_the_report_always_carries_the_readiness_verdict(repo):
    report = fairness.render(fairness.collect(repo))
    assert "production_ready" in report or "production ready" in report.lower()
    assert "has not been reviewed by a native speaker" in report


def test_the_report_warns_against_hand_editing(repo):
    assert "Do not edit by hand" in fairness.render(fairness.collect(repo))
