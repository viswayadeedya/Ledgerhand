import json
from pathlib import Path

import pytest

from cua.artifacts.recorder import ArtifactBuildError, build_artifact
from cua.core.models import ActionType, RecordedStep

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_RUN_LOG = REPO_ROOT / "evidence" / "discovery-member-lookup" / "run_log.json"


@pytest.fixture
def real_run():
    data = json.loads(REAL_RUN_LOG.read_text(encoding="utf-8"))
    steps = [RecordedStep.model_validate(s) for s in data["steps"]]
    return data, steps


# The discovery model picks its own output key names each run (e.g.
# "name" vs "member_name") -- not perfectly deterministic, so tests derive
# expected keys from whatever the real run log actually contains rather
# than hardcoding one run's exact naming.
def _non_id_output_keys(data) -> list[str]:
    return [k for k in data["outputs"] if k != "member_id"]


def _build(data, steps, **overrides):
    output_keys = _non_id_output_keys(data)
    kwargs = dict(
        capability_id="member-savings-lookup",
        title="Look up a member's savings balance",
        description="Signs in, searches for a member by ID, and reads their balances.",
        target_domain=data["allowed_domain"],
        entry_url=data["target_url"],
        discovery_model=data["model"],
        inputs={"member_id": "10001"},
        input_descriptions={"member_id": "The member ID to look up."},
        secret_names=["username", "password"],
        secret_values={"username": "teller1"},
        output_descriptions={k: f"Extracted value for {k}." for k in output_keys},
        output_values={k: data["outputs"][k] for k in output_keys},
        checkpoint_text="Savings Balance",
        source_run_log=str(REAL_RUN_LOG),
    )
    kwargs.update(overrides)
    return build_artifact(steps, **kwargs)


def test_builds_artifact_from_a_real_discovery_run(real_run):
    data, steps = real_run
    artifact = _build(data, steps)

    assert artifact.id == "member-savings-lookup"
    assert artifact.target_domain == "127.0.0.1:5055"
    assert artifact.entry_url == "http://127.0.0.1:5055/login"
    assert len(artifact.steps) > 0
    expected_keys = set(_non_id_output_keys(data))
    assert {o.name for o in artifact.outputs} == expected_keys
    assert "savings_balance" in expected_keys
    assert "checking_balance" in expected_keys


def test_type_and_key_actions_are_excluded_from_replayable_steps(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    assert all(step.type not in (ActionType.TYPE, ActionType.KEY) for step in artifact.steps)


def test_member_id_is_parameterized(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    values = [s.value for s in artifact.steps if s.value is not None]
    assert "{{inputs.member_id}}" in values
    assert "10001" not in values  # the literal never survives into the artifact


def test_username_is_parameterized_as_a_secret(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    values = [s.value for s in artifact.steps if s.value is not None]
    assert "{{secrets.username}}" in values
    assert "teller1" not in values


def test_password_placeholder_survives_as_a_secret(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    values = [s.value for s in artifact.steps if s.value is not None]
    assert "{{secrets.password}}" in values


def test_no_secret_or_input_literal_anywhere_in_the_artifact(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    dumped = artifact.model_dump_json()
    assert "teller123" not in dumped
    assert "teller1" not in dumped
    assert "{{secrets.username}}" in dumped
    assert "{{secrets.password}}" in dumped


def test_outputs_have_locators_not_just_remembered_values(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    savings = next(o for o in artifact.outputs if o.name == "savings_balance")
    assert savings.target.candidates
    assert savings.target.candidates[0].value  # a real locator value, not the dollar amount itself


def test_checkpoint_resolves_to_a_locator(real_run):
    data, steps = real_run
    artifact = _build(data, steps)
    assert artifact.checkpoint.candidates
    assert "Savings Balance" in artifact.checkpoint.candidates[0].value


def test_missing_checkpoint_text_raises(real_run):
    data, steps = real_run
    with pytest.raises(ArtifactBuildError):
        _build(data, steps, checkpoint_text="")


def test_checkpoint_text_not_on_page_raises(real_run):
    data, steps = real_run
    with pytest.raises(ArtifactBuildError):
        _build(data, steps, checkpoint_text="Definitely Not On This Page XYZ")


def test_unfindable_output_value_raises(real_run):
    data, steps = real_run
    with pytest.raises(ArtifactBuildError):
        _build(data, steps, output_values={"bogus_output": "no such value anywhere"})


def test_no_replayable_steps_raises():
    with pytest.raises(ArtifactBuildError):
        build_artifact(
            [],
            capability_id="empty",
            title="x",
            description="x",
            target_domain="x",
            entry_url="x",
            discovery_model="x",
            checkpoint_text="x",
        )
