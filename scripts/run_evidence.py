"""Produces every evidence folder under /evidence, from real runs.

One scenario per runtime condition the brief names (Section 3.3, plus
"outright app errors" from Section 1), plus the success, identity and
handoff cases. Each writes one folder containing:

    command.txt   how to reproduce this exact run
    result.json   the ReplayResult, masked
    run.log       what the run printed
    failure.png   only when the run failed -- the richer signal Section 3.5 asks for

Per-step screenshots go to evidence/runs/<scenario>/ instead, which is
git-ignored scratch. Committing eight PNGs per scenario would be a hundred
images nobody looks at; the one that matters is the state a failure ended
in, and that is the one kept.

Two execution modes, and the difference is recorded in each folder's
command.txt rather than smoothed over:
  - Most scenarios run the real CLI as a subprocess, so the command is
    literally reproducible and the exit code is a real process exit code.
  - The three handoff scenarios run in-process, because they need a
    programmable operator and the CLI deliberately only offers a human one
    (`--handoff terminal`/`interactive`). Their exit code is derived from
    the same EXIT_CODES table the CLI uses.

Usage:
    python scripts/run_evidence.py                  # all scenarios
    python scripts/run_evidence.py --only not_found # one
    python scripts/run_evidence.py --list
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.schema import load_yaml  # noqa: E402
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.guardrails.redact import mask_sensitive  # noqa: E402
from cua.handoff import EscalationRequest, HandoffAction, HandoffDecision, MockOperatorHandoff  # noqa: E402
from cua.replay.__main__ import EXIT_CODES  # noqa: E402
from cua.replay.engine import ReplayEngine  # noqa: E402

DOMAIN = "127.0.0.1:5055"
BASE_URL = f"http://{DOMAIN}"
EVIDENCE = REPO_ROOT / "evidence"
SCRATCH = EVIDENCE / "runs"

LOOKUP = "artifacts/member-savings-lookup.yaml"
SUBACCOUNT = "artifacts/member-subaccount-open.yaml"

USERNAME = "teller1"
PASSWORD = "teller123"
"""The fake app's own demo credential. It is written into no file this
script produces -- command.txt parameterizes it as $env:TELLER_PASSWORD and
the value is documented in the top-level README only, so the leak scan can
be absolute with no exemption list.
"""

LOOKUP_INPUTS = {"member_id": "10001"}
SUBACCOUNT_INPUTS = {"member_id": "10001", "account_type": "checking", "initial_deposit": "250.00"}


# -- the app ---------------------------------------------------------------


def _reachable() -> bool:
    try:
        urllib.request.urlopen(f"{BASE_URL}/login", timeout=1)
        return True
    except Exception:
        return False


def _port_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", 5055)) != 0


def start_app() -> subprocess.Popen | None:
    """Starts the fake app on its documented port, or reuses one already
    there. Returns the process we own, or None if we're borrowing someone
    else's -- so we only shut down what we started.

    The port is fixed rather than ephemeral because the artifacts and the
    guardrail allowlist both name it, and because command.txt has to be a
    command someone can actually paste.
    """
    if _reachable():
        print(f"Using the fake app already running on {DOMAIN}")
        return None
    if not _port_free():
        raise RuntimeError(f"port 5055 is busy but not answering as the fake app; free it first")

    proc = subprocess.Popen(
        [sys.executable, "-m", "cua.fake_app"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if _reachable():
            print(f"Started the fake app on {DOMAIN}")
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("the fake app did not come up in time")


def _post(path: str, payload: dict) -> None:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request)


def reset() -> None:
    urllib.request.urlopen(urllib.request.Request(f"{BASE_URL}/admin/reset", method="POST"))


def arm(fault: str) -> Callable[[], None]:
    return lambda: _post("/admin/faults/api", {"fault": fault, "armed": True})


def setting(name: str, value: float) -> Callable[[], None]:
    return lambda: _post("/admin/settings/api", {"name": name, "value": value})


def both(*setups: Callable[[], None]) -> Callable[[], None]:
    def _run() -> None:
        for setup in setups:
            setup()

    return _run


# -- scenarios -------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    brief: str
    expected: str
    artifact: str = LOOKUP
    inputs: dict = field(default_factory=lambda: dict(LOOKUP_INPUTS))
    setup: Callable[[], None] | None = None
    operator: Callable | None = None
    """Present for the three handoff scenarios; makes this run in-process."""


def _abandon(request: EscalationRequest, surface) -> HandoffDecision:
    return HandoffDecision(
        action=HandoffAction.ABANDON,
        operator_note="Two conflicting records and no way to tell which is right; declining to guess.",
    )


def _take_over(request: EscalationRequest, surface) -> HandoffDecision:
    # Acting on the SAME live session replay was driving -- the click lands
    # in the real browser, and the recorder picks it up from the page.
    surface.page.frame(name="main").get_by_role("link", name="View").first.click()
    return HandoffDecision(
        action=HandoffAction.MANUAL_RESOLVED,
        operator_note="Reviewed both records and selected the genuine one.",
    )


def _approve(request: EscalationRequest, surface) -> HandoffDecision:
    return HandoffDecision(
        action=HandoffAction.APPROVE_AND_RETRY,
        operator_note="Verified with the member in branch; approved opening the sub-account.",
    )


SCENARIOS: list[Scenario] = [
    Scenario("success_10001", "3.3 replay + outputs", "success", inputs={"member_id": "10001"}),
    Scenario(
        "success_10002",
        "3.3 replay + outputs",
        "success",
        inputs={"member_id": "10002"},
    ),
    Scenario("not_found", "3.3 expected business outcome", "business_outcome member_not_found", inputs={"member_id": "99999"}),
    Scenario("invalid_input", "3.3 validation error", "hard_failure input_invalid", inputs={"member_id": "abc"}),
    Scenario("popup_recovered", "3.3 unexpected dialog", "recovered", setup=arm("popup")),
    Scenario("session_expired_recovered", "3.3 session timeout", "recovered", setup=arm("session_expired")),
    Scenario(
        "slow_load_ok",
        "3.3 transient slowness",
        "success",
        setup=both(setting("slow_load_seconds", 2), arm("slow_load")),
    ),
    Scenario(
        "slow_load_timeout",
        "3.3 slow/failed load",
        "hard_failure timeout",
        setup=both(setting("slow_load_seconds", 12), arm("slow_load")),
    ),
    Scenario("permission_denied", "3.3 permission denial", "business_outcome permission_denied", setup=arm("permission_denied")),
    Scenario("app_error", "3.3 / 1 app error", "hard_failure app_error", setup=arm("app_error")),
    Scenario("wrong_member", "3.3 hard failure (identity)", "hard_failure identity_mismatch", setup=arm("wrong_member")),
    # The outcome name rides along on a NEEDS_HUMAN too: the run stopped
    # *because of* a named business outcome, and the caller needs to know
    # which one, not just that a person is required.
    Scenario(
        "duplicate_abandon",
        "3.6 escalation",
        "needs_human ambiguous_duplicate",
        setup=arm("duplicate_members"),
        operator=_abandon,
    ),
    Scenario("duplicate_takeover", "3.6 take control of the live session", "recovered", setup=arm("duplicate_members"), operator=_take_over),
    Scenario(
        "risky_step_approved",
        "3.6 + 3.4 risky step approved",
        "recovered",
        artifact=SUBACCOUNT,
        inputs=dict(SUBACCOUNT_INPUTS),
        operator=_approve,
    ),
]


# -- running one -----------------------------------------------------------


def _command_text(scenario: Scenario) -> str:
    """The reproduction recipe, with the credential parameterized.

    The password is never written into a file under evidence/ -- see the
    leak scan. `$env:TELLER_PASSWORD` is PowerShell's form because the
    project's documented commands are PowerShell; the value lives in the
    top-level README.
    """
    inputs = " ".join(f'--input "{k}={v}"' for k, v in scenario.inputs.items())
    setup_lines = []
    if scenario.setup is not None:
        setup_lines.append("# Arm the condition first -- see this folder's row in evidence/README.md")
    if scenario.operator is not None:
        return "\n".join(
            [
                "# Runs in-process: this scenario needs a programmable operator, and the CLI",
                "# deliberately offers only human handoff modes (--handoff terminal|interactive).",
                f"python scripts/run_evidence.py --only {scenario.name}",
                "",
                "# The equivalent interactive command, if you want to play the operator yourself:",
                f'python -m cua.replay --artifact "{scenario.artifact}" {inputs} \\',
                f'  --secret "username={USERNAME}" --secret "password=$env:TELLER_PASSWORD" \\',
                "  --handoff terminal",
            ]
        )
    return "\n".join(
        setup_lines
        + [
            f"python scripts/run_evidence.py --only {scenario.name}",
            "",
            "# which runs:",
            f'python -m cua.replay --artifact "{scenario.artifact}" {inputs} \\',
            f'  --secret "username={USERNAME}" --secret "password=$env:TELLER_PASSWORD" \\',
            f'  --out "evidence/{scenario.name}/result.json" --evidence-dir "evidence/runs/{scenario.name}"',
        ]
    )


def _run_via_cli(scenario: Scenario, out_dir: Path, scratch: Path) -> tuple[int, str]:
    args = [
        sys.executable, "-m", "cua.replay",
        "--artifact", scenario.artifact,
        "--secret", f"username={USERNAME}",
        "--secret", f"password={PASSWORD}",
        "--out", str(out_dir / "result.json"),
        "--evidence-dir", str(scratch),
    ]
    for name, value in scenario.inputs.items():
        args += ["--input", f"{name}={value}"]

    completed = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return completed.returncode, completed.stdout + completed.stderr


def _run_in_process(scenario: Scenario, out_dir: Path, scratch: Path) -> tuple[int, str]:
    artifact = load_yaml((REPO_ROOT / scenario.artifact).read_text(encoding="utf-8"))
    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [DOMAIN]}))
    engine = ReplayEngine(
        policy=policy,
        headless=True,
        evidence_dir=scratch,
        # The handler writes to the scenario folder, not the scratch dir:
        # the escalation record and what the operator did are the evidence
        # here, unlike the per-step screenshots.
        handoff=MockOperatorHandoff(scenario.operator, evidence_dir=out_dir),
    )
    result = engine.run(
        artifact,
        inputs=scenario.inputs,
        secrets={"username": USERNAME, "password": PASSWORD},
    )

    payload = result.model_dump(mode="json")
    payload["outputs"] = mask_sensitive(result.outputs, result.sensitive_outputs)
    sensitive_inputs = [i.name for i in artifact.inputs if i.sensitive]
    (out_dir / "result.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact.id,
                "artifact_version": artifact.version,
                "inputs": mask_sensitive(scenario.inputs, sensitive_inputs),
                "result": payload,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [f"Outcome: {result.outcome.value}", f"Steps executed: {result.steps_executed}/{len(artifact.steps)}"]
    for escalation in result.escalations:
        lines.append(f"Escalation: {escalation.decision.value} -- {escalation.operator_note}")
        for action in escalation.operator_actions:
            lines.append(f"  operator {action.kind}: {action.target}" + (f" = {action.value}" if action.value else ""))
    for span in result.control_timeline:
        lines.append(f"Control: {span.holder.value} {span.started_at} -> {span.ended_at} ({span.reason})")
    if result.outputs:
        lines.append(f"Outputs: {json.dumps(payload['outputs'])}")
    if result.error:
        lines.append(f"Failure: {result.error.reason_code.value} -- {result.error.message}")
    return EXIT_CODES[result.outcome], "\n".join(lines) + "\n"


def _actual_from_result(out_dir: Path) -> str:
    """What the run really produced, read back off disk rather than from
    whatever we hoped -- the README table's "actual" column has to come
    from the artifact of the run, not from the scenario's expectation.
    """
    path = out_dir / "result.json"
    if not path.exists():
        return "no result written"
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result", payload)
    outcome = result.get("outcome", "?")
    if result.get("business_outcome"):
        return f"{outcome} {result['business_outcome']}"
    if result.get("error"):
        return f"{outcome} {result['error']['reason_code']}"
    return outcome


def _keep_failure_screenshot(out_dir: Path) -> str | None:
    """Copies the failing state's screenshot next to the result.

    Only on failure, and only one: the per-step images stay in scratch.
    Committing every step of every scenario would be a hundred PNGs nobody
    opens, and the one that carries information is the state it ended in.
    """
    path = out_dir / "result.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result", payload)
    error = result.get("error") or {}
    source = error.get("screenshot_path")
    if not source:
        return None
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    if not source_path.exists():
        return None
    shutil.copyfile(source_path, out_dir / "failure.png")
    return "failure.png"


def run_scenario(scenario: Scenario) -> dict:
    out_dir = EVIDENCE / scenario.name
    scratch = SCRATCH / scenario.name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    scratch.mkdir(parents=True, exist_ok=True)

    reset()
    if scenario.setup is not None:
        scenario.setup()

    runner = _run_in_process if scenario.operator is not None else _run_via_cli
    exit_code, log = runner(scenario, out_dir, scratch)

    (out_dir / "command.txt").write_text(_command_text(scenario) + "\n", encoding="utf-8")
    (out_dir / "run.log").write_text(log, encoding="utf-8")
    screenshot = _keep_failure_screenshot(out_dir)

    actual = _actual_from_result(out_dir)
    row = {
        "scenario": scenario.name,
        "brief": scenario.brief,
        "expected": scenario.expected,
        "actual": actual,
        "exit_code": exit_code,
        "folder": scenario.name,
        "mode": "in-process" if scenario.operator else "cli",
        "screenshot": screenshot,
    }
    match = "ok " if actual == scenario.expected else "DIFF"
    print(f"  [{match}] {scenario.name:<26} exit={exit_code:<3} {actual}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="Run just this scenario; repeatable")
    parser.add_argument("--list", action="store_true", help="List scenario names and exit")
    args = parser.parse_args()

    if args.list:
        for scenario in SCENARIOS:
            print(scenario.name)
        return 0

    selected = SCENARIOS
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.name for s in SCENARIOS}
        if unknown:
            parser.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")
        selected = [s for s in SCENARIOS if s.name in wanted]

    app = start_app()
    rows = []
    try:
        print(f"\nRunning {len(selected)} scenario(s):")
        for scenario in selected:
            rows.append(run_scenario(scenario))
    finally:
        reset()
        if app is not None:
            app.terminate()
            app.wait(timeout=10)

    index_path = EVIDENCE / "index.json"
    if args.only and index_path.exists():
        # A partial run updates the rows it produced and leaves the rest,
        # so re-running one scenario can't silently truncate the index the
        # README is generated from.
        existing = {r["scenario"]: r for r in json.loads(index_path.read_text(encoding="utf-8"))["scenarios"]}
        existing.update({r["scenario"]: r for r in rows})
        rows = [existing[s.name] for s in SCENARIOS if s.name in existing]
    index_path.write_text(json.dumps({"scenarios": rows}, indent=2), encoding="utf-8")
    print(f"\nWrote {index_path}")

    mismatched = [r for r in rows if r["actual"] != next(s.expected for s in SCENARIOS if s.name == r["scenario"])]
    if mismatched:
        print("\nScenarios whose actual outcome differs from the expected one:")
        for row in mismatched:
            print(f"  {row['scenario']}: expected {row['expected']!r}, got {row['actual']!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
