"""Legibility, pinned the way this repo pins every other promise.

The complaint that started this file was not subtle: black text that cannot
be read, and text sitting on top of other text. Both were real. Both were
found by walking the rendered page in a headless browser, in all five
palettes, at 1440 and 820 — computing, for every text node, the contrast of
its colour against the background actually painted beneath it.

A browser walk is not a test, though: it needs Chrome, a server and four
minutes. So the findings come back here as *static* assertions over
static/style.css. The stylesheet is parsed with the stdlib `re` (the suite
gains no dependency), each palette's token block is resolved the way the
cascade resolves it — specificity first, then source order — and every
foreground/background pair the stylesheet actually puts together is held to
WCAG AA: 4.5:1 for body text, 3:1 for large.

Three kinds of pair are checked, in increasing order of how much they had
to be discovered rather than derived:

  1. SURFACES   — each palette's ink tokens (--ink, --ink-dim, --ink-faint)
                  against each surface that palette actually paints: the
                  page, a raised panel, a panel inside a panel, and (vivid
                  only) a white card. Nothing is hand-fed; the surfaces are
                  composited from the palette's own tokens.

  2. SAME BLOCK — every rule in the file that declares BOTH `color` and a
                  `background`, resolved per palette. This is the honest
                  reading of "pairs the stylesheet uses together": the rule
                  itself says so.

  3. MEASURED   — pairs where the colour and the background come from
                  different rules, so only a rendered page could prove they
                  meet. Each entry names the palette, the element, the
                  ratio the browser measured, and the file:line responsible.

WHERE THE STYLESHEET LOSES, THIS FILE DOES NOT FIX IT. static/style.css is
owned elsewhere, and a test that quietly relaxed its own threshold would
hide the very thing it exists to surface. Each current violation is pinned
in FAILING_PAIRS with the measured ratio and what it should become. The
list is a shrinking to-do, not a silent pass: test_the_allow_list_does_not_rot
fails when an entry is repaired but left listed, so a fixed pair cannot
leave a permanent hole in the contract behind it.

One structural trap gets its own test, because it is the bug that produced
"black text that cannot be read" and no ratio check on its own would
explain why: `.cs-btn.ghost` (0,2,0) is outranked by
`:root:not([data-theme="vivid"]) .cs-btn` (0,3,0), so in the four editorial
palettes a ghost button is painted --surface on --surface. See
test_a_variant_button_is_not_outranked_by_its_themed_base.

WHAT THIS FILE DELIBERATELY DOES NOT TEST: the other half of the report,
text overlapping text, is geometry. It needs a layout engine, and a static
proxy for it ("this grid's children declare min-width: 0") would fail on
honest edits and pass on the real bug. The two overlaps the walk found are
written up with coordinates and fixes in the accompanying report, not
faked into an assertion here.

The static side was checked against the live one before it was trusted:
for all five palettes the colours resolved here are hex-identical to the
ones Chrome computed (page, one raised panel, a panel inside a panel, and
--ink-faint on each), so the ratios below are the ratios on screen.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = REPO_ROOT / "static" / "style.css"

# WCAG 2.1 AA. Large text is >= 24px, or >= 18.66px at weight >= 700.
AA_BODY = 4.5
AA_LARGE = 3.0


# --------------------------------------------------------------------------
# colour algebra
# --------------------------------------------------------------------------

_NAMED = {
    "transparent": (0.0, 0.0, 0.0, 0.0),
    "white": (255.0, 255.0, 255.0, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
}


class Unresolvable(Exception):
    """A value this parser will not pretend to understand (gradient, url, …)."""


def parse_color(value):
    """CSS colour -> (r, g, b, a) floats. Raises Unresolvable for the rest."""
    v = value.strip().rstrip("!important").strip()
    if v in _NAMED:
        return _NAMED[v]

    m = re.fullmatch(r"#([0-9a-fA-F]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            raise Unresolvable(value)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (float(r), float(g), float(b), a)

    m = re.fullmatch(r"rgba?\(([^()]*)\)", v)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1).strip()) if p]
        if len(parts) < 3:
            raise Unresolvable(value)

        def chan(p):
            return float(p[:-1]) / 100 * 255 if p.endswith("%") else float(p)

        r, g, b = (chan(p) for p in parts[:3])
        if len(parts) > 3:
            a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
        else:
            a = 1.0
        return (r, g, b, a)

    raise Unresolvable(value)


def over(top, bottom):
    """Source-over compositing of two premultiplied-free RGBA tuples."""
    a = top[3] + bottom[3] * (1 - top[3])
    if a == 0:
        return (0.0, 0.0, 0.0, 0.0)
    ch = [(top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a for i in range(3)]
    return (ch[0], ch[1], ch[2], a)


def flatten(color, backdrop):
    """Paint `color` onto an opaque `backdrop` and return an opaque colour."""
    return over(color, backdrop)[:3] + (1.0,)


def luminance(c):
    def lin(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])

def contrast(fg, bg):
    """WCAG contrast ratio. Both colours must already be opaque."""
    la, lb = luminance(fg), luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hexy(c):
    return "#%02x%02x%02x" % tuple(int(round(max(0, min(255, x)))) for x in c[:3])


# --------------------------------------------------------------------------
# a very small CSS reader — enough for this stylesheet, no dependency
# --------------------------------------------------------------------------

def _strip_comments(text):
    """Blank out /* … */ while preserving line numbers."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def parse_rules(text):
    """[(selector, {prop: value}, line), …] — including blocks nested one
    level inside @media/@supports/@layer, which is where the responsive
    overrides live."""
    src = _strip_comments(text)
    line_at = []
    line = 1
    for ch in src:
        line_at.append(line)
        if ch == "\n":
            line += 1
    line_at.append(line)

    out = []

    def walk(start, end):
        i = start
        while i < end:
            brace = src.find("{", i)
            if brace == -1 or brace >= end:
                return
            selector = src[i:brace].strip()
            depth, j = 1, brace + 1
            while j < end and depth:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                j += 1
            body = src[brace + 1:j - 1]
            if selector.startswith("@"):
                at = selector.split()[0]
                # `@media print` repaints the page black-on-white on purpose;
                # it is a different medium with its own (perfect) contrast,
                # and letting its `color: #000 !important` into the screen
                # cascade would report every dark palette as failing.
                if at in ("@media", "@supports", "@layer") and "print" not in selector:
                    walk(brace + 1, j - 1)
            elif selector:
                out.append((selector, parse_declarations(body), line_at[brace]))
            i = j

    walk(0, len(src))
    return out


