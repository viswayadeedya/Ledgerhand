# Design write-up

Distilled; full "chose X over Y because Z" detail in
[`DECISIONS.md`](DECISIONS.md).

## 1. Architecture

Single synchronous process, no queues or services — the brief discourages
premature scaling infrastructure. Layered, each part depending only on
those below it:

```
guardrails -> surface -> agent (discovery)
                 |            |
                 v            v
          (shared: core.Action / Target / LocatorCandidate)
                 |            |
                 v            v
            replay  <----  artifacts (recorder)
                 |
                 v
              handoff
```

`cua.core.models` is the one shared vocabulary. An artifact's `steps` are
`Action`s — what discovery produces and `PolicyEngine` checks — so
"decoupled from the transcript" is structural: an artifact *cannot* carry
tool names or model output, because `Action` never had any.

| Choice | Reason |
|---|---|
| `browser_toolset_20260801` over `computer_toolset_20260801` | Accessibility-tree `read_page`/`find`, built to wrap a Playwright page; nearer "survives no clean DOM" than screenshot-and-click, and reuses replay's own resolver |
| Refs and coordinate clicks both resolve to ranked `LocatorCandidate`s | What's recorded is always a real locator, never a coordinate |
| Self-hosted fake app (FastAPI) | The brief's environment: iframes, table markup, *inconsistent* labelling — real fallback cases for ranking |
| Seven fire-once fault switches | Every failure mode below can be triggered, not just described |
| No persistence, orchestration or broker | Scope here is the abstractions a capability store and replay queue would sit on |

## 2. Artifact schema

```
CapabilityArtifact                schema_version: 1.1
  id, version, title, description
  target_domain, entry_url
  inputs:   [InputSpec]           -- business params; `sensitive` marks ones to mask
  secrets:  [SecretSpec]          -- declared, never given a value here
  outputs:  [OutputSpec]          -- own locator + `type` + `must_equal` + `sensitive`
  steps:    [ArtifactStep]        -- an Action plus `description` and `risk`
  checkpoint: Target              -- asserted success condition
  business_outcomes: [...]        -- named non-success endings
  provenance                      -- when/which model, source run log
```

YAML with defaults omitted: a human approves this before it runs
unattended, and `role: null` forty times over stops people reading
carefully. **Locators are ranked candidates**, `role > label > text >
table_label > table_position > css`, requiring exactly one match —
ambiguity is a hard failure, never a guess, and the ranking *is* the
robustness reasoning.

Three layers keep an output honest, each catching what the last misses; a
failure in any returns **no outputs at all**.
[`evidence/extra_row/`](evidence/extra_row/) shows all three on one page.

| Layer | Catches | Its blind spot |
|---|---|---|
| `table_label` — the row whose label reads exactly "Savings Balance" | An inserted row shifting values down | A renamed label; `table_position` stays as fallback, since the two fail oppositely |
| `must_equal: "{{inputs.member_id}}"` | The *wrong record* — every member's page says "Savings Balance" | Needs the page to echo the input |
| `type: money` | A resolved value that isn't what it claims — a date where a balance belongs | Shape isn't identity; a wrong member's balance is still money |

| Also in the schema | Reason |
|---|---|
| Values and URLs parameterized, risk labels canonicalized (`/app/member/:member_id`) | `/app/member/10001` is a fact about one run, not the capability. One test asserts no input literal survives anywhere, since the ways one creeps back aren't enumerable — four so far |
| Per-step `description` from the locator that resolved | Reviewable without decoding selectors |
| Per-step `risk` from where the step *went* (page diffed before/after) | A selector says nothing about its destination. Never captured = `unverified`, not assumed safe. Review metadata; replay re-evaluates live |
| `1.0` loads with a warning; a newer minor is refused | The fields likeliest to be new are *checks*; dropping one silently means running without it, then reporting success |

## 3. Determinism & error handling

