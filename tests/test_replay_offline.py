"""Replay must not call the LLM. Proved by taking the LLM away.

The brief's central claim is that discovery needs the model and replay
does not -- "reliably and cheaply, without re-reasoning about the UI every
time". That claim has been asserted in prose and by reading the imports;
neither survives someone adding a convenience call later. These tests break
the thing that would have to work.

Two independent checks, because each misses what the other catches:

  Blocking the network proves no call is *made*. It would still pass if the
  SDK were imported and merely unused, and it is the check that fails the
  day someone adds an LLM fallback to a failing step.

  Asserting the module is never imported proves the dependency isn't in
  the replay path at all. It would still pass if a call were made through
  something other than the SDK, and it is the check that catches an import
  creeping in before it grows a call site.
"""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from cua.artifacts.schema import load_yaml
from cua.replay.engine import ReplayEngine
from cua.replay.models import ReplayOutcome
from tests.test_replay import ARTIFACT_PATH, _retarget

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRETS = {"username": "teller1", "password": "teller123"}

BLOCKED_HOSTS = ("anthropic.com", "api.anthropic.com")


class AnthropicWasCalled(AssertionError):
    """Raised at the socket layer, so it names the violation rather than
    surfacing as some connection error a caller might reasonably retry.
    """


@pytest.fixture
def anthropic_unreachable(monkeypatch):
    """Makes any attempt to reach Anthropic fail loudly, at DNS.

    Everything else keeps working -- the fake app is on 127.0.0.1 and
    Playwright talks to its driver over a local socket -- so a failure here
    can only mean replay tried to reach the model.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def guard(host):
        if host and any(blocked in str(host).lower() for blocked in BLOCKED_HOSTS):
            raise AnthropicWasCalled(
                f"replay tried to reach {host!r}. Deterministic replay must not call the LLM: "
                "every decision comes from the artifact on disk."
            )

    def fake_getaddrinfo(host, *args, **kwargs):
        guard(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def fake_create_connection(address, *args, **kwargs):
        guard(address[0] if address else None)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    # A key that would fail anyway, so a passing run can't be explained by
    # a real one sitting in the environment.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key-for-tests")


def test_the_guard_itself_blocks_anthropic(anthropic_unreachable):
    """The other tests pass trivially if this fixture does nothing."""
    with pytest.raises(AnthropicWasCalled):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_the_guard_leaves_the_fake_app_reachable(anthropic_unreachable, fake_app_server):
    socket.getaddrinfo("127.0.0.1", 80)  # must not raise


def test_replay_succeeds_with_anthropic_unreachable(
    anthropic_unreachable, fake_app_server, test_policy, tmp_path
):
    """A full, real replay -- browser, frames, locators, checkpoint,
    outputs -- with the model unreachable.
    """
    artifact = _retarget(
        load_yaml(ARTIFACT_PATH.read_text(encoding="utf-8")),
        fake_app_server.replace("http://", ""),
    )
    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)
    result = engine.run(artifact, inputs={"member_id": "10001"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.SUCCESS
    assert result.outputs["savings_balance"] == "$2340.18"
    assert result.steps_executed == len(artifact.steps)


def test_a_business_outcome_also_needs_no_model(
    anthropic_unreachable, fake_app_server, test_policy, tmp_path
):
    """Not just the happy path. Recognising "no such member" is exactly the
    sort of judgement someone might reach for a model to make, and it is
    made from the artifact's own detect locator.
    """
    artifact = _retarget(
        load_yaml(ARTIFACT_PATH.read_text(encoding="utf-8")),
        fake_app_server.replace("http://", ""),
    )
    engine = ReplayEngine(policy=test_policy, headless=True, evidence_dir=tmp_path)
    result = engine.run(artifact, inputs={"member_id": "99999"}, secrets=SECRETS)

    assert result.outcome == ReplayOutcome.BUSINESS_OUTCOME
    assert result.business_outcome == "member_not_found"


def test_importing_the_replay_cli_does_not_import_the_anthropic_sdk():
    """Run in a fresh interpreter on purpose: by this point in a full test
    session another module may well have imported `anthropic`, and asking
    `sys.modules` in-process would then answer about the session rather
    than about replay.
    """
    code = (
        "import sys;"
        "import cua.replay.__main__;"
        "leaked = [m for m in sys.modules if m.split('.')[0] == 'anthropic'];"
        "print(','.join(leaked))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", (
        f"importing the replay CLI pulled in {completed.stdout.strip()}. "
        "Replay must not depend on the model SDK, even unused."
    )


def test_the_discovery_path_does_import_it():
    """The control. Without this, the test above would keep passing if the
    SDK were removed from the project altogether, and would have stopped
    saying anything about replay.
    """
    code = (
        "import sys;"
        "import cua.agent.discovery;"
        "print(any(m.split('.')[0] == 'anthropic' for m in sys.modules))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "True"