def parse_declarations(body):
    """prop -> value, splitting on top-level semicolons only (so the commas
    and semicolons inside color-mix()/rgba() survive)."""
    decls = {}
    depth = 0
    buf = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            _add_decl(decls, "".join(buf))
            buf = []
        else:
            buf.append(ch)
    _add_decl(decls, "".join(buf))
    return decls


def _add_decl(decls, chunk):
    chunk = chunk.strip()
    if ":" not in chunk:
        return
    prop, _, value = chunk.partition(":")
    prop, value = prop.strip(), value.strip()
    if prop and value and not prop.startswith("{"):
        decls[prop] = value


def selector_parts(selector):
    """Split a selector list on top-level commas only.

    `a:not(.x, .y, .cs-btn)` is ONE selector. Splitting it naively hands you
    a phantom `.cs-btn)` rule and, in this stylesheet, makes vivid's link
    colour look like the primary button's — a false failure that took a
    while to stop believing.
    """
    return _split_top_level(selector)


def specificity(selector):
    """(id, class, type) — enough to order the rules this file compares.

    :not()/:is()/:where() are handled the way the spec says: :where() adds
    nothing, the others take the specificity of their most specific argument.
    """
    sel = selector.strip()
    sel = re.sub(r":where\(([^()]*)\)", " ", sel)
    inner = 0, 0, 0
    for m in re.finditer(r":(?:not|is|has)\(([^()]*)\)", sel):
        for arg in m.group(1).split(","):
            cand = specificity(arg)
            inner = max(inner, cand)
    sel = re.sub(r":(?:not|is|has)\(([^()]*)\)", " ", sel)
    ids = len(re.findall(r"#[\w-]+", sel))
    classes = len(re.findall(r"\.[\w-]+", sel))
    classes += len(re.findall(r"\[[^\]]*\]", sel))
    classes += len(re.findall(r"(?<!:):(?!:)[a-zA-Z-]+", sel))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", sel))
    types += len(re.findall(r"::[a-zA-Z-]+", sel))
    return (ids + inner[0], classes + inner[1], types + inner[2])


