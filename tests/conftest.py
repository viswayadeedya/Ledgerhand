import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from cua.artifacts.schema import CapabilityArtifact
from cua.core.models import Action, ActionType, LocatorCandidate, LocatorStrategy, Target
from cua.guardrails.policy import PolicyConfig, PolicyEngine
from cua.surface.playwright_surface import PlaywrightSurface

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def fake_app_server():
    port = _free_port()
    env = os.environ.copy()
    env["FAKE_APP_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "cua.fake_app"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base_url}/login", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("fake app did not start in time")
    yield base_url
    proc.terminate()
    proc.wait(timeout=5)


def arm_fault(base_url: str, fault: str, armed: bool) -> None:
    req = urllib.request.Request(
        f"{base_url}/admin/faults/api",
        data=json.dumps({"fault": fault, "armed": armed}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)


def reset_app(base_url: str) -> None:
    """Puts member data, faults and settings back to their seeded state.

    Needed because `fake_app_server` is module-scoped: tests share one live
    app, so a test that opens a sub-account changes what the *next* one
    sees. Any test asserting on state a previous test can have written has
    to establish its own preconditions rather than inherit them.
    """
    urllib.request.urlopen(urllib.request.Request(f"{base_url}/admin/reset", method="POST"))


def set_setting(base_url: str, name: str, value: float) -> None:
    req = urllib.request.Request(
        f"{base_url}/admin/settings/api",
        data=json.dumps({"name": name, "value": value}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)


def risky_artifact_for(base_url: str) -> "CapabilityArtifact":
    """A small synthetic artifact pointed straight at the sub-account commit
    route. Not the member-lookup capability -- its only job is to reach an
    irreversible action so guardrail blocking can be exercised. Shared so
    the engine test and the CLI exit-code test provoke NEEDS_HUMAN the same
    way rather than drifting apart.
    """
    return CapabilityArtifact(
        id="risky-test",
        title="risky test",
        description="Directly submits the sub-account commit form -- for testing guardrail blocking only.",
        target_domain=base_url.replace("http://", ""),
        entry_url=f"{base_url}/login",
        secrets=[{"name": "username"}, {"name": "password"}],
        steps=[
            Action(type=ActionType.NAVIGATE, url=f"{base_url}/login"),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value='input[name="username"]')]),
                value="{{secrets.username}}",
            ),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value='input[name="password"]')]),
                value="{{secrets.password}}",
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Sign On")]),
            ),
            Action(type=ActionType.NAVIGATE, url=f"{base_url}/app/member/10001/new-subaccount"),
            Action(
                type=ActionType.FILL,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value="#initial_deposit")]),
                value="100",
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Continue")]),
            ),
            Action(
                type=ActionType.CLICK,
                target=Target(
                    candidates=[
                        LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Confirm & Open Account")
                    ]
                ),
            ),
        ],
        checkpoint=Target(candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value="opened")]),
        checkpoint_description="Sub-account opened confirmation is shown.",
        provenance={"discovered_at": "2026-01-01T00:00:00Z", "discovery_model": "manual-test"},
    )


@pytest.fixture
def test_policy(fake_app_server) -> PolicyEngine:
    domain = fake_app_server.replace("http://", "")
    cfg = PolicyConfig.load().model_copy(update={"allowed_domains": [domain]})
    return PolicyEngine(cfg)


@pytest.fixture
def surface(fake_app_server, test_policy, tmp_path):
    s = PlaywrightSurface(base_url=fake_app_server, policy=test_policy, headless=True, evidence_dir=tmp_path)
    yield s
    s.close()
