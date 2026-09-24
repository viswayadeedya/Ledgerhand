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

- **Steps/checkpoint/outputs reuse `cua.core.models.Action`/`Target`/
  `LocatorCandidate` directly** rather than a parallel set of artifact-only
  types. The recorder's whole job is to take the same "how do I find this
  element" contract discovery and guardrails already share and strip it down
  to just what's needed to act -- no tool names, no raw model input, no
  `ActionResult`/`Observation` noise. Reusing the type makes that
  "decoupled from the raw transcript" requirement structural rather than
  a promise: `CapabilityArtifact.steps: list[Action]` cannot accidentally
  carry transcript baggage, because `Action` never had any.
- **The recorder is a pure function over `DiscoveryResult`, not a step in
  the discovery loop itself.** `build_artifact()` never touches the browser,
  the model, or the guardrail policy -- it takes a `RecordedStep` list (from
  a fresh run or, just as well, a `run_log.json` read back off disk) plus a
  human's declarations (which literal values are inputs vs secrets, what
  text proves the checkpoint) and produces the artifact. This keeps "what
  happened" (Part 4) and "what we're willing to call reusable" (this part)
  as separate concerns a human reviews independently -- exactly the
  "reviewable" property the brief asks for is easier to deliver when
  recording isn't entangled with discovery's control flow.
- **Only actions with a real locator survive into the artifact.**
  `ActionType.TYPE`/`KEY` (discovery-only, act on "whatever has focus," no
  target) are filtered out; only NAVIGATE/CLICK/SUBMIT/FILL/SELECT/WAIT/
  DISMISS_DIALOG make it through, since those are exactly the ones that
  carry a `Target` (or, for NAVIGATE, a URL) replay can act on
  deterministically. If a discovery run leaned on TYPE/KEY to reach the
  goal, that run's artifact would have a real gap -- which is precisely why
  Part 4's system-prompt nudge toward `form_input` over click-then-`type`
  wasn't just about speed; it's what makes a run *recordable* at all, not
  just successful.
- **Parameterization is value-matching against caller-declared dicts, not
  inference.** The recorder doesn't guess which literal values are
  "parameters" -- the caller passes `inputs={"member_id": "10001"}` and
  `secret_values={"username": "teller1"}` (values known from the same run),
  and any step value that exactly matches gets replaced with
  `{{inputs.member_id}}` / `{{secrets.username}}`. This mirrors how a human
  would actually build a capability: you know what varies because you're
  the one who ran it with that value in mind, not because the system
  pattern-matched something that looked like an ID.
- **The password placeholder change from Part 4 pays off directly here.**
  Because a verified `input[type=password]` value is redacted to the literal
  `"{{secrets.password}}"` (not a generic `"***REDACTED***"` marker), the
  recorder's parameterization step needs no special case for it at all -- it
  already *is* the correct template string by the time the recorder sees it.
  A generic marker would have needed a second translation step and an
  assumption about which secret name it corresponded to.
- **Outputs get their own locator, extracted from the final `Observation`,
  not just the value discovery happened to report.** `report_success`'s
  `outputs` dict is freeform text the model wrote in its own message -- on
  its own, replaying it would mean literally repeating "$2340.18" forever
  regardless of what the real page says next time, which isn't a capability
  at all. Instead, the recorder searches the last recorded `Observation`'s
  `elements` for one whose text matches the declared output value
  (normalized: case, `$`, commas stripped) and builds a `TEXT`-strategy
  `Target` from *that live element*, so replay re-reads the real page every
  time. This needed a small but real fix upstream: `ElementSummary` didn't
  track which frame an element came from (it was a flat cross-frame list),
  so nothing this function found could be reliably re-located later --
  added `ElementSummary.frame`, set in `PlaywrightSurface.observe()`.
- **A known simplification, cut deliberately for scope:** output/checkpoint
  locators built this way only ever get a single `TEXT` candidate, not the
  full ranked role/label/table-position/CSS fallback chain that discovery's
  own `read_page`/`find` produce for elements it actually interacts with.
  `Observation.elements` (from the lightweight `scan_frame`/`_SCAN_JS`)
  doesn't carry that richer descriptor -- only the fuller
  `scan_frame_described`/`_SCAN_DESCRIBE_JS` used by `read_page`/`find`
  does, and by the time `report_success` is called there's no guarantee the
  model still has fresh refs open on exactly the right elements. A more
  complete version would re-run the fuller descriptor scan against the live
  page at recording time (if the browser session is still open) to get the
  same ranked-candidate robustness as interacted-with elements. Documented
  here rather than silently shipped as if it were the same quality bar.