# --------------------------------------------------------------------------
# palettes
# --------------------------------------------------------------------------

PALETTES = ("horizon", "mission", "paper", "dawn", "vivid")


@pytest.fixture(scope="module")
def rules():
    return parse_rules(STYLE_CSS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tokens(rules):
    """{palette: {--name: raw value}} resolved by the cascade's own order:
    higher specificity wins, ties broken by source order."""
    out = {}
    for palette in PALETTES:
        want = {":root", "html", ':root[data-theme="%s"]' % palette,
                '[data-theme="%s"]' % palette}
        best = {}
        for order, (selector, decls, _line) in enumerate(rules):
            for part in selector_parts(selector):
                if part not in want:
                    continue
                spec = specificity(part)
                for name, value in decls.items():
                    if not name.startswith("--"):
                        continue
                    prev = best.get(name)
                    if prev is None or (spec, order) >= (prev[0], prev[1]):
                        best[name] = (spec, order, value)
        out[palette] = {k: v[2] for k, v in best.items()}
    return out


def resolve(value, table, depth=0):
    """Resolve var()/color-mix() against a palette's token table -> RGBA."""
    if depth > 12:
        raise Unresolvable(value)
    value = value.strip().replace("!important", "").strip()

    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.*))?\)", value, re.S)
    if m:
        name, fallback = m.group(1), m.group(2)
        if name in table:
            return resolve(table[name], table, depth + 1)
        if fallback:
            return resolve(fallback, table, depth + 1)
        raise Unresolvable(value)

    m = re.fullmatch(r"color-mix\(\s*in\s+srgb\s*,\s*(.+)\)", value, re.S)
    if m:
        args = _split_top_level(m.group(1))
        if len(args) != 2:
            raise Unresolvable(value)
        first, second = args
        pm = re.search(r"([\d.]+)%\s*$", first)
        if not pm:
            raise Unresolvable(value)
        pct = float(pm.group(1)) / 100
        a = resolve(first[:pm.start()].strip(), table, depth + 1)
        b = resolve(second.strip(), table, depth + 1)
        # srgb mixing, alpha included — matches what Chrome computed for the
        # tinted badges and chips.
        alpha = a[3] * pct + b[3] * (1 - pct)
        if alpha == 0:
            return (0.0, 0.0, 0.0, 0.0)
        ch = [
            (a[i] * a[3] * pct + b[i] * b[3] * (1 - pct)) / alpha
            for i in range(3)
        ]
        return (ch[0], ch[1], ch[2], alpha)

    return parse_color(value)


def _split_top_level(text):
    parts, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def surfaces(table):
    """The opaque backgrounds a palette actually paints, named the way the
    page stacks them. `raised2` is a panel inside a panel, which is where
    the desk's cards live and which the browser walk confirmed: horizon's
    card measured #18304c, exactly --surface-raised applied twice."""
    page = flatten(resolve("var(--surface)", table), (255.0, 255.0, 255.0, 1.0))
    raised = resolve("var(--surface-raised)", table)
    one = flatten(raised, page)
    two = flatten(raised, one)
    out = {"page": page, "raised": one, "raised2": two}
    card = resolve("var(--card-bg)", table)
    out["card"] = flatten(card, page)
    return out


# --------------------------------------------------------------------------
# the allow-list — every pair that is failing today
# --------------------------------------------------------------------------
#
# Key: (palette, pair-id). The comment on each entry is the whole point:
# it names what the ratio is now and what it must become. Delete an entry
# the moment the stylesheet is fixed — test_the_allow_list_does_not_rot
# will fail if you do not.

