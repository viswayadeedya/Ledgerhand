import pytest

from cua.guardrails import Action, ActionType, PolicyConfig, PolicyEngine, redact_text, redact_value
from cua.guardrails.redact import mask_partial, mask_sensitive, shape_of

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


# -- shape hints -----------------------------------------------------------


def test_shape_hint_keeps_a_wrong_shaped_value_visible_while_masked():
    """The reason shape hints exist. "***14" and "***18" are equally
    unreadable, so a masked record of the extra_row locator bug would hide
    the very thing it's evidence of. The shapes make it plain without
    either figure landing on disk.
    """
    wrong = mask_partial("2019-03-14", shape_hint=True)
    right = mask_partial("$2340.18", shape_hint=True)

    assert wrong == "***14 [shape: date]"
    assert right == "***18 [shape: money]"
    assert "2019-03" not in wrong  # the shape is published; the value is not
    assert "2340" not in right


def test_shape_hint_is_off_by_default():
    """Identity errors already say what was expected and what was seen; a
    shape there is noise. Callers that need it ask for it.
    """
    assert mask_partial("10001") == "***01"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("$2340.18", "money"),
        ("-$5.00", "money"),
        ("1,234.56", "money"),
        ("2019-03-14", "date"),
        ("3/14/2019", "date"),
        ("10001", "integer"),
        ("Maria Garcia", "text"),
        ("", "empty"),
        (None, "empty"),
    ],
)
def test_shape_of(value, expected):
    assert shape_of(value) == expected


def test_mask_sensitive_only_touches_the_named_values():
    masked = mask_sensitive(
        {"savings_balance": "$2340.18", "member_id": "10001"}, ["savings_balance"]
    )
    assert masked["savings_balance"] == "***18 [shape: money]"
    assert masked["member_id"] == "10001"
