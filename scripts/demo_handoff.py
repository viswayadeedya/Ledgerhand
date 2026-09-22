"""Produces real, tracked evidence for Part 7 (human handoff) against the
actual committed artifact and the live fake app.

Uses MockOperatorHandoff rather than TerminalOperatorHandoff/
InteractivePauseHandoff because this script runs unattended -- no person is
sitting at a keyboard or clicking "Resume" in a GUI to produce this
evidence. The control-transfer mechanism itself is identical either way
(escalate() gets the same live surface, the run genuinely pauses until it
returns); only how the decision gets made differs. See DECISIONS.md Part 7
and cua/handoff/mock.py's own docstring.

Two runs, both against the artifact's real "ambiguous_duplicate" outcome
(member 10001 with the duplicate_members fault armed):
1. abandoned/   -- operator declines -> NEEDS_HUMAN, run stops cleanly.
2. resolved/    -- operator acts directly on the SAME live session (clicks
                    a real "View" link) -> RECOVERED, with the correct
                    member's real balance read afterward.

Usage:
    python scripts/demo_handoff.py
"""

import json
import sys
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.schema import CapabilityArtifact  # noqa: E402
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.handoff import EscalationRequest, HandoffAction, HandoffDecision, MockOperatorHandoff  # noqa: E402
from cua.replay.engine import ReplayEngine  # noqa: E402
from cua.replay.models import ReplayOutcome  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"
SECRETS = {"username": "teller1", "password": "teller123"}
EVIDENCE_ROOT = REPO_ROOT / "evidence" / "handoff-ambiguous-duplicate"


def _reset_and_arm(domain: str) -> None:
    urllib.request.urlopen(urllib.request.Request(f"http://{domain}/admin/reset", method="POST"))
    urllib.request.urlopen(
        urllib.request.Request(
            f"http://{domain}/admin/faults/api",
            data=json.dumps({"fault": "duplicate_members", "armed": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    )


def _write_result(out_dir: Path, result) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")


def run_abandoned(artifact: CapabilityArtifact, policy: PolicyEngine) -> None:
    out_dir = EVIDENCE_ROOT / "abandoned"

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        return HandoffDecision(
            action=HandoffAction.ABANDON,
            operator_note="Demo: operator unavailable, declining to guess which record is correct.",
        )

    _reset_and_arm(artifact.target_domain)
    engine = ReplayEngine(policy=policy, headless=True, evidence_dir=out_dir, handoff=MockOperatorHandoff(operator, out_dir))
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    _write_result(out_dir, result)
    print(f"[abandoned] outcome={result.outcome.value} escalations={len(result.escalations)}")
    assert result.outcome == ReplayOutcome.NEEDS_HUMAN


def run_resolved(artifact: CapabilityArtifact, policy: PolicyEngine) -> None:
    out_dir = EVIDENCE_ROOT / "resolved"

    def operator(request: EscalationRequest, surface) -> HandoffDecision:
        # Acting on `surface` -- the SAME live session replay was already
        # on, exactly what a human taking over the browser would do.
        main_frame = surface.page.frame(name="main")
        assert main_frame is not None
        main_frame.get_by_role("link", name="View").first.click()
        return HandoffDecision(
            action=HandoffAction.MANUAL_RESOLVED,
            operator_note="Demo: operator reviewed both records and selected the first (non-duplicate) one.",
        )

    _reset_and_arm(artifact.target_domain)
    engine = ReplayEngine(policy=policy, headless=True, evidence_dir=out_dir, handoff=MockOperatorHandoff(operator, out_dir))
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)
    _write_result(out_dir, result)
    print(f"[resolved]  outcome={result.outcome.value} outputs={result.outputs}")
    assert result.outcome == ReplayOutcome.RECOVERED
    assert result.outputs["savings_balance"] == "$2340.18"


def main() -> None:
    artifact = CapabilityArtifact.model_validate(yaml.safe_load(ARTIFACT_PATH.read_text(encoding="utf-8")))
    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [artifact.target_domain]}))

    run_abandoned(artifact, policy)
    run_resolved(artifact, policy)
    print(f"\nEvidence written under {EVIDENCE_ROOT}")


if __name__ == "__main__":
    main()
