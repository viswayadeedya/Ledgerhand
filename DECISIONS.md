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

- **Multi-frame layout via `<iframe>`, not a real `<frameset>`.** The brief
  groups "iframes/framesets" together as equally valid hostile-surface
  options (Section 4), and modern Chromium's support for the obsolete
  `<frameset>`/`<frame>` elements is unreliable enough to be a real risk for
  a component the whole system depends on. Named iframes (`banner`, `nav`,
  `main`) driven by `target="..."` on forms/links reproduce the same
  automation problem — content lives behind a named frame boundary the
  agent/replay engine must target explicitly — without betting the fake
  app's rendering on deprecated markup.
- **No test IDs, deliberately inconsistent locator surface.** Some controls
  (the sub-account form) get proper `<label for>`/`id` pairs; others (the
  member-lookup box, the admin panel) have only preceding table-cell text as
  their "label," or no label at all. This isn't sloppiness — it's there on
  purpose so Part 3's locator-ranking strategy (role+name > label > text >
  table position > CSS) has real cases to fall back through, matching the
  brief's description of legacy apps as inconsistent even within one app.
- **Faults are server-side, global, and fire-once (auto-disarm).** An admin
  endpoint (`/admin/faults`, HTML + JSON) arms a fault; the next relevant
  request consumes it and it turns itself off. This means the exact same
  artifact + input params can be replayed twice back-to-back — once clean,
  once against the injected condition — which is the comparison the
  evaluation/evidence needs to show error handling working, without needing
  per-request fault parameters that would leak test-harness concerns into
  the artifact schema itself.
- **`member_not_found` is a toggle, not just a missing ID.** A genuinely
  unassigned ID (e.g. 99999) already produces a natural not-found result
  with no fault needed. The toggle exists so a *valid* member ID can also
  return "not found" on demand — simulating a record archived/deleted a
  moment earlier — so the same input parameters can demonstrate both the
  success path and the business-outcome path.
- **Two flavors of "multiple results," only one is a fault.** Searching by
  last name can naturally return more than one record (Maria and Carlos
  Garcia are both seeded, unconditionally) — real name collisions need no
  fault flag. `duplicate_members` instead forces an ID search (normally
  unique) to return two conflicting records, simulating a data-quality
  anomaly. Both hit the same disambiguation-table UI, but only the second is
  the kind of ambiguity that should make the system stop and escalate to a
  human rather than guess (Part 7).
- **`popup` fires a native `confirm()` dialog**, not an in-page modal div.
  Playwright has an explicit, well-known API for browser-native dialogs
  (`page.on("dialog", ...)`), and this is a closer match to how legacy apps
  actually surprise automation (a blocking `window.confirm`/`alert` an
  operator didn't expect) than a styled overlay would be.
- **Closed-account business outcome (member 10005) added as a free bonus.**
  Reuses existing data/status fields with no new fault-toggle plumbing, and
  gives the error taxonomy (Part 6) one more "expected business outcome"
  case beyond the five named faults.
- **Sub-account commit endpoint is implemented for real**, even though no
  automation in this project is ever meant to call it. Building it honestly
  (it appends a sub-account and renders a success page) means the guardrail
  in Part 2/6 that blocks it is stopping a *real* irreversible action, not a
  stub that begs the question.
- **Environment needed installing:** this machine had no working Python
  (only the Windows Store alias stub). Installed Python 3.12 via
  `winget install Python.Python.3.12` with the user's confirmation, then
  created a project-local `.venv`.
- **Starlette's `Jinja2Templates.TemplateResponse` signature**: the version
  resolved by `pip install -e .` (Starlette 1.6.0) requires
  `TemplateResponse(request, name, context)`, not the older
  `TemplateResponse(name, {"request": request, ...})` form. All templates
  wire the `request` object as the first positional argument accordingly.

## Part 2 — Policy / guardrails + redaction

- **Guardrails are a standalone package the agent/replay layers call into,
  not something baked into the prompt.** `PolicyEngine.evaluate(action)`
  only ever sees a typed `Action` (type + url + method), never the model's
  reasoning or transcript — a manipulated or confused LLM has no path to
  argue its way past the check, because the check doesn't read anything the
  model wrote.
- **Allowlist as a YAML file, not code.** `guardrails/policy.yaml` lists
  `allowed_domains`, `allowed_routes` (globs), `allowed_action_types`, and
  `risky_routes`. Added `pyyaml` as a new dependency specifically so this
  file can carry comments explaining *why* a route is allowed or risky —
  the brief calls out that both a human reviewer and a calling agent need
  to understand the policy, and a commented YAML file reviews better than a
  Python dict literal. Path is overridable via `GUARDRAILS_POLICY_PATH`,
  foreshadowing the multi-tenant story (Section 3.7): each tenant could ship
  its own policy file without touching code.
- **Risky/irreversible actions are blocked by default, not merely flagged.**
  `risky_action_default: block` in the policy file means a route matching
  `risky_routes` (currently just `*/new-subaccount/commit`) is refused
  unless the caller explicitly passes `human_approved=True`. This is the
  conservative end of the spectrum the brief offers ("block, require
  confirmation, or flag — your call") — for money-moving actions in a
  banking context, failing closed is the safer default, and the escalation
  path (Part 7) is exactly how a human supplies that approval.
  Full reasoning in `/REPORT.md` Section 6.
- **Redaction is key-name-based first, pattern-based second.** `redact_value`
  recursively masks any dict key matching a sensitive-name list (password,
  session, token, ssn, etc.) regardless of content; `redact_text` also
  regex-masks SSN- and card-shaped strings that might appear in free text
  (e.g. OCR'd screenshot text or a log message). This is explicitly a
  best-effort net, not a guarantee — documented as a stated limitation
  rather than oversold.
- **Action vocabulary defined here, before the surface layer exists.**
  `guardrails/models.py` defines `ActionType` (navigate/click/fill/select/
  read/submit/wait/dismiss_dialog) and the `Action` model as the contract
  Part 3 (surface) and Part 4 (agent loop) will produce. Guardrails needed
  *some* stable shape to check against, and keeping it deliberately thin
  (no coordinates, no selector detail) means the surface layer is free to
  choose its own targeting strategy without the guardrail layer caring.

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
