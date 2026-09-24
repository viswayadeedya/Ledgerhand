"""Replays the lookup capability 20 times against randomly injected faults
and writes a scoreboard to evidence/stability/.

The brief's optional multi-run stability signal, with one addition that
turns it from a tally into a check: every value a run hands back is
compared against the fake app's own seed data for the member that was
actually requested. Counting outcomes alone would report a run that
returned someone else's balance as a clean success, which is the single
worst thing this system can do -- so `wrong_answers` is the number that
matters and it must be 0.

Two signals, deliberately separate:

  wrong_answers        A run returned data that isn't the requested
                       member's. Must be 0. Anything else is a correctness
                       emergency, not a flake.
  unexpected_outcomes  A run ended somewhere other than where its injected
                       fault should have led. This is the actual flakiness
                       signal -- the variance here is injected on purpose,
                       so a raw outcome histogram measures the fault mix,
                       not stability.

Seeded, so the run is reproducible; pass --seed to vary it.

Usage:
    python scripts/run_stability.py
    python scripts/run_stability.py --runs 40 --seed 7
"""

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_evidence as ev  # noqa: E402  -- reuse the app lifecycle and fault helpers

from cua.artifacts.schema import load_yaml  # noqa: E402
from cua.fake_app.data import _TEMPLATE_MEMBERS  # noqa: E402
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.guardrails.redact import mask_partial  # noqa: E402
from cua.replay.engine import ReplayEngine  # noqa: E402
from cua.replay.models import ReplayOutcome  # noqa: E402

OUT_DIR = ev.EVIDENCE / "stability"

MEMBERS = ["10001", "10002", "10003", "10004"]
"""Active members only. 10005 is closed and has no balances, so its detail
page never shows the checkpoint -- a legitimate hard failure, but one about
account status rather than about the fault under test, and it would sit in
the scoreboard looking like instability.
"""

# Each fault and where it should land, unattended (no handoff configured).
FAULT_EXPECTATIONS: dict[str | None, str] = {
    None: "success",
    "popup": "recovered",
    "session_expired": "recovered",
    "slow_load": "success",
    "extra_row": "success",
    "member_not_found": "business_outcome",
    "permission_denied": "business_outcome",
    "duplicate_members": "needs_human",
    "wrong_member": "hard_failure",
    "app_error": "hard_failure",
}


def expected_outputs(member_id: str) -> dict[str, str]:
    """Ground truth, straight from the fake app's seed template.

    The template rather than the live dict: this process doesn't share
    memory with the app, and the template is the pristine state a reset
    restores, which is what every run starts from.
    """
    member = _TEMPLATE_MEMBERS[int(member_id)]
    return {
        "member_id": str(member["id"]),
        "member_name": f"{member['first_name']} {member['last_name']}",
        "savings_balance": f"${member['savings_balance']:.2f}",
        "checking_balance": f"${member['checking_balance']:.2f}",
    }


def check_outputs(member_id: str, outputs: dict[str, str]) -> list[dict]:
    """Every returned value against the seed data, as a list of mismatches.

    Reported masked with a shape hint, the same way every other written-down
    value is: a correctness alarm has to be legible without putting the
    figures it is complaining about on disk.
    """
    if not outputs:
        return []
    expected = expected_outputs(member_id)
    problems = []
    for name, got in outputs.items():
        want = expected.get(name)
        if want is None or got.strip() == want:
            continue
        problems.append(
            {
                "output": name,
                "expected": mask_partial(want, shape_hint=True),
                "observed": mask_partial(got, shape_hint=True),
            }
        )
    return problems


def _fault_schedule(rng: random.Random, runs: int) -> list[str | None]:
    """A shuffled schedule that covers every fault, rather than sampling
    independently each run.

    Drawing at random with replacement looks more "random" and is worse
    evidence: the first seeded 20-run pass produced eight `app_error`s and
    not one `popup`, `slow_load` or `duplicate_members`, so three of the
    conditions this is supposed to exercise never ran at all. Cycling a
    shuffled pool keeps the order and the member unpredictable while
    guaranteeing the coverage the scoreboard claims.
    """
    faults = list(FAULT_EXPECTATIONS)
    schedule: list[str | None] = []
    while len(schedule) < runs:
        block = faults[:]
        rng.shuffle(block)
        schedule.extend(block)
    return schedule[:runs]


