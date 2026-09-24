"""Builds the sub-account-open capability by driving the flow for real,
deterministically, with no LLM.

Why not a second discovery run: the brief requires *one* genuine
LLM-driven run and that one exists (Part 4, evidence/discovery-member-lookup/).
This capability's job is to reach a guardrail-blocked irreversible action,
which discovery cannot do unattended -- the commit is refused by policy and
the model has no handoff path during discovery, so the run would stall at
exactly the step the artifact needs to record. Re-running the model to
re-derive a flow already known from building the app would also be cost for
no information.

What is and isn't derived, stated plainly so nobody mistakes this for
discovery:
  - Hand-chosen here: the step order and each step's ranked locator
    candidates. They are hand-chosen but not hand-waved -- every one is
    resolved against the live page during capture under the same strict
    "exactly one match or fail" rule replay uses, so a candidate that
    doesn't really work fails this script rather than shipping.
  - Derived by the recorder, exactly as for a discovered artifact: the
    parameterization, the URL canonicalization, the per-step descriptions,
    the risk labels (from the destinations actually observed, through the
    real PolicyEngine), the output locators and the checkpoint -- all built
    from RecordedSteps carrying real captured Observations.

The commit step is executed with human_approved=True. That is the whole
point of the capability: the artifact has to contain a step that policy
blocks, so replay can then demonstrate the block, the escalation, and the
human approval that releases it.

Usage:
    python scripts/capture_subaccount_capability.py
"""

import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.artifacts.recorder import build_artifact  # noqa: E402
from cua.artifacts.schema import to_yaml  # noqa: E402
from cua.core.models import (  # noqa: E402
    Action,
    ActionType,
    LocatorCandidate,
    LocatorStrategy,
    RecordedStep,
    Target,
)
from cua.guardrails.policy import PolicyConfig, PolicyEngine  # noqa: E402
from cua.surface.playwright_surface import PlaywrightSurface  # noqa: E402

OUT_PATH = REPO_ROOT / "artifacts" / "member-subaccount-open.yaml"
DOMAIN = "127.0.0.1:5055"

MEMBER_ID = "10001"
ACCOUNT_TYPE = "checking"
INITIAL_DEPOSIT = "250.00"
SECRETS = {"username": "teller1", "password": "teller123"}


def _role(name: str, role: str) -> LocatorCandidate:
    return LocatorCandidate(strategy=LocatorStrategy.ROLE, value=name, role=role)


def _css(selector: str) -> LocatorCandidate:
    return LocatorCandidate(strategy=LocatorStrategy.CSS, value=selector)


def _label(text: str) -> LocatorCandidate:
    return LocatorCandidate(strategy=LocatorStrategy.LABEL, value=text)


def _text(value: str) -> LocatorCandidate:
    return LocatorCandidate(strategy=LocatorStrategy.TEXT, value=value)


def _plan(base_url: str) -> list[Action]:
    """The flow a teller actually performs, through the app's own screens.

    Deliberately not a direct navigate to the sub-account form: going via
    search and the member page means the artifact crosses frames the way
    the real task does, and the recorder gets genuine before/after
    observations to judge each step's destination from.
    """
    return [
        Action(type=ActionType.NAVIGATE, url=f"{base_url}/login"),
        Action(
            type=ActionType.FILL,
            target=Target(candidates=[_css('input[name="username"]')]),
            value=SECRETS["username"],
        ),
        Action(
            type=ActionType.FILL,
            target=Target(candidates=[_css('input[name="password"]')]),
            value=SECRETS["password"],
        ),
        Action(type=ActionType.CLICK, target=Target(candidates=[_role("Sign On", "button"), _css('input[value="Sign On"]')])),
        Action(
            type=ActionType.FILL,
            target=Target(frame="nav", candidates=[_css('input[name="q"]')]),
            value=MEMBER_ID,
        ),
        Action(
            type=ActionType.CLICK,
            target=Target(frame="nav", candidates=[_role("Search", "button"), _css('input[value="Search"]')]),
        ),
        Action(
            type=ActionType.CLICK,
            target=Target(frame="main", candidates=[_role("View", "link"), _text("View")]),
        ),
        Action(
            type=ActionType.CLICK,
            target=Target(
                frame="main",
                candidates=[_role("Open New Sub-Account", "link"), _text("Open New Sub-Account")],
            ),
        ),
        # The form uses real <label for> pairs (Part 1 made the locator
        # surface deliberately inconsistent), so these get the label
        # strategy rather than falling back to a selector.
        Action(
            type=ActionType.SELECT,
            target=Target(frame="main", candidates=[_label("Account Type:"), _css("#account_type")]),
            value=ACCOUNT_TYPE,
        ),
        Action(
            type=ActionType.FILL,
            target=Target(frame="main", candidates=[_label("Initial Deposit ($):"), _css("#initial_deposit")]),
            value=INITIAL_DEPOSIT,
        ),
        Action(
            type=ActionType.CLICK,
            target=Target(frame="main", candidates=[_role("Continue", "button"), _css('input[value="Continue"]')]),
        ),
        # The irreversible one. Blocked by policy for every caller that
        # hasn't been explicitly approved -- including this script, which
        # passes human_approved only for the capture itself.
        Action(
            type=ActionType.CLICK,
            target=Target(
                frame="main",
                candidates=[_role("Confirm & Open Account", "button"), _css('input[value="Confirm & Open Account"]')],
            ),
        ),
    ]


