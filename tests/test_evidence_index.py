"""The evidence index and the README generated from it.

These run in milliseconds and never touch a browser: what's under test is
that the committed table still describes the committed runs. A README that
has drifted from `index.json` is worse than no README, because it reads
exactly as authoritatively.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_evidence  # noqa: E402

EVIDENCE = REPO_ROOT / "evidence"
INDEX = EVIDENCE / "index.json"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["scenarios"]


def test_every_declared_scenario_actually_ran(rows):
    assert [r["scenario"] for r in rows] == [s.name for s in run_evidence.SCENARIOS]


def test_every_scenario_ended_where_it_was_expected_to(rows):
    """The committed evidence has to agree with itself. A folder whose
    result contradicts its own expectation is the one thing this whole
    directory exists to make impossible to miss.
    """
    disagreeing = [(r["scenario"], r["expected"], r["actual"]) for r in rows if r["expected"] != r["actual"]]
    assert disagreeing == []


def test_the_readme_is_what_the_generator_produces(rows):
    """"Do not edit by hand" is a comment; this is the enforcement.

    The Actual and Exit columns are only worth reading because nobody
    typed them, so a README that no longer matches its generator has lost
    the property that makes it evidence.
    """
    generated = run_evidence.render_readme(rows)
    committed = (EVIDENCE / "README.md").read_text(encoding="utf-8")
    assert committed == generated, "evidence/README.md is stale -- re-run scripts/run_evidence.py"


def test_each_scenario_folder_has_its_command_result_and_log(rows):
    for row in rows:
        folder = EVIDENCE / row["folder"]
        assert (folder / "command.txt").exists(), f"{row['folder']} has no command.txt"
        assert (folder / "result.json").exists(), f"{row['folder']} has no result.json"
        assert (folder / "run.log").exists(), f"{row['folder']} has no run.log"


def test_a_failing_scenario_kept_a_screenshot(rows):
    """The brief's "at least one richer signal on failure" (Section 3.5),
    checked against the folders rather than trusted.
    """
    failed = [r for r in rows if r["actual"].startswith("hard_failure")]
    assert failed, "no failure scenarios at all -- the error paths aren't being exercised"
    for row in failed:
        if row["scenario"] == "invalid_input":
            # Rejected before a browser opened, so there is nothing to
            # photograph. That is the feature, not a gap.
            continue
        assert (EVIDENCE / row["folder"] / "failure.png").exists(), f"{row['folder']} failed without a screenshot"


def test_every_runtime_condition_the_brief_names_has_a_scenario(rows):
    """Section 3.3's list, plus "outright app errors" from Section 1. The
    goal of this pass was one scenario each -- and a list in a docstring
    doesn't stay true, so it is asserted.
    """
    by_brief = " ".join(r["brief"] for r in rows)
    for condition in [
        "validation error",
        "business outcome",  # "record not found"
        "permission denial",
        "unexpected dialog",
        "session timeout",
        "transient slowness",
        "slow/failed load",
        "app error",
    ]:
        assert condition in by_brief, f"no scenario covers the brief's {condition!r}"