Replay never imports the Anthropic SDK — verified by grep and by running
with a deliberately invalid key. Everything comes from `artifact.steps`, on
disk before the run starts.

| Outcome | Meaning | Exit |
|---|---|---|
| `SUCCESS` / `RECOVERED` | Reached the checkpoint; `RECOVERED` needed a retry | `0` |
| `HARD_FAILURE` | Unrecognized state; stop with full detail | `1` |
| `BUSINESS_OUTCOME` | A named, legitimate non-success answer | `2` |
| `NEEDS_HUMAN` | Policy block, or outcome marked `requires_human` | `3` |
| — | Bad invocation, kept off `2` | `64` |

Exit codes let a caller branch without parsing stdout — retrying on any
nonzero would retry "no such member" forever. Each `HARD_FAILURE` carries a
stable `reason_code` (`identity_mismatch`, `format_invalid`,
`element_not_found`, `timeout`, …), so failures are triaged by kind, not
grepped for: `element_not_found` is drift, `identity_mismatch` a
correctness emergency.

| Behaviour | Reason |
|---|---|
| Resolver separates "matched nothing" from "ambiguous: matched 2" | Identical symptoms, opposite fixes — page changed vs. locator no longer unique |
| Recovery is generic, not per-artifact: dialogs, session expiry | Dialogs checked when a step fails *and* after one succeeds (a popup can fire post-click); expiry replays the prefix that already worked, bounded to one attempt |
| Business outcomes checked proactively *and* reactively | A robust locator's fallback chain could otherwise succeed straight *through* an outcome that should have stopped the run |
| Waiting bounded two ways: instant check, bounded poll | Instant where strict single-match semantics are needed; poll where cross-frame navigation needs wall-clock time. Both were bugs first (DECISIONS.md, Part 6) |
| UI drift: ranked fallback, then loud failure | Secondary to runtime errors per the brief. A restructured page isn't self-healing; `HARD_FAILURE` names the failing candidate — the signal to re-run discovery |
| Permission denial vs. app error judged by HTTP status, not page text | A 403 is the app answering a question it understood (a business outcome); a 5xx is it failing to answer (`app_error`). Structural, so rewording either page changes nothing |
| "Still loading" separated from "not there" before reporting a locator failure | Both arrive as "no element matched" and need opposite responses. An in-flight document request catches the server still thinking; `document.readyState` catches the parse phase after |

**Two limits worth naming.**

*Status codes aren't universal.* Judging denials and app errors from the
status line is right here and would not be sufficient in production: plenty
of legacy apps answer `200 OK` and put "Access denied" or a stack trace in
the page body, because the error is rendered by the application rather than
signalled by the transport. Those would need per-app *text* detection
declared in the artifact — the same `detect` locator shape
`business_outcomes` already uses, plus an equivalent for the failure side
so a recognised error page can be a `HARD_FAILURE` rather than a business
outcome. The status check would stay as the free, capability-independent
default, with declared text as the per-app override. Not built here: the
fake app signals honestly, so building it would mean shipping a mechanism
with no real case behind it.

*The load check reads the document's progress.* A page that returns quickly
and then fetches its data in the background reports `readyState: complete`
with nothing on it yet, so a slow load of that shape is still reported as
`element_not_found` rather than `timeout`. That degrades toward the old
behaviour rather than toward a wrong answer, but a heavily client-rendered
surface would need a second signal (a pending-XHR count, or an app-specific
"ready" marker) for it to stay useful.

## 4. Heterogeneity & multi-tenant

**Surface abstraction**: two methods (`observe()`, `act()`); nothing above
knows `PlaywrightSurface` is a browser. A desktop app implements the same
interface against OS accessibility APIs — `observe()` walks the
accessibility tree, `act()` dispatches native invocations. `role`+`name` is
how UI Automation names a control too; only `table_*`/`css` are web-specific.

