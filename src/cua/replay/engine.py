"""Deterministic replay: executes a CapabilityArtifact's steps with no LLM
in the loop, using the exact same stable-locator resolution and guardrail
policy discovery uses. This is the path an AI agent would trigger in
production -- reliable and cheap because there's no model call per step.
"""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cua.artifacts.schema import CapabilityArtifact, Target
from cua.core.models import Action, ActionType
from cua.guardrails.policy import PolicyEngine
from cua.handoff.handler import HandoffHandler
from cua.handoff.models import EscalationRecord, EscalationRequest, HandoffAction, HandoffDecision
from cua.replay.models import RecoveryEvent, ReplayError, ReplayOutcome, ReplayResult
from cua.replay.render import render_action
from cua.surface.locator import LocatorResolutionError, resolve_with_wait
from cua.surface.playwright_surface import PlaywrightSurface

MAX_RECOVERY_ATTEMPTS = 1  # one bounded retry per step -- never loop indefinitely


class _ReplayEnd(Exception):
    """Internal control-flow signal: a terminal ReplayResult was reached
    before all steps ran (NEEDS_HUMAN or HARD_FAILURE). Caught once, at the
    top of run().
    """

    def __init__(self, result: ReplayResult):
        self.result = result


class _SkipToEnding(Exception):
    """A human resolved things directly in the live session (or approved
    continuing) -- don't blindly keep running the rest of the scripted
    steps against what might now be a different page state; go straight to
    checking whether we've reached the checkpoint or a business outcome.
    """


@dataclass
class _RunContext:
    artifact: CapabilityArtifact
    surface: PlaywrightSurface
    inputs: dict[str, str]
    secrets: dict[str, str]
    human_approved: bool
    handoff: HandoffHandler | None
    rendered_steps: list[Action] = field(default_factory=list)
    recovery_events: list[RecoveryEvent] = field(default_factory=list)
    escalations: list[EscalationRecord] = field(default_factory=list)
    escalated_outcomes: set[str] = field(default_factory=set)


