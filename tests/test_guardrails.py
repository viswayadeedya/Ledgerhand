import pytest

from cua.guardrails import Action, ActionType, PolicyConfig, PolicyEngine, redact_text, redact_value

BASE_URL = "http://127.0.0.1:5055"


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine(PolicyConfig.load())


def test_safe_allowed_action_passes(engine: PolicyEngine):
    action = Action(type=ActionType.CLICK, url=f"{BASE_URL}/app/member/10001", description="view member")
    decision = engine.evaluate(action)
    assert decision.allowed is True
    assert decision.risk == "safe"


def test_disallowed_domain_is_blocked(engine: PolicyEngine):
    action = Action(type=ActionType.NAVIGATE, url="https://evil.example.com/steal")
    decision = engine.evaluate(action)
    assert decision.allowed is False
    assert "domain" in decision.reason


def test_disallowed_route_is_blocked(engine: PolicyEngine):
    action = Action(type=ActionType.NAVIGATE, url=f"{BASE_URL}/admin/faults")
    decision = engine.evaluate(action)
    assert decision.allowed is False
    assert "route" in decision.reason


def test_disallowed_action_type_is_blocked(engine: PolicyEngine):
    action = Action(type=ActionType.NAVIGATE, url=f"{BASE_URL}/app")
    # Build a config that doesn't permit navigate at all.
    cfg = engine.config.model_copy(update={"allowed_action_types": [ActionType.CLICK]})
    restricted = PolicyEngine(cfg)
    decision = restricted.evaluate(action)
    assert decision.allowed is False
    assert "action type" in decision.reason


def test_risky_route_is_blocked_by_default(engine: PolicyEngine):
    action = Action(
        type=ActionType.SUBMIT,
        url=f"{BASE_URL}/app/member/10001/new-subaccount/commit",
        method="POST",
    )
    decision = engine.evaluate(action)
    assert decision.allowed is False
    assert decision.risk == "risky"
    assert decision.requires_confirmation is True


def test_risky_route_allowed_with_human_approval(engine: PolicyEngine):
    action = Action(
        type=ActionType.SUBMIT,
        url=f"{BASE_URL}/app/member/10001/new-subaccount/commit",
        method="POST",
    )
    decision = engine.evaluate(action, human_approved=True)
    assert decision.allowed is True
    assert decision.risk == "risky"
    assert decision.requires_confirmation is False


def test_action_with_no_url_is_allowed_if_type_is_allowed(engine: PolicyEngine):
    action = Action(type=ActionType.WAIT)
    decision = engine.evaluate(action)
    assert decision.allowed is True


def test_redact_value_masks_sensitive_keys():
    payload = {
        "username": "teller1",
        "password": "teller123",
        "session": "abc123",
        "nested": {"api_key": "sk-ant-xyz", "member_id": 10001},
    }
    redacted = redact_value(payload)
    assert redacted["username"] == "teller1"
    assert redacted["password"] == "***REDACTED***"
    assert redacted["session"] == "***REDACTED***"
    assert redacted["nested"]["api_key"] == "***REDACTED***"
    assert redacted["nested"]["member_id"] == 10001


def test_redact_text_masks_ssn_pattern():
    text = "Member SSN on file: 123-45-6789, please verify."
    assert "123-45-6789" not in redact_text(text)
