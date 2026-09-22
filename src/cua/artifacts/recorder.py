"""Turns a discovery run's RecordedStep log into a CapabilityArtifact.

Deliberately a pure function over data the discovery loop already produced
(cua.agent.discovery.DiscoveryResult) -- it doesn't touch the browser, the
model, or the guardrail policy. That keeps "what happened" (Part 4) and
"what we're willing to call reusable" (this file) as separate concerns: a
human can look at a run's log, decide these particular values are the real
parameters, and build the artifact without re-running anything.
"""

from datetime import datetime, timezone

from cua.core.models import (
    Action,
    ActionType,
    LocatorCandidate,
    LocatorStrategy,
    Observation,
    RecordedStep,
    Target,
)
from cua.artifacts.schema import (
    BusinessOutcomeSpec,
    CapabilityArtifact,
    InputSpec,
    OutputSpec,
    ProvenanceInfo,
    SecretSpec,
)

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
    checkpoint_text: str = "",
    source_run_log: str | None = None,
    version: int = 1,
) -> CapabilityArtifact:
    inputs = inputs or {}
    input_descriptions = input_descriptions or {}
    secret_names = secret_names or []
    secret_values = secret_values or {}
    output_descriptions = output_descriptions or {}
    output_values = output_values or {}

    replayable = [s.action for s in steps if s.action is not None and s.action.type in _REPLAYABLE_TYPES]
    if not replayable:
        raise ArtifactBuildError("no replayable steps found -- every action was TYPE/KEY or unresolved")

    parameterized_steps = [_parameterize(a, inputs, secret_values) for a in replayable]

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
            OutputSpec(name=name, type="string", description=output_descriptions.get(name, ""), target=target)
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


def _parameterize(action: Action, inputs: dict[str, str], secret_values: dict[str, str]) -> Action:
    if action.value is None:
        return action
    new_value = _parameterize_value(action.value, inputs, secret_values)
    if new_value == action.value:
        return action
    return action.model_copy(update={"value": new_value})


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
                candidates = [
                    LocatorCandidate(
                        strategy=LocatorStrategy.TABLE_POSITION,
                        value=f"row={element.table_row},col={element.table_col}",
                    )
                ]
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
