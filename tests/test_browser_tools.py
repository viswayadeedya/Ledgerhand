from cua.agent.browser_tools import BrowserToolExecutor
from tests.conftest import arm_fault as _arm_fault


def _login(executor: BrowserToolExecutor, fake_app_server: str) -> None:
    content, is_error = executor.dispatch("navigate", {"url": f"{fake_app_server}/login"})
    assert not is_error, content

    content, is_error = executor.dispatch("read_page", {})
    assert not is_error
    assert "textbox" in content  # username/password inputs, surfaced with no label -> CSS-backed refs

    # Find the two text inputs and the submit button by scanning read_page's
    # output for their refs (order isn't guaranteed to be stable by name).
    lines = content.splitlines()
    textbox_refs = [line.split("[")[-1].rstrip("]") for line in lines if line.startswith("textbox")]
    assert len(textbox_refs) == 2

    content, is_error = executor.dispatch(
        "form_input", {"target": {"type": "ref", "ref": textbox_refs[0]}, "value": "teller1"}
    )
    assert not is_error, content
    content, is_error = executor.dispatch(
        "form_input", {"target": {"type": "ref", "ref": textbox_refs[1]}, "value": "teller123"}
    )
    assert not is_error, content

    find_result, is_error = executor.dispatch("find", {"query": "sign on button"})
    assert not is_error
    button_ref = find_result.split("[")[-1].rstrip("]")

    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": button_ref}})
    assert not is_error, content
    assert "/app" in executor.surface.page.url


def test_read_page_lists_frames_then_drills_in(surface, fake_app_server):
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    top, is_error = executor.dispatch("read_page", {})
    assert not is_error
    assert 'frame "nav"' in top
    assert 'frame "main"' in top

    nav_ref = [l for l in top.splitlines() if 'frame "nav"' in l][0].split("[")[-1].rstrip("]")
    nav_content, is_error = executor.dispatch("read_page", {"ref": nav_ref})
    assert not is_error
    assert "textbox" in nav_content
    assert 'button "Search"' in nav_content


def test_find_and_click_reach_member_detail(surface, fake_app_server):
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    top, _ = executor.dispatch("read_page", {})
    nav_ref = [l for l in top.splitlines() if 'frame "nav"' in l][0].split("[")[-1].rstrip("]")
    nav_content, _ = executor.dispatch("read_page", {"ref": nav_ref})
    search_box_ref = [l for l in nav_content.splitlines() if l.startswith("textbox")][0].split("[")[-1].rstrip("]")

    content, is_error = executor.dispatch(
        "form_input", {"target": {"type": "ref", "ref": search_box_ref}, "value": "10001"}
    )
    assert not is_error, content

    find_result, _ = executor.dispatch("find", {"query": "search button"})
    search_button_ref = find_result.split("[")[-1].rstrip("]")
    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": search_button_ref}})
    assert not is_error, content

    # refs were cleared by the click (page changed); re-read to find "View"
    find_result, _ = executor.dispatch("find", {"query": "view member"})
    assert "link" in find_result
    view_ref = find_result.split("[")[-1].rstrip("]")
    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": view_ref}})
    assert not is_error, content

    main_frame = surface.page.frame(name="main")
    assert "Maria" in main_frame.content()
    assert "2340.18" in main_frame.content()


def test_stale_ref_after_navigation_returns_clear_error(surface, fake_app_server):
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    top, _ = executor.dispatch("read_page", {})
    nav_ref = [l for l in top.splitlines() if 'frame "nav"' in l][0].split("[")[-1].rstrip("]")

    # Navigating invalidates every ref registered before it.
    executor.dispatch("navigate", {"url": f"{fake_app_server}/app/member/10001/new-subaccount"})

    content, is_error = executor.dispatch("read_page", {"ref": nav_ref})
    assert is_error
    assert "stale or not found" in content


