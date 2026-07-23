# Brand assets

| File | What it is |
|------|------------|
| `twinlight-logo.svg` | The primary mark + wordmark used in the README. Theme-aware: it switches to lifted colours under `prefers-color-scheme: dark`. |
| `twinlight-logo-concepts.drawio` | Six logo directions, the construction grid for the chosen mark, lock-ups, and the palette. |

## Working on the concepts file

Open `twinlight-logo-concepts.drawio` at <https://app.diagrams.net> (File →
Open From → Device) or in the *Draw.io Integration* extension for VS Code. It has
four pages:

1. **Concepts overview** — six directions with the reasoning for each, and a
   recommendation.
2. **Primary mark — construction** — the chosen mark on a 24×24 grid, with scale
   tests, clear-space rule and a "do not" list.
3. **Lock-ups** — horizontal, stacked, dark-background and one-colour variants.
4. **Palette** — the two-hue system and the rule that keeps it meaningful.

## The one rule worth keeping

**Cyan is the real network; violet is the twin.** That mapping holds in the
logo, in the web UI, and in any figure produced for a paper — which is what
lets a reader decode a plot before finding the legend. Everything else in the
identity is negotiable.

## Exporting

From diagrams.net: File → Export as → SVG, with *Transparent Background* on and
*Include a copy of my diagram* off. For a favicon, export the mark alone (no
wordmark) at 512 px, and drop the dash pattern below ~20 px — at that size the
dashes fill in and the two rays stop being distinguishable.
