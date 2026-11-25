# Spec: "Remember this", a line added to the core page with a preview (slice b of step 2.3)

Status: proposed (build plan step 2.3, slice b). Lane: `pillar:knowledge`. Builds on `docs/specs/core-page.md` (the private core page and
its cap), `docs/specs/one-page-write.md` (one write function, a stale save is refused) and `docs/specs/answer-record-everywhere.md`.

## Problem

A person can edit the core page only by opening it and typing. The two other ways something becomes "remembered" today do not go through
the page: memory is distilled from chats by a model with no preview, and the agent's `remember` tool writes a line to the wiki log that
nothing reads. The architecture says what a person asks Anthill to remember becomes an entry on their core page, shown before it is saved,
with no silent write.

## Requirements

1. **Remember this, from an answer or a message.** Two routes for the signed-in owner, with no model call:
   - `POST /core-page/remember/preview` takes `text` and returns what will be saved: the line as it will be written, the core page as it
     will read after the save, the characters left under the cap, whether the line is already there, and any sensitive item found in it (see
     4). Nothing is written.
   - `POST /core-page/remember` takes the same `text` and the `base_hash` the preview returned, and adds the line. It writes as the
     person's own edit through the one write function. If the page changed since the preview, nothing is saved and the person is told
     (the stale-save rule). The line is the person's own words, edited in the preview; the model does not rewrite it.
2. **One line, under Preferences.** A line is added to `## Preferences` as `- <text>`, with the text collapsed to one line of at most 300
   characters. A line already on the page is not added again (the preview says so and the confirm does nothing). A line that takes the page
   over its cap is refused with the number of characters to cut, and the page is unchanged. The core page is created if it does not exist.
3. **The agent proposes, a person decides.** In a session that can read the core page (the owner's chat agent mode), the `remember` tool
   no longer writes to the wiki log. It proposes the same one-line change to the core page through the review queue
   (`propose_wiki_write(..., always_review=True)`, as `write_wiki_draft` does) and tells the model it is waiting for a person. In a session
   that cannot read the core page (a task, a persistent agent, an organisation turn) the tool is not offered. Nothing an agent proposes
   reaches the page, or an answer, before the owner approves it.
4. **A sensitive item is named, not blocked.** The preview runs the existing PII detector (`anthill.hybrid.scrub.scrub`) over the line and
   lists what it found by kind (for example "an email address"). The person decides. The core page is private to its owner and is given to the
   model only in the owner's own sessions, but a model on a connected backend receives it, so the preview says so in plain words when anything
   sensitive is found.
5. **No silent write, no suggestion.** Nothing is saved without a confirm. This slice does not suggest saving after a correction or a
   repeated preference: that needs a trigger that does not exist yet.
6. **Memory is unchanged.** Automatic memory distillation, recall and the saved-answer cache stay on; they are step 2.4.
7. **Words.** Strings say "core page" and "remember", never "caused".

## Acceptance

- Preview then confirm adds one `- ` line under Preferences, the core page has a new version, the record of the next answer lists that
  version, and the prompt of the next owner turn holds the line.
- Preview alone writes nothing (file bytes and version unchanged). Confirming a line that is already there changes nothing.
- A line over 300 characters is cut to one line; a line that takes the page over the cap is refused and the page is unchanged.
- If the page is edited between preview and confirm, the confirm saves nothing and says so.
- A line with an email address or a card number is saved only on confirm and the preview lists the kind found.
- In chat agent mode `remember` creates a pending review for the core page and writes nothing; approving it adds the line. In a task, a
  persistent agent and an organisation turn the tool is not in the list the model is given.
- Someone else's core page cannot be previewed or written: the routes act on the signed-in owner's page only.
- The full suite passes; tests check behaviour (a line on the page, a prompt that holds it), not structure.

## Not in this slice

- The button and the screen (the UI/UX session, after these routes exist), a chosen text selection, and "save progress".
- Suggesting a save after a clear correction or a repeated preference.
- The memory archive and selective import (slice c).

## Known limits

- The PII detector is the regex baseline (and Presidio when installed). It will miss things; the preview says what it found, not that
  nothing is there.
- The cap is characters over four, not the model's tokenizer.
- A proposal for the core page is applied by the existing approve route as a whole-page write; a stale proposal is refused and the owner
  re-proposes.
