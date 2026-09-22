"""Captures a real page state for a known failure/business-outcome path and
attaches it to an existing artifact via add_business_outcome().

This is deterministic exploration, not LLM discovery: we already know (from
building the fake app in Part 1) that searching a member ID with no match
lands on a "No member found" page. An LLM re-discovering that fact each time
would be needless cost for something this project's own author already
knows -- but the resulting locator still has to come from a REAL captured
Observation, the same as everything else in an artifact, not from memory of
what the template says. This script drives the artifact's own login+search
step prefix (reusing render_action, the exact same machinery replay uses)
with a deliberately-nonexistent member ID, and captures what's really there.

Usage:
    python scripts/capture_business_outcome.py
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.recorder import add_business_outcome  # noqa: E402
from cua.artifacts.schema import CapabilityArtifact  # noqa: E402
from cua.guardrails.policy import PolicyEngine, PolicyConfig  # noqa: E402
from cua.replay.render import render_action  # noqa: E402
from cua.surface.playwright_surface import PlaywrightSurface  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"
NONEXISTENT_MEMBER_ID = "99999"
SECRETS = {"username": "teller1", "password": "teller123"}


def main() -> None:
    artifact = CapabilityArtifact.model_validate(yaml.safe_load(ARTIFACT_PATH.read_text(encoding="utf-8")))

    domain = artifact.target_domain
    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [domain]}))
    surface = PlaywrightSurface(
        base_url=f"http://{domain}",
        policy=policy,
        headless=True,
        evidence_dir=str(REPO_ROOT / "evidence" / "runs" / "capture-not-found"),
    )

    try:
        # Login + search prefix only -- the "click View" step doesn't exist
        # on a not-found results page, so we stop right before it.
        prefix = artifact.steps[:6]
        for idx, raw_action in enumerate(prefix):
            action = render_action(raw_action, {"member_id": NONEXISTENT_MEMBER_ID}, SECRETS)
            result = surface.act(action)
            if not result.success:
                raise RuntimeError(f"prefix step {idx} failed: {result.error or result.policy_reason}")

        # The last step's own observation can be a beat ahead of the "main"
        # frame's cross-frame navigation actually finishing -- take a fresh
        # one after a short settle instead of trusting that immediate one.
        surface.page.wait_for_timeout(1000)
        observation = surface.observe()
        print("Captured page text:")
        for el in observation.elements:
            print(f"  [{el.frame}] {el.tag}: {el.text!r}")

        updated = add_business_outcome(
            artifact,
            observation,
            name="member_not_found",
            description="No member exists with the given ID.",
            detect_text="No member found",
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