- **The checkpoint is asserted by a human, not inferred.** `checkpoint_text`
  is a required parameter with no default -- the recorder refuses to build
  an artifact without it (`ArtifactBuildError`). This matches the glossary's
  own framing of a checkpoint ("a condition you assert to confirm you
  actually reached the state you expected") -- asserting is something a
  person does when reviewing a capability before trusting it for replay, not
  something safe to auto-detect from whatever text happened to be on the
  final screenshot.
- **Format: YAML files under a top-level `/artifacts/` directory**, one file
  per capability, mirroring `guardrails/policy.yaml`'s precedent that a
  human-reviewed contract reads better as commented-shape YAML than a JSON
  blob or a Python literal. `/evidence/` stays the proof-of-work (logs,
  screenshots) and links to the artifact rather than duplicating it, so
  there's exactly one copy to keep in sync as artifacts change.
- **First real artifact built and committed**: `artifacts/member-savings-lookup.yaml`,
  built from `evidence/discovery-member-lookup/run_log.json` via
  `python -m cua.artifacts` (see `artifacts/README.md` for the exact
  command). 7 replayable steps, 4 outputs each with their own locator, a
  resolved checkpoint, no secret or input literal anywhere in the file
  (verified by test and by hand). 12 new tests in `tests/test_recorder.py`
  -- notably built directly against this *real* run log, not a synthetic
  fixture, so the tests double as a second confirmation the artifact is
  sound. 41/41 tests pass repo-wide.

## Part 6 — Replay + error taxonomy

- **Five-way outcome taxonomy**: `SUCCESS`, `RECOVERED`, `BUSINESS_OUTCOME`,
  `NEEDS_HUMAN`, `HARD_FAILURE`. The brief's own three-way split (expected
  business outcome / recoverable condition / hard failure) maps onto the
  first three and the last; `RECOVERED` and `NEEDS_HUMAN` are additions:
  `RECOVERED` is a *terminal* outcome distinct from a clean `SUCCESS` --
  same outputs, but flagged because it only got there after handling a
  hiccup (a dialog, a dropped session), which matters for spotting a flaky
  artifact over many replays even though the caller's data is fine.
  `NEEDS_HUMAN` is the direct hook into Part 7: a policy-blocked risky step,
  or a business outcome explicitly marked `requires_human` (e.g. an
  ambiguous multi-match result), ends here instead of either succeeding
  silently or being reported as an ordinary answer.
- **Business outcomes are detected the moment a step fails to resolve, not
  only after every step in the recipe has already run.** First cut only
  checked `artifact.business_outcomes` once, after the full step list
  finished -- which meant "member not found" was reported as a
  `HARD_FAILURE`, because there's no "View" result to click on a
  not-found page, so the very next recorded step (by design, since it
  assumes the happy path) fails to resolve before the engine ever got to
  ask "did we land somewhere recognized instead?". Fixed by extracting a
  shared `_check_business_outcomes()` called both post-steps (the checkpoint
  path) and from inside a failed step's error handling, before that failure
  is allowed to become a `HARD_FAILURE`. A business outcome mid-flow is a
  real answer, not a bug to paper over.
- **Output locators had to move from matching *the value* to matching *the
  cell*.** The first artifact built in Part 5 located each output by
  searching for its literal discovery-time text ("$2340.18") and storing a
  `TEXT` candidate for that exact string. Replaying the identical artifact
  against a *different* member (10002 instead of 10001) failed outright:
  "$2340.18" is Maria Garcia's balance, and Carlos Garcia's page will never
  contain it. Fixed by preferring a `TABLE_POSITION` candidate (row/col,
  content-independent) for outputs specifically, falling back to `TEXT`
  only when no table position is available -- required adding
  `ElementSummary.table_row`/`table_col` (the lightweight `Observation`
  scan didn't track position at all; only the richer read_page/find scan
  did) and extending the scan JS to compute it. Verified by replaying the
  *same* artifact against member 10002 and getting Carlos Garcia's real,
  different, correctly-read balance back -- see
  `evidence/replay-member-lookup/README.md`.
- **The same value-locking mistake, one layer up: a business outcome's own
  detect text.** `add_business_outcome()` was first built the same way --
  search for a phrase, then store the *matched element's full text* as the
  candidate. Captured against a probe search for member "99999", the stored
  text was "No member found matching \"99999\".", which only ever matches
  that one nonexistent ID again. Fixed by storing the caller's own search
  phrase ("No member found") instead of the matched element's text --
  Playwright's `get_by_text()` matches substrings by default, so the
  shorter, stable phrase still finds the message regardless of which ID is
  in it. This is the same lesson as the output-locking bug, in a different
  part of the recorder, which is worth naming plainly: matching by *content*
  is fine for detecting an element once, but wrong for building a locator
  meant to keep working after the content changes.
