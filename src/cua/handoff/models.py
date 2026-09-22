from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class HandoffAction(str, Enum):
    APPROVE_AND_RETRY = "approve_and_retry"  # automation retries the SAME blocked step, now human-approved
    MANUAL_RESOLVED = "manual_resolved"  # the human acted directly in the live session; skip to checking the ending
    ABANDON = "abandon"  # the human declined or couldn't resolve it; stop here


class EscalationRequest(BaseModel):
    """Everything a human needs to act on an intervention request, without
    having to go re-read code or logs first -- which capability/goal, where
    it stopped, and why.
    """

    capability_id: str
    capability_title: str
    step_index: int | None
    reason: str
    business_outcome: str | None = None
    current_url: str
    screenshot_path: str | None
    requested_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HandoffDecision(BaseModel):
    action: HandoffAction
    operator_note: str = ""


class EscalationRecord(BaseModel):
    """What actually happened during one escalation -- appended to
    ReplayResult so a run that needed a human is never indistinguishable
    from one that didn't, even when it ultimately succeeded.
    """

    step_index: int | None
    reason: str
    decision: HandoffAction
    operator_note: str
    resumed_via: str
    requested_at: str
    resolved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
