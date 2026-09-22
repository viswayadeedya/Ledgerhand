# Capability artifacts

Each `.yaml` file here is a **capability**: a typed, versioned, reviewable
description of one reusable flow, produced by `cua.artifacts.build_artifact`
from a discovery run's log (`python -m cua.agent ... --evidence-dir ...`,
then `python -m cua.artifacts`). Part 6 (replay) executes these without an
LLM in the loop.

## Reading one

- `inputs` / `secrets` -- what the caller must supply per invocation.
  `inputs` are business parameters (e.g. a member ID); `secrets` are
  credentials, never given a literal value here.
- `outputs` -- what the caller gets back. Each has its own `target` (a
  locator), because replay always re-reads the live page at the checkpoint;
  it never just repeats whatever value discovery happened to see.
- `steps` -- the ordered, replayable actions. Each `target.candidates` list
  is ranked most-to-least robust (`role` > `label` > `text` >
  `table_position` > `css`); replay tries them in order and requires exactly
  one match, never a guess among several. Any literal value that matched a
  declared input or secret during recording has been replaced with a
  `{{inputs.x}}` / `{{secrets.x}}` placeholder -- the real values never made
  it in.
- `checkpoint` -- the condition that proves the flow actually reached the
  expected state, asserted by a human when the artifact was built (not
  inferred). Same `Target` shape as everything else.
- `business_outcomes` -- named, legitimate non-success endings (Part 6),
  each with its own `detect` locator. `member-savings-lookup.yaml` has two:
  `member_not_found` (a plain answer) and `ambiguous_duplicate`, marked
  `requires_human: true` -- replay (with a handoff handler configured,
  Part 7) escalates that one to a person instead of just reporting it.
- `provenance` -- when/which model discovered this, and a pointer back to
  the run log it came from, for audit.

## How this one was built

```
# 1. The happy-path steps, outputs, and checkpoint, from a real discovery run
python -m cua.artifacts \
  --run-log "evidence/discovery-member-lookup/run_log.json" \
  --out "artifacts/member-savings-lookup.yaml" \
  --id "member-savings-lookup" \
  --title "Look up a member's savings balance" \
  --description "Signs in to the teller system, searches for a member by ID, and reads their current savings and checking balance." \
  --input "member_id=10001" \
  --input-desc "member_id=The member ID to look up." \
  --secret username --secret password \
  --secret-value "username=teller1" \
  --output-desc "member_name=Member's full name." \
  --output-desc "savings_balance=Current savings balance." \
  --output-desc "checking_balance=Current checking balance." \
  --checkpoint-text "Savings Balance"

# 2. Two business outcomes, added afterward from real captured page states
#    (deterministic exploration, not LLM discovery -- see each script's docstring)
python scripts/capture_business_outcome.py
python scripts/capture_ambiguous_duplicate_outcome.py
```

`--output` values default to whatever the discovery run's own
`report_success` call returned (`run_log.json`'s `outputs`), so you only need
`--output name=value` if you want to point at something different than what
discovery reported. `--output-desc` adds the human-facing description on top.
The exact output key names (e.g. `member_name` vs `name`) come from
whatever the model called them that run -- not perfectly deterministic
across runs, so check your own `run_log.json`'s `outputs` before copying
this command verbatim.
