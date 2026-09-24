from enum import Enum

from pydantic import BaseModel

from cua.handoff.models import EscalationRecord


class ReplayOutcome(str, Enum):
    SUCCESS = "success"  # reached checkpoint cleanly, no recovery needed
    RECOVERED = "recovered"  # reached checkpoint, but only after handling a hiccup along the way
    BUSINESS_OUTCOME = "business_outcome"  # a named, legitimate non-success ending (e.g. "no such member")
    NEEDS_HUMAN = "needs_human"  # a risky action was blocked, or an outcome is too ambiguous to auto-resolve
    HARD_FAILURE = "hard_failure"  # unrecognized state; stop and surface a clear, debuggable error


class FailureReason(str, Enum):
    """Why a run ended badly, as a stable code rather than prose.

    `message` is for a human reading one failure; this is for everything
    else -- triage, alerting, and counting failures by kind across many
    replays (an artifact that starts throwing `element_not_found` at one
    tenant is drift; one throwing `identity_mismatch` is a correctness
    emergency, and grepping message strings to tell them apart would be a
    bad way to find that out).
    """

    INPUT_INVALID = "input_invalid"  # a caller-supplied input didn't match the contract; nothing was run
    IDENTITY_MISMATCH = "identity_mismatch"  # the page's record isn't the one that was asked for
    FORMAT_INVALID = "format_invalid"  # a value was found but doesn't look like what it should be
    ELEMENT_NOT_FOUND = "element_not_found"  # no locator candidate resolved
    TIMEOUT = "timeout"  # the surface gave up waiting
    UNRECOGNIZED_STATE = "unrecognized_state"  # neither checkpoint nor any known business outcome
    STEP_FAILED = "step_failed"  # a step failed for some other reason
    RECOVERY_FAILED = "recovery_failed"  # re-authentication or another recovery attempt didn't work
    POLICY_BLOCKED = "policy_blocked"  # guardrails refused; needs human approval
    OPERATOR_ABANDONED = "operator_abandoned"  # a human was asked and declined


class RecoveryEvent(BaseModel):
    step_index: int
    kind: str  # "dialog_dismissed" | "session_reauthenticated"
    detail: str


class ReplayError(BaseModel):
    """What/where/why, so a human can actually debug a HARD_FAILURE or
    NEEDS_HUMAN result without re-running anything.
    """

    reason_code: FailureReason
    step_index: int | None
    expected: str
    observed: str
    message: str


class ReplayResult(BaseModel):
    outcome: ReplayOutcome
    capability_id: str
    outputs: dict[str, str] = {}
    sensitive_outputs: list[str] = []
    """Names within `outputs` whose values must be masked anywhere they're
    written down. The values stay intact here -- the caller asked for them
    and needs them -- so masking belongs at the boundaries that persist or
    print, not in the data the capability returns.
    """
    business_outcome: str | None = None
    business_outcome_description: str | None = None
    recovery_events: list[RecoveryEvent] = []
    escalations: list[EscalationRecord] = []
    error: ReplayError | None = None
    steps_executed: int = 0
