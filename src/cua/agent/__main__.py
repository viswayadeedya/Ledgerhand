import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from cua.agent.discovery import run_discovery


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM-driven discovery session against a live surface.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--target", required=True, help="Starting URL, e.g. http://127.0.0.1:5055/login")
    parser.add_argument("--domain", required=True, help="Allowed host:port, e.g. 127.0.0.1:5055")
    parser.add_argument("--username", default=None, help="Sign-on username, if the target requires login")
    parser.add_argument("--password", default=None, help="Sign-on password, if the target requires login")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--headed", action="store_true", help="show the browser window instead of running headless")
    parser.add_argument("--evidence-dir", default="evidence/runs", help="where screenshots + the run log are written")
    args = parser.parse_args()

    credentials = None
    if args.username or args.password:
        credentials = {"username": args.username or "", "password": args.password or ""}

    result = run_discovery(
        goal=args.goal,
        target_url=args.target,
        allowed_domain=args.domain,
        credentials=credentials,
        model=args.model,
        max_steps=args.max_steps,
        headless=not args.headed,
        evidence_dir=args.evidence_dir,
    )

    print(f"\nOutcome: {result.outcome}")
    print(f"Summary: {result.summary}")
    print(f"Outputs: {json.dumps(result.outputs, indent=2)}")
    print(f"Steps recorded: {len(result.steps)}  Model: {result.model}")

    run_log = {
        "goal": args.goal,
        "target_url": args.target,
        "allowed_domain": args.domain,
        "model": result.model,
        "outcome": result.outcome,
        "summary": result.summary,
        "outputs": result.outputs,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "steps": [step.model_dump(mode="json") for step in result.steps],
    }
    log_path = Path(args.evidence_dir) / "run_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(run_log, indent=2), encoding="utf-8")
    print(f"Run log written to {log_path}")


if __name__ == "__main__":
    main()
