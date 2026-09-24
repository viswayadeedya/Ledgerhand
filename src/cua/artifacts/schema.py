"""The capability artifact: a typed, versioned, reviewable description of a
reusable flow -- decoupled from the raw discovery transcript on purpose.
Steps/checkpoint/outputs reuse cua.core.models.Action/Target/LocatorCandidate
directly rather than inventing parallel types, since replay (Part 6) needs
exactly the same "how do I find this element" contract discovery already
produces.
"""

import re
from enum import Enum

import yaml
from pydantic import BaseModel, field_validator

from cua.core.models import Action, Target


class OutputType(str, Enum):
    """What an output's value should look like.

    Doubles as format validation at replay time: a value that doesn't
    match its declared type is a HARD_FAILURE and is never returned. That
    matters most when a page shifts under a positional locator -- reading
    a date where a balance should be is the kind of mistake that's obvious
    to a type check and invisible to everything else.
    """

    STRING = "string"  # anything; no validation
    MONEY = "money"
    INTEGER = "integer"


# Deliberately loose about presentation (a leading $, thousands commas) and
# strict about shape. Parenthesised negatives -- "($5.00)", used by some
# ledgers -- are NOT covered; a page using those needs its own type rather
# than a pattern loose enough to let anything through.
_TYPE_PATTERNS: dict[OutputType, re.Pattern] = {
    OutputType.MONEY: re.compile(r"^-?\$?-?[\d,]+\.\d{2}$"),
    OutputType.INTEGER: re.compile(r"^-?[\d,]+$"),
}


def format_error(output_type: OutputType, value: str) -> str | None:
    """Returns a human-readable complaint if `value` doesn't look like
    `output_type`, or None if it's fine.
    """
    pattern = _TYPE_PATTERNS.get(output_type)
    if pattern is None or pattern.match(value.strip()):
        return None
    return f"{value!r} does not look like {output_type.value}"


class StepRisk(str, Enum):
    """How a step was classified, for a human skimming the artifact.

    Deliberately not a fourth thing replay consults -- the live policy
    engine re-decides every action at replay time, exactly as before. This
    is review metadata: it answers "should I be nervous about step 7"
    before anyone runs it.
    """

    SAFE = "safe"
    RISKY = "risky"
    UNVERIFIED = "unverified"
    """Nothing was observed for this step during discovery, so no honest
    claim can be made about where it goes. Says "nobody knows" rather than
    letting an unchecked step look checked.
    """


class ArtifactStep(Action):
    """An Action plus the thing a reviewer needs and replay doesn't: how
    risky this step was judged to be, and on what evidence.

    A subclass rather than a wrapper object on purpose. Replay, the policy
    engine and the surface all keep receiving something that *is* an
    Action, so none of them change; and an artifact written before these
    fields existed still validates, with both defaulting to empty. A
    wrapper would have been tidier and would have broken every existing
    artifact -- too much for a minor schema bump.

    `description` is inherited, not redeclared. It has been on Action all
    along for the model to explain its own intent during discovery, and has
    been null on every recorded step; the recorder now fills it from what
    the step actually did rather than adding a second description field
    beside an empty one.
    """

    risk: StepRisk | None = None
    risk_note: str = ""
    """Where the risk judgement came from, e.g. "destination
    /app/member/10001 observed at discovery; re-checked at replay". Kept
    separate from `risk` so the label stays machine-readable while the
    provenance stays human-readable.
    """


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
    type: OutputType = OutputType.STRING
    description: str = ""
    target: Target

    must_equal: str | None = None
    """An assertion that this output matches something the caller supplied,
    written as a template (e.g. "{{inputs.member_id}}").

    This is what turns a checkpoint from "a savings balance is on screen"
    into "the *right member's* savings balance is on screen". Every
    member's detail page says "Savings Balance", so without this, landing
    on the wrong record returns SUCCESS with someone else's money. A
    mismatch is a HARD_FAILURE and no outputs are returned at all -- in
    this domain a confidently wrong answer is worse than a crash.

    Only possible when the page actually displays the identifier; a
    capability whose result page never echoes its input can't prove
    identity this way.
    """

    sensitive: bool = False
    """Marks a value that must never be written down in full. It still
    flows to the caller intact (that's the point of the capability) -- but
    anywhere it gets persisted or printed, it's masked to its last couple
    of characters. See guardrails.redact.mask_partial.
    """


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

    steps: list[ArtifactStep]

    @field_validator("steps", mode="before")
    @classmethod
    def _accept_plain_actions(cls, value):
        """A bare Action is a perfectly good step -- it just doesn't carry a
        risk label. Accept one and let the new fields default, so code that
        builds an artifact directly (tests, scripts) doesn't have to know
        about ArtifactStep, and so an artifact written before these fields
        existed loads unchanged.
        """
        if not isinstance(value, list):
            return value
        return [
            ArtifactStep(**item.model_dump())
            if isinstance(item, Action) and not isinstance(item, ArtifactStep)
            else item
            for item in value
        ]

    checkpoint: Target
    checkpoint_description: str

    business_outcomes: list[BusinessOutcomeSpec] = []

    provenance: ProvenanceInfo


# Written out even when they equal their defaults: they identify which
# contract this file is, and a reader shouldn't have to know the defaults
# to answer "what version is this?".
_ALWAYS_WRITTEN = ("schema_version", "version")


def to_yaml(artifact: CapabilityArtifact) -> str:
    """Serializes an artifact for review, leaving out every field still at
    its default.

    The point is that the artifact is meant to be *read* by a human before
    it's trusted. A dump of every field buries the four lines that matter
    under `role: null`, `frame: null`, `sensitive: false` and
    `description: ''` repeated a few dozen times, and a reviewer who has to
    skim past noise stops reading carefully.

    `exclude_defaults` rather than "drop anything falsy": a `value: ''` on
    a fill step is a real instruction (clear this box) and differs from the
    default of None, so it survives -- where a blanket empty-check would
    silently change what the artifact does.
    """
    lean = artifact.model_dump(mode="json", exclude_defaults=True)
    full = artifact.model_dump(mode="json")
    ordered = {
        name: (full[name] if name in _ALWAYS_WRITTEN else lean[name])
        for name in full  # model_dump preserves declaration order
        if name in lean or name in _ALWAYS_WRITTEN
    }
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)
