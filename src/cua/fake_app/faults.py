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


FAULTS = FaultState()
