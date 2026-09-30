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

**3. Installed models that aren't in the curated catalog are now real, selectable rows in the same
list - not a second-class, read-only afterthought.** Founder pushback after seeing the first version
of this fix live: "I don't understand why we need to have a model storage list. All the models that
have been installed need to just show up in that list... they cannot just evaporate," and, more
pointedly, "these are core models: Qwen, Mistral, Llama, Granite... why should they not be there in
the normal list?" The curated catalog (`anthill/hosting/model_catalog.json`) is a fixed set of ~29
entries; on the founder's own test machine, 9 of 13 actually-installed models (including a plain
`mistral:7b`, an older release the catalog no longer carries in favor of "Mistral Small 3 24B") were
in `ollama list` but invisible everywhere in this picker - no row, no way to select them as the
active model without dropping to the CLI. The first attempt at fixing this added them to a separate,
read-only "Other models on this device" section with no checkbox; the founder rejected that too, on
the grounds that a model already proven to fit and run on this machine is not less real than a
catalog entry.

The actual fix: `OllamaBackend.installed_models_with_sizes()` (new; `installed_models()` now
delegates to it, so both callers share one `/api/tags` probe instead of two) feeds
`_model_picker_view`'s new `other_installed` list - every installed tag not in the catalog, sorted
alphabetically with its size. `_council_builder.html` renders these as ordinary
`<label class="mp-row council-row">` rows appended to the *same* `.mp-list` the catalog renders into,
each with a real `<input type="checkbox" name="council_models" value="{{ m.tag }}">` - the identical
mechanism a catalog row uses, so selecting one round-trips through `/personalize/compute` exactly
like any other. This is safe because `_apply_solo_compute` already has no catalog-membership check
at all for a single-model selection (`cfg.ollama_model = council[0]` is set unconditionally) -
catalog lookup is only used for `params_b`-dependent cloud-sizing and multi-model memory-fit math,
which no-ops gracefully when a tag isn't found. Each row shows an unconditional `Fits` badge (it's
already installed and running - stronger evidence than the catalog's own size-estimate-based fit
check) and an honest `{{ size }} GB - not in the curated list` meta line instead of a fabricated
catalog description, plus the same `installed`/`Uninstall` badges as any other row.

## Verification

New tests in `tests/test_settings_model_picker.py`: the Uninstall control appears next to an
installed, non-active model's badge and is absent for the active/lead tag
(`test_uninstall_button_next_to_installed_badge_hides_for_the_active_model`); it never appears during
first-run setup even though the `installed` badge itself still does
(`test_uninstall_button_is_never_shown_during_first_run_setup`); a non-catalog installed tag renders
as a real checkbox row with the same `council_models` input name, an honest "not in the curated list"
label, and its own Uninstall control
(`test_non_catalog_installed_model_is_a_real_selectable_row`); selecting one as the council lead
saves `cfg.ollama_model` with no catalog check and no error
(`test_selecting_a_non_catalog_installed_model_as_lead_saves_with_no_catalog_check`).
New tests in `tests/test_ollama_serving.py` for `installed_models_with_sizes()`: returns
name/size-in-bytes pairs from a real `/api/tags` shape
(`test_installed_models_with_sizes_returns_name_and_size_pairs`) and degrades to `[]` on a
non-JSON response, same never-raises contract as `installed_models()`
(`test_installed_models_with_sizes_survives_a_non_json_response`).
`tests/test_local_model_settings.py::test_model_storage_card_lives_in_the_model_tab_before_advanced`
(new): the storage card renders inside the Model panel, before the `Advanced` heading, and is absent
from the This-device panel. `tests/test_solo_cloud_training.py`'s existing placement test (from the
2026-09-29 move) asserted the *previous* location - updated to assert the new one instead (renamed to
`test_model_storage_lives_in_model_tab_before_advanced_not_this_device`), since it was directly
testing what this change deliberately reverses. Full suite: 2813 passed, 8 skipped. `ruff check`/
`ruff format --check` and the em/en-dash slop gate both clean across all six touched files.

Verified live in a real browser: the storage card renders in the Model tab with the same
header/tooltip style as its siblings, sits before Advanced, and its "Manage" link still leads to the
working `/models#installed` page unchanged. The inline Uninstall button renders next to "installed"
in the model list, computed-style-matches the "recommended"/"Lead" filled badges exactly (font-size,
weight, padding, border-radius, line-height all identical - the only difference is against the
separately-styled *outlined* "installed" badge, which already carried its own 1px border before this
change).
