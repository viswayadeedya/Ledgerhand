"""Turns a discovery run's RecordedStep log into a CapabilityArtifact.

Deliberately a pure function over data the discovery loop already produced
(cua.agent.discovery.DiscoveryResult) -- it doesn't touch the browser, the
model, or the guardrail policy. That keeps "what happened" (Part 4) and
"what we're willing to call reusable" (this file) as separate concerns: a
human can look at a run's log, decide these particular values are the real
parameters, and build the artifact without re-running anything.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cua.core.models import (
    Action,
    ActionType,
    LocatorCandidate,
    LocatorStrategy,
    Observation,
    RecordedStep,
    RiskLevel,
    Target,
)
from cua.artifacts.schema import (
    ArtifactStep,
    BusinessOutcomeSpec,
    CapabilityArtifact,
    InputSpec,
    OutputSpec,
    OutputType,
    ProvenanceInfo,
    SecretSpec,
    StepRisk,
)
from cua.guardrails.policy import PolicyEngine

# Only these action types have a stable, replayable locator. TYPE/KEY act on
# "whatever has focus" with no target at all, so they can't be replayed
# deterministically -- if discovery relied on one to reach the goal, that
# path isn't recordable and the run should be redone (see DECISIONS.md).
_REPLAYABLE_TYPES = {
    ActionType.NAVIGATE,
    ActionType.CLICK,
    ActionType.SUBMIT,
    ActionType.FILL,
    ActionType.SELECT,
    ActionType.WAIT,
    ActionType.DISMISS_DIALOG,
}

_SECRET_SENTINELS = {"{{secrets.password}}", "***REDACTED***"}


class ArtifactBuildError(RuntimeError):
    pass


def build_artifact(
    steps: list[RecordedStep],
    *,
    capability_id: str,
    title: str,
    description: str,
    target_domain: str,
    entry_url: str,
    discovery_model: str,
    inputs: dict[str, str] | None = None,
    input_descriptions: dict[str, str] | None = None,
    secret_names: list[str] | None = None,
    secret_values: dict[str, str] | None = None,
    output_descriptions: dict[str, str] | None = None,
    output_values: dict[str, str] | None = None,
    output_assertions: dict[str, str] | None = None,
    output_types: dict[str, str] | None = None,
    sensitive_outputs: list[str] | None = None,
    checkpoint_text: str = "",
    source_run_log: str | None = None,
    version: int = 1,
    policy: PolicyEngine | None = None,
) -> CapabilityArtifact:
    inputs = inputs or {}
    input_descriptions = input_descriptions or {}
    secret_names = secret_names or []
    secret_values = secret_values or {}
    output_descriptions = output_descriptions or {}
    output_values = output_values or {}
    output_assertions = output_assertions or {}
    output_types = output_types or {}
    sensitive_outputs = sensitive_outputs or []

    replayable = [s for s in steps if s.action is not None and s.action.type in _REPLAYABLE_TYPES]
    if not replayable:
        raise ArtifactBuildError("no replayable steps found -- every action was TYPE/KEY or unresolved")

    policy = policy or PolicyEngine()
    parameterized_steps = _build_steps(steps, inputs, secret_values, policy)

    final_observation = _last_observation(steps)
    if final_observation is None:
        raise ArtifactBuildError("no observation found in the run to extract outputs/checkpoint from")

    outputs: list[OutputSpec] = []
    for name, value in output_values.items():
        target = find_target_for_text(final_observation, value, prefer_table_position=True)
        if target is None:
            raise ArtifactBuildError(
                f"could not find an element matching output '{name}'={value!r} in the final observation"
            )
        outputs.append(
            OutputSpec(
                name=name,
                type=OutputType(output_types.get(name, OutputType.STRING.value)),
                description=output_descriptions.get(name, ""),
                target=target,
                must_equal=output_assertions.get(name),
                sensitive=name in sensitive_outputs,
            )
        )

    if not checkpoint_text:
        raise ArtifactBuildError("checkpoint_text is required -- a human must assert what proves success")
    checkpoint_target = find_target_for_text(final_observation, checkpoint_text)
    if checkpoint_target is None:
        raise ArtifactBuildError(f"checkpoint text {checkpoint_text!r} not found in the final observation")

    return CapabilityArtifact(
        id=capability_id,
        version=version,
        title=title,
        description=description,
        target_domain=target_domain,
        entry_url=entry_url,
        inputs=[
            InputSpec(name=name, description=input_descriptions.get(name, ""), example=value)
            for name, value in inputs.items()
        ],
        secrets=[SecretSpec(name=name) for name in secret_names],
        outputs=outputs,
        steps=parameterized_steps,
        checkpoint=checkpoint_target,
        checkpoint_description=f'Page shows text matching "{checkpoint_text}".',
        provenance=ProvenanceInfo(
            discovered_at=datetime.now(timezone.utc).isoformat(),
            discovery_model=discovery_model,
            source_run_log=source_run_log,
        ),
    )


def _build_steps(
    steps: list[RecordedStep],
    inputs: dict[str, str],
    secret_values: dict[str, str],
    policy: PolicyEngine,
) -> list[ArtifactStep]:
    """Walks the whole run, not just the replayable steps, so each step can
    be compared against the page as it stood immediately before it ran --
    which is what makes "where did this step take us" answerable.
    """
    artifact_steps: list[ArtifactStep] = []
    before: Observation | None = None
    for recorded in steps:
        action = recorded.action
        if action is not None and action.type in _REPLAYABLE_TYPES:
            artifact_steps.append(_to_artifact_step(recorded, before, inputs, secret_values, policy))
        if recorded.result is not None and recorded.result.observation is not None:
            before = recorded.result.observation
    return artifact_steps


def _to_artifact_step(
    recorded: RecordedStep,
    before: Observation | None,
    inputs: dict[str, str],
    secret_values: dict[str, str],
    policy: PolicyEngine,
) -> ArtifactStep:
    action = _parameterize(recorded.action, inputs, secret_values)
    risk, note = _classify(recorded, before, policy)
    fields = action.model_dump()
    # Keep whatever discovery recorded if it said anything; only fill the
    # blank. The model's own account of why it did something is better
    # context than a generated sentence when it exists.
    fields["description"] = action.description or _describe(action, recorded)
    return ArtifactStep(**fields, risk=risk, risk_note=note)


def _parameterize(action: Action, inputs: dict[str, str], secret_values: dict[str, str]) -> Action:
    if action.value is None:
        return action
    new_value = _parameterize_value(action.value, inputs, secret_values)
    if new_value == action.value:
        return action
    return action.model_copy(update={"value": new_value})


# -- plain-English descriptions -------------------------------------------


def _describe(action: Action, recorded: RecordedStep) -> str:
    """Says what the step does, in the terms a person would use.

    Built from the locator that actually resolved during discovery, not the
    raw selector: "Click the Sign On button" is reviewable, 'Click
    tr:nth-child(3) > td > a' is not. Everything here comes from the run
    log -- nothing is inferred about intent, because the recorder doesn't
    know it and guessing would put a confident wrong sentence next to a
    step a human is supposed to be checking.
    """
    what = _human_target(action, recorded)
    if action.type == ActionType.NAVIGATE:
        return f"Go to {_path_of(action.url)}."
    if action.type == ActionType.CLICK:
        return f"Click {what}." if what else "Click the recorded element."
    if action.type == ActionType.SUBMIT:
        return f"Submit {what}." if what else "Submit the form."
    if action.type == ActionType.FILL:
        return f"Enter {_describe_value(action.value)} into {what}." if what else "Fill the recorded field."
    if action.type == ActionType.SELECT:
        return f"Choose {_describe_value(action.value)} in {what}." if what else "Make the recorded selection."
    if action.type == ActionType.WAIT:
        return "Wait for the page to settle."
    if action.type == ActionType.DISMISS_DIALOG:
        return "Dismiss the dialog the page opens."
    return f"{action.type.value} step."


def _describe_value(value: str | None) -> str:
    """Names a value without reproducing it.

    A parameterized step says "the member_id input"; a literal says "this
    fixed value". Either way the artifact never gains a sentence with a
    real credential or member ID in it -- the templates exist precisely so
    those aren't in the file.
    """
    if not value:
        return "an empty value"
    m = re.match(r"^\{\{(inputs|secrets)\.(\w+)\}\}$", value)
    if m:
        kind, name = m.groups()
        return f"the {name} {'input' if kind == 'inputs' else 'secret'}"
    return f"the recorded value {value!r}"


def _human_target(action: Action, recorded: RecordedStep) -> str:
    """The most readable description of what the step acted on.

    Prefers the strategy that actually resolved at discovery time, since
    that's the one replay will try first and the one a reviewer can check
    against the real page.
    """
    result = recorded.result
    if result is not None and result.resolved_strategy is not None and result.resolved_value:
        strategy, value = result.resolved_strategy, result.resolved_value
        if strategy == LocatorStrategy.ROLE:
            return f'the "{value}" {action.target.candidates[0].role or "control"}' if action.target else f'"{value}"'
        if strategy == LocatorStrategy.LABEL:
            return f'the "{value}" field'
        if strategy == LocatorStrategy.TEXT:
            return f'the "{value}" link or text'
        if strategy == LocatorStrategy.TABLE_LABEL:
            label, _, _col = value.partition(",col=")
            return f'the "{label.removeprefix("label=")}" field'
        if strategy == LocatorStrategy.TABLE_POSITION:
            # "row=1,col=1" tells a reviewer nothing they can check against
            # the real page. The cell to its left usually holds the form's
            # own label for it, so use that when the run actually captured
            # one and fall back to the coordinates when it didn't.
            label = _label_for_position(recorded, value)
            return f'the "{label}" field' if label else f"the table cell at {value}"
        return f"the element matching {value!r}"


def _label_for_position(recorded: RecordedStep, position: str) -> str | None:
    """The form's own label for the cell at `position`, for describing it.

    Looks for the label cell directly rather than via the element being
    filled, because that element is often not in the observation at all --
    a password input is deliberately never scanned for its value, so
    nothing is recorded at its coordinates.

    Checks left first (`User ID: | [input]`) then above
    (`Member ID or Last Name:` / `[input]`), which covers both layouts this
    app uses. Only used for the human-readable description; the locator
    itself is unaffected by what this returns.
    """
    observation = recorded.result.observation if recorded.result is not None else None
    if observation is None:
        return None
    m = re.match(r"^row=(\d+),col=(\d+)$", position)
    if not m:
        return None
    row, col = int(m.group(1)), int(m.group(2))
    frame = recorded.action.target.frame if recorded.action.target else None

    neighbours = [(row, col - 1), (row - 1, col)]
    for want_row, want_col in neighbours:
        if want_row < 0 or want_col < 0:
            continue
        for element in observation.elements:
            if (
                element.frame == frame
                and element.table_row == want_row
                and element.table_col == want_col
                and element.tag not in _NON_TEXT_EXTRACTABLE_TAGS
            ):
                label = (element.text or "").strip().rstrip(":").strip()
                if label:
                    return label
    return None
    if action.target and action.target.candidates:
        first = action.target.candidates[0]
        return f"the element matching {first.value!r}"
    return ""


def _path_of(url: str | None) -> str:
    if not url:
        return "the recorded URL"
    return urlsplit(url).path or url


# -- risk labels -----------------------------------------------------------


def _classify(
    recorded: RecordedStep, before: Observation | None, policy: PolicyEngine
) -> tuple[StepRisk, str]:
    """Labels a step using the destination discovery actually reached.

    A click's risk can't be judged from the click itself -- the selector
    says nothing about where it goes, and "*/new-subaccount/commit" is a
    route, not a button. But the run log recorded where the page went
    afterwards, so that is what gets classified, through the same
    PolicyEngine replay uses. When the log captured no destination the
    label is UNVERIFIED: "nobody checked" is the honest answer, and
    quietly labelling an unchecked step safe is the dangerous one.

    This is review metadata only. Replay still re-evaluates every action
    live, so a step labelled safe here that navigates somewhere risky at
    replay time is still blocked then.
    """
    action = recorded.action
    if action.type == ActionType.NAVIGATE and action.url:
        return _risk_of([action.url], policy, f"declared destination {_path_of(action.url)}")

    after = recorded.result.observation if recorded.result is not None else None
    if after is None:
        return StepRisk.UNVERIFIED, "no page state was captured at discovery; replay still checks it live"

    moved = _destinations_reached(before, after)
    if moved:
        where = ", ".join(_path_of(url) for url in moved)
        return _risk_of(moved, policy, f"destination {where} observed at discovery")

    # The step changed nothing about where we are (typing into a field, for
    # instance). Say so rather than reporting the current URL as a
    # "destination" it never travelled to.
    return _risk_of([after.url], policy, f"no navigation; stayed on {_path_of(after.url)} at discovery")


def _risk_of(urls: list[str], policy: PolicyEngine, note: str) -> tuple[StepRisk, str]:
    """Riskiest wins. A step that opened several frames is as risky as the
    riskiest place it landed.
    """
    decisions = [policy.evaluate(Action(type=ActionType.NAVIGATE, url=url)) for url in urls if url]
    if not decisions:
        return StepRisk.UNVERIFIED, note
    risk = StepRisk.RISKY if any(d.risk == RiskLevel.RISKY for d in decisions) else StepRisk.SAFE
    return risk, f"{note}; re-checked at replay"


def _destinations_reached(before: Observation | None, after: Observation) -> list[str]:
    """Which URLs this step actually navigated to.

    Comparing before/after rather than reading "the main frame" keeps this
    honest on a frameset app: the control that was clicked usually lives in
    one frame (the nav) while the navigation happens in another (the
    content), so the frame the step *acted in* is the wrong answer. What
    changed is the right one, and it needs no knowledge of this particular
    app's frame names.
    """
    if before is None:
        return [after.url] if after.url else []
    if after.url and after.url != before.url:
        # The whole page moved; frame changes underneath it are consequences.
        return [after.url]
    return [url for name, url in after.frames.items() if before.frames.get(name) != url]


def _parameterize_value(value: str, inputs: dict[str, str], secret_values: dict[str, str]) -> str:
    if value in _SECRET_SENTINELS:
        return "{{secrets.password}}"
    for name, literal in inputs.items():
        if literal and value == literal:
            return f"{{{{inputs.{name}}}}}"
    for name, literal in secret_values.items():
        if literal and value == literal:
            return f"{{{{secrets.{name}}}}}"
    return value


def _last_observation(steps: list[RecordedStep]) -> Observation | None:
    for step in reversed(steps):
        if step.result is not None and step.result.observation is not None:
            return step.result.observation
    return None


def _normalize(text: str) -> str:
    return text.strip().lower().lstrip("$").replace(",", "")


# A TEXT-strategy locator reads via inner_text() at replay time, which is
# always empty for form controls (their content is a .value, not rendered
# text) -- a candidate built from one of these could never resolve, no
# matter how exactly it matched during recording. Skip them here so a
# broken locator can't be produced in the first place, rather than relying
# on whoever builds the artifact to notice and exclude it.
_NON_TEXT_EXTRACTABLE_TAGS = {"input", "select", "textarea"}


def _label_cell_text(observation: Observation, value_element) -> str | None:
    """The cell immediately to the left of a value, in the same row and
    frame -- "Savings Balance" for the cell holding "$2340.18".

    Read out of the same captured observation as everything else rather
    than inferred: if the page doesn't actually have a label cell there,
    this returns None and the locator falls back to position alone.
    """
    if value_element.table_col is None or value_element.table_col < 1:
        return None
    for element in observation.elements:
        if (
            element.frame == value_element.frame
            and element.table_row == value_element.table_row
            and element.table_col == value_element.table_col - 1
        ):
            label = (element.text or "").strip()
            return label or None
    return None


def find_target_for_text(observation: Observation, text: str, prefer_table_position: bool = False) -> Target | None:
    """Finds an element matching `text` and builds a locator for it.

    `prefer_table_position` matters a lot for outputs: a TEXT candidate
    matches the *value itself* ("$2340.18"), which is exactly wrong for a
    value that's supposed to vary per invocation -- replay with a different
    member would never find "$2340.18" again, because a different balance is
    now there. A TABLE_POSITION candidate matches the *cell*, independent of
    whatever text currently sits in it, so replay re-reads whatever's really
    there each time. Checkpoints don't have this problem (checkpoint text is
    usually a static label, not a value that changes), so they keep
    preferring TEXT, which is also the more human-readable strategy to
    review.
    """
    needle = _normalize(text)
    if not needle:
        return None
    for element in observation.elements:
        if element.tag in _NON_TEXT_EXTRACTABLE_TAGS:
            continue
        haystack = _normalize(element.text or "")
        if haystack and (haystack == needle or needle in haystack):
            has_position = element.table_row is not None and element.table_col is not None
            if prefer_table_position and has_position:
                # TEXT is deliberately NOT included as a fallback here: it
                # would match this exact value, which is wrong precisely
                # when the value is supposed to change (a different
                # member's balance). Falling back to it would silently
                # degrade a correct position-based locator into a broken
                # value-based one instead of failing loudly.
                candidates = []
                label = _label_cell_text(observation, element)
                if label:
                    # Anchored to what the row says, so an inserted row
                    # above it doesn't silently shift which cell we read.
                    candidates.append(
                        LocatorCandidate(
                            strategy=LocatorStrategy.TABLE_LABEL,
                            value=f"label={label},col={element.table_col}",
                        )
                    )
                candidates.append(
                    LocatorCandidate(
                        strategy=LocatorStrategy.TABLE_POSITION,
                        value=f"row={element.table_row},col={element.table_col}",
                    )
                )
            else:
                # Use the caller's own search phrase, not the full matched
                # element text: for a checkpoint/business-outcome, that
                # phrase is a deliberately stable substring ("No member
                # found"), while the element's full text can carry a
                # variable part ("No member found matching \"99999\".") that
                # would only ever match this one exact case again. Playwright
                # get_by_text() matches substrings by default, so the
                # shorter, stable phrase still finds the element correctly.
                candidates = [LocatorCandidate(strategy=LocatorStrategy.TEXT, value=text.strip())]
            return Target(frame=element.frame, candidates=candidates)
    return None


def add_business_outcome(
    artifact: CapabilityArtifact,
    observation: Observation,
    *,
    name: str,
    description: str,
    detect_text: str,
    requires_human: bool = False,
) -> CapabilityArtifact:
    """Attaches a named, recognizable non-success ending to an existing
    artifact. Takes a real captured Observation (from actually driving the
    surface into that state, not a guess) so `detect` is a locator built the
    same way checkpoint/outputs are -- see scripts/ for how this project's
    "member not found" outcome was captured.
    """
    target = find_target_for_text(observation, detect_text)
    if target is None:
        raise ArtifactBuildError(f"business outcome text {detect_text!r} not found in the given observation")
    outcome = BusinessOutcomeSpec(name=name, description=description, detect=target, requires_human=requires_human)
    others = [o for o in artifact.business_outcomes if o.name != name]
    return artifact.model_copy(update={"business_outcomes": [*others, outcome]})