def run_once(engine: ReplayEngine, artifact, member_id: str, fault: str | None) -> dict:
    ev.reset()
    if fault is not None:
        ev.arm(fault)()

    result = engine.run(
        artifact,
        inputs={"member_id": member_id},
        secrets={"username": ev.USERNAME, "password": ev.PASSWORD},
    )

    wrong = check_outputs(member_id, result.outputs)
    expected_outcome = FAULT_EXPECTATIONS[fault]
    return {
        "fault": fault or "none",
        # Masked like every other written-down value, and nothing is lost
        # by it: the seeded members differ in their last two digits, so
        # ***01 and ***02 stay as distinguishable as the full IDs. Worth
        # doing rather than claiming an exemption that buys nothing.
        "member_id": mask_partial(member_id),
        "outcome": result.outcome.value,
        "expected_outcome": expected_outcome,
        "as_expected": result.outcome.value == expected_outcome,
        "reason_code": result.error.reason_code.value if result.error else None,
        "recovery": [e.kind for e in result.recovery_events],
        "wrong_answer": bool(wrong),
        "mismatches": wrong,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    schedule = _fault_schedule(rng, args.runs)

    artifact = load_yaml((REPO_ROOT / ev.LOOKUP).read_text(encoding="utf-8"))
    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [ev.DOMAIN]}))

    app = ev.start_app()
    rows = []
    try:
        print(f"\n{args.runs} replays, seed {args.seed}:")
        for index, fault in enumerate(schedule):
            member_id = rng.choice(MEMBERS)
            engine = ReplayEngine(
                policy=policy, headless=True, evidence_dir=ev.SCRATCH / "stability" / f"run_{index:02d}"
            )
            row = run_once(engine, artifact, member_id, fault)
            rows.append(row)
            flag = "ok  " if row["as_expected"] else "DIFF"
            wrong = "  WRONG ANSWER" if row["wrong_answer"] else ""
            print(f"  [{flag}] {index:>2} fault={row['fault']:<18} -> {row['outcome']}{wrong}")
    finally:
        ev.reset()
        if app is not None:
            app.terminate()
            app.wait(timeout=10)

    wrong_answers = [r for r in rows if r["wrong_answer"]]
    unexpected = [r for r in rows if not r["as_expected"]]

    scoreboard = {
        "seed": args.seed,
        "runs": len(rows),
        "artifact": artifact.id,
        "artifact_version": artifact.version,
        "wrong_answers": len(wrong_answers),
        "unexpected_outcomes": len(unexpected),
        "outcomes": dict(sorted(Counter(r["outcome"] for r in rows).items())),
        "reason_codes": dict(sorted(Counter(r["reason_code"] for r in rows if r["reason_code"]).items())),
        "faults": dict(sorted(Counter(r["fault"] for r in rows).items())),
        "recovery_kinds": dict(sorted(Counter(k for r in rows for k in r["recovery"]).items())),
        "runs_detail": rows,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "scoreboard.json").write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")
    (OUT_DIR / "README.md").write_text(_render(scoreboard), encoding="utf-8")

    print(f"\nwrong answers      : {len(wrong_answers)}")
    print(f"unexpected outcomes: {len(unexpected)}")
    print(f"Wrote {OUT_DIR / 'scoreboard.json'}")

    if wrong_answers:
        print("\nA run returned data that is not the requested member's:")
        for row in wrong_answers:
            for mismatch in row["mismatches"]:
                print(
                    f"  fault={row['fault']} member={row['member_id']} "
                    f"{mismatch['output']}: expected {mismatch['expected']}, got {mismatch['observed']}"
                )
        return 1
    if unexpected:
        print("\nRuns that ended somewhere other than their fault predicts:")
        for row in unexpected:
            print(f"  fault={row['fault']}: expected {row['expected_outcome']}, got {row['outcome']}")
        return 1
    return 0


def _render(scoreboard: dict) -> str:
    def table(title: str, counts: dict) -> str:
        if not counts:
            return ""
        rows = "\n".join(f"| `{k}` | {v} |" for k, v in counts.items())
        return f"\n**{title}**\n\n| | count |\n| --- | --- |\n{rows}\n"

    verdict = (
        "**0 wrong answers.**" if not scoreboard["wrong_answers"] else f"**{scoreboard['wrong_answers']} WRONG ANSWERS.**"
    )
    return f"""# Stability

{scoreboard['runs']} replays of `{scoreboard['artifact']}` v{scoreboard['artifact_version']},
covering every injected fault twice, in random order, plus clean runs.

The schedule is a shuffled pool rather than an independent draw per run:
sampling with replacement looks more random and is worse evidence -- the
first seeded pass produced eight `app_error`s and never once fired
`popup`, `slow_load` or `duplicate_members`. "No fault" is an entry in
that pool like any other, so clean runs are guaranteed too and a
regression that only breaks the happy path can't hide behind a scoreboard
made entirely of faults. Order and member stay unpredictable; coverage is
guaranteed. Seed `{scoreboard['seed']}` -- reproducible with:

```
python scripts/run_stability.py --runs {scoreboard['runs']} --seed {scoreboard['seed']}
```

{verdict} Every value each run handed back was compared against the fake
app's own seed data for the member actually requested. This is the number
that matters: a tally of outcomes would happily record a run that returned
someone else's balance as a clean success.

`unexpected_outcomes` is **{scoreboard['unexpected_outcomes']}** -- runs that
ended somewhere other than their injected fault predicts. That, not the
outcome histogram, is the flakiness signal: the variance below is injected
on purpose, so the histogram measures the fault mix rather than stability.
{table("Outcomes", scoreboard["outcomes"])}{table("Failure reasons", scoreboard["reason_codes"])}{table("Recovery performed", scoreboard["recovery_kinds"])}{table("Faults injected", scoreboard["faults"])}
Per-run detail, including which fault produced which outcome, is in
[`scoreboard.json`](scoreboard.json).
"""


if __name__ == "__main__":
    sys.exit(main())
