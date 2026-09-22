from cua.core.models import Action, ActionType, LocatorCandidate, LocatorStrategy, Target
from tests.conftest import arm_fault as _arm_fault


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


def test_password_value_never_appears_in_observations(surface, fake_app_server):
    """Regression test: a real discovery run leaked the literal password
    into evidence via ActionResult.observation.elements[].text, populated by
    a DOM scan that read input[type=password]'s .value directly -- browsers
    only mask the password visually, the DOM value is always plaintext. The
    action's own recorded input can be redacted all day; if the *observation*
    taken right after re-scans the live DOM, it has to mask password values
    at the source, not rely on anyone downstream remembering to redact.
    """
    r = surface.act(Action(type=ActionType.NAVIGATE, url=f"{fake_app_server}/login"))
    assert r.success

    fill_result = surface.act(
        Action(type=ActionType.FILL, target=_css_target('input[name="password"]'), value="teller123")
    )
    assert fill_result.success
    assert fill_result.observation is not None
    for element in fill_result.observation.elements:
        assert "teller123" not in (element.text or "")
        assert "teller123" not in (element.name or "")

    obs = surface.observe()
    for element in obs.elements:
        assert "teller123" not in (element.text or "")


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
