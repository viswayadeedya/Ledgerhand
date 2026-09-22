"""The capability artifact: a typed, versioned, reviewable description of a
reusable flow -- decoupled from the raw discovery transcript on purpose.
Steps/checkpoint/outputs reuse cua.core.models.Action/Target/LocatorCandidate
directly rather than inventing parallel types, since replay (Part 6) needs
exactly the same "how do I find this element" contract discovery already
produces.
"""

from pydantic import BaseModel

from cua.core.models import Action, Target


class InputSpec(BaseModel):
    """A parameter the caller supplies per invocation (e.g. a member ID)."""

    name: str
    type: str = "string"
    description: str = ""
    example: str | None = None


class SecretSpec(BaseModel):
    """A credential the caller supplies out-of-band, never stored here."""

    name: str
    description: str = ""


class OutputSpec(BaseModel):
    """A value replay reads off the page at the checkpoint, with its own
    locator -- replay always re-reads this live, it never just repeats
    whatever value discovery happened to see.
    """

    name: str
    type: str = "string"
    description: str = ""
    target: Target


class BusinessOutcomeSpec(BaseModel):
    """A named, legitimate non-success ending -- "no such member" is data
    the caller needs, not a crash. `detect` is how replay recognizes it (the
    same Target shape as everything else); `requires_human` marks outcomes
    ambiguous enough that automation shouldn't just report them and move on
    (e.g. multiple matching records) -- replay escalates those to
    NEEDS_HUMAN instead of returning them as a normal answer.
    """

    name: str
    description: str = ""
    detect: Target
    requires_human: bool = False


class ProvenanceInfo(BaseModel):
    discovered_at: str
    discovery_model: str
    source_run_log: str | None = None


class CapabilityArtifact(BaseModel):
    schema_version: str = "1.0"
    id: str
    version: int = 1
    title: str
    description: str

    target_domain: str
    entry_url: str

    inputs: list[InputSpec] = []
    secrets: list[SecretSpec] = []
    outputs: list[OutputSpec] = []

    steps: list[Action]

    checkpoint: Target
    checkpoint_description: str

    business_outcomes: list[BusinessOutcomeSpec] = []

    provenance: ProvenanceInfo
