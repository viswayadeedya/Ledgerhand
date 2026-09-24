# Capability artifacts

Each `.yaml` file here is a **capability**: a typed, versioned, reviewable
description of one reusable flow, produced by `cua.artifacts.build_artifact`
from a discovery run's log (`python -m cua.agent ... --evidence-dir ...`,
then `python -m cua.artifacts`). Part 6 (replay) executes these without an
LLM in the loop.

## Reading one

- `schema_version` -- which version of this contract the file speaks
  (`1.2`). An older artifact still loads, with a warning naming what it
  predates; a newer one is refused rather than partly applied, since the
  fields most likely to be new are *checks*, and silently dropping one
  means running without the safety it was written with.
- `version` -- the capability's own revision, independent of the schema's.
- `inputs` / `secrets` -- what the caller must supply per invocation.
  `inputs` are business parameters (e.g. a member ID); `secrets` are
  credentials, never given a literal value here. An input may also carry:
  - `pattern` -- a regex the caller's value must fully match, checked
    *before a browser opens*. A member ID of `abc` can't become a correct
    answer however well the rest of the run goes, so it's rejected for
    nothing rather than after a login and six steps — and reported as a
    validation error (`input_invalid`) rather than as whichever step
    happened to fall over first.
  - `example` -- a value safe to publish. Never the discovery run's own
    value; the recorder refuses that outright, since parameterizing it away
    was the whole point.
  - `sensitive: true` -- a result file records it masked.
- `outputs` -- what the caller gets back. Each has its own `target` (a
  locator), because replay always re-reads the live page at the checkpoint;
  it never just repeats whatever value discovery happened to see. Each may
  also carry:
  - `type` (`money` / `integer` / `string`) -- doubles as validation. A
    value that doesn't match is a hard failure and is never returned.
  - `must_equal` -- an assertion that this output matches something the
    caller supplied, e.g. `"{{inputs.member_id}}"`. This is what proves the
    run landed on the *right record*: every member's page says "Savings
    Balance", so the checkpoint alone can't tell them apart.
  - `sensitive` -- masked anywhere it's printed or persisted, with a shape
    hint (`***18 [shape: money]`). The caller still receives it intact.
- `steps` -- the ordered, replayable actions. Each `target.candidates` list
  is ranked most-to-least robust (`role` > `label` > `text` >
  `table_label` > `table_position` > `css`); replay tries them in order and
  requires exactly one match, never a guess among several. Each step also
  has a plain-English `description` and a `risk` label (`safe` / `risky` /
  `unverified`) with a `risk_note` saying where that judgement came from --
  review metadata, since replay re-checks every action live regardless.
  Any literal that matched a declared input or secret during recording has
  been replaced with a `{{inputs.x}}` / `{{secrets.x}}` placeholder, and
  URLs are canonicalized (`/app/member/:member_id`) -- no value from the
  discovery run survives anywhere in the file.
- `checkpoint` -- the condition that proves the flow actually reached the
  expected state, asserted by a human when the artifact was built (not
  inferred). Same `Target` shape as everything else.
- `business_outcomes` -- named, legitimate non-success endings (Part 6),
  each with its own `detect` locator. `member-savings-lookup.yaml` has
  three: `member_not_found` and `permission_denied` (plain answers the
  caller needs), and `ambiguous_duplicate`, marked `requires_human: true`
  -- replay (with a handoff handler configured, Part 7) escalates that one
  to a person instead of just reporting it.

  Note what is *not* listed here: an app error. A 5xx is detected from the
  response status rather than declared per capability, so every artifact
  gets it for free and none has to describe its app's error pages. The
  split is deliberate — a denial is the app answering, a 5xx is the app
  failing to answer.
- `provenance` -- when/which model discovered this, and a pointer back to
  the run log it came from, for audit.

## How this one was built

This file is never hand-edited. Every change to it goes through the
recorder or a capture script and bumps `version`, so what's committed is
always something a tool produced from a real run.

```
# 1. The happy-path steps, outputs, and checkpoint, from a real discovery run
python -m cua.artifacts \
  --run-log "evidence/discovery-member-lookup/run_log.json" \
  --out "artifacts/member-savings-lookup.yaml" \
  --id "member-savings-lookup" --version 3 \
  --title "Look up a member's savings balance" \
  --description "Signs in to the teller system, searches for a member by ID, and reads their name and balances off the member detail page." \
  --input "member_id=10001" \
  --input-desc "member_id=The member ID to look up. Echoed back on the detail page, which is what makes the identity assertion possible." \
  --input-pattern "member_id=^[0-9]{5}$" \
  --input-example "member_id=00000" \
  --sensitive-input member_id \
  --secret username --secret password \
  --secret-value "username=teller1" \
  --output-desc "member_id=The member ID shown on the record. Asserted equal to the requested one." \
  --output-desc "member_name=The member's full name as shown on the record." \
  --output-desc "savings_balance=Current savings balance." \
  --output-desc "checking_balance=Current checking balance." \
  --assert-equals "member_id={{inputs.member_id}}" \
  --output-type "savings_balance=money" \
  --output-type "checking_balance=money" \
  --sensitive-output member_id --sensitive-output member_name \
  --sensitive-output savings_balance --sensitive-output checking_balance \
  --checkpoint-text "Savings Balance"

# 2. Business outcomes, added afterward from real captured page states
#    (deterministic exploration, not LLM discovery -- see each script's docstring)
python scripts/capture_business_outcome.py
python scripts/capture_ambiguous_duplicate_outcome.py
python scripts/capture_permission_denied_outcome.py
```

The capture scripts deliberately don't bump `version` themselves — three
of them attaching three outcomes would carry one revision to 3, 4 and 5
and make the number depend on how many outcomes an artifact happens to
have. One regeneration pass is one revision: the build command in step 1
carries it, and the scripts preserve it.

Step 1 needs no API key and no browser -- it reads the run log off disk.
That separation is deliberate: re-deriving an artifact should never need
the model again. Step 2 does drive a real browser, against a running fake
app.

`--output` values default to whatever the discovery run's own
`report_success` call returned (`run_log.json`'s `outputs`), so you only need
`--output name=value` if you want to point at something different than what
discovery reported. `--output-desc` adds the human-facing description on top.
The exact output key names (e.g. `member_name` vs `name`) come from
whatever the model called them that run -- not perfectly deterministic
across runs, so check your own `run_log.json`'s `outputs` before copying
this command verbatim.
