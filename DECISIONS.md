# Decisions log

Running record of "chose X over Y because Z" calls made while building this system.
Ordered chronologically within each part. See /REPORT.md for the synthesized write-up.

## Part 0 — Skeleton

- **Language: Python (3.11+)**, over TypeScript/Node. Both the Anthropic SDK and
  Playwright have first-class Python support, and Python's dataclass/Pydantic
  tooling is a fast way to get a strict, typed artifact schema — which the brief
  calls out as a focal point of evaluation.
- **Browser automation: Playwright**, over Selenium/Puppeteer. Playwright's
  locator API (role, label, text, `has-text`, frame-aware chaining) and built-in
  auto-waiting map directly onto the "legacy, no clean DOM, framesets, nested
  tables" surface this project targets. Selenium's explicit-wait boilerplate and
  weaker frame ergonomics would cost time without buying anything back.
- **Discovery model: Claude (Anthropic), via the computer-use tool family**,
  chosen for direct API access and because the model is read from `.env`
  (`DISCOVERY_MODEL`) rather than hardcoded, so it can be swapped without code
  changes. Default `claude-sonnet-5`; retry with `claude-opus-5`
  (`DISCOVERY_FALLBACK_MODEL`) if the discovery run stalls.
- **Target application: a self-built fake "legacy credit union" web app**
  (Flask, server-rendered, frameset layout, table-based markup, no test IDs),
  over a public sandbox site. A public site can't be made to fail on demand.
  This project's evaluation criteria center on runtime-error handling
  (validation errors, not-found, session expiry, popups, slow loads) — that
  requires an app whose faults we can toggle deterministically. Fake data only,
  no real PII.
- **Fake app framework: FastAPI** (with `Jinja2Templates` for server-rendered
  HTML and Starlette's `SessionMiddleware` for cookie-based session state),
  over Flask. Flask was the initial pick on framework-fit grounds (marginally
  less wiring for plain server-rendered HTML), but this project's ground rules
  require being able to defend every part of the submission in detail — since
  FastAPI is the framework actually familiar, it's the better choice even
  though the fit is slightly less snug out of the box. The extra wiring
  (`Jinja2Templates`, `SessionMiddleware`, `python-multipart` for form posts)
  is a one-time setup cost, not an ongoing one, and doesn't change anything
  about the "legacy server-rendered app" profile the fake app needs to
  present — frames, tables, no test IDs, toggleable faults.
- **Architecture: single process, synchronous, no queues.** The brief explicitly
  discourages building scaling infrastructure (Section 9). Discovery, replay,
  and the fake app run as separate local processes/scripts invoked directly
  (no message broker, no worker pool) — sufficient to demonstrate the
  abstractions without premature infra.
- **Repo/package layout: `src/cua/` with one subpackage per part**
  (`fake_app`, `guardrails`, `surface`, `agent`, `artifacts`, `replay`,
  `handoff`), installed editable via `pyproject.toml`. Keeps the seam between
  "perceiving/acting on a surface" (`surface/`), "recording what happened"
  (`artifacts/`), and "replaying it later" (`replay/`) visible in the directory
  structure itself, which matters for the heterogeneity/multi-tenant story in
  the write-up (Section 3.7).
- **Secrets:** `.env` (gitignored) holds `ANTHROPIC_API_KEY`, `DISCOVERY_MODEL`,
  `DISCOVERY_FALLBACK_MODEL`. `.env.example` is committed as a template.

## Part 1 — Fake app

_(not yet built)_

## Part 2 — Policy / guardrails + redaction

_(not yet built)_

## Part 3 — Surface interface

_(not yet built)_

## Part 4 — Agent loop

_(not yet built)_

## Part 5 — Recorder → artifact

_(not yet built)_

## Part 6 — Replay + error taxonomy

_(not yet built)_

## Part 7 — Human handoff

_(not yet built)_

## Part 8 — Evidence + tests

_(not yet built)_

## Part 9 — README + REPORT

_(not yet built)_
