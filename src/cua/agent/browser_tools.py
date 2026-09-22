"""Bridges Anthropic's browser_toolset_20260801 member calls onto our
Surface. Anthropic's docs are explicit that a client toolset requires the
caller's own application to run every member call -- this module is that
application-side executor: it owns the ref registry, translates each member
into a Surface Action, and formats the acknowledgment text/errors the
toolset's calling convention expects.
"""

import base64
import re

from cua.agent.refs import RefRegistry
from cua.core.models import Action, ActionType, Point, RecordedStep
from cua.guardrails.redact import redact_value
from cua.surface.locator import LocatorResolutionError
from cua.surface.perceive import candidates_from_description, focused_element_is_password, scan_frame_described
from cua.surface.playwright_surface import PlaywrightSurface

_PASSWORD_MARKERS = ("password", "passwd", "pwd", "secret", "token")


def _accessible_name(desc: dict) -> str:
    return desc.get("aria_label") or desc.get("label_text") or desc.get("text") or "(unlabeled)"


def _describe_line(refs: RefRegistry, frame_name: str | None, desc: dict) -> str:
    name = _accessible_name(desc)
    candidates = candidates_from_description(desc)
    ref = refs.register_element(frame_name, candidates, name, is_secret=bool(desc.get("is_password")))
    return f'{desc["role"]} "{name}" [{ref}]'


def read_page(surface: PlaywrightSurface, refs: RefRegistry, scope_ref: str | None) -> str:
    if scope_ref is not None:
        frame_name = refs.resolve_frame(scope_ref)  # KeyError -> stale/unknown ref
        scope = surface.scope_for(frame_name)
        lines = [_describe_line(refs, frame_name, d) for d in scan_frame_described(scope)]
        return "\n".join(lines) if lines else "(no interactive elements found in this frame)"

    named_frames = [f.name for f in surface.page.frames if f.name]
    if named_frames:
        lines = [f'frame "{name}" [{refs.register_frame(name)}]' for name in named_frames]
        return "\n".join(lines)

    lines = [_describe_line(refs, None, d) for d in scan_frame_described(surface.page)]
    return "\n".join(lines) if lines else "(no interactive elements found)"


def find(surface: PlaywrightSurface, refs: RefRegistry, query: str) -> str:
    query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    named_frames = [f.name for f in surface.page.frames if f.name]
    scan_targets = [(name, surface.scope_for(name)) for name in named_frames] or [(None, surface.page)]

    scored: list[tuple[int, str | None, dict]] = []
    for frame_name, scope in scan_targets:
        try:
            descs = scan_frame_described(scope)
        except Exception:
            continue
        for desc in descs:
            haystack = " ".join(
                filter(None, [desc.get("role"), desc.get("aria_label"), desc.get("label_text"), desc.get("text")])
            ).lower()
            score = len(query_tokens & set(re.findall(r"[a-z0-9]+", haystack)))
            if score > 0:
                scored.append((score, frame_name, desc))
    scored.sort(key=lambda t: -t[0])

    lines = [_describe_line(refs, frame_name, desc) for _, frame_name, desc in scored[:20]]
    return "\n".join(lines) if lines else f'(no elements matched "{query}")'


