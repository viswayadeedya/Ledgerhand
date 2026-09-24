"""Scans every committed file under /evidence and /artifacts for values
that must never be written down.

Two rules, deliberately not the same rule:

  The credential is absolute. `teller123` must not appear in any committed
  file in either tree -- no exemptions, no allowlist. The moment a scan
  carries a list of files where the password is permitted, it stops being
  a check and becomes a record of exceptions, and the next entry is always
  easier to add than the first. Commands that need it take it from
  $env:TELLER_PASSWORD instead.

  Values read off the page (member names, balances) are forbidden with one
  exemption, named by exact path and argued below.

Derived from the fake app's own seed data rather than a hardcoded list, so
the scan cannot drift from the values it is protecting: add a member to
data.py and this starts guarding that member too.

What this does not cover, and what covers it instead: screenshots. A PNG
of the member detail page shows the balance as plainly as any JSON, and no
text scan will ever see it. That is a retention problem rather than a
redaction one -- see REPORT.md Section 6.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cua.fake_app.data import _TEMPLATE_MEMBERS  # noqa: E402
from cua.fake_app.main import FAKE_CREDENTIALS  # noqa: E402

SCANNED_TREES = [REPO_ROOT / "evidence", REPO_ROOT / "artifacts"]

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".webm"}

VALUES_RULE_EXEMPT = {
    REPO_ROOT / "evidence" / "discovery-member-lookup" / "run_log.json",
}
"""The one exemption, by exact path, from the *values* rule only.

This is the transcript of the genuine LLM discovery run the brief
requires. It is a record of what the model actually saw on each step --
the page text it read, the values it extracted, the reasoning it produced
from them. Masking those observations would not make it a safer piece of
evidence; it would make it stop being evidence, because what it exists to
prove is precisely that a model read this real page and reached this real
answer.

The exemption is for read-off-the-page values only. The credential rule
still applies to this file like every other.

In production a transcript like this would not live in a repository at
all -- see REPORT.md Section 6 for where it would live and how long for.
"""


def _forbidden_values() -> dict[str, str]:
    """The seeded values, by the label a failure should report them under.

    Zero balances are skipped: "0.00" carries no information and would
    match all sorts of unrelated text.
    """
    forbidden = {}
    for member in _TEMPLATE_MEMBERS.values():
        forbidden[f"{member['first_name']} {member['last_name']}"] = f"member {member['id']} name"
        for field in ("savings_balance", "checking_balance"):
            amount = member[field]
            if amount:
                forbidden[f"{amount:.2f}"] = f"member {member['id']} {field}"
    return forbidden


def _scannable_files() -> list[Path]:
    files = []
    for tree in SCANNED_TREES:
        for path in sorted(tree.rglob("*")):
            if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
                continue
            # evidence/runs/ is git-ignored scratch, not committed evidence.
            if "runs" in path.relative_to(REPO_ROOT).parts:
                continue
            files.append(path)
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_there_is_something_to_scan():
    """A scan over an empty file list passes silently and proves nothing --
    the failure mode where a glob stops matching and every check below
    turns green.
    """
    files = _scannable_files()
    assert len(files) > 20, f"only found {len(files)} files to scan; the glob is probably wrong"
    assert any(p.name == "result.json" for p in files)
    assert any(p.suffix == ".yaml" for p in files)


@pytest.mark.parametrize("password", sorted(FAKE_CREDENTIALS.values()))
def test_no_committed_file_contains_the_password(password):
    """Absolute, with no exemption list. Taken from the app's own
    credential table so it can't drift from what the app actually accepts.
    """
    offenders = [
        str(path.relative_to(REPO_ROOT)) for path in _scannable_files() if password in _read(path)
    ]
    assert offenders == [], (
        f"the fake password appears in: {', '.join(offenders)}. "
        "Commands should take it from $env:TELLER_PASSWORD; the value belongs in the top-level README only."
    )


def test_no_committed_file_contains_an_unmasked_seed_value():
    """Names and balances are read off the page and must be masked wherever
    they are written down. One exemption, by exact path, argued at
    VALUES_RULE_EXEMPT.
    """
    forbidden = _forbidden_values()
    offenders = []
    for path in _scannable_files():
        if path.resolve() in {p.resolve() for p in VALUES_RULE_EXEMPT}:
            continue
        content = _read(path)
        for value, label in forbidden.items():
            if value in content:
                offenders.append(f"{path.relative_to(REPO_ROOT)} contains {label}")
    assert offenders == [], "unmasked values found:\n  " + "\n  ".join(offenders)


def test_the_exemption_is_real_and_still_needed():
    """An exemption nobody checks is a hole. If this file ever stops
    containing what it is exempted for, the exemption should go rather than
    sit there permitting something that no longer happens.
    """
    for path in VALUES_RULE_EXEMPT:
        assert path.exists(), f"exempted path no longer exists: {path}"
        content = _read(path)
        assert any(
            value in content for value in _forbidden_values()
        ), f"{path} no longer contains seed values; remove its exemption"


def test_the_exemption_does_not_extend_to_the_password():
    """The two rules are separate, and the file exempted from one is
    explicitly not exempt from the other.
    """
    for path in VALUES_RULE_EXEMPT:
        for password in FAKE_CREDENTIALS.values():
            assert password not in _read(path)


def test_masked_values_are_what_appears_instead():
    """The rules above pass just as well if the evidence were empty. This
    checks the masked form is actually present -- that values were masked
    rather than omitted.
    """
    result = REPO_ROOT / "evidence" / "success_10001" / "result.json"
    content = _read(result)
    assert "***" in content and "shape:" in content
