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

69 tests, ~2 minutes (most of that is real Playwright browser launches; the
fake-app-only tests in `test_fake_app.py` run in well under a second).

## Demo path

**1. Start the fake app** (leave this running in its own terminal):
```powershell
python -m cua.fake_app
```
It listens on `http://127.0.0.1:5055`. Sign-on credentials: `teller1` /
`teller123` (fake, hardcoded, documented -- see `src/cua/fake_app/main.py`).
Member IDs `10001`-`10005` exist in the seed data
(`src/cua/fake_app/data.py`); anything else is a legitimate "not found."

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
  --run-log "evidence/runs/discovery-member-lookup/run_log.json" `
  --out "artifacts/member-savings-lookup.yaml" `
  --id "member-savings-lookup" `
  --title "Look up a member's savings balance" `
  --description "Signs in, searches for a member by ID, and reads their balances." `
  --input "member_id=10001" `
  --secret username --secret password `
  --secret-value "username=teller1" `
  --checkpoint-text "Savings Balance"
```
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

**5. Trigger a recoverable runtime fault** and watch replay handle it:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5055/admin/faults/api" `
  -ContentType "application/json" -Body '{"fault":"popup","armed":true}'
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" `
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123"
```
Outcome: `recovered` -- an unexpected dialog fires, replay dismisses it
itself, and still returns the correct balance. Other faults:
`member_not_found`, `session_expired`, `slow_load`, `duplicate_members`
(see `src/cua/fake_app/faults.py`).

**6. Try human handoff**, live, at your own terminal:
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5055/admin/faults/api" `
  -ContentType "application/json" -Body '{"fault":"duplicate_members","armed":true}'
python -m cua.replay --artifact "artifacts/member-savings-lookup.yaml" `
  --input "member_id=10001" --secret "username=teller1" --secret "password=teller123" `
  --handoff terminal
```
Replay hits a genuinely ambiguous result (two conflicting records), stops,
and asks you right there in the terminal what to do -- approve, say you
already fixed it manually, or abandon. Add `--headed` instead of
`--handoff terminal` and pass `--handoff interactive` to take over the
*actual browser window* via the Playwright Inspector.

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
tests/         69 tests across every layer above
scripts/       one-off deterministic exploration scripts (business-outcome capture, handoff demo)
artifacts/     saved capability YAML files (the reusable deliverable)
evidence/      real run logs, screenshots, and results -- see evidence/README.md
```

## Everything else

- Design reasoning and trade-offs: [`REPORT.md`](REPORT.md)
- The full chronological decision log (every "chose X over Y because Z",
  including real bugs found and fixed along the way): [`DECISIONS.md`](DECISIONS.md)
- Real, reproducible evidence for every part: [`evidence/README.md`](evidence/README.md)
