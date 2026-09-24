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

### Canonicalized destinations, and shape hints for masked values

- **Observed destinations are canonicalized: `/app/member/10001` becomes
  `/app/member/:member_id`.** The risk labels added in Phase 3 were
  quietly reintroducing the one thing the rest of the recorder works to
  keep out -- a real record identifier from one discovery run, baked into
  a file that is committed and reused for every other member. **This is
  the canonicalization the brief asks for**, applied to the place it was
  still leaking. `/app/member/10001` is a fact about one run;
  `/app/member/:member_id` is a fact about the capability.
- **Whole path segments only, never substrings.** An input of `"1"` must
  not turn `/app/member/10001` into `/app/member/:member_id0001`. Tested,
  because a substring rule looks correct on the one ID you developed
  against and corrupts every other one.
- **Navigate URLs are parameterized too, not just canonicalized in the
  label.** A recorded `navigate` to a record-specific page kept that run's
  identifier, so replaying it for a different member would have silently
  fetched the *original* member's page -- a wrong answer, not a crash.
  `render_value` substitutes placeholders anywhere in a string, so a
  segment-level `{{inputs.member_id}}` renders correctly at replay.
- **`InputSpec.example` is no longer filled from the discovery literal.**
  The recorder is given the literal so it can *recognize* and parameterize
  it away; storing it back as an example undid exactly that work and left
  a real member ID in a committed file. The field stays in the schema for
  an artifact whose example is genuinely safe to publish.
- **One test asserts no input value survives anywhere in the serialized
  artifact**, rather than checking each field. The ways a literal can leak
  back in are not enumerable in advance -- it has now shown up in a step
  value, a navigate URL, an input example and a risk note, each for a
  different reason.
- **Masked values carry a shape hint: `***18 [shape: money]`.** Masking a
  value and *then* using it as evidence are in tension: `***14` and
  `***18` are equally unreadable, so a masked record of the extra_row
  locator bug would have hidden the very thing it exists to show. The
  shape publishes the one property that matters -- what kind of value this
  is -- while withholding the value. Off by default; on for outputs and
  for `format_invalid` errors, where the complaint is *about* the shape
  and "***14" alone would state the problem while withholding the evidence
  for it.
- **Shape patterns live in `guardrails/redact.py`, not reused from
  `OutputType`.** Guardrails sits below the artifact layer and shouldn't
  depend on it, and the two answer different questions -- "date" is a
  shape worth naming when masking and is not a type an artifact declares.
- **`evidence/extra_row/` is now masked with no exceptions.** It was
  originally captured unmasked, on the argument that masking would destroy
  the comparison. That argument was correct until the shape hint existed
  and wrong afterwards; the folder was regenerated rather than kept as a
  standing exception to the redaction rule. Recording the reversal here
  because "we argued for the exception and then removed the need for it"
  is more useful to a reviewer than a folder that simply looks consistent.

- **`member_id` is sensitive as an output *and* as an input.** It was left
  unmasked at first on the grounds that it is only the caller's own input
  echoed back. That was inconsistent: the identity-mismatch error already
  masked the same ID to `***01`, so printing it in full two lines later
  made the rule look arbitrary rather than principled. A value is either
  sensitive or it isn't, and which message it appears in doesn't change
  that.
- **`InputSpec.sensitive` added rather than inferring it from a matching
  output name.** Masking an output while filing the identical value under
  `"inputs"` two lines above it would be theatre, but name-matching would
  only protect inputs that happen to be echoed back as outputs -- a narrow
  rule with an obvious hole. An explicit declaration covers an input that
  is never displayed, and is additive, which suits the 1.1 bump.
- **The reproduction command in evidence keeps its literal
  `--input member_id=10001`.** It is the recipe for re-running the
  evidence, not a record of data; masking it leaves evidence nobody can
  reproduce, which is the same reason `--secret password=teller123` is
  printed there. Both are fake credentials for a fake app, already
  published in the top-level README.

## Post-review hardening — Phase 4: versioning and regeneration

- **`schema_version` 1.1, artifact `version` 2 — a minor bump, not a
  major.** Every field added across Phases 1-3 (`must_equal`, `type`,
  `sensitive`, step `description`/`risk`) is optional and defaults to the
  old behaviour, so a 1.0 artifact loads and runs exactly as before. That
  is what "minor" is for, and it is why `ArtifactStep` subclasses `Action`
  rather than wrapping it -- a wrapper would have restructured `steps` and
  forced a 2.0.
- **Older artifacts warn rather than fail.** Refusing 1.0 outright would
  strand every artifact built before this week for no safety gain; loading
  it silently would let someone keep running a capability that isn't
  proving it reached the right record. The warning names the specific
  thing it predates (`must_equal`) rather than saying "outdated".
- **Newer artifacts are refused, not partly applied.** Pydantic would
  happily drop fields it doesn't recognise, and the fields most likely to
  be new are *checks* -- so running a 1.2 artifact on 1.1 code would mean
  ignoring the safety it was written with and then reporting success.
  Forward-compatibility is the wrong default when the unknown parts are
  the guardrails.
- **`load_yaml` unwraps the version error.** Pydantic buries a validator's
  exception under field paths and a docs link. A person told "this
  artifact is newer than your code, upgrade cua" can act; the same person
  shown a `value_error` traceback usually can't.
