import json
import re
from pathlib import Path

import pytest
import yaml

from cua.artifacts.recorder import ArtifactBuildError, build_artifact
from cua.artifacts.schema import (
    CURRENT_SCHEMA_VERSION,
    CapabilityArtifact,
    OutdatedArtifactWarning,
    SchemaVersionError,
    StepRisk,
    load_yaml,
    to_yaml,
)
from cua.core.models import (
    Action,
    ActionResult,
    ActionType,
    ElementSummary,
    LocatorCandidate,
    LocatorStrategy,
    Observation,
    RecordedStep,
    Target,
)

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


def test_outputs_are_label_anchored_first_then_position(real_run):
    """The label comes out of the real captured observation -- the cell to
    the left of the value in the same row -- not from anything hand-written.
    """
    data, steps = real_run
    artifact = _build(data, steps)
    savings = next(o for o in artifact.outputs if o.name == "savings_balance")

    strategies = [c.strategy for c in savings.target.candidates]
    assert strategies[0] == LocatorStrategy.TABLE_LABEL
    assert savings.target.candidates[0].value == "label=Savings Balance,col=1"
    assert LocatorStrategy.TABLE_POSITION in strategies  # kept as the fallback


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


# -- Phase 3: is the artifact actually reviewable? -------------------------


def test_every_step_gets_a_plain_english_description(real_run):
    """A reviewer should be able to read the steps without decoding
    selectors. Descriptions are generated from the locator that actually
    resolved during discovery, so they describe the real page.
    """
    data, steps = real_run
    artifact = _build(data, steps)

    assert all(s.description for s in artifact.steps)
    descriptions = [s.description for s in artifact.steps]
    assert 'Click the "Sign On" button.' in descriptions
    assert 'Enter the member_id input into the "Member ID or Last Name" field.' in descriptions
    # The field's own label, not "the table cell at row=2,col=0".
    assert not any("table cell at row=" in d for d in descriptions)


def test_descriptions_name_parameters_without_reproducing_their_values(real_run):
    """The templates exist so credentials and member IDs aren't in the file.
    A generated sentence must not put them back.
    """
    data, steps = real_run
    artifact = _build(data, steps)

    blob = " ".join(s.description for s in artifact.steps)
    assert "teller1" not in blob
    assert "teller123" not in blob
    assert "10001" not in blob
    assert "the password secret" in blob
    assert "the member_id input" in blob


def test_risk_labels_come_from_where_discovery_actually_landed(real_run):
    data, steps = real_run
    artifact = _build(data, steps)

    by_description = {s.description: s for s in artifact.steps}
    view = by_description['Click the "View" link.']
    assert view.risk == StepRisk.SAFE
    # The destination, not the frame the link lived in -- the app is
    # frame-based and the click happened in the nav frame.
    assert "/app/member/:member_id" in view.risk_note
    assert "observed at discovery" in view.risk_note
    assert "re-checked at replay" in view.risk_note


def test_a_step_landing_on_a_risky_route_is_labelled_risky():
    """The same PolicyEngine replay uses, applied to the destination the run
    log recorded. A click's selector says nothing about where it goes, so
    this is the only honest way to label one.
    """
    commit_url = "http://127.0.0.1:5055/app/member/10001/new-subaccount/commit"
    steps = [
        RecordedStep(
            index=0,
            tool_name="browser_click",
            action=Action(type=ActionType.NAVIGATE, url="http://127.0.0.1:5055/app/member/10001"),
            result=ActionResult(
                success=True,
                observation=Observation(url="http://127.0.0.1:5055/app", frames={"main": "/app/member/10001"}),
            ),
        ),
        RecordedStep(
            index=1,
            tool_name="browser_click",
            action=Action(
                type=ActionType.CLICK,
                target=Target(
                    frame="main",
                    candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Confirm")],
                ),
            ),
            result=ActionResult(
                success=True,
                resolved_strategy=LocatorStrategy.ROLE,
                resolved_value="Confirm",
                observation=Observation(
                    url="http://127.0.0.1:5055/app",
                    frames={"main": commit_url},
                    elements=[ElementSummary(tag="td", text="Savings Balance")],
                ),
            ),
        ),
    ]

    artifact = build_artifact(
        steps,
        capability_id="risky",
        title="x",
        description="x",
        target_domain="127.0.0.1:5055",
        entry_url="http://127.0.0.1:5055/login",
        discovery_model="manual-test",
        checkpoint_text="Savings Balance",
    )

    assert artifact.steps[1].risk == StepRisk.RISKY
    assert "new-subaccount/commit" in artifact.steps[1].risk_note


