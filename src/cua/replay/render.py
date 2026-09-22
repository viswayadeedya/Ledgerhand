"""Substitutes {{inputs.x}} / {{secrets.x}} placeholders with real values at
replay time -- the exact inverse of what the recorder does when it builds an
artifact. This is the only place a real secret value and a step's template
ever meet; the rendered Action lives only in memory for the duration of the
act() call that consumes it.
"""

import re

from cua.core.models import Action

_PLACEHOLDER = re.compile(r"\{\{(inputs|secrets)\.([a-zA-Z0-9_]+)\}\}")


class RenderError(RuntimeError):
    pass


def render_value(value: str | None, inputs: dict[str, str], secrets: dict[str, str]) -> str | None:
    if value is None:
        return None

    def _sub(match: re.Match) -> str:
        namespace, name = match.group(1), match.group(2)
        source = inputs if namespace == "inputs" else secrets
        if name not in source:
            raise RenderError(f"missing {namespace}.{name} -- required by this artifact but not supplied")
        return source[name]

    return _PLACEHOLDER.sub(_sub, value)


def render_action(action: Action, inputs: dict[str, str], secrets: dict[str, str]) -> Action:
    new_value = render_value(action.value, inputs, secrets)
    new_url = render_value(action.url, inputs, secrets)
    if new_value == action.value and new_url == action.url:
        return action
    return action.model_copy(update={"value": new_value, "url": new_url})