- **Regenerated from the existing `run_log.json`, with no LLM and no new
  discovery run.** The point of separating discovery from recording is
  that re-deriving an artifact shouldn't need the model again -- this pass
  exercised that claim rather than restating it. Business outcomes were
  re-attached by re-running the two capture scripts, so their locators
  still come from really-captured page states rather than being copied
  forward from the old file.
- **Tests now exercise the shipped artifact directly** where it carries
  the thing under test. `_with_identity_assertion` and `_with_money_types`
  existed to patch capabilities into a copy before the committed file had
  them; keeping them would have meant the tests no longer proved anything
  about what actually ships.
- **The position-only control is built by *removing* anchors from the
  shipped artifact**, not by using the committed file as-is. Before this
  phase the extra_row comparison used the committed artifact as its
  position-only control, which worked only because that file happened not
  to have anchors yet. The moment it gained them, the control would have
  quietly become a second copy of the fix and the test would have gone on
  passing while testing nothing.
- **`demo_handoff.py` masks its own result file.** It writes evidence
  directly rather than through the replay CLI, so it doesn't inherit the
  CLI's masking boundary -- it has to apply the same rule itself. Its
  assertions still run against the unmasked in-memory values, so the check
  is on the real figure rather than on its mask.
- **REPORT.md cut to roughly three pages**, with the long bug narratives
  (the three password leaks, the proactive/reactive business-outcome
  interaction, the instant-vs-bounded wait) moved here and linked. They
  are worth keeping -- they are the evidence that the rules were learned
  rather than assumed -- but a design write-up that buries its design in
  war stories is harder to review, which is the same argument as trimming
  the artifact YAML.

## Post-review hardening — Phase 5: one scenario per named runtime condition

The brief names eight runtime conditions replay has to handle (Section 3.3,
plus "outright app errors" from Section 1). Four had evidence; four were
either untested or had no way to occur at all. This phase gives each one a
scenario with a real run behind it, and nothing beyond that list.

### Step 1 — the two missing faults, and making slow_load cut both ways

- **`permission_denied` answers 403, `app_error` answers 500.** The status
  codes are the point, not decoration: they are what lets replay tell the
  two apart without reading either page. A permission denial is a real
  answer to a well-formed request -- the caller needs to know the teller
  isn't entitled to that record, and no retry will change it, which is
  exactly a business outcome. A 500 means the app fell over: nothing about
  the request was wrong and the same inputs might work in a minute, but
  nothing here can decide that, so it stops. Classifying those by HTTP
  status is structural, in the same sense as Phase 1's `error_kind` -- no
  prose gets pattern-matched, so rewording either page changes nothing.
- **Both fire before the record is looked up.** An entitlement check
  happens on the way in, and an app that has crashed never reaches its
  data. It also avoids a small information leak: a restricted ID that
  doesn't exist says "access denied" rather than confirming there's no such
  record.
- **`slow_load` gains a setting instead of a second fault.** The brief
  names transient slowness (recoverable) and a slow/failed load (a hard
  failure) as different conditions, but they are the same fault at
  different magnitudes -- what separates them is whether the delay fits
  inside replay's wait budget. Two faults would have encoded that budget in
  the fake app, in two places, where it would drift from the real one in
  `resolve_with_wait`. One duration knob keeps the distinction where it
  actually lives.
- **Settings are a separate dict from faults, with their own endpoint.**
  `FaultState.as_dict()` returning all-bools is relied on by the admin page
  and by the "reset disarms everything" test; a float in that dict makes
  "arm everything" meaningless for the one entry that isn't a switch.
  Settings also persist until reset rather than firing once, because a
  duration describes a fault rather than being an occurrence of one.
- **The default dropped from 4s to 2s.** 4s was uncomfortably close to the
  5s locator budget and already past the 3s checkpoint one -- a latent
  flake that happened not to have fired. 2s is unambiguously inside both,
  which is what the recoverable case is supposed to demonstrate.
- **An unknown setting name returns 400, not a 500.** A script arming
  settings in a loop should be told which name it got wrong; the existing
  faults endpoint still raises, and is left alone rather than changed as a
  drive-by.
- **The duration test's thresholds are deliberately loose.** Windows' sleep
  and clock granularity is ~16ms, and a 0.5s sleep measures as 0.49 often
  enough to matter. What's under test is which number was used and that the
  fault fired once, not the timer's precision -- a threshold sitting exactly
  on the nominal value tests the platform's clock instead.

### Step 2 — input validation before anything runs

- **`InputSpec.pattern`, checked before the browser is constructed.** The
  brief lists "a validation error" among the runtime conditions replay must
  handle deliberately. The cheapest place to handle one is before any work
  happens: a member ID of `abc` cannot become a correct answer however well
  the rest of the run goes, so spending a Chromium launch, a login and six
  steps to discover that is pure waste. It also reports better -- left to
  run, the failure would eventually surface as "element not found" at
  whichever step happened to fall over first, which names the symptom
  instead of the cause.
- **The pattern is the caller's contract, not the app's.** It says what
  this capability accepts, which is deliberately not the same question as
  what the app would reject. An input that passes can still come back as
  `member_not_found` -- well-formed and nonexistent are different answers,
  and collapsing them would turn a business outcome into a validation
  error.
