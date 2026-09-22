# Human handoff: ambiguous duplicate member

Two real replay runs of the actual committed artifact
([`/artifacts/member-savings-lookup.yaml`](../../artifacts/member-savings-lookup.yaml)),
against the live fake app with `duplicate_members` armed, demonstrating
Part 7's escalation and control-transfer mechanism end to end. No LLM is
involved in either run -- this is deterministic replay hitting a state it
correctly recognizes it shouldn't resolve on its own.

Both use `MockOperatorHandoff` (see `scripts/demo_handoff.py`) rather than
`TerminalOperatorHandoff`/`InteractivePauseHandoff`, since this script runs
unattended to produce evidence -- no person is at a keyboard clicking
"Resume". The mechanism is identical either way: `escalate()` receives the
exact same live `PlaywrightSurface` the replay run was already using, and
the run genuinely pauses (no further automated steps happen) until it
returns a decision. Only *how* the decision gets made differs -- see
`DECISIONS.md` Part 7 and `cua/handoff/mock.py`'s docstring.

## abandoned/ -- operator is unavailable

```python
def operator(request, surface):
    return HandoffDecision(action=HandoffAction.ABANDON, ...)
```

The engine detects the ambiguous "Multiple members matched" page (two
records both named Garcia), raises an `EscalationRequest` with full context
(capability, reason, current URL, a screenshot), and waits. The operator
declines. Result: `NEEDS_HUMAN`, with the escalation recorded and the
declined decision preserved in `result.json` and
`escalation_outcome_ambiguous_duplicate.json`.

## resolved/ -- operator takes over the live session and picks one

```python
def operator(request, surface):
    main_frame = surface.page.frame(name="main")
    main_frame.get_by_role("link", name="View").first.click()
    return HandoffDecision(action=HandoffAction.MANUAL_RESOLVED, ...)
```

This is the key proof: the operator callback doesn't just approve in the
abstract, it reaches into `surface` -- the *exact object* replay was already
driving, not a new browser -- and clicks a real element on it, precisely
what a human taking over the session would do. Replay then resumes,
re-checks the page, and reads the resulting member's real balance live.

Result: `RECOVERED` (reached the checkpoint, but only after a human stepped
in), with `outputs.savings_balance == "$2340.18"` -- Maria Garcia's real,
live-read balance, proving control genuinely returned to automation
afterward and picked up correctly on the same session, not a fresh one.

## Files

Each folder has the same shape:
- `escalation_outcome_ambiguous_duplicate.json` -- the full intervention
  request (capability, reason, URL, screenshot path) plus the operator's
  eventual decision, written *before* the operator acts so context survives
  even if they never respond.
- `result.json` -- the full `ReplayResult`, including `escalations`.
- `step_001.png` .. `step_008.png` -- screenshots from every step replay
  took, including the ambiguous results page itself.
