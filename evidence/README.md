# Evidence

Proof-of-work for the end-to-end flow: **a goal → a genuine LLM-driven
discovery run → a saved capability artifact → deterministic replay →
human escalation and handoff**. Every folder is a real run against the
live fake app (`src/cua/fake_app/`); nothing is hand-authored.

One folder per scenario, produced by `scripts/run_evidence.py`. Each
contains `command.txt` (how to reproduce it), `result.json` (the
`ReplayResult`, masked), `run.log`, and `failure.png` when the run failed.
Per-step screenshots go to the git-ignored `evidence/runs/` instead —
committing eight images per scenario would be a hundred PNGs nobody opens.

```
python scripts/run_evidence.py              # all scenarios
python scripts/run_evidence.py --only app_error
python scripts/run_evidence.py --list
```

The scenario table — scenario, brief section, expected, actual, exit code,
folder — is generated from `index.json` in the next step of this pass. Until
then, `index.json` is the machine-readable record of what each run actually
produced.

## Not produced by that script

| Folder | What it shows |
| --- | --- |
| [`discovery-member-lookup/`](discovery-member-lookup/) | The **required** genuine LLM-driven run: `claude-sonnet-5`, via `browser_toolset_20260801`, really drives the browser through login → search → read balance. Full step log plus screenshots. Not a replay scenario, so it isn't re-run by `run_evidence.py`. |
| [`extra_row/`](extra_row/) | Three replays of the **same page** with one extra row inserted, differing only in the artifact: position-only locators return `success` with a **date in the savings balance**; label-anchored locators return the right figure; a declared `money` type catches the same shifted value as a second layer. Frozen — the comparison *is* the evidence. |

The capability artifacts live in [`/artifacts/`](../artifacts/) — every
replay below executes one of those exact files.

## What's *not* here: `runs/`

`evidence/runs/` is git-ignored scratch. Every scenario points its
`--evidence-dir` there, so per-step screenshots and intermediate captures
land somewhere that isn't committed.
