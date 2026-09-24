from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

from cua.artifacts.schema import CapabilityArtifact, OutputType, load_yaml
from cua.core.models import Action, ActionType, LocatorCandidate, LocatorStrategy, Target
from cua.replay.engine import ReplayEngine
from cua.replay.models import FailureReason, ReplayOutcome
from tests.conftest import arm_fault as _arm_fault
from tests.conftest import risky_artifact_for as _risky_artifact_for

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
    raw = load_yaml(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return _retarget(raw, fake_app_server.replace("http://", ""))


@pytest.fixture
def engine(test_policy, tmp_path) -> ReplayEngine:
    return ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)


def _with_identity_assertion(
    artifact: CapabilityArtifact, *, sensitive: bool = False, template: str = "{{inputs.member_id}}"
) -> CapabilityArtifact:
    """Rewrites the member_id assertion.

    The shipped artifact now carries the real one, so tests of the normal
    path use it directly rather than patching a copy. This remains for the
    cases that need a *different* assertion than the one that ships --
    chiefly the deliberately unresolvable template.
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
    risky_artifact = _risky_artifact_for(fake_app_server)
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


def _without_label_anchors(artifact: CapabilityArtifact) -> CapabilityArtifact:
    """Strips the label anchors back off, leaving the positional fallback.

    Written as a *removal* from the shipped artifact rather than as an
    addition to an older one. Before Phase 4 these tests built the anchored
    version by hand and used the committed file as the position-only
    control -- which worked only because that file happened not to have
    anchors yet. The moment it gained them, the control would have quietly
    become a second copy of the fix, and the test would have gone on
    passing while testing nothing.
    """
    outputs = []
    for spec in artifact.outputs:
        kept = [c for c in spec.target.candidates if c.strategy != LocatorStrategy.TABLE_LABEL]
        assert kept, f"output {spec.name} has no fallback behind its label anchor"
        outputs.append(spec.model_copy(update={"target": spec.target.model_copy(update={"candidates": kept})}))
    return artifact.model_copy(update={"outputs": outputs})


def _without_output_types(artifact: CapabilityArtifact) -> CapabilityArtifact:
    outputs = [spec.model_copy(update={"type": OutputType.STRING}) for spec in artifact.outputs]
    return artifact.model_copy(update={"outputs": outputs})


def test_extra_row_label_anchored_vs_position_only(engine, artifact, fake_app_server):
    """The same page, the same inserted row, three artifacts -- side by side.

    This is deliberately one test rather than three, because the contrast
    IS the claim. Read as a table:

        artifact                        outcome        savings_balance   reason
        ------------------------------  -------------  ----------------  --------------
        as shipped (label + money)      SUCCESS        $2340.18          -
        anchors removed, types removed  SUCCESS        2019-03-14 (!)    -
        anchors removed, types kept     HARD_FAILURE   (none)            format_invalid

    Row 2 is the bug: no error, a plausible-looking string, and the wrong
    answer -- savings' money has slid down into checking_balance. Row 1 is
    the fix as it ships, row 3 the net under it for when a label is renamed
    and only the positional fallback is left.
    """

    def run(variant):
        _arm_fault(fake_app_server, "extra_row", True)  # fire-once, so re-arm per run
        return engine.run(variant, inputs={"member_id": "10001"}, secrets=SECRETS)

    def summarize(result):
        return (
            result.outcome,
            result.outputs.get("savings_balance"),
            result.outputs.get("checking_balance"),
            result.error.reason_code if result.error else None,
        )

    anchored = run(artifact)  # exactly what ships, no test-only edits
    position_only = run(_without_output_types(_without_label_anchors(artifact)))
    position_only_typed = run(_without_label_anchors(artifact))

    assert summarize(anchored) == (ReplayOutcome.SUCCESS, "$2340.18", "$512.44", None)
    assert summarize(position_only) == (ReplayOutcome.SUCCESS, "2019-03-14", "$2340.18", None)
    assert summarize(position_only_typed) == (
        ReplayOutcome.HARD_FAILURE,
        None,  # nothing is returned, not even the outputs that were fine
        None,
        FailureReason.FORMAT_INVALID,
    )
    assert "savings_balance" in position_only_typed.error.message


def test_unresolvable_output_label_is_named_in_the_failure(engine, artifact):
    """The locator layer already refuses an ambiguous or missing label (see
    test_locator.py). What matters here is that its account of WHY survives
    the trip out to the caller: "could not be extracted" alone would leave
    a human unable to tell a renamed label from a duplicated one.
    """
    outputs = [
        spec.model_copy(
            update={
                "target": spec.target.model_copy(
                    update={
                        "candidates": [
                            LocatorCandidate(
                                strategy=LocatorStrategy.TABLE_LABEL, value="label=Renamed Balance,col=1"
                            )
                        ]
                    }
                )
            }
        )
        if spec.name == "savings_balance"
        else spec
        for spec in artifact.outputs
    ]
    result = engine.run(
        artifact.model_copy(update={"outputs": outputs}), inputs={"member_id": "10001"}, secrets=SECRETS
    )

    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.error.reason_code == FailureReason.ELEMENT_NOT_FOUND
    assert "savings_balance" in result.error.message  # which output
    assert "Renamed Balance" in result.error.observed  # which label
    assert "matched nothing" in result.error.observed  # and whether it was zero or several
    assert result.outputs == {}


def test_money_type_accepts_a_real_balance(engine, artifact):
    """The type check must not cry wolf on the normal path."""
    assert artifact.outputs[2].type == OutputType.MONEY  # the shipped artifact really declares it
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    assert result.outcome == ReplayOutcome.SUCCESS
    assert result.outputs["savings_balance"] == "$2340.18"


def test_identity_assertion_passes_on_the_right_record(engine, artifact):
    """The assertion must not cry wolf: a correct run still succeeds."""
    member_id = next(o for o in artifact.outputs if o.name == "member_id")
    assert member_id.must_equal == "{{inputs.member_id}}"  # as shipped, not patched in here
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
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
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.error is not None
    assert result.error.reason_code == FailureReason.IDENTITY_MISMATCH
    assert result.outputs == {}  # nothing escapes, not even partially
    assert "different record" in result.error.message


def test_identity_mismatch_masks_sensitive_values_in_the_error(engine, artifact, fake_app_server):
    _arm_fault(fake_app_server, "wrong_member", True)
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

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
