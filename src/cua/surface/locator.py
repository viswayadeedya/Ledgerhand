import re
import time

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
    if candidate.strategy == LocatorStrategy.TABLE_LABEL:
        label, col = _parse_table_label(candidate.value)
        # exact=True, not a substring match: "Balance" must not match a row
        # labelled "Savings Balance". A label that's merely *contained* in
        # two rows would resolve to two cells, and resolve()'s exactly-one
        # rule would reject it -- but silently matching the wrong row when
        # only one happens to contain the substring is the worse failure,
        # and exactness rules it out up front.
        row = scope.locator("tr").filter(has=scope.get_by_text(label, exact=True))
        return _cell_or_its_control(row.locator("td, th").nth(col))
    if candidate.strategy == LocatorStrategy.TABLE_POSITION:
        row, col = _parse_table_position(candidate.value)
        return _cell_or_its_control(scope.locator("table tr").nth(row).locator("td, th").nth(col))
    if candidate.strategy == LocatorStrategy.CSS:
        return scope.locator(candidate.value)
    raise ValueError(f"unknown locator strategy: {candidate.strategy}")


def _cell_or_its_control(cell):
    """The cell itself is rarely what we want to click or fill -- if it
    holds exactly one interactive element, target that instead.
    """
    interactive = cell.locator("input, select, textarea, button, a")
    if interactive.count() == 1:
        return interactive
    return cell


def _parse_table_position(value: str) -> tuple[int, int]:
    m = re.match(r"row=(\d+),col=(\d+)", value)
    if not m:
        raise ValueError(f"malformed table_position value: {value!r}")
    return int(m.group(1)), int(m.group(2))


def _parse_table_label(value: str) -> tuple[str, int]:
    # Greedy on the label so a label containing ",col=" still parses -- the
    # real separator is the LAST one.
    m = re.match(r"^label=(.*),col=(\d+)$", value)
    if not m:
        raise ValueError(f"malformed table_label value: {value!r} (expected 'label=<text>,col=<n>')")
    return m.group(1).strip(), int(m.group(2))


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


def resolve_with_wait(scope, candidates: list[LocatorCandidate], timeout_ms: int = 5000, poll_ms: int = 200):
    """Like resolve(), but retries for up to timeout_ms before giving up.

    resolve()'s .count() check is instantaneous -- it reports whatever's in
    the DOM at that exact moment, with no auto-wait. That's correct for a
    genuinely-missing element, but wrong for a target frame that's still
    mid-navigation: a real page load takes real time, and a single instant
    check can race and report "not found" a moment before the content
    actually appears. This is what turns that race into a bounded wait
    instead of a false failure -- used wherever replay/discovery act on a
    page that just navigated, never for the strict "does this resolve right
    now" checks locator.py's own tests care about.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            return resolve(scope, candidates)
        except LocatorResolutionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_ms / 1000)
