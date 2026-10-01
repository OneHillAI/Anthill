# Spec: no version number visible anywhere in the app

Status: implemented. Lane: `pillar:feature`.

## Problem

Founder, 2026-10-01: "do i see a version of the app inside the app?" Checked: no. Not in any web
template, not in a Tauri About dialog or menu, not in a footer. Version strings exist in
`pyproject.toml`, `anthill/__init__.py`, the build-time `src-tauri/tauri.conf.json` (stamped from
the release tag by CI, not the source of truth), `CHANGELOG.md`, and the GitHub Releases page - none
of them visible while using the app.

## Fix

Settings' "This device" tab (`/personalize#device`) is the natural home: already the one place for
genuinely device/install-local settings (Appearance lives there for the same reason - nothing about
which model runs, just this install). Added a small "Version" card directly below Appearance,
showing `Anthill {{ app_version }}`. `personalize_get` (`anthill/web/app.py`) now imports
`anthill.__version__` and passes it into the template context as `app_version` - the same single
source of truth `pyproject.toml`'s build already derives from, so this can never drift from what
actually shipped.

## Verification

`tests/test_settings_version_display.py::test_this_device_tab_shows_the_app_version` - a Solo
account's `/personalize` response contains `Anthill {{ anthill.__version__ }}` inside the `device`
tab panel specifically (not just anywhere on the page). Also live-verified in a real browser:
logged into a real server instance, opened Settings -> This device, and confirmed the card renders
with the real installed version. Full suite: 2831 passed, 5 skipped.
