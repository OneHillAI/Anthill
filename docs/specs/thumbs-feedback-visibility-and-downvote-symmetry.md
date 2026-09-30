# Spec: visible thumbs feedback, and a thumbs-down that actually does something

Status: implemented. Lane: `pillar:feature`.

## Problem

Founder QA on the just-shipped v0.12.9 build: "thumbs up and down in chat doesn't seem to work."
Live reproduction (both the page-reload and live-streamed render paths) showed the click and the
server round-trip both worked correctly - `rate()` posts to `/chat/{id}/thumbs`, the response is
`200`, and the button gets a `selected-up`/`selected-down` class - but the ONLY visual effect was
`.msg .thumbs.selected-up { color:#2D6A4F; }` / `.selected-down { color:#A32D2D; }`: a small tint on
the icon's stroke color, no background, no fill. On a small icon in a row that now carries up to six
of them, that reads as "nothing happened" even though the rating was recorded.

Separately, a genuine asymmetry: a thumbs-up finds the matching `TrainingExample` (same `output` +
`org_id`) and promotes it to `quality="gold"` - a real effect on what `export_jsonl` (default
`min_quality="silver"`) will later export for fine-tuning. A thumbs-down only ever set
`ChatMessage.thumbs_up = False` (which feeds `metrics.py`'s org satisfaction-% number) and touched
the matching example not at all. If that example had already been promoted to gold (an earlier
upvote later regretted) or silver (org-corroborated), a downvote couldn't undo either - a
known-bad answer stayed eligible for training regardless of the explicit "this was wrong" signal.

## Fix

**Visibility** (`chat.html`): `.selected-up`/`.selected-down` now add a background fill
(`rgba(45,106,79,.16)` / `rgba(163,45,45,.16)`) at the same weight as the existing
`.badge-cache`/`.badge-escalated` chips, so a rating reads as a real status change rather than a
subtle stroke-color shift. Dark-mode overrides added in both existing guard blocks
(`@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]`), matching the pale-on-pale
fix already applied to the other chat badges.

**Downvote symmetry** (`/chat/{conv_id}/thumbs` in `app.py`): the matching-`TrainingExample` lookup
now runs for both directions, not just `value == 1`. A thumbs-down demotes a `gold`/`silver` example
back to `bronze` - below `export_jsonl`'s default floor - undoing an earlier promotion instead of
leaving it in place. A downvote on an example that's already bronze is a no-op (nothing to demote,
matches the pre-existing "only write when there's a real signal" shape of the thumbs-up branch).

## Verification

`tests/test_chat_thumbs_training_quality.py` (new): thumbs-up still promotes to gold; thumbs-down
demotes an existing gold example to bronze; thumbs-down demotes silver too; thumbs-down on an
already-bronze example leaves it untouched (`user_id` stays unset, confirming no unnecessary write);
`ChatMessage.thumbs_up` still records for the satisfaction metric regardless. The pre-existing
`test_idor_owner_scope.py` thumbs test passes unmodified (9 total, this file + that one). The
background-chip fix was verified visually: seeded a two-answer conversation, thumbs-up on one and
thumbs-down on the other via a real browser, confirmed via `getComputedStyle` that each renders its
distinct background/color (`rgba(45,106,79,.16)` / `rgba(163,45,45,.16)`) - no dedicated browser test
added (pure CSS, no behavior to assert beyond what the computed-style check already confirmed).
