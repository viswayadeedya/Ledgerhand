from dataclasses import dataclass, fields


@dataclass
class FaultState:
    """Server-side toggles for runtime error/exceptional-state simulation.

    Each fault fires once and auto-disarms (`consume`), so the same artifact
    inputs can be replayed twice against the fake app -- once clean, once
    against the injected condition -- without extra state to reset by hand.
    """

    member_not_found: bool = False
    session_expired: bool = False
    popup: bool = False
    slow_load: bool = False
    duplicate_members: bool = False
    wrong_member: bool = False
    """Serves a different member's detail page than the one requested, with
    no error, no redirect, and a perfectly normal-looking page. The nastiest
    failure in this set: every other fault is visible, this one is only
    detectable by checking that the record on screen is the record that was
    asked for.
    """
    extra_row: bool = False
    """Inserts an extra row above the balances on the member detail page --
    a stand-in for the benign, extremely common way a tenant's version of
    the same vendor product differs. Harmless to a human reading the page;
    silently fatal to a locator that finds values by row number.
    """
    permission_denied: bool = False
    """Serves an "Access denied" page instead of the member record: the
    teller is signed in perfectly well, but isn't entitled to this one
    record. A legitimate answer the caller needs, not a breakage -- which
    is why replay treats it as a business outcome rather than a failure.
    """
    app_error: bool = False
    """Serves a 500-status error page: the app itself fell over. The
    opposite classification to permission_denied -- nothing about the
    request was wrong and retrying the same inputs might work, but nothing
    here can decide that, so it stops and surfaces the error.
    """

    def as_dict(self) -> dict[str, bool]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def set(self, name: str, armed: bool) -> None:
        if not hasattr(self, name):
            raise ValueError(f"unknown fault: {name}")
        setattr(self, name, armed)

    def reset(self) -> None:
        for f in fields(self):
            setattr(self, f.name, False)

    def consume(self, name: str) -> bool:
        armed = getattr(self, name)
        if armed:
            setattr(self, name, False)
        return armed


@dataclass
class SettingState:
    """Knobs that shape *how* an armed fault behaves, as opposed to whether
    it fires.

    Kept separate from FaultState rather than added as extra fields on it:
    every fault is a bool that fires once and auto-disarms, and `as_dict()`
    returning `dict[str, bool]` is something the admin page and the tests
    both rely on. A float sitting in that dict would quietly break the
    "arm/disarm everything" contract -- a duration isn't armed or disarmed,
    it just has a value. Settings persist until reset instead of firing
    once, because they describe the fault, not an occurrence of it.
    """

    slow_load_seconds: float = 2.0
    """How long an armed `slow_load` stalls the search response.

    The default sits deliberately under replay's locator wait budget (5s
    for a step's own target, 3s for checkpoint/business-outcome
    resolution), so the default slow load is the *recoverable* kind: replay
    waits it out and still succeeds. Raising it past that budget is what
    turns the same fault into the unrecoverable kind, which is the only way
    to exercise both halves of the brief's "transient slowness" vs.
    "slow/failed load" distinction with one fault.
    """

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def set(self, name: str, value: float) -> None:
        if not hasattr(self, name):
            raise ValueError(f"unknown setting: {name}")
        setattr(self, name, value)

    def reset(self) -> None:
        defaults = SettingState()
        for f in fields(self):
            setattr(self, f.name, getattr(defaults, f.name))


FAULTS = FaultState()
SETTINGS = SettingState()
