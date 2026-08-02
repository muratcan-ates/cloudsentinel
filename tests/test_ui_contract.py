"""The interface's own contract, enforced the way the API's is.

The recurring complaint about this dashboard has never been that it is
ugly — it is that a control does not say what it does. A button reading
"Pulse →" is a promise the markup has to keep somewhere: in its text, in
a title, in the sentence next to it. Everywhere else in this repo that
kind of promise is pinned by a test (test_openapi_contract.py holds the
schema to its word, test_endpoint_matrix.py holds every path to its
status codes). This file does the same for static/index.html, so the
answer to "what does this button do?" stops being a matter of taste and
starts being a matter of the suite going red.

Four contracts, parsed out of the markup with the stdlib html.parser so
the suite gains no dependency:

  1. NAME     — every button, link, select and input has an accessible
                name, and no control is named by an icon alone.
  2. EXPLAIN  — every control that is not self-evident carries an
                explanation a hovering, reading or screen-reading human
                can reach.
  3. WIRING   — every id app.js reaches for exists (no dead handler),
                and every data-* hook the markup declares is read by
                app.js (no dead markup).
  4. ROUTES   — no <a href> points at a path this app does not serve.

WHERE THE MARKUP LOSES, THIS FILE DOES NOT FIX IT. static/index.html is
owned elsewhere; a test that quietly patched the page would hide the
very thing it exists to surface. Each current violation is pinned in an
explicit allow-list below with a comment naming the fix. The lists are a
shrinking to-do, not a silent pass: test_the_allow_lists_do_not_rot
fails when an entry is repaired but left listed, so a fixed control
cannot leave a permanent hole in the contract behind it.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"


# ======================================================================
# The allow-lists — every one of these is a real violation, pinned
# ======================================================================

# Controls whose purpose the markup never states. Each entry names the
# fix; deleting the entry is the definition of done.
#
# Empty since the footer's "guided tour ▸" was given a title: it is a
# control wearing a link's clothes (?tour=1 launches the overlay rather
# than navigating), and the title now says it walks the six rooms in
# order and can be left at any step. Every control on the page states
# its purpose somewhere a human can reach.
UNEXPLAINED_CONTROLS: set[str] = set()

# Ids app.js reads with getElementById()/#selectors that no element in
# index.html carries and no app.js template creates.
#
# Empty since the colophon gained <p id="mission-posture">: before it,
# renderMissionPosture() — shipped in 2de862c — returned at its own
# first line on every render, so the mission switch never said what it
# had changed.
MISSING_IDS: set[str] = set()

# data-* hooks the markup declares that app.js never reads.
UNREAD_DATA_HOOKS: set[str] = set()

# <a href> targets the app does not serve.
DEAD_LINKS: set[str] = set()

# The EXPLAIN contract's exceptions are not a list of control ids — they
# are the four rules in obviousness_for() below (navigation links,
# labelled option groups, rows introduced by their own text, and rows
# introduced by a leading label). Each is justified in a comment there.
# Anything those rules do not clear must either explain itself or be
# pinned in UNEXPLAINED_CONTROLS above with the fix written down.


# ======================================================================
# A minimal DOM, built with html.parser
# ======================================================================

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Classes this design system uses for explanatory copy. These are the
# paragraphs a reader's eye lands on next to a control.
DESCRIPTION_CLASSES = {"meta", "hint", "cs-card-sub"}


class Node:
    """One element: its tag, attributes, children and own text."""

    __slots__ = ("tag", "attrs", "parent", "children", "own_text")

    def __init__(self, tag: str, attrs: list, parent: "Node | None") -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[Node] = []
        self.own_text: list[str] = []

    @property
    def text(self) -> str:
        """All text under this node, whitespace-collapsed."""
        parts = list(self.own_text)
        parts.extend(child.text for child in self.children)
        return " ".join(part for part in parts if part).strip()

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def attr(self, name: str) -> str:
        return (self.attrs.get(name) or "").strip()

    def is_ancestor_of(self, other: "Node") -> bool:
        walker = other.parent
        while walker is not None:
            if walker is self:
                return True
            walker = walker.parent
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} {self.attrs.get('id') or self.attrs.get('class')!r}>"


class _DomBuilder(HTMLParser):
    """Builds a parent/child tree. Tolerant: unbalanced tags never raise."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", [], None)
        self._open = [self.root]
        self.nodes: list[Node] = []

    def _append(self, tag: str, attrs: list) -> Node:
        node = Node(tag, attrs, self._open[-1])
        self._open[-1].children.append(node)
        self.nodes.append(node)
        return node

    def handle_starttag(self, tag: str, attrs: list) -> None:
        node = self._append(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self._open.append(node)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._append(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._open[-1].own_text.append(data.strip())


class Dom:
    """The parsed page plus the lookups every contract below needs."""

    def __init__(self, markup: str) -> None:
        builder = _DomBuilder()
        builder.feed(markup)
        builder.close()
        self.nodes = builder.nodes
        self.by_id = {n.attrs["id"]: n for n in self.nodes if n.attrs.get("id")}
        self.labels_for: dict[str, Node] = {}
        for node in self.nodes:
            target = node.attr("for")
            if node.tag == "label" and target:
                self.labels_for.setdefault(target, node)

    @property
    def controls(self) -> list[Node]:
        return [n for n in self.nodes if is_control(n)]


def is_control(node: Node) -> bool:
    """Anything a person can click, type into or choose from.

    aria-hidden subtrees are excluded: they are removed from the
    accessibility tree, so demanding a name from them would be wrong.
    """
    if node.tag == "button":
        interactive = True
    elif node.tag in ("select", "textarea"):
        interactive = True
    elif node.tag == "input":
        interactive = node.attrs.get("type") != "hidden"
    elif node.tag == "a":
        interactive = "href" in node.attrs
    else:
        interactive = False
    if not interactive:
        return False
    walker: Node | None = node
    while walker is not None:
        if walker.attr("aria-hidden") == "true":
            return False
        walker = walker.parent
    return True


def locator(node: Node) -> str:
    """A stable, readable key for the allow-lists."""
    if node.attrs.get("id"):
        return f"{node.tag}#{node.attrs['id']}"
    if node.tag == "a" and node.attrs.get("href"):
        return f'a[href="{node.attrs["href"]}"]'
    return f"{node.tag}[{accessible_name(node)}]"


# ======================================================================
# Contract 1 — the name
# ======================================================================


def naming_label(dom: Dom, node: Node) -> Node | None:
    """The <label> that names this control: for= first, then wrapping."""
    node_id = node.attrs.get("id")
    if node_id and node_id in dom.labels_for:
        return dom.labels_for[node_id]
    walker = node.parent
    while walker is not None:
        if walker.tag == "label":
            return walker
        walker = walker.parent
    return None


def accessible_name(node: Node, dom: "Dom | None" = None) -> str:
    """The name in roughly the order the accname spec resolves it."""
    if node.attr("aria-label"):
        return node.attr("aria-label")
    labelled_by = node.attr("aria-labelledby")
    if labelled_by and dom is not None:
        joined = " ".join(
            dom.by_id[ref].text for ref in labelled_by.split() if ref in dom.by_id
        ).strip()
        if joined:
            return joined
    if dom is not None:
        label = naming_label(dom, node)
        if label is not None and label.text:
            return label.text
    if node.text:
        return node.text
    for fallback in ("value", "placeholder", "alt", "title"):
        if node.attr(fallback):
            return node.attr(fallback)
    return ""


def name_is_only_a_glyph(name: str) -> bool:
    """True when the name is an icon: fewer than two letters or digits.

    "→", "▾", "⧉", "☼" all collapse to nothing here; "a–z" keeps two
    letters and passes, which is the intent — a real, if terse, word.
    """
    return len(re.sub(r"[^0-9A-Za-zÀ-ɏ]", "", name)) < 2


# ======================================================================
# Contract 2 — the explanation
# ======================================================================


def nearby_descriptions(scope: Node, control: Node) -> list[Node]:
    """Explanatory paragraphs a reader would take as being about `control`.

    Direct children of `scope` only — a description three levels down
    belongs to something else. Ancestors of the control are skipped (a
    <p class="meta"> that *contains* the link does not explain it), and
    so are describers nested inside a <label>, which name a different
    control rather than explaining this one.
    """
    found = []
    for child in scope.children:
        if child is control or child.is_ancestor_of(control):
            continue
        if child.tag == "label":
            continue
        if child.classes & DESCRIPTION_CLASSES and child.text:
            found.append(child)
    return found


def explanation_for(dom: Dom, node: Node) -> str:
    """Why a reader can tell what this control does — '' when they cannot."""
    if node.attr("title"):
        return "title attribute"
    described_by = node.attr("aria-describedby")
    if described_by and any(ref in dom.by_id for ref in described_by.split()):
        return "aria-describedby"
    # A four-word aria-label is a sentence, not a name: "Filter signals
    # by service" says what happens, "Password" does not.
    if len(node.attr("aria-label").split()) >= 4:
        return "described aria-label"
    label = naming_label(dom, node)
    if label is not None:
        if label.attr("title"):
            return "title on its label"
        if any(child.classes & {"hint"} and child.text for child in label.children):
            return "hint inside its label"
    # A disclosure explains itself by what it opens.
    if node.attrs.get("aria-controls") and "aria-expanded" in node.attrs:
        panel = dom.by_id.get(node.attr("aria-controls"))
        if panel is not None and len(panel.text.split()) >= 5:
            return "the panel it discloses"
    # Its own copy: either a sentence, or the <b>title</b><span>gloss</span>
    # pattern this design uses for cards and flow stops.
    if len(node.text.split()) >= 4:
        return "its own sentence"
    gloss = [c for c in node.children if c.text]
    if len(gloss) >= 2 and len(node.text.split()) >= 3:
        return "an inline gloss"
    # The paragraph beside it, or beside the row it sits in.
    scope, hops = node.parent, 0
    while scope is not None and hops <= 1:
        if nearby_descriptions(scope, node):
            return f"adjacent .meta/.hint (+{hops})"
        scope, hops = scope.parent, hops + 1
    return ""


def obviousness_for(dom: Dom, node: Node) -> str:
    """Why this control needs no explanation at all — '' when it does."""
    # A link whose href is a real destination is explained by where it
    # goes; contract 4 already proves the destination exists. In-page
    # fragments and query-only hrefs are not navigation in that sense —
    # ?tour=1 launches an overlay — so they stay in the EXPLAIN contract.
    href = node.attr("href")
    if node.tag == "a" and (href.startswith("/") or "://" in href):
        return "navigation link"
    parent = node.parent
    if parent is None or parent.tag == "label":
        return ""
    # One option among several, under a heading that labels the row:
    # role=group + aria-label, a leading text node ("sort —", "Palette —"),
    # or a leading label element (the a11y steppers' "Text size").
    if parent.attr("role") == "group" and parent.attr("aria-label"):
        return f"option in group {parent.attr('aria-label')!r}"
    if parent.own_text:
        return f"option in row {parent.own_text[0]!r}"
    # The last plain element before it, if there is one. <label> and
    # <output> are skipped deliberately: a label names some *other*
    # control in the row, and an output prints a value — neither
    # introduces the row. Without that, the colophon's "2.00" readout
    # would be read as an introduction to the Re-scan button beside it.
    introduction = ""
    for sibling in parent.children:
        if sibling is node:
            break
        if is_control(sibling) or sibling.tag in ("label", "output"):
            continue
        if len(re.sub(r"[^0-9A-Za-zÀ-ɏ]", "", sibling.text)) >= 2:
            introduction = sibling.text
    return f"introduced by {introduction!r}" if introduction else ""


# ======================================================================
# Contract 3 — the wiring
# ======================================================================

GET_BY_ID = re.compile(r"""getElementById\(\s*['"`]([^'"`]+)['"`]""")
HASH_SELECTOR = re.compile(r"""['"`]#([A-Za-z][\w-]*)['"`]""")
# app.js writes markup of its own; those ids are created, not expected.
JS_TEMPLATE_ID = re.compile(r"""\bid=["'`]?([A-Za-z][\w-]*)""")
JS_ASSIGNED_ID = re.compile(r"""\.id\s*=\s*["'`]([A-Za-z][\w-]*)["'`]""")
# Elements built through a helper that takes an attribute object —
# svgEl("g", { id: "radar-blips" }) — are created at runtime just as
# surely as a template string is. Without this the radar's blip layer
# read as a dead handler when it is the opposite: code that makes the
# element it then looks for.
JS_OBJECT_ID = re.compile(r"""\bid\s*:\s*["'`]([A-Za-z][\w-]*)["'`]""")


def ids_app_js_reads(source: str) -> set[str]:
    return set(GET_BY_ID.findall(source)) | set(HASH_SELECTOR.findall(source))


def ids_app_js_creates(source: str) -> set[str]:
    return (
        set(JS_TEMPLATE_ID.findall(source))
        | set(JS_ASSIGNED_ID.findall(source))
        | set(JS_OBJECT_ID.findall(source))
    )


def data_hooks_declared(dom: Dom) -> set[str]:
    return {
        attr
        for node in dom.nodes
        for attr in node.attrs
        if attr.startswith("data-")
    }


def dataset_property(attribute: str) -> str:
    """data-a11y-toggle -> a11yToggle, the name JS reads it under."""
    head, *tail = attribute[len("data-"):].split("-")
    return head + "".join(part.capitalize() for part in tail)


# ======================================================================
# Contract 4 — the routes
# ======================================================================


def served_paths() -> set[str]:
    """Everything the app answers on: schema paths plus the unlisted ones.

    app.openapi() misses the rooms and "/" on purpose — they are
    registered with include_in_schema=False — so the route table is
    unioned in rather than the room list being hardcoded here.
    """
    paths = set(app.openapi()["paths"])
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            paths.add(path)
    return paths


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture(scope="module")
def dom() -> Dom:
    return Dom(INDEX_HTML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ======================================================================
# Contract 1 — the name
# ======================================================================


def test_the_page_parses_into_the_controls_we_expect(dom):
    """A guard on the parser itself: a silent parse failure would make
    every contract below vacuously true."""
    tags = {node.tag for node in dom.controls}
    assert {"button", "a", "select", "input"} <= tags
    assert len(dom.controls) > 50
    assert "threshold" in dom.by_id and "mission-select" in dom.by_id


def test_every_control_has_an_accessible_name(dom):
    unnamed = [
        locator(node)
        for node in dom.controls
        if not accessible_name(node, dom).strip()
    ]
    assert not unnamed, (
        "controls with no accessible name — a screen reader announces "
        f"these as bare 'button' or 'link': {sorted(unnamed)}"
    )


def test_no_control_is_named_only_by_an_icon(dom):
    """An arrow or a glyph is decoration; it is never a name."""
    glyph_only = [
        locator(node)
        for node in dom.controls
        if name_is_only_a_glyph(accessible_name(node, dom))
    ]
    assert not glyph_only, (
        "controls named by an icon alone — add text or an aria-label: "
        f"{sorted(glyph_only)}"
    )


def test_every_form_field_is_named_by_a_label_or_aria_label(dom):
    """Placeholders vanish on the first keystroke, so they do not count."""
    fields = [n for n in dom.controls if n.tag in ("input", "select", "textarea")]
    assert fields, "no form fields found — the parser lost the colophon"
    unlabelled = [
        locator(node)
        for node in fields
        if not node.attr("aria-label")
        and not node.attr("aria-labelledby")
        and naming_label(dom, node) is None
    ]
    assert not unlabelled, (
        f"form fields named only by their placeholder: {sorted(unlabelled)}"
    )


# ======================================================================
# Contract 2 — the explanation
# ======================================================================


def test_every_non_obvious_control_carries_an_explanation(dom):
    """The owner's complaint, as an assertion.

    A control passes when a human can find out what it does without
    clicking it: a title, a described label, its own sentence, the panel
    it opens, or the paragraph printed beside it.
    """
    unexplained = {
        locator(node)
        for node in dom.controls
        if not explanation_for(dom, node) and not obviousness_for(dom, node)
    }
    new = unexplained - UNEXPLAINED_CONTROLS
    assert not new, (
        "controls that never say what they do. Give each a title, a "
        "descriptive aria-label, or a .meta line beside it — or pin it "
        f"in UNEXPLAINED_CONTROLS with the fix named: {sorted(new)}"
    )


def test_the_controls_that_do_explain_themselves_stay_that_way(dom):
    """The named, load-bearing controls of the demo, held individually.

    A regression in the group above would be one line in a set; these
    are the four the jury actually drives, so they get their own
    failure message.
    """
    for control_id in ("pulse-run", "mission-select", "threshold", "copy-brief"):
        assert control_id in dom.by_id, f"#{control_id} vanished from the page"
        node = dom.by_id[control_id]
        assert explanation_for(dom, node), (
            f"#{control_id} lost its explanation — this is a control the "
            "demo depends on being self-describing"
        )


# ======================================================================
# Contract 3 — the wiring, both directions
# ======================================================================


def test_every_id_app_js_reads_exists_in_the_markup(dom, app_js):
    """Dead handlers: JS reaching for an element that was never shipped.

    Ids app.js writes into its own templates are excluded — those are
    created at runtime, not expected in index.html.
    """
    missing = ids_app_js_reads(app_js) - set(dom.by_id) - ids_app_js_creates(app_js)
    new = missing - MISSING_IDS
    assert not new, (
        "app.js reads ids that exist nowhere — the feature behind each "
        f"of these silently does nothing: {sorted(new)}"
    )


def test_every_data_hook_the_markup_declares_is_read_by_app_js(dom, app_js):
    """Dead markup: a data-* hook nothing listens for."""
    unread = set()
    for hook in data_hooks_declared(dom):
        if hook in app_js:
            continue
        if re.search(rf"\bdataset\.{re.escape(dataset_property(hook))}\b", app_js):
            continue
        unread.add(hook)
    new = unread - UNREAD_DATA_HOOKS
    assert not new, (
        "the markup declares hooks app.js never reads — clicking these "
        f"does nothing: {sorted(new)}"
    )


def test_the_interactive_hooks_of_each_room_are_both_declared_and_read(dom, app_js):
    """The specific hooks the six rooms and the palette hang on.

    Named one by one because a rename that broke all of them at once
    would still satisfy a set-difference check if both sides changed
    together — but only one side is edited at a time in practice.
    """
    for hook in ("data-view", "data-room", "data-theme-choice", "data-a11y-toggle"):
        declared = any(hook in node.attrs for node in dom.nodes)
        assert declared, f"{hook} disappeared from index.html"
        assert hook in app_js, f"{hook} is declared in the markup but read nowhere"


# ======================================================================
# Contract 4 — the routes
# ======================================================================


def test_no_link_points_at_a_route_the_app_does_not_serve(dom):
    """Every internal <a href> resolves: schema path, room, or static file."""
    known = served_paths()
    broken = set()
    for node in dom.nodes:
        if node.tag != "a":
            continue
        href = node.attr("href")
        if not href or href.startswith(("#", "?", "mailto:")) or "://" in href:
            continue
        path = href.split("?")[0].split("#")[0]
        if path.startswith("/static/"):
            if not (STATIC_DIR / path[len("/static/"):]).exists():
                broken.add(href)
            continue
        if path not in known:
            broken.add(href)
    new = broken - DEAD_LINKS
    assert not new, (
        f"links to paths the app does not serve: {sorted(new)}. "
        f"Served paths: {len(known)}"
    )


def test_every_in_page_anchor_lands_on_an_element_that_exists(dom):
    """A jump link to a missing id scrolls nowhere and says nothing."""
    dangling = {
        node.attr("href")
        for node in dom.nodes
        if node.tag == "a"
        and node.attr("href").startswith("#")
        and node.attr("href")[1:]
        and node.attr("href")[1:] not in dom.by_id
    }
    assert not dangling, f"anchors with no target: {sorted(dangling)}"


def test_the_proof_menu_only_offers_endpoints_that_answer(dom):
    """The "what it can prove" panel is the honesty claim, in links.

    Every row there promises the jury a live endpoint. A dead one would
    turn the product's central claim into a 404 on stage.
    """
    known = served_paths()
    panel = dom.by_id["mega-panel"]
    rows = [n for n in panel.children if n.tag == "a"] or [
        n for n in dom.nodes if "cs-mega-item" in n.classes
    ]
    # A floor, not a census: curating the menu is someone else's call,
    # but an emptied panel means the parser or the markup broke.
    assert len(rows) >= 6, "the proof menu is all but empty"
    for row in rows:
        href = row.attr("href").split("?")[0]
        assert href in known, f"the proof menu offers {href}, which 404s"
        assert row.text.strip(), f"{href} is offered with no description"


# ======================================================================
# The allow-lists are a to-do, not a hiding place
# ======================================================================


def test_the_allow_lists_do_not_rot(dom, app_js):
    """Every pinned violation must still be one.

    Without this, a fixed control would leave its exemption behind and
    the contract would quietly stop covering it — the failure mode that
    makes allow-lists worse than no test at all.
    """
    still_unexplained = {
        locator(node)
        for node in dom.controls
        if not explanation_for(dom, node) and not obviousness_for(dom, node)
    }
    repaired = UNEXPLAINED_CONTROLS - still_unexplained
    assert not repaired, (
        f"fixed, but still listed in UNEXPLAINED_CONTROLS: {sorted(repaired)} "
        "— delete the entry so the contract covers it again"
    )

    live_ids = ids_app_js_reads(app_js) - set(dom.by_id) - ids_app_js_creates(app_js)
    wired = MISSING_IDS - live_ids
    assert not wired, (
        f"now present in the markup, but still listed in MISSING_IDS: {sorted(wired)}"
    )

    for pinned in UNEXPLAINED_CONTROLS:
        assert any(locator(node) == pinned for node in dom.controls), (
            f"{pinned} is pinned but no longer exists in index.html — "
            "delete the entry"
        )
