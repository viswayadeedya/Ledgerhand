from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

from cua.artifacts.schema import CapabilityArtifact
from cua.core.models import Action, ActionType, LocatorCandidate, LocatorStrategy, Target
from cua.replay.engine import ReplayEngine
from cua.replay.models import FailureReason, ReplayOutcome
from tests.conftest import arm_fault as _arm_fault

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"

SECRETS = {"username": "teller1", "password": "teller123"}


def _retarget(artifact: CapabilityArtifact, domain: str) -> CapabilityArtifact:
    """Points a committed, fixed-port artifact at this test module's
    isolated, ephemeral-port fake app instance -- same trick a real
    per-tenant override would need (Section 3.7's multi-tenant story), just
    scoped to tests here.
    """

    def _swap(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit(parts._replace(netloc=domain))

    steps = [a.model_copy(update={"url": _swap(a.url)}) if a.type == ActionType.NAVIGATE else a for a in artifact.steps]
    return artifact.model_copy(update={"target_domain": domain, "entry_url": _swap(artifact.entry_url), "steps": steps})


@pytest.fixture
def artifact(fake_app_server) -> CapabilityArtifact:
    raw = CapabilityArtifact.model_validate(yaml.safe_load(ARTIFACT_PATH.read_text(encoding="utf-8")))
    return _retarget(raw, fake_app_server.replace("http://", ""))


@pytest.fixture
def engine(test_policy, tmp_path) -> ReplayEngine:
    return ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)


def _with_identity_assertion(
    artifact: CapabilityArtifact, *, sensitive: bool = False, template: str = "{{inputs.member_id}}"
) -> CapabilityArtifact:
    """Adds the member_id identity assertion the committed artifact will
    carry once it's regenerated -- kept here so these tests don't depend on
    that having happened yet.
    """
    outputs = [
        o.model_copy(update={"must_equal": template, "sensitive": sensitive}) if o.name == "member_id" else o
        for o in artifact.outputs
    ]
    return artifact.model_copy(update={"outputs": outputs})


def test_replay_succeeds_and_matches_real_fixture_data(engine, artifact):
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.SUCCESS
    assert result.steps_executed == len(artifact.steps)
    assert result.outputs["savings_balance"] == "$2340.18"
    assert result.outputs["checking_balance"] == "$512.44"
    assert "Maria" in " ".join(result.outputs.values())


def test_replay_generalizes_to_a_different_member(engine, artifact):
    """The whole point of an artifact: the SAME recipe, a different input,
    reads DIFFERENT real data live -- not a cached/memorized answer.
    """
    result = engine.run(artifact, inputs={"member_id": "10002"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.SUCCESS
    assert result.outputs["savings_balance"] == "$8112.02"
    assert result.outputs["checking_balance"] == "$1200.00"
    assert "Carlos" in " ".join(result.outputs.values())


def test_replay_reports_business_outcome_for_unknown_member(engine, artifact):
    result = engine.run(artifact, inputs={"member_id": "77777"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.BUSINESS_OUTCOME
    assert result.business_outcome == "member_not_found"
    assert result.outputs == {}


def test_replay_recovers_from_unexpected_popup(engine, artifact, fake_app_server):
    _arm_fault(fake_app_server, "popup", True)
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.RECOVERED
    assert any(ev.kind == "dialog_dismissed" for ev in result.recovery_events)
    assert result.outputs["savings_balance"] == "$2340.18"


def test_replay_recovers_from_session_expiry(engine, artifact, fake_app_server):
    _arm_fault(fake_app_server, "session_expired", True)
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.RECOVERED
    assert any(ev.kind == "session_reauthenticated" for ev in result.recovery_events)
    assert result.outputs["savings_balance"] == "$2340.18"


def test_replay_blocks_risky_action_and_needs_human_approval(fake_app_server, test_policy, tmp_path):
    """Not the member-lookup artifact -- a small synthetic one pointed
    straight at the sub-account commit route, to prove replay's own
    guardrail wiring (not just Surface's, proven back in Part 3) refuses an
    irreversible action and reports NEEDS_HUMAN with enough detail to act on,
    then proceeds when a human approves it.
    """
    domain = fake_app_server.replace("http://", "")
    risky_artifact = CapabilityArtifact(
        id="risky-test",
        title="risky test",
        description="Directly submits the sub-account commit form -- for testing guardrail blocking only.",
        target_domain=domain,
        entry_url=f"{fake_app_server}/login",
        secrets=[{"name": "username"}, {"name": "password"}],
        steps=[
            Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/login"),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value='input[name="username"]')]),
                value="{{secrets.username}}",
            ),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value='input[name="password"]')]),
                value="{{secrets.password}}",
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Sign On")]),
            ),
            Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/app/member/10001/new-subaccount"),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value="#initial_deposit")]),
                value="100",
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(
                    candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Continue")]
                ),
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(
                    candidates=[
                        LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Confirm & Open Account")
                    ]
                ),
            ),
        ],
        checkpoint=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value="opened")]),
        checkpoint_description="Sub-account opened confirmation is shown.",
        provenance={"discovered_at": "2026-01-01T00:00:00Z", "discovery_model": "manual-test"},
    )

    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)

    blocked = engine.run(risky_artifact, secrets=SECRETS, human_approved=False)
    assert blocked.outcome == ReplayOutcome.NEEDS_HUMAN
    assert blocked.error is not None
    assert blocked.error.step_index == 7
    assert "policy" in blocked.error.message.lower()

    approved = engine.run(risky_artifact, secrets=SECRETS, human_approved=True)
    assert approved.outcome in (ReplayOutcome.SUCCESS, ReplayOutcome.RECOVERED)


