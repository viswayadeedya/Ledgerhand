# Computer-Use Automation System

An AI figures out how to do a task on a legacy web app once ("discovery"),
we save what it learned as a typed, reviewable **capability artifact**, and
a deterministic **replay** engine runs that artifact forever after, live,
without an LLM in the loop. When automation genuinely can't or shouldn't
proceed alone, it escalates to a human who can take over the *same* live
browser session and hand control back.

Built against a self-hosted fake "legacy credit union" app
(`src/cua/fake_app/`) standing in for the real thing -- server-rendered,
table-based markup, iframes, no test IDs, and toggleable runtime faults
(session expiry, an unexpected dialog, ambiguous search results, etc.).

See [`REPORT.md`](REPORT.md) for the design write-up and
[`DECISIONS.md`](DECISIONS.md) for the full, chronological "chose X over Y
because Z" log this README and REPORT.md were distilled from.

## Setup

Requires **Python 3.11+**. Tested on Windows; nothing in the code is
platform-specific, but the commands below use PowerShell syntax.

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the project (editable) plus test dependencies
pip install -e ".[dev]"

# 3. Install Playwright's Chromium browser (one-time)
playwright install chromium

# 4. Copy the env template and fill in your Anthropic API key
copy .env.example .env
```

Open `.env` and set `ANTHROPIC_API_KEY` to a real key. Everything else in
`.env` already has working defaults. **The API key is only needed for
discovery** (Part 4, the one genuinely LLM-driven step) -- the fake app,
guardrails, replay, and human handoff never touch it and work with no key
at all, or even a deliberately invalid one (see `DECISIONS.md` Part 6 for
how that was verified).

## Running without live services

Everything except the discovery step is fully offline once the fake app is
running locally -- no external network calls, no API key:

```powershell
python -m pytest tests/ -v
```

120 tests, ~3 minutes (most of that is real Playwright browser launches;
the fake-app-only tests in `test_fake_app.py` run in well under a second).

## Demo path

**1. Start the fake app** (leave this running in its own terminal):
```powershell
python -m cua.fake_app
```
It listens on `http://127.0.0.1:5055`. Sign-on credentials: `teller1` /
`teller123` (fake, hardcoded, documented -- see `src/cua/fake_app/main.py`).
Member IDs `10001`-`10005` exist in the seed data
(`src/cua/fake_app/data.py`); anything else is a legitimate "not found."

**This file is the only place that password is written down.** Nothing
under `evidence/` or `artifacts/` contains it, and a test enforces that
with no exemption list. The reproduction commands in those folders take it
from an environment variable instead, so set it once per shell before
running them:
```powershell
$env:TELLER_PASSWORD = "teller123"
```

**2. Run discovery** (needs your API key; a browser drives itself
invisibly by default -- add `--headed` to watch it):
```powershell
python -m cua.agent `
  --goal "Look up member 10001 and read their current savings balance." `
  --target "http://127.0.0.1:5055/login" `
  --domain "127.0.0.1:5055" `
  --username teller1 --password teller123 `
  --max-steps 20 `
  --evidence-dir "evidence/runs/discovery-member-lookup"
```
Prints the outcome and outputs, and writes a full structured step log
(`run_log.json`) plus a screenshot per step to `--evidence-dir`.

**3. Turn that run into a reusable artifact** (no LLM, no API key):
```powershell
python -m cua.artifacts `
  --run-log "evidence/discovery-member-lookup/run_log.json" `
  --out "artifacts/member-savings-lookup.yaml" `
  --id "member-savings-lookup" --version 2 `
  --title "Look up a member's savings balance" `
  --description "Signs in to the teller system, searches for a member by ID, and reads their name and balances off the member detail page." `
  --input "member_id=10001" --sensitive-input member_id `
  --secret username --secret password `
  --secret-value "username=teller1" `
  --assert-equals "member_id={{inputs.member_id}}" `
  --output-type "savings_balance=money" --output-type "checking_balance=money" `
  --sensitive-output member_id --sensitive-output member_name `
  --sensitive-output savings_balance --sensitive-output checking_balance `
  --checkpoint-text "Savings Balance"
```
The `--input` literal is how the recorder *recognizes* a value in order to
parameterize it away; it is never stored. `--assert-equals` is what makes
replay prove it reached the right record, and `--sensitive-*` marks what
gets masked wherever it's written down. Business outcomes are attached
afterwards by `scripts/capture_business_outcome.py` and
`scripts/capture_ambiguous_duplicate_outcome.py`, each from a really
captured page state.

A committed, real example is already at
[`artifacts/member-savings-lookup.yaml`](artifacts/member-savings-lookup.yaml)
-- you don't have to run steps 2-3 yourself to try step 4.

**4. Replay the artifact** (no LLM, no API key -- this is the production
path an AI agent would trigger):
```powershell
python -m cua.replay `
  --artifact "artifacts/member-savings-lookup.yaml" `
  --input "member_id=10001" `
  --secret "username=teller1" --secret "password=teller123"
```
Try a different member (`member_id=10002`) to see it read different, real
data live -- not a cached answer. Try a nonexistent one (`member_id=99999`)
to see a clean `business_outcome` instead of a crash.

The CLI exits with a code a caller can branch on, so an orchestrator
doesn't have to parse stdout to know what happened:

