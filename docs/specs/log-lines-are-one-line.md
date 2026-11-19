# Spec: a log entry is always one line

Status: implemented. Lane: `pillar:knowledge`. Part of the knowledge base update (epic #270): what the wiki records about itself
must be what happened.

## Problem

Each wiki keeps a log (`log.md`) with one line per event: `## [date] kind | title`. The title is text that sometimes comes from
outside, for example a note an agent saves with `remember`. A line break in that text started a new line in the log, so a note
could look like several events.

## Requirements

1. `Workspace.append_log(kind, title)` writes exactly one line. Any run of whitespace in `kind` or `title` (line breaks, tabs, the
   Unicode line separator and other whitespace) becomes a single space; leading and trailing whitespace is dropped.
2. An ordinary entry is written exactly as before.
3. Every caller gets this, because it is done where the line is written.

## Known limits

- The log is for people to read; no answer reads it. An OKGF export includes it.
- Existing logs are not rewritten.
- Whether an agent's `remember` note should go to a review queue is not changed here.

## Acceptance

`tests/test_log_lines_are_one_line.py`: a note with line breaks and a forged entry makes one entry and no stray line; tabs, vertical
tab, form feed, the Unicode line separator and surrounding spaces become single spaces; an ordinary entry is unchanged.
