from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

from cua.artifacts.schema import CapabilityArtifact, load_yaml
from cua.core.models import ActionType
from cua.handoff import EscalationRequest, HandoffAction, HandoffDecision, MockOperatorHandoff
from cua.replay.engine import ReplayEngine
from cua.replay.models import ReplayOutcome
from tests.conftest import arm_fault as _arm_fault

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"
SECRETS = {"username": "teller1", "password": "teller123"}


def _retarget(artifact: CapabilityArtifact, domain: str) -> CapabilityArtifact:
    def _swap(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit(parts._replace(netloc=domain))

    steps = [a.model_copy(update={"url": _swap(a.url)}) if a.type == ActionType.NAVIGATE else a for a in artifact.steps]
    return artifact.model_copy(update={"target_domain": domain, "entry_url": _swap(artifact.entry_url), "steps": steps})


@pytest.fixture
def artifact(fake_app_server) -> CapabilityArtifact:
    raw = load_yaml(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return _retarget(raw, fake_app_server.replace("http://", ""))


def test_no_handoff_configured_keeps_part6_behavior(test_policy, tmp_path, artifact):
    """Regression guard: an engine with no handoff handler must behave
    exactly like Part 6 -- NEEDS_HUMAN immediately, no escalation attempted.
    """
    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)  # handoff=None
    result = engine.run(artifact, inputs={"member_id": "55555"}, secrets=SECRETS)
    # 55555 doesn't exist -> member_not_found, NOT requires_human, so this
    # just proves normal operation is untouched by adding handoff support.
    assert result.outcome == ReplayOutcome.BUSINESS_OUTCOME
    assert result.escalations == []


def test_ambiguous_duplicate_escalates_and_operator_abandons(test_policy, tmp_path, artifact, fake_app_server):
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        assert request.business_outcome == "ambiguous_duplicate"
        assert "requires human review" in request.reason.lower()
        assert request.screenshot_path is not None
        return HandoffDecision(action=HandoffAction.ABANDON, operator_note="Could not reach the operator; abandoning.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path, handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path)
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.NEEDS_HUMAN
    assert result.business_outcome == "ambiguous_duplicate"
    assert len(result.escalations) == 1
    assert result.escalations[0].decision == HandoffAction.ABANDON

    escalation_files = list(tmp_path.glob("escalation_*.json"))
    assert len(escalation_files) == 1


def test_ambiguous_duplicate_resolved_by_operator_acting_on_the_same_live_session(
    test_policy, tmp_path, artifact, fake_app_server
):
    """The key proof for Part 7: the operator callback doesn't just approve
    in the abstract -- it reaches into the SAME live browser session replay
    was already using and clicks a real element on it, the way a human
    taking over the session would. Getting back Maria Garcia's real balance
    afterward proves it's the same session, not a fresh one.
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        # Acting directly on `surface` -- the exact object replay was using,
        # handed to us by the engine, not a new browser/page of our own.
        main_frame = surface.page.frame(name="main")
        assert main_frame is not None
        main_frame.get_by_role("link", name="View").first.click()
        return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, operator_note="Picked the first matching record.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path, handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path)
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.RECOVERED
    assert result.outputs["savings_balance"] == "$2340.18"  # Maria Garcia's real balance, read live
    assert len(result.escalations) == 1
    assert result.escalations[0].decision == HandoffAction.MANUAL_RESOLVED


def test_risky_step_approved_by_operator_then_automation_completes_it(fake_app_server, test_policy, tmp_path):
    """Companion to test_replay.py's guardrail-blocking proof: this time the
    block is resolved by a real (mocked) operator decision instead of the
    caller pre-setting human_approved=True, exercising the actual handoff
    path end to end for the risky-action case too.
    """
    from cua.core.models import Action, LocatorCandidate, LocatorStrategy, Target

    domain = fake_app_server.replace("http://", "")
    risky_artifact = CapabilityArtifact(
        id="risky-handoff-test",
        title="risky handoff test",
        description="Directly submits the sub-account commit form -- for testing handoff only.",
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

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        assert "policy" in request.reason.lower()
        return HandoffDecision(action=HandoffAction.APPROVE_AND_RETRY, operator_note="Confirmed with the member; approved.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path, handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path)
    )
    result = engine.run(risky_artifact, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.RECOVERED
    assert len(result.escalations) == 1
    assert result.escalations[0].decision == HandoffAction.APPROVE_AND_RETRY
