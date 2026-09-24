import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cua.artifacts.schema import CapabilityArtifact
from cua.guardrails.redact import mask_sensitive
from cua.handoff import InteractivePauseHandoff, TerminalOperatorHandoff
from cua.replay.engine import ReplayEngine
from cua.replay.models import ReplayOutcome

EXIT_CODES: dict[ReplayOutcome, int] = {
    ReplayOutcome.SUCCESS: 0,
    ReplayOutcome.RECOVERED: 0,  # it worked; that it needed a retry is detail, not failure
    ReplayOutcome.HARD_FAILURE: 1,
    ReplayOutcome.BUSINESS_OUTCOME: 2,
    ReplayOutcome.NEEDS_HUMAN: 3,
}
"""So a caller can branch on the result without parsing stdout.

The distinctions matter to whoever is scripting this: 2 means the app
answered correctly and the answer was "no such member" -- retrying won't
help and nothing is broken. 3 means a human has to decide. Only 1 means
something is actually wrong.
"""

USAGE_EXIT_CODE = 64
"""argparse exits 2 on a bad command line, which would be indistinguishable
from a business outcome. Moved to the conventional EX_USAGE so the codes
above mean only what they say.
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # pragma: no cover - argparse internals
        self.print_usage(sys.stderr)
        self.exit(USAGE_EXIT_CODE, f"{self.prog}: error: {message}\n")


def _parse_kv(pairs: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs or []:
        name, _, value = pair.partition("=")
        result[name] = value
    return result


def main() -> int:
    parser = _Parser(description="Deterministically replay a saved capability artifact. No LLM involved.")
    parser.add_argument("--artifact", required=True, help="Path to a capability YAML from python -m cua.artifacts")
    parser.add_argument("--input", action="append", metavar="name=value", help="Repeatable")
    parser.add_argument("--secret", action="append", metavar="name=value", help="Repeatable")
    parser.add_argument("--human-approved", action="store_true", help="Allow risky/irreversible steps this run")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--evidence-dir", default="evidence/runs")
    parser.add_argument("--out", default=None, help="Optional path to also write the ReplayResult as JSON")
    parser.add_argument(
        "--show-sensitive",
        action="store_true",
        help=(
            "Print outputs marked 'sensitive' in the artifact in full instead of masked. For local "
            "demos on fake data. Files written by --out and everything under --evidence-dir stay "
            "masked regardless -- this affects your terminal only."
        ),
    )
    parser.add_argument(
        "--handoff",
        choices=["none", "terminal", "interactive"],
        default="none",
        help=(
            "How to handle a NEEDS_HUMAN condition: 'none' stops and reports it (Part 6 behavior); "
            "'terminal' prompts you at this terminal for a decision; 'interactive' pauses with the "
            "Playwright Inspector open on the live browser for you to take over directly (needs --headed)."
        ),
    )
    args = parser.parse_args()

    artifact = CapabilityArtifact.model_validate(yaml.safe_load(Path(args.artifact).read_text(encoding="utf-8")))

    handoff = None
    if args.handoff == "terminal":
        handoff = TerminalOperatorHandoff(evidence_dir=args.evidence_dir)
    elif args.handoff == "interactive":
        handoff = InteractivePauseHandoff(evidence_dir=args.evidence_dir)

    engine = ReplayEngine(headless=not args.headed, evidence_dir=args.evidence_dir, handoff=handoff)
    try:
        result = engine.run(
            artifact,
            inputs=_parse_kv(args.input),
            secrets=_parse_kv(args.secret),
            human_approved=args.human_approved,
        )
    except ValueError as exc:
        # The engine's pre-flight checks (missing inputs or secrets, an
        # assertion template that names nothing) all mean "this was invoked
        # wrong" -- the same class as a bad flag, and raised before a
        # browser opens. A traceback would suggest a defect in replay; this
        # is a one-line correction to the command.
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE

    print(f"\nOutcome: {result.outcome.value}")
    print(f"Steps executed: {result.steps_executed}/{len(artifact.steps)}")
    if result.recovery_events:
        print("Recovery events:")
        for ev in result.recovery_events:
            print(f"  - step {ev.step_index}: {ev.kind} ({ev.detail})")
    if result.escalations:
        print("Escalations:")
        for esc in result.escalations:
            print(f"  - step {esc.step_index}: {esc.decision.value} -- {esc.operator_note}")
    if result.outputs:
        shown = result.outputs if args.show_sensitive else mask_sensitive(result.outputs, result.sensitive_outputs)
        print(f"Outputs: {json.dumps(shown, indent=2)}")
        if result.sensitive_outputs and not args.show_sensitive:
            masked = ", ".join(sorted(result.sensitive_outputs))
            print(f"  ({masked} masked -- pass --show-sensitive to print in full)")
    if result.business_outcome:
        print(f"Business outcome: {result.business_outcome} -- {result.business_outcome_description}")
    if result.error:
        where = "step " + str(result.error.step_index) if result.error.step_index is not None else "after all steps"
        print(f"Failure   : {result.error.reason_code.value} ({where})")
        print(f"  expected: {result.error.expected}")
        print(f"  observed: {result.error.observed}")
        print(f"  detail  : {result.error.message}")

    if args.out:
        # Always masked, no flag. --show-sensitive is about what a person
        # sees on their own screen for a moment; this is a file that
        # outlives the run, gets committed as evidence, and is read by
        # people who were never part of it.
        persisted = result.model_dump(mode="json")
        persisted["outputs"] = mask_sensitive(result.outputs, result.sensitive_outputs)
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "artifact_id": artifact.id,
            "artifact_version": artifact.version,
            "inputs": _parse_kv(args.input),
            "result": persisted,
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Result written to {out_path}")

    return EXIT_CODES[result.outcome]


if __name__ == "__main__":
    sys.exit(main())
