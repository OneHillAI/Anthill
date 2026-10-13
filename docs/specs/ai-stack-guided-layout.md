# Choose your AI: models sit inside Your machine / Your cloud, the provider stays separate

Lane: `pillar:platform`

## Problem

The first-run "Choose your AI" step and Settings' "Change where it runs" ask three things: where the AI runs
(Your machine or Your cloud), which model(s), and which inference provider, if any. They were three blocks one
after the other. Nothing showed that the models belong to the machine or the cloud that was just picked, and for
Your cloud the provider and key opened as a separate block above the model list. The provider card was labelled
"Recommended for most people" although it is optional, its one-line explanation did not say why a person
might want it, and two image-understanding checkboxes sat between it and the Continue button as if they were part
of the three steps.

## Fix

One layout, used by both screens (the same three partials, so the two cannot drift):

- **Your machine / Your cloud are tabs, and the model choice is their body.** The selected card opens into a
  panel under the cards (same accent border, no seam between them). The panel holds that tier's own content:
  for Your machine the models that fit this machine; for Your cloud the cloud provider, its API key and the
  cloud models. Choosing the other card swaps the panel's content and moves the tab. On a narrow window the
  cards stay side by side as compact tabs. The Model / Knowledge base box and the "Good fit for" sentence are
  hidden there, and each privacy line names its own subject instead ("Model and knowledge base: Stays on this
  device"; on Your cloud "Model: Stays in your cloud account" and "Knowledge base: Sent to your cloud when used"),
  so the disclosure about what goes to the cloud is still complete. Nothing moves when the other card is clicked and the Tab order is the
  visual order. This is the layout the desktop window has at its smallest size.
- **The inference provider is its own card after the panel, not attached to the tier.** It is labelled
  "Optional". "Don't use one" is selected by default. Its text now says why: "Your model may not be strong
  enough for your hardest work. A provider adds frontier-scale open models." at the same size as the machine
  card's hardware line (15px).
- **Advanced is one collapsed block.** In setup it holds the image-understanding choices (on by default, the
  larger model still opt-in) together with the existing "coming soon" rows; they no longer sit between the provider
  card and Continue. Its summary line says "Image understanding on" (or "off"), because the option starts a
  background download of about 2.4 GB and is no longer in view. The summary is set from the checkbox when the
  script runs, when the box changes and when the page is shown again, so a box restored by the browser after a
  back/forward or a reload never sits under the wrong word. In Settings it is the existing
  "Advanced setups".
- **The three setup steps are numbered** 1 (where it runs), 2 (which model, inside the panel) and 3 (the optional
  provider). Settings has no numbers: its summary card already names the first question, and a test keeps that
  heading from being repeated there.
- **The page is mostly right before any script runs.** The card for the saved choice carries the `sel` class and
  `aria-pressed="true"`, `data-sel` names the side, the panel shows that tier's content, and only that tier's model
  boxes are enabled (so what would be posted is the right list), so an account saved on Your cloud does not flash
  the Your machine card as selected. The cloud provider cards (RunPod, Lambda) are drawn by the script, so
  without it that area is empty. An account saved on Your cloud with no provider chosen yet now gets those cards
  after the script runs (before, they appeared only after a card was clicked). The script (`ccMarkTier`) keeps the class, the
  pressed state, `data-sel` and the panel's name (`aria-labelledby` the selected card's name) together on a click
  and when the saved state is loaded. The cards are buttons that say whether they are pressed and what they
  control, and the panel is a labelled region. A focused card is drawn above the panel so its whole focus ring shows.
- **Contrast.** Text on the accent and amber fills is dark in the dark theme (white on the lighter green was 3.2:1,
  on the amber 4.0:1; both are above 5:1 now).
- **Small layout changes that came with it,** stated here because the first version did not: the cards no longer
  lift on hover or when selected (a card that moves would break the seam with the panel). The grey strip that
  used to sit under the cards ("Your wiki, memory and files are stored on this device either way - Your cloud only
  moves where the model itself runs", with a help bubble) is removed: with the cloud card saying "Knowledge base:
  Sent to your cloud when used" it contradicted the cards and read as noise (founder, 2026-10-12). The cards'
  privacy lines are the one statement about where the knowledge base goes.
- No new lines or helper text: the connecting line, the explanation under the first step and the summary strip
  proposed earlier were dropped on review.

## Not changed

Every form field name and value is the same, so saving stores exactly what it did. The model lists, council
rules (up to three models, the first is the lead, the memory floor), the cloud providers, the inference provider
list and its key and model picker, the organization setup path, and the text of the "Where your AI runs" summary
in Settings are untouched. The "Frontier models" and "Skip for now" links under the setup form stay where they were.

## Acceptance criteria

- With Your machine selected, the model list is inside the panel under the cards, the left edge of the selected
  card is the left edge of the panel, and the card's bottom meets the panel's top (browser test).
- Selecting Your cloud shows its provider choice, key field and model list inside the same panel and hides the
  machine list; the right edge of the selected card is the right edge of the panel (browser test).
- The inference provider card is outside the panel, at the same distance from it whichever tier is selected,
  and "Don't use one" is selected with an empty provider on load (browser test).
- Continue stays disabled until a model is ticked, and the form still posts `compute`, `council_models`,
  `council_lead` and `enable_vision` (browser test).
- Advanced is collapsed, holds the vision choices (image understanding on, the larger model off) and the "coming
  soon" rows, and the old loose vision block is gone (unit and browser tests).
- At 620px wide the cards stay side by side in the same order as the Tab order, do not move when the other card
  is clicked, and the selected one still opens into the panel (browser test).
- An account saved on Your cloud renders the cloud card as selected, pressed, with the cloud content showing,
  without any script, and the same with script (unit and browser tests). The cards say which one is pressed, the
  panel is named for the selected card, and a keyboard-focused card is above the panel (unit and browser tests).
- The posted values, not only the names, are the same: `compute`, `council_models`, `council_lead`,
  `enable_vision` and an empty `escalation_provider` by default (browser test); in Settings every named field inside
  the panel carries `form="compute-form"` (unit test).
- The text on the number and "Optional" badges is above 4.5:1 in both themes (unit test).
- The Advanced summary follows the image-understanding checkbox (browser test).
- Settings' "Change where it runs" opens the same panel and keeps no numbered titles and no repeated "Where does
  your model run?" (unit and browser tests).
- "Recommended for most people" is gone; the provider card says "Optional" and gives the reason above.

## Known limits

- Checked visually in a Chromium browser, light and dark, at 1180px, 880px and 560px wide. Not checked in the
  desktop app's own web view or on Windows and Linux.
- The setup page's Continue button is rendered disabled by the server (as on main) and needs the script to enable
  it; the Settings page posts the same fields without the script.
- The existing "ollama spawn guard" in `tests/conftest.py` reports an error at the end of browser test modules that
  load a model page on a machine with Ollama installed; it does the same on main and is hidden in CI.