- **A rejection is returned as a `ReplayResult`, not raised.** The existing
  missing-input/missing-secret checks raise `ValueError` and exit 64, which
  is right for them: a missing parameter means the *command* was malformed
  and there is no run to report on. A malformed value is different -- it is
  an answer about this invocation, and the caller needs it in the same
  shape as every other ending, with an exit code to branch on and a result
  file that says what was expected and what arrived. Hence
  `HARD_FAILURE`/`input_invalid` and exit 1.
- **Not a business outcome, though it is "expected".** The taxonomy's job
  is telling a caller what to do. A business outcome means the app answered
  and retrying is pointless; `input_invalid` means *nothing ran* and the
  caller should fix the call and try again. Filing it under
  `BUSINESS_OUTCOME` would have told an orchestrator to give up on a
  request that a corrected input would satisfy.
- **The rejected value is masked with a shape hint** when the input is
  declared sensitive, for the same reason `format_invalid` turns it on: the
  complaint is *about* the shape of the value, and a bare `***bc` states
  the problem while withholding the evidence for it.
- **An invalid regex blames the artifact, not the caller.** It reports the
  pattern as unusable and says to rebuild, rather than reporting the
  caller's perfectly fine input as malformed -- the recorder refuses to
  build one, so reaching this means the file was tampered with.
- **The recorder refuses three contradictory contracts at build time**: a
  pattern that can't compile, a pattern the recorded run's own value fails
  (the capability would reject the value it was built from), and an example
  that doesn't satisfy its own pattern. All three are free to check when
  building and expensive to meet later.
- **`example` may not be the discovery literal, enforced rather than
  documented.** Phase 3 left `example` unfilled precisely because the
  obvious value to put there is the run's own member ID -- which is real
  data, in a committed file, and exactly what parameterization exists to
  remove. Now that the field is used, that reasoning became a check:
  `00000` is well-formed, matches the pattern, and matches no seeded
  member.
- **Schema 1.1 -> 1.2, artifact version 2 -> 3.** `pattern` is optional and
  defaults to the old behaviour, so a 1.1 artifact still loads and runs.
- **The outdated-artifact warning now names what *that* file predates.**
  Phase 4's single hardcoded sentence ("notably `must_equal`") was right
  while there was one bump to describe; with two it would have told a 1.1
  artifact it lacks something it has. A map of what each minor added keeps
  the warning specific, which was the point of it over "this is outdated".
- **The forward-compatibility test now derives the version it tries.** It
  was written as the literal `"1.2"`, which passed only until 1.2 became
  current -- then failed for a reason unrelated to what it checks. Second
  time that shape of test has broken on a bump; deriving `minor + 1` ends
  it.
- **Regenerating produced a five-line diff.** Everything but the four new
  lines and the version came back byte-identical from the same run log,
  which is a small standing check that recording stays deterministic and
  that no discovery-time literal has crept back in.

### Step 3 — the permission denial, the app error, and the slow load that doesn't finish

- **Both new conditions are classified by HTTP status, never by page
  text.** A 403 is the app answering a question it understood -- the teller
  isn't entitled to this record, the caller needs to know, and retrying
  will not change it, which is precisely a business outcome. A 5xx is the
  app failing to answer at all: nothing about the request was wrong, the
  same inputs might work later, and nothing in replay can decide that, so
  it stops. Judging that from a status line means no wording is
  pattern-matched and rewording either page changes nothing -- the same
  argument as Phase 1's `error_kind`.
- **`app_error` needed no artifact field.** The alternative was declaring
  error pages on the artifact the way business outcomes are declared, which
  would have meant every capability re-describing the same thing and a new
  schema concept for "a detected state that is a failure". The status code
  is already there, already generic, and already right for capabilities
  nobody has written yet.
- **Only a *currently displayed* 5xx counts.** `failing_document()` matches
  the recorded statuses against the URLs the page and its frames hold right
  now, so a page that failed and was navigated away from is history rather
  than a verdict on the current state.
- **Checked before business outcomes, and on a failed step as well as at
  the ending.** If the app says it fell over, that outranks pattern-matching
  its page; and a step that failed against a 500 failed *because* the app is
  down -- reported as `element_not_found` it would send someone to check a
  locator that is fine.
- **"Still loading" is distinguished from "not there" by two signals, and
  the obvious one alone was wrong.** `document.readyState` looks like the
  whole answer and isn't: while a navigation is in flight the frame still
  holds its *previous* document, which reports `complete` the entire time
  the server is thinking -- exactly our slow-load case. The load is only
  visible as an unanswered document request. readyState still earns its
  place for the parse/subresource phase after the bytes arrive, so both are
  checked.
- **Documented limit rather than a silent one:** a page that returns fast
  and fetches its data in the background reports `complete` with nothing on
  it, so that shape of slow load still reads as `element_not_found`. It
  degrades toward the old behaviour, not toward a wrong answer. Noted in
  REPORT.md Section 3.
- **A test for the *other* direction.** Classifying a timeout is only worth
  anything if a genuinely missing element still says so -- otherwise this is
  a relabelling of every failure. Both directions are asserted.
