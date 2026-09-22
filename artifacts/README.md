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
- `provenance` -- when/which model discovered this, and a pointer back to
  the run log it came from, for audit.

## How this one was built

```
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
  --output-desc "savings_balance=Current savings balance." \
  --output-desc "checking_balance=Current checking balance." \
  --output-desc "name=Member's full name." \
  --checkpoint-text "Savings Balance"
```

`--output` values default to whatever the discovery run's own
`report_success` call returned (`run_log.json`'s `outputs`), so you only need
`--output name=value` if you want to point at something different than what
discovery reported. `--output-desc` adds the human-facing description on top.
