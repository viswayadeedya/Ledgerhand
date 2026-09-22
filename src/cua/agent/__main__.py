import argparse
import json

from cua.agent.discovery import run_discovery


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM-driven discovery session against a live surface.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--target", required=True, help="Starting URL, e.g. http://127.0.0.1:5055/login")
    parser.add_argument("--domain", required=True, help="Allowed host:port, e.g. 127.0.0.1:5055")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--headed", action="store_true", help="show the browser window instead of running headless")
    args = parser.parse_args()

    result = run_discovery(
        goal=args.goal,
        target_url=args.target,
        allowed_domain=args.domain,
        model=args.model,
        max_steps=args.max_steps,
        headless=not args.headed,
    )

    print(f"\nOutcome: {result.outcome}")
    print(f"Summary: {result.summary}")
    print(f"Outputs: {json.dumps(result.outputs, indent=2)}")
    print(f"Steps recorded: {len(result.steps)}  Model: {result.model}")


if __name__ == "__main__":
    main()
