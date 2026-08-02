"""The chrome three pages share: the room rail, the stylesheet links, one version.

The dashboard, the console and the handbook all carry the same bar, and for
most of this project's life each carried its own copy of the markup for it.
Copies drift, and these had: the console was missing intelligence, broadsheet
and handbook and had grown an `api` tab no other page offered; the handbook
was missing the console; and the three of them pinned two different
cache-busting versions of the same stylesheet, so a visitor moving between
them could be handed two different stylesheets for one design and see the
older one win.

None of that was a decision anybody made. It is what happens to markup that
exists in three files: you edit the page you are looking at.

So the rail is data here and the markup is generated from it. A page carries a
placeholder where its copy used to be. What stays with each page is what only
that page has — the dashboard's mega panel and its live badge live in
index.html, because a thing that exists once cannot drift.
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# One version for every stylesheet link on every page. Bump it whenever a
# stylesheet changes: a visitor with a warm cache is otherwise served the
# design you replaced, which is indistinguishable from the change not having
# worked. This was three hand-maintained strings before, and two disagreed.
ASSET_VERSION = "s12.0"

# The rail, in order, and the whole of it. `href` is also the identity a page
# passes to say which tab is its own. `view` is the room name app.js resolves
# from the path; the two standalone pages are not rooms and have none.
ROOMS: tuple[tuple[str, str, str | None, str], ...] = (
    ("/watch", "watch", "watch",
     "The desk of everything open right now, the anomaly lanes and the cost ledger."),
    ("/investigate", "investigation", "investigate",
     "One signal at a time: fourteen days of evidence, the analyst's cited triage, "
     "the recommender's two options."),
    ("/decide", "decision desk", "decide",
     "Where the hand decides: proposals awaiting a verdict, and the sealed chain of "
     "the verdicts already given."),
    ("/intel", "intelligence", "intel",
     "What the decisions added up to: funnel, approved value, agent telemetry and "
     "the standing market bands."),
    ("/brain", "brain", "brain",
     "What the system concludes about itself: insights, self-review, routines, "
     "runbooks and the detection backtest."),
    ("/broadsheet", "broadsheet", "all",
     "Every room on one page, in order — the view to read straight through or to print."),
    ("/static/chat.html", "console", None,
     "Ask one of the four agents a question about this estate and read the answer "
     "beside the evidence it used."),
    ("/static/guide.html", "handbook", None,
     "The whole product in ten minutes: what it is, what each room shows, what is "
     "real and what is simulated."),
)

TABS_SLOT = "<!--{{chrome:tabs}}-->"
STYLES_SLOT = "<!--{{chrome:styles}}-->"


def render_tabs(current: str | None = None, indent: str = "      ") -> str:
    """The eight tabs as markup.

    `current` is the href of the page doing the asking, or None on the
    dashboard — there the rooms are one page and app.js sets the pressed tab
    from the path, so pinning one here would be a second answer to a question
    that already has one.
    """
    lines = []
    for href, label, view, title in ROOMS:
        attrs = ['class="view-tab"', f'href="{href}"']
        if view:
            attrs.append(f'data-view="{view}"')
        if href == current:
            attrs += ['aria-current="page"', 'aria-pressed="true"']
        elif view:
            attrs.append('aria-pressed="false"')
        attrs.append(f'title="{html.escape(title, quote=True)}"')
        lines.append(f"{indent}<a {' '.join(attrs)}>{label}</a>")
    return "\n".join(lines).lstrip()


def render_styles(*extra: str, indent: str = "  ") -> str:
    """The stylesheet links, every one of them carrying the same version."""
    sheets = ("style.css", *extra)
    links = [f'<link rel="stylesheet" href="/static/{s}?v={ASSET_VERSION}" />' for s in sheets]
    return f"\n{indent}".join(links)


@lru_cache(maxsize=16)
def _compose(filename: str, current: str | None, extra: tuple[str, ...], _stamp: float) -> str:
    src = (STATIC_DIR / filename).read_text(encoding="utf-8")
    if TABS_SLOT not in src or STYLES_SLOT not in src:
        missing = [s for s in (TABS_SLOT, STYLES_SLOT) if s not in src]
        raise ValueError(f"{filename} is missing its chrome slot(s): {', '.join(missing)}")
    return src.replace(TABS_SLOT, render_tabs(current)).replace(
        STYLES_SLOT, render_styles(*extra)
    )


def page(filename: str, current: str | None = None, extra_sheets: tuple[str, ...] = ()) -> str:
    """A page with its chrome filled in.

    Memoised on the source file's mtime, so a served page costs one dict
    lookup rather than a read and two substitutions, and editing the file
    still takes effect without a restart.
    """
    return _compose(filename, current, tuple(extra_sheets),
                    (STATIC_DIR / filename).stat().st_mtime)
