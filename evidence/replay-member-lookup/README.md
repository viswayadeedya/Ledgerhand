# Deterministic replay: member lookup

Three real runs of `python -m cua.replay` against
[`/artifacts/member-savings-lookup.yaml`](../../artifacts/member-savings-lookup.yaml)
(Part 5) and the live fake app -- **no LLM involved in any of these**. Each
folder has the screenshots captured at every step plus `result.json`, the
full `ReplayResult` (outcome, outputs, recovery events, or error detail).

## success/ -- member 10001

```
python -m cua.replay --artifact artifacts/member-savings-lookup.yaml \
  --input member_id=10001 --secret username=teller1 --secret password=teller123 \
  --evidence-dir evidence/replay-member-lookup/success \
  --out evidence/replay-member-lookup/success/result.json
```

Outcome: `success`, 7/7 steps, outputs read live off the page
(`savings_balance: ***18 [shape: money]`). The artifact marks the balances
and the member's name and ID `sensitive`, so everything written here is
masked; add `--show-sensitive` to see the figures on your own terminal.
Replaying the same artifact with `member_id=10002` instead (not saved here,
but easy to reproduce) returns a different, correct balance -- proof this
reads the real page each time rather than repeating a memorized answer from
when it was recorded.

## business-outcome-not-found/ -- member 55555 (doesn't exist)

```
python -m cua.replay --artifact artifacts/member-savings-lookup.yaml \
  --input member_id=55555 --secret username=teller1 --secret password=teller123 \
  --evidence-dir evidence/replay-member-lookup/business-outcome-not-found \
  --out evidence/replay-member-lookup/business-outcome-not-found/result.json
```

Outcome: `business_outcome` (`member_not_found`), stopping at step 6/7 --
there's no "View" result to click when the search comes back empty, so the
engine checks the artifact's declared business outcomes right there instead
of just reporting a broken locator, recognizes the "No member found" page,
and returns it as a legitimate answer. This is the "bad input / not-found
result" exceptional-state demonstration.

## recovered-popup/ -- an unexpected dialog mid-flow

```
curl -X POST http://127.0.0.1:5055/admin/faults/api \
  -H "Content-Type: application/json" -d '{"fault":"popup","armed":true}'
python -m cua.replay --artifact artifacts/member-savings-lookup.yaml \
  --input member_id=10001 --secret username=teller1 --secret password=teller123 \
  --evidence-dir evidence/replay-member-lookup/recovered-popup \
  --out evidence/replay-member-lookup/recovered-popup/result.json
```

Outcome: `recovered`, 7/7 steps, correct outputs, with one recorded recovery
event (`dialog_dismissed`). The fake app's `popup` fault fires a native
`confirm()` dialog on the member detail page; replay detects it, dismisses
it, and continues to the same successful ending -- distinct from a clean
`success` so this run can be told apart from one that needed no help.

A fourth scenario -- `session_expired` -- is covered the same way (see
`DECISIONS.md` Part 6) but not re-saved here to keep this folder to the
three the deliverables ask for; it's exercised by
`tests/test_replay.py::test_replay_recovers_from_session_expiry` against the
real fake app on every test run.

## What each of these took to get right

Every one of the three scenarios above initially failed for a real reason,
not a flaky test -- see `DECISIONS.md` Part 6 for the full account:

- Output/checkpoint locators built from a discovery run had to move from
  matching *what the value was* to matching *where the value lives*
  (table position, not text) -- otherwise the "same artifact, different
  member" story above wouldn't hold; the locator would only ever work for
  the exact member it was recorded against.
- A business outcome recorded with the specific bad ID baked into its
  detection text ("...matching \"99999\".") only ever matched that one ID
  again -- fixed to use the stable phrase ("No member found") instead.
- Business outcomes have to be checked whenever a step fails to resolve,
  not only after every step in the recipe has already succeeded -- there's
  no "View" link to click on a not-found page, so that step fails by design,
  and needs to be told apart from a real bug.
- A dialog can appear as a side effect of a *successful* click (the click
  itself works; the page's `onload` handler fires the popup afterward), so
  it has to be checked for after success too, not only when handling a
  step's failure.