- **The failure screenshot is taken once, centrally, in `run()`'s
  `finally`.** There are about eight places a HARD_FAILURE is constructed;
  threading a screenshot through all of them would have been eight chances
  to forget one. Nothing happens between the failure and the browser
  closing, so the last moment before `close()` *is* the failing state. It is
  skipped while a native dialog is open (Part 3's render-pipeline hang) and
  can never turn a reportable failure into a crash -- a missing picture is a
  worse result, not a different one.
- **A real pre-existing bug, found while restructuring for that:
  `_resolve_ending` was called from inside a sibling `except` clause.**
  `except _SkipToEnding: result = self._resolve_ending(ctx)` -- an exception
  raised there does *not* reach the sibling `except _ReplayEnd`, so a human
  who resolved a handoff and then landed somewhere unrecognised got an
  internal control-flow exception out of `run()` instead of a HARD_FAILURE.
  Fixed by nesting, so `_resolve_ending` is always inside the try that
  catches it. Exactly the interaction this step would have hit: an app error
  after a handoff.
- **Artifact version 4, one bump for the whole regeneration.** The capture
  scripts deliberately don't bump it themselves -- three scripts attaching
  three outcomes would take one revision to 3, 4 and 5 and make the number
  depend on how many outcomes an artifact happens to have. One pass, one
  revision; the build command carries the version and the scripts preserve
  it.
- **Status-code detection is the default, not the whole answer.** Recorded
  as a limit rather than built: plenty of legacy apps answer `200 OK` and
  put "Access denied" or a stack trace in the body, because the error is
  rendered by the application rather than signalled by the transport. Those
  need per-app *text* detection declared on the artifact -- the `detect`
  locator shape `business_outcomes` already has, plus an equivalent on the
  failure side so a recognised error page can be a HARD_FAILURE rather than
  a business outcome. Building that now would mean shipping a mechanism
  with no real case behind it, since the fake app signals honestly; the
  status check is the capability-independent default it would override.
  REPORT.md Section 3.

### Step 4 — recording what the human did, and who held the wheel

- **Actions are captured from the page, not from a wrapper API.** The
  obvious design -- hand the operator an `OperatorSession.click()` /
  `.fill()` and log the calls -- works only for an operator that is code. A
  person driving the Playwright Inspector makes no Python calls at all, so
  the one mode where a real human is genuinely at the wheel would have been
  the only mode that recorded nothing. A capture-phase listener injected
  into every frame sees a person clicking in the Inspector and a handler
  calling `locator.click()` identically, because both are real events in a
  real DOM. One mechanism, both modes, and what gets recorded is what
  happened to the page rather than what the caller says it did.
- **Capture-phase listeners**, so a page handler that calls
  `stopPropagation()` can't hide an operator's click from the record.
- **A password never leaves the page.** The JS reports it as absent rather
  than sending it for Python to mask -- the Part 4 finding was that a value
  a scan can reach is a value some future consumer forgets to redact, and
  the fix there was to stop the scan returning it at all. Everything else
  is sent and masked immediately by `mask_partial`, so the masking rule
  stays in one place instead of being reimplemented in JavaScript.
- **The element description never reads an input's `value`.** A password
  field would otherwise put the secret into a description string, which is
  exactly the sort of second path Part 4's three separate leaks were about.
- **A capture failure is recorded, not swallowed.** An empty action list
  has to mean "the operator did nothing" -- so a session that couldn't
  install its listener says so, rather than looking identical to a decline.
  Instrumentation also never breaks the handoff: a person taking over a
  stuck run matters more than the record of it.
- **The engine wraps `escalate()`, so no handler knows about any of this.**
  The handoff contract is unchanged and all three implementations got
  recording without being touched -- including `InteractivePauseHandoff`,
  which can't be tested automatically and so is the one that most needed
  not to require its own code path.
- **`_Install` indirection exists because `expose_function` registers a
  name once per page.** Without it, a second escalation in the same run
  couldn't re-register and its events would have gone on arriving at the
  first escalation's record -- a silent, plausible-looking wrong answer.
- **Control spans open and close around the `escalate()` call itself.**
  The timeline is then a recording of the same fact the call stack already
  enforces ("automation touches nothing until escalate returns"), not a
  separate description of it that could drift from the behaviour.
- **A clean run still gets one span.** "Nobody took over" and "we didn't
  track it" must not serialize identically, and a single uninterrupted
  automation span says the first plainly.
- **Spans are contiguous, asserted in a test**: each starts exactly where
  the previous ended, so there is no instant the record can't say who was
  driving. A timeline with gaps would be worse than none, since it would
  look complete.
- **`escalation_path()` is shared rather than copied.** Two callers now
  need the file's name -- the handler writing the request, the engine
  appending what the operator did -- and a naming rule duplicated in both
  would split one escalation across two files the first time either
  changed. The append is best-effort: the same data also rides on the
  `ReplayResult`, so a handler that wrote its request elsewhere costs the
  record nothing.
- **Submits and Enter presses are captured too, not only clicks and
  changes.** An operator who types into a field and hits Enter never clicks
  anything, so a click-only recorder would have shown them doing nothing at
  all. A submit is also its own fact rather than a repeat of whatever
  caused it -- it can come from Enter, from a button, or from script, and it
  is the moment the form was actually committed.
- **A key name is not masked.** `mask_partial("Enter")` gives `"***er"`,
  which destroys the only information the field carries and protects
  nothing, because the value is our vocabulary rather than the operator's
  data. The JS marks those `literal` so the distinction is explicit at the
  source instead of being inferred from the event kind downstream.
- **A form is never described by its `innerText`** -- that is every label
  and value it contains flattened into one string, which would put whatever
  the operator typed into the description field, right past the masking.
  Tested.
- **The listener survives navigation**, via `add_init_script` for documents
  opened later plus a direct injection into the frames already open. Going
  somewhere else is the first thing a person taking over a stuck run tends
  to do, and a recorder that silently stopped at that exact point would be
  worse than none -- it would look like the operator stopped acting.
- **Named in REPORT.md rather than left implied: page-level recording can't
  see the browser.** Back/forward, the address bar, new tabs and native
  dialogs are chrome rather than DOM and fire no page events. A
  `framenavigated` entry still records where the operator ended up, just not
  how they got there, so the log is a faithful record of what happened *to
  the page* and an incomplete record of what the operator did. Completing it
  means capturing browser-level events (CDP transition types, plus the
  existing dialog handler feeding the same log) -- a bigger seam than this
  needs, and better stated than quietly missing.

### Step 5 — a capability whose whole point is being blocked

- **`artifacts/member-subaccount-open.yaml` exists because nothing else
  reached the risky route.** The only committed artifact was a read-only
  lookup, and the only thing exercising `*/new-subaccount/commit` was a
  synthetic artifact built in Python inside `tests/conftest.py` -- never
  written to disk, so `risky_step_approved` had nothing to replay.
- **Deterministic capture, not a second discovery run**, and the script
  says so at length rather than leaving it to be inferred. The brief
  requires *one* genuine LLM run and that one exists. This capability's
  purpose is to reach a guardrail-blocked irreversible action, which
  discovery cannot do unattended: policy refuses the commit and the model
  has no handoff path during discovery, so the run would stall at exactly
  the step the artifact needs to record.
- **What is hand-chosen is stated, not glossed.** The step order and each
  step's ranked locator candidates are written by hand. They are not
  hand-waved: every one resolves against the live page during capture under
  the same strict "exactly one match or fail" rule replay uses, so a
  candidate that doesn't really work fails the script instead of shipping.
  Everything the recorder derives for a discovered artifact is still
  derived here -- parameterization, canonicalization, descriptions, risk
  labels through the real PolicyEngine, output locators, checkpoint.
- **The flow goes through the app's own screens** (search -> View -> Open
  New Sub-Account) rather than navigating straight to the form. It crosses
  frames the way the real task does, and it gives the recorder genuine
  before/after observations to judge each step's destination from -- which
  is what produced the single `risk: risky` label on the commit, correctly
  canonicalized to `/app/member/:member_id/new-subaccount/commit`.

