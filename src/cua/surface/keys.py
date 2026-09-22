"""Translates the browser toolset's key-name format ('ctrl+a', 'Enter',
space-separated sequences like 'Backspace Backspace') into strings
Playwright's keyboard.press() accepts ('Control+A', 'Enter', pressed one
at a time). Covers the common cases our fake app and typical discovery
flows need; not a complete keyboard-layout-aware implementation.
"""

_MODIFIER_MAP = {"ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt", "super": "Meta", "cmd": "Meta", "meta": "Meta"}

_KEY_ALIASES = {
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "space": "Space",
}


def translate_key_spec(spec: str) -> str:
    parts = [p for p in spec.split("+") if p]
    if not parts:
        return spec
    *mods, main = parts
    translated_mods = [_MODIFIER_MAP.get(m.lower(), m.capitalize()) for m in mods]
    main_translated = _KEY_ALIASES.get(main.lower(), main)
    return "+".join([*translated_mods, main_translated])


def translate_key_sequence(text: str) -> list[str]:
    return [translate_key_spec(spec) for spec in text.split() if spec]
