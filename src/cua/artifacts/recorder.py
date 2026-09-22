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
from cua.artifacts.schema import CapabilityArtifact, InputSpec, OutputSpec, ProvenanceInfo, SecretSpec

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
        target = _find_target_for_text(final_observation, value)
        if target is None:
            raise ArtifactBuildError(
                f"could not find an element matching output '{name}'={value!r} in the final observation"
            )
        outputs.append(
            OutputSpec(name=name, type="string", description=output_descriptions.get(name, ""), target=target)
        )

    if not checkpoint_text:
        raise ArtifactBuildError("checkpoint_text is required -- a human must assert what proves success")
    checkpoint_target = _find_target_for_text(final_observation, checkpoint_text)
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


def _find_target_for_text(observation: Observation, text: str) -> Target | None:
    needle = _normalize(text)
    if not needle:
        return None
    for element in observation.elements:
        haystack = _normalize(element.text or "")
        if haystack and (haystack == needle or needle in haystack):
            return Target(
                frame=element.frame,
                candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value=(element.text or "").strip())],
            )
    return None
