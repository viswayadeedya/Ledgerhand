import pytest
from playwright.sync_api import sync_playwright

from cua.core.models import LocatorCandidate, LocatorStrategy
from cua.surface.locator import LocatorResolutionError, resolve

SAMPLE_HTML = """
<html><body>
<table>
<tr><td>Name:</td><td><input id="name_field" name="name"></td></tr>
<tr><td>Notes:</td><td><input name="notes"></td></tr>
</table>
<label for="name_field">Full Name</label>
<button aria-label="Save Record">Save</button>
<a href="/x">Click here</a>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.set_content(SAMPLE_HTML)
        yield pg
        browser.close()


def test_role_strategy_resolves_by_accessible_name(page):
    loc, candidate = resolve(
        page, [LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Save Record")]
    )
    assert loc.count() == 1
    assert candidate.strategy == LocatorStrategy.ROLE


def test_label_strategy_resolves_associated_input(page):
    loc, candidate = resolve(page, [LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Full Name")])
    assert loc.get_attribute("name") == "name"


def test_text_strategy_resolves_link(page):
    loc, candidate = resolve(page, [LocatorCandidate(strategy=LocatorStrategy.TEXT, value="Click here")])
    assert loc.get_attribute("href") == "/x"


def test_table_position_strategy_resolves_cell_input(page):
    loc, candidate = resolve(
        page, [LocatorCandidate(strategy=LocatorStrategy.TABLE_POSITION, value="row=1,col=1")]
    )
    assert loc.get_attribute("name") == "notes"


def test_css_fallback_resolves(page):
    loc, candidate = resolve(
        page, [LocatorCandidate(strategy=LocatorStrategy.CSS, value='input[name="notes"]')]
    )
    assert loc.count() == 1


def test_falls_through_ranked_candidates_to_first_working_one(page):
    candidates = [
        LocatorCandidate(strategy=LocatorStrategy.ROLE, role="button", value="Does Not Exist"),
        LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Full Name"),
    ]
    loc, candidate = resolve(page, candidates)
    assert candidate.strategy == LocatorStrategy.LABEL


def test_raises_when_nothing_resolves_to_exactly_one_element(page):
    with pytest.raises(LocatorResolutionError):
        resolve(page, [LocatorCandidate(strategy=LocatorStrategy.CSS, value="input")])
