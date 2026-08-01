# static/img — the drawn assets

A small, hand-written SVG set: six room icons, one empty-state illustration, one
social card, the favicon and the bare mark. No embedded raster, no `@font-face`,
no external host, no gradients — every file is plain XML you can read in one
screen. Nothing here is wired into HTML or CSS yet; the markup an integrator
needs is at the bottom of this file.

## The files

| File | Size | Grid / viewBox | What it is | Where it belongs |
| --- | ---: | --- | --- | --- |
| `icon-watch.svg` | 419 B | 24×24 | a series with one reading above a dashed threshold | the **watch** room (`data-view="watch"`, `/watch`) |
| `icon-investigation.svg` | 313 B | 24×24 | a lens over two rows of evidence | the **investigation** room (`data-view="investigate"`, `/investigate`) |
| `icon-decide.svg` | 399 B | 24×24 | two option rows, one ticked by hand | the **decision desk** (`data-view="decide"`, `/decide`) |
| `icon-intelligence.svg` | 468 B | 24×24 | three measured bars against a dashed target | the **intelligence** room (`data-view="intel"`, `/intel`) |
| `icon-brain.svg` | 447 B | 24×24 | three nodes, one settled, joined into memory | the **brain** room (`data-view="brain"`, `/brain`) |
| `icon-desk.svg` | 386 B | 24×24 | a card standing on a working surface | the **desk** section (`#sec-desk`) |
| `mark.svg` | 235 B | 24×24 | the sentinel mark alone (the spike), in `currentColor` | next to the `.nav-brand` wordmark, or any place needing the logo inline |
| `empty-state.svg` | 1109 B | 320×160 | every reading inside its band — nothing broke out | any "nothing is waiting on you" empty state: decision inbox, anomaly list, investigation feed, desk feed |
| `social-card.svg` | 2022 B | 1200×630 | product name, thesis line, group name | `og:image` / `twitter:image` source — see the caveat below |
| `favicon.svg` | 285 B | 32×32 | the mark in an accent tile | already referenced by `index.html` and `docs.html` at `/static/img/favicon.svg` |
| `hands-dither.png` | 194331 B | raster | pre-existing photograph, not part of this set | used in `index.html` (the hero figure) and as the current `og:image` |

Sizes are `ls -l` bytes as of this commit.

## Rules the set follows

- **One stroke weight.** Every 24×24 icon is `stroke-width="1.5"`, round caps and
  joins, `fill="none"`, drawn inside a 2 px margin so the six sit on the same
  optical weight in a row.
- **`currentColor` only.** No icon, and no part of the empty state, names a
  colour. They take the ink of whatever is around them, which is what makes them
  work in all five palettes (`horizon`, `mission`, `paper`, `dawn`, `vivid`) and
  in the high-contrast mode.
- **One accent, `#1d5cff`.** It appears in exactly two places, both of which are
  standalone images with no inherited colour to take: the favicon tile and the
  social card (the mark, the wordmark's period, and the ring around the one
  reading that broke out). That is the `--accent` of the `vivid` light palette.
- **A shared vocabulary.** The dashed line means *a threshold someone set*; the
  ring means *the reading that crossed it*; the spike is the mark. Watch,
  intelligence, the empty state, the card and the favicon all reuse those three.

Checked by rendering: all six icons at 24 px and 72 px on the `vivid`, `horizon`
and `dawn` grounds; the empty state on `vivid`, `mission`, `paper` and `dawn`;
the favicon at 16, 24, 32 and 48 px on white and on near-black browser chrome;
the social card at 1200, 400 and 240 px wide.

## How to use them — `currentColor` needs inlining or a mask

`<img src="…">` renders an SVG in its own document, where `currentColor`
resolves to black. **These icons must not be dropped into an `<img>` tag.** Two
ways that do work, both allowed by the CSP in `main.py`
(`default-src 'self'`, `img-src 'self' data:`):

