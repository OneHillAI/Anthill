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

## Follow-up: the sidebar footer, and decluttering its help menu

Founder, 2026-10-02 (with a screenshot of the sidebar's account-chip popover): replace the
"Solo &middot; private" subtitle with the version number, and remove the "How it works" and "Setup"
links from that same menu.

**Why "Solo &middot; private" specifically:** it was a hardcoded constant in the non-org branch of
the footer chip (`_sidebar.html`), always the same text regardless of what the account had actually
done (connected a cloud GPU, started training, etc. - `test_connected_solo_account_keeps_solo_nav_label`
already existed to confirm it never changes) - a Solo account's one piece of truly dynamic,
genuinely useful info in that slot is which version is running, not a label that never updates.

**Fix:** `app_version` moved out of `personalize_get`'s own context into `_nav_context` (`app.py`),
the context processor Jinja2Templates already runs on every render - so it's available sidebar-wide,
not just on one page (the explicit pass-through in `personalize_get` was removed as now-redundant;
`personalize.html`'s `{{ app_version }}` reference needed no change). The footer chip's non-org
branch now shows `v{{ app_version }}`; the org branch (showing the org name) is untouched, since an
org account already has a more useful label there. The "How it works" and "Setup" links were removed
from the footer's "..." overflow menu - they remain reachable from the Dashboard and elsewhere in the
app, so nothing is orphaned; this only removes a redundant second entry point, as part of moving that
content's primary home to docs.anthill.run (handed to a sibling session - see below, not done in this
PR).

**Verification:** `tests/test_solo_cloud_connect.py::test_connected_solo_account_keeps_solo_nav_label`
updated to assert the version string instead of the old hardcoded text (same invariant: no org name
banner appears for a non-org account). `tests/test_nav_roles.py::test_help_links_moved_from_rail_to_footer`
updated to assert the two links are now absent from the footer menu. Live-verified in a real browser:
opened the footer's account chip and confirmed it read "Personal / v1.0.0", and confirmed via the
DOM that the open help menu contained exactly Take a tour / Contribute / Enable notifications - no
How it works, no Setup. Full suite: 2833 passed, 3 skipped (non-browser) + 22 browser.

**Not done here:** migrating `docs/how-it-works.md` and `docs/setup.md`'s actual content onto
docs.anthill.run is a different repo/surface, handed to "Regenerate the README brand banner" as a
peer-session request rather than attempted in this PR.
