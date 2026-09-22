from abc import ABC, abstractmethod

from cua.core.models import Action, ActionResult, Observation


class Surface(ABC):
    """The seam between 'what to do' (agent loop, replay engine) and 'how to
    actually do it' on a given UI. A future legacy-web or desktop surface
    implements the same two methods differently underneath; nothing above
    this layer needs to know the difference.
    """

    @abstractmethod
    def observe(self) -> Observation: ...

    @abstractmethod
    def act(self, action: Action, human_approved: bool = False) -> ActionResult: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "Surface":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
