import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

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


def _arm_fault(base_url: str, fault: str, armed: bool) -> None:
    req = urllib.request.Request(
        f"{base_url}/admin/faults/api",
        data=json.dumps({"fault": fault, "armed": armed}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)


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


def _css_target(css: str, frame: str | None = None) -> Target:
    return Target(frame=frame, candidates=[LocatorCandidate(strategy=LocatorStrategy.CSS, value=css)])


def _role_target(role: str, name: str, frame: str | None = None) -> Target:
    return Target(frame=frame, candidates=[LocatorCandidate(strategy=LocatorStrategy.ROLE, role=role, value=name)])


def _login(surface, fake_app_server) -> None:
    r = surface.act(Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/login"))
    assert r.success, r.error
    r = surface.act(Action(type=ActionType.FILL, target=_css_target('input[name="username"]'), value="teller1"))
    assert r.success, r.error
    r = surface.act(Action(type=ActionType.FILL, target=_css_target('input[name="password"]'), value="teller123"))
    assert r.success, r.error
    r = surface.act(Action(type=ActionType.CLICK, target=_role_target("button", "Sign On")))
    assert r.success, r.error


def test_login_search_and_view_member(surface, fake_app_server):
    _login(surface, fake_app_server)
    assert "/app" in surface.page.url

    obs = surface.observe()
    assert "nav" in obs.frames
    assert "main" in obs.frames

    r = surface.act(
        Action(type=ActionType.FILL, target=_css_target('input[name="q"]', frame="nav"), value="10001")
    )
    assert r.success, r.error

    r = surface.act(Action(type=ActionType.CLICK, target=_role_target("button", "Search", frame="nav")))
    assert r.success, r.error

    r = surface.act(
        Action(
            type=ActionType.CLICK,
            target=Target(frame="main", candidates=[LocatorCandidate(strategy=LocatorStrategy.TEXT, value="View")]),
        )
    )
    assert r.success, r.error

    main_frame = surface.page.frame(name="main")
    assert "Maria" in main_frame.content()
    assert "2340.18" in main_frame.content()


def test_disallowed_route_is_blocked_before_navigating(surface, fake_app_server):
    r = surface.act(Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/admin/faults"))
    assert r.blocked is True
    assert r.success is False


def test_risky_commit_is_blocked_then_allowed_with_human_approval(surface, fake_app_server):
    _login(surface, fake_app_server)
    r = surface.act(Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/app/member/10001/new-subaccount"))
    assert r.success, r.error

    r = surface.act(Action(type=ActionType.FILL, target=_css_target("#initial_deposit"), value="100"))
    assert r.success, r.error

    r = surface.act(Action(type=ActionType.CLICK, target=_role_target("button", "Continue")))
    assert r.success, r.error
    assert "Confirm New Sub-Account" in surface.page.content()

    blocked = surface.act(
        Action(type=ActionType.CLICK, target=_role_target("button", "Confirm & Open Account"))
    )
    assert blocked.success is False
    assert blocked.blocked is True
    assert "Confirm New Sub-Account" in surface.page.content()  # never left the review screen

    approved = surface.act(
        Action(type=ActionType.CLICK, target=_role_target("button", "Confirm & Open Account")),
        human_approved=True,
    )
    assert approved.success is True
    assert "opened" in surface.page.content()


def test_popup_dialog_is_observed_then_dismissed(surface, fake_app_server):
    _arm_fault(fake_app_server, "popup", True)
    _login(surface, fake_app_server)

    r = surface.act(Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/app/member/10001"))
    assert r.success, r.error
    surface.page.wait_for_timeout(300)

    obs = surface.observe()
    assert obs.dialog_message is not None

    r = surface.act(Action(type=ActionType.DISMISS_DIALOG, value="accept"))
    assert r.success, r.error

    obs2 = surface.observe()
    assert obs2.dialog_message is None
