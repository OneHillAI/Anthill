# Helper-text purge and the `?` popover, Wave 2

Status: implemented. Owner: design. Scope: `style.css` + surface templates.

## Problem

The app carried standing explanatory prose on almost every surface: multi-line intros, under-heading
descriptions, and footer walls. It read as clutter and made screens hard to scan. Founder direction:
if a control is not clear, redesign it; the only permitted inline help is a `?` icon opening a small
popover.

## Rule

- No standing explanatory prose in the UI.
- If a control is not self-evident, first try to make it self-evident (label, placeholder, structure).
- If an explanation is still genuinely needed, attach a single `?` next to the label or heading that
  opens a small popover. Nothing more.
- Onboarding and a Help page carry longer teaching content, not the working surfaces.

## The primitive

`.help` in `style.css`: a 16px round `?` button that shows a `.help-pop` popover on hover and focus.

```html
<button type="button" class="help" aria-label="More info">?<span class="help-pop">short text</span></button>
```

Keyboard focusable, popover inverts (`--text` on `--surface`) so it reads in both themes.

## Surfaces done in this wave

Settings, Tasks, Agents, Chat composer, Integrations. Each lost its intro and under-heading prose;
genuine explanations (cache threshold, PII scrubbing, agent governance, task drafting, integration
approval) moved into `?` popovers.

## Known limitation

A `?` on a control near the very top of the scroll area opens upward and can clip. Smart flip
(open downward near the top) is a follow-up.

## Follow-ups

Remaining prose-heavy surfaces (Personalize, Models, Backend, and others) get the same pass.