class ReplayEngine:
    def __init__(
        self,
        policy: PolicyEngine | None = None,
        headless: bool = True,
        evidence_dir: str | Path = "evidence/runs",
        handoff: HandoffHandler | None = None,
    ):
        self.policy = policy or PolicyEngine()
        self.headless = headless
        self.evidence_dir = evidence_dir
        self.handoff = handoff

    def run(
        self,
        artifact: CapabilityArtifact,
        inputs: dict[str, str] | None = None,
        secrets: dict[str, str] | None = None,
        human_approved: bool = False,
    ) -> ReplayResult:
        inputs = inputs or {}
        secrets = secrets or {}
        _validate_params(artifact, inputs, secrets)

        surface = PlaywrightSurface(
            base_url=f"http://{artifact.target_domain}",
            policy=self.policy,
            headless=self.headless,
            evidence_dir=self.evidence_dir,
        )
        ctx = _RunContext(
            artifact=artifact,
            surface=surface,
            inputs=inputs,
            secrets=secrets,
            human_approved=human_approved,
            handoff=self.handoff,
        )
        ctx.rendered_steps = [render_action(a, inputs, secrets) for a in artifact.steps]

        steps_executed = 0
        try:
            for idx, action in enumerate(ctx.rendered_steps):
                self._execute_step(ctx, idx, action)
                steps_executed = idx + 1
            result = self._resolve_ending(ctx)
        except _SkipToEnding:
            result = self._resolve_ending(ctx)
        except _ReplayEnd as end:
            result = end.result
        finally:
            surface.close()

        result.steps_executed = steps_executed
        result.recovery_events = ctx.recovery_events
        result.escalations = ctx.escalations
        return result

    # -- human handoff -----------------------------------------------------

    def _escalate(
        self, ctx: _RunContext, step_index: int | None, reason: str, business_outcome: str | None = None
    ) -> HandoffDecision:
        """Routes an intervention request to ctx.handoff and hands it the
        SAME live surface -- control genuinely leaves this loop until
        escalate() returns; nothing here touches the surface again first.
        """
        observation = _safe_observe(ctx.surface)
        request = EscalationRequest(
            capability_id=ctx.artifact.id,
            capability_title=ctx.artifact.title,
            step_index=step_index,
            reason=reason,
            business_outcome=business_outcome,
            current_url=observation.url if observation else ctx.surface.page.url,
            screenshot_path=observation.screenshot_path if observation else None,
        )
        decision = ctx.handoff.escalate(request, ctx.surface)
        ctx.escalations.append(
            EscalationRecord(
                step_index=step_index,
                reason=reason,
                decision=decision.action,
                operator_note=decision.operator_note,
                resumed_via=type(ctx.handoff).__name__,
                requested_at=request.requested_at,
            )
        )
        return decision

    # -- step execution, with bounded recovery ---------------------------

    def _execute_step(self, ctx: _RunContext, idx: int, action: Action) -> None:
        # Checked before attempting the step, not just after it fails: a
        # locator's own robustness fallbacks (e.g. TABLE_POSITION, which
        # picks a row by position regardless of *which* row is ambiguous)
        # can make a step resolve successfully even when the page is
        # already showing a known business outcome -- "click View" happily
        # clicks row 1 even when the search legitimately came back
        # ambiguous. timeout_ms=0: a single, instant check, not a wait --
        # this runs before every step, so it must stay cheap on the normal
        # (nothing matches) path.
        outcome_result = self._check_business_outcomes(ctx, timeout_ms=0)
        if outcome_result is not None:
            raise _ReplayEnd(outcome_result)

        result = None
        for attempt in range(MAX_RECOVERY_ATTEMPTS + 1):
            result = ctx.surface.act(action, human_approved=ctx.human_approved)

            if result.blocked:
                if ctx.handoff is None:
                    raise _ReplayEnd(
                        ReplayResult(
                            outcome=ReplayOutcome.NEEDS_HUMAN,
                            capability_id=ctx.artifact.id,
                            error=ReplayError(
                                step_index=idx,
                                expected="action allowed by policy",
                                observed=result.policy_reason or "blocked",
                                message=(
                                    f"Step {idx} ({_describe_action(action)}) is blocked by policy and requires "
                                    f"human approval: {result.policy_reason}"
                                ),
                            ),
                        )
                    )
                decision = self._escalate(
                    ctx, idx, reason=f"blocked by policy ({_describe_action(action)}): {result.policy_reason}"
                )
                if decision.action == HandoffAction.APPROVE_AND_RETRY:
                    retry = ctx.surface.act(action, human_approved=True)
                    if retry.success:
                        self._dismiss_pending_dialog(ctx, idx)
                        return
                    raise _ReplayEnd(
                        ReplayResult(
                            outcome=ReplayOutcome.HARD_FAILURE,
                            capability_id=ctx.artifact.id,
                            error=ReplayError(
                                step_index=idx,
                                expected=_describe_action(action),
                                observed=retry.error or "failed even after human approval",
                                message=f"Step {idx} failed even after human approval: {retry.error}",
                            ),
                        )
                    )
                if decision.action == HandoffAction.MANUAL_RESOLVED:
                    raise _SkipToEnding()
                raise _ReplayEnd(  # ABANDON
                    ReplayResult(
                        outcome=ReplayOutcome.NEEDS_HUMAN,
                        capability_id=ctx.artifact.id,
                        error=ReplayError(
                            step_index=idx,
                            expected="action allowed by policy",
                            observed=result.policy_reason or "blocked",
                            message=f"Step {idx} blocked by policy; operator abandoned: {decision.operator_note}",
                        ),
                    )
                )
            if result.success:
                # A dialog can appear as a *side effect* of a successful
                # action (the click itself worked; the page's onload handler
                # fires the popup afterward) -- not just as a reason a step
                # failed. Left unhandled, it blocks every later locator
                # resolution (checkpoint/output extraction hangs), since
                # nothing else in the loop would ever think to check again.
                self._dismiss_pending_dialog(ctx, idx)
                return

            if attempt >= MAX_RECOVERY_ATTEMPTS:
                break

            observation = result.observation or _safe_observe(ctx.surface)
            if observation is not None and observation.dialog_message:
                dismiss = ctx.surface.act(Action(type=ActionType.DISMISS_DIALOG, value="accept"))
                ctx.recovery_events.append(
                    RecoveryEvent(step_index=idx, kind="dialog_dismissed", detail=observation.dialog_message)
                )
                if dismiss.success:
                    continue  # retry the same action
                break

            if observation is not None and _looks_like_session_expired(observation, ctx.artifact, idx):
                self._reauthenticate(ctx, idx)
                ctx.recovery_events.append(
                    RecoveryEvent(step_index=idx, kind="session_reauthenticated", detail=f"redirected to {observation.url}")
                )
                continue  # retry the same action

            break  # nothing recoverable matched

        # A step can legitimately fail to resolve because the flow diverged
        # onto a known business outcome mid-way -- e.g. there's no "View"
        # result to click because the search came back empty. Check before
        # giving up: a business outcome here is a real answer, not a bug.
        outcome_result = self._check_business_outcomes(ctx)
        if outcome_result is not None:
            raise _ReplayEnd(outcome_result)

        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    step_index=idx,
                    expected=_describe_action(action),
                    observed=result.error or "action did not succeed",
                    message=f"Step {idx} failed: {result.error or 'unknown error'}",
                ),
            )
        )

    def _reauthenticate(self, ctx: _RunContext, idx: int) -> None:
        """Re-runs the step prefix that got us this far, to re-establish a
        session a runtime timeout silently dropped -- not a guess, the exact
        same steps that worked the first time in this same replay.
        """
        for prefix_idx, prefix_action in enumerate(ctx.rendered_steps[:idx]):
            r = ctx.surface.act(prefix_action, human_approved=ctx.human_approved)
            if not r.success and not r.blocked:
                raise _ReplayEnd(
                    ReplayResult(
                        outcome=ReplayOutcome.HARD_FAILURE,
                        capability_id=ctx.artifact.id,
                        error=ReplayError(
                            step_index=idx,
                            expected="session re-authentication to succeed",
                            observed=r.error or "unknown error",
                            message=(
                                f"Session recovery failed while replaying step {prefix_idx} "
                                f"({_describe_action(prefix_action)}): {r.error}"
                            ),
                        ),
                    )
                )
            if r.success:
                self._dismiss_pending_dialog(ctx, idx)

    def _dismiss_pending_dialog(self, ctx: _RunContext, idx: int) -> None:
        if ctx.surface._pending_dialog is None:
            return
        message = ctx.surface._pending_dialog.message
        dismiss = ctx.surface.act(Action(type=ActionType.DISMISS_DIALOG, value="accept"))
        if dismiss.success:
            ctx.recovery_events.append(RecoveryEvent(step_index=idx, kind="dialog_dismissed", detail=message))

    # -- ending: checkpoint, outputs, or a known business outcome ---------

    def _resolve_ending(self, ctx: _RunContext) -> ReplayResult:
        self._dismiss_pending_dialog(ctx, len(ctx.rendered_steps) - 1)  # defensive: don't resolve against a blocked page
        if _target_resolves(ctx.surface, ctx.artifact.checkpoint):
            outputs: dict[str, str] = {}
            for spec in ctx.artifact.outputs:
                value = _extract_text(ctx.surface, spec.target)
                if value is None:
                    raise _ReplayEnd(
                        ReplayResult(
                            outcome=ReplayOutcome.HARD_FAILURE,
                            capability_id=ctx.artifact.id,
                            error=ReplayError(
                                step_index=None,
                                expected=f"output '{spec.name}' locator to resolve",
                                observed="not found",
                                message=f"Checkpoint matched but output '{spec.name}' could not be extracted.",
                            ),
                        )
                    )
                outputs[spec.name] = value
            needed_help = ctx.recovery_events or ctx.escalations
            outcome = ReplayOutcome.RECOVERED if needed_help else ReplayOutcome.SUCCESS
            return ReplayResult(outcome=outcome, capability_id=ctx.artifact.id, outputs=outputs)

        outcome_result = self._check_business_outcomes(ctx)
        if outcome_result is not None:
            return outcome_result

        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    step_index=None,
                    expected=ctx.artifact.checkpoint_description,
                    observed="neither the checkpoint nor any known business outcome matched",
                    message="Reached the end of the recorded steps in an unrecognized state.",
                ),
            )
        )

    def _check_business_outcomes(self, ctx: _RunContext, timeout_ms: int = 3000) -> ReplayResult | None:
        for spec in ctx.artifact.business_outcomes:
            if not _target_resolves(ctx.surface, spec.detect, timeout_ms=timeout_ms):
                continue

            if not spec.requires_human:
                return ReplayResult(
                    outcome=ReplayOutcome.BUSINESS_OUTCOME,
                    capability_id=ctx.artifact.id,
                    business_outcome=spec.name,
                    business_outcome_description=spec.description,
                )

            # An ambiguous/risky outcome: don't just report it, offer a
            # human the chance to resolve it in the live session -- but only
            # once per outcome per run, so a human declining (or the page
            # genuinely not changing) can't loop forever.
            if ctx.handoff is None or spec.name in ctx.escalated_outcomes:
                return ReplayResult(
                    outcome=ReplayOutcome.NEEDS_HUMAN,
                    capability_id=ctx.artifact.id,
                    business_outcome=spec.name,
                    business_outcome_description=spec.description,
                )

            ctx.escalated_outcomes.add(spec.name)
            decision = self._escalate(
                ctx, step_index=None, reason=f"outcome '{spec.name}' requires human review: {spec.description}", business_outcome=spec.name
            )
            if decision.action == HandoffAction.ABANDON:
                return ReplayResult(
                    outcome=ReplayOutcome.NEEDS_HUMAN,
                    capability_id=ctx.artifact.id,
                    business_outcome=spec.name,
                    business_outcome_description=spec.description,
                )
            raise _SkipToEnding()  # human acted (or approved); re-evaluate the page from scratch
        return None


