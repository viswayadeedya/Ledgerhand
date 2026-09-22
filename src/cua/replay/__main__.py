import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cua.artifacts.schema import CapabilityArtifact
from cua.handoff import InteractivePauseHandoff, TerminalOperatorHandoff
from cua.replay.engine import ReplayEngine


def _parse_kv(pairs: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs or []:
        name, _, value = pair.partition("=")
        result[name] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministically replay a saved capability artifact. No LLM involved.")
    parser.add_argument("--artifact", required=True, help="Path to a capability YAML from python -m cua.artifacts")
    parser.add_argument("--input", action="append", metavar="name=value", help="Repeatable")
    parser.add_argument("--secret", action="append", metavar="name=value", help="Repeatable")
    parser.add_argument("--human-approved", action="store_true", help="Allow risky/irreversible steps this run")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--evidence-dir", default="evidence/runs")
    parser.add_argument("--out", default=None, help="Optional path to also write the ReplayResult as JSON")
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
    result = engine.run(
        artifact,
        inputs=_parse_kv(args.input),
        secrets=_parse_kv(args.secret),
        human_approved=args.human_approved,
    )

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
        print(f"Outputs: {json.dumps(result.outputs, indent=2)}")
    if result.business_outcome:
        print(f"Business outcome: {result.business_outcome} -- {result.business_outcome_description}")
    if result.error:
        print(f"Error at step {result.error.step_index}: {result.error.message}")
        print(f"  expected: {result.error.expected}")
        print(f"  observed: {result.error.observed}")

    if args.out:
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "artifact_id": artifact.id,
            "artifact_version": artifact.version,
            "inputs": _parse_kv(args.input),
            "result": result.model_dump(mode="json"),
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Result written to {out_path}")


if __name__ == "__main__":
    main()
