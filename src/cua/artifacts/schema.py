"""The capability artifact: a typed, versioned, reviewable description of a
reusable flow -- decoupled from the raw discovery transcript on purpose.
Steps/checkpoint/outputs reuse cua.core.models.Action/Target/LocatorCandidate
directly rather than inventing parallel types, since replay (Part 6) needs
exactly the same "how do I find this element" contract discovery already
produces.
"""

import re
import warnings
from enum import Enum

import yaml
from pydantic import BaseModel, ValidationError, field_validator

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
    """A value safe to publish, for a reader working out how to call this.

    Never filled from the discovery literal -- the recorder is given that
    literal so it can recognize and parameterize it away, and storing it
    back here would undo exactly that work. An example has to be chosen to
    be obviously fake (00000 matches no seeded member) so nobody mistakes
    the documentation for data.
    """

    pattern: str | None = None
    """A regex the caller's value must match, checked before a browser opens.

    This is the brief's "validation error" handled at the only place it can
    be handled cheaply: a member ID of "abc" cannot become a correct answer
    no matter how well the rest of the run goes, so spending a browser
    launch, a login and six steps to discover that is pure waste -- and the
    failure it eventually produced would have been some downstream
    "element not found", which describes the symptom rather than the cause.

    Deliberately the caller's contract, not the app's: this says what this
    capability accepts, which is not the same as what the app would reject.
    An input that passes here can still come back as a business outcome.
    """

    sensitive: bool = False
    """Marks an input that must be masked anywhere it's written down.

    Separate from OutputSpec.sensitive because the same value can be both
    and the declarations serve different boundaries: a member ID read *off*
    the page is masked when printed, and the same ID handed *in* by the
    caller is masked when a result file records what was asked for. Only
    the second is covered here. Without it, masking an output while filing
    the identical value under "inputs" two lines up would be theatre.
    """


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


CURRENT_SCHEMA_VERSION = "1.2"
"""The schema this code reads and writes.

Every bump so far has been *minor*, because every field added has been
optional and defaulted to the old behaviour -- so an artifact written
against an earlier minor still loads and still runs exactly as it did.
"""

_MINOR_ADDITIONS = {
    1: "identity assertions (must_equal), which is what proves a replay reached the right record",
    2: "input patterns, which reject a malformed input before a browser opens",
}
"""What each minor version added, so an outdated-artifact warning can name
the specific checks *that* file predates.

A single hardcoded sentence was fine while there was one bump to describe;
with two it would have started telling a 1.1 artifact it lacks something it
has. Naming the real gap is the whole value of the warning over "this is
outdated".
"""


class SchemaVersionError(ValueError):
    """An artifact this code can't safely run. Deliberately not a warning:
    the difference between "loaded it" and "refused it" has to be visible
    at the door, not discovered from a wrong answer later.
    """


class OutdatedArtifactWarning(UserWarning):
    """An artifact older than the current schema. It runs, but it predates
    checks that exist now -- most importantly the identity assertion, so it
    may not be proving it landed on the right record.
    """


def check_schema_version(value: str) -> str:
    current_major, current_minor = (int(p) for p in CURRENT_SCHEMA_VERSION.split("."))
    try:
        major, minor = (int(p) for p in str(value).split("."))
    except ValueError:
        raise SchemaVersionError(
            f"artifact schema_version {value!r} is not a MAJOR.MINOR version; "
            f"this code reads {CURRENT_SCHEMA_VERSION}"
        ) from None

    if major != current_major:
        raise SchemaVersionError(
            f"artifact schema_version {value} has a different major version than "
            f"{CURRENT_SCHEMA_VERSION}; its structure isn't compatible with this code. "
            "Rebuild it from its source run log with python -m cua.artifacts."
        )
    if minor > current_minor:
        # Refusing forward-compatibility on purpose. Pydantic would happily
        # drop fields it doesn't know, and the fields most likely to be new
        # are *checks* -- silently ignoring a must_equal written by a newer
        # recorder means running without the safety it was added for, and
        # reporting success.
        raise SchemaVersionError(
            f"artifact schema_version {value} is newer than this code's {CURRENT_SCHEMA_VERSION}. "
            "It may rely on checks this version doesn't implement, which would be ignored "
            "rather than applied. Upgrade cua before running it."
        )
    if minor < current_minor:
        missing = "; ".join(
            text for added_at, text in sorted(_MINOR_ADDITIONS.items()) if added_at > minor
        )
        warnings.warn(
            f"artifact schema_version {value} predates this code's {CURRENT_SCHEMA_VERSION}; "
            f"it will run, but without the checks added since: {missing}. "
            "Rebuild it from its source run log to pick those up.",
            OutdatedArtifactWarning,
            stacklevel=2,
        )
    return str(value)


class CapabilityArtifact(BaseModel):
    schema_version: str = CURRENT_SCHEMA_VERSION
    id: str
    version: int = 1

    _check_schema_version = field_validator("schema_version")(check_schema_version)
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


def load_yaml(text: str) -> CapabilityArtifact:
    """Parses an artifact, surfacing a version refusal as itself.

    Pydantic wraps a validator's exception in a ValidationError whose
    printed form buries the reason under field paths and a docs link. A
    person told "this artifact is newer than your code" can act on it; the
    same person shown a `value_error` traceback generally can't.
    """
    try:
        return CapabilityArtifact.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        for error in exc.errors():
            cause = (error.get("ctx") or {}).get("error")
            if isinstance(cause, SchemaVersionError):
                raise cause from None
        raise


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
