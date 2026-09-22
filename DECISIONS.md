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

- **Shared contracts moved to a new `cua.core` package.** `Action` (and
  friends) started in `guardrails.models` (Part 2), but Surface needs it
  too, and neither package should depend on the other just to share a
  vocabulary. `cua/core/models.py` now owns `Action`, `Target`,
  `LocatorCandidate`, `Point`, `Observation`, `ActionResult`, etc.;
  `guardrails/models.py` re-exports from it so the Part 2 tests didn't need
  to change. Chose to do this refactor now, while the blast radius is still
  two files, rather than let guardrails keep growing into a de facto
  "everything" module.
- **Two ways to target an element, one underlying resolver.** An `Action`
  can carry either a `target` (ranked `LocatorCandidate`s: role → label →
  text → table position → CSS, strict "exactly one match or fail") or a
  `point` (pixel coordinates). This is deliberate: discovery (Part 4) will
  drive the page the way Anthropic's computer-use tool works, via
  screenshots and coordinates, but coordinates never get recorded into an
  artifact. `surface/perceive.py` hit-tests a point back to a DOM element
  and derives the same ranked candidates a semantic target would carry, so
  by the time an action is done, the system has a robust locator either way
  -- coordinates are how the model acts, not what gets remembered.
- **Resolve → predict destination → check policy → act, in that order,
  always.** For CLICK/SUBMIT, the surface resolves the target element
  *first*, reads its `href` or enclosing `<form>`'s `action`/`method`
  *without clicking*, builds the real destination URL, and only then calls
  `PolicyEngine.evaluate()`. A blocked click never reaches the browser --
  proven by `test_risky_commit_is_blocked_then_allowed_with_human_approval`,
  which asserts the app never left the confirmation screen until
  `human_approved=True` was passed. This is what makes Part 2's guardrails
  a real enforcement mechanism instead of a policy nobody consults.
- **TABLE_POSITION resolves through to the interactive control inside the
  cell, not the cell itself.** First cut returned the `<td>`/`<th>` locator
  directly, which is rarely what you want to click or fill. If a cell has
  exactly one interactive descendant (`input`/`select`/`textarea`/`button`/
  `a`), that's what gets targeted instead -- caught by
  `test_table_position_strategy_resolves_cell_input` failing on the first
  run.
- **Two real bugs found only by actually running Playwright against a page
  with a blocking `confirm()` dialog** (both would have shipped silently
  without the integration test in `test_surface.py`):
  1. `page.goto()` defaults to waiting for the `load` event; a page whose
     `window.onload` handler opens a `confirm()` never finishes that event,
     so `goto()` hung forever. Fixed by navigating with
     `wait_until="domcontentloaded"` instead.
  2. Even after that fix, `observe()` still hung: a pending native dialog
     blocks the page's entire script/render pipeline, so both
     `evaluate()` (element scanning) *and* `screenshot()` (it waits on font
     loading, which needs the pipeline) stall until the dialog is resolved.
     Fixed by skipping both while `self._pending_dialog` is set -- `observe()`
     still reports the dialog's message and current URL, just not a fresh
     scan/screenshot until it's dismissed. Also added a short (150ms)
     settle pause after navigate/click so the "dialog opened" event has
     time to reach our handler before we decide whether it's safe to
     evaluate -- there's a small residual race here in theory (a
     pathologically slow event-dispatch could still lose it), acceptable
     for this project's stable-enterprise-UI target but worth flagging as a
     known limitation rather than a guarantee.
- **Locator resolution unit-tested independent of the fake app**
  (`tests/test_locator.py`, using `page.set_content()` on inline HTML) so
  ranking logic is verified in isolation from server/browser-orchestration
  flakiness; the fuller integration suite (`tests/test_surface.py`) spins up
  a real fake-app instance on a free port per test module and drives it
  end-to-end, including login, frame-crossing search, the guardrail-blocked
  commit, and the popup dialog.

## Part 4 — Agent loop

- **Toolset: `browser_toolset_20260801`, not the generic OS-level
  `computer_toolset_20260801`.** Checked both against the current Anthropic
  docs before writing any code (per the earlier note to verify tool
  version/beta header rather than guess). The browser toolset gives the
  model an accessibility-tree-style `read_page`/`find` (ref-based element
  references) *alongside* coordinate fallback, is explicitly documented as
  "designed to complement Playwright... wrapping an existing
  Playwright-controlled page," and runs with no beta header on
  `claude-sonnet-5` (our default `DISCOVERY_MODEL`) and `claude-opus-5` (our
  fallback). This is a much closer match to the brief's "bias toward an
  approach that would still work when the surface has no clean DOM" than
  raw screenshot+coordinate clicking would have been, and it lets discovery
  reuse essentially all of Part 3's locator-ranking work directly instead of
  building a parallel coordinate-only path.
