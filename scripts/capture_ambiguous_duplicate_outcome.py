"""Same deterministic-exploration pattern as capture_business_outcome.py,
for a second, more interesting outcome: an ID search that comes back with
two conflicting records (the fake app's `duplicate_members` fault -- a
stand-in for a real data-quality issue, e.g. a merged/duplicate account).

This one is marked requires_human=True when attached: picking the wrong
record in a banking context is exactly the kind of guess automation
shouldn't make on its own (see Part 1's DECISIONS.md). Part 7's replay
engine escalates it to a human instead of silently reporting it or picking
one arbitrarily.

Usage:
    python scripts/capture_ambiguous_duplicate_outcome.py
"""

import json
import sys
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.recorder import add_business_outcome  # noqa: E402
from cua.artifacts.schema import CapabilityArtifact  # noqa: E402
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.replay.render import render_action  # noqa: E402
from cua.surface.playwright_surface import PlaywrightSurface  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"
MEMBER_ID = "10001"  # any valid ID triggers the fault; the fault, not the ID, causes the ambiguity
SECRETS = {"username": "teller1", "password": "teller123"}


def _arm_duplicate_fault(domain: str) -> None:
    req = urllib.request.Request(
        f"http://{domain}/admin/faults/api",
        data=json.dumps({"fault": "duplicate_members", "armed": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)


def main() -> None:
    artifact = CapabilityArtifact.model_validate(yaml.safe_load(ARTIFACT_PATH.read_text(encoding="utf-8")))
    domain = artifact.target_domain
    _arm_duplicate_fault(domain)

    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [domain]}))
    surface = PlaywrightSurface(
        base_url=f"http://{domain}",
        policy=policy,
        headless=True,
        evidence_dir=str(REPO_ROOT / "evidence" / "runs" / "capture-ambiguous-duplicate"),
    )

    try:
        # Login + search prefix only -- same reasoning as
        # capture_business_outcome.py: there's no single "View" link when
        # the search returns two conflicting rows instead of one.
        prefix = artifact.steps[:6]
        for idx, raw_action in enumerate(prefix):
            action = render_action(raw_action, {"member_id": MEMBER_ID}, SECRETS)
            result = surface.act(action)
            if not result.success:
                raise RuntimeError(f"prefix step {idx} failed: {result.error or result.policy_reason}")

        surface.page.wait_for_timeout(1000)
        observation = surface.observe()
        print("Captured page text:")
        for el in observation.elements:
            print(f"  [{el.frame}] {el.tag}: {el.text!r}")

        updated = add_business_outcome(
            artifact,
            observation,
            name="ambiguous_duplicate",
            description="More than one member record matched; automation should not guess which one.",
            detect_text="Multiple members matched",
            requires_human=True,
        )
    finally:
        surface.close()

    ARTIFACT_PATH.write_text(
        yaml.safe_dump(updated.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nWrote {ARTIFACT_PATH} with business_outcomes={[o.name for o in updated.business_outcomes]}")


if __name__ == "__main__":
    main()
