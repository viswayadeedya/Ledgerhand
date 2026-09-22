from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Dialog, Page, sync_playwright

from cua.core.models import (
    Action,
    ActionResult,
    ActionType,
    ElementSummary,
    Observation,
    PolicyDecision,
)
from cua.guardrails.policy import PolicyEngine
from cua.surface.base import Surface
from cua.surface.keys import translate_key_sequence
from cua.surface.locator import LocatorResolutionError, resolve_with_wait
from cua.surface.perceive import candidates_from_description, describe_point, link_info, scan_frame


class PlaywrightSurface(Surface):
    """Drives the fake app (or any similarly framed web app) with Playwright.

    Every state-changing act() first resolves *what* would be acted on and
    *where it would lead* (a link's href, a form's action) before checking
    the guardrail policy -- so a blocked action never reaches the browser at
    all, rather than being clicked and then apologized for.
    """

    def __init__(
        self,
        base_url: str,
        policy: PolicyEngine | None = None,
        headless: bool = True,
        evidence_dir: str | Path = "evidence/runs",
    ):
        self.base_url = base_url.rstrip("/")
        self.policy = policy or PolicyEngine()
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        self.browser: Browser = self._pw.chromium.launch(headless=headless)
        self.context: BrowserContext = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page: Page = self.context.new_page()
        self._pending_dialog: Dialog | None = None
        self.page.on("dialog", self._on_dialog)
        self._step = 0

    def _on_dialog(self, dialog: Dialog) -> None:
        self._pending_dialog = dialog

    # ---- observation --------------------------------------------------

    def observe(self) -> Observation:
        frames = {f.name: f.url for f in self.page.frames if f.name}
        elements: list[ElementSummary] = []
        dialog_open = self._pending_dialog is not None
        # A pending native dialog (window.confirm/alert) blocks the page's
        # render/script pipeline entirely -- both evaluate()-based scanning
        # and screenshot() hang while it's open. url/frame metadata don't
        # need either, so those still work; skip the rest until it's
        # dismissed.
        if not dialog_open:
            scopes = [f for f in self.page.frames if f.name] or [self.page.main_frame]
            for scope in scopes:
                frame_name = getattr(scope, "name", "") or None
                try:
                    for raw in scan_frame(scope):
                        elements.append(ElementSummary(**raw, frame=frame_name))
                except Exception:
                    continue
        return Observation(
            url=self.page.url,
            frames=frames,
            elements=elements[:80],
            dialog_message=self._pending_dialog.message if dialog_open else None,
            screenshot_path=None if dialog_open else self._screenshot(),
        )

    def _screenshot(self) -> str:
        _, path = self.capture_screenshot()
        return path

    def capture_screenshot(self) -> tuple[bytes, str]:
        """Saves a screenshot to the evidence dir and also returns the raw
        bytes, so a caller (e.g. the discovery loop, to hand to the model)
        doesn't have to re-read the file it was just written to.
        """
        self._step += 1
        path = self.evidence_dir / f"step_{self._step:03d}.png"
        data = self.page.screenshot(path=str(path))
        return data, str(path)

    # ---- action ---------------------------------------------------------

    def act(self, action: Action, human_approved: bool = False) -> ActionResult:
        handler = {
            ActionType.NAVIGATE: self._do_navigate,
            ActionType.CLICK: self._do_click_or_submit,
            ActionType.SUBMIT: self._do_click_or_submit,
            ActionType.FILL: self._do_fill,
            ActionType.SELECT: self._do_select,
            ActionType.READ: self._do_read,
            ActionType.WAIT: self._do_wait,
            ActionType.DISMISS_DIALOG: self._do_dismiss_dialog,
            ActionType.TYPE: self._do_type,
            ActionType.KEY: self._do_key,
        }.get(action.type)
        if handler is None:
            return ActionResult(success=False, error=f"unsupported action type: {action.type}")
        try:
            return handler(action, human_approved)
        except LocatorResolutionError as exc:
            return ActionResult(success=False, error=str(exc))

    def scope_for(self, frame_name: str | None):
        if frame_name is None:
            return self.page
        frame = self.page.frame(name=frame_name)
        if frame is None:
            raise LocatorResolutionError(f"no frame named '{frame_name}'")
        return frame

    def _absolute(self, path: str | None) -> str | None:
        if not path:
            return path
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base_url + (path if path.startswith("/") else "/" + path)

    def _do_navigate(self, action: Action, human_approved: bool) -> ActionResult:
        decision = self.policy.evaluate(action, human_approved=human_approved)
        if not decision.allowed:
            return ActionResult(success=False, blocked=True, policy_reason=decision.reason)
        # domcontentloaded, not the default "load": a page whose window.onload
        # handler fires a blocking confirm() (our `popup` fault) would never
        # finish the "load" event, deadlocking goto() otherwise.
        self.page.goto(action.url, wait_until="domcontentloaded")
        self._settle()
        return ActionResult(success=True, observation=self.observe())

    def _settle(self, max_ms: int = 600, poll_ms: int = 50) -> None:
        """Pause after a navigation/click so a window.onload dialog (if any)
        has time to fire and reach our `dialog` handler before we decide
        whether it's safe to evaluate() JS in the page -- closes the race
        between "the page has navigated" and "load fires the popup".

        Polls instead of a single flat sleep: returns as soon as a dialog
        shows up (usually much faster than the max), but still waits out the
        full budget on the normal, no-dialog path -- a fixed 150ms wasn't
        reliably enough for a cross-frame navigation (a real HTTP round trip
        to the backend, not just a JS timer) to finish before observe() went
        looking for the dialog or scanned the page's content.
        """
        waited = 0
        while waited < max_ms:
            if self._pending_dialog is not None:
                return
            self.page.wait_for_timeout(poll_ms)
            waited += poll_ms

    def _resolve_target(self, action: Action):
        """Returns (locator, winning_candidate, predicted_url, predicted_method)."""
        if action.target is not None:
            scope = self.scope_for(action.target.frame)
            locator, candidate = resolve_with_wait(scope, action.target.candidates)
            info = link_info(locator)
            return locator, candidate, self._absolute(info.get("url")), info.get("method")
        if action.point is not None:
            desc = describe_point(self.page, action.point.x, action.point.y)
            if desc is None:
                raise LocatorResolutionError(f"no element at point ({action.point.x}, {action.point.y})")
            candidates = candidates_from_description(desc)
            scope = self.scope_for(desc.get("frame"))
            locator, candidate = resolve_with_wait(scope, candidates)
            url = desc.get("href") or desc.get("form_action")
            return locator, candidate, self._absolute(url), desc.get("form_method")
        raise LocatorResolutionError("action has neither target nor point")

    def _check_policy(self, action: Action, predicted_url, predicted_method, human_approved: bool) -> PolicyDecision:
        check_action = action.model_copy(
            update={"url": predicted_url or action.url, "method": predicted_method or action.method}
        )
        return self.policy.evaluate(check_action, human_approved=human_approved)

    def _do_click_or_submit(self, action: Action, human_approved: bool) -> ActionResult:
        locator, candidate, predicted_url, predicted_method = self._resolve_target(action)
        decision = self._check_policy(action, predicted_url, predicted_method, human_approved)
        if not decision.allowed:
            return ActionResult(
                success=False,
                blocked=True,
                policy_reason=decision.reason,
                resolved_strategy=candidate.strategy,
                resolved_value=candidate.value,
            )
        locator.click()
        self._settle()
        return ActionResult(
            success=True,
            resolved_strategy=candidate.strategy,
            resolved_value=candidate.value,
            observation=self.observe(),
        )

    def _do_fill(self, action: Action, human_approved: bool) -> ActionResult:
        if action.target is None:
            raise LocatorResolutionError("fill requires an explicit target")
        scope = self.scope_for(action.target.frame)
        locator, candidate = resolve_with_wait(scope, action.target.candidates)
        decision = self.policy.evaluate(action, human_approved=human_approved)
        if not decision.allowed:
            return ActionResult(success=False, blocked=True, policy_reason=decision.reason)
        locator.fill(action.value or "")
        return ActionResult(
            success=True,
            resolved_strategy=candidate.strategy,
            resolved_value=candidate.value,
            observation=self.observe(),
        )

    def _do_select(self, action: Action, human_approved: bool) -> ActionResult:
        if action.target is None:
            raise LocatorResolutionError("select requires an explicit target")
        scope = self.scope_for(action.target.frame)
        locator, candidate = resolve_with_wait(scope, action.target.candidates)
        decision = self.policy.evaluate(action, human_approved=human_approved)
        if not decision.allowed:
            return ActionResult(success=False, blocked=True, policy_reason=decision.reason)
        locator.select_option(action.value or "")
        return ActionResult(
            success=True,
            resolved_strategy=candidate.strategy,
            resolved_value=candidate.value,
            observation=self.observe(),
        )

    def _do_read(self, action: Action, human_approved: bool) -> ActionResult:
        return ActionResult(success=True, observation=self.observe())

    def _do_wait(self, action: Action, human_approved: bool) -> ActionResult:
        if action.value:
            try:
                seconds = float(action.value)
                self.page.wait_for_timeout(seconds * 1000)
                return ActionResult(success=True, observation=self.observe())
            except ValueError:
                pass
            try:
                self.page.get_by_text(action.value).first.wait_for(timeout=5000)
            except Exception as exc:
                return ActionResult(success=False, error=str(exc))
        else:
            self.page.wait_for_timeout(500)
        return ActionResult(success=True, observation=self.observe())

    def _do_type(self, action: Action, human_approved: bool) -> ActionResult:
        """Types at whatever element currently has focus, the way the
        discovery loop's browser toolset works (click to focus, then type --
        no locator involved). Never used by deterministic replay.
        """
        decision = self.policy.evaluate(action, human_approved=human_approved)
        if not decision.allowed:
            return ActionResult(success=False, blocked=True, policy_reason=decision.reason)
        self.page.keyboard.type(action.value or "")
        return ActionResult(success=True, observation=self.observe())

    def _do_key(self, action: Action, human_approved: bool) -> ActionResult:
        decision = self.policy.evaluate(action, human_approved=human_approved)
        if not decision.allowed:
            return ActionResult(success=False, blocked=True, policy_reason=decision.reason)
        for key_spec in translate_key_sequence(action.value or ""):
            self.page.keyboard.press(key_spec)
        self._settle()
        return ActionResult(success=True, observation=self.observe())

    def _do_dismiss_dialog(self, action: Action, human_approved: bool) -> ActionResult:
        if self._pending_dialog is None:
            return ActionResult(success=False, error="no dialog is currently open")
        dialog = self._pending_dialog
        self._pending_dialog = None
        if action.value == "dismiss":
            dialog.dismiss()
        else:
            dialog.accept()
        return ActionResult(success=True, observation=self.observe())

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self._pw.stop()