def test_a_step_with_nothing_observed_is_unverified_not_assumed_safe():
    """Saying "nobody checked" is the honest answer. Labelling an unchecked
    step safe is the one that gets someone hurt.
    """
    steps = [
        RecordedStep(
            index=0,
            tool_name="browser_click",
            action=Action(type=ActionType.NAVIGATE, url="http://127.0.0.1:5055/login"),
            result=ActionResult(
                success=True,
                observation=Observation(
                    url="http://127.0.0.1:5055/login",
                    elements=[ElementSummary(tag="td", text="Savings Balance")],
                ),
            ),
        ),
        RecordedStep(
            index=1,
            tool_name="browser_click",
            action=Action(
                type=ActionType.CLICK,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value="Somewhere")]),
            ),
            result=None,  # the run ended before anything was captured
        ),
    ]

    artifact = build_artifact(
        steps,
        capability_id="unknown",
        title="x",
        description="x",
        target_domain="127.0.0.1:5055",
        entry_url="http://127.0.0.1:5055/login",
        discovery_model="manual-test",
        checkpoint_text="Savings Balance",
    )

    assert artifact.steps[1].risk == StepRisk.UNVERIFIED
    assert "no page state was captured" in artifact.steps[1].risk_note


def test_yaml_omits_fields_still_at_their_defaults(real_run):
    """The artifact is meant to be read before it's trusted. `role: null`
    and `sensitive: false` repeated a few dozen times is what stops people
    reading carefully.
    """
    data, steps = real_run
    text = to_yaml(_build(data, steps, sensitive_outputs=["savings_balance"]))

    assert "role: null" not in text
    assert "frame: null" not in text
    assert "point: null" not in text
    assert "method: null" not in text
    assert "sensitive: false" not in text
    assert "business_outcomes: []" not in text

    # ...but the identifying fields are written even at their defaults, and
    # anything actually set survives.
    assert "schema_version:" in text
    assert "version: 1" in text
    assert "sensitive: true" in text


def test_yaml_keeps_a_deliberately_empty_value(real_run):
    """`value: ''` on a fill means "clear this box" -- a real instruction
    that differs from the default of None. A blanket "drop anything falsy"
    would silently change what the artifact does.
    """
    data, steps = real_run
    artifact = _build(data, steps)
    cleared = artifact.steps[1].model_copy(update={"value": ""})
    artifact = artifact.model_copy(update={"steps": [cleared, *artifact.steps[1:]]})

    assert "value: ''" in to_yaml(artifact)


def test_yaml_round_trips(real_run):
    """Pruned output must still load back to the same artifact -- otherwise
    "readable" has been bought with a broken contract.
    """
    data, steps = real_run
    original = _build(data, steps, sensitive_outputs=["savings_balance"])
    reloaded = CapabilityArtifact.model_validate(yaml.safe_load(to_yaml(original)))

    assert reloaded == original


def test_observed_destinations_are_canonicalized_not_pinned_to_one_record(real_run):
    """The risk note has to say where a step goes without saying whose
    record it went to. /app/member/10001 is a fact about one discovery run;
    /app/member/:member_id is a fact about the capability.
    """
    data, steps = real_run
    artifact = _build(data, steps)

    notes = " ".join(s.risk_note for s in artifact.steps)
    assert "/app/member/:member_id" in notes
    assert "/app/member/10001" not in notes


