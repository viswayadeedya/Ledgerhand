"""Same deterministic-exploration pattern as capture_business_outcome.py,
for the brief's "permission denial" runtime condition.

Why this is a business outcome and not a failure: the app understood the
request and answered it. The teller is signed in perfectly well and simply
isn't entitled to this record, which is something the caller needs told --
and retrying will not change it. The fake app serves it as a 403, in
contrast to `app_error`'s 500, and replay leans on exactly that
distinction: a 5xx is the app failing to answer, and is a hard failure no
matter what page comes with it.

Unlike the other two outcomes this one fires on the member *detail* page,
so the full step list runs -- there's no truncated prefix here.

Usage:
    python scripts/capture_permission_denied_outcome.py
"""

import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.recorder import add_business_outcome  # noqa: E402
from cua.artifacts.schema import load_yaml, to_yaml  # noqa: E402
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.replay.render import render_action  # noqa: E402
from cua.surface.playwright_surface import PlaywrightSurface  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "artifacts" / "member-savings-lookup.yaml"
MEMBER_ID = "10001"  # any valid ID; the fault decides entitlement, not the ID
SECRETS = {"username": "teller1", "password": "teller123"}


def _arm(domain: str, fault: str) -> None:
    req = urllib.request.Request(
        f"http://{domain}/admin/faults/api",
        data=json.dumps({"fault": fault, "armed": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)


def main() -> None:
    artifact = load_yaml(ARTIFACT_PATH.read_text(encoding="utf-8"))
    domain = artifact.target_domain
    _arm(domain, "permission_denied")

    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [domain]}))
    surface = PlaywrightSurface(
        base_url=f"http://{domain}",
        policy=policy,
        headless=True,
        evidence_dir=str(REPO_ROOT / "evidence" / "runs" / "capture-permission-denied"),
    )

    try:
        for idx, raw_action in enumerate(artifact.steps):
            action = render_action(raw_action, {"member_id": MEMBER_ID}, SECRETS)
            result = surface.act(action)
            if not result.success:
                raise RuntimeError(f"step {idx} failed: {result.error or result.policy_reason}")

        # The last step's own observation can be a beat ahead of the main
        # frame's cross-frame navigation finishing; take a fresh one.
        surface.page.wait_for_timeout(1000)
        observation = surface.observe()
        print("Captured page text:")
        for el in observation.elements:
            print(f"  [{el.frame}] {el.tag}: {el.text!r}")

        updated = add_business_outcome(
            artifact,
            observation,
            name="permission_denied",
            description="The signed-in teller is not authorized to view this member's record.",
            # The stable half of the sentence. The rest names the member,
            # and a locator built from that only ever matches this one ID
            # again -- the same lesson as member_not_found's detect text.
            detect_text="Access denied",
        )
    finally:
        surface.close()

    ARTIFACT_PATH.write_text(to_yaml(updated), encoding="utf-8")
    print(f"\nWrote {ARTIFACT_PATH} with business_outcomes={[o.name for o in updated.business_outcomes]}")


if __name__ == "__main__":
    main()