def _reset(domain: str) -> None:
    urllib.request.urlopen(urllib.request.Request(f"http://{domain}/admin/reset", method="POST"))


def main() -> None:
    base_url = f"http://{DOMAIN}"
    _reset(DOMAIN)

    policy = PolicyEngine(PolicyConfig.load().model_copy(update={"allowed_domains": [DOMAIN]}))
    surface = PlaywrightSurface(
        base_url=base_url,
        policy=policy,
        headless=True,
        evidence_dir=str(REPO_ROOT / "evidence" / "runs" / "capture-subaccount"),
    )

    steps: list[RecordedStep] = []
    try:
        for idx, action in enumerate(_plan(base_url)):
            result = surface.act(action, human_approved=True)
            if not result.success:
                raise RuntimeError(f"step {idx} ({action.type.value}) failed: {result.error or result.policy_reason}")
            steps.append(RecordedStep(index=idx, tool_name="deterministic_capture", action=action, result=result))

        # A final observation after the cross-frame navigation has settled.
        # READ isn't a replayable type, so this contributes its observation
        # (which the outputs and checkpoint are built from) without adding
        # a step to the artifact.
        surface.page.wait_for_timeout(1000)
        settled = surface.act(Action(type=ActionType.READ))
        steps.append(RecordedStep(index=len(steps), tool_name="settle", action=None, result=settled))

        print("Captured final page:")
        for el in settled.observation.elements:
            if el.frame == "main":
                print(f"  [{el.frame}] {el.tag} r{el.table_row}c{el.table_col}: {el.text!r}")

        artifact = build_artifact(
            steps,
            capability_id="member-subaccount-open",
            title="Open a new sub-account for a member",
            description=(
                "Signs in to the teller system, finds a member, opens a new sub-account of the "
                "requested type with the requested initial deposit, and confirms it on the "
                "success screen. The final commit is an irreversible action and is blocked by "
                "policy unless a human approves it."
            ),
            target_domain=DOMAIN,
            entry_url=f"{base_url}/login",
            discovery_model="deterministic-capture (no LLM; see scripts/capture_subaccount_capability.py)",
            inputs={
                "member_id": MEMBER_ID,
                "account_type": ACCOUNT_TYPE,
                "initial_deposit": INITIAL_DEPOSIT,
            },
            input_descriptions={
                "member_id": "The member the sub-account is opened for.",
                "account_type": "One of savings, checking, money_market.",
                "initial_deposit": "Opening deposit, at least 25.00.",
            },
            input_patterns={
                "member_id": r"^[0-9]{5}$",
                "account_type": r"^(savings|checking|money_market)$",
                # The app enforces a 25.00 minimum itself and answers with a
                # validation page; this only rejects what isn't an amount at
                # all. A pattern that also encoded the minimum would be this
                # artifact quietly asserting a business rule it doesn't own.
                "initial_deposit": r"^[0-9]+\.[0-9]{2}$",
            },
            input_examples={
                "member_id": "00000",
                "account_type": "savings",
                "initial_deposit": "25.00",
            },
            sensitive_inputs=["member_id"],
            secret_names=["username", "password"],
            # Both, not just the username. A discovery run gets away with
            # declaring only the username because the browser tools redact
            # the password to its placeholder before it is ever recorded;
            # this script drives the surface directly, where nothing does.
            # Omitting it put the live credential in the artifact, in the
            # step value and in the generated description -- build_artifact
            # now refuses that rather than leaving it to be noticed.
            secret_values=SECRETS,
            output_values={
                "member_id": MEMBER_ID,
                "sub_account_number": "1",
                "account_type": ACCOUNT_TYPE,
                "initial_deposit": "$250.00",
            },
            output_descriptions={
                "member_id": "The member the sub-account was opened for. Asserted equal to the requested one.",
                "sub_account_number": "The number assigned to the new sub-account.",
                "account_type": "The type actually opened, as confirmed by the app.",
                "initial_deposit": "The deposit actually recorded, as confirmed by the app.",
            },
            # The identity check matters more here than on a read: this
            # capability *changes* something, and opening an account on the
            # wrong member is not a mistake a later read can undo.
            output_assertions={"member_id": "{{inputs.member_id}}"},
            output_types={"sub_account_number": "integer", "initial_deposit": "money"},
            sensitive_outputs=["member_id", "sub_account_number", "initial_deposit"],
            checkpoint_text="opened",
            version=1,
            policy=policy,
        )
    finally:
        surface.close()
        _reset(DOMAIN)  # the capture really did open an account; put the app back

    OUT_PATH.write_text(to_yaml(artifact), encoding="utf-8")
    risky = [s for s in artifact.steps if s.risk and s.risk.value == "risky"]
    print(f"\nWrote {OUT_PATH} ({len(artifact.steps)} steps, {len(artifact.outputs)} outputs)")
    print(f"Steps labelled risky: {len(risky)}")
    for step in risky:
        print(f"  - {step.description} ({step.risk_note})")


if __name__ == "__main__":
    main()