def test_risky_commit_blocked_then_allowed_with_human_approval(surface, fake_app_server):
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    executor.dispatch("navigate", {"url": f"{fake_app_server}/app/member/10001/new-subaccount"})
    page_content, _ = executor.dispatch("read_page", {})
    deposit_ref = [l for l in page_content.splitlines() if "Initial Deposit" in l][0].split("[")[-1].rstrip("]")
    executor.dispatch("form_input", {"target": {"type": "ref", "ref": deposit_ref}, "value": "100"})

    continue_result, _ = executor.dispatch("find", {"query": "continue button"})
    continue_ref = continue_result.split("[")[-1].rstrip("]")
    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": continue_ref}})
    assert not is_error, content
    assert "Confirm New Sub-Account" in surface.page.content()

    confirm_result, _ = executor.dispatch("find", {"query": "confirm and open account button"})
    confirm_ref = confirm_result.split("[")[-1].rstrip("]")

    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": confirm_ref}})
    assert is_error
    assert "Blocked by policy" in content
    assert "Confirm New Sub-Account" in surface.page.content()

    # Re-find (refs were cleared by the blocked attempt) and retry with approval.
    confirm_result, _ = executor.dispatch("find", {"query": "confirm and open account button"})
    confirm_ref = confirm_result.split("[")[-1].rstrip("]")
    content, is_error = executor.dispatch("left_click", {"target": {"type": "ref", "ref": confirm_ref}}, human_approved=True)
    assert not is_error, content
    assert "opened" in surface.page.content()


def test_password_field_is_redacted_in_recorded_steps(surface, fake_app_server):
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    form_input_steps = [s for s in executor.steps if s.tool_name == "form_input"]
    assert len(form_input_steps) == 2
    values = [s.tool_input.get("value") for s in form_input_steps]
    assert "teller123" not in values
    # A DOM-verified password field is redacted to the actual template
    # placeholder (not a generic marker) so the artifact recorder (Part 5)
    # can use the value as-is without a separate translation step.
    assert "{{secrets.password}}" in values
    assert "teller1" in values  # the username isn't secret and stays legible

    # tool_input isn't the only thing that gets serialized into the run log --
    # the internal Action object (real value, needed to actually perform the
    # fill) is stored on RecordedStep.action too, and must be redacted there
    # independently: this leaked in a real discovery run even after
    # tool_input redaction was already in place.
    action_values = [s.action.value for s in form_input_steps if s.action is not None]
    assert "teller123" not in action_values
    assert "{{secrets.password}}" in action_values


def test_read_page_never_surfaces_password_value(surface, fake_app_server):
    """Companion to the ActionResult.observation leak fixed in
    test_surface.py: read_page/find scan the DOM independently of observe(),
    via a separate JS snippet, so they needed the same input[type=password]
    masking at the source.
    """
    executor = BrowserToolExecutor(surface)
    executor.dispatch("navigate", {"url": f"{fake_app_server}/login"})
    top, _ = executor.dispatch("read_page", {})
    textbox_refs = [line.split("[")[-1].rstrip("]") for line in top.splitlines() if line.startswith("textbox")]
    executor.dispatch("form_input", {"target": {"type": "ref", "ref": textbox_refs[1]}, "value": "teller123"})

    content, _ = executor.dispatch("read_page", {})
    assert "teller123" not in content

    content, _ = executor.dispatch("find", {"query": "password field"})
    assert "teller123" not in content


def test_password_redacted_even_when_reached_via_tab_not_click(surface, fake_app_server):
    """Regression test: a real discovery run leaked a password into the run
    log because the model clicked the username field, typed, then pressed
    Tab (not a click) to reach the password field before typing again.
    Redaction must key off actual DOM focus, not click history.
    """
    executor = BrowserToolExecutor(surface)
    executor.dispatch("navigate", {"url": f"{fake_app_server}/login"})
    top, _ = executor.dispatch("read_page", {})
    username_ref = [l for l in top.splitlines() if l.startswith("textbox")][0].split("[")[-1].rstrip("]")

    executor.dispatch("left_click", {"target": {"type": "ref", "ref": username_ref}})
    executor.dispatch("type", {"text": "teller1"})
    executor.dispatch("key", {"text": "Tab"})
    executor.dispatch("type", {"text": "teller123"})

    type_steps = [s for s in executor.steps if s.tool_name == "type"]
    values = [s.tool_input.get("text") for s in type_steps]
    assert values == ["teller1", "{{secrets.password}}"]


def test_dialog_notice_returned_instead_of_hanging(surface, fake_app_server):
    _arm_fault(fake_app_server, "popup", True)
    executor = BrowserToolExecutor(surface)
    _login(executor, fake_app_server)

    executor.dispatch("navigate", {"url": f"{fake_app_server}/app/member/10001"})
    surface.page.wait_for_timeout(300)

    content, is_error = executor.dispatch("read_page", {})
    assert not is_error
    assert "A dialog is open" in content

    ack = executor.dismiss_dialog(accept=True)
    assert "accepted" in ack.lower()

    content, is_error = executor.dispatch("read_page", {})
    assert not is_error
    assert "A dialog is open" not in content
