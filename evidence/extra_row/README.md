# `extra_row`: why output locators are anchored to labels

Three real replay runs against **the same page, the same member, the same
fault**. The only difference is the artifact. Everything here is captured
output, not hand-written.

The `extra_row` fault inserts one row (`Account Opened | 2019-03-14`) above
the balances on the member detail page — standing in for the thing that
actually happens to a legacy app in production: a tenant on a slightly
different build, or a field added last quarter. Nothing is broken; the page
is simply one row taller than it was on the day the capability was
discovered.

## Results

| artifact | outcome | `savings_balance` | `checking_balance` | exit |
|---|---|---|---|---|
| [`position-only`](position-only.txt) | success | **`2019-03-14`** | **`$2340.18`** | 0 |
| [`label-anchored`](label-anchored.txt) | success | `$2340.18` | `$512.44` | 0 |
| [`position-only-money-typed`](position-only-money-typed.txt) | hard_failure | *(not returned)* | *(not returned)* | 1 |

Row 1 is the failure worth caring about. There is no error, no warning and
no crash — the locators resolved, returned strings, and the run reported
`success` with exit code 0. But every value below the inserted row has slid
down one: a **date** is being reported as a savings balance, and the
savings balance is being reported as the *checking* balance. Handed to
whoever asked, both look entirely plausible. In this domain that is worse
than a crash, because nothing downstream has any reason to question it.

Row 2 is the fix. `label=Savings Balance,col=1` finds the row whose label
cell reads exactly that, then reads the cell beside it. The row moved; what
it says did not.

Row 3 is the net underneath, for the case where the label itself changes
and the positional fallback is all that is left: `type: money` on the
balances turns the same silent wrong answer into a refusal that names the
output and shows what it actually read.

## Files

| file | what it is |
|---|---|
| `*.txt` | the exact commands and their verbatim terminal output |
| `*.result.json` | the machine-readable `ReplayResult` from the same run (`--out`) |
| `*.yaml` | the artifact each run used, frozen |

The artifacts are **frozen copies**, not references to
`artifacts/member-savings-lookup.yaml`. That file is being regenerated as
part of this hardening work; freezing them keeps this comparison
reproducible afterwards instead of quietly turning into three identical
runs.

- `position-only.yaml` — the committed artifact as it stood before Phase 2.
- `position-only-money-typed.yaml` — the same file with `type: money` added
  to the two balance outputs, and nothing else changed.
- `label-anchored.yaml` — rebuilt by the recorder from the **same**
  `evidence/discovery-member-lookup/run_log.json`, with no new discovery run
  and no LLM call. The label text (`Savings Balance`) is read out of the
  captured observation — the cell to the left of the value, in the same row
  — so it comes from what the app actually rendered, not from a human
  guessing at labels afterwards.

## Reproducing

Start the app (`python -m cua.fake_app`), then run the commands in any
`.txt` file. The fault is fire-once and disarms itself, so re-arm it before
each run — that is why the `curl` appears in all three.

## A note on redaction

These runs deliberately use artifacts that do **not** mark the balances
`sensitive`, because the comparison's entire content is the values
themselves: masked to `***14` and `***18`, the point (a date landed in a
money field) would be invisible. The shipped capability does mark them
sensitive, and its own run evidence under `evidence/runs/` is masked
accordingly. This folder is a locator-behaviour demonstration against fake
data, not a record of a real capability run.
