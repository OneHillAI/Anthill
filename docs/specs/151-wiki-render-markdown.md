# Spec: wiki pages open as a rendered document

Status: implemented. Lane: `pillar:knowledge`. Issue: #151. Related: #150 (ingest fidelity).

## Problem

Opening a page from the wiki showed its source in a monospace box: `#` headings and `-` bullets as typed, and the
machine provenance comment (`<!-- anthill: model=... ingested=... source=... -->`) at the bottom. To a first-time
user it looked like a code file, and the comment looked like something had broken. On the wiki index the Pages
card caption ("the wiki documents in this scope") ran straight into the "Take a tour" link.

## Requirements

1. The page route removes the provenance comment from the text it renders, along with the frontmatter it already
   removed. The comment is still on disk and `read_provenance` still reads it.
2. The body is rendered as Markdown by the renderer task results already use (`marked` plus a strict DOMPurify
   policy), shared as `templates/_markdown_render.html`. Page text is untrusted: the policy allows only the tags
   Markdown produces for text, turns images into plain links, and keeps only http(s), mailto and same-site links.
3. Without JavaScript, or if either library fails to load, the body is the page text, HTML-escaped and pre-wrapped.
4. The provenance appears as one muted line under the page ("Written by MODEL on DATE from SOURCE."), outside the
   body. A page with no provenance comment shows no line.
5. A `[[link]]` whose slug matches an existing page in the same scope becomes a link to that page. A link to a
   page that does not exist is shown as its plain text. The link target is the slug, never the typed text.
6. The Pages card caption on the wiki index reads "The wiki documents in this scope." before the tour link.

## Known limits

- The shared renderer opens every link in a new tab, including links to other wiki pages.
- Rendering happens in the browser, so the model-free tests check what the server sends (escaped source, the
  renderer scripts, no comment, the metadata line, the links), not the rendered result. The renderer itself is
  covered by the browser tests for task results.
- Only the page route changes. The Markdown the model writes, the review diff view and the on-disk format are as
  before. Links with a label (`[[slug|label]]`) are not a form the wiki writes, so they are not parsed.

## Acceptance

- A page ingested with a provenance tag opens with no `<!-- anthill` anywhere in the response and a line naming
  the model, date and source file.
- The page body sits in a `data-md` element holding the escaped source; a `<b>` typed in the note is text there.
- `[[other-note]]` becomes a same-site link when `other-note` exists in the scope and plain text when it does not.
- The wiki index caption reads as a sentence.
- Tests: `tests/test_wiki_page_render.py` (model-free).
