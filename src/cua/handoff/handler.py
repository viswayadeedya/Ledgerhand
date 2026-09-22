from abc import ABC, abstractmethod

from cua.handoff.models import EscalationRequest, HandoffDecision
from cua.surface.playwright_surface import PlaywrightSurface


class HandoffHandler(ABC):
    """The seam between automation and a human operator.

    escalate() is handed the SAME live PlaywrightSurface the run was already
    using -- not a fresh one -- so an implementation that lets a human act
    can do so on the actual browser/page/context the run is on, and any
    Surface.act() calls it makes go through the same guardrail policy
    automation does. Nothing above this point in the call stack touches the
    surface again until escalate() returns, which is the "who is in
    control" seam the brief asks for made concrete: control is with
    whatever escalate() is doing until it hands back a decision.
    """

    @abstractmethod
    def escalate(self, request: EscalationRequest, surface: PlaywrightSurface) -> HandoffDecision: ...
