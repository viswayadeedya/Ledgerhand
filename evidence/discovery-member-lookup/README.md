# Discovery run: member lookup

A genuine LLM-driven run (`claude-sonnet-5`, via the `browser_toolset_20260801`
client toolset) against the live fake app, using the `cua.agent` discovery
loop from Part 4. No steps were scripted or hand-edited after the fact.

This run's log was turned into the capability artifact at
[`/artifacts/member-savings-lookup.yaml`](../../artifacts/member-savings-lookup.yaml)
(Part 5) -- see that file and `/artifacts/README.md` for the reusable,
LLM-free version of this same flow.

**Goal:** "Look up member 10001 and read their current savings balance."

**Command:**
```
python -m cua.agent \
  --goal "Look up member 10001 and read their current savings balance." \
  --target "http://127.0.0.1:5055/login" \
  --domain "127.0.0.1:5055" \
  --username teller1 --password teller123 \
  --max-steps 20 \
  --evidence-dir "evidence/runs/discovery-member-lookup"
```

**Outcome:** `success`, in 15 recorded steps.

```json
{
  "member_id": "10001",
  "name": "Maria Garcia",
  "savings_balance": "$2340.18",
  "checking_balance": "$512.44"
}
```

**Files:**
- `run_log.json` -- the full structured step log (`RecordedStep` per action:
  tool name, redacted input, the translated `Action`, and the `ActionResult`).
- `step_001.png` .. `step_008.png` -- screenshots captured at each
  navigate/click/screenshot step (`read_page`/`find`/`form_input` don't
  screenshot, so there are fewer images than steps).

**What this run did** (from `run_log.json`): navigated to `/login`, read the
page, filled the username and password fields via `form_input` (direct,
ref-targeted), clicked Sign On, read the frame list, drilled into the `nav`
frame, filled the member-ID search box, clicked Search, read the `main`
frame, clicked into the member record, and reported success with the
balance read straight off the page.

## Getting here took three redaction fixes

This is the fourth attempt, and the earlier ones are worth recording because
each surfaced a genuine bug, not a flaky test:

1. **First run never logged in at all** -- it had no credentials to use.
   Fixed by adding a `credentials` parameter that's injected into the system
   prompt (see `agent/prompts.py`); the model has to be told the sign-on
   values the way a real operator would give them, not guess.
2. **Early runs burned most of their step budget fumbling the login form.**
   The username/password fields have no label, id, or aria-label at all (by
   design -- Part 1's "inconsistent locator surface"), so click-then-`type`
   kept landing in the wrong field. Fixed by recommending `form_input` over
   click-then-`type` in the system prompt when a ref is already known --
   `form_input` sets a value on an explicit element reference directly, so
   it can't be thrown off by focus landing somewhere else.
3. **The password leaked into the run log three separate times, at three
   separate layers**, each requiring its own fix:
   - `RecordedStep.tool_input` -- the model's own `form_input`/`type` call
     with the literal value. Fixed by redacting it when the target/focused
     field is a `type=password` input.
   - `ActionResult.observation.elements[].text` -- a *different* code path
     (`Surface.observe()`'s DOM scan) reads every input's live `.value`,
     which for a password field is always plaintext even though the browser
     only masks it visually. This one wasn't caught by the tool_input fix at
     all, because it's a fresh re-scan of the page, not a copy of what was
     typed. Fixed at the source: the scan JS now returns `''` for any
     `input[type=password]`, so no downstream consumer (current or future)
     can leak it by forgetting to redact.
   - `RecordedStep.action.value` -- the internal `Action` object needs the
     *real* value to actually perform the fill, and that same object was
     being stored straight into the log. Fixed by logging a redacted copy of
     the action (`action.model_copy(update={"value": "***REDACTED***"})`)
     built *after* execution, never the one used to act.

   Each fix has a regression test in `tests/test_browser_tools.py` and
   `tests/test_surface.py`. See `DECISIONS.md` Part 4 for the full story --
   it's a concrete illustration of why a single redaction pass in one place
   isn't enough when the same secret value can flow through a system on more
   than one path.
