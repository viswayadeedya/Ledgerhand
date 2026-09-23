"""Fast, direct tests of the fake app's own HTTP behavior (FastAPI
TestClient, no browser). Every other test file exercises this same app
correctly but only indirectly, through a real Playwright browser -- these
are the fast regression net for the app's own logic (auth, search, faults,
sub-account flow) without paying browser-launch cost for every case.
"""

import pytest
from fastapi.testclient import TestClient

from cua.fake_app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/admin/reset")
        yield c
        c.post("/admin/reset")


def _login(client: TestClient) -> None:
    r = client.post("/login", data={"username": "teller1", "password": "teller123"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app"


def test_login_page_loads(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign On" in r.text


def test_wrong_credentials_redirect_with_error(client):
    r = client.post("/login", data={"username": "teller1", "password": "wrong"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?error=1"


def test_correct_credentials_redirect_to_app(client):
    _login(client)


def test_protected_route_without_session_redirects_to_login(client):
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_search_valid_member_id_returns_result(client):
    _login(client)
    r = client.get("/app/search", params={"q": "10001"})
    assert r.status_code == 200
    assert "Maria" in r.text
    assert "Garcia" in r.text


def test_search_unknown_member_id_returns_not_found(client):
    _login(client)
    r = client.get("/app/search", params={"q": "99999"})
    assert "No member found" in r.text


def test_search_by_last_name_finds_natural_duplicates(client):
    _login(client)
    r = client.get("/app/search", params={"q": "Garcia"})
    assert "Multiple members matched" in r.text
    assert r.text.count("10001") >= 1
    assert r.text.count("10002") >= 1


def test_member_detail_shows_balance(client):
    _login(client)
    r = client.get("/app/member/10001")
    assert "2340.18" in r.text
    assert "512.44" in r.text


def test_closed_account_shows_closed_message_not_balance(client):
    _login(client)
    r = client.get("/app/member/10005")
    assert "closed" in r.text.lower()
    assert "Savings Balance" not in r.text


def test_subaccount_form_rejects_deposit_below_minimum(client):
    _login(client)
    r = client.post(
        "/app/member/10001/new-subaccount/review",
        data={"account_type": "savings", "initial_deposit": "5", "opened_by": "teller1"},
    )
    assert "at least $25.00" in r.text


def test_subaccount_commit_appends_and_shows_on_detail_page(client):
    _login(client)
    review = client.post(
        "/app/member/10001/new-subaccount/review",
        data={"account_type": "checking", "initial_deposit": "250", "opened_by": "teller1"},
    )
    assert "Confirm New Sub-Account" in review.text

    commit = client.post(
        "/app/member/10001/new-subaccount/commit",
        data={"account_type": "checking", "initial_deposit": "250", "opened_by": "teller1"},
    )
    assert "opened" in commit.text.lower()

    detail = client.get("/app/member/10001")
    assert "Sub-Accounts" in detail.text
    assert "250.00" in detail.text


def test_member_not_found_fault_fires_once_then_auto_disarms(client):
    _login(client)
    client.post("/admin/faults/api", json={"fault": "member_not_found", "armed": True})

    first = client.get("/app/search", params={"q": "10001"})
    assert "No member found" in first.text

    second = client.get("/app/search", params={"q": "10001"})
    assert "Maria" in second.text


def test_duplicate_members_fault_fires_once_then_auto_disarms(client):
    _login(client)
    client.post("/admin/faults/api", json={"fault": "duplicate_members", "armed": True})

    first = client.get("/app/search", params={"q": "10001"})
    assert "Multiple members matched" in first.text

    second = client.get("/app/search", params={"q": "10001"})
    assert "Multiple members matched" not in second.text
    assert "Maria" in second.text


def test_wrong_member_fault_serves_a_different_record_silently(client):
    """The fault this exists to catch: a 200 OK, a normal-looking page, and
    the wrong person's money on it.
    """
    _login(client)
    client.post("/admin/faults/api", json={"fault": "wrong_member", "armed": True})

    served = client.get("/app/member/10001")
    assert served.status_code == 200
    assert "Maria" not in served.text  # not the member that was asked for
    assert "Savings Balance" in served.text  # and nothing on the page says so

    # fires once, then the app behaves again
    assert "Maria" in client.get("/app/member/10001").text


def test_session_expired_fault_redirects_with_flag(client):
    _login(client)
    client.post("/admin/faults/api", json={"fault": "session_expired", "armed": True})

    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?expired=1"

    # session was cleared server-side -- the next protected request is
    # treated as logged out, not just re-triggering the same fault.
    r2 = client.get("/app", follow_redirects=False)
    assert r2.headers["location"] == "/login"


def test_admin_faults_api_get_and_reset(client):
    client.post("/admin/faults/api", json={"fault": "popup", "armed": True})
    state = client.get("/admin/faults/api").json()
    assert state["popup"] is True

    client.post("/admin/reset")
    state_after_reset = client.get("/admin/faults/api").json()
    assert all(v is False for v in state_after_reset.values())


def test_reset_also_restores_member_data(client):
    _login(client)
    client.post(
        "/app/member/10001/new-subaccount/commit",
        data={"account_type": "savings", "initial_deposit": "100", "opened_by": "teller1"},
    )
    assert "Sub-Accounts" in client.get("/app/member/10001").text

    client.post("/admin/reset")
    assert "Sub-Accounts" not in client.get("/app/member/10001").text
