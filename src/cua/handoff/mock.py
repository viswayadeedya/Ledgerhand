"""Two stand-ins for a full operator console, per the brief's own scope
note ("a full real-time co-browsing operator console is out of scope...
mock the operator UI if needed, but make the handoff mechanism and the
control-transfer model real"). Both hand escalate() the SAME live surface
InteractivePauseHandoff does; the difference is only how a decision gets
made, not whether control genuinely transfers.
"""

from pathlib import Path
from typing import Callable

from cua.handoff.evidence import append_decision, write_escalation_request
from cua.handoff.handler import HandoffHandler
from cua.handoff.models import EscalationRequest, HandoffAction, HandoffDecision
from cua.surface.playwright_surface import PlaywrightSurface


class MockOperatorHandoff(HandoffHandler):
    """For automated tests and demos: a programmable callback plays the
    operator. It still receives the real live surface and, if it chooses
    to, can act on it directly (click, fill -- exactly what a human would
    do through a UI) before handing back a decision. This is what proves
    the control-transfer model works without requiring an actual person or
    GUI to be present for a test run.
    """

    def __init__(
        self,
        operator_fn: Callable[[EscalationRequest, PlaywrightSurface], HandoffDecision],
        evidence_dir: str | Path = "evidence/runs",
    ):
        self._operator_fn = operator_fn
        self.evidence_dir = evidence_dir

    def escalate(self, request: EscalationRequest, surface: PlaywrightSurface) -> HandoffDecision:
        request_path = write_escalation_request(request, surface, self.evidence_dir)
        decision = self._operator_fn(request, surface)
        append_decision(request_path, decision)
        return decision


class TerminalOperatorHandoff(HandoffHandler):
    """A real person makes a real decision, at a terminal instead of a
    browser-based console: prints the escalation context (reason, step,
    where to find the screenshot), then blocks on real keyboard input for
    their decision. Nothing about the decision-making or the control
    transfer is faked -- only the visual "look at the live page yourself"
    part is, since that's the piece the brief explicitly allows mocking.
    Pair with InteractivePauseHandoff instead when a full hands-on browser
    takeover is what's needed, not just a go/no-go decision.
    """

    def __init__(self, evidence_dir: str | Path = "evidence/runs", input_fn: Callable[[str], str] = input):
        self.evidence_dir = evidence_dir
        self._input_fn = input_fn

    def escalate(self, request: EscalationRequest, surface: PlaywrightSurface) -> HandoffDecision:
        request_path = write_escalation_request(request, surface, self.evidence_dir)

        print("\n=== INTERVENTION REQUESTED ===")
        print(f"Capability : {request.capability_title} ({request.capability_id})")
        print(f"Step       : {request.step_index}")
        print(f"Reason     : {request.reason}")
        if request.business_outcome:
            print(f"Outcome    : {request.business_outcome}")
        print(f"Current URL: {request.current_url}")
        print(f"Screenshot : {request.screenshot_path}")
        print("\nDecide: [a]pprove and retry / [m]anual (I already fixed it live) / [x] abandon")

        raw = self._input_fn("> ").strip().lower()
        action = {
            "a": HandoffAction.APPROVE_AND_RETRY,
            "m": HandoffAction.MANUAL_RESOLVED,
            "x": HandoffAction.ABANDON,
        }.get(raw, HandoffAction.ABANDON)
        note = self._input_fn("Note for the record (optional): ").strip()

        decision = HandoffDecision(action=action, operator_note=note or f"Operator chose '{raw}'.")
        append_decision(request_path, decision)
        return decision
