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
| `browser_toolset_20260801` over `computer_toolset_20260801` | Accessibility-tree `read_page`/`find`, built to wrap a Playwright page; nearer "survives no clean DOM" than screenshot-and-click, and reuses replay's resolver |
| Refs and coordinate clicks both resolve to ranked `LocatorCandidate`s | What's recorded is always a real locator, never a coordinate |
| Self-hosted fake app (FastAPI) | The brief's environment: iframes, table markup, *inconsistent* labelling — real fallback cases for ranking |
| Nine fire-once fault switches, plus a slow-load duration | Every failure mode below can be triggered, not just described |
| No persistence, orchestration or broker | The scope here is the abstractions a capability store and replay queue would sit on |

## 2. Artifact schema

```
CapabilityArtifact                schema_version: 1.2
  id, version, title, description; target_domain, entry_url
  inputs:   [InputSpec]    -- `pattern`, `example`, `sensitive`
  secrets:  [SecretSpec]   -- declared, never given a value here
  outputs:  [OutputSpec]   -- own locator + `type` + `must_equal` + `sensitive`
  steps:    [ArtifactStep] -- an Action plus `description` and `risk`
  checkpoint: Target       -- asserted success condition
  business_outcomes: [...] -- named non-success endings
  provenance               -- when/which model, source run log
```

YAML with defaults omitted: a human approves this before it runs
unattended, and `role: null` forty times over stops people reading
carefully. **Locators are ranked candidates**, `role > label > text >
table_label > table_position > css`, requiring exactly one match —
ambiguity is a hard failure, never a guess, and the ranking *is* the
robustness reasoning. `pattern` rejects a malformed input *before a browser
opens*, and is the caller's contract rather than the app's: an input that
passes can still come back `member_not_found`.

Three layers keep an output honest, each catching what the last misses; a
failure in any returns **no outputs at all**.
[`evidence/extra_row/`](evidence/extra_row/) shows all three on one page.

| Layer | Catches | Blind spot |
|---|---|---|
| `table_label` — the row labelled exactly "Savings Balance" | An inserted row shifting values down | A renamed label; `table_position` stays as fallback, since the two fail oppositely |
| `must_equal: "{{inputs.member_id}}"` | The *wrong record* — every member's page says "Savings Balance" | Needs the page to echo the input |
| `type: money` | A value that isn't what it claims — a date where a balance belongs | Shape isn't identity; a wrong member's balance is still money |

| Also in the schema | Reason |
|---|---|
| Values and URLs parameterized, risk labels canonicalized (`/app/member/:member_id`) | `/app/member/10001` is a fact about one run, not the capability. A test asserts no input literal survives anywhere — the ways one creeps back aren't enumerable, four so far |
| Per-step `description` from the locator that resolved | Reviewable without decoding selectors |
| Per-step `risk` from where the step *went* (page diffed before/after) | A selector says nothing about its destination. Never captured = `unverified`, not assumed safe; replay re-evaluates live regardless |
| An older minor loads with a warning naming what it predates; a newer one is refused | The fields likeliest to be new are *checks*; dropping one silently means running without it, then reporting success |

## 3. Determinism & error handling

Replay never calls the LLM, proved by taking it away: one test makes
`api.anthropic.com` unresolvable at the socket layer and runs a full real
replay; another asserts, in a fresh interpreter, that importing the replay
CLI pulls in no `anthropic` module, with a control test asserting discovery
*does* — without which the first would pass if the SDK were deleted.
Everything comes from `artifact.steps`, on disk before the run starts.

| Outcome | Meaning | Exit |
|---|---|---|
| `SUCCESS` / `RECOVERED` | Reached the checkpoint; `RECOVERED` needed a retry | `0` |
| `HARD_FAILURE` | Unrecognized state; stop with full detail | `1` |
| `BUSINESS_OUTCOME` | A named, legitimate non-success answer | `2` |
| `NEEDS_HUMAN` | Policy block, or outcome marked `requires_human` | `3` |
| — | Bad invocation, kept off `2` | `64` |

Callers branch on the code without parsing stdout — retrying on any nonzero
would retry "no such member" forever. Each failure carries a stable
`reason_code` (`identity_mismatch`, `app_error`, `timeout`,
`input_invalid`, …), so failures are triaged by kind: `element_not_found`
is drift, `identity_mismatch` a correctness emergency.

