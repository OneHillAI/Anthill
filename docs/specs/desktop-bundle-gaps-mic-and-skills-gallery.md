# Two desktop-bundle gaps: dictation never asks for permission, the skills gallery is never bundled

## Status

Fixed. Two independent papercuts reported live from the packaged desktop app - neither reproduces in
`anthill web` (dev mode), since both are specific to how the Tauri app and its PyInstaller sidecar are
built, not to the application code the dev server already exercises.

## Problem 1: the dictation mic button appears to do nothing

`anthillDictate()` (`anthill/web/static/dictation.js`) uses the browser's Web Speech API
(`SpeechRecognition`/`webkitSpeechRecognition`). On macOS, Tauri's native webview (WKWebView) does
support speech recognition - but only once the app's `Info.plist` declares
`NSMicrophoneUsageDescription` and `NSSpeechRecognitionUsageDescription`. Without them, macOS denies
mic/speech access before the app can even prompt the user, and the denial surfaces to the page as a
silent failure (`rec.start()` throws synchronously, caught by dictation.js's own try/catch, which just
removes the "dictating" class) - no error, no fallback message, nothing visibly happens. The repo had
no `Info.plist` at all; `src-tauri/tauri.conf.json`'s `bundle.macOS` only set `entitlements`.

## Problem 2: the Skills page's "Template gallery" is empty

`anthill/skills_gallery/` (4 real vendored templates: brand-guidelines, frontend-design,
internal-comms, theme-factory) is read at runtime by `agent.skills.load_gallery()` and rendered on
`/skills` behind `{% if gallery %}`. Neither PyInstaller spec (`Anthill.spec` for the standalone .app,
`Anthill-sidecar.spec` for the Tauri sidecar) listed this directory in `datas` - both only bundled an
unrelated top-level `skills/` directory. So in every packaged build, `load_gallery()` finds nothing and
the entire "Template gallery" section never renders - not a partial list, the whole section is absent,
which is exactly what was reported ("I'm also missing the skills templates").

## Fix

- `src-tauri/Info.plist` (new): declares both usage-description strings, wired via
  `bundle.macOS.infoPlist` in `tauri.conf.json` (confirmed against Tauri v2's actual JSON schema -
  `MacConfig.infoPlist`, `string | null` - not just the doc prose).
- `Anthill.spec` and `Anthill-sidecar.spec`: both add
  `("anthill/skills_gallery", "anthill/skills_gallery")` to `datas`, alongside the existing (different)
  `("skills", "skills")` entry.

## Verification

- Rebuilt the actual `anthill-server` PyInstaller sidecar from this exact commit (not a description of
  what should happen): booted it headless, signed up, and confirmed `GET /skills` renders "Template
  gallery" with all 4 real templates (Brand Guidelines, Frontend Design, Internal Comms, Theme Factory).
- Rebuilt the sidecar from the commit *before* this fix and confirmed the same request shows no gallery
  section at all - a clean before/after, not just a plausible theory.
- `Info.plist` validated with `plutil -lint`; `infoPlist` confirmed to exist in Tauri v2's real schema
  (`https://schema.tauri.app/config/2`, `MacConfig.infoPlist`), not just the documentation prose.
- The mic-button fix cannot be end-to-end verified without a signed, notarized build run on a real Mac
  with a fresh permission-prompt state (this environment cannot grant/deny a real macOS permission
  dialog) - verification here is that the Info.plist keys are the exact ones Tauri's own docs/schema
  and Apple's TCC (privacy permission) system require, matching the documented failure mode precisely.

## Out of scope

- Any change to `dictation.js` itself - the JS code is correct; the gap was purely the missing
  entitlement, not application logic.
- Auditing every other PyInstaller `datas` entry for similar gaps - this fixes the two reported cases,
  not a general audit of bundle completeness.