def _validate_params(artifact: CapabilityArtifact, inputs: dict[str, str], secrets: dict[str, str]) -> None:
    missing_inputs = [spec.name for spec in artifact.inputs if spec.name not in inputs]
    if missing_inputs:
        raise ValueError(f"missing required inputs: {', '.join(missing_inputs)}")
    missing_secrets = [spec.name for spec in artifact.secrets if spec.name not in secrets]
    if missing_secrets:
        raise ValueError(f"missing required secrets: {', '.join(missing_secrets)}")


def _safe_observe(surface: PlaywrightSurface):
    try:
        return surface.observe()
    except Exception:
        return None


def _looks_like_session_expired(observation, artifact: CapabilityArtifact, idx: int) -> bool:
    if idx == 0:
        return False
    # Compare paths, not full URLs: a real app's "your session expired,
    # please sign in again" redirect very plausibly carries a query string
    # (our own fake app's does -- "?expired=1") that would never match the
    # bare entry_url on a naive full-URL comparison.
    current_path = urlsplit(observation.url).path.rstrip("/")
    entry_path = urlsplit(artifact.entry_url).path.rstrip("/")
    return current_path == entry_path or current_path.endswith("/login")




def _target_resolves(surface: PlaywrightSurface, target: Target, timeout_ms: int = 3000) -> bool:
    try:
        scope = surface.scope_for(target.frame)
        resolve_with_wait(scope, target.candidates, timeout_ms=timeout_ms)
        return True
    except LocatorResolutionError:
        return False


def _extract_text(surface: PlaywrightSurface, target: Target, timeout_ms: int = 3000) -> str | None:
    try:
        scope = surface.scope_for(target.frame)
        locator, _candidate = resolve_with_wait(scope, target.candidates, timeout_ms=timeout_ms)
    except LocatorResolutionError:
        return None
    text = locator.inner_text()
    return text.strip() if text else None


def _describe_action(action: Action) -> str:
    if action.type == ActionType.NAVIGATE:
        return f"navigate to {action.url}"
    if action.target and action.target.candidates:
        c = action.target.candidates[0]
        frame_bit = f" in frame '{action.target.frame}'" if action.target.frame else ""
        return f"{action.type.value} on {c.strategy.value}={c.value!r}{frame_bit}"
    return action.type.value
