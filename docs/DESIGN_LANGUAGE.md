# The design language

CloudSentinel has four typefaces and a habit of roman numerals, and until
tonight that was decoration confined to one masthead. Everything below the
nameplate could have belonged to any dashboard.

This page is the correction: the identity is the system, not the ornament.
It is written down so that the next person — or the next agent — extends the
product without diluting it, and so that a claim like "this looks like us"
can be checked rather than argued.

## The idea in one line

**A newspaper for machines.** Roman structure, pixel ink, and prose that
never overstates. The product watches continuously and reports like an
editor: numbered sections, a nameplate, a kicker, hairline rules, and
figures set in a serif so a number reads as a fact rather than a metric.

Two ancestries meet on purpose:

- **Roman** — numerals (`0 · I · II · III …`) as the spine of the page, the
  small-caps kicker, the measure of a column, the scotch rule under a head.
- **Pixel** — the blackletter nameplate rendered in a bitmap face, the square
  status mark, the period after the wordmark. The machine's own hand.

Neither is a costume. The roman numerals are how a reader navigates; the
pixel face is how the product signs its name.

## The four faces, and the one job each has

| Face | Job | Never used for |
|---|---|---|
| **Jacquard 24** (`.pixel-black`) | The nameplate and section words. The product's signature. | Body text, labels, anything the eye must read quickly |
| **UnifrakturMaguntia** (`.fraktur`) | Single emphasised words inside a line — *watches*, *artificial vigilance*, *operator*. | Whole sentences |
| **Instrument Serif** | Figures: money, counts, z-scores, the threshold. A number in serif reads as a measured fact. | Interface labels |
| **Inter** | Everything a person actually reads: prose, labels, controls, tables. | Display, figures |

The rule that keeps it coherent: **display faces name things, Inter explains
them, the serif counts them.** A screen that reverses this stops looking like
the product.

## The recurring motifs

1. **The numeral before the word.** Every section head carries its roman
   numeral (`0 the desk`, `I anomalies`, `II cost ledger`). The numeral is
   ink, the word is pixel-blackletter. This is the strongest single signal of
   the identity and it costs one span.
2. **The kicker.** A small-caps line above a head, in prose, saying what the
   section is for — the newspaper's standfirst. Never a slogan.
3. **The period.** `cloudsentinel.` always ends in a full stop, and the stop
   is the accent colour. It is the only place the accent is used decoratively.
4. **The thesis line.** *the machine watches — the human decides*, with
   *watches* in blackletter and *decides* in italic. It belongs on any page
   that introduces the product: the dashboard, the handbook, the console.
5. **The status square.** A filled square, never a circle, before a status
   line. Circles belong to buttons; squares belong to statements of fact.
6. **The scotch rule.** A double rule under the masthead and a hairline under
   a section head. Space is the newspaper's punctuation.
7. **Grain.** The dither tokens (`--dot-a`, `--dot-b`) put a barely-visible
   texture on the ground so a light page reads as paper rather than as white.

## The palettes are moods, not products

Five palettes render the same identity: `vivid` (light, the default — the
control surface), `horizon` (night blue, the original), `mission` (graphite),
`paper` (bone and ink), `dawn` (ember). A palette may change the ground and
the accent. It may **not** change the faces, the numerals, the motifs or the
copy. A visitor switching palettes should recognise one product in two moods,
never two products.

## The copy is part of the design

The interface explains itself in plain sentences and refuses to overstate.
Three habits carry more identity than any typeface:

- **Every control says what it does**, next to the control, in one sentence:
  *"How far a day must sit from its own baseline to count as a signal."*
- **Absent is not zero.** A capability that cannot answer says
  *unavailable*; a figure with no data says so. A dash reads as zero, and
  zero is a claim.
- **Provenance is stated.** Every agent answer carries a badge naming where
  it came from; execution says SIMULATED where it is simulated; the ledger
  proves it was not rewritten rather than asking to be believed.

## Applying it to a new page

A page belongs to this product when all five are true:

1. It loads `/static/style.css` and takes its colour from the palette tokens,
   never from a hex literal.
2. Its title block uses the nameplate treatment: pixel-blackletter wordmark,
   accent period, thesis line.
3. Its sections carry numerals and kickers.
4. Its figures are in the serif; its prose is in Inter; nothing in prose is
   set in small caps.
5. Every interactive element states its purpose in a sentence a stranger
   could act on.

`tests/test_ui_contract.py` enforces the fifth. The rest is judgement — which
is why it is written down.
