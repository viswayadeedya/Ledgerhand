import copy

_TEMPLATE_MEMBERS: dict[int, dict] = {
    10001: dict(
        id=10001, first_name="Maria", last_name="Garcia", status="active",
        savings_balance=2340.18, checking_balance=512.44, sub_accounts=[],
    ),
    10002: dict(
        id=10002, first_name="Carlos", last_name="Garcia", status="active",
        savings_balance=8112.02, checking_balance=1200.00, sub_accounts=[],
    ),
    10003: dict(
        id=10003, first_name="John", last_name="Smith", status="active",
        savings_balance=954.76, checking_balance=320.10, sub_accounts=[],
    ),
    10004: dict(
        id=10004, first_name="Angela", last_name="Nguyen", status="active",
        savings_balance=15320.55, checking_balance=4200.00, sub_accounts=[],
    ),
    10005: dict(
        id=10005, first_name="Robert", last_name="Chen", status="closed",
        savings_balance=0.0, checking_balance=0.0, sub_accounts=[],
    ),
}

MEMBERS: dict[int, dict] = {}


def reset() -> None:
    global MEMBERS
    MEMBERS = copy.deepcopy(_TEMPLATE_MEMBERS)


reset()


def get_member(member_id: int) -> dict | None:
    return MEMBERS.get(member_id)


def search_by_last_name(last_name: str) -> list[dict]:
    needle = last_name.strip().lower()
    return [m for m in MEMBERS.values() if m["last_name"].lower() == needle]


SUB_ACCOUNT_BASE = 4000
"""Sub-account numbers start at 4001 rather than 1.

Not cosmetic. A capability that extracts the number has to locate it by
searching the captured page for its value, and locator matching is
substring-based -- "1" is contained in the member ID "10001" two rows
above, so a one-digit number resolves to the wrong cell. Realistic-looking
numbers are also simply what a real system produces.
"""


def next_sub_account_number(member_id: int) -> int:
    return SUB_ACCOUNT_BASE + len(MEMBERS[member_id]["sub_accounts"]) + 1
