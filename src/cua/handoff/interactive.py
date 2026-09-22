"""The real, hands-on-the-wheel handoff: a person takes over the actual
live browser window, not a description of it.

Uses Playwright's own built-in mechanism for exactly this -- page.pause()
opens the Playwright Inspector against the SAME page/context/session
automation was just driving, lets a person click, type, and navigate in it
directly, and returns control to this process the instant they click
"Resume" in the Inspector. We didn't build a custom co-browsing UI; we
reused the one the browser-automation tool we already depend on ships for
this exact purpose.

Needs a headed session with a visible desktop (page.pause() has nothing
meaningful to show over a real display) and a person physically present to
click Resume, so it can't be driven by an automated test the way
MockOperatorHandoff can -- see DECISIONS.md Part 7 for why both exist.
"""

from pathlib import Path

from cua.handoff.evidence import append_decision, write_escalation_request
from cua.handoff.handler import HandoffHandler
from cua.handoff.models import EscalationRequest, HandoffAction, HandoffDecision
from cua.surface.playwright_surface import PlaywrightSurface


class InteractivePauseHandoff(HandoffHandler):
    def __init__(self, evidence_dir: str | Path = "evidence/runs", resume_action: HandoffAction = HandoffAction.MANUAL_RESOLVED):
        self.evidence_dir = evidence_dir
        self.resume_action = resume_action

    def escalate(self, request: EscalationRequest, surface: PlaywrightSurface) -> HandoffDecision:
        request_path = write_escalation_request(request, surface, self.evidence_dir)

        # Blocks here until a human clicks Resume in the Inspector window.
        # Automation makes no further calls to `surface` until this returns.
        surface.page.pause()

        decision = HandoffDecision(
            action=self.resume_action,
            operator_note="Resumed via Playwright Inspector (live session, human-driven).",
        )
        append_decision(request_path, decision)
        return decision