FAILING_PAIRS = {
    # ---- 1 · SURFACES ----------------------------------------------------
    # --ink-faint is the page's quietest ink and it is under AA on every
    # surface in every palette. It carries real content, not decoration:
    # .cs-row-note (style.css:2243), .cs-stat-label (:2229),
    # .watch-detail (:947), .market-id (:1618), .flow-stop span (:1979),
    # .identity-line (:484). FIX: raise each palette's --ink-faint alpha
    # until it clears 4.5:1 on `raised2`, the darkest/lightest surface it
    # lands on — roughly 0.42 -> 0.62 (horizon), 0.38 -> 0.60 (mission),
    # 0.40 -> 0.58 (paper), 0.42 -> 0.62 (dawn), 0.44 -> 0.60 (vivid).
    ("horizon", "--ink-faint on page"): 3.73,
    ("horizon", "--ink-faint on raised"): 3.60,
    ("horizon", "--ink-faint on raised2"): 3.42,
    ("horizon", "--ink-faint on card"): 3.60,
    ("mission", "--ink-faint on page"): 3.17,
    ("mission", "--ink-faint on raised"): 3.14,
    ("mission", "--ink-faint on raised2"): 3.03,
    ("mission", "--ink-faint on card"): 3.14,
    ("paper", "--ink-faint on page"): 2.42,
    ("paper", "--ink-faint on raised"): 2.39,
    ("paper", "--ink-faint on raised2"): 2.36,
    ("paper", "--ink-faint on card"): 2.39,
    ("dawn", "--ink-faint on page"): 3.64,
    ("dawn", "--ink-faint on raised"): 3.54,
    ("dawn", "--ink-faint on raised2"): 3.34,
    ("dawn", "--ink-faint on card"): 3.54,
    ("vivid", "--ink-faint on page"): 2.69,
    # vivid raises real white surfaces (--surface-raised and --card-bg are
    # both #ffffff), which is *less* forgiving than the page tint beneath
    # them. FIX with the same --ink-faint change above.
    ("vivid", "--ink-faint on raised"): 2.75,
    ("vivid", "--ink-faint on raised2"): 2.75,
    ("vivid", "--ink-faint on card"): 2.75,

    # --ink-dim is fine everywhere except paper, and only just.
    # paper's --ink-dim is rgba(24,28,48,0.64) = 5.5:1 on the page but it
    # is the .microcap colour (style.css:368) and .microcap sits inside
    # .brain-identity's black wash — see the MEASURED section below.

    # ---- 2 · SAME BLOCK --------------------------------------------------
    # `#pulse-run:disabled` (style.css:729) and its vivid twin (:3025) paint
    # --ink-faint on the raised surface. Same root cause as above; they are
    # listed by the SURFACES entries, not separately, because the rule's own
    # `background: none` means the surface underneath is what it lands on.

    # ---- 3 · MEASURED ----------------------------------------------------
    # The three that only a rendered page could prove.

    # THE UNREADABLE ONE. `.cs-btn.ghost` (style.css:2289) sets
    # color: var(--accent) at specificity (0,2,0). Twelve lines earlier,
    # `.brain-identity` (style.css:1717) paints background: rgba(0,0,0,0.18)
    # — a hard-coded black wash written for a dark palette. On the two light
    # palettes it drops the panel to #bfbdb7 (paper) / #d1d1d1 (vivid) and
    # takes .microcap, #identity-note, #auth-login and #auth-register down
    # with it. FIX: background: var(--surface-raised) so the wash follows
    # the palette instead of assuming one.
    ("paper", "brain-identity panel ink"): 3.88,
    ("paper", "brain-identity accent buttons"): 4.34,
    ("vivid", "brain-identity panel ink"): 4.47,
    ("vivid", "brain-identity accent buttons"): 3.42,
}


