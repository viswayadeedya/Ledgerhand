# Evidence

This is the proof-of-work for the end-to-end flow the brief asks for:
**a goal → a genuine LLM-driven discovery run → a saved capability
artifact → deterministic replay (success, an error/business outcome, and
recovery) → human escalation and handoff**. Every folder here is a real
run against the live fake app (`src/cua/fake_app/`) -- nothing is
hand-authored or simulated. Each has its own `README.md` with the exact
command(s) used and what to look for.

| Folder | Part | What it shows |
| --- | --- | --- |
| [`discovery-member-lookup/`](discovery-member-lookup/) | 4 | The **required** genuine LLM-driven run: `claude-sonnet-5`, via the `browser_toolset_20260801`, actually drives the browser through login → search → read balance. Full structured step log (`run_log.json`) plus screenshots. |
| [`replay-member-lookup/`](replay-member-lookup/) | 6 | Deterministic replay of the saved artifact, no LLM: a clean **success**, a **business outcome** (member not found), and a **recovered** run (an injected popup dismissed automatically). |
| [`handoff-ambiguous-duplicate/`](handoff-ambiguous-duplicate/) | 7 | Human escalation on a genuinely ambiguous result (two conflicting records): one run where the operator **abandons**, one where the operator **takes over the same live session** and resolves it, after which replay reads the correct member's real data. |
| [`extra_row/`](extra_row/) | hardening | Three replays of the **same page** with one extra row inserted, differing only in the artifact: position-only locators return `outcome: success` with a **date in the savings balance**; label-anchored locators return the right figure; a declared `money` type catches the same shifted value as a second layer. |

The capability artifact itself lives at
[`/artifacts/member-savings-lookup.yaml`](../artifacts/member-savings-lookup.yaml)
(see `/artifacts/README.md`) -- every replay/handoff run above executes
that exact file.

## How these were produced (so they're reproducible, not just claimed)

```
# Discovery (Part 4) -- needs a real ANTHROPIC_API_KEY in .env
python -m cua.agent --goal "Look up member 10001 and read their current savings balance." \
  --target "http://127.0.0.1:5055/login" --domain "127.0.0.1:5055" \
  --username teller1 --password teller123 --max-steps 20 \
  --evidence-dir "evidence/discovery-member-lookup"

# Recorder -> artifact (Part 5)
python -m cua.artifacts --run-log "evidence/discovery-member-lookup/run_log.json" \
  --out "artifacts/member-savings-lookup.yaml" --id "member-savings-lookup" \
  --title "Look up a member's savings balance" \
  --description "..." --input "member_id=10001" --secret username --secret password \
  --secret-value "username=teller1" --checkpoint-text "Savings Balance"

# Replay (Part 6) -- no LLM, no API key needed
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" \
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123"

# Human handoff (Part 7) -- interactive at your own terminal
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" \
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123" \
  --handoff terminal
```

The `handoff-ambiguous-duplicate/` evidence specifically was produced by
`scripts/demo_handoff.py`, which uses `MockOperatorHandoff` instead of the
interactive terminal prompt above, since it runs unattended to produce
reproducible evidence -- see that folder's own README for why that's still
a real demonstration of the control-transfer mechanism, not a stand-in for
it.

## What's *not* here: `runs/`

`evidence/runs/` is git-ignored scratch space -- every CLI command above
defaults to writing its screenshots and logs there unless you pass
`--evidence-dir` explicitly. The curated folders above are hand-picked,
verified-clean copies of specific runs, promoted out of that scratch space
on purpose (see each command's `--evidence-dir` above).

## Redaction, verified

Every JSON file under this directory has been checked -- by both automated
test (`tests/test_browser_tools.py`, `tests/test_surface.py`) and a manual
`git grep` sweep before each commit -- to contain no real secret value
(the fake teller password, in particular) anywhere, including inside
nested `Observation`/`ActionResult` data, not just the top-level fields.
See `DECISIONS.md` Part 4 for the three separate redaction bugs that
history uncovered before this held.
