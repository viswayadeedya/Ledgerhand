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

What the person does here is still recorded, and this handler contains no
code to do that. The replay engine opens an OperatorSession around every
escalate() call, and that session listens to the *page* rather than to the
caller -- so a human clicking in the Inspector and a handler calling
locator.click() are recorded by the same listener, because both are real
events in a real DOM. That is the whole reason the capture lives in the
page instead of in a wrapper API: a wrapper would have recorded every mode
except the one where a human is genuinely at the wheel.
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
