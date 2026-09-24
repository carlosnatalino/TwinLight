# Logo assets

| File | What it is |
|------|------------|
| `twinlight-logo.svg` | Mark and wordmark, for light backgrounds. The wordmark also switches to its light variant under `prefers-color-scheme: dark`. |
| `twinlight-logo-dark.svg` | Mark and wordmark, for dark backgrounds. |

The README selects between the two with a `<picture>` element. The media query
inside `twinlight-logo.svg` follows the viewer's operating-system theme rather
than GitHub's own theme setting, so the explicit dark variant is the more
reliable choice when embedding the logo elsewhere.

## What the mark means

The left half is the real network and the right half its digital twin. Two
signals cross the seam in opposite directions: real → twin carries observed
state, twin → real carries prediction. The twin's interior is drawn in grey,
with dashed links and a hollow node, to show structure the twin infers rather
than copies exactly.

## Palette

| Role | Colour |
|------|--------|
| Real network | `#0284C7` |
| Digital twin | `#8B5CF6` |
| Inferred structure | `#64748B` |

Each colour meets the WCAG 1.4.11 non-text contrast ratio of 3:1 on both light
and dark backgrounds, so the mark itself needs no theme variant; only the
wordmark changes. Blue for the real network and violet for the twin is a
useful convention to keep in figures made with TwinLight.
