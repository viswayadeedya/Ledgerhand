import argparse
import json
from pathlib import Path

import yaml

from cua.artifacts.recorder import build_artifact
from cua.core.models import RecordedStep


def _parse_kv(pairs: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs or []:
        name, _, value = pair.partition("=")
        result[name] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a capability artifact from a discovery run's log (see `python -m cua.agent`)."
    )
    parser.add_argument("--run-log", required=True, help="Path to a run_log.json produced by python -m cua.agent")
    parser.add_argument("--out", required=True, help="Where to write the artifact YAML")
    parser.add_argument("--id", required=True, dest="capability_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--version", type=int, default=1)

    parser.add_argument("--input", action="append", metavar="name=value", help="Repeatable")
    parser.add_argument("--input-desc", action="append", metavar="name=description", help="Repeatable")
    parser.add_argument("--secret", action="append", metavar="name", help="Repeatable; declares a required secret")
    parser.add_argument(
        "--secret-value",
        action="append",
        metavar="name=value",
        help="Repeatable; only for secrets that survive un-redacted in the log (e.g. username)",
    )
    parser.add_argument(
        "--output",
        action="append",
        metavar="name=value",
        help="Repeatable; overrides the value used to locate this output (defaults to the run log's own outputs)",
    )
    parser.add_argument("--output-desc", action="append", metavar="name=description", help="Repeatable")
    parser.add_argument("--checkpoint-text", required=True)

    args = parser.parse_args()

    run_log = json.loads(Path(args.run_log).read_text(encoding="utf-8"))
    steps = [RecordedStep.model_validate(s) for s in run_log["steps"]]

    output_values = dict(run_log.get("outputs") or {})
    output_values.update(_parse_kv(args.output))

    artifact = build_artifact(
        steps,
        capability_id=args.capability_id,
        title=args.title,
        description=args.description,
        target_domain=run_log["allowed_domain"],
        entry_url=run_log["target_url"],
        discovery_model=run_log["model"],
        inputs=_parse_kv(args.input),
        input_descriptions=_parse_kv(args.input_desc),
        secret_names=args.secret or [],
        secret_values=_parse_kv(args.secret_value),
        output_descriptions=_parse_kv(args.output_desc),
        output_values=output_values,
        checkpoint_text=args.checkpoint_text,
        source_run_log=args.run_log,
        version=args.version,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(artifact.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({len(artifact.steps)} steps, {len(artifact.outputs)} outputs)")


if __name__ == "__main__":
    main()