def test_replay_hard_failure_has_debuggable_detail(engine, artifact):
    """A locator that can never resolve -- proves a genuine break is
    reported as HARD_FAILURE with step index, what was expected, and what
    was observed, not a silent hang or an unhelpful crash.
    """
    broken = artifact.model_copy(
        update={
            "steps": [
                artifact.steps[0],
                Action(
                    type=ActionType.CLICK,
                    target=Target(
                        candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value="This Button Does Not Exist")]
                    ),
                ),
            ]
        }
    )
    result = engine.run(broken, inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.error is not None
    assert result.error.step_index == 1
    assert "This Button Does Not Exist" in result.error.expected


def test_identity_assertion_passes_on_the_right_record(engine, artifact):
    """The assertion must not cry wolf: a correct run still succeeds."""
    result = engine.run(_with_identity_assertion(artifact), inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.SUCCESS
    assert result.outputs["member_id"] == "10001"


def test_wrong_record_is_hard_failure_not_success(engine, artifact, fake_app_server):
    """The whole point of Phase 1. The app serves a different member's page
    with a 200 and no visible error; the old checkpoint ("Savings Balance"
    is on screen) is perfectly true of that page, so replay would have
    returned SUCCESS with someone else's balance. In this domain a
    confidently wrong answer is worse than a crash.
    """
    _arm_fault(fake_app_server, "wrong_member", True)
    result = engine.run(_with_identity_assertion(artifact), inputs={"member_id": "10001"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.error is not None
    assert result.error.reason_code == FailureReason.IDENTITY_MISMATCH
    assert result.outputs == {}  # nothing escapes, not even partially
    assert "different record" in result.error.message


def test_identity_mismatch_masks_sensitive_values_in_the_error(engine, artifact, fake_app_server):
    _arm_fault(fake_app_server, "wrong_member", True)
    result = engine.run(
        _with_identity_assertion(artifact, sensitive=True), inputs={"member_id": "10001"}, secrets=SECRETS
    )

    assert result.outcome == ReplayOutcome.HARD_FAILURE
    blob = f"{result.error.message} {result.error.expected} {result.error.observed}"
    assert "10002" not in blob  # the record that was wrongly served
    assert "10001" not in blob  # and the one that was asked for
    assert "***02" in blob  # but enough tail to debug which was which
    assert "***01" in blob


def test_unresolvable_assertion_fails_before_opening_a_browser(engine, artifact):
    """A malformed artifact should fail loudly at the door, not halfway
    through a run with a browser already open.
    """
    broken = _with_identity_assertion(artifact, template="{{inputs.not_a_real_input}}")
    with pytest.raises(ValueError, match="unresolvable must_equal"):
        engine.run(broken, inputs={"member_id": "10001"}, secrets=SECRETS)


def test_replay_requires_declared_inputs_and_secrets(engine, artifact):
    with pytest.raises(ValueError, match="missing required inputs"):
        engine.run(artifact, inputs={}, secrets=SECRETS)
    with pytest.raises(ValueError, match="missing required secrets"):
        engine.run(artifact, inputs={"member_id": "10001"}, secrets={})
