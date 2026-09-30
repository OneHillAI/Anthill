# Desktop splash screen: replace the generic orange square with the Anthill mark

## Problem

`src-tauri/splash/index.html` is the first thing the desktop shell ever shows: `tauri.conf.json` points
`build.frontendDist` at this directory, and `src-tauri/src/lib.rs`'s `setup()` starts the update check and
backend boot on it before there is anything real to navigate to (`show_on()` only replaces it once the
sidecar answers HTTP, and `show_startup_error()` replaces it on a fatal boot failure).

What that first screen actually showed was a generic placeholder: a `64x64` orange-gradient rounded square
(`.mark`, `background: linear-gradient(135deg, #ffb020, #ff7a18)`) with the caption "Starting Anthill" and
three pulsing dots. It carries no Anthill branding and does not match the app's real identity - the app's
actual mark (`assets/anthill-mark.png`) is a green hill/mound, and its accent color everywhere else in the
product is the deep-pine green defined by `--accent` in `anthill/web/static/style.css`, not orange. Founder
report: "when the app is opened, it shows an orange square for loading."

## Fix

Redraw the splash entirely in inline SVG/CSS (still zero external assets, zero network - the window has to
render correctly before the backend exists to serve anything, and before the app's real stylesheet or
self-hosted webfont are reachable):

1. **The hill mark.** A small inline-SVG redraw of `assets/anthill-mark.png` (dome, two strata arcs, dark
   entrance, ground line, soft shadow), colored from the same tokens the rest of the app uses
   (`--accent` / `--accent-dk`), not a copy of the PNG - the PNG is `RGBA` and could be referenced, but an
   inline shape needs no bundled file and can't fail to load.
2. **Ants, running.** Five small inline-SVG ants beneath the hill, each a simple 2-frame leg-position
   swap (`ant-legs-a` / `ant-legs-b`, hard-cut via `steps(1)` rather than crossfaded, so it reads as a quick
   trot) plus a synced body bob, staggered per-ant via a `--d` custom property so they don't move in
   lockstep. The whole row also has a small shared side-to-side drift (`ant-creep`) to read as forward
   motion without needing an actual scrolling/looping sprite sheet. Color sampled from
   `assets/ant-mark.png`'s body (`#D48926`) since that PNG's own background isn't transparent and can't be
   dropped in directly.
3. **Caption.** "We're on our way!" replaces "Starting Anthill"; the three pulsing dots are removed (the
   ants now carry the "something is happening" cue).
4. **Theme-aware background.** The old splash was hardcoded dark (`#0f1115`) regardless of the OS setting,
   even though `color-scheme: light dark` was already declared on `:root`. It now follows
   `prefers-color-scheme` using the same light/dark token values as the rest of the app (`--bg`, `--text`,
   `--accent`), so the splash doesn't jump from dark to a light app window (or vice versa) once the backend
   comes up.
5. **`prefers-reduced-motion: reduce`** freezes the ant-leg/bob/creep animations at a resting pose instead
   of forcing motion on a user who has asked the OS not to show it.

No Rust, `tauri.conf.json`, or boot-sequence changes - this is a content-only change to the one HTML file
Tauri already serves as the frontend for that window.

## Acceptance criteria

- Opening `src-tauri/splash/index.html` directly (no build, no backend, no network) renders the hill mark,
  five running ants, and the caption "We're on our way!" - no broken image requests, no references to
  anything outside this one file.
- The background, hill, and text follow the OS light/dark setting and match the app's own `--accent` /
  `--bg` / `--text` values for that mode (verified: light `#F7F8FA` bg / `#2F6B4F` accent; dark `#141619` bg
  / `#4E9E78` accent).
- With `prefers-reduced-motion: reduce`, the ants render in a static resting pose (no bob, no leg swap, no
  drift) instead of animating.
- No new files are added under `src-tauri/splash/` and `tauri.conf.json` / `lib.rs` are unchanged - the
  window's boot sequence (update check -> backend spawn -> health poll -> navigate) behaves exactly as
  before; only what renders while that sequence runs has changed.
