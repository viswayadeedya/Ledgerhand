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

## 6. Safety

| Control | Reason |
|---|---|
| Allowlist (domains, routes, action types) enforced outside the model | `PolicyEngine` sees only a typed `Action` — never the LLM's reasoning, never whether a step succeeded. Discovery and replay alike |
| Resolve, predict, check, then act — never the reverse | A click's destination (`href`, form `action`) is checked *before* clicking, so a blocked action never reaches the browser |
| `risky_routes` blocked by default, not flagged | Needs explicit `human_approved` — the conservative end of the brief, since it stands in for a money-moving action |
| Redaction fixed at each layer it leaked from | A password leaked three times, at three layers (DECISIONS.md, Part 4); each fix has a regression test reproducing the leak |
| Sensitive values masked with a shape hint: `***18 [shape: money]` | The shape keeps the record useful — a date in a money field stays visible without the figure. The caller gets the value intact; masking applies where things are *written down* |
| `--show-sensitive` affects the terminal only | Result files and evidence are masked with no flag: they outlive the run and are read by people who weren't part of it |

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
