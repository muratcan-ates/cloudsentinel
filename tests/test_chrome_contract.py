"""The three pages that carry the bar must carry the same bar.

Every complaint that started this file was a drift complaint: the top bar
changes when you move between pages. It changed because the markup for it
existed in three files and nobody edits three files. app/chrome.py made it
data; this pins the promise so it cannot come apart again by hand.

The rule these tests encode is the one the design asks for out loud — moving
between pages, only the colours change.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import chrome
from main import app

client = TestClient(app)

# page file, the URL it is served at, the href it should mark as current
PAGES = (
    ("index.html", "/", None),
    ("chat.html", "/static/chat.html", "/static/chat.html"),
    ("guide.html", "/static/guide.html", "/static/guide.html"),
)

TAB = re.compile(r'<a class="view-tab"[^>]*>[^<]*</a>')
SHEET = re.compile(r'<link rel="stylesheet" href="/static/([^"?]+)\?v=([^"]+)" />')


def _served(url: str) -> str:
    r = client.get(url)
    assert r.status_code == 200, f"{url} returned {r.status_code}"
    return r.text


def _tab_identity(markup: str) -> list[tuple[str, str]]:
    """(href, label) for each tab — the part that must not differ by page."""
    out = []
    for tag in TAB.findall(markup):
        href = re.search(r'href="([^"]+)"', tag)
        label = re.search(r">([^<]*)</a>", tag)
        out.append((href.group(1), label.group(1)))
    return out


def test_every_page_serves_the_same_rail():
    """Same tabs, same order, same labels, on all three."""
    rails = {url: _tab_identity(_served(url)) for _f, url, _c in PAGES}
    first = rails["/"]
    assert len(first) == len(chrome.ROOMS), (
        "the dashboard's rail is not the full room list — chrome.ROOMS has "
        f"{len(chrome.ROOMS)} entries, the page rendered {len(first)}"
    )
    for url, rail in rails.items():
        assert rail == first, (
            f"{url} serves a different rail from the dashboard.\n"
            f"  {url}: {rail}\n  /: {first}"
        )


@pytest.mark.parametrize("filename,url,current", PAGES)
def test_a_page_marks_itself_and_only_itself_current(filename, url, current):
    """A standalone page names its own tab; the dashboard names none.

    On the dashboard the six rooms are one document and app.js sets the
    pressed tab from the path — a second answer baked into the markup would
    be one the client immediately contradicts.
    """
    markup = _served(url)
    pressed = re.findall(r'<a class="view-tab"[^>]*aria-current="page"[^>]*href="([^"]+)"'
                         r'|<a class="view-tab"[^>]*href="([^"]+)"[^>]*aria-current="page"',
                         markup)
    hrefs = [a or b for a, b in pressed]
    if current is None:
        assert not hrefs, f"{url} pins a current tab in markup; app.js owns that"
    else:
        assert hrefs == [current], f"{url} should mark exactly {current}, marked {hrefs}"


def test_one_asset_version_across_every_page():
    """Two versions of one stylesheet means two designs, and cache picks."""
    seen: dict[str, set[str]] = {}
    for _f, url, _c in PAGES:
        for sheet, version in SHEET.findall(_served(url)):
            seen.setdefault(sheet, set()).add(version)
    assert seen, "no stylesheet links found on any page — the slot stopped rendering"
    wrong = {s: v for s, v in seen.items() if v != {chrome.ASSET_VERSION}}
    assert not wrong, (
        "stylesheets are served at more than one version, so a warm cache can "
        f"serve a design you replaced: {wrong} (expected {chrome.ASSET_VERSION})"
    )


@pytest.mark.parametrize("filename,_url,_current", PAGES)
def test_no_page_keeps_its_own_copy_of_the_rail(filename, _url, _current):
    """The source file holds a slot, never tabs. This is the drift itself."""
    src = (chrome.STATIC_DIR / filename).read_text(encoding="utf-8")
    assert chrome.TABS_SLOT in src, f"{filename} lost its {chrome.TABS_SLOT} slot"
    assert chrome.STYLES_SLOT in src, f"{filename} lost its {chrome.STYLES_SLOT} slot"
    assert '<a class="view-tab" href=' not in src, (
        f"{filename} has grown its own copy of a rail tab again — the rail "
        "lives in app/chrome.py:ROOMS so that all three pages cannot disagree"
    )


def test_the_rail_reaches_both_standalone_pages():
    """A room the rail cannot reach is a room that got quietly orphaned.

    The console had lost intelligence, broadsheet and handbook this way, and
    the handbook had lost the console.
    """
    hrefs = {href for href, _l, _v, _t in chrome.ROOMS}
    for _f, url, current in PAGES:
        if current:
            assert current in hrefs, f"{url} is served but not reachable from the rail"
