import fnmatch
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from cua.guardrails.models import Action, ActionType, PolicyDecision, RiskLevel

load_dotenv()

_DEFAULT_POLICY_PATH = Path(__file__).parent / "policy.yaml"


class PolicyConfig(BaseModel):
    allowed_domains: list[str]
    allowed_routes: list[str]
    allowed_action_types: list[ActionType]
    risky_routes: list[str]
    risky_action_default: str  # "block" | "allow"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PolicyConfig":
        resolved = Path(path or os.environ.get("GUARDRAILS_POLICY_PATH") or _DEFAULT_POLICY_PATH)
        with open(resolved, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)


def _path_of(url: str) -> tuple[str | None, str]:
    """Splits a URL (absolute or relative) into (netloc-or-None, path)."""
    parts = urlsplit(url)
    if parts.netloc:
        return parts.netloc, parts.path or "/"
    return None, url.split("?", 1)[0] or "/"


class PolicyEngine:
    """Guardrail checkpoint every action must pass before it reaches the surface.

    Deliberately independent of the LLM: it only ever sees the Action the
    agent/replay engine produced, never the model's reasoning, so a bad or
    hijacked prompt can't talk its way past it.
    """

    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig.load()

    def evaluate(self, action: Action, human_approved: bool = False) -> PolicyDecision:
        if action.type not in self.config.allowed_action_types:
            return PolicyDecision(
                allowed=False,
                risk=RiskLevel.SAFE,
                requires_confirmation=False,
                reason=f"action type '{action.type.value}' is not in the allowlist",
            )

        if action.url is not None:
            domain, path = _path_of(action.url)
            if domain is not None and domain not in self.config.allowed_domains:
                return PolicyDecision(
                    allowed=False,
                    risk=RiskLevel.SAFE,
                    requires_confirmation=False,
                    reason=f"domain '{domain}' is not in the allowlist",
                )
            if not any(fnmatch.fnmatch(path, pattern) for pattern in self.config.allowed_routes):
                return PolicyDecision(
                    allowed=False,
                    risk=RiskLevel.SAFE,
                    requires_confirmation=False,
                    reason=f"route '{path}' is not in the allowlist",
                )
            if any(fnmatch.fnmatch(path, pattern) for pattern in self.config.risky_routes):
                if human_approved:
                    return PolicyDecision(
                        allowed=True,
                        risk=RiskLevel.RISKY,
                        requires_confirmation=False,
                        reason=f"route '{path}' is risky/irreversible but was human-approved",
                    )
                if self.config.risky_action_default == "block":
                    return PolicyDecision(
                        allowed=False,
                        risk=RiskLevel.RISKY,
                        requires_confirmation=True,
                        reason=f"route '{path}' is risky/irreversible and requires human approval",
                    )
                return PolicyDecision(
                    allowed=True,
                    risk=RiskLevel.RISKY,
                    requires_confirmation=False,
                    reason=f"route '{path}' is risky but policy default is 'allow'",
                )

        return PolicyDecision(
            allowed=True,
            risk=RiskLevel.SAFE,
            requires_confirmation=False,
            reason="action matches the allowlist and no risky route pattern",
        )