- **A locator resolved instantly (`.count()`, no wait) is right for "does
  this exist right now" and wrong for "has the page finished navigating
  yet."** `resolve()`'s single, instant DOM check is deliberate for the
  strict single-match semantics locator.py's own tests rely on, but using it
  for checkpoint/output resolution right after a cross-frame navigation
  raced against real backend response time. Added `resolve_with_wait()`
  (bounded polling, default 3-5s) as a separate function -- `resolve()`
  itself is untouched, still the fast, non-waiting primitive for tests and
  the recorder (which only ever looks at already-captured, static data).
  Surface's own step execution and the replay engine's ending resolution
  both switched to the waiting version; discovery's `browser_tools.py` still
  goes through the same `Surface.act()` path, so it benefits too.
- **A dialog can appear as a side effect of a *successful* action, not only
  as the reason one failed.** The member-detail page's popup fires from a
  `window.onload` handler *after* the click that navigated there already
  reported success -- so the recovery logic (originally written to trigger
  only inside a failed step's retry loop) never ran, and the *next* locator
  resolution (checkpoint, or the next step) hung against a page whose
  script/render pipeline the dialog was blocking (the same underlying
  Playwright behavior Part 3 found for `evaluate()`/`screenshot()`, now
  hitting `resolve_with_wait()`'s `.count()` check too). Fixed by checking
  for and dismissing a pending dialog after every *successful* action as
  well, plus defensively at the start of ending-resolution.
- **The session-expiry heuristic broke on a query string.** `_looks_like_
  session_expired()` first compared full URLs; our own fake app's "your
  session expired" redirect is `/login?expired=1`, which never equals the
  bare `entry_url` and doesn't end in exactly `/login` either. Fixed by
  comparing `urlsplit(...).path` on both sides -- a reminder that a
  same-page-different-query-string redirect is a completely normal, expected
  shape for this exact condition, not an edge case to special-case away.
- **Recovery is bounded to one retry per step, always** (`MAX_RECOVERY_
  ATTEMPTS = 1`), whether it's a dismissed dialog or a re-authentication.
  Re-authentication replays the exact step prefix that already worked once
  in this same run (not a guess at "the login steps" by name or position
  convention) -- `ctx.rendered_steps[:idx]`, the literal already-rendered
  actions.
- **`scan_frame`'s element selector was too narrow for general text
  content.** Building the `member_not_found` business outcome failed
  silently at first -- the captured observation had zero elements from the
  "main" frame at all. The fake app's not-found message is a bare `<p>`,
  and the selector (`a,button,input,select,textarea,td,th`) never matched
  paragraph text, only table cells and interactive controls. Added
  `p,li,h1..h6` to the selector used for `Observation.elements` (not to
  `read_page`/`find`'s selector, which deliberately stays interactive-only
  per the Part 4 design).
- **`add_business_outcome()` is deterministic exploration, not LLM
  discovery**, and says so in its own docstring: `scripts/
  capture_business_outcome.py` drives the artifact's own recorded
  login+search step prefix (via `render_action`, the same machinery replay
  uses) against a known-invalid member ID and captures what's really there,
  rather than an LLM re-discovering a fact already known from building the
  fake app in Part 1. The distinction matters for the write-up: the one
  *required* genuine LLM-driven run is Part 4's discovery session; enriching
  an artifact with known failure modes afterward is closer to how a human
  engineer would actually build out a runbook's edge cases, and pretending
  otherwise would overstate what's LLM-driven here.
- **Guardrails apply to replay exactly the same way they apply to
  discovery** -- `ReplayEngine` calls `Surface.act()` for every step, the
  same method `browser_tools.py` calls, so the same `PolicyEngine.evaluate()`
  checkpoint (Part 2/3) runs regardless of caller. `test_replay_blocks_
  risky_action_and_needs_human_approval` proves this independently for
  replay specifically (not just re-relying on Part 3's proof), including
  that `human_approved=True` is the only way a blocked step proceeds.
- **49 tests pass repo-wide**, 8 of them new in `tests/test_replay.py`
  (success, cross-member generalization, business outcome, both recovery
  paths, guardrail blocking, hard-failure detail, missing-params
  validation) -- every bug described above has a regression test that
  reproduces the exact failing scenario first, not just a description of
  the fix. Plus three real, tracked replay runs against the live fake app
  in `evidence/replay-member-lookup/` (success, business outcome, recovered
  popup) with screenshots and full `ReplayResult` JSON for each.

## Part 7 — Human handoff

- **The handoff mechanism gets the exact same live `PlaywrightSurface` the
  run was already using -- never a fresh one.** `HandoffHandler.escalate(
  request, surface)` receives the actual object `ReplayEngine` has been
  driving all along. Automation makes no further calls to it until
  `escalate()` returns; that's the "who is in control" seam the brief asks
  for made literal in code, not just documented -- control is wherever
  `escalate()`'s implementation is, for as long as it's running.
- **Two real handoff implementations, not one mock.**
  `InteractivePauseHandoff` uses Playwright's own built-in mechanism for
  this exact scenario -- `page.pause()` opens the real Playwright Inspector
  against the live browser, lets a person click/type/navigate in it
  directly, and returns control the instant they click Resume. We didn't
  build a custom co-browsing UI; we reused the one the automation tool we
  already depend on ships for precisely this purpose. It needs a headed
  session and a person physically present, so it can't be exercised by an
  automated test (or by me, an AI, without a person to click Resume) --
  `MockOperatorHandoff` (a programmable callback standing in for the
  operator, still handed the same real surface) is what proves the
  control-transfer model in tests and in the evidence under
  `evidence/handoff-ambiguous-duplicate/`, per the brief's own allowance to
  mock the operator UI as long as the handoff mechanism and control-transfer
  model are real. A third, `TerminalOperatorHandoff`, sits between the two:
  a real person makes a real decision, at a terminal instead of a
  browser-based console -- only the "look at the live page yourself" part is
  mocked (a screenshot path is printed instead of an embedded view), which
  is exactly the piece the brief's scope note says is fine to mock.
- **Three decisions an operator can make, matched to what's actually being
  asked of them**: `APPROVE_AND_RETRY` (go ahead, retry the exact step that
  was blocked -- used for a policy-blocked risky action, where automation
  still does the honors once approved), `MANUAL_RESOLVED` (the human already
  fixed it live in the browser -- used for an ambiguous business outcome,
  where there's no single "step" to retry, just a state only a person could
  disambiguate), `ABANDON` (decline; stop here). Business-outcome escalation
  only offers the latter two -- there's no specific action to retry when the
  problem is "I don't know which of two records is right," only "did you
  fix it" or "no."
- **`MANUAL_RESOLVED` skips the rest of the scripted steps, going straight
  to checkpoint/business-outcome resolution**, rather than blindly resuming
  the recorded step sequence. Once a human has taken the wheel, the old
  script's assumptions about page structure may no longer hold (they might
  have ended up somewhere the recipe didn't anticipate) -- so replay just
  asks "did we reach a recognized ending?" instead of trusting the next
  scripted click still makes sense. Implemented via a small internal
  `_SkipToEnding` signal, the same pattern `_ReplayEnd` already used for
  "stop early with a result."
- **A real, non-obvious bug the ambiguous-duplicate scenario surfaced**:
  the recorded "click View" step's locator candidates rank
  `TABLE_POSITION` as a fallback (Part 3's robustness ranking), and a
  table-position locator resolves by *position*, not content -- so it
  clicked "row 1's View link" successfully even when the search had
  legitimately come back ambiguous, sailing right past the business outcome
  without ever failing. Reactive detection (checking business outcomes only
  when a step fails to resolve, Part 6's original design) is therefore
  incomplete: some outcomes can be true on the page without any step ever
  failing, depending on how robust that step's own locator happens to be.
  Fixed by checking business outcomes *before* each step too, not only
  after failure -- using `resolve_with_wait(timeout_ms=0)` (a single,
  instant check, not a wait) so this costs nothing extra on the normal,
  nothing-matches path where it runs on every step. This is a genuine
  interaction between two features built for different reasons (locator
  robustness in Part 3, business-outcome safety in Part 6) that only showed
  up once both were exercised together against a real ambiguous state --
  exactly the kind of thing a design doc can't predict and only running the
  system for real surfaces.
- **Escalation is bounded to one attempt per named outcome per run**
  (`ctx.escalated_outcomes`), the same "never retry indefinitely" principle
  Part 6 applied to dialog/session recovery. If a human declines, or the
  page genuinely hasn't changed after they say they fixed it, the second
  encounter with the same outcome name returns `NEEDS_HUMAN` directly
  instead of asking again.
- **A successful handoff still shows up as `RECOVERED`, not a 6th outcome
  value.** Reusing the taxonomy from Part 6 rather than inventing
  `ESCALATED_AND_RESOLVED`: `RECOVERED` already means "reached a good
  outcome, but only after handling something along the way," which is
  exactly what happened -- `result.escalations` (a new field, alongside the
  existing `recovery_events`) is what distinguishes "needed a human" from
  "needed an automatic dialog dismiss" for anyone inspecting the result
  afterward, without growing the outcome enum for what's really a *detail*
  of how SUCCESS-shaped-but-not-quite-clean was reached.
- **Escalation context is written to disk before the operator ever acts**,
  not after (`write_escalation_request` runs first in every handler,
  `append_decision` only afterward) -- so the intervention request survives
  even if the operator never responds, matching "preserve context and
  evidence across the handoff" from the brief. It includes a screenshot,
  gated the same way Part 3 found necessary elsewhere: skipped if a native
  dialog is currently blocking the page (would hang otherwise), same
  root cause as the Part 3/Part 6 dialog-hang findings.
- **Second business outcome added for real**: `ambiguous_duplicate`
  (`artifacts/member-savings-lookup.yaml`), captured the same deterministic-
  exploration way as `member_not_found` (`scripts/
  capture_ambiguous_duplicate_outcome.py`, arming the fake app's
  `duplicate_members` fault and driving the artifact's own recorded prefix),
  marked `requires_human=True` -- this is the "genuinely ambiguous, don't
  guess" case Part 1's own design commentary flagged as the right kind of
  thing to escalate rather than silently resolve.
- **53 tests pass repo-wide**, 4 new in `tests/test_handoff.py`: no-handoff-
  configured behaves exactly like Part 6 (regression guard), operator
  abandons an ambiguous outcome, operator resolves one by acting on the
  live session (the key same-session proof), and operator approves a
  policy-blocked risky step end to end through the real handoff path (not
  just pre-setting `human_approved=True` the way Part 6's own test did).
  Plus two real, tracked replay runs against the live fake app and the
  actual committed artifact in `evidence/handoff-ambiguous-duplicate/`
  (abandoned and resolved), with escalation records, screenshots, and full
  results.

## Part 8 — Evidence + tests

Mostly an audit pass, not new construction: evidence and tests were built
incrementally alongside every part from 4 onward (real runs, real
screenshots, real regression tests for each bug found), rather than saved
up for the end. This part is what's actually left once that's true.

- **Added `tests/test_fake_app.py` -- the one real coverage gap.** Every
  other layer (guardrails, locator, surface, browser_tools, recorder,
  replay, handoff) has direct tests, but the fake app itself
  (`src/cua/fake_app/`) had only ever been exercised *through* those layers,
  via a real Playwright browser -- correct, but slow, and never testing the
  app's own HTTP logic in isolation. 16 new tests via FastAPI's `TestClient`
  (no browser, no Playwright) covering auth, search (found/not-found/
  natural duplicates), the sub-account flow, every fault's fire-once
  behavior, and admin reset -- all in well under a second. 69 tests pass
  repo-wide now.
- **Full-repo secret sweep, not just the specific files already checked.**
  `git grep -liE "teller123|sk-ant-api"` across every tracked file, not
  just the evidence directories redaction bugs were found in earlier. Every
  hit is a legitimate, intentional occurrence of the fake app's own
  hardcoded demo credential (its definition in `fake_app/main.py`, and
  references to it in docs/scripts/tests) -- none in any run log, result,
  or artifact file. No real API key pattern anywhere. `.env` itself was
  confirmed never tracked, at any point in git history.
- **Added a top-level `/evidence/README.md`** indexing the four run
  folders (discovery, replay, handoff) with what each demonstrates and the
  exact commands that produced them, plus an explicit note on what
  `evidence/runs/` is (git-ignored scratch, not evidence) so that
  distinction doesn't have to be inferred.
- **Fixed drift between evidence READMEs and the files they describe.**
  The discovery evidence folder was regenerated several times over the
  course of Parts 5-6 (chasing the `ElementSummary.frame` and
  `table_row`/`table_col` fixes), and its README had accumulated stale
  claims -- a step count from an earlier run (15 vs. the current 16), an
  output key name that had changed (`name` vs. `member_name`, since the
  model doesn't name its own outputs identically every run), and a
  redaction-marker example (`***REDACTED***`) that predated the Part 5
  switch to the literal `{{secrets.password}}` placeholder. Fixed by
  diffing every evidence README's specific claims against the actual
  current files rather than trusting what was written when each was first
  produced -- exactly the kind of small, easy-to-miss inconsistency an
  audit pass exists to catch.
- **What this part deliberately did not add**: a static type-checker
  (mypy) as a CI-style gate. The codebase uses type hints and Pydantic
  models consistently throughout by convention, and the brief's own
  evaluation criteria lists code quality last and asks for "reasonably
  typed," not a fully strict-mode pass -- adding one this late and chasing
  every finding would have been schedule risk for a lower-weighted
  criterion than the ones the rest of this project spent its time on.
  Noted as a real cut, not a silent omission -- see `REPORT.md` Section 7.

## Part 9 — README + REPORT

- **REPORT.md is a distillation, not a copy.** This file (`DECISIONS.md`)
  is the chronological, complete record -- every decision, every real bug
  found and fixed, in the order it happened, useful for understanding *how*
  the system got here. `REPORT.md` is written for a reviewer meeting the
  project cold: the same underlying reasoning, reorganized around the
  seven required headings and cut down to what's load-bearing for
  evaluation, not the debugging journey. Deliberately kept close to the
  brief's own "~1-3 pages" guidance rather than trying to include
  everything this log has -- a reviewer who wants the full story already
  has this file.
- **Every command in README.md was actually run before being written
  down**, not transcribed from memory of what the CLI *should* accept --
  including the PowerShell-specific backtick line-continuation syntax and
  the `Invoke-RestMethod` fault-arming calls (real `curl` flags don't work
  against PowerShell's `curl` alias, a genuine trip-up earlier in this
  project's own testing -- see the conversation around Part 7's first
  handoff walkthrough). Verified end to end: activate venv, arm a fault,
  replay, see the expected `recovered` outcome with the expected balance.
- **README.md leads with what a reader needs to *do*, REPORT.md with what
  a reader needs to *understand*.** The split mirrors the brief's own two
  deliverables rather than merging them: setup + exact runnable commands
  in one file, architecture/trade-off reasoning in the other, so neither
  has to do both jobs at once.

## Post-review hardening — Phase 1: prove it's the right record

Review feedback: the checkpoint only asserted that "Savings Balance"
appeared on the page -- text that is true of *every* member's detail page.
Land on the wrong record and replay returns SUCCESS with someone else's
money. In banking, a confidently wrong answer is worse than a crash.

- **Assertion lives on the output, not in a separate `assertions` block.**
  `OutputSpec.must_equal` holds a template (`"{{inputs.member_id}}"`)
  rendered at replay time with the same `render_value` the steps use, then
  compared to what was actually read off the page. Chose this over a
  general-purpose assertions list (left/right/operator) because the only
  requirement was "an extracted output must equal an input", and a
  comparison DSL would have been a second, parallel way to express
  things the artifact already expresses -- more schema for no additional
  capability today.
- **Comparison is trim-then-exact, deliberately not fuzzy.** Normalizing
  case or stripping punctuation would make the check pass in cases it
  shouldn't; an identity check that's lenient isn't one. The documented
  cost: a page rendering `Member #10001` rather than `10001` wouldn't
  match and would need its own output/locator.
- **A mismatch aborts before `outputs` is populated**, rather than
  returning a flag alongside the data. Chose raising over returning
  because any code path that can hand back a value from the wrong record
  -- even marked "unverified" -- is one a caller can ignore.
- **Reused HARD_FAILURE rather than adding a sixth outcome**, per review.
  It is technically a *recognized* bad state rather than an unrecognized
  one, but the taxonomy's job is telling a caller what to do, and
  "never treat this as an answer" is exactly HARD_FAILURE's job.
- **Added `FailureReason` codes to `ReplayError`** (per review):
  `identity_mismatch`, `format_invalid`, `element_not_found`, `timeout`,
  `unrecognized_state`, `step_failed`, `recovery_failed`,
  `policy_blocked`, `operator_abandoned`. `message` serves a human reading
  one failure; the code serves everything else -- triage, alerting, and
  counting failures by kind across tenants. An artifact throwing
  `element_not_found` at one tenant is drift; one throwing
  `identity_mismatch` is a correctness emergency, and telling those apart
  by grepping message strings would be a bad way to find out.
  Made it a **required** field so every construction site has to classify
  itself rather than defaulting to a vague catch-all.
- **Classified failures structurally, not by sniffing error text.** Added
  `ActionResult.error_kind` ("locator_not_found" / "timeout" /
  "surface_error"), set by the surface where the exception is actually
  caught, so the engine maps a *kind* to a reason code instead of
  pattern-matching prose that could be reworded at any time.
- **Widened `Surface.act()`'s exception handling while doing it.** It
  previously caught only `LocatorResolutionError`; a Playwright timeout or
  a closed-page error escaped `act()` and crashed the whole run instead of
  becoming a reportable failure. That also meant the `timeout` reason code
  would have been unreachable in practice. Now broad-caught and tagged,
  with the exception type kept in the message so nothing is lost.
- **Partial masking (`***01`) rather than full redaction** in error text,
  per review. Full redaction of an identifier makes a failure
  undebuggable -- an operator can't tell which request went wrong --
  while the full value shouldn't be persisted. Keeping the last two
  characters lets a human correlate the error with the request they made
  and leaks almost nothing. One helper (`redact.mask_partial`) so the
  rule lives in a single place.
- **Assertion templates are validated in `_validate_params`**, before a
  browser opens, alongside the existing missing-input/secret checks -- a
  malformed artifact should fail at the door, not halfway through a run.
- **Tested against a real fault, not a synthetic mismatch.** Added a
  `wrong_member` fault to the fake app: it serves a *different* member's
  detail page with a 200, no redirect, and a page that looks entirely
  normal. It's the nastiest fault in the set -- every other one is
  visible. Verified the gap is real before claiming to fix it: with the
  same fault and the pre-Phase-1 artifact, replay returned
  `outcome: success` with `member_id: 10002, savings_balance: $8112.02`
  when 10001 was requested. Notably the artifact was *already extracting*
  `member_id` -- the data needed to catch this was being read and thrown
  away, never compared.
- **Known limit, documented on the field itself**: this only works when
  the page displays the identifier. A capability whose result page never
  echoes its input can't prove identity this way and needs a different
  anchor.

## Post-review hardening — Phase 2: anchor outputs to labels, not positions

Review feedback: every output was located by table position alone
(`savings = row=3,col=1`). A tenant's version of the same vendor page with
one extra row in it silently returns the wrong value.

- **New `TABLE_LABEL` strategy, ranked above `TABLE_POSITION`.** Stored as
  `label=Savings Balance,col=1` -- "the row whose label cell reads exactly
  this, then cell N of that row". Chose a readable key=value string over an
  XPath (`following-sibling::td[1]`) because the artifact's whole point is
  being reviewable by a human, and an XPath in YAML is neither reviewable
  nor tag-agnostic (`td` vs `th`).
- **Exact label match, not substring**, per review. `get_by_text(label,
  exact=True)`: "Balance" must not match a row labelled "Savings Balance".
  Substring matching fails two ways -- it resolves two rows (loud, fine) or
  quietly picks the wrong one when only one row happens to contain the
  substring (silent, not fine). Exactness removes the second case entirely.
  Parser is greedy on the label so a label containing ",col=" still splits
  on the real separator.
- **The label comes from the captured observation, not from anyone typing
  it.** The recorder looks for the cell at (same frame, same row, col-1)
  and uses its text; no label cell there means no anchor and the locator
  falls back to position alone, rather than inventing one.
- **`TABLE_POSITION` kept as the fallback rather than replaced.** The two
  fail in different directions -- a renamed label breaks the anchor, an
  inserted row breaks the position -- so keeping both covers more real
  drift than either alone. This is the same ranked-candidate idea the
  steps already use, applied to outputs for the first time.
- **Format validation folded into `type` rather than a separate `format`
  field.** The review asked for both "optional format validation" and
  "balances get a money type"; making `type: money` *carry* its own
  pattern means one concept in the YAML instead of two that could
  disagree. `string` stays permissive, `money` and `integer` validate, and
  a value failing its type is HARD_FAILURE (`format_invalid`) and never
  returned. Made `OutputType` a proper enum so an unknown type fails at
  load rather than silently skipping validation.
- **Deliberately loose about presentation, strict about shape**: money
  accepts a leading `$` and thousands commas but requires exactly two
  decimals. Parenthesised negatives ("($5.00)") are *not* covered and
  documented as such -- a pattern loose enough for those is loose enough
  to let a date through, which is exactly the thing this is for.
- **Ending resolution restructured into three passes** (read everything →
  assert identity → validate shape). Previously one loop did all three per
  output, which meant the order outputs happened to be declared in decided
  which problem got reported. Identity now always reports before shape:
  "you're on the wrong record" is a more useful diagnosis than "that
  balance looks like a date" when both are true.
- **Tested with `extra_row`, a deliberately *benign* fault.** The other
  faults simulate breakage; this one simulates a tenant whose page just
  has one more field -- harmless to a human, silently fatal to positional
  locators. Proved both directions rather than only the fix: with the row
  inserted, the label-anchored artifact still returns `$2340.18`, while
  the position-only artifact returns `outcome: success` with
  `savings_balance: "2019-03-14"` and the savings figure reported as
  `checking_balance` -- a wrong answer that looks entirely plausible.
  A third test shows the declared `money` type catching that same shifted
  value as a second, independent layer.

### Phase 2 follow-up: ambiguity, exit codes, and a frozen comparison

- **An ambiguous label was silently answering with the first row.** The
  requested "label matches two rows" test found a real bug rather than
  confirming existing behaviour: `row.locator("td, th")` on a multi-row
  match flattens every row's cells into one list, so `.nth(col)` returned
  row 1's cell and `resolve()` counted exactly one element and accepted
  it. `build_locator` now returns the row match itself when it isn't
  unique, so `resolve()` sees the true count and refuses. Chose that over
  raising inside `build_locator` because it keeps every "how many matched"
  decision in one place -- `resolve()` -- instead of splitting the
  exactly-one rule across two functions.
- **Failures name the locator, and say zero vs. many.** "matched nothing"
  (the page changed) and "ambiguous: matched N elements" (the locator
  stopped being unique) arrive identically and need opposite fixes.
  `_extract_text` now returns the resolver's account of why instead of
  discarding it, so an extraction HARD_FAILURE says *which* label went
  wrong rather than "not found".
- **Three extra_row tests collapsed into one.** The contrast between the
  three artifacts *is* the claim; as separate tests one could be deleted
  without the others noticing, and the comparison was only visible to
  someone who read all three.
- **Process exit codes: 0 success/recovered, 1 hard failure, 2 business
  outcome, 3 needs human.** So an orchestrator can branch without parsing
  stdout. The distinction that matters: a caller retrying on any nonzero
  exit would retry "no such member" forever, when nothing is broken and
  the answer will never change. `recovered` shares `0` with `success`
  because it *worked* -- needing a retry is detail, not failure.
- **Bad command lines moved to `64` (`EX_USAGE`).** argparse exits `2` by
  default, which would have been indistinguishable from a business
  outcome. Chose to override argparse rather than renumber the outcomes,
  since the outcome codes are the contract callers depend on.
- **CLI exit codes tested through a real subprocess**, not by calling
  `main()` and checking its return value -- the latter would pass even if
  nothing ever handed it to `sys.exit()`. A separate test asserts every
  `ReplayOutcome` has a code, so adding an outcome can't reach the CLI and
  raise `KeyError` at the end of an otherwise-successful run.
- **`evidence/extra_row/` freezes its own artifacts** rather than pointing
  at `artifacts/member-savings-lookup.yaml`. That file is being
  regenerated in this same hardening pass; referencing it would quietly
  turn the three-way comparison into three identical runs. The frozen
  artifacts deliberately don't mark the balances `sensitive`, because the
  comparison's entire content is the values -- masked to `***14` and
  `***18`, "a date landed in a money field" becomes invisible. Real
  capability runs under `evidence/runs/` stay masked.

### Phase 3: making the artifact reviewable

The artifact is the thing a human is supposed to approve before it runs
unattended. That only works if it can actually be read.

- **Step descriptions are generated, not hand-written, and come from the
  locator that actually resolved at discovery.** "Click the Sign On
  button" is checkable against the real page; `tr:nth-child(5) > td >
  input` is not. Nothing is inferred about *intent* -- the recorder
  doesn't know it, and a confident wrong sentence next to a step someone
  is meant to be checking is worse than no sentence.
- **`Action.description` reused rather than a second description field.**
  It has been on `Action` since Part 4 for the model to explain itself
  during discovery, and has been null on every recorded step. Filling the
  blank was the whole ask; adding `ArtifactStep.description` beside it
  would have left two fields with one meaning. A discovery-supplied
  description, when there is one, wins over the generated one.
- **Descriptions name parameters without reproducing their values**: "the
  password secret", "the member_id input". The templates exist precisely
  so credentials and member IDs aren't in the file -- a generated sentence
  must not put them back. Tested.
- **Field labels for form steps read from the captured observation**,
  looking left (`User ID: | [input]`) then above (`Member ID or Last
  Name:` over the box), because this app uses both layouts. Looked up by
  coordinate rather than via the element being filled, since a password
  input is deliberately never scanned and so *nothing* is recorded at its
  position -- going through it would have left exactly the password field
  undescribed.
- **Risk is judged from where the step actually went, by diffing the
  observation before and after it.** A click's selector says nothing about
  its destination, and `*/new-subaccount/commit` is a route, not a button.
  Diffing rather than reading "the main frame" keeps it honest on a
  frameset app, where the control clicked lives in one frame (nav) and the
  navigation happens in another (content) -- the frame the step *acted in*
  is the wrong answer, and needs app-specific knowledge to correct. A step
  that navigated nowhere says so instead of reporting its current URL as a
  "destination" it never travelled to.
- **`unverified` when nothing was observed**, rather than defaulting to
  safe. Saying "nobody checked" is honest; labelling an unchecked step
  safe is the failure mode this whole pass exists to remove.
- **Risk labels are review metadata, not a third enforcement point.**
  Replay still re-evaluates every action live through the same
  `PolicyEngine`, so a step labelled safe that navigates somewhere risky
  at replay time is still blocked then. The label answers "should I be
  nervous about step 7" before anyone runs it.
- **`ArtifactStep` subclasses `Action` instead of wrapping it.** A wrapper
  (`{description, risk, action}`) would have been tidier and would have
  broken every existing artifact -- too much for the 1.1 bump Phase 4
  makes, which should mean old files still load. As a subclass, replay,
  the policy engine and the surface all keep receiving something that *is*
  an `Action`, and a `before` validator accepts a plain `Action` so code
  that builds artifacts directly doesn't need to know the type exists.
- **YAML written with `exclude_defaults` rather than "drop anything
  falsy".** `value: ''` on a fill is a real instruction (clear this box)
  and differs from the default of `None`, so it survives; a blanket
  empty-check would have silently changed what artifacts do. Chose that
  over hand-listing fields to prune, which would drift every time a field
  is added. `schema_version` and `version` are written even at their
  defaults -- a reader shouldn't need to know the defaults to answer "what
  version is this?". A round-trip test keeps "readable" from being bought
  with a broken contract.
- **Masking is opt-in per artifact and applied at the write-down
  boundaries only.** The value reaches the caller intact -- it asked for
  the balance and needs the balance; the scrollback and the disk don't.
  `--show-sensitive` affects the terminal only, because that is a person
  looking at their own screen for a moment; `--out` files and everything
  under `--evidence-dir` stay masked with no flag, because they outlive
  the run and get read by people who were never part of it.
- **`mask_partial` keeps the last two characters** rather than full
  redaction, so an operator can still correlate "***18" with the balance
  they were asked about. Full redaction makes a failure undebuggable;
  the full value shouldn't be on disk at all.