#### Two real leaks this capture found, both in code that already shipped

- **The password went into the artifact.** `_parameterize_value` replaces a
  literal only when the caller passes it in `secret_values`. A discovery
  run gets away with declaring only the username because `browser_tools`
  redacts the password to `{{secrets.password}}` *before* it is ever
  recorded -- so the recorder never sees the real one. A capture script
  drives the surface directly, where nothing redacts anything, and one
  missing entry put a live credential into a committed file twice: in the
  step's `value` and in the generated description ("Enter the recorded
  value 'teller123'..."). Exactly the Part 4 lesson again -- a secret
  reaches a file by more than one path, and fixing the path you are looking
  at is not the same as fixing the value.
  Fixed structurally, not just in the script: `build_artifact` now refuses
  an artifact that declares a secret no step references as
  `{{secrets.<name>}}`. Declared-but-absent is the precise signature of the
  mistake -- the flow plainly used the secret to get where it got, so if no
  step references it, it went in as a literal under some other guise.
- **`prefer_table_position` stopped preferring table positions.**
  `find_target_for_text` took the *first* element containing the value, and
  the success page announces itself in prose above the table
  ("Sub-account #4001 opened for ... (ID 10001)"). Prose has no table
  position, so the search fell straight through to a `TEXT` candidate
  pinned to `"4001"` and `"10001"` -- the value-locking bug the flag exists
  to prevent, reintroduced by search order rather than by the missing
  branch it was written for. It never bit before because the member detail
  page has no prose repeating its values. Fixed with two passes: positioned
  elements first, prose only as fallback, both directions tested.
- **Sub-account numbers start at 4001, not 1.** Not cosmetic: output
  locators are found by substring-matching the captured page, and `"1"` is
  contained in the member ID `"10001"` two rows above, so a one-digit
  number resolves to the wrong cell. Realistic numbers are also what a real
  system produces.
- **The success page gained a details table.** Without one, the only place
  the confirmed values appeared was the prose headline, and a locator built
  from that is pinned to one run's numbers. This is a change to the app's
  surface rather than to a fault, made so the capability can have real
  label-anchored outputs and -- more importantly -- an identity assertion.
  That matters more here than on the lookup: this capability *changes*
  something, and opening an account on the wrong member is not a mistake a
  later read can undo.
- **`initial_deposit`'s pattern rejects non-amounts, not amounts below the
  minimum.** The app owns the 25.00 rule and answers with its own
  validation page; encoding it in the artifact too would be the capability
  quietly asserting a business rule it doesn't own, and the two would drift.
- **The capture resets the app afterward.** It really does open an account;
  leaving it there would make the next capture record sub-account 4002.
- **Evidence for the scenario comes from `scripts/run_evidence` in step 6**,
  not a bespoke script here. Step 5's deliverable is the artifact and the
  end-to-end proof in tests; producing a folder for it twice is exactly the
  duplication the evidence rewrite is meant to remove.
