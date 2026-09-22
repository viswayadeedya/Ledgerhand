from enum import Enum

from pydantic import BaseModel


class ReplayOutcome(str, Enum):
    SUCCESS = "success"  # reached checkpoint cleanly, no recovery needed
    RECOVERED = "recovered"  # reached checkpoint, but only after handling a hiccup along the way
    BUSINESS_OUTCOME = "business_outcome"  # a named, legitimate non-success ending (e.g. "no such member")
    NEEDS_HUMAN = "needs_human"  # a risky action was blocked, or an outcome is too ambiguous to auto-resolve
    HARD_FAILURE = "hard_failure"  # unrecognized state; stop and surface a clear, debuggable error


class RecoveryEvent(BaseModel):
    step_index: int
    kind: str  # "dialog_dismissed" | "session_reauthenticated"
    detail: str


class ReplayError(BaseModel):
    """What/where/why, so a human can actually debug a HARD_FAILURE or
    NEEDS_HUMAN result without re-running anything.
    """

    step_index: int | None
    expected: str
    observed: str
    message: str


class ReplayResult(BaseModel):
    outcome: ReplayOutcome
    capability_id: str
    outputs: dict[str, str] = {}
    business_outcome: str | None = None
    business_outcome_description: str | None = None
    recovery_events: list[RecoveryEvent] = []
    error: ReplayError | None = None
    steps_executed: int = 0