- **We own the ref registry, not the API.** Per Anthropic's docs, a client
  toolset's `read_page`/`find` refs (`ref_1`, etc.) are assigned and tracked
  entirely by the calling application. `agent/refs.py`'s `RefRegistry` maps
  each ref to a `Target` (frame + ranked `LocatorCandidate`s) built the same
  way Part 3's coordinate hit-testing builds one -- so a ref the model clicks
  and a coordinate the model clicks both resolve through the identical
  strict-single-match locator resolver. Frames get their own refs too
  (`frame_nav`, `frame_main`, ...), so `read_page` with no scope just lists
  frames and the model has to explicitly drill in -- an honest reflection of
  the frameset surface rather than silently flattening it away.
- **Ref lifetime is approximated, not exact.** The spec says refs stay valid
  "until tab navigates or DOM changes." We don't have a DOM-mutation
  observer wired in, so refs are cleared on the actions most likely to
  change the page (`navigate`, `left_click`, an Enter `key` press) and kept
  otherwise (e.g. across a sequence of `form_input` calls). Good enough for
  this project's stable, non-adversarial UI; documented as a scope cut
  rather than silently assumed correct.
- **Two custom tools sit alongside the toolset**: `report_success`/
  `report_stuck` give the loop an explicit, structured way to end (rather
  than inferring "done" from the model going quiet), and directly produce
  the `outputs` dict Part 5's recorder will need for the artifact's declared
  outputs. `dismiss_dialog` exists because the browser toolset has no member
  for native `confirm()`/`alert()` dialogs -- and because of the Part 3
  finding that both `evaluate()` and `screenshot()` hang while one is open,
  `read_page`/`find`/`screenshot` all check for a pending dialog first and
  return an instructive message instead of attempting to hang.
- **Redaction needed a second signal beyond label text.** The login
  password field has no label, id, or aria-label at all (deliberately, per
  Part 1's "inconsistent locator surface" design) -- so the initial
  redaction heuristic (checking the accessible-name label for "password")
  had nothing to match and let a real password value straight into
  `RecordedStep.tool_input`, caught by
  `test_password_field_is_redacted_in_recorded_steps`. Fixed by adding a
  same-origin `is_password` flag straight from the DOM
  (`input[type=password]`) in the element scan, carried on `RefEntry`, and
  checked alongside the label heuristic. A concrete example of why relying
  on a single signal for a safety property is risky.
- **All 6 new tests in `tests/test_browser_tools.py` exercise the dispatcher
  against the real fake app with synthetic tool_input dicts** (no API calls,
  no tokens spent) -- read_page frame drill-down, find+click reaching member
  detail, a stale-ref error after navigation, the risky-commit block/approve
  path via ref-based clicks, the password redaction fix above, and the
  dialog notice-instead-of-hang path.
- **The live run: done, and it took several real attempts to get right.**
  With the user's own `ANTHROPIC_API_KEY`, `claude-sonnet-5` genuinely drove
  the fake app end to end for "Look up member 10001 and read their current
  savings balance," landing on the correct balance ($2340.18) and calling
  `report_success` with structured outputs matching the goal. Evidence is in
  `/evidence/discovery-member-lookup/` (screenshots + full step log); see
  that folder's own README for the blow-by-blow. Three things had to be
  fixed along the way, each a genuine bug the live run exposed that no unit
  test had (or could have, without knowing to look):
  1. The first attempt never logged in -- it had no credentials. Discovery
     needs sign-on values supplied out-of-band, the way a real operator
     would give them, not guessed. Added a `credentials` param to
     `run_discovery()`, injected into the system prompt.
  2. Early attempts burned most of their step budget mistyping into the
     login form's deliberately unlabeled fields via click-then-`type`.
     Fixed by recommending `form_input` (direct, ref-targeted, can't lose
     focus) over click-then-`type` in the system prompt -- cut a
     ~40-step, timed-out run down to a clean 15-step success.
  3. **The password leaked into the run log three times, at three separate
     layers, needing three separate fixes** -- this is the one worth
     defending in detail:
     - `RecordedStep.tool_input`: the model's own call with the literal
       value. Fixed by redacting when the target/focused field is
       `type=password` -- but this alone wasn't enough, see next.
     - `ActionResult.observation.elements[].text`: a *different* code path
       (`Surface.observe()`'s DOM scan, used by every action's result, not
       just typing ones) reads every input's live `.value` directly, which
       for a password field is always plaintext -- the browser only masks
       it visually. Redacting the model's call did nothing for this, since
       it's an independent re-scan of the live page, not a copy of what was
       typed. Fixed at the source: the scan JS itself now returns `''` for
       any `input[type=password]`, so no current or future consumer of
       `Observation.elements` can leak it by forgetting to redact.
     - `RecordedStep.action.value`: the internal `Action` object needs the
       *real* password to actually perform the fill -- and that same object
       was going straight into the log. Fixed by logging a redacted copy
       (`action.model_copy(update={"value": "***REDACTED***"})`) built
       *after* execution, never the object used to act.
     Each fix has a regression test (`tests/test_browser_tools.py`,
     `tests/test_surface.py`) reproducing the exact scenario that leaked --
     29/29 tests pass repo-wide. The broader lesson, worth carrying into
     Part 5/6/8: a secret in this system can flow through more than one
     path to a log or artifact, and redacting the path you're looking at
     is not the same as redacting the value everywhere it can appear.

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