# Cascade failures that are a specificity fact rather than a ratio. Keyed by
# the pair of rules, so the moment either moves the pin stops matching and
# test_the_allow_list_does_not_rot says so.
#
# Empty, and it should stay that way. The one entry that lived here was the
# ghost button: `:root:not([data-theme="vivid"]) .cs-btn { color: var(--surface) }`
# reached for a palette scope, picked up a class of specificity doing it,
# and at (0,3,0) outranked `.cs-btn.ghost` (0,2,0) one line below — so in
# horizon, mission, paper and dawn a ghost button painted --surface onto
# --surface and "Re-scan" and "Go to the decisions" could not be read.
#
# It was healed by removing the reason the scope existed. The palette
# exclusion was there because vivid ran its own button system; with the
# five palettes sharing one, the selector is plain `:root .cs-btn` at
# (0,2,0), the ghost rule matches it and comes later, and the variant
# decides its own colour. Lowering the base cost nothing that raising the
# variant would have bought.
PINNED_CASCADE_TRAPS = set()


# --------------------------------------------------------------------------
# 1 · SURFACES
# --------------------------------------------------------------------------

INK_TOKENS = ("--ink", "--ink-dim", "--ink-faint")


def _pairs_surfaces(table):
    for ink in INK_TOKENS:
        fg_raw = resolve("var(%s)" % ink, table)
        for name, bg in sorted(surfaces(table).items()):
            fg = flatten(fg_raw, bg)
            yield "%s on %s" % (ink, name), fg, bg


@pytest.mark.parametrize("palette", PALETTES)
def test_every_ink_token_reads_on_every_surface_its_palette_paints(palette, tokens):
    """A palette's own ink, on a palette's own surfaces, at AA.

    Nothing here is hand-fed: the surfaces are composited from --surface,
    --surface-raised and --card-bg, so a palette added tomorrow is covered
    the moment it declares its tokens.
    """
    table = tokens[palette]
    bad = []
    for pair_id, fg, bg in _pairs_surfaces(table):
        ratio = contrast(fg, bg)
        if (palette, pair_id) in FAILING_PAIRS:
            continue
        if ratio < AA_BODY:
            bad.append("%s: %s on %s = %.2f:1 (needs %.1f)"
                       % (pair_id, hexy(fg), hexy(bg), ratio, AA_BODY))
    assert not bad, "%s palette, ink under AA:\n  %s" % (palette, "\n  ".join(bad))


# --------------------------------------------------------------------------
# 2 · SAME BLOCK
# --------------------------------------------------------------------------

NOT_A_BACKGROUND = {"none", "transparent", "inherit", "initial", "unset", "currentcolor"}

# A palette guard is a scope a rule may wear without changing WHAT it
# targets: `:root[data-theme="vivid"] .cs-btn` and `.cs-btn` are two rules
# arguing over the same element. Stripping the guard gives that shared
# "target".
#
# [data-a11y-*] is deliberately NOT stripped: the accessibility panel's
# forced-contrast mode is an opt-in state, not a palette, and its
# `color: #000` on `#ffe600` must not be mistaken for the default one.
_SCOPE = re.compile(
    r"""^\s*(?::root|html)?
        (?:\[data-theme[^\]]*\])*
        (?::not\(\s*\[data-theme[^\]]*\]\s*\))*
        (?:\[data-theme[^\]]*\])*
        \s+""",
    re.X,
)


def _target(selector_part):
    """`:root:not([data-theme="vivid"]) .cs-btn` -> `.cs-btn`."""
    prev = None
    out = selector_part.strip()
    while out != prev:
        prev = out
        out = _SCOPE.sub("", out).strip()
    return " ".join(out.split())


