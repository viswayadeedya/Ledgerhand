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
