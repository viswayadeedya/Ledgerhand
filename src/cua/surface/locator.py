import re

from cua.core.models import LocatorCandidate, LocatorStrategy


class LocatorResolutionError(RuntimeError):
    """Raised when no candidate in a ranked list resolves to exactly one
    element. This is deliberate: a locator matching zero or several elements
    is a hard failure to surface, never a guess to make silently.
    """


def build_locator(scope, candidate: LocatorCandidate):
    if candidate.strategy == LocatorStrategy.ROLE:
        return scope.get_by_role(candidate.role or "button", name=candidate.value)
    if candidate.strategy == LocatorStrategy.LABEL:
        return scope.get_by_label(candidate.value)
    if candidate.strategy == LocatorStrategy.TEXT:
        return scope.get_by_text(candidate.value)
    if candidate.strategy == LocatorStrategy.TABLE_POSITION:
        row, col = _parse_table_position(candidate.value)
        cell = scope.locator("table tr").nth(row).locator("td, th").nth(col)
        # The cell itself is rarely what we want to click/fill -- if it has
        # exactly one interactive descendant, target that instead.
        interactive = cell.locator("input, select, textarea, button, a")
        if interactive.count() == 1:
            return interactive
        return cell
    if candidate.strategy == LocatorStrategy.CSS:
        return scope.locator(candidate.value)
    raise ValueError(f"unknown locator strategy: {candidate.strategy}")


def _parse_table_position(value: str) -> tuple[int, int]:
    m = re.match(r"row=(\d+),col=(\d+)", value)
    if not m:
        raise ValueError(f"malformed table_position value: {value!r}")
    return int(m.group(1)), int(m.group(2))


def resolve(scope, candidates: list[LocatorCandidate]):
    """Tries each candidate in ranked order; the first that resolves to
    EXACTLY one element wins. Returns (locator, the winning candidate).

    Never picks among multiple matches and never falls back to "first
    result" -- an ambiguous match is exactly the kind of thing that should
    become a LocatorResolutionError (a hard failure) or route to a human,
    not something automation guesses its way through.
    """
    attempts: list[str] = []
    for candidate in candidates:
        try:
            locator = build_locator(scope, candidate)
            count = locator.count()
        except Exception as exc:
            attempts.append(f"{candidate.strategy.value}={candidate.value!r} -> error: {exc}")
            continue
        if count == 1:
            return locator, candidate
        attempts.append(f"{candidate.strategy.value}={candidate.value!r} -> {count} matches")
    raise LocatorResolutionError(
        "no candidate resolved to exactly one element: " + "; ".join(attempts)
    )
