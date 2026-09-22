"""Assigns short reference tokens to page elements/frames, the way the
browser_toolset_20260801 API expects: read_page/find return refs, and later
left_click/form_input calls act on them. Anthropic's docs are explicit that
the *executor* (us) owns this registry -- the API only requires refs to be
stable within a page/DOM lifetime, which we approximate by clearing on any
action likely to have navigated or changed the DOM (see browser_tools.py).
"""

from dataclasses import dataclass, field

from cua.core.models import LocatorCandidate, Target


@dataclass
class RefEntry:
    kind: str  # "frame" | "element"
    frame: str | None
    candidates: list[LocatorCandidate] = field(default_factory=list)
    label: str = ""
    is_secret: bool = False


class RefRegistry:
    def __init__(self):
        self._entries: dict[str, RefEntry] = {}
        self._counter = 0

    def clear(self) -> None:
        self._entries.clear()

    def register_frame(self, name: str) -> str:
        ref = f"frame_{name}"
        self._entries[ref] = RefEntry(kind="frame", frame=name, label=f'frame "{name}"')
        return ref

    def register_element(
        self, frame: str | None, candidates: list[LocatorCandidate], label: str, is_secret: bool = False
    ) -> str:
        self._counter += 1
        ref = f"el_{self._counter}"
        self._entries[ref] = RefEntry(kind="element", frame=frame, candidates=candidates, label=label, is_secret=is_secret)
        return ref

    def get(self, ref: str) -> RefEntry:
        entry = self._entries.get(ref)
        if entry is None:
            raise KeyError(ref)
        return entry

    def resolve_target(self, ref: str) -> Target:
        entry = self.get(ref)
        if entry.kind != "element":
            raise KeyError(ref)
        return Target(frame=entry.frame, candidates=entry.candidates)

    def resolve_frame(self, ref: str) -> str:
        entry = self.get(ref)
        if entry.kind != "frame":
            raise KeyError(ref)
        return entry.frame
