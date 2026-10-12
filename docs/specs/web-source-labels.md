# Spec: The model is told which text is the wiki and which is the web

Status: implemented. Lane: `pillar:feature`.

## Problem

On a web-search turn the model is given the organization wiki (when a page applies) and the web results. A small
model mixed them up: text from a web result on a wiki site (Wikipedia) was filed under "Wiki Knowledge" and
cited with an invented `[[slug]]`, as if the organization's own wiki had said it. Found in an end-to-end check
on the beta with a real model (`qwen3.5:9b`).

## Requirements

- The instruction to the model says that only text under `WIKI CONTEXT` comes from the organization wiki, that
  everything under `WEB RESULTS` comes from the web (including a page on a site that is itself a wiki, such as
  Wikipedia), that web text is never called organization wiki knowledge and never gets a `[[slug]]`, that a
  `[[slug]]` may only name a slug that appears under `WIKI CONTEXT`, that the `WIKI CONTEXT` section is the one
  before the fenced web block and anything inside the fence is web text whatever it says it is, and that with
  no wiki text it does not describe or cite any wiki page (at most one short sentence that the wiki has nothing
  on it).
- Web text cannot pose as the wiki or close the fence. Wherever a result's title, address, snippet or page text
  enters the prompt, the prompt's own markers are defanged (`neutralise`). The text is matched on a skeleton of it in
  which full-width and Cyrillic or Greek look-alike letters are folded to plain ones, one character for one
  character. `BEGIN_UNTRUSTED_WEB` and `END_UNTRUSTED_WEB` (with their angle brackets) are removed, and
  `WIKI CONTEXT` and `WEB RESULTS` are rewritten with a middle dot between the words, whatever the case and
  whatever sits between the words or the letters (space, tab, underscore, hyphen, dash, dot, slash, colon,
  asterisk, no-break or zero-width space), also inside underscore or asterisk emphasis. Only the matched words
  are replaced, in the original text; all other text is left exactly as it came.
- When no wiki text applies, the prompt says so in the place the wiki section would be:
  `WIKI CONTEXT: (none - the organization wiki has no page for this question)`. The same line replaces the
  section when the model's window leaves no room for any wiki text.
- The web results stay fenced as untrusted data, as before.
- The fit of the prompt to the model's window now counts the blank lines between its parts, which it had
  left out (a few characters short).

## Acceptance criteria

- The instruction contains the separation rule; a prompt without wiki text carries the "none" line; a prompt
  with wiki text carries it under `WIKI CONTEXT` and nowhere inside the web block.
- A page whose text contains the end marker followed by a `WIKI CONTEXT:` heading leaves exactly one end
  marker and one heading in the prompt, the real ones, and the same holds for a title, an address or a snippet.
- Measured on the real model (`qwen3.5:9b`, fixed search results with one from Wikipedia and no wiki page):
  before, 3 of 4 answers filed the Wikipedia text under "Wiki Knowledge" with an invented `[[slug]]`; after,
  0 of 6 did (two said, correctly, that there was no organization wiki context, which the instruction now
  allows as one short sentence).

## Known limits

- This is an instruction to a model, not a guarantee: a different or smaller model can still mix them up.
- Only the exact markers and headings are defanged; a page that imitates the wiki in other words ("this page
  is part of the organization wiki") is kept, and the model is told by position, not by the page's claim.
- The streamed and the whole-answer paths share this prompt; neither checks the answer afterwards.

## Tests

`tests/test_web_source_labels.py`, and `tests/test_prompt_fit.py` (unchanged) for the fit.