**Multi-tenant reuse**: the schema separates *what to do* (candidates) from
*where* (`target_domain`, `entry_url`), so swapping those two replays the
artifact elsewhere — proven by the suite's retargeting helper. Label
anchoring makes it likelier to hold: tenant variation is usually extra rows
and reordered fields, not renamed labels. Real customization wants a
per-tenant *override* layer, which the ranked structure already supports.

**Drift detection**: `HARD_FAILURE` + `reason_code` is the raw signal. At
scale — not built, per the brief's scope note — track failure rate per
`(artifact_id, tenant, reason_code)`: an `element_not_found` spike for one
tenant flags a UI change before others hit it; any `identity_mismatch` is a
correctness alarm.

## 5. Escalation & handoff

**Detecting "stuck"**: a policy block, or an outcome marked
`requires_human` (`ambiguous_duplicate` — picking the wrong bank record is
the guess automation shouldn't make).

**Taking control**: `escalate(request, surface)` receives the *exact*
`PlaywrightSurface` the run was using, and automation makes no further
calls until it returns — control is concretely wherever that call is
executing. Three implementations, per the brief's "mock the UI, keep the
mechanism real":

| Implementation | What's real |
|---|---|
| `InteractivePauseHandoff` | Everything — Playwright's Inspector on the live browser. Needs a person, so no test drives it |
| `MockOperatorHandoff` | A callback handed the same live surface; clicks real elements. Proves the model unattended, in tests and evidence |
| `TerminalOperatorHandoff` | A real person, a real decision; only "look at the page" is mocked |

**Handing back**: `APPROVE_AND_RETRY`; `MANUAL_RESOLVED` (the human acted,
so skip the remaining scripted steps — the old recipe's assumptions may no
longer hold — and go straight to the checkpoint); `ABANDON`. One escalation
per named outcome per run, so an unfixed state can't loop. Verified end to
end: a test and matching evidence have the operator click a real "View"
link on the live session, then replay resumes and reads the *correct*
member's balance.

**Who was in control, and what they did.** Every run carries a
`control_timeline` of contiguous spans (`automation` → `human` →
`automation`), each with a holder, a reason and both endpoints. A run
nobody touched is one uninterrupted automation span — "nobody took over"
and "we didn't track it" have to look different. The spans open and close
around the `escalate()` call itself, so the timeline records the same fact
the call stack already enforces rather than describing it separately.

The operator's actions are captured from the **page**, not from a wrapper
API: a capture-phase listener injected into every frame reports clicks and
field changes back through an exposed binding. That choice is what makes
one mechanism cover both modes — a wrapper would have recorded every
handler that is code and recorded nothing for `InteractivePauseHandoff`,
the one mode where a real person is genuinely at the wheel. Values are
masked on the way in (`***02`), and a password is never sent out of the
page at all, following the Part 4 rule that a value a scan can reach is a
value someone eventually forgets to redact. A capture failure is recorded
explicitly, so an empty action list always means "they did nothing".
Clicks, field changes, form submits and Enter presses are all captured — an
operator who types and hits Enter never clicks anything, so clicks alone
would have shown them doing nothing — and the listener survives the
operator navigating, which is the first thing a person taking over a stuck
run usually does.

**What page-level recording cannot see.** It observes the *document*, so
anything the operator does to the browser rather than to the page is
invisible to it: back/forward navigation, typing a URL into the address
bar, opening a new tab, and native dialogs (`confirm`/`alert`, file
pickers, basic-auth prompts), which are chrome rather than DOM and fire no
page events. Some of that is partly recoverable — a `framenavigated`
listener catches the *result* of a back button or an address-bar jump as a
`navigate` entry, so the record shows where they ended up but not how they
got there — and a native dialog is at least visible to Playwright's own
`dialog` event, which the surface already tracks for other reasons. The
honest summary is that the action log is a faithful record of what happened
*to the page* and an incomplete record of what the operator did. For an
audit trail that has to be complete, the browser-level events would need
capturing too (CDP `Page.frameNavigated` with a transition type, plus the
dialog handler feeding the same log), which is a bigger seam than this
project needs.

## 6. Safety

| Control | Reason |
|---|---|
| Allowlist (domains, routes, action types) enforced outside the model | `PolicyEngine` sees only a typed `Action` — never the LLM's reasoning, never whether a step succeeded. Discovery and replay alike |
| Resolve, predict, check, then act — never the reverse | A click's destination (`href`, form `action`) is checked *before* clicking, so a blocked action never reaches the browser |
| `risky_routes` blocked by default, not flagged | Needs explicit `human_approved` — the conservative end of the brief, since it stands in for a money-moving action |
| Redaction fixed at each layer it leaked from | A password leaked three times, at three layers (DECISIONS.md, Part 4); each fix has a regression test reproducing the leak |
| Sensitive values masked with a shape hint: `***18 [shape: money]` | The shape keeps the record useful — a date in a money field stays visible without the figure. The caller gets the value intact; masking applies where things are *written down* |
| `--show-sensitive` affects the terminal only | Result files and evidence are masked with no flag: they outlive the run and are read by people who weren't part of it |
| A test scans every committed file under `/evidence` and `/artifacts` | The credential rule is absolute, with no exemption list — a scan carrying a list of files where the password is allowed has stopped being a check. Forbidden values are derived from the fake app's seed data, so the scan can't drift from what it protects. Verified by planting each kind of leak and confirming it fails |

**Discovery transcripts and screenshots are a retention problem, not a
redaction one.** A discovery run log records what the model actually saw —
the page text it read, the values it extracted — and a screenshot shows a
balance as plainly as any JSON, which no text scan will ever catch.
Masking them doesn't make them safer evidence; it makes them stop being
evidence, since what they exist to prove is that a model read a real page
and reached a real answer. In production they would live in
access-controlled, short-retention storage, never in a repository, and be
deleted once the artifact they produced is approved — the artifact is the
durable object, and it never holds those values either way. Here, one file
(`evidence/discovery-member-lookup/run_log.json`) is exempted from the
values rule by exact path, with that reasoning written into the test; the
credential rule still applies to it.

**Limits, stated plainly:**

| Limit | Consequence |
|---|---|
| Redaction is key-name and pattern based | Not exhaustive; an unanticipated secret-shaped field isn't caught |
| Sensitivity is declared per artifact | An output nobody marked stays unmasked — a human judgement the system doesn't second-guess |
| Screenshots under `--evidence-dir` are not masked | They're pixels; pretending otherwise would be worse than saying so |
| Allowlist is route-based, not content-based | A route rename needs a policy update |
| No secrets vault | Credentials passed at call time, held in memory for the run only |
| No rate-limiting or anomaly detection | Beyond the allowlist, nothing |

## 7. Cuts

| Cut | Reason |
|---|---|
| Desktop / multi-tenant implementation | Designed for (Section 4), not built, per the brief's scope note |
| Richer *checkpoint* locators | Outputs now get a ranked chain; that gap is closed. Checkpoints still get one `text` candidate, and want the fuller descriptor scan at record time |
| Business outcomes by deterministic exploration, not LLM re-discovery | The required LLM run is the happy path; adding known failure modes after is how an engineer builds a runbook's edge cases |
| mypy as a CI gate | Types and Pydantic models are used by convention; a strict pass this late was schedule risk for a lower-weighted criterion |
| Confidence/approval workflow, stability scoring, code-gen from artifacts | Named stretch goals; natural once more than one or two artifacts exist to compare |
| Full co-browsing console | Out of scope per the brief; used Playwright's Inspector instead |

**What's next**: (1) per-tenant overrides plus drift tracking keyed on
`(artifact_id, tenant, reason_code)` — the reason codes are what make that
tracking meaningful; (2) the ranked-candidate treatment for checkpoints
that outputs now get; (3) a confidence/approval gate, once there's a real
population of artifacts to score.