def effective_color(rules, selector_part, palette, upto_order):
    """What `color` this target actually ends up with in this palette.

    A block can declare `color: #fff` and be overruled eleven lines later by
    a palette-scoped rule for the same target — which is exactly what
    `.cs-btn`, `.cs-chip[aria-pressed]` and `.a11y-launch` all do. Comparing
    the block's own declaration against the block's own background would
    report three failures the browser never paints.
    """
    target = _target(selector_part)
    best = None
    for order, (selector, decls, line) in enumerate(rules):
        if "color" not in decls:
            continue
        for part in selector_parts(selector):
            if _target(part) != target:
                continue
            if not _applies_to(part, palette):
                continue
            key = (specificity(part), order)
            if best is None or key >= best[0]:
                best = (key, decls["color"], line)
    return (best[1], best[2]) if best else (None, None)


def _same_block_pairs(rules, table, palette):
    """(selector, line, fg, bg) for every rule that declares both a colour
    and an opaque-enough background of its own — with the colour resolved
    the way the cascade resolves it, not the way the block declares it."""
    for selector, decls, line in rules:
        raw_fg = decls.get("color")
        raw_bg = decls.get("background-color") or decls.get("background")
        if not raw_fg or not raw_bg:
            continue
        bg_value = raw_bg.replace("!important", "").strip()
        # a shorthand carrying more than a colour (gradient, url, layers)
        if any(k in bg_value for k in ("gradient", "url(")):
            continue
        bg_value = _split_top_level(bg_value)[0] if "," in bg_value and not bg_value.startswith(
            ("rgb", "color-mix", "hsl")) else bg_value
        if bg_value.lower() in NOT_A_BACKGROUND:
            continue
        winner, _at = effective_color(rules, selector_parts(selector)[0], palette, line)
        raw_fg = winner or raw_fg
        if raw_fg.replace("!important", "").strip().lower() in (
                "inherit", "currentcolor", "initial", "unset"):
            continue
        try:
            bg = resolve(bg_value, table)
            fg = resolve(raw_fg, table)
        except (Unresolvable, ValueError):
            continue
        if bg[3] < 0.6:
            # a wash, not a fill: what is underneath decides, and the
            # MEASURED section is where those are pinned.
            continue
        page = flatten(resolve("var(--surface)", table), (255.0, 255.0, 255.0, 1.0))
        bg_flat = flatten(bg, page)
        yield selector, line, flatten(fg, bg_flat), bg_flat


def _applies_to(selector, palette):
    """False when a rule is scoped to a palette other than this one."""
    for other in PALETTES:
        marker = '[data-theme="%s"]' % other
        if marker in selector and other != palette:
            if ":not(%s)" % marker in selector or ':not([data-theme="%s"])' % other in selector:
                continue
            return False
    marker = '[data-theme="%s"]' % palette
    if ':not(%s)' % marker in selector:
        return False
    return True


@pytest.mark.parametrize("palette", PALETTES)
def test_every_rule_that_paints_its_own_background_reads_on_it(palette, rules, tokens):
    """If one rule declares both the ink and the fill, it has made a claim.

    This is the pair set the stylesheet states outright — no measurement
    needed, no curation possible. It is what catches a solid button whose
    label was written for a different palette.
    """
    table = tokens[palette]
    bad = []
    for selector, line, fg, bg in _same_block_pairs(rules, table, palette):
        if not _applies_to(selector, palette):
            continue
        if "a11y-contrast" in selector:
            # the forced-contrast escape hatch is deliberately not palette ink
            continue
        pair_id = "%s @%d" % (" ".join(selector.split())[:60], line)
        if (palette, pair_id) in FAILING_PAIRS:
            continue
        ratio = contrast(fg, bg)
        if ratio < AA_LARGE:
            bad.append("style.css:%d  %s — %s on %s = %.2f:1"
                       % (line, pair_id, hexy(fg), hexy(bg), ratio))
    assert not bad, ("%s palette, a rule's own ink is illegible on its own "
                     "background:\n  %s" % (palette, "\n  ".join(bad)))


# --------------------------------------------------------------------------
# 3 · MEASURED — pairs proved by walking the rendered page
# --------------------------------------------------------------------------
#
# fg expression, background stack (painted bottom-up onto the page), and
# the file:line that puts them together. These are the ones no single rule
# admits to, because the colour and the fill come from different places.