class BrowserToolExecutor:
    """Owns one Surface + ref registry for the lifetime of a discovery run."""

    def __init__(self, surface: PlaywrightSurface):
        self.surface = surface
        self.refs = RefRegistry()
        self.steps: list[RecordedStep] = []
        self._pending_redaction: str | None = None

    # -- dispatch ---------------------------------------------------------

    def dispatch(self, name: str, tool_input: dict, human_approved: bool = False):
        """Returns (content, is_error) for a browser_toolset member call."""
        handlers = {
            "navigate": self._navigate,
            "screenshot": self._screenshot,
            "read_page": self._read_page,
            "find": self._find,
            "left_click": self._click,
            "double_click": self._click,
            "type": self._type,
            "key": self._key,
            "form_input": self._form_input,
            "wait": self._wait,
        }
        handler = handlers.get(name)
        if handler is None:
            return (f"Error: member '{name}' is not implemented in this environment.", True)
        try:
            return handler(tool_input, human_approved)
        except KeyError as exc:
            return (
                f"Error: {exc.args[0]} is stale or not found on the current page. Re-read the page to get fresh references.",
                True,
            )
        except LocatorResolutionError as exc:
            return (f"Error: {exc}", True)

    def dismiss_dialog(self, accept: bool) -> str:
        action = Action(type=ActionType.DISMISS_DIALOG, value="accept" if accept else "dismiss")
        result = self.surface.act(action)
        self._record("dismiss_dialog", {"accept": accept}, action, result)
        if not result.success:
            return f"Error: {result.error}"
        self.refs.clear()
        return "Dialog accepted." if accept else "Dialog dismissed."

    # -- members ------------------------------------------------------------

    def _dialog_notice(self) -> str:
        msg = self.surface._pending_dialog.message if self.surface._pending_dialog else ""
        return f'A dialog is open: "{msg}". Call dismiss_dialog before continuing.'

    def _navigate(self, tool_input: dict, human_approved: bool):
        url = tool_input.get("url", "")
        if url in ("back", "forward", "reload"):
            {"back": self.surface.page.go_back, "forward": self.surface.page.go_forward, "reload": self.surface.page.reload}[
                url
            ](wait_until="domcontentloaded")
            self.surface._settle()
            self.refs.clear()
            self._record("navigate", tool_input, None, None)
            return (f"Navigated. Now at {self.surface.page.url}", False)
        full_url = url if "://" in url else f"https://{url}"
        action = Action(type=ActionType.NAVIGATE, url=full_url)
        result = self.surface.act(action, human_approved=human_approved)
        self._record("navigate", tool_input, action, result)
        self.refs.clear()
        if result.blocked:
            return (f"Blocked by policy: {result.policy_reason}", True)
        if not result.success:
            return (f"Error: {result.error}", True)
        return (f"Navigated to {self.surface.page.url}", False)

    def _screenshot(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        data, _path = self.surface.capture_screenshot()
        self._record("screenshot", {}, None, None)
        b64 = base64.b64encode(data).decode("ascii")
        return ([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}], False)

    def _read_page(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        text = read_page(self.surface, self.refs, tool_input.get("ref"))
        self._record("read_page", tool_input, None, None)
        return (text, False)

    def _find(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        text = find(self.surface, self.refs, tool_input.get("query", ""))
        self._record("find", tool_input, None, None)
        return (text, False)

    def _click(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        target_spec = tool_input.get("target") or {}
        self._pending_redaction = None
        if target_spec.get("type") == "ref":
            ref = target_spec["ref"]
            entry = self.refs.get(ref)  # KeyError -> stale, caught by dispatch()
            self._pending_redaction = self._redaction_for_entry(entry)
            action = Action(type=ActionType.CLICK, target=self.refs.resolve_target(ref))
            label = ref
        elif target_spec.get("type") == "coordinate":
            action = Action(type=ActionType.CLICK, point=Point(x=target_spec["x"], y=target_spec["y"]))
            label = f"({target_spec.get('x')}, {target_spec.get('y')})"
        else:
            return ("Error: target must be {type: ref, ref} or {type: coordinate, x, y}.", True)

        result = self.surface.act(action, human_approved=human_approved)
        self._record("left_click", tool_input, action, result)
        self.refs.clear()  # a click may have navigated; force a fresh read_page
        if result.blocked:
            return (f"Blocked by policy: {result.policy_reason}", True)
        if not result.success:
            return (f"Error: {result.error}", True)
        return (f"Clicked element {label}." if target_spec.get("type") == "ref" else f"clicked at {label}", False)

    def _type(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        text = tool_input.get("text", "")
        # `type` has no target of its own -- it types wherever focus already
        # is, which could have gotten there via a Tab press, not just our
        # last-tracked click. Check the real DOM focus, not click history.
        try:
            self._pending_redaction = "{{secrets.password}}" if focused_element_is_password(self.surface.page) else None
        except Exception:
            self._pending_redaction = "***REDACTED***"  # can't verify -> redact conservatively
        action = Action(type=ActionType.TYPE, value=text)
        result = self.surface.act(action, human_approved=human_approved)
        self._record("type", tool_input, action, result)
        if not result.success:
            return (f"Error: {result.error}", True)
        return (f"typed: {text}", False)

    def _key(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        text = tool_input.get("text", "")
        repeat = max(1, int(tool_input.get("repeat") or 1))
        result = None
        for _ in range(repeat):
            action = Action(type=ActionType.KEY, value=text)
            result = self.surface.act(action, human_approved=human_approved)
            if not result.success:
                break
        self._record("key", tool_input, Action(type=ActionType.KEY, value=text), result)
        if result and not result.success:
            return (f"Error: {result.error}", True)
        if any(tok.lower() in ("enter", "return") for tok in text.split()):
            self.refs.clear()  # Enter may have submitted a form
        return (f"pressed {text}", False)

    def _form_input(self, tool_input: dict, human_approved: bool):
        if self.surface._pending_dialog is not None:
            return (self._dialog_notice(), False)
        target_spec = tool_input.get("target") or {}
        if target_spec.get("type") != "ref":
            return ("Error: form_input only accepts a ref target.", True)
        ref = target_spec["ref"]
        entry = self.refs.get(ref)
        self._pending_redaction = self._redaction_for_entry(entry)
        target = self.refs.resolve_target(ref)
        value = tool_input.get("value")
        role = entry.candidates[0].role if entry.candidates and entry.candidates[0].role else None
        action_type = ActionType.SELECT if role == "combobox" else ActionType.FILL
        action = Action(type=action_type, target=target, value=str(value))
        result = self.surface.act(action, human_approved=human_approved)
        self._record("form_input", tool_input, action, result)
        if not result.success:
            return (f"Error: {result.error or result.policy_reason}", True)
        return (f"Set form input to {value}", False)

    def _wait(self, tool_input: dict, human_approved: bool):
        duration = float(tool_input.get("duration", 0) or 0)
        action = Action(type=ActionType.WAIT, value=str(duration))
        result = self.surface.act(action)
        self._record("wait", tool_input, action, result)
        return (f"Waited {duration} seconds", False)

    # -- recording ----------------------------------------------------------

    def _redaction_for_entry(self, entry) -> str | None:
        """`entry.is_secret` is the DOM-verified input[type=password] signal
        (reliable -> we know exactly which secret this is, so we can log the
        real template placeholder instead of a generic marker). The label
        heuristic is a looser fallback for secret-shaped fields that aren't
        specifically a password input; we can't know its name, so it gets
        the generic marker instead and a human has to fix it up if they turn
        this run into an artifact.
        """
        if entry.is_secret:
            return "{{secrets.password}}"
        if any(m in entry.label.lower() for m in _PASSWORD_MARKERS):
            return "***REDACTED***"
        return None

    def _record(self, tool_name: str, tool_input: dict, action, result) -> None:
        redaction = self._pending_redaction if tool_name in ("type", "form_input") else None
        self.steps.append(
            RecordedStep(
                index=len(self.steps),
                tool_name=tool_name,
                tool_input=self._redact_input(tool_input, redaction),
                action=self._redact_action(action, redaction),
                result=result,
            )
        )

    def _redact_input(self, tool_input: dict, redaction: str | None) -> dict:
        redacted = redact_value(dict(tool_input))
        if redaction:
            if "text" in redacted:
                redacted["text"] = redaction
            if "value" in redacted:
                redacted["value"] = redaction
        return redacted

    def _redact_action(self, action, redaction: str | None):
        # The real Action (with its real value) already did its job by the
        # time this runs -- executing it. What we store here is purely for
        # the log/artifact, so it gets the same treatment as tool_input
        # rather than trusting every future caller to remember to redact it
        # downstream.
        if action is not None and redaction and action.value is not None:
            return action.model_copy(update={"value": redaction})
        return action
