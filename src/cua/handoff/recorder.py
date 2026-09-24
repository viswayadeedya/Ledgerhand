"""Records what a human did while they held the live session.

The brief asks the handoff to "record what the human did". The obvious
implementation -- give the operator a wrapper API and log the calls -- only
works for an operator that is code. A person driving the Playwright
Inspector makes no Python calls at all, so the one mode where a real human
is genuinely at the wheel would have been the one mode that recorded
nothing.

So this listens to the page instead. A capture-phase listener injected into
every frame reports clicks and field changes back to Python through an
exposed binding, which means it sees a person clicking in the Inspector and
a handler calling `locator.click()` identically -- both are real events in
a real DOM. One mechanism, both modes, and what gets recorded is what
actually happened to the page rather than what the caller claims it did.

Values are masked on the way in, and a password never leaves the page at
all: the JS reports it as absent rather than sending it for Python to mask,
following the Part 4 finding that a value a scan can reach is a value some
future consumer forgets to redact.
"""

import json
import weakref
from datetime import datetime, timezone

from cua.guardrails.redact import mask_partial
from cua.handoff.models import OperatorAction
from cua.surface.playwright_surface import PlaywrightSurface

_BINDING = "__cuaOperatorEvent"

_WATCH_JS = """
(() => {
  if (window.__cuaOperatorWatch) return;
  window.__cuaOperatorWatch = true;

  const attr = (el, n) => (el.getAttribute && el.getAttribute(n)) || '';
  const isPassword = (el) =>
    el && el.tagName && el.tagName.toLowerCase() === 'input' &&
    (attr(el, 'type') || '').toLowerCase() === 'password';

  const describe = (el) => {
    if (!el || !el.tagName) return 'unknown';
    const tag = el.tagName.toLowerCase();
    const name = attr(el, 'aria-label') || attr(el, 'name') || attr(el, 'id') || '';
    if (tag === 'form') {
      // Never innerText for a form -- that is every label and value it
      // contains, flattened into one string.
      const target = attr(el, 'action') || '';
      if (name) return 'form "' + name + '"';
      return target ? 'form -> ' + target : 'form';
    }
    if (tag === 'input' || tag === 'select' || tag === 'textarea') {
      // Deliberately never el.value: for a password field that would put
      // the secret into a description string, which nothing downstream
      // would think to mask.
      const kind = tag === 'input' ? (attr(el, 'type') || 'text') : tag;
      return name ? kind + ' field "' + name + '"' : kind + ' field';
    }
    let text = '';
    try { text = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60); } catch (e) {}
    if (text) return tag + ' "' + text + '"';
    return name ? tag + ' "' + name + '"' : tag;
  };

  const valueOf = (el) => {
    if (!el || !el.tagName || isPassword(el)) return null;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return el.value;
    return null;
  };

  const report = (kind, el, value, literal) => {
    try {
      window.__cuaOperatorEvent(JSON.stringify({
        kind: kind,
        target: describe(el),
        value: value,
        // Marks a value that is ours, not the user's -- a key name rather
        // than something typed. Python masks the second and not the first.
        literal: !!literal,
        password: isPassword(el),
        frame: window.name || null,
        url: location.href,
      }));
    } catch (e) {}
  };

  // Capture phase throughout, so a page handler calling stopPropagation()
  // can't hide what the operator did from the record.
  document.addEventListener('click', (e) => report('click', e.target, null), true);
  document.addEventListener('change', (e) => report('change', e.target, valueOf(e.target)), true);
  // A submit is its own fact, not a repeat of the click that caused it:
  // it can come from Enter in a field, from a button, or from script, and
  // it is the moment the form was actually committed.
  document.addEventListener('submit', (e) => report('submit', e.target, null), true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') report('key', e.target, 'Enter', true);
  }, true);
})();
"""


class _Install:
    """One installation per surface, routing events to whichever session is
    currently open.

    The indirection is load-bearing: `expose_function` can only register a
    name once per page, so a second escalation in the same run cannot
    re-register -- and without this, its events would keep arriving at the
    first escalation's record.
    """

    def __init__(self) -> None:
        self.sink = None

    def __call__(self, payload: str) -> None:
        if self.sink is not None:
            self.sink(payload)

    def attach(self, sink) -> None:
        self.sink = sink

    def detach(self) -> None:
        self.sink = None


_INSTALLS: "weakref.WeakKeyDictionary[PlaywrightSurface, _Install]" = weakref.WeakKeyDictionary()


def _install_for(surface: PlaywrightSurface) -> _Install:
    install = _INSTALLS.get(surface)
    if install is not None:
        return install

    install = _Install()
    surface.page.expose_function(_BINDING, install)
    # For documents loaded from here on, including frames.
    surface.page.add_init_script(_WATCH_JS)
    # And for the ones already open, which an init script never reaches.
    # The JS no-ops if it has run in that document already.
    for frame in surface.page.frames:
        try:
            frame.evaluate(_WATCH_JS)
        except Exception:
            continue

    surface.page.on("framenavigated", lambda frame: install(_navigation_payload(frame)))
    _INSTALLS[surface] = install
    return install


def _navigation_payload(frame) -> str:
    return json.dumps(
        {
            "kind": "navigate",
            "target": f"frame '{frame.name}'" if frame.name else "page",
            "value": None,
            "password": False,
            "frame": frame.name or None,
            "url": frame.url,
        }
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorSession:
    """Open while a human holds the session; closed when they hand back.

    Used as a context manager by the replay engine around `escalate()`, so
    every handler gets recording without any of them knowing about it and
    without changing the handoff contract.
    """

    def __init__(self, surface: PlaywrightSurface):
        self.surface = surface
        self.actions: list[OperatorAction] = []
        self.capture_error: str | None = None
        """Why nothing was captured, when that's the case. An empty action
        list should mean "the operator did nothing", so a session that
        couldn't listen has to say so rather than look identical to one
        where nothing happened.
        """

    def __enter__(self) -> "OperatorSession":
        try:
            _install_for(self.surface).attach(self._record)
        except Exception as exc:
            # Never let instrumentation break the handoff itself: a person
            # taking over a stuck run matters more than the record of it.
            self.capture_error = f"{type(exc).__name__}: {exc}"
        return self

    def __exit__(self, *exc_info) -> bool:
        install = _INSTALLS.get(self.surface)
        if install is not None:
            install.detach()
        return False

    def _record(self, payload: str) -> None:
        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            return
        raw = event.get("value")
        if event.get("password"):
            # The JS never sent it. Recorded as present-but-withheld rather
            # than omitted, so the log still shows a credential was entered.
            value = "***"
        elif not raw:
            value = None
        elif event.get("literal"):
            # A key name is our own vocabulary, not something the operator
            # typed. Masking "Enter" to "***er" would destroy the only
            # information the field carries while protecting nothing.
            value = str(raw)
        else:
            value = mask_partial(raw)
        self.actions.append(
            OperatorAction(
                at=_now(),
                kind=str(event.get("kind") or "unknown"),
                target=str(event.get("target") or "unknown"),
                value=value,
                frame=event.get("frame"),
                url=event.get("url"),
            )
        )
