import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

from cua.artifacts.schema import CapabilityArtifact, StepRisk, load_yaml
from cua.core.models import ActionType
from cua.handoff import (
    ControlHolder,
    EscalationRequest,
    HandoffAction,
    HandoffDecision,
    MockOperatorHandoff,
)
from cua.replay.engine import ReplayEngine
from cua.replay.models import ReplayOutcome
from tests.conftest import arm_fault as _arm_fault
from tests.conftest import reset_app as _reset_app

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


# -- Phase 5 step 4: what the human did, and who held the wheel -----------


def test_a_run_with_no_handoff_still_records_one_control_span(test_policy, tmp_path, artifact):
    """"Nobody took over" and "we didn't track it" must not look the same.
    A clean run is one uninterrupted automation span, closed at the end.
    """
    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    assert [s.holder for s in result.control_timeline] == [ControlHolder.AUTOMATION]
    assert result.control_timeline[0].ended_at is not None


def test_control_timeline_shows_the_handover_and_the_handback(
    test_policy, tmp_path, artifact, fake_app_server
):
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        surface.page.frame(name="main").get_by_role("link", name="View").first.click()
        return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, operator_note="Picked the first record.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    holders = [s.holder for s in result.control_timeline]
    assert holders == [ControlHolder.AUTOMATION, ControlHolder.HUMAN, ControlHolder.AUTOMATION]
    assert all(s.ended_at is not None for s in result.control_timeline)

    # Contiguous: each span starts exactly where the previous one ended, so
    # there is no moment the record can't say who was driving.
    for earlier, later in zip(result.control_timeline, result.control_timeline[1:]):
        assert earlier.ended_at == later.started_at


