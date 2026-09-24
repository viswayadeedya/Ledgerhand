"""Exit codes for the replay CLI.

These run the CLI as a real subprocess rather than calling main() in
process, because the thing under test is precisely what a caller sees from
outside: the process's exit status. A test that asserted on main()'s return
value would pass even if nothing ever passed it to sys.exit().
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cua.artifacts.schema import CapabilityArtifact, to_yaml
from cua.guardrails.policy import PolicyConfig
from cua.replay.__main__ import EXIT_CODES, USAGE_EXIT_CODE
from cua.replay.models import ReplayOutcome
from tests.conftest import arm_fault as _arm_fault
from tests.conftest import risky_artifact_for as _risky_artifact_for
from tests.test_replay import ARTIFACT_PATH, _retarget, _with_identity_assertion

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_ARGS = ["--secret", "username=teller1", "--secret", "password=teller123"]


def test_every_outcome_has_an_exit_code():
    """A new ReplayOutcome must not be able to reach the CLI without someone
    deciding what it means to a caller -- otherwise it would raise KeyError
    at the very end of an otherwise-successful run.
    """
    assert set(EXIT_CODES) == set(ReplayOutcome)
    assert USAGE_EXIT_CODE not in EXIT_CODES.values()  # a bad command line stays distinguishable


@pytest.fixture
def run_cli(fake_app_server, tmp_path):
    """Runs the real CLI against this module's ephemeral fake app.

    The default policy allowlist names the fixed dev port, so the subprocess
    gets a policy file pointed at the test server instead -- the same
    GUARDRAILS_POLICY_PATH override a per-environment deployment would use.
    """
    domain = fake_app_server.replace("http://", "")
    policy_path = tmp_path / "policy.yaml"
    cfg = PolicyConfig.load().model_copy(update={"allowed_domains": [domain]})
    policy_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")

    def _run(artifact: CapabilityArtifact, *args: str) -> subprocess.CompletedProcess:
        artifact_path = tmp_path / f"{artifact.id}.yaml"
        artifact_path.write_text(to_yaml(artifact), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable, "-m", "cua.replay",
                "--artifact", str(artifact_path),
                "--evidence-dir", str(tmp_path / "evidence"),
                *SECRET_ARGS,
                *args,
            ],
            cwd=str(REPO_ROOT),
            env={**os.environ, "GUARDRAILS_POLICY_PATH": str(policy_path)},
            capture_output=True,
            text=True,
        )

    return _run


@pytest.fixture
def lookup(fake_app_server) -> CapabilityArtifact:
    raw = CapabilityArtifact.model_validate(yaml.safe_load(ARTIFACT_PATH.read_text(encoding="utf-8")))
    return _retarget(raw, fake_app_server.replace("http://", ""))


def test_success_exits_zero(run_cli, lookup):
    proc = run_cli(lookup, "--input", "member_id=10001")
    assert proc.returncode == 0
    assert "Outcome: success" in proc.stdout


def test_business_outcome_exits_two(run_cli, lookup):
    """"No such member" is the app answering correctly. A caller retrying
    this on a nonzero exit would retry forever; 2 says "done, and the answer
    is no".
    """
    proc = run_cli(lookup, "--input", "member_id=99999")
    assert proc.returncode == 2
    assert "Outcome: business_outcome" in proc.stdout


def test_needs_human_exits_three(run_cli, fake_app_server):
    proc = run_cli(_risky_artifact_for(fake_app_server))
    assert proc.returncode == 3
    assert "Outcome: needs_human" in proc.stdout


def test_hard_failure_exits_one(run_cli, lookup, fake_app_server):
    _arm_fault(fake_app_server, "wrong_member", True)
    proc = run_cli(_with_identity_assertion(lookup), "--input", "member_id=10001")

    assert proc.returncode == 1
    assert "Outcome: hard_failure" in proc.stdout
    assert "identity_mismatch" in proc.stdout  # the structured output is unchanged
    assert "Traceback" not in proc.stderr


def test_bad_command_line_is_not_mistaken_for_a_business_outcome(run_cli, lookup):
    proc = run_cli(lookup, "--input", "member_id=10001", "--no-such-flag")
    assert proc.returncode == USAGE_EXIT_CODE


def test_missing_required_input_is_a_usage_error_not_a_traceback(run_cli, lookup):
    """Forgetting --input is the likeliest mistake. It's caught before a
    browser opens, so it should read as a correction to the command, not as
    replay having crashed.
    """
    proc = run_cli(lookup)  # no --input at all

    assert proc.returncode == USAGE_EXIT_CODE
    assert "Traceback" not in proc.stderr
    assert "missing required inputs" in proc.stderr


# -- Phase 3: what gets written down --------------------------------------


@pytest.fixture
def sensitive_lookup(lookup) -> CapabilityArtifact:
    """The committed artifact with the values a real deployment would mark
    sensitive: the member's name and both balances.
    """
    outputs = [
        spec.model_copy(update={"sensitive": True})
        if spec.name in ("member_name", "savings_balance", "checking_balance")
        else spec
        for spec in lookup.outputs
    ]
    return lookup.model_copy(update={"outputs": outputs})


def test_sensitive_outputs_are_masked_in_the_terminal_by_default(run_cli, sensitive_lookup):
    proc = run_cli(sensitive_lookup, "--input", "member_id=10001")

    assert proc.returncode == 0
    assert "$2340.18" not in proc.stdout
    assert "Maria Garcia" not in proc.stdout
    assert "***18" in proc.stdout  # enough tail to recognise, not enough to leak
    assert "--show-sensitive" in proc.stdout  # and says how to see the full value
    assert "10001" in proc.stdout  # member_id isn't marked sensitive here, so it isn't touched


def test_show_sensitive_prints_them_in_full(run_cli, sensitive_lookup):
    proc = run_cli(sensitive_lookup, "--input", "member_id=10001", "--show-sensitive")

    assert proc.returncode == 0
    assert "$2340.18" in proc.stdout
    assert "Maria Garcia" in proc.stdout


def test_result_files_stay_masked_even_with_show_sensitive(run_cli, sensitive_lookup, tmp_path):
    """The flag is about what a person sees on their own screen for a
    moment. A result file outlives the run and gets read by people who were
    never part of it, so it is masked regardless.
    """
    out_path = tmp_path / "result.json"
    proc = run_cli(
        sensitive_lookup, "--input", "member_id=10001", "--show-sensitive", "--out", str(out_path)
    )

    assert proc.returncode == 0
    assert "$2340.18" in proc.stdout  # the flag worked for the terminal

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert "$2340.18" not in out_path.read_text(encoding="utf-8")
    assert written["result"]["outputs"]["savings_balance"] == "***18"
    assert written["result"]["outputs"]["member_name"] == "***ia"
    assert written["result"]["outputs"]["member_id"] == "10001"  # not marked sensitive


def test_masking_does_not_touch_an_artifact_that_marks_nothing_sensitive(run_cli, lookup):
    """No flags, no sensitive declarations -- nothing changes. The masking
    has to be opt-in per artifact, not a blanket transform on every value.
    """
    proc = run_cli(lookup, "--input", "member_id=10001")

    assert "$2340.18" in proc.stdout
    assert "--show-sensitive" not in proc.stdout  # no note offered when there's nothing to mask
