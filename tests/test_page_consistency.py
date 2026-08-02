"""The site is four documents — they must behave like one product.

The dashboard is a single page, but the console, the handbook and the API
browser are their own HTML files. Three things had drifted between them and
each one was visible to anyone clicking around: the palette reset to a
hardcoded default, the top bar changed shape, and the stylesheet stamp was
from an older build so a returning browser served that build's colours.

These tests pin the three, because the drift returns the moment a page is
edited on its own.
"""

import re

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# The whole site, in the order the nav lists them.
PAGES = ("/", "/static/chat.html", "/static/guide.html", "/static/docs.html")

# One nav, one order, everywhere.
EXPECTED_NAV = [
    "/watch",
    "/investigate",
    "/decide",
    "/intel",
    "/brain",
    "/broadsheet",
    "/static/chat.html",
    "/static/guide.html",
    "/static/docs.html",
]


def page(path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.text


def nav_hrefs(markup: str) -> list[str]:
    block = markup.split('<div class="nav-links">', 1)[1].split("</div>", 1)[0]
    return re.findall(r'<a class="view-tab" href="([^"]+)"', block)


@pytest.mark.parametrize("path", PAGES)
def test_every_page_boots_the_shared_appearance(path):
    """Palette and accessibility settings are stamped before the paint.

    A visitor on the paper palette with larger text used to land on the
    console in vivid at default size, because only app.js knew how to read
    the preference and app.js does not run there.
    """
    assert "/static/appearance.js" in page(path)


@pytest.mark.parametrize("path", PAGES)
def test_no_page_hardcodes_a_palette(path):
    """`data-theme` belongs to the visitor's choice, not to the markup."""
    opening_tag = page(path).split(">", 2)[1]
    assert "data-theme" not in opening_tag


@pytest.mark.parametrize("path", PAGES)
def test_every_page_carries_the_same_nav(path):
    assert nav_hrefs(page(path)) == EXPECTED_NAV


@pytest.mark.parametrize("path", PAGES)
def test_every_page_marks_where_the_visitor_is(path):
    """Exactly one tab is the current page — and on the dashboard the SPA
    marks it at runtime, so the served markup carries none."""
    marks = page(path).count('aria-current="page"')
    assert marks == (0 if path == "/" else 1)


def test_the_api_browser_is_not_a_dead_end():
    """It used to render bare Swagger with no way back into the product."""
    markup = page("/static/docs.html")
    assert 'class="top-nav"' in markup
    assert "swagger-ui" in markup


@pytest.mark.parametrize("path", PAGES)
def test_pages_agree_on_the_stylesheet_build(path):
    """A stale `?v=` on one page serves that build's palette from cache.

    chat.html and guide.html sat on s9.0 while the dashboard shipped s11.0,
    so the console genuinely showed an older design to anyone who had been
    there before.
    """
    stamps = set(re.findall(r"/static/style\.css\?v=([^\"']+)", page(path)))
    assert stamps, f"{path} does not load the site stylesheet"
    dashboard = set(re.findall(r"/static/style\.css\?v=([^\"']+)", page("/")))
    assert stamps == dashboard


def test_the_appearance_module_owns_the_palette_list():
    """One list of palettes, read by the switcher rather than restated."""
    module = client.get("/static/appearance.js").text
    for theme in ("horizon", "mission", "paper", "dawn", "vivid"):
        assert f'"{theme}"' in module
    app_js = client.get("/static/app.js").text
    assert "appearance.THEMES" in app_js or "SentinelAppearance" in app_js
    # the dashboard must not keep a second copy that can drift
    assert 'const THEMES = ["horizon"' not in app_js


def test_the_appearance_module_survives_blocked_storage():
    """Private windows throw on localStorage; the page must still paint."""
    module = client.get("/static/appearance.js").text
    assert module.count("catch") >= 4
