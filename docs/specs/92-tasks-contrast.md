# Spec: Tasks meet WCAG AA contrast in light and dark themes

Status: implemented. Lane: `pillar:feature`. Issue: #92.

## Problem

Parts of the Tasks pages fall below WCAG AA contrast, most in the dark theme, and some colours are written into
the templates instead of coming from theme tokens. Measured on the rendered pages before the change, in the
dark theme: white text on the primary button 3.2:1, the failed-result text 2.5:1 and the secondary text on
cards 2.9:1; placeholder text in a field 3.55:1 (the browser default). In the light theme the verifier line on a run ("result needs review") used `--warn` at 3.99:1 on
white and 3.46:1 on the subtle surface. A form-field boundary was 1.2:1 (light) and 1.4:1 (dark).

## Requirements

- Normal text, including placeholder text in fields, meets 4.5:1 and large text meets 3:1, against what it is drawn on, in forced light, forced dark
  and the system dark preference.
- A form field that is identified by its boundary (text, number, date and time inputs, selects, text areas) has a
  boundary of at least 3:1 against the surface behind it (WCAG 1.4.11). A field with no `type` attribute (the time zone box) gets the same surface and text colour as the other fields, so its
  placeholder is not drawn on the browser's white default in dark. A focused field still changes its border to
  the accent colour, as it did before (the focus rule follows the field rule at the same specificity), and shows
  a 2px accent ring while it is focused.
- New semantic tokens in `style.css`, defined once for light and in both dark blocks: `--btn-primary-bg`,
  `--btn-primary-hover`, `--danger-text`, `--warn-text`, `--clay-text`, `--info-dot` and
  `--control-border`. The existing `--danger`, `--warn`, `--success` and `--accent` stay for fills and icons.
- `.btn-primary` uses the primary button tokens, so white text on it passes in both themes. This class is shared
  across the app, so the dark-theme primary button is slightly darker on every page.
- The Tasks templates use classes for colour (`ink-clay`, `ink-warn`, `live-dot`) and `--control-border` for
  field borders, not literal colours. The Tasks pages sit in a `tasks-page` wrapper, which scopes the field
  border rule.
- Scope (Solo or Org) is shown as a word. Its marker also differs in shape (round for Solo, square for Org),
  so it does not rely on colour, and the marker is hidden from assistive technology because the word is shown.
- The issue names an undefined `--text-muted` in the Tasks templates. It is not used there: it appears only in
  `tour.js`, `walkthrough.js` and `_council_builder.html`, with a fallback value, so it is left as it is.
- No change to layout or wording. A focused field keeps its accent border and adds a 2px accent ring.

## Measured ratios

The lowest ratio of each ink against the page, surface and subtle-surface colours, from the tokens:

| Token | Light | Dark |
|---|---|---|
| `--danger-text` | 5.09 | 6.72 |
| `--warn-text` | 5.13 | 8.17 |
| `--clay-text` | 4.84 | 6.70 |
| `--muted` | 5.98 | 5.69 |
| `--text` | 14.17 | 12.53 |
| White on `--btn-primary-bg` | 6.29 | 5.20 |
| White on `--btn-primary-hover` | 8.02 | 6.01 |
| `--control-border` | 3.69 | 3.43 |

## Acceptance criteria

- Every piece of text on the Tasks list (with a task in each state, the queue panel open and the create dialog
  open) and on the task result page (finished, failed and running) meets AA in the three theme settings.
- Every visible form field on those pages has a boundary of at least 3:1.
- The scope marker of an Org task and a Solo task differ in shape, and the scope word is present.

## Proof

- `tests/test_tasks_contrast_tokens.py` reads the token values from `style.css` and checks the ratios above,
  so an edit that lowers one fails without a browser.
- `tests/browser/test_tasks_contrast.py` measures the rendered pages (text, placeholder text and field borders, with the create dialog set to a daily schedule so the time zone row is measured too) from computed styles, compositing
  translucent backgrounds over what is behind them, in forced light, forced dark and the system dark
  preference, and checks the scope marker. It failed before the change (dark theme) and passes after.
