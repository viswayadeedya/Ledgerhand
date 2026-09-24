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


class OperatorAction(BaseModel):
    """One thing a human did while they held the session.

    Captured from the browser's own events rather than from whatever the
    operator's code says it did, so it records what actually happened on
    the page -- and so it works the same whether the person drove through
    the Playwright Inspector or a handler drove Playwright directly.
    """

    at: str
    kind: str  # "click" | "change" | "navigate"
    target: str
    """A readable description of the element -- 'link "View"', 'password
    field "password"'. Never built from an input's value: a password field
    would put the secret somewhere nothing downstream would think to mask.
    """
    value: str | None = None
    """Masked. What was typed is worth recording; the literal is not worth
    keeping, and for a password field it never leaves the page at all.
    """
    frame: str | None = None
    url: str | None = None


class ControlHolder(str, Enum):
    AUTOMATION = "automation"
    HUMAN = "human"


class ControlSpan(BaseModel):
    """Who held the session, and between when and when.

    The brief asks for "a way to know who is (or should be) in control".
    A boolean answers that only for right now; a run that has already
    finished still has to be able to say who was driving when, which is
    what makes the record auditable after the fact rather than during.
    """

    holder: ControlHolder
    started_at: str
    ended_at: str | None = None
    """None only while the span is still open -- i.e. on a record captured
    mid-run. Every span in a finished result is closed.
    """
    reason: str = ""


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

    operator_actions: list[OperatorAction] = []
    """What the human did, in order. Empty is a real answer: an operator
    who declined without touching the page did nothing, and that is worth
    being able to tell apart from an operator who tried something first.
    """
