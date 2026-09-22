"""Persists escalation context to disk -- "preserve context and evidence
across the handoff" from the brief. Called by every HandoffHandler before it
does anything else, so the request is on disk even if the operator never
responds.
"""

import json
from pathlib import Path

from cua.handoff.models import EscalationRequest, HandoffDecision
from cua.surface.playwright_surface import PlaywrightSurface


def write_escalation_request(request: EscalationRequest, surface: PlaywrightSurface, evidence_dir: str | Path) -> Path:
    out_dir = Path(evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = None
    if surface._pending_dialog is None:  # a screenshot would hang against a blocked dialog, same as Part 3's finding
        try:
            _data, screenshot_path = surface.capture_screenshot()
        except Exception:
            screenshot_path = None
    payload = request.model_dump(mode="json")
    payload["screenshot_path"] = screenshot_path or request.screenshot_path
    # step_index is None for a business-outcome escalation (not tied to any
    # one step) -- name it after the outcome instead of literally "None".
    label = f"step{request.step_index}" if request.step_index is not None else f"outcome_{request.business_outcome}"
    path = out_dir / f"escalation_{label}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def append_decision(request_path: Path, decision: HandoffDecision) -> None:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["decision"] = decision.model_dump(mode="json")
    request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