**A · inline the markup** (best for a nav — one HTTP request fewer, and the icon
inherits the link's colour and `:hover` state for free). Paste the file's
contents, add `aria-hidden="true"` and `focusable="false"`, and let the adjacent
text be the label:

```html
<a class="view-tab" href="/watch" data-view="watch" aria-pressed="false">
  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
       fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
       stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M2.5 8.75h19" stroke-dasharray="2 2.5" opacity=".5"/>
    <path d="M2.5 17 5.5 15.5 8.5 16.75 11 14.75 13.5 4.75 16 16.25 19 14.5 21.5 15.75"/>
    <circle cx="13.5" cy="4.75" r="1.15" fill="currentColor" stroke="none"/>
  </svg>
  watch
</a>
```

**B · CSS mask** (best if the HTML must stay untouched — the file stays a file,
and the colour still follows the ink):

```css
.room-icon {
  display: inline-block;
  width: 1em;
  height: 1em;
  background-color: currentColor;      /* the colour comes from here */
  -webkit-mask: no-repeat center / contain;
  mask: no-repeat center / contain;
}
.room-icon[data-icon="watch"]        { -webkit-mask-image: url("/static/img/icon-watch.svg");        mask-image: url("/static/img/icon-watch.svg"); }
.room-icon[data-icon="investigation"]{ -webkit-mask-image: url("/static/img/icon-investigation.svg");mask-image: url("/static/img/icon-investigation.svg"); }
.room-icon[data-icon="decide"]       { -webkit-mask-image: url("/static/img/icon-decide.svg");       mask-image: url("/static/img/icon-decide.svg"); }
.room-icon[data-icon="intelligence"] { -webkit-mask-image: url("/static/img/icon-intelligence.svg"); mask-image: url("/static/img/icon-intelligence.svg"); }
.room-icon[data-icon="brain"]        { -webkit-mask-image: url("/static/img/icon-brain.svg");        mask-image: url("/static/img/icon-brain.svg"); }
.room-icon[data-icon="desk"]         { -webkit-mask-image: url("/static/img/icon-desk.svg");         mask-image: url("/static/img/icon-desk.svg"); }
```

```html
<span class="room-icon" data-icon="watch" aria-hidden="true"></span>
```

The mask route keeps the opacity steps in `icon-watch`, `icon-intelligence` and
the empty state — a mask reads the rendered alpha, so the dashed threshold still
comes out lighter than the series.

One honest limit: those opacity steps are baked into the files, so they survive
`:root[data-a11y-contrast="high"]` unchanged. If the forced-contrast mode should
flatten them, inline the icons and add one rule — `:root[data-a11y-contrast="high"]
svg [opacity] { opacity: 1 }`. It cannot be done through a mask, where opacity is
the alpha the mask is made of.

### The empty state

Inline it, or use it as a mask on a `320 × 160` box. The illustration carries no
text on purpose — the sentence is the integrator's, in real HTML, so it can be
translated and read aloud:

```html
<figure class="empty">
  <!-- paste empty-state.svg here, with aria-hidden="true" added to the <svg> -->
  <figcaption>
    <b>Nothing is waiting on you.</b>
    <span>Every reading is inside its band. The watch keeps running.</span>
  </figcaption>
</figure>
```

Give the wrapper `color: var(--ink-dim)` if the full ink is too loud; the whole
drawing follows it.

### The favicon

Already live — `favicon.svg` replaces the previous blue square-and-dot mark at
the same path, so no HTML changes and `tests/test_dashboard.py` (which asserts
`/static/img/favicon.svg` returns 200) stays green. It is a `#1d5cff` tile with
the sentinel spike in `#f2efe6`, the same paper tone as the `paper` palette's
surface. The mark is drawn at 32 units with a 3.6 stroke, which lands at roughly
1.8 device pixels at 16 px — the smallest size a browser tab asks for.

### The social card

**Caveat, and it matters:** most crawlers (X, LinkedIn, Slack, WhatsApp) do not
render SVG for `og:image`. `social-card.svg` is the *source* of the card, not a
drop-in replacement for the current `og:image`, which still points at
`hands-dither.png`. To ship it, export a PNG first — e.g. with a headless
browser, or `rsvg-convert -w 1200 -h 630 social-card.svg -o social-card.png` —
then:

```html
<meta property="og:image" content="https://cloudsentinel-y5zh.onrender.com/static/img/social-card.png" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta name="twitter:card" content="summary_large_image" />
```

Until that PNG exists, leave the meta tags as they are; the SVG is still worth
having — it renders in any browser, in a README, or on a slide.

The card uses `Inter` with a system fallback (`'Helvetica Neue', Helvetica,
Arial, sans-serif`) and a system monospace stack for the group line. Nothing is
fetched: on this site Inter is already self-hosted in `static/fonts/`, and
anywhere else the fallback takes over. The layout is left-aligned with short
lines precisely so a substituted font cannot break it.
