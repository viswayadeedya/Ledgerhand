"""The stability run's correctness check, tested in both directions.

"0 wrong answers" is the headline number of the whole stability scoreboard,
and a check that cannot fail would produce that number just as happily with
the system completely broken. These are fast (no browser, no app) because
what's under test is the comparison, not the replay.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_stability  # noqa: E402


def test_correct_outputs_are_accepted():
    outputs = run_stability.expected_outputs("10001")
    assert run_stability.check_outputs("10001", outputs) == []


def test_another_members_balance_is_caught():
    """The failure this whole check exists for: a run that reports success
    while handing back somebody else's money.
    """
    outputs = run_stability.expected_outputs("10001")
    outputs["savings_balance"] = run_stability.expected_outputs("10002")["savings_balance"]

    problems = run_stability.check_outputs("10001", outputs)

    assert [p["output"] for p in problems] == ["savings_balance"]


def test_a_wholesale_wrong_record_is_caught():
    problems = run_stability.check_outputs("10001", run_stability.expected_outputs("10002"))
    assert {p["output"] for p in problems} == {
        "member_id",
        "member_name",
        "savings_balance",
        "checking_balance",
    }


def test_a_mismatch_is_reported_masked_with_a_shape():
    """A correctness alarm has to be legible without putting the figures it
    is complaining about on disk -- the scoreboard is committed.
    """
    problems = run_stability.check_outputs("10001", run_stability.expected_outputs("10002"))
    savings = next(p for p in problems if p["output"] == "savings_balance")

    assert "2340.18" not in savings["expected"]
    assert "8112.02" not in savings["observed"]
    assert savings["expected"].startswith("***")
    assert "shape: money" in savings["observed"]


def test_no_outputs_is_not_a_wrong_answer():
    """A business outcome or a hard failure returns nothing, and nothing is
    not incorrect -- counting it as a wrong answer would bury the real
    signal under every not-found run.
    """
    assert run_stability.check_outputs("10001", {}) == []


def test_every_fault_has_a_declared_expected_outcome():
    """`unexpected_outcomes` is only meaningful if each fault says where it
    should land; a fault added without one would silently never be able to
    disagree.
    """
    assert all(v for v in run_stability.FAULT_EXPECTATIONS.values())


@pytest.mark.parametrize("runs", [1, 7, 20, 25])
def test_the_fault_schedule_covers_every_fault_once_it_can(runs):
    """Independent random draws skipped three conditions entirely in a real
    20-run pass. The schedule must cover them all as soon as there are
    enough runs to do so.
    """
    import random

    schedule = run_stability._fault_schedule(random.Random(1729), runs)

    assert len(schedule) == runs
    if runs >= len(run_stability.FAULT_EXPECTATIONS):
        assert set(schedule) == set(run_stability.FAULT_EXPECTATIONS)
