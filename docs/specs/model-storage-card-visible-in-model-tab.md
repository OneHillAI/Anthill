# Spec: model uninstall was too buried to discover

Status: implemented. Lane: `pillar:feature`.

## Problem

Founder QA: "this must be a solution for any user. They don't like a model. They need to be able to
uninstall it. Otherwise, it uses gigabytes of space on the computer." The capability itself already
existed and worked correctly - `/models#installed` lists every installed model with a "Remove"
button (the current/pulling model is correctly excluded) - but reaching it took three steps: Settings
-> the secondary "This device" tab (not the tab Settings opens on) -> a "Model storage" card -> a
"Manage" link to a separate page. For a user who just noticed their disk filling up, that's too much
to hunt through.

## Fix

Two complementary changes, both founder-directed.

**1. Uninstall right where the models are already listed.** `_council_builder.html`'s model list (the
one "Change where it runs" shows, and the setup wizard's own "Which model do you run?" screen) marks
each installed model with an `installed` badge. That row now also gets a red `Uninstall` badge/button
right next to it, for any installed model that isn't the currently-active tag (lead or any council
member) - clicking it `fetch()`s the existing `/models/delete` route (no new endpoint) and removes the
row on success, with a confirm-dialog first. Never shown during first-run setup (`in_setup`) - a
brand-new account has nothing worth uninstalling yet, and the point during setup is choosing, not
pruning. Never shown for the active tag - deleting your own running model would both fail
server-side and make no sense mid-selection. Styled as the same filled-badge chip as "recommended"/
"Lead" (identical font-size, weight, padding, border-radius, line-height), just in `var(--danger)` red,
so it reads as an action among the other status badges rather than a mismatched control.

Implemented as a `<button>` with a `fetch()` handler, not a nested `<form>`: this row already sits
inside the outer Settings form (or `chooser_form` in the setup wizard), and a nested `<form>` silently
detaches everything after it from its outer form (a recurring class of bug in this codebase).

**2. The standalone "Model storage" card is easier to find too**, for anyone who does navigate
Settings normally rather than uninstalling inline. It relocates from the secondary `data-st="device"`
panel into the `data-st="model"` panel (the tab Settings opens on), as its own always-visible card - a
sibling of "Where your AI runs" and the "Advanced" disclosure, positioned before Advanced - with the
same `?` help-tooltip header treatment as its new siblings. This exact card has already moved twice in
the days before this fix, both the founder's own calls: 2026-09-26 into the Model tab's Advanced
disclosure, 2026-09-29 back out to "This device" as its own card (nesting it inside Advanced was
itself "an extra buried sub-menu nested two levels deep"). This move keeps the lesson from both:
visible (not nested in a disclosure) AND in the tab Settings actually opens on.

Considered and explicitly declined (founder call): a new top-level Settings tab between Model and
Knowledge. Relocating within the existing Model tab is the smaller, more reversible change.

Also checked and left alone: `/models`'s own "Pull a model by tag" and "Frontier models" sections
(both collapsed `<details>`, above the installed-models table) look like they might duplicate
Settings' "Change where it runs," but don't - that flow only offers the curated, hardware-fit
catalog; pull-by-tag is the only way in the app to install an arbitrary Ollama/HuggingFace GGUF
model, and "Frontier models" is a static informational list with no selection control at all. Founder
confirmed: leave both as they are.

## Verification

New tests in `tests/test_settings_model_picker.py`: the Uninstall control appears next to an
installed, non-active model's badge and is absent for the active/lead tag
(`test_uninstall_button_next_to_installed_badge_hides_for_the_active_model`); it never appears during
first-run setup even though the `installed` badge itself still does
(`test_uninstall_button_is_never_shown_during_first_run_setup`).
`tests/test_local_model_settings.py::test_model_storage_card_lives_in_the_model_tab_before_advanced`
(new): the storage card renders inside the Model panel, before the `Advanced` heading, and is absent
from the This-device panel. `tests/test_solo_cloud_training.py`'s existing placement test (from the
2026-09-29 move) asserted the *previous* location - updated to assert the new one instead (renamed to
`test_model_storage_lives_in_model_tab_before_advanced_not_this_device`), since it was directly
testing what this change deliberately reverses. Full suite: 2807 passed, 8 skipped. `ruff check`/
`ruff format --check` and the em/en-dash slop gate both clean.

Verified live in a real browser: the storage card renders in the Model tab with the same
header/tooltip style as its siblings, sits before Advanced, and its "Manage" link still leads to the
working `/models#installed` page unchanged. The inline Uninstall button renders next to "installed"
in the model list, computed-style-matches the "recommended"/"Lead" filled badges exactly (font-size,
weight, padding, border-radius, line-height all identical - the only difference is against the
separately-styled *outlined* "installed" badge, which already carried its own 1px border before this
change).
