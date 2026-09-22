import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

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
