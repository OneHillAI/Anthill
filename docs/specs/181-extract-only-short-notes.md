# Spec: short notes are filed as they are, not summarised

Status: implemented. Lane: `pillar:knowledge`. Issue: #181. Related: #150 (fidelity guards), #149 (de-duplication), #148 (tiny upload timeout).

## Problem

#150 showed that a small local model asked to summarise a four-line interview note rewrites a quote as a claim,
moves who wanted what, and adds facts. The guards from #150 (a stricter prompt, the source H1 as the title,
quotes restored word for word) limit the damage but cannot stop the model writing a paraphrase next to the real
quote. For a note this short there is little to condense, so summarising adds risk for almost no value, and it
costs a model call (and its timeout, #148) on a machine that may have no model ready. The maintainer decided
that short notes skip summarising.

## Requirements

1. A source is filed as it is when it is a `.md` or `.txt` file, has no images, has some text, and its text is at
   most `_VERBATIM_NOTE_MAX_CHARS` characters (1,500, about a screen of text). Any other source, and any longer
   one, takes the summarise path unchanged: PDFs, Office files, HTML, images, and an empty or blank note.
2. Such a note makes no model call. `ingest` never touches the backend for it, and `web_enrich` does not apply
   (there is no summary to enrich).
3. The page is the note. If the note has its own H1 that is the title; otherwise the page starts with
   `# <file stem>`. The body is the note's text with surrounding blank lines trimmed and nothing else changed.
   Quote restoration (`_apply_source_fidelity`) is skipped because nothing is reworded.
4. `normalize_wiki_page` and `_neutralize_dangling_links` are skipped. The first would reorder a `## Related`
   heading and merge its lines, dropping anything after it; the second would strip the brackets from the user's
   own `[[links]]`. Both exist to repair model output, and rewriting the user's text is the fault this feature
   removes.
5. The page still carries the provenance tag, with the writer `verbatim` and the content-addressed source name
   (`<stem>-<hash12>.md`) and no pipeline model. Duplicate detection (#149) reads that source, so the same short
   note uploaded again is held exactly as before (a note with no H1 no longer gets a different model-chosen title
   on each run).
6. A `verbatim` page is never stale: `stale_pages` skips it and `renovate` reports it unchanged, including with
   `only_stale=False`. A model upgrade has nothing to regenerate.
7. The page view reads "The note as uploaded on <date> from <source>." for a verbatim page, and "AI summary written
   by <model> on <date> ..." for every other page (it read "Written by <model>" until
   `docs/specs/honest-upload-messages.md`).

## Known limits

- A note with its own `[[link]]` to a page that does not exist is not delinked. The review gate sees it as it
  would a hand-typed page and flags `broken_links`, so the upload is queued for review instead of filed at once.
  This is the cost of not editing the user's text; links to pages that exist, and to the note's own title, pass.
- Each short note is a page with no synthesis and no `## Related` links beyond what the author wrote, so the wiki
  is less connected until a later pass. A short connector document (`/wiki/import-connector`) is a `.md` file
  and takes the same path; with no typed title and no heading the import route titles it by the
  file name without its extension, not a model-chosen title. A longer untitled document is still summarised
  and titled by the model. The route adds its heading before the length check in ingest, so a document
  within a dozen characters of the limit can be summarised instead of filed as it is; it keeps the file-name
  title.
- A short note makes no model call, so the 60 second upload limit and its retry button (#148) never apply to it; they
  still apply to every note long enough to be summarised.
- Existing pages written by a model from a short note are not converted, and a manual renovation of one still
  regenerates it through the model.
- The 1,500-character threshold is a constant, not a setting; there is no per-upload choice yet. The optional
  one-line generated summary the issue floated is not added, so no model-written text appears on these pages.
- The web upload handlers still call `_ensure_backend_ready` before ingest, which may wait for a stopped local
  engine to start; it no longer matters to the result for a short note.
- Not checked in a browser or with a real model.

## Acceptance

- Ingesting the courier note from #150 with a backend whose `chat` raises gives the title
  `Interview: courier (P11), 19 Sep 2026`, a body equal to the note (quote, request and wording unchanged) and no
  `## Quotes` section.
- A note without an H1 is titled with its file name. A note of exactly 1,500 characters is filed as it is; one
  character more goes to the summariser. A blank note, a PDF and an image keep their current path.
- A note with its own `## Related` section, later headings and `[[links]]` keeps every line. A note with a
  dangling `[[link]]` is flagged `broken_links` by the review gate; one without links is not flagged.
- The page's tag reads `model=verbatim` with a `source=` name; uploading the same short note again is held as
  `same_file`.
- The page view shows "The note as uploaded on <date> from <source>."
- `stale_pages` does not list a verbatim page, and `renovate` (stale only or all pages) leaves it untouched.
- Tests: `tests/test_wiki_short_notes.py`; the summarise-path tests that used short fixtures
  (`test_wiki_ingest_fidelity.py` and a few others) opt out with the `summarise_every_note` fixture in
  `tests/conftest.py`. All model-free.