- **A test-isolation bug this step introduced, kept as a note because the
  fix is a rule not a patch.** The new sub-account test asserted the
  confirmed number was 4001 and got 4002: `fake_app_server` is
  module-scoped, and an earlier test in the same file already opens a
  sub-account for the same member. A test asserting on state an earlier
  test can have written has to establish its own preconditions rather than
  inherit them -- hence `reset_app()` in conftest, called by the tests that
  depend on the seeded state. Worth recording because the failure looked
  like a fake-app numbering bug and was a test-ordering one.

#### Step 5 follow-up: fixing the match rule instead of dodging it

- **Sub-account numbers are back to 1, 2, 3.** Starting them at 4001 made
  the symptom go away without touching the cause, and left the fake app
  carrying a comment explaining a recorder bug -- the wrong file to explain
  it in, and a landmine for the next short value.
- **The value search now requires the cell's whole trimmed text to equal
  the value**, the same exactly-this-cell rule `TABLE_LABEL` already uses.
  A value *is* the entire content of the cell holding it; substring
  matching let a short one resolve to any longer one containing it, which
  is how `"1"` found the member ID `"10001"` one row above.
- **Checkpoints and business outcomes keep substring matching**, and the
  two rules are now explicitly different rather than accidentally shared.
  They are looking for opposite things: a value occupies its whole cell,
  while a checkpoint phrase is deliberately a stable *fragment* of a longer
  sentence -- `"No member found"` inside `'No member found matching
  "99999".'`. Tightening both would have broken every business outcome in
  the repo. Both directions are tested.
- **The positioned-first pass is kept even though exact matching already
  rules out the prose.** They cover different cases: exactness stops a
  value matching text that merely mentions it, while the ordering handles a
  page that legitimately shows the same value twice, once loose and once in
  a cell. Only the cell yields a content-independent locator.
- **Regenerating the lookup artifact produced a timestamp-only diff**,
  which is the check that mattered: the stricter rule changed nothing about
  an artifact whose values already occupied their own cells.

#### A limit this created, worth naming

- **Prose-only confirmation has no content-independent locator.** We gave
  the fake app a details table so the sub-account confirmation could be
  anchored by label, and real applications often don't have one -- plenty
  confirm entirely in a sentence ("Sub-account #1 opened for ..."), where
  the value exists only inside prose. For those, `find_target_for_text`
  falls back to a `TEXT` candidate pinned to that run's literal, which
  replays correctly exactly once. Handling them properly needs a different
  extraction strategy than a locator -- a capture-group pattern against the
  matched element's text ("Sub-account #(\d+) opened"), declared on the
  output -- which is a real gap rather than something the current schema
  expresses badly. Named here rather than papered over, since the fake app
  having a convenient table is a fact about the fake app.

#### The password leak: not in history

- **Checked rather than assumed.** `git log -S "teller123" -- artifacts/`
  returns nothing, and the single committed blob of
  `member-subaccount-open.yaml` greps clean; the branch was also 11 commits
  ahead of `origin/main`, so nothing had been pushed anywhere. The leaking
  version existed only in the working tree between writing it and reading
  it, and was fixed before the first `git add`. No history rewrite was
  needed -- recorded because "we checked and it was clean" and "we didn't
  look" are indistinguishable afterwards otherwise.

### Step 6 — one script, fourteen folders

- **`scripts/run_evidence.py` replaces the hand-run scripts and the folders
  they produced.** `evidence/replay-member-lookup/` and
  `evidence/handoff-ambiguous-duplicate/` became five of the fourteen
  scenarios, and `scripts/demo_handoff.py` was deleted rather than left
  beside the runner producing the same two runs a second way.
  `discovery-member-lookup/` stays: it is the required genuine LLM run, not
  a replay scenario, and nothing here re-runs it. `extra_row/` stays frozen.
- **Only the failure screenshot is committed.** The engine writes one per
  step, and fourteen scenarios' worth would be about a hundred PNGs that
  nobody opens. The per-step images go to the git-ignored scratch dir and
  the run copies out `failure.png` -- the state the run actually ended in,
  which is the one carrying information. This is also exactly what the
  brief asks for ("at least one richer signal on failure").
- **Eleven scenarios run the real CLI as a subprocess; three run
  in-process.** The split is real and is written into each folder's
  `command.txt` rather than smoothed over: the handoff scenarios need a
  *programmable* operator, and the CLI deliberately offers only human modes
  (`--handoff terminal|interactive`). Going through the CLI where possible
  means the command in the folder is literally the command that ran and the
  exit code is a real process exit status; the three in-process ones derive
  theirs from the same `EXIT_CODES` table the CLI uses, and their
  `command.txt` gives the interactive equivalent for a person who wants to
  play the operator themselves.
- **A fixed port (5055), not an ephemeral one.** The artifacts and the
  guardrail allowlist both name it, and `command.txt` has to be something
  someone can paste. The script reuses an app already listening there and
  only shuts down one it started itself.
- **The credential is parameterized in `command.txt`** as
  `$env:TELLER_PASSWORD`, with the value documented in the top-level README
  only. The real value is passed in argv to the subprocess and written down
  nowhere under `evidence/`, so the step 9 scan can be absolute with no
  exemption list.
- **"Actual" is read back off `result.json`, never taken from the
  scenario's expectation.** A table whose "actual" column is populated from
  what the runner hoped for is a table that cannot disagree with itself,
  which is the only thing it exists to do. The script exits non-zero when
  any actual differs from its expected.
