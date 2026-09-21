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


class Action(BaseModel):
    """A single step the agent or replay engine wants to perform.

    This is the contract the surface layer (Part 3) and agent loop (Part 4)
    will produce and the guardrail/replay layers consume -- deliberately
    thin, so it doesn't presume a browser-specific action vocabulary.
    """

    type: ActionType
    url: str | None = None
    method: str | None = None
    description: str | None = None


class RiskLevel(str, Enum):
    SAFE = "safe"
    RISKY = "risky"


class PolicyDecision(BaseModel):
    allowed: bool
    risk: RiskLevel
    requires_confirmation: bool
    reason: str
