"""Persists escalation context to disk -- "preserve context and evidence
across the handoff" from the brief. Called by every HandoffHandler before it
does anything else, so the request is on disk even if the operator never
responds.
"""

import json
from pathlib import Path

from cua.handoff.models import EscalationRequest, HandoffDecision
from cua.surface.playwright_surface import PlaywrightSurface


def escalation_path(evidence_dir: str | Path, request: EscalationRequest) -> Path:
    """Where one escalation's record lives.

    Shared rather than duplicated because two callers now need it: the
    handler writes the request and its decision, and the engine appends
    what the operator actually did. A naming rule copied into both would
    silently split one escalation across two files the first time either
    changed.
    """
    # step_index is None for a business-outcome escalation (not tied to any
    # one step) -- name it after the outcome instead of literally "None".
    label = f"step{request.step_index}" if request.step_index is not None else f"outcome_{request.business_outcome}"
    return Path(evidence_dir) / f"escalation_{label}.json"


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
    path = escalation_path(out_dir, request)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def append_decision(request_path: Path, decision: HandoffDecision) -> None:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["decision"] = decision.model_dump(mode="json")
    request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_operator_record(
    evidence_dir: str | Path,
    request: EscalationRequest,
    *,
    actions: list,
    spans: list,
    capture_error: str | None = None,
) -> None:
    """Adds what the human did, and who held control when, to the file the
    handler already wrote.

    Best-effort on purpose: a handler that wrote its request somewhere else
    (or not at all) shouldn't turn "we recorded the handoff" into "the run
    crashed". The same data also travels on the ReplayResult, so nothing is
    lost if this can't find the file.
    """
    path = escalation_path(evidence_dir, request)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    payload["operator_actions"] = [a.model_dump(mode="json") for a in actions]
    payload["control_timeline"] = [s.model_dump(mode="json") for s in spans]
    if capture_error:
        # An empty action list must mean "they did nothing", so a session
        # that couldn't listen has to say so rather than look the same.
        payload["operator_actions_capture_error"] = capture_error
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return
