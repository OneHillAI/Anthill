# Spec: the published guide describes the new first-run setup, model list, downloads, Settings and chat page

Status: implemented. Lane: `pillar:model`. Issue: #285. Follows #239 (PR #244) and #280 (PR #283). Related: #261, #263, #265, #262, #282, #200, #202, #183, #230.

## Problem

The first-run setup, the model list, model downloads, Settings and the chat page were reworked between 2026-11-18 and
2026-11-19, and earlier model work was never described. The pages published at docs.anthill.run still described the old
first run and said nothing about the rest:

1. `docs/using-models-and-compute.md` said you pick Your machine or Your cloud at "Choose your AI" in setup, and that
   image understanding is turned off under Advanced in setup. First run now says the model runs on this computer, has
   already chosen the best fit ("Best fit for this machine"), and leaves cloud for Settings; the optional inference
   provider is its own fourth step with "Not now", and "Enable image understanding" sits under Advanced on that step
   (`model_picker.html`, `_compute_chooser.html`, `_inference_provider.html`, `_setup_steps.html`).
2. The fit labels are now "Fits", "Runs, tight" and "Runs, slower", with too-big models in a closed list
   (`_council_builder.html`); a computer with no GPU is described by its memory (#183, #230).
3. Live download progress, reuse of a running download and "Cancel download" were not described (#200, #202;
   `models.html`, `dashboard.html`).
4. The Settings tabs and the "No unsaved changes" note were not described (#263).
5. The answer source label ("On this device", "Sent over the network") and the linked "Pages in context" were not
   described (#262, #282; `chat.html`).
6. The "Try this next" dashboard cards for a new account were not described (#265; `dashboard.html`).

## Requirements

1. `docs/using-models-and-compute.md` SHALL describe the first run as it is: one decision, the model runs on this
   computer, the best fit is chosen and marked, the button names the model, the provider step is optional with "Not
   now", and image understanding is under Advanced on that step; and SHALL say that bigger models in your own cloud are
   set up later in Settings.
2. It SHALL use the labels the app shows ("Fits", "Runs, tight", "Runs, slower", "Models too big for this machine") and
   say how a computer with no GPU is described.
3. It SHALL describe download progress (percentage, size, speed, time left) on the Dashboard, the Models page and
   Settings, that a running download is reused, and "Cancel download".
4. `docs/setup.md` SHALL name the four setup steps and point to the models page for the last two.
5. `docs/USING_ANTHILL.md` SHALL list the Settings tabs, say nothing saves until Save, name the two unsaved-changes
   messages, and describe "Try this next".
6. `docs/using-chat-and-knowledge.md` SHALL describe the answer source label and that "Pages in context" are links.
7. Every label and figure SHALL be one the templates or code show. Headings, page order, links between pages and the
   publish whitelist SHALL NOT change.

## Known limits

- The chat speed line (first-word time and tokens per second, #222) is not described: it could not be found in the chat
  template.
- The setup token and mesh token are already in `docs/setup.md`. Backups, privacy, remote Ollama and Linux are a separate
  documentation issue.
- The text describes a Solo account's first run. An organisation account's step 3 shows provider tiles for the shared
  backend instead.

## Acceptance

- Each statement matches the template or constant named above.
- The doc tests pass and `node docs-site/scripts/collect-docs.js` copies all eight pages.
- After merge, the live pages show the new text.
