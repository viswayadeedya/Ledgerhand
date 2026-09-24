import argparse
import json
from pathlib import Path

from cua.artifacts.recorder import build_artifact
from cua.artifacts.schema import to_yaml
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
    parser.add_argument(
        "--input-pattern",
        action="append",
        metavar="name=regex",
        help=(
            "Repeatable; a regex the caller's value must fully match, e.g. "
            '--input-pattern "member_id=^[0-9]{5}$". Checked before a browser opens, so a malformed '
            "input costs nothing and reports itself as a validation error rather than as whatever "
            "downstream step happened to fall over first."
        ),
    )
    parser.add_argument(
        "--input-example",
        action="append",
        metavar="name=value",
        help=(
            "Repeatable; a value safe to publish, for whoever reads this artifact to work out how to "
            "call it. Must not be the discovery run's own value -- that is real data, and the whole "
            "point of parameterizing it was to keep it out of a committed file."
        ),
    )
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
    parser.add_argument(
        "--exclude-output",
        action="append",
        metavar="name",
        help=(
            "Repeatable; drops a name the run log's own outputs would otherwise include by default. "
            "Useful when a value (e.g. an echoed input) only appears inside a form control -- text-locator "
            "extraction only works on real text content, not an <input>'s value, so those can't be "
            "reliably re-extracted at replay time and are better left out."
        ),
    )
    parser.add_argument(
        "--assert-equals",
        action="append",
        metavar="name=template",
        help=(
            "Repeatable; asserts an extracted output matches a caller-supplied value, e.g. "
            '--assert-equals "member_id={{inputs.member_id}}". This is what proves replay landed on the '
            "RIGHT record -- without it, a checkpoint like 'Savings Balance is on screen' is true of every "
            "member's page. A mismatch is a HARD_FAILURE and no outputs are returned."
        ),
    )
    parser.add_argument(
        "--output-type",
        action="append",
        metavar="name=type",
        help=(
            "Repeatable; declares what an output should look like (string|money|integer). A value that "
            "doesn't match is a HARD_FAILURE and is never returned -- e.g. a date read where a balance "
            "should be, after a page shifted under a positional locator."
        ),
    )
    parser.add_argument(
        "--sensitive-output",
        action="append",
        metavar="name",
        help="Repeatable; marks an output whose value must be masked anywhere it's printed or persisted",
    )
    parser.add_argument(
        "--sensitive-input",
        action="append",
        metavar="name",
        help=(
            "Repeatable; marks an input whose value must be masked when a result file records what "
            "was asked for. Usually the same names as --sensitive-output: masking a member ID read "
            "off the page while filing the identical ID under 'inputs' would be theatre."
        ),
    )
    parser.add_argument("--checkpoint-text", required=True)

    args = parser.parse_args()

    run_log = json.loads(Path(args.run_log).read_text(encoding="utf-8"))
    steps = [RecordedStep.model_validate(s) for s in run_log["steps"]]

    output_values = dict(run_log.get("outputs") or {})
    output_values.update(_parse_kv(args.output))
    for name in args.exclude_output or []:
        output_values.pop(name, None)

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
        input_patterns=_parse_kv(args.input_pattern),
        input_examples=_parse_kv(args.input_example),
        secret_names=args.secret or [],
        secret_values=_parse_kv(args.secret_value),
        output_descriptions=_parse_kv(args.output_desc),
        output_values=output_values,
        output_assertions=_parse_kv(args.assert_equals),
        output_types=_parse_kv(args.output_type),
        sensitive_outputs=args.sensitive_output or [],
        sensitive_inputs=args.sensitive_input or [],
        checkpoint_text=args.checkpoint_text,
        source_run_log=args.run_log,
        version=args.version,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_yaml(artifact), encoding="utf-8")
    print(f"Wrote {out_path} ({len(artifact.steps)} steps, {len(artifact.outputs)} outputs)")


if __name__ == "__main__":
    main()