- **That check earned its keep on the first full run.** `duplicate_abandon`
  reported `needs_human ambiguous_duplicate` against an expected plain
  `needs_human` -- the expectation was less precise than the behaviour, not
  the other way round. The outcome name rides along on a NEEDS_HUMAN
  because the run stopped *because of* a named business outcome and the
  caller needs to know which. Fixed the expectation.
- **`--only` merges into the existing index rather than replacing it**, so
  re-running one scenario can't silently truncate the record the README is
  generated from.

### Step 7 — a stability run that can actually fail

- **`wrong_answers` is the headline, not the outcome histogram.** The
  brief's optional stability signal is "replay N times and report
  flakiness", but a tally of outcomes would record a run that returned
  someone else's balance as a clean success -- the single worst thing this
  system can do. Every value each run hands back is compared against the
  fake app's seed template for the member actually requested, and that
  count must be 0.
- **Ground truth comes from `_TEMPLATE_MEMBERS`, not the live dict.** The
  app runs in another process, and the template is the pristine state a
  reset restores -- which is what every run starts from. Reading the live
  values would mean checking the app against itself.
- **`unexpected_outcomes` is the actual flakiness signal**, kept separate.
  Each fault declares where it should land, and a deviation is the
  interesting event. The raw histogram measures the *fault mix*, because
  the variance is injected deliberately -- reporting it as a stability
  number would be reporting the experiment's design as a result.
- **A balanced shuffled schedule, not an independent draw per run.** The
  first seeded 20-run pass produced eight `app_error`s and never fired
  `popup`, `slow_load` or `duplicate_members` at all -- three of the
  conditions the scoreboard claims to exercise never ran. Sampling with
  replacement looks more random and is worse evidence. Order and member
  stay unpredictable; coverage is guaranteed, and a test asserts it.
- **The check is tested in both directions.** "0 wrong answers" would be
  produced just as happily by a check that cannot fail, so there are tests
  for a single substituted balance, a wholesale wrong record, and an empty
  output set (a business outcome returns nothing, and nothing is not
  incorrect -- counting it would bury the real signal under every
  not-found run).
- **Mismatches are reported masked with a shape hint.** The scoreboard is
  committed; a correctness alarm has to be legible without putting the
  figures it complains about on disk.
- **The member ID is masked in the scoreboard too**, unlike in
  `command.txt`. It costs nothing here -- the seeded members differ in
  their last two digits, so `***01` and `***02` stay exactly as
  distinguishable as the full IDs -- and claiming an exemption that buys
  nothing is how exemptions accumulate.
- **Closed member 10005 is excluded.** Its detail page has no balances, so
  the checkpoint never matches and the run is a legitimate hard failure --
  about account status rather than about the fault under test, and it would
  sit in the scoreboard looking like instability.
- **Reuses `run_evidence`'s app lifecycle and fault helpers by importing
  it**, rather than copying the start/reset/arm logic into a second script.
- **"No fault" is an entry in the pool like any other**, so clean runs are
  guaranteed alongside the faults -- two of them in a 20-run pass. Worth
  being explicit about in the scoreboard's own text: a stability report
  made entirely of injected failures would let a regression that only
  breaks the happy path pass unnoticed.

### Step 8 — a table that can disagree with itself

- **The whole README is generated, not a hand-written page with a
  generated table in it.** The `Actual` and `Exit` columns are the only
  reason the document is worth reading, and they are worth reading only
  because nobody typed them. Leaving the surrounding prose hand-maintained
  would have left a half that drifts every time a scenario is added.
- **`Expected` and `Actual` are separate columns filled from separate
  sources.** Expected is what the scenario declares; actual is read back
  out of that run's own `result.json` afterwards. Collapsing them into one
  "result" column, or filling actual from the runner's intention, produces
  a table that cannot disagree with itself -- which is the only thing it is
  for.
- **A test asserts the committed README is what the generator produces.**
  "Do not edit by hand" in a comment is a request; this makes it a
  failure. A README that has drifted from its index is worse than none,
  because it reads exactly as authoritatively.
- **The brief's own list of runtime conditions is asserted, not just
  described.** A docstring claiming one scenario per named condition stops
  being true the moment someone deletes one; the test names all eight and
  fails if any loses its scenario.
- **The failure-screenshot check exempts `invalid_input` explicitly**, with
  the reason in the test: it is rejected before a browser opens, so there
  is nothing to photograph. That is the feature the scenario exists to
  demonstrate, and an unexplained exemption would look like a gap.

### Step 9 — a scan with one rule that has no exceptions

- **The credential rule is absolute; the values rule has exactly one
  exemption.** Keeping them separate is the whole design. The moment a
  password scan carries a list of files where the password is permitted, it
  stops being a check and becomes a record of exceptions, and the second
  entry is always easier to add than the first. So the commands in
  `evidence/extra_row/*.txt` and the discovery README now take the password
  from `$env:TELLER_PASSWORD`, and the literal appears nowhere under
  `evidence/` or `artifacts/`.
- **`evidence/extra_row/` stayed frozen where it matters.** Only the
  command lines changed; the three captured `.result.json` files are
  untouched. The comparison between them *is* the evidence, and re-running
  it would quietly turn three different artifacts into three copies of
  whatever the recorder does today.