def test_operator_actions_are_captured_from_the_page_itself(
    test_policy, tmp_path, artifact, fake_app_server
):
    """The operator clicks through Playwright here; a person in the
    Inspector would produce the same DOM events. Capturing from the page
    is what makes one mechanism cover both.
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        surface.page.frame(name="main").get_by_role("link", name="View").first.click()
        return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, operator_note="Picked the first record.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    actions = result.escalations[0].operator_actions
    assert actions, "the operator clicked a link and nothing recorded it"
    assert any(a.kind == "click" and "View" in a.target for a in actions)


def test_an_operator_who_does_nothing_records_nothing(test_policy, tmp_path, artifact, fake_app_server):
    """The other direction: an empty list has to be trustworthy, or it
    can't be read as "they declined without touching anything".
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        return HandoffDecision(action=HandoffAction.ABANDON, operator_note="Declining.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    record = result.escalations[0]
    assert record.operator_actions == []


def test_a_value_the_operator_types_is_recorded_masked(test_policy, tmp_path, artifact, fake_app_server):
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        nav = surface.page.frame(name="nav")
        nav.locator('input[name="q"]').fill("10002")
        surface.page.frame(name="main").get_by_role("link", name="View").first.click()
        return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, operator_note="Corrected the search.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    typed = [a for a in result.escalations[0].operator_actions if a.kind == "change"]
    assert typed, "a filled field produced no change event"
    assert all(a.value != "10002" for a in typed)  # never the literal
    assert any(a.value == "***02" for a in typed)


def test_the_escalation_file_carries_the_actions_and_the_timeline(
    test_policy, tmp_path, artifact, fake_app_server
):
    """The intervention request and what came of it stay one document --
    that file is what a reviewer opens.
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        surface.page.frame(name="main").get_by_role("link", name="View").first.click()
        return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, operator_note="Picked the first record.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    written = json.loads(next(tmp_path.glob("escalation_*.json")).read_text(encoding="utf-8"))
    assert written["decision"]["action"] == "manual_resolved"
    assert any("View" in a["target"] for a in written["operator_actions"])
    assert [s["holder"] for s in written["control_timeline"]] == ["human", "automation"]


def test_submits_and_enter_presses_are_recorded_not_only_clicks(
    test_policy, tmp_path, artifact, fake_app_server
):
    """An operator who types into a field and hits Enter never clicks
    anything. Recording clicks alone would show them doing nothing at all,
    and the submit is the moment the form was actually committed.
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        nav = surface.page.frame(name="nav")
        nav.locator('input[name="q"]').fill("10002")
        nav.locator('input[name="q"]').press("Enter")
        surface.page.wait_for_timeout(500)
        return HandoffDecision(action=HandoffAction.ABANDON, operator_note="Re-searched, then stopped.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    actions = result.escalations[0].operator_actions
    kinds = {a.kind for a in actions}
    assert "key" in kinds and "submit" in kinds

    enter = next(a for a in actions if a.kind == "key")
    # Not masked: a key name is our own vocabulary, not user data, and
    # "***er" would destroy the only thing the field says.
    assert enter.value == "Enter"

    submitted = next(a for a in actions if a.kind == "submit")
    assert "form" in submitted.target
    # The form's own text is never used to describe it -- that would be
    # every label and value it contains, flattened into one string.
    assert "10002" not in submitted.target


def test_actions_on_a_page_opened_during_the_handoff_are_still_recorded(
    test_policy, tmp_path, artifact, fake_app_server
):
    """The listener has to survive the operator navigating. A human taking
    over a stuck run very often goes somewhere else first, and a recorder
    that only covers the document that was open when they arrived would
    quietly stop recording at exactly that point.
    """
    _arm_fault(fake_app_server, "duplicate_members", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        main = surface.page.frame(name="main")
        main.goto(f"{fake_app_server}/app/member/10002")  # a document loaded mid-handoff
        main.get_by_role("link", name="Open New Sub-Account").click()
        surface.page.wait_for_timeout(300)
        return HandoffDecision(action=HandoffAction.ABANDON, operator_note="Looked around, then stopped.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    actions = result.escalations[0].operator_actions
    clicked = [a for a in actions if a.kind == "click"]
    assert clicked, "a click on a page opened during the handoff recorded nothing"
    assert any("Open New Sub-Account" in a.target for a in clicked)
    assert any("10002" in (a.url or "") for a in clicked)


# -- Phase 5 step 5: the risky step, on the real committed artifact -------

SUBACCOUNT_ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-subaccount-open.yaml"


@pytest.fixture
def subaccount(fake_app_server) -> CapabilityArtifact:
    raw = load_yaml(SUBACCOUNT_ARTIFACT_PATH.read_text(encoding="utf-8"))
    return _retarget(raw, fake_app_server.replace("http://", ""))


SUBACCOUNT_INPUTS = {"member_id": "10001", "account_type": "checking", "initial_deposit": "250.00"}


def test_the_shipped_subaccount_artifact_marks_exactly_one_step_risky(subaccount):
    """The label is review metadata, and it is only worth anything if it
    points at the commit rather than at everything or nothing.
    """
    risky = [s for s in subaccount.steps if s.risk == StepRisk.RISKY]
    assert len(risky) == 1
    assert "commit" in risky[0].risk_note
    # Canonicalized: the route, not the member whose record was recorded.
    assert "10001" not in risky[0].risk_note


def test_subaccount_commit_is_blocked_without_a_human(test_policy, tmp_path, subaccount):
    """No handoff configured: the irreversible step stops the run rather
    than going through. This is the baseline the approval below changes.
    """
    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)
    result = engine.run(subaccount, inputs=SUBACCOUNT_INPUTS, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.NEEDS_HUMAN
    assert result.error.reason_code.value == "policy_blocked"
    assert result.steps_executed < len(subaccount.steps)  # it never reached the end


def test_subaccount_commit_blocked_then_approved_then_the_run_completes(
    test_policy, tmp_path, subaccount, fake_app_server
):
    """The whole risky-step story end to end, on the file that ships:
    policy refuses the commit, a human approves, automation performs it,
    and the run reads the confirmation off the real success page.
    """
    # The app is module-scoped and an earlier test in this file opens a
    # sub-account for the same member, so 4001 is only the right answer for
    # a test that establishes its own starting state.
    _reset_app(fake_app_server)
    seen = {}

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        seen["reason"] = request.reason
        return HandoffDecision(
            action=HandoffAction.APPROVE_AND_RETRY,
            operator_note="Verified with the member in branch; approved.",
        )

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(subaccount, inputs=SUBACCOUNT_INPUTS, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.RECOVERED
    assert "policy" in seen["reason"].lower()
    assert result.escalations[0].decision == HandoffAction.APPROVE_AND_RETRY

    # The account really was opened, and the app says so in its own words.
    assert result.outputs["sub_account_number"] == "1"
    assert result.outputs["account_type"] == "checking"
    assert result.outputs["initial_deposit"] == "$250.00"
    # And on the right member -- the assertion that matters most for a
    # capability that changes something rather than reading it.
    assert result.outputs["member_id"] == "10001"

    holders = [s.holder for s in result.control_timeline]
    assert holders == [ControlHolder.AUTOMATION, ControlHolder.HUMAN, ControlHolder.AUTOMATION]


def test_opening_a_subaccount_on_the_wrong_member_is_refused(
    test_policy, tmp_path, subaccount, fake_app_server
):
    """The identity assertion, on a capability that writes. A read that
    lands on the wrong record returns the wrong number; a write that does
    has already changed something -- so the check has to be on the
    confirmation the app gives back, not on where we thought we were.
    """
    _arm_fault(fake_app_server, "wrong_member", True)

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        return HandoffDecision(action=HandoffAction.APPROVE_AND_RETRY, operator_note="Approved.")

    engine = ReplayEngine(
        policy=test_policy, headless=True, evidence_dir=tmp_path,
        handoff=MockOperatorHandoff(operator, evidence_dir=tmp_path),
    )
    result = engine.run(subaccount, inputs=SUBACCOUNT_INPUTS, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.outputs == {}  # nothing about the wrong record reaches the caller