MEASURED_PAIRS = (
    # id, fg expression, [background layers over the page], where
    ("ghost button label on raised2",
     "var(--accent)",
     ["var(--surface-raised)", "var(--surface-raised)"],
     "style.css:2305 .cs-btn.ghost, which now decides its own colour"),
    ("brain-identity panel ink",
     "var(--ink-dim)",
     ["var(--surface-raised)", "rgba(0, 0, 0, 0.18)"],
     "style.css:1717 .brain-identity background"),
    ("brain-identity accent buttons",
     "var(--accent)",
     ["var(--surface-raised)", "rgba(0, 0, 0, 0.18)"],
     "style.css:1717 under style.css:1113 .head-action"),
)


@pytest.mark.parametrize("palette", PALETTES)
def test_the_pairs_the_rendered_page_proved(palette, tokens):
    """The cross-rule pairs a browser walk found and a parser could not.

    `ghost button label` was the reported bug: --surface painted onto
    --surface, because a themed base rule outranked the variant. It is
    expressed here as a colour pair so the ratio is re-derived from
    whatever the tokens become, and as a cascade fact in
    test_a_variant_button_is_not_outranked_by_its_themed_base, so the fix
    cannot be a recolour that leaves the specificity trap in place.

    It reads var(--accent) in all five palettes now. There is no longer a
    palette that is exempt: the exclusion that let vivid through was the
    trap, and it went when the five stopped running separate button
    systems.
    """
    table = tokens[palette]
    bad = []
    for pair_id, fg_expr, layers, where in MEASURED_PAIRS:
        bg = flatten(resolve("var(--surface)", table), (255.0, 255.0, 255.0, 1.0))
        for layer in layers:
            bg = flatten(resolve(layer, table), bg)
        fg = flatten(resolve(fg_expr, table), bg)
        ratio = contrast(fg, bg)
        if (palette, pair_id) in FAILING_PAIRS:
            continue
        if ratio < AA_BODY:
            bad.append("%s — %s on %s = %.2f:1 (%s)"
                       % (pair_id, hexy(fg), hexy(bg), ratio, where))
    assert not bad, "%s palette:\n  %s" % (palette, "\n  ".join(bad))


# --------------------------------------------------------------------------
# 4 · the cascade trap that made the text invisible
# --------------------------------------------------------------------------

def test_a_variant_button_is_not_outranked_by_its_themed_base(rules):
    """`.cs-btn.ghost` must decide its own colour.

    A ratio check alone would let someone "fix" this by recolouring
    --surface, which would break something else and leave the trap armed.
    The real defect is a cascade one: a base rule reached for a palette
    scope, gained a class of specificity doing it, and started overriding
    the variant that is supposed to opt out of it.
    """
    ghost = [(sel, decls, line) for sel, decls, line in rules
             if ".cs-btn.ghost" in sel and "color" in decls]
    assert ghost, "static/style.css no longer styles .cs-btn.ghost — update this test"

    base = [(sel, decls, line) for sel, decls, line in rules
            if "color" in decls
            and re.search(r"(^|[\s,])[^,]*\.cs-btn\s*(,|$)", sel)
            and ".ghost" not in sel]

    losers, seen = [], set()
    for gsel, _gd, gline in ghost:
        gpart = max((specificity(p) for p in selector_parts(gsel) if ".cs-btn.ghost" in p),
                    default=(0, 0, 0))
        for bsel, _bd, bline in base:
            bpart = max((specificity(p) for p in selector_parts(bsel) if ".cs-btn" in p),
                        default=(0, 0, 0))
            # a rule later in the file only loses if it is strictly less specific
            if bline > gline or bpart <= gpart:
                continue
            trap = ("style.css:%d" % bline, "style.css:%d" % gline)
            seen.add(trap)
            if trap in PINNED_CASCADE_TRAPS:
                continue
            losers.append(
                "style.css:%d `%s` %s outranks style.css:%d `%s` %s — "
                "the ghost button is painted by the base rule"
                % (bline, " ".join(bsel.split())[:64], bpart,
                   gline, " ".join(gsel.split())[:64], gpart))
    assert not losers, (
        "a .cs-btn.ghost colour is overridden by a more specific .cs-btn rule:\n  "
        + "\n  ".join(losers)
        + "\n\nFIX: `:root .cs-btn.ghost { background: transparent; color: var(--accent); }`"
          " — same specificity as the themed base, later in source order, so it wins."
    )

    healed = PINNED_CASCADE_TRAPS - seen
    assert not healed, (
        "fixed, but still listed in PINNED_CASCADE_TRAPS: %s — delete the entry "
        "(and its FAILING_PAIRS 'ghost button label' rows) so the contract "
        "covers the ghost button again" % sorted(healed))