| exit | outcome | what a caller should do |
|---|---|---|
| `0` | `success`, `recovered` | take the outputs |
| `1` | `hard_failure` | something is wrong -- alert, don't retry blindly |
| `2` | `business_outcome` | the app answered, and the answer was "no" -- retrying won't change it |
| `3` | `needs_human` | route to a person |
| `64` | -- | bad command line, or a missing `--input`/`--secret` (kept off `2` so it can't be read as a business outcome) |

Outputs the artifact marks `sensitive` print masked to their last two
characters, with a hint at the value's shape
(`"savings_balance": "***18 [shape: money]"`). The shape is what keeps a
masked record useful -- a date sitting in a money field stays visible
without the figure itself being written down. The value still reaches the
caller intact -- masking applies where things get *written down*, not to
what the capability returns. Add `--show-sensitive` to print them in full
on your own terminal; files written by `--out` and everything under
`--evidence-dir` stay masked regardless, since those outlive the run and
get read by people who weren't part of it.

**5. Trigger a recoverable runtime fault** and watch replay handle it:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5055/admin/faults/api" `
  -ContentType "application/json" -Body '{"fault":"popup","armed":true}'
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" `
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123"
```
Outcome: `recovered` -- an unexpected dialog fires, replay dismisses it
itself, and still returns the correct balance. Other faults:
`member_not_found`, `session_expired`, `slow_load`, `duplicate_members`,
`permission_denied` (a 403 "Access denied" page), `app_error` (a 500-status
error page), `wrong_member` (serves a *different* member's page with a 200
and no visible error -- caught by the artifact's identity assertion) and
`extra_row` (inserts a row above the balances -- see
[`evidence/extra_row/`](evidence/extra_row/)). All fire once and disarm
themselves; see `src/cua/fake_app/faults.py`.

`slow_load` also has a *setting*, since how slow it is decides whether
replay can ride it out. The default (2s) sits under replay's locator wait
budget, so an armed `slow_load` is recoverable; raise it past that budget
and the same fault becomes a hard failure:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5055/admin/settings/api" `
  -ContentType "application/json" -Body '{"name":"slow_load_seconds","value":12}'
```
Settings persist until `POST /admin/reset`, unlike faults, which fire once.

**6. Try human handoff**, live, at your own terminal:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5055/admin/faults/api" `
  -ContentType "application/json" -Body '{"fault":"duplicate_members","armed":true}'
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" `
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123" `
  --handoff terminal
```
Two more worth trying, because they end differently on purpose:
`permission_denied` comes back as a **business outcome** (exit 2) — the app
answered, the teller just isn't entitled to that record — while `app_error`
comes back as a **hard failure** (exit 1) with the HTTP status it saw and a
screenshot. Neither is recognised by reading the page; the status code
decides.

Replay hits a genuinely ambiguous result (two conflicting records), stops,
and asks you right there in the terminal what to do -- approve, say you
already fixed it manually, or abandon. Add `--headed` instead of
`--handoff terminal` and pass `--handoff interactive` to take over the
*actual browser window* via the Playwright Inspector.

**7. Regenerate all the evidence** (every scenario, from real runs):
```powershell
python scripts/run_evidence.py            # 14 scenarios -> one folder each
python scripts/run_evidence.py --list     # just the names
python scripts/run_stability.py           # 20 replays -> evidence/stability/
```
`run_evidence.py` starts the fake app itself (or reuses one already on
5055) and writes [`evidence/README.md`](evidence/README.md) from the runs
it just did -- the expected/actual columns there are read back off each
run's own `result.json`, never typed. `run_stability.py` replays against
every injected fault twice plus clean runs, and checks every value handed
back against the app's seed data; **wrong answers must be 0**.

Both exit non-zero if any run ends somewhere other than where it should,
so they work as checks, not just as generators.

## Project structure

```
src/cua/
  fake_app/    Part 1 -- the legacy-style target app (FastAPI)
  guardrails/  Part 2 -- allowlist policy, risk classification, redaction
  surface/     Part 3 -- Playwright driver + ranked locator resolution
  agent/       Part 4 -- the LLM-driven discovery loop
  core/        shared Action/Target/LocatorCandidate contract
  artifacts/   Part 5 -- capability schema + recorder
  replay/      Part 6 -- deterministic replay + error taxonomy
  handoff/     Part 7 -- human escalation and live-session takeover
tests/         191 tests across every layer above
scripts/       deterministic capture scripts, plus the evidence and stability runners
artifacts/     saved capability YAML files (the reusable deliverable)
evidence/      real run logs, screenshots, and results -- see evidence/README.md
```

Two capabilities ship in `artifacts/`:
[`member-savings-lookup.yaml`](artifacts/member-savings-lookup.yaml), built
from the genuine LLM discovery run, and
[`member-subaccount-open.yaml`](artifacts/member-subaccount-open.yaml),
which **opens an account** -- an irreversible action whose commit step is
blocked by policy until a human approves it. See
[`artifacts/README.md`](artifacts/README.md) for how each was produced and
why the second is a deterministic capture rather than a second LLM run.

## Everything else

- Design reasoning and trade-offs: [`REPORT.md`](REPORT.md)
- The full chronological decision log (every "chose X over Y because Z",
  including real bugs found and fixed along the way): [`DECISIONS.md`](DECISIONS.md)
- Real, reproducible evidence for every part: [`evidence/README.md`](evidence/README.md)
