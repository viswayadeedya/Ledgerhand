# Design write-up

## 1. Architecture

Single Python process, synchronous, no queues or services -- the brief
explicitly discourages building scaling infrastructure prematurely, and
nothing here needs it yet. The system is layered so each part only depends
on the ones "below" it:

```
guardrails  ->  surface  ->  agent (discovery)
                   |             |
                   v             v
              (shared: core.Action/Target/LocatorCandidate)
                   |             |
                   v             v
              replay  <-----  artifacts (recorder)
                   |
                   v
               handoff
```

`cua.core.models` holds the one shared vocabulary (`Action`, `Target`,
`LocatorCandidate`) that guardrails, the surface, the recorder, and replay
all use directly, rather than each layer inventing its own. An artifact's
`steps` field is literally `list[Action]` -- the same type the discovery
loop produces and `PolicyEngine.evaluate()` checks. This is what makes the
artifact "decoupled from the raw transcript" structural rather than a
promise: it cannot carry tool names or raw model output, because `Action`
never had any.

**Discovery uses `browser_toolset_20260801`, not the generic OS-level
`computer_toolset_20260801`.** The browser toolset gives the model an
accessibility-tree-style `read_page`/`find` (ref-based element references)
alongside coordinate fallback, and is documented as designed to wrap an
existing Playwright-controlled page. That's a much closer match to "bias
toward an approach that survives no clean DOM" than raw screenshot+
coordinate clicking, and it let discovery reuse Part 3's locator-ranking
resolver directly instead of a parallel coordinate-only path. Refs are
owned by our own `RefRegistry`, mapped to the same ranked `LocatorCandidate`
lists a coordinate click resolves to via DOM hit-testing -- so whichever way
the model acts, what gets recorded is always a real, robust locator, never
a raw coordinate.