# --------------------------------------------------------------------------
# 5 · the allow-list is a to-do, not a hiding place
# --------------------------------------------------------------------------

def _all_ratios(palette, rules, tokens):
    """{pair_id: ratio} for every pair this file knows how to compute."""
    table = tokens[palette]
    found = {}
    for pair_id, fg, bg in _pairs_surfaces(table):
        found[pair_id] = contrast(fg, bg)
    for selector, line, fg, bg in _same_block_pairs(rules, table, palette):
        if not _applies_to(selector, palette) or "a11y-contrast" in selector:
            continue
        found["%s @%d" % (" ".join(selector.split())[:60], line)] = contrast(fg, bg)
    for pair_id, fg_expr, layers, _where in MEASURED_PAIRS:
        if pair_id.startswith("ghost button") and palette == "vivid":
            continue
        bg = flatten(resolve("var(--surface)", table), (255.0, 255.0, 255.0, 1.0))
        for layer in layers:
            bg = flatten(resolve(layer, table), bg)
        found[pair_id] = contrast(flatten(resolve(fg_expr, table), bg), bg)
    return found


def test_the_allow_list_does_not_rot(rules, tokens):
    """Every pinned failure must still be one, and still exist.

    Without this, a repaired pair would leave its exemption behind and the
    contract would quietly stop covering it — the failure mode that makes
    allow-lists worse than no test at all.
    """
    repaired, vanished, drifted = [], [], []
    for palette in PALETTES:
        found = _all_ratios(palette, rules, tokens)
        for (pal, pair_id), pinned in FAILING_PAIRS.items():
            if pal != palette:
                continue
            if pair_id not in found:
                vanished.append("%s / %s" % (pal, pair_id))
                continue
            now = found[pair_id]
            if now >= AA_BODY:
                repaired.append("%s / %s is now %.2f:1" % (pal, pair_id, now))
            elif abs(now - pinned) > 0.06:
                drifted.append("%s / %s: pinned %.2f, measures %.2f"
                               % (pal, pair_id, pinned, now))

    assert not repaired, (
        "fixed, but still listed in FAILING_PAIRS:\n  " + "\n  ".join(repaired)
        + "\n— delete the entry so the contract covers it again")
    assert not vanished, (
        "pinned but no longer produced by static/style.css:\n  " + "\n  ".join(vanished)
        + "\n— delete the entry, or update the selector it names")
    assert not drifted, (
        "the stylesheet moved but the pinned ratio did not:\n  " + "\n  ".join(drifted)
        + "\n— update the number so the to-do stays honest")


def test_the_allow_list_says_what_it_is_waiting_for():
    """Every entry must be reachable from a comment that names the fix.

    A bare tuple in a dict is a silent pass with extra steps. This asserts
    the file keeps explaining itself: each pinned pair-id appears in the
    prose above it.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    body = source.split("FAILING_PAIRS = {", 1)[1].split("\n}", 1)[0]
    commented = {line.strip() for line in body.splitlines() if line.strip().startswith("#")}
    assert len(commented) >= 20, (
        "FAILING_PAIRS has lost its explanations — every group of pinned "
        "pairs needs a comment naming the palette, the rule and the fix")
    for token in ("--ink-faint", "style.css:2289", "style.css:1717"):
        assert token in body, (
            "%s is no longer named in the allow-list's comments; a pinned "
            "failure without a cited rule is a silent pass" % token)
