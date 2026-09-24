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
| [`position-only`](position-only.txt) | success | **`***14 [shape: date]`** | **`***18 [shape: money]`** | 0 |
| [`label-anchored`](label-anchored.txt) | success | `***18 [shape: money]` | `***44 [shape: money]` | 0 |
| [`position-only-money-typed`](position-only-money-typed.txt) | hard_failure | *(not returned)* | *(not returned)* | 1 |

The balances are masked here, as they are everywhere this project writes a
value down. The shape hint is what keeps that from destroying the point:
`***14` and `***18` are equally unreadable, but `[shape: date]` next to
`[shape: money]` shows a date sitting in a money field without either
figure landing on disk. The `checking_balance` shapes match in rows 1 and
2 because the wrong value there *is* money — just the wrong member's line,
which is exactly why shape alone isn't a sufficient check and the label
anchor is the real fix.

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

All three artifacts mark `member_id`, `member_name` and both balances
`sensitive`, and every value here is masked -- no exceptions, and no
`--show-sensitive` anywhere in the commands above.

`member_id` was briefly left unmasked, on the grounds that it is only the
caller's own input echoed back. That was inconsistent: the identity-check
failure already masks the same ID to `***01`, so printing it in full two
lines later in the outputs made the rule look arbitrary. A value is either
sensitive or it is not, and which message it appears in doesn't change
that. It is now masked as an **input** too, so the `inputs` block of each
`.result.json` reads `***01 [shape: integer]` rather than filing the
identical value two lines above the masked output.

The one place the full ID still appears is the `--input member_id=10001`
in each command above. That is the recipe for reproducing the run, not a
record of data -- masking it would leave evidence nobody can re-run.

The password is *not* treated the same way, and the difference is
deliberate. A member ID is the caller's own parameter; a credential is a
credential even when it is fake, and the moment a scan has to carry a list
of files where the password is allowed, the scan stops being a check and
becomes a record of exceptions. So the commands above take it from
`$env:TELLER_PASSWORD`, whose value is in the top-level README, and the
literal appears in no file under `evidence/` or `artifacts/` at all --
which a test enforces with no exemption list.

Only the command lines changed here. The captured results below are
byte-frozen: the comparison *is* the evidence, and rerunning it would
quietly turn three different artifacts into three copies of whatever the
recorder does today.

An earlier version of this folder was captured *unmasked*, on the argument
that masking would destroy the comparison. That was wrong once the shape
hint existed -- it publishes the one property that matters (what kind of
value this is) while withholding the value. The earlier reasoning is kept
in DECISIONS.md rather than deleted, since being talked out of it is part
of the record.