**The fake target app** (`src/cua/fake_app/`, FastAPI) intentionally
reproduces the environment described in the brief: an iframe-based
frameset (real `<frameset>` support in modern Chromium was judged too
fragile to depend on; iframes create the identical "must target a named
frame" problem safely), table-based markup, and a deliberately
*inconsistent* locator surface -- some controls have proper labels, the
login/search fields have none at all -- so the locator-ranking strategy has
real fallback cases to exercise, not a toy.

**Trade-off accepted**: no persistence layer, no multi-process
orchestration, no message broker. A real deployment would want a
capability store and a queue for replay requests; this project's scope is
the abstractions those would sit on top of, not the infrastructure itself
(Section 3.7 explicitly separates the two).

## 2. Artifact schema

```
CapabilityArtifact
  id, version, title, description
  target_domain, entry_url
  inputs: [InputSpec]              -- business params (e.g. member_id)
  secrets: [SecretSpec]            -- declared, never given a value here
  outputs: [OutputSpec]            -- each has its OWN locator (Target)
  steps: [Action]                  -- ordered, replayable actions
  checkpoint: Target               -- asserted success condition
  business_outcomes: [BusinessOutcomeSpec]  -- named non-success endings
  provenance                       -- when/which model, source run log
```

Stored as reviewable YAML (matching `guardrails/policy.yaml`'s precedent):
a human should be able to open the file and understand what the capability
does, needs, and returns without reading code.

**Steps carry ranked locator candidates**, not single selectors:
`role > label > text > table_position > css`. Replay tries each in order
and requires exactly one match -- an ambiguous match is a hard failure to
surface, never a guess. This is the artifact's answer to "how each target
element is identified, with reasoning about robustness": the ranking order
*is* the reasoning.

**Outputs get a locator, not a memorized value.** The first version of the
recorder built an output's locator by matching its literal discovery-time
text ("$2340.18") -- which meant replaying the identical artifact against a
*different* member failed outright, because that exact string only exists
for the member it was recorded against. Fixed by preferring a
`TABLE_POSITION` candidate (row/col, content-independent) for outputs,
falling back to `TEXT` only when no table position exists. Verified by
replaying the same artifact against two different members and getting two
different, correct, live-read balances back.

**Parameterization is explicit, not inferred.** The recorder doesn't guess
which literal values are "parameters" -- the caller declares
`inputs={"member_id": "10001"}` and `secret_values={"username": "teller1"}`
(known from the same run), and any step value that exactly matches becomes
`{{inputs.member_id}}` / `{{secrets.username}}`. A verified password value
is redacted directly to the literal `{{secrets.password}}` placeholder at
record time (not a generic marker), so the recorder needs no special case
for it at all.

**Business outcomes** are attached after the fact, from a real captured
page state (`add_business_outcome`), not guessed from memory of what a
template says. `member-savings-lookup.yaml` has two: `member_not_found`
(a plain answer) and `ambiguous_duplicate` (`requires_human: true` -- two
conflicting records is exactly the kind of thing automation shouldn't
silently pick between).

## 3. Determinism & error handling

Replay never imports the Anthropic SDK at all -- verified by grep, not just
asserted, and further verified by running replay with a deliberately
invalid API key. Everything it does comes from `artifact.steps`, already on
disk before the run starts.

**Five-way outcome taxonomy**: `SUCCESS`, `RECOVERED` (succeeded, but only
after handling a hiccup), `BUSINESS_OUTCOME` (a named, legitimate
non-success answer), `NEEDS_HUMAN` (a policy block or an outcome marked
`requires_human`), `HARD_FAILURE` (unrecognized state; stop with full
detail: step index, what was expected, what was observed).

**Recoverable conditions are detected generically**, not per-artifact: a
dialog appearing (checked both when a step fails *and* as a side effect of
one succeeding -- a real bug, since a page's `onload` handler can fire a
popup *after* a click already reported success) and an unexpected redirect
back to the entry URL (session expiry -- recovered by replaying the exact
step prefix that already worked once in this run, then retrying, bounded
to one attempt).

**Business outcomes are checked both proactively and reactively.** A step
can fail to resolve because the flow diverged onto a known outcome (no
"View" link exists on a not-found page) -- checked reactively, on failure.
But a *robust* locator's own fallback chain can silently succeed straight
through an outcome that should have stopped the run: `TABLE_POSITION`
resolves by position, not content, so "click the first result" worked even
when the search had legitimately come back ambiguous. Fixed by also
checking business outcomes before every step (a single, instant, non-
waiting check, so it costs nothing on the normal path). This was a genuine
interaction between two features built for different reasons -- locator
robustness (Part 3) and outcome safety (Part 6) -- that only surfaced once
both were exercised together against a real ambiguous state.

**Waiting is bounded and purposeful, not blind retries.** Locator
resolution has two modes: an instant, non-waiting check (`resolve()`,
"is this here right now") for the strict single-match semantics tests and
the recorder depend on, and a bounded-poll variant (`resolve_with_wait()`,
default 3-5s) for checkpoint/output resolution and live step execution,
where a cross-frame navigation genuinely needs real wall-clock time to
finish. Using the instant check for the wrong case (right after a
navigation) produced a real false failure in testing; using the patient
one for the wrong case (every step, unconditionally) would have made every
replay run seconds slower for no reason.

**UI drift** (secondary to runtime errors, per the brief): the ranked
fallback chain is the main defense -- a renamed CSS class or an added
table column doesn't break a step whose primary candidate is `role`+`name`.
A genuinely restructured page (a control removed, a frame renamed) is not
self-healing; it surfaces as a `HARD_FAILURE` with exactly which candidate
failed and why, which is the signal a human would use to decide whether to
re-run discovery.

## 4. Heterogeneity & multi-tenant

**Surface abstraction**: `Surface` is a two-method interface
(`observe() -> Observation`, `act(Action) -> ActionResult`); nothing above
it -- the discovery loop, the recorder, replay -- knows or cares that
`PlaywrightSurface` is a browser. A legacy web app needs no new
abstraction at all (it's what this project targets). A desktop app would
implement the same interface against OS accessibility APIs (Windows UI
Automation, macOS Accessibility) instead of a DOM: `observe()` would walk
the accessibility tree instead of scanning HTML, `act()` would dispatch
native control invocations instead of Playwright clicks. The locator
vocabulary already anticipates this -- `role`+`name` is literally how a
screen reader or UI Automation client identifies a control on desktop too;
only `table_position`/`css` are web-specific and would need a
desktop-appropriate substitute (e.g., control index within a parent).

**Multi-tenant reuse**: the artifact schema already separates *what to do*
(steps' locator candidates) from *where* (`target_domain`, `entry_url`).
An artifact recorded against one tenant's instance of a shared vendor
product can be replayed against another tenant's instance by swapping only
those two fields and any tenant-specific business-outcome text -- proven
internally by the test suite's retargeting helper, which does exactly this
to run the same committed artifact against an isolated test instance on a
different port. For tenants with real customization (extra branding,
reordered fields), the natural extension is a small per-tenant *override*
layer on top of a base artifact -- additional or substituted locator
candidates, an alternate checkpoint phrase -- rather than re-recording from
scratch, since the ranked-candidate structure already supports "try this
first, fall back to the base artifact's candidates."

**Drift detection**: replay's own `HARD_FAILURE` (which candidate failed,
what was expected vs. observed) is already the raw signal. At scale, the
natural next step (not built here, per the brief's own "don't build scaling
infrastructure" guidance) is tracking failure rate per `(artifact_id,
tenant)` over time -- a spike specific to one tenant flags a
tenant-specific UI change worth reviewing before other tenants replay the
same now-broken artifact.

## 5. Escalation & handoff

**Detecting "stuck"**: `PolicyEngine` blocking a risky/irreversible action,
or a business outcome explicitly marked `requires_human=True` (e.g.
`ambiguous_duplicate` -- picking the wrong bank record is exactly the kind
of guess automation shouldn't make).

**Taking control of the live session**: `HandoffHandler.escalate(request,
surface)` receives the *exact* `PlaywrightSurface` object the run was
already using -- not a fresh one. Automation makes no further calls to it
until `escalate()` returns; control is concretely wherever that call is
executing. Three implementations, matched to the brief's own scope note
(a full co-browsing console is out of scope; mock the UI, keep the
mechanism real):
- `InteractivePauseHandoff` -- the real, hands-on mechanism. Uses
  Playwright's own `page.pause()`, which opens the Playwright Inspector
  against the live browser for a person to click/type/navigate in
  directly, resuming the instant they click Resume. Needs a headed session
  and a person present, so it can't be driven by an automated test.
- `MockOperatorHandoff` -- a programmable callback, still handed the real
  surface, so it can act on it exactly as a human would (a real click, not
  a stub). This is what proves the control-transfer model in tests and in
  committed evidence without requiring a person physically present.
- `TerminalOperatorHandoff` -- a real person, a real decision, at a
  terminal instead of a browser console. Only the "look at the live page"
  part is mocked (a screenshot path is printed rather than embedded);
  decision-making and control transfer are not.

**Handing control back**: three decisions an operator can make.
`APPROVE_AND_RETRY` (automation retries the exact blocked step, now
approved). `MANUAL_RESOLVED` (the human already acted live; skip the rest
of the scripted steps -- the old recipe's assumptions about page structure
may no longer hold -- and go straight to checking whether the checkpoint or
a business outcome now matches). `ABANDON`. Escalation is bounded to one
attempt per named outcome per run, so a declined or unfixed state can't
loop.

Verified end to end, not just designed: a test (and matching committed
evidence) has the mock operator click a real "View" link on the actual live
session, then confirms replay resumes and reads the *correct* member's real
balance afterward -- proof it's the same session, not a fresh one.

## 6. Safety

**Allowlist, enforced outside the model.** `guardrails/policy.yaml`
declares permitted domains, route globs, and action types; `PolicyEngine.
evaluate()` checks every action -- discovery's and replay's alike -- against
it. Neither the LLM's reasoning nor a step's own success is ever
consulted; the check only ever sees a typed `Action`.

**Resolve, predict, check, then act -- never the reverse.** For a click,
the surface resolves the target element and reads its destination (an
`<a>`'s `href`, or the enclosing `<form>`'s `action`/`method`) *before*
clicking, builds the real destination URL, and only then checks policy. A
blocked action never reaches the browser at all.

**Risky/irreversible actions are blocked by default**, not merely flagged.
Routes matching `risky_routes` (currently the sub-account commit endpoint)
require an explicit `human_approved=True` -- the conservative end of what
the brief allows, chosen because this stands in for a money-moving action
in a banking context.

**Redaction, and the real story behind it.** A verified password value
leaked into logs/artifacts three separate times during development, at
three separate layers, each requiring its own fix: the recorded tool
input, a *separate* DOM re-scan (`Observation.elements`) that reads a
password input's live `.value` regardless of visual masking, and the
internal `Action` object that needs the real value to actually perform the
fill. Each is now fixed at its own layer (the DOM scan masks at the
source; the recorded copies are built *after* execution from a redacted
copy, never the object used to act) and covered by a regression test that
reproduces the original leak.

**Limits, stated plainly.** Redaction is key-name and pattern based, not
exhaustive -- a secret-shaped field that isn't specifically
`type="password"` gets a generic marker, not a guaranteed-correct one, and
would need a human's attention when turning that run into an artifact.
The allowlist is domain/route-based, not content-based -- a route rename on
the target app requires a policy update, not something the system detects
on its own. There is no secrets-vault integration; credentials are passed
in at call time and held only in memory for the duration of a run. No
rate-limiting or anomaly detection beyond the allowlist itself.

## 7. Cuts

- **Desktop/multi-tenant implementation** -- designed for (Section 4
  above), not built, per the brief's own explicit scope note.
- **Richer output/checkpoint locators.** Outputs and checkpoints get one
  candidate (position or text) built from the lightweight observation
  scan, not the full ranked role/label/table-position/CSS chain
  interacted-with elements get from the richer descriptor scan. A more
  complete version would re-run that fuller scan at recording time.
- **Business outcomes are attached via deterministic exploration
  (a small script driving the artifact's own recorded prefix against a
  known-bad input), not re-discovered by the LLM each time.** The one
  *required* genuine LLM run is discovery of the happy path; enriching an
  artifact with already-known failure modes afterward mirrors how an
  engineer would actually build out a runbook's edge cases, and was a
  deliberate choice to spend the LLM-run requirement where it matters most.
- **Static type-checking (mypy) as a CI gate.** The codebase uses type
  hints and Pydantic models consistently by convention; a formal
  strict-mode pass this late would have been schedule risk for a
  lower-weighted criterion.
- **No confidence/approval workflow** (draft -> approved gating unattended
  replay) or **multi-run stability scoring** -- both named as stretch goals
  in the brief; natural next steps once more than one or two artifacts
  exist to compare.
- **No code-generation from an artifact** (e.g. emitting a Playwright test
  file) -- also a stretch goal, not attempted.
- **A full co-browsing operator console** -- explicitly out of scope per
  the brief; used Playwright's own built-in Inspector for the real
  hands-on case instead of building one.

**What's next, in priority order**: (1) the richer per-candidate output
locators, since that's the most direct correctness improvement for a
wider range of real pages; (2) per-tenant artifact overrides plus basic
failure-rate drift tracking, since that's the most direct path toward the
multi-tenant story actually paying off at scale; (3) a confidence/approval
gate, once there's a real population of artifacts and replay history to
score.