| Behaviour | Reason |
|---|---|
| "Matched nothing" separated from "ambiguous: matched 2" | Identical symptoms, opposite fixes — page changed vs. locator no longer unique |
| Recovery is generic, not per-artifact: dialogs, session expiry | Dialogs checked when a step fails *and* after one succeeds (a popup can fire post-click); expiry replays the prefix that already worked. One attempt each |
| Business outcomes checked proactively *and* reactively | A robust locator's fallbacks could otherwise succeed straight *through* an outcome that should have stopped the run |
| Waiting bounded two ways: instant check, bounded poll | Instant for strict single-match semantics; poll where cross-frame navigation needs wall-clock time |
| UI drift: ranked fallback, then loud failure | Secondary to runtime errors per the brief; the failure names the failing candidate, which is the signal to re-record |
| Denial vs. app error judged by HTTP status, not page text | 403 is the app answering a question it understood (business outcome); 5xx is it failing to answer. Structural, so rewording either page changes nothing |
| "Still loading" separated from "not there" | Both arrive as "no element matched" and need opposite responses. An in-flight document request catches the server still thinking; `readyState` the parse phase after |
| Every failure keeps a screenshot, taken once centrally | Section 3.5's richer signal; eight construction sites would be eight chances to forget one |

**Limits.** Status codes aren't universal — a legacy app answering `200 OK`
with the error in its body needs per-app *text* detection declared on the
artifact — and the load check reads the *document*, so a page that fetches
its data in the background reports `complete` with nothing on it and that
shape of slow load still reads as `element_not_found`
([why, and what each would take](DECISIONS.md#step-3--the-permission-denial-the-app-error-and-the-slow-load-that-doesnt-finish)).

## 4. Heterogeneity & multi-tenant

**Surface abstraction**: two methods (`observe()`, `act()`); nothing above
knows `PlaywrightSurface` is a browser. A desktop app implements the same
interface against OS accessibility APIs — `observe()` walks the
accessibility tree, `act()` dispatches native invocations. `role`+`name` is
how UI Automation names a control too; only `table_*`/`css` are web-specific.

**Multi-tenant reuse**: the schema separates *what to do* (candidates) from
*where* (`target_domain`, `entry_url`), so swapping those replays the
artifact elsewhere — proven by the suite's retargeting helper. Label
anchoring makes it likelier to hold: tenant variation is usually extra rows
and reordered fields, not renamed labels. Real customization wants a
per-tenant *override* layer, which the ranked structure already supports.

**Drift detection**: `reason_code` is the raw signal. At scale — not built,
per the brief's scope note — track failure rate per `(artifact_id, tenant,
reason_code)`: an `element_not_found` spike for one tenant flags a UI
change before others hit it; any `identity_mismatch` is a correctness
alarm.

## 5. Escalation & handoff

**Stuck** = a policy block, or an outcome marked `requires_human`
(`ambiguous_duplicate` — picking the wrong bank record is the guess
automation shouldn't make).

**Taking control**: `escalate(request, surface)` receives the *exact*
`PlaywrightSurface` the run was using, and automation makes no further call
until it returns — control is concretely wherever that call is executing.
Three implementations, per "mock the UI, keep the mechanism real":
`InteractivePauseHandoff` (Playwright's Inspector on the live browser —
fully real, needs a person, so no test drives it), `MockOperatorHandoff` (a
callback handed the same live surface, clicking real elements — proves the
model unattended), `TerminalOperatorHandoff` (a real person, a real
decision; only "look at the page" is mocked).

**Handing back**: `APPROVE_AND_RETRY`; `MANUAL_RESOLVED` (skip the
remaining scripted steps — the old recipe's assumptions may no longer hold
— and go straight to the checkpoint); `ABANDON`. One escalation per named
outcome per run, so an unfixed state can't loop. Verified end to end twice:
the operator clicks a real "View" link and replay reads the *correct*
member's balance; and the sub-account commit is blocked, approved, then
performed by automation.

**Who was in control, and what they did.** Each run carries a
`control_timeline` of contiguous spans (`automation` → `human` →
`automation`); a run nobody touched is one uninterrupted span, because
"nobody took over" and "we didn't track it" must look different. Spans open
and close around the `escalate()` call, so the timeline records the fact
the call stack already enforces rather than restating it.

Operator actions are captured from the **page** — a capture-phase listener
in every frame reporting clicks, changes, submits and Enter presses through
an exposed binding — not from a wrapper API. That is what makes one
mechanism cover both modes: a wrapper records every handler that is *code*
and nothing for `InteractivePauseHandoff`, the one mode where a real person
is at the wheel. Submits and keys matter because an operator who types and
hits Enter never clicks anything, and the listener survives navigation.
Values are masked on arrival (`***02`); a password never leaves the page,
per the Part 4 rule that a value a scan can reach is one someone forgets to
redact. A capture failure is recorded, so an empty list always means "they
did nothing".

**Its limit**: what the operator does to the *browser* — back/forward,
address bar, new tabs, native dialogs — is chrome rather than DOM and stays
invisible, so the log is faithful about the page and incomplete about the
operator
([what's partly recoverable, and what completing it takes](DECISIONS.md#step-4--recording-what-the-human-did-and-who-held-the-wheel)).

## 6. Safety

| Control | Reason |
|---|---|
| Allowlist (domains, routes, action types) enforced outside the model | `PolicyEngine` sees only a typed `Action` — never the model's reasoning. Discovery and replay alike |
| Resolve, predict, check, then act | A click's destination (`href`, form `action`) is checked *before* clicking, so a blocked action never reaches the browser |
| `risky_routes` blocked by default, not flagged | Needs explicit `human_approved` — the conservative end of the brief, since it stands in for a money-moving action |
| Redaction fixed at each layer it leaked from | A password leaked three times at three layers; each fix has a regression test reproducing it. A fourth leak, found this phase, is now refused at build time |
| Sensitive values masked with a shape hint: `***18 [shape: money]` | The shape keeps the record useful — a date in a money field stays visible without the figure. The caller gets the value intact; masking applies where things are *written down* |
| `--show-sensitive` affects the terminal only | Files outlive the run and are read by people who weren't part of it |
| A test scans every committed file under `/evidence` and `/artifacts` | The credential rule is absolute, no exemption list — a scan listing files where the password is allowed has stopped being a check. Forbidden values derive from the app's seed data, so it can't drift. Verified by planting each kind of leak |

**Discovery transcripts and screenshots are a retention problem, not a
redaction one.** They record what the model saw, and masking them doesn't
make them safer evidence — it stops them being evidence. In production they
belong in access-controlled, short-retention storage, never a repository,
deleted once the artifact they produced is approved; the artifact is the
durable object and never holds those values either way. Here
`evidence/discovery-member-lookup/run_log.json` is exempt from the values
rule by exact path, with the reasoning in the test, and still subject to
the credential rule.

**Limits:** redaction is key-name and pattern based (an unanticipated
secret-shaped field isn't caught), sensitivity is declared per artifact (an
output nobody marked stays unmasked), screenshots are pixels and aren't
masked, the allowlist is route-based rather than content-based, and there
is no secrets vault and no rate-limiting or anomaly detection
([reasoning](DECISIONS.md#step-9--a-scan-with-one-rule-that-has-no-exceptions)).

## 7. Cuts

| Cut | Reason |
|---|---|
| Desktop / multi-tenant implementation | Designed for (Section 4), not built, per the brief's scope note |
| Richer *checkpoint* locators | Outputs get a ranked chain; checkpoints still get one `text` candidate, and want the fuller descriptor scan at record time |
| Business outcomes and the sub-account capability by deterministic capture, not LLM re-discovery | The sub-account flow *cannot* be discovered unattended: policy blocks its commit and discovery has no handoff path, so the run stalls at the step the artifact needs |
| mypy as a CI gate | Types and Pydantic models used by convention; a strict pass this late was schedule risk for a lower-weighted criterion |
| Confidence/approval workflow, code-gen from artifacts | Named stretch goals, natural once more artifacts exist to compare. Multi-run stability *was* built — `evidence/stability/` |
| Full co-browsing console | Out of scope per the brief; used Playwright's Inspector |
| Prose-only confirmation pages | An output appearing only inside a sentence gets a `text` locator pinned to one run's value. Needs a capture-group pattern on the output, not a locator — a real schema gap |

**What's next**: (1) per-tenant overrides plus drift tracking keyed on
`(artifact_id, tenant, reason_code)` — the reason codes are what make that
tracking meaningful; (2) the ranked-candidate treatment for checkpoints
that outputs now get; (3) a confidence/approval gate, once there's a real
population of artifacts to score.