- **`run_log.json` is exempt from the values rule, by exact path, with the
  reason in the test.** It is the transcript of the genuine LLM run: what
  the model saw, what it extracted, what it concluded. A masked transcript
  would not be safer evidence, it would stop being evidence. The exemption
  is narrow in three ways -- one path, one rule, and two tests guard it:
  one asserts the file still contains what it is exempted for (an exemption
  nobody checks is a hole), and one asserts it is still subject to the
  password rule.
- **The discovery README was masked, though it sits beside the exempt
  file.** It quotes the outputs as prose *about* the run; only the
  transcript itself has the argument for staying unmasked. Drawing the line
  at the file rather than the folder is what keeps the exemption meaningful.
- **Forbidden values are derived from `_TEMPLATE_MEMBERS`, not listed.**
  Adding a member to `data.py` starts guarding that member automatically; a
  hardcoded list would silently stop covering the data it was written for.
  Zero balances are skipped -- "0.00" carries no information and would
  match unrelated text.
- **The scan was verified by planting leaks.** A scan that passes proves
  nothing until it has been seen to fail: a password, a balance and a name
  were each written into a file under `evidence/` in turn, and each was
  caught. There is also a test that the file list isn't empty, since a glob
  that stops matching turns every check green.
- **Screenshots are named as out of scope, not quietly ignored.** A PNG of
  the detail page shows the balance as plainly as any JSON and no text scan
  will see it. That is a retention problem rather than a redaction one, and
  REPORT.md Section 6 now says where such files would live in production
  and when they would be deleted.

### Step 10 — proving replay needs no model by taking the model away

- **Two independent checks, because each misses what the other catches.**
  Blocking the network proves no call is *made*, and is what fails the day
  someone adds an LLM fallback to a failing step; asserting the module is
  never imported proves the dependency isn't in the replay path at all, and
  catches an import creeping in before it grows a call site. Either alone
  would leave an obvious way to be wrong.
- **Blocked at DNS, with an exception that names the violation.** A generic
  connection error is something a caller might reasonably retry;
  `AnthropicWasCalled` says what rule was broken. Only Anthropic hosts are
  blocked, so the fake app (127.0.0.1) and Playwright's local driver socket
  keep working -- which means a failure can only mean replay reached for
  the model.
- **The guard is tested too.** `test_the_guard_itself_blocks_anthropic`
  exists because every other test in the file passes trivially if the
  fixture silently does nothing -- the same reason the leak scan is
  verified by planting leaks.
- **A control test asserts discovery *does* import the SDK.** Without it,
  "replay doesn't import anthropic" would keep passing if the SDK were
  removed from the project altogether, at which point it would no longer be
  saying anything about replay.
- **The import check runs in a fresh interpreter.** By that point in a full
  session another module may well have imported `anthropic`, and asking
  `sys.modules` in-process would answer about the test session rather than
  about replay.
- **A business-outcome run is covered as well as the happy path.**
  Recognising "no such member" is exactly the sort of judgement someone
  might reach for a model to make; it comes from the artifact's own detect
  locator, and the test proves that with the model unreachable.
- **A fake `ANTHROPIC_API_KEY` is set for the duration**, so a passing run
  can't be explained by a real key happening to sit in the environment.
- **REPORT.md's claim was downgraded from "verified by grep".** It now
  describes what the tests do, because "we looked and didn't see it" and
  "we broke it and nothing noticed" are different strengths of evidence and
  the write-up should not claim the stronger one while doing the weaker.

### Closing the pass — README

- **The root README is named as the single place the password is written
  down**, and now says so explicitly, with the `$env:TELLER_PASSWORD` line
  needed to run any reproduction command under `evidence/`. The scan's
  no-exemptions rule only works if there is one sanctioned location and a
  reader can find it.
- **Both capabilities are listed, with the second's provenance up front.**
  A reader finding two artifacts should not have to open them to learn that
  one came from the LLM run and one from deterministic capture.

### Final polish

- **Named the project Ledgerhand**, with a one-line tagline and a "Proof at
  a glance" table linking straight to the evidence. A reviewer meeting the
  repo cold should be able to see what was demonstrated before deciding how
  much of it to read.
- **Both shells, everywhere.** The commands were PowerShell-only because
  that is where they were developed and verified; CI runs Linux, so leaving
  them that way would have made the documented path untestable on the
  platform that actually tests it.
- **CI sets no `ANTHROPIC_API_KEY`, on purpose.** Every test must pass
  without one -- discovery is the only part that needs the model, and
  `tests/test_replay_offline.py` exists to prove replay does not. A CI job
  with a key would quietly stop testing that.
- **REPORT.md is 2,556 words against a ~2,000 target, and that overage is
  a deliberate choice rather than an oversight.** Four passes of
  compression took it from 2,802 without removing a single decision;
  getting under 2,000 from here means deleting content, and the cheapest
  content to delete is the "Limits" passages in Sections 3, 5 and 6 -- what
  status-code detection misses on a 200-with-error-body app, what the load
  check can't see on a client-rendered page, what page-level recording
  can't observe about the browser. Those are the honest-limitation
  statements, which are decisions in their own right and the part a
  reviewer is least able to reconstruct. Kept, and the number reported
  rather than the constraint silently missed.
- **Confirmed the assignment PDF is absent** from the working tree and from
  every commit reachable in the repo (`git log --diff-filter=A`, plus an
  object-name sweep across all refs).
