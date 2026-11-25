# Spec: keep what Anthill remembered in a file, and offer it for import (slice c of step 2.3)

Status: proposed (build plan step 2.3, slice c). Lane: `pillar:knowledge`. Builds on `docs/specs/core-page.md` and
`docs/specs/remember-this.md` (a line is added to the core page with a preview).

## Problem

Anthill has been distilling memory rows from a person's chats and recalling them into answers (`MemoryItem`). The person can list and
delete them on the memory page, but a row has no page, no history and no id in an answer record. When memory is switched off (step 2.4)
those rows must not be lost: they are a person's own words about themselves, and some of them are worth keeping as preferences. The build
plan says existing memory is exported to a file archive, never deleted, and offered for selective import onto the core page.

## Requirements

1. **An archive file, once, automatically.** The first time a person's own turn is given their core page after this change (the owner's
   own personal-context sessions only), their personal memory rows are written to `archive/memory-<date>.json` and a readable
   `archive/memory-<date>.md` in their personal wiki folder, and `archive/.memory-archived.json` records that it was done. The marker is
   checked before any database read, so later turns cost one file check. If the write fails the reason is logged, nothing else changes,
   and it is tried again at the next turn.
2. **Never deleted, never changed.** The archive step reads memory rows and writes files. It does not delete, change or hide a row, and
   memory recall, distillation and the memory page work as before (switching memory off is step 2.4). The archive holds, for each row: its
   id, text, kind, source and time. It holds no embedding.
3. **Only the person's own.** Rows with `scope = personal` and `user_id` equal to the person are archived. Team and organisation rows are
   not theirs to keep or import and are left alone (see Known limits).
4. **Offered for import, line by line.** `GET /core-page/memory-import` lists the archived rows that are not yet on the core page, with each
   row's text, kind and time and what the preview of adding it would say (sensitive items named, as in `remember-this.md`).
   `POST /core-page/memory-import` takes the chosen row ids and adds each as one line under Preferences, through the same one-line rule,
   duplicate check and cap as "Remember this", as the person's own edit, in one save. If the cap is reached, the lines that fit are added,
   and the response names the rows that did not fit. Nothing is imported unless chosen, and a row the person never imports stays in the
   archive.
5. **Words.** Strings say "archive", "import" and "core page"; nothing says "caused".

## Acceptance

- A person with memory rows gets the two archive files on their first personal-context turn, with exactly their personal rows and no
  embedding; a second turn writes nothing; a person with no rows gets an empty archive and the marker.
- No `MemoryItem` row changes or disappears, and recall still returns what it returned.
- Another person's rows, and team and organisation rows, are not in the archive or the import list.
- Importing chosen rows adds exactly those lines under Preferences in one new version; a row already on the page is skipped; rows over
  the cap are named and not added; an unchosen row is untouched.
- A turn not allowed personal context (an organisation turn, Slack, the widget) writes no archive.
- A write failure does not stop the answer, and the archive is tried again next turn.
- The full suite passes; tests check behaviour (files written, lines on the page), not structure.

## Not in this slice

- Switching memory off, recall changes, or removing the memory page (step 2.4, after the independent evaluation).
- The screen for import (the UI/UX session, after these routes exist).
- Importing as a model-written summary: lines are the person's own stored words, one row to one line.

## Known limits

- Team and organisation memory rows (made when a memory could be promoted to a team or the organisation) belong to those spaces, not to one person. They are not
  archived here. Deciding what happens to them is part of step 2.4.
- The archive is in the person's personal wiki folder, so it is as private as the core page and goes with that folder's own backups.
- A memory row longer than 300 characters is cut to one line on import; the archive keeps the full text.