def test_no_input_value_from_the_discovery_run_survives_anywhere(real_run):
    """One assertion over the whole serialized artifact, because the ways a
    literal can leak back in are not enumerable in advance -- it has shown
    up in a step value, in a navigate URL, in an input example and in a
    risk note, each for a different reason.
    """
    data, steps = real_run
    artifact = _build(data, steps)

    assert "10001" not in artifact.model_dump_json()
    assert "10001" not in to_yaml(artifact)
    assert "{{inputs.member_id}}" in artifact.model_dump_json()  # parameterized, not merely deleted


def test_a_navigate_url_holding_a_record_id_is_parameterized():
    """A recorded navigate to a record-specific page would otherwise replay
    for the *original* member no matter who was asked for -- a silent wrong
    answer, not a crash.
    """
    steps = [
        RecordedStep(
            index=0,
            tool_name="browser_navigate",
            action=Action(type=ActionType.NAVIGATE, url="http://127.0.0.1:5055/app/member/10001"),
            result=ActionResult(
                success=True,
                observation=Observation(
                    url="http://127.0.0.1:5055/app/member/10001",
                    elements=[ElementSummary(tag="td", text="Savings Balance")],
                ),
            ),
        )
    ]

    artifact = build_artifact(
        steps,
        capability_id="direct",
        title="x",
        description="x",
        target_domain="127.0.0.1:5055",
        entry_url="http://127.0.0.1:5055/login",
        discovery_model="manual-test",
        inputs={"member_id": "10001"},
        checkpoint_text="Savings Balance",
    )

    assert artifact.steps[0].url == "http://127.0.0.1:5055/app/member/{{inputs.member_id}}"
    assert "10001" not in to_yaml(artifact)


def test_canonicalization_matches_whole_segments_only():
    """An input of "1" must not turn /app/member/10001 into
    /app/member/:member_id0001. Substring replacement on a URL is how you
    get a locator that works on exactly one member's ID and corrupts every
    other one.
    """
    steps = [
        RecordedStep(
            index=0,
            tool_name="browser_navigate",
            action=Action(type=ActionType.NAVIGATE, url="http://127.0.0.1:5055/app/member/10001"),
            result=ActionResult(
                success=True,
                observation=Observation(
                    url="http://127.0.0.1:5055/app/member/10001",
                    elements=[ElementSummary(tag="td", text="Savings Balance")],
                ),
            ),
        )
    ]

    artifact = build_artifact(
        steps,
        capability_id="direct",
        title="x",
        description="x",
        target_domain="127.0.0.1:5055",
        entry_url="http://127.0.0.1:5055/login",
        discovery_model="manual-test",
        inputs={"digit": "1"},
        checkpoint_text="Savings Balance",
    )

    assert artifact.steps[0].url == "http://127.0.0.1:5055/app/member/10001"


# -- Phase 4: schema versioning -------------------------------------------


def _shipped() -> dict:
    return yaml.safe_load((REPO_ROOT / "artifacts" / "member-savings-lookup.yaml").read_text(encoding="utf-8"))


def test_an_invalid_pattern_is_refused_at_build_time(real_run):
    """A pattern that can't compile fails at the door of every future
    replay. It costs nothing to find out here and something every time
    otherwise.
    """
    data, steps = real_run
    with pytest.raises(ArtifactBuildError, match="invalid pattern"):
        _build(data, steps, input_patterns={"member_id": "^[0-9{5}$"})


def test_a_pattern_the_recorded_run_would_fail_is_refused(real_run):
    """The capability would reject the very value it was built from -- a
    contradiction, and one that would otherwise show up as a mystifying
    validation error on the first replay of a known-good input.
    """
    data, steps = real_run
    with pytest.raises(ArtifactBuildError, match="the value it was built from"):
        _build(data, steps, input_patterns={"member_id": "^[a-z]+$"})


