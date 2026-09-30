# Design tokens, Wave 1

Status: implemented. Owner: design. Scope: `anthill/web/static/style.css` + the inline-styled templates.

## Why

The UI read small, dated and cramped: a 14px base with a 10-13px tail, no spacing or type
scale, a warm parchment palette with a dark sidebar, and ~120 hardcoded `font-size` and colour
values scattered inline. This first wave introduces the token layer and repaints the app to one
clean bright world so later waves can restructure flows on a stable base.

## Decisions

- **One bright world.** Cool-neutral content ground (`#F7F8FA`), white cards, and a light
  sidebar. The old dark espresso rail is retired.
- **Accent = deep pine green** (`--accent:#2F6B4F`), the action colour for buttons, links, active
  nav, focus and badges. Chosen over amber (too close to Claude) and blue (too cold). Kept as a
  deep pine, distinct from the brighter `--success` grass so brand and success never blur. The old
  `--amber*` brand tokens are replaced by `--accent*`.
- **The mound mark moves into the app's green family, on white/`--bg`, not a separate soil
  family.** Superseded decision: this wave originally kept the mound's identity colour
  deliberately apart from the chrome (a soil/clay family, warm against the cool bright ground) so
  the brand mark read as distinct from UI accents. In practice that meant the mark carried its own
  palette nobody else in the app used, and every rendering of it (README banner, desktop/PWA icon
  set, the two inline sidebar/chat SVGs) had to be migrated by hand and repeatedly drifted out of
  sync with each other and with this token layer. The mound now uses a green drawn from the same
  pine-green family as `--accent`/`--accent-dk` (a first pass that tinted the dome literally
  toward `--accent` read as moss/mildew on the mound's rounded silhouette, so the dome is a more
  saturated, more vivid green instead - not a plain `--accent` tint, but the same hue family) on
  the app's own white/`--bg` ground instead of a separate cream. One palette, one source of truth:
  `scripts/gen_icons.py` for every generated tile, `scripts/recolor_logo_banner.py` for the README
  banner, and `_sidebar.html`'s `#ah-mound`/`#ah-logo` symbols for the two inline uses. The mark's
  actual shape (a dome with a centered dark arch) reads as a generic "abstract mark with a hole" -
  the same silhouette several other AI companies use - and is a separate, bigger redesign this
  wave does not attempt.
- **16px base, 1.5 line-height.** Type scale tokens `--fs-1..7` (minor third) and 8pt spacing
  tokens `--sp-1..7` are defined for later per-surface adoption.
- **Dark theme** is a clean neutral charcoal (not warm brown), with the accent lifted for
  contrast. Structure comes from hairline borders and separated surfaces.
- **All colour values are AA verified** (body 15:1, muted 6.5:1, accent on white 6.3:1, green CTA
  label white 6.3:1).

## Out of scope (later waves)

Per-surface density and progressive disclosure (settings prose to `?`/Advanced), the empty-state
and composer rework on Chat, the ant-street loader refinement (ants as real ants, not dots), and
migrating the remaining hardcoded `font-size` values onto the scale.
