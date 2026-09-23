"""Shared contracts used across guardrails, surface, agent, and replay.

Centralized here (rather than owned by any one part) because the Action
model in particular has to be produced by the agent loop, checked by
guardrails, and executed by the surface layer -- three packages that would
otherwise need to depend on each other in a cycle.
"""

from enum import Enum

from pydantic import BaseModel


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    READ = "read"
    SUBMIT = "submit"
    WAIT = "wait"
    DISMISS_DIALOG = "dismiss_dialog"
    TYPE = "type"  # raw keystrokes at current focus -- discovery only, never replay
    KEY = "key"  # single key or chord press -- discovery only, never replay


class LocatorStrategy(str, Enum):
    """Ranked from most to least robust; see surface/locator.py."""

    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    TABLE_POSITION = "table_position"
    CSS = "css"


class LocatorCandidate(BaseModel):
    strategy: LocatorStrategy
    value: str
    role: str | None = None  # only meaningful when strategy == ROLE


class Target(BaseModel):
    frame: str | None = None  # named frame to act within; None = top-level page
    candidates: list[LocatorCandidate]


class Point(BaseModel):
    """A pixel coordinate, as produced by a screenshot-driven discovery step.

    The surface resolves this to a semantic Target before acting, so
    coordinates never make it into a recorded artifact -- see
    surface/perceive.py.
    """

    x: int
    y: int


class Action(BaseModel):
    """A single step the agent or replay engine wants to perform."""

    type: ActionType
    url: str | None = None
    method: str | None = None
    description: str | None = None
    target: Target | None = None
    point: Point | None = None
    value: str | None = None


class RiskLevel(str, Enum):
    SAFE = "safe"
    RISKY = "risky"


class PolicyDecision(BaseModel):
    allowed: bool
    risk: RiskLevel
    requires_confirmation: bool
    reason: str


class ElementSummary(BaseModel):
    tag: str
    role: str | None = None
    name: str | None = None
    text: str | None = None
    frame: str | None = None  # which named frame this was scanned from; None = top-level page
    table_row: int | None = None  # position within its table, if any -- see recorder.find_target_for_text
    table_col: int | None = None


class Observation(BaseModel):
    url: str
    frames: dict[str, str] = {}
    elements: list[ElementSummary] = []
    dialog_message: str | None = None
    screenshot_path: str | None = None


class ActionResult(BaseModel):
    success: bool
    blocked: bool = False
    policy_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None
    """A coarse classification of `error` ("locator_not_found", "timeout",
    "surface_error") set by the surface itself, so callers can react to the
    *kind* of failure without pattern-matching on the message text.
    """
    resolved_strategy: LocatorStrategy | None = None
    resolved_value: str | None = None
    observation: Observation | None = None


class RecordedStep(BaseModel):
    """One step of a discovery run, as the recorder (Part 5) will consume it
    to build an artifact. tool_input is pre-redacted before it ever lands
    here -- see agent/browser_tools.py.
    """

    index: int
    tool_name: str
    tool_input: dict = {}
    action: Action | None = None
    result: ActionResult | None = None