def test_the_discovery_literal_cannot_be_stored_as_an_example(real_run):
    """The easy mistake, and the one parameterization exists to prevent:
    the run's own member ID is right there and does match the pattern, so
    reaching for it as "the example" puts real data straight back into a
    committed file.
    """
    data, steps = real_run
    with pytest.raises(ArtifactBuildError, match="discovery run's own value"):
        _build(data, steps, input_examples={"member_id": "10001"})


def test_an_example_that_contradicts_its_own_pattern_is_refused(real_run):
    data, steps = real_run
    with pytest.raises(ArtifactBuildError, match="does not match its own pattern"):
        _build(
            data,
            steps,
            input_patterns={"member_id": "^[0-9]{5}$"},
            input_examples={"member_id": "abc"},
        )


def test_the_shipped_artifact_declares_a_member_id_pattern_and_a_fake_example():
    """Checked against what actually ships, not a copy built in the test:
    the pattern is only worth anything if the committed file carries it.
    """
    shipped = _shipped()
    member_id = next(i for i in shipped["inputs"] if i["name"] == "member_id")

    assert member_id["pattern"] == "^[0-9]{5}$"
    assert member_id["example"] == "00000"
    # The example is published documentation. It has to be plainly not a
    # real record -- 00000 is well-formed and matches nothing seeded.
    assert re.fullmatch(member_id["pattern"], member_id["example"])


def test_the_shipped_artifact_is_at_the_current_schema():
    shipped = _shipped()
    assert shipped["schema_version"] == CURRENT_SCHEMA_VERSION
    assert shipped["version"] == 4  # the capability's own revision, not the schema's


def test_an_older_artifact_still_loads_but_says_what_it_is_missing():
    """A 1.0 artifact predates the identity assertion. It runs -- refusing
    it would strand every artifact built before this week -- but silence
    would let someone keep running a capability that isn't checking it
    reached the right record.
    """
    older = dict(_shipped(), schema_version="1.0")

    with pytest.warns(OutdatedArtifactWarning, match="must_equal"):
        artifact = load_yaml(yaml.safe_dump(older))

    assert artifact.id == "member-savings-lookup"  # loaded, not refused


def test_a_newer_minor_version_is_refused_rather_than_partly_applied():
    """The fields most likely to be new are *checks*. Pydantic would drop
    what it doesn't recognise, so running a next-minor artifact on this
    code means silently ignoring the safety it was written with -- and
    reporting success.
    """
    # Derived, not hardcoded: written as a literal this test passes only
    # until that version becomes the current one, and then fails for a
    # reason that has nothing to do with what it checks.
    major, minor = (int(p) for p in CURRENT_SCHEMA_VERSION.split("."))
    newer = dict(_shipped(), schema_version=f"{major}.{minor + 1}")

    with pytest.raises(SchemaVersionError, match="newer than this code"):
        load_yaml(yaml.safe_dump(newer))


def test_a_different_major_version_is_refused():
    with pytest.raises(SchemaVersionError, match="different major version"):
        load_yaml(yaml.safe_dump(dict(_shipped(), schema_version="2.0")))


def test_an_unparseable_version_is_refused_not_ignored():
    with pytest.raises(SchemaVersionError, match="not a MAJOR.MINOR"):
        load_yaml(yaml.safe_dump(dict(_shipped(), schema_version="banana")))


def test_version_refusals_name_the_version_and_what_to_do():
    """A refusal a person can act on. "value_error" is not one."""
    with pytest.raises(SchemaVersionError) as exc:
        load_yaml(yaml.safe_dump(dict(_shipped(), schema_version="2.0")))

    message = str(exc.value)
    assert "2.0" in message and CURRENT_SCHEMA_VERSION in message
    assert "python -m cua.artifacts" in message  # how to fix it
