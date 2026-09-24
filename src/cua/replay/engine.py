"""Deterministic replay: executes a CapabilityArtifact's steps with no LLM
in the loop, using the exact same stable-locator resolution and guardrail
policy discovery uses. This is the path an AI agent would trigger in
production -- reliable and cheap because there's no model call per step.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cua.artifacts.schema import CapabilityArtifact, OutputSpec, Target, format_error
from cua.core.models import Action, ActionResult, ActionType
from cua.guardrails.policy import PolicyEngine
from cua.guardrails.redact import mask_partial
from cua.handoff.evidence import append_operator_record
from cua.handoff.handler import HandoffHandler
from cua.handoff.models import (
    ControlHolder,
    ControlSpan,
    EscalationRecord,
    EscalationRequest,
    HandoffAction,
    HandoffDecision,
)
from cua.handoff.recorder import OperatorSession
from cua.replay.models import FailureReason, RecoveryEvent, ReplayError, ReplayOutcome, ReplayResult
from cua.replay.render import RenderError, render_action, render_value
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
    control_timeline: list[ControlSpan] = field(default_factory=list)


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

        # Deliberately between validation and the browser: a value the
        # capability's own contract rejects can never become a correct
        # answer, so launching Chromium, signing in and running six steps
        # to find that out is pure waste -- and the failure it would
        # eventually produce ("element not found", probably) names the
        # symptom instead of the cause.
        rejected = _check_input_patterns(artifact, inputs)
        if rejected is not None:
            return rejected

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
        _take_control(ctx, ControlHolder.AUTOMATION, "replay started")

        steps_executed = 0
        result: ReplayResult | None = None
        try:
            # Nested rather than a sibling `except _SkipToEnding`, so that
            # _resolve_ending is always inside the try that catches
            # _ReplayEnd. Raised from a sibling except clause it would have
            # escaped run() entirely -- an internal control-flow exception
            # reaching the caller instead of a HARD_FAILURE -- which is what
            # happened when a human resolved a handoff and the page then
            # turned out to be in a state nothing recognised.
            try:
                for idx, action in enumerate(ctx.rendered_steps):
                    self._execute_step(ctx, idx, action)
                    steps_executed = idx + 1
            except _SkipToEnding:
                pass
            result = self._resolve_ending(ctx)
        except _ReplayEnd as end:
            result = end.result
        finally:
            # Taken here rather than at each of the ~eight places a failure
            # is constructed: this is the last moment before the browser
            # closes and nothing has happened since the failure, so it is
            # the failing state, and one call can't drift from seven others.
            if result is not None and result.error is not None:
                result.error.screenshot_path = _failure_screenshot(surface)
            surface.close()

        _close_control(ctx, "run ended")
        result.steps_executed = steps_executed
        result.recovery_events = ctx.recovery_events
        result.escalations = ctx.escalations
        result.control_timeline = ctx.control_timeline
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
        # The control transfer, made literal. The span opens before
        # escalate() is called and closes after it returns, which is
        # exactly the window in which this loop touches nothing -- so the
        # timeline isn't a description of the handoff, it's a recording of
        # the same fact the call stack already enforces.
        _take_control(ctx, ControlHolder.HUMAN, reason)
        with OperatorSession(ctx.surface) as session:
            try:
                decision = ctx.handoff.escalate(request, ctx.surface)
            finally:
                _take_control(ctx, ControlHolder.AUTOMATION, "operator handed control back")

        record = EscalationRecord(
            step_index=step_index,
            reason=reason,
            decision=decision.action,
            operator_note=decision.operator_note,
            resumed_via=type(ctx.handoff).__name__,
            requested_at=request.requested_at,
            operator_actions=session.actions,
        )
        ctx.escalations.append(record)

        # Also onto the escalation file the handler wrote, so the
        # intervention request and its answer stay one document -- that
        # file is what a reviewer opens, and "what did they actually do"
        # belongs next to "what were they asked".
        append_operator_record(
            getattr(ctx.handoff, "evidence_dir", self.evidence_dir),
            request,
            actions=session.actions,
            spans=ctx.control_timeline[-2:],
            capture_error=session.capture_error,
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
                                reason_code=FailureReason.POLICY_BLOCKED,
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
                                reason_code=_reason_for(retry),
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
                            reason_code=FailureReason.OPERATOR_ABANDONED,
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

        # A step that failed against a 5xx page failed *because* the app is
        # down. Reported as "element not found" it would send someone to
        # check a locator that is fine.
        self._check_app_error(ctx, step_index=idx)

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
                    reason_code=_reason_for(result),
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
                            reason_code=FailureReason.RECOVERY_FAILED,
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
            # Three passes rather than one, so the order outputs happen to
            # be declared in can't decide which problem gets reported: read
            # everything, then check we're on the right record at all, then
            # check the values look like what they claim to be.
            outputs: dict[str, str] = {}
            for spec in ctx.artifact.outputs:
                value, why_not = _extract_text(ctx.surface, spec.target)
                if value is None:
                    raise _ReplayEnd(
                        ReplayResult(
                            outcome=ReplayOutcome.HARD_FAILURE,
                            capability_id=ctx.artifact.id,
                            error=ReplayError(
                                reason_code=FailureReason.ELEMENT_NOT_FOUND,
                                step_index=None,
                                expected=f"output '{spec.name}' locator to resolve to exactly one element",
                                observed=why_not or "element resolved but had no text",
                                message=f"Checkpoint matched but output '{spec.name}' could not be extracted.",
                            ),
                        )
                    )
                outputs[spec.name] = value

            for spec in ctx.artifact.outputs:
                self._assert_identity(ctx, spec, outputs[spec.name])
            for spec in ctx.artifact.outputs:
                self._validate_format(ctx, spec, outputs[spec.name])

            needed_help = ctx.recovery_events or ctx.escalations
            outcome = ReplayOutcome.RECOVERED if needed_help else ReplayOutcome.SUCCESS
            return ReplayResult(
                outcome=outcome,
                capability_id=ctx.artifact.id,
                outputs=outputs,
                sensitive_outputs=[s.name for s in ctx.artifact.outputs if s.sensitive],
            )

        # Before pattern-matching the page: if the app said it fell over,
        # that is the answer, and no amount of reading its error page will
        # improve on the status line it came with.
        self._check_app_error(ctx, step_index=None)

        outcome_result = self._check_business_outcomes(ctx)
        if outcome_result is not None:
            return outcome_result

        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    reason_code=FailureReason.UNRECOGNIZED_STATE,
                    step_index=None,
                    expected=ctx.artifact.checkpoint_description,
                    observed="neither the checkpoint nor any known business outcome matched",
                    message="Reached the end of the recorded steps in an unrecognized state.",
                ),
            )
        )

    def _assert_identity(self, ctx: _RunContext, spec: OutputSpec, value: str) -> None:
        """Proves the page is showing the record that was actually asked for.

        Raises rather than returning a flag on purpose: a failed identity
        check must abort before `outputs` is returned, so a value belonging
        to the wrong record can never reach the caller even partially.
        """
        if not spec.must_equal:
            return
        expected = render_value(spec.must_equal, ctx.inputs, ctx.secrets)
        if value.strip() == (expected or "").strip():
            return

        shown_expected = mask_partial(expected) if spec.sensitive else expected
        shown_observed = mask_partial(value) if spec.sensitive else value
        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    reason_code=FailureReason.IDENTITY_MISMATCH,
                    step_index=None,
                    expected=f"output '{spec.name}' to equal {shown_expected!r} (asserted by {spec.must_equal})",
                    observed=f"{shown_observed!r}",
                    message=(
                        f"Identity check failed after {len(ctx.rendered_steps)} steps: the page's "
                        f"'{spec.name}' is {shown_observed!r}, but this run asked for {shown_expected!r}. "
                        f"Refusing to return data that may belong to a different record."
                    ),
                ),
            )
        )

    def _validate_format(self, ctx: _RunContext, spec: OutputSpec, value: str) -> None:
        """Checks a value looks like what the artifact says it is.

        Catches the failure a positional locator makes possible: when a
        page grows a row, the locator still resolves and still returns a
        string -- just the wrong one. A date where a balance belongs is
        obvious to a type check and invisible to every other check we have.
        """
        problem = format_error(spec.type, value)
        if problem is None:
            return
        # Shape hint on, here specifically: this error is *about* the value
        # being the wrong shape, and "***14" alone would state the problem
        # while withholding the evidence for it. "***14 [shape: date]"
        # makes "does not look like money" checkable without printing the
        # figure.
        shown = mask_partial(value, shape_hint=True) if spec.sensitive else value
        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    reason_code=FailureReason.FORMAT_INVALID,
                    step_index=None,
                    expected=f"output '{spec.name}' to look like {spec.type.value}",
                    observed=f"{shown!r}",
                    message=(
                        f"Output '{spec.name}' was read as {shown!r}, which does not look like "
                        f"{spec.type.value}. Refusing to return it -- the locator resolved, but to "
                        f"something that isn't the value it claims to be."
                    ),
                ),
            )
        )

    def _check_app_error(self, ctx: _RunContext, step_index: int | None) -> None:
        """Ends the run if a document currently on screen came back 5xx.

        The counterpart to a permission denial, and the reason both are
        judged by status code rather than by page text: a 403 is the app
        answering a question it understood -- a real result the caller needs
        -- while a 5xx is the app failing to answer at all. Nothing here can
        tell whether retrying would help, so it stops and says exactly what
        it saw.
        """
        failing = ctx.surface.failing_document()
        if failing is None:
            return
        url, status = failing
        raise _ReplayEnd(
            ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=ctx.artifact.id,
                error=ReplayError(
                    reason_code=FailureReason.APP_ERROR,
                    step_index=step_index,
                    expected=f"{urlsplit(url).path} to return a usable page",
                    observed=f"HTTP {status} from {urlsplit(url).path}",
                    message=(
                        f"The application returned HTTP {status}. This is the app failing, not the "
                        f"request being wrong -- the same inputs may work later, but nothing here can "
                        f"decide that."
                    ),
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


def _reason_for(result: ActionResult) -> FailureReason:
    """Maps the surface's own classification of a failure onto a reason
    code, rather than pattern-matching the error text.
    """
    return {
        "locator_not_found": FailureReason.ELEMENT_NOT_FOUND,
        "timeout": FailureReason.TIMEOUT,
    }.get(result.error_kind or "", FailureReason.STEP_FAILED)


def _validate_params(artifact: CapabilityArtifact, inputs: dict[str, str], secrets: dict[str, str]) -> None:
    missing_inputs = [spec.name for spec in artifact.inputs if spec.name not in inputs]
    if missing_inputs:
        raise ValueError(f"missing required inputs: {', '.join(missing_inputs)}")
    missing_secrets = [spec.name for spec in artifact.secrets if spec.name not in secrets]
    if missing_secrets:
        raise ValueError(f"missing required secrets: {', '.join(missing_secrets)}")

    # Render every assertion template up front so a malformed one fails
    # here -- before a browser opens -- instead of halfway through a run.
    for spec in artifact.outputs:
        if not spec.must_equal:
            continue
        try:
            render_value(spec.must_equal, inputs, secrets)
        except RenderError as exc:
            raise ValueError(f"output '{spec.name}' has an unresolvable must_equal ({spec.must_equal}): {exc}") from exc


def _take_control(ctx: _RunContext, holder: ControlHolder, reason: str) -> None:
    """Closes whoever held the session and opens a span for the new holder.

    One function for both directions so a span can't be opened without the
    previous one being closed -- the failure mode being a timeline that
    claims two parties held control at once.
    """
    now = datetime.now(timezone.utc).isoformat()
    if ctx.control_timeline:
        ctx.control_timeline[-1].ended_at = now
    ctx.control_timeline.append(ControlSpan(holder=holder, started_at=now, reason=reason))


def _close_control(ctx: _RunContext, reason: str) -> None:
    if ctx.control_timeline and ctx.control_timeline[-1].ended_at is None:
        ctx.control_timeline[-1].ended_at = datetime.now(timezone.utc).isoformat()


def _check_input_patterns(artifact: CapabilityArtifact, inputs: dict[str, str]) -> ReplayResult | None:
    """Rejects an input that doesn't match its declared pattern, as a
    result rather than an exception.

    Returning a ReplayResult instead of raising, unlike the missing-param
    checks above: a missing input means the command was malformed and there
    is nothing to report *about a run*, while a malformed input is a
    validation error the caller needs back in the same shape as every other
    outcome -- an exit code to branch on and a result file that says what
    was expected and what arrived.
    """
    for spec in artifact.inputs:
        if not spec.pattern or spec.name not in inputs:
            continue
        value = inputs[spec.name]
        try:
            matched = re.fullmatch(spec.pattern, value) is not None
        except re.error as exc:
            # A pattern the recorder should have refused; say so plainly
            # rather than blaming the caller's perfectly fine input.
            return ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                capability_id=artifact.id,
                error=ReplayError(
                    reason_code=FailureReason.INPUT_INVALID,
                    step_index=None,
                    expected=f"input '{spec.name}' to declare a usable pattern",
                    observed=f"{spec.pattern!r} is not a valid regex: {exc}",
                    message=f"Artifact declares an invalid pattern for input '{spec.name}'. Rebuild it.",
                ),
            )
        if matched:
            continue

        # Shape hint on, for the same reason format_invalid turns it on:
        # the complaint *is* about the value's shape, and a bare "***bc"
        # would state the problem while withholding the evidence for it.
        shown = mask_partial(value, shape_hint=True) if spec.sensitive else repr(value)
        return ReplayResult(
            outcome=ReplayOutcome.HARD_FAILURE,
            capability_id=artifact.id,
            error=ReplayError(
                reason_code=FailureReason.INPUT_INVALID,
                step_index=None,
                expected=f"input '{spec.name}' to match {spec.pattern}",
                observed=shown,
                message=(
                    f"Input '{spec.name}' was rejected before anything ran: it does not match the "
                    f"pattern this capability declares ({spec.pattern}). No browser was started."
                ),
            ),
        )
    return None


def _safe_observe(surface: PlaywrightSurface):
    try:
        return surface.observe()
    except Exception:
        return None


def _failure_screenshot(surface: PlaywrightSurface) -> str | None:
    """The brief's "at least one richer signal on failure" (Section 3.5).

    Skipped while a native dialog is open, for the reason found in Part 3:
    a pending dialog blocks the page's render pipeline and screenshot()
    waits on it forever. Never allowed to turn a reportable failure into a
    crash -- a missing picture is a worse result, not a different one.
    """
    if surface._pending_dialog is not None:
        return None
    try:
        _data, path = surface.capture_screenshot()
        return path
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


def _extract_text(
    surface: PlaywrightSurface, target: Target, timeout_ms: int = 3000
) -> tuple[str | None, str | None]:
    """Returns (value, why_not).

    `why_not` carries the resolver's own account of what it tried and how
    many elements each attempt matched. Swallowing that and reporting a
    bare "not found" would hide the difference between the two failures
    that look identical from here and need opposite fixes: a label that
    matched *nothing* (the page changed) and a label that matched *two*
    rows (the label isn't unique any more).
    """
    try:
        scope = surface.scope_for(target.frame)
        locator, _candidate = resolve_with_wait(scope, target.candidates, timeout_ms=timeout_ms)
    except LocatorResolutionError as exc:
        return None, str(exc)
    text = locator.inner_text()
    return (text.strip() if text else None), None


def _describe_action(action: Action) -> str:
    if action.type == ActionType.NAVIGATE:
        return f"navigate to {action.url}"
    if action.target and action.target.candidates:
        c = action.target.candidates[0]
        frame_bit = f" in frame '{action.target.frame}'" if action.target.frame else ""
        return f"{action.type.value} on {c.strategy.value}={c.value!r}{frame_bit}"
    return action.type.value
