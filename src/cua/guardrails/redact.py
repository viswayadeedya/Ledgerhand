import re
from typing import Any

REDACTED = "***REDACTED***"

# Field names whose values are always masked, regardless of content, when
# they show up in anything we log or write into an artifact.
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "session",
    "cookie",
    "authorization",
    "ssn",
    "social_security_number",
}

# Best-effort patterns for sensitive values that show up inside free text
# (e.g. a screenshot's OCR'd text or a log message), not just structured
# fields. Not exhaustive -- see REPORT.md Section 6 for the stated limits.
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,16}\b")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact_value(value: Any) -> Any:
    """Recursively redacts sensitive keys/patterns in dicts, lists, and strings."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive_key(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    text = _SSN_PATTERN.sub(REDACTED, text)
    text = _CARD_PATTERN.sub(REDACTED, text)
    return text


# What a value LOOKS like, which is safe to publish even when the value
# itself isn't. Deliberately not imported from artifacts.schema's
# OutputType: guardrails sits below the artifact layer and shouldn't depend
# on it, and these answer a different question -- "date" is a shape worth
# naming here and is not a type an artifact can declare.
_SHAPES: list[tuple[str, re.Pattern]] = [
    ("money", re.compile(r"^-?\$?-?[\d,]+\.\d{2}$")),
    ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("integer", re.compile(r"^-?[\d,]+$")),
    ("text", re.compile(r".*", re.DOTALL)),
]


def shape_of(value: str | None) -> str:
    """Names the form of a value without revealing it.

    This is what makes masking survivable for evidence. "***14" and
    "***18" are equally unreadable, so a masked record of a locator bug
    would hide the very thing it exists to show; "***14 [shape: date]"
    next to "***18 [shape: money]" makes "a date landed in a money field"
    plain without putting either figure on disk.
    """
    if not value:
        return "empty"
    text = str(value).strip()
    for name, pattern in _SHAPES:
        if pattern.match(text):
            return name
    return "text"


def mask_sensitive(values: dict[str, str], sensitive_names, shape_hint: bool = True) -> dict[str, str]:
    """Masks the named values, leaving the rest alone.

    Applied at the boundaries that *write things down* -- the terminal, a
    result file -- and never to the data the capability returns to its
    caller. The caller asked for the balance and needs the balance; the
    disk and the scrollback don't.
    """
    names = set(sensitive_names or ())
    return {
        name: (mask_partial(value, shape_hint=shape_hint) if name in names else value)
        for name, value in values.items()
    }


def mask_partial(value: str | None, keep: int = 2, shape_hint: bool = False) -> str:
    """Masks a sensitive value but keeps its last few characters.

    Used wherever a sensitive value has to be *written down* -- an error
    message, a log line, an evidence file. Full redaction there would make
    a failure undebuggable (an operator can't tell which request went
    wrong), while the full value shouldn't be persisted at all. Keeping the
    tail lets a human correlate "***01" with the member ID they asked
    about, without the record itself landing on disk.

    `shape_hint` appends what the value looked like ("***18 [shape:
    money]"). Off by default because an error message already says what was
    expected and what shape it got; on for outputs, where a masked record
    would otherwise be unable to show a wrong-shaped value at all.
    """
    if not value:
        return REDACTED
    text = str(value)
    masked = "***" if len(text) <= keep else "***" + text[-keep:]
    return f"{masked} [shape: {shape_of(text)}]" if shape_hint else masked
