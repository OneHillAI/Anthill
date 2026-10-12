# Spec: A web turn that could not use the web says so

Status: implemented. Lane: `pillar:feature`.

## Problem

When a member turns web search on and the web gives nothing (offline, rate limited, a blocked search engine, no
search library, a search that finds nothing usable, or a model that cannot write from the results), the turn
answers from the wiki and the model alone. Nothing on the page said so, so the answer looked like a researched
one. Found in an end-to-end check on the beta (v1.1.0-rc.1) with a real model.

## Requirements

- Such a turn starts its answer with a fixed note in one of three wordings, by cause, and says whether a wiki
  page was used:
  - the search failed: "The web search could not be completed, so this answer comes from your wiki and the model
    alone, not from the web.";
  - the search worked but left no usable page (none found, or every one left out as carrying an instruction):
    "The web search found no usable pages, so this answer ...";
  - the search worked but the model could not write from the results before its first word (the retry without
    thinking also failed): "The web results could not be used to write an answer, so this answer ...".
  When no wiki page applied, "your wiki and the model" reads "the model".
- On the streamed path the page is told it is writing (`Stage("writing")`) and the note is the first words of the
  answer, sent with the first words of the fallback and not before it (a fallback that fails leaves a failed
  turn, not a note followed by a failure; a fallback that says nothing still gets the note). The page is told it
  is writing before the fallback starts, so it does not keep saying "Searching" while the model thinks, so it is shown as the answer streams and is saved with the conversation. The blocking `ask()` (images,
  cloud and organisation backends, including an image turn) puts the same note in front of the returned answer;
  it also does so when a web answer was discarded as hijacked.
- A turn whose search worked, and a turn that did not ask for the web, have no note.
- The note belongs on the page and in the saved conversation only. It is removed (`strip_web_note`) from the
  history fed to the next turn, the local answer handed to the Automated-mode grader, the output kept as a
  training example, the transcript distilled into memory, the transcript a skill is drafted from, and an answer
  filed as a wiki page (also the
  command-line `/save`). It is never cached, published or filed.
- The answer to a turn that asked for the web is never stored in the answer cache, whichever way it was written:
  a later turn that does not use the web would otherwise be given an answer written for a web turn (spec 108,
  requirement 4). (Web turns never read the cache; the blocking path already stored nothing for one.)
- A search that returns no result at all is now a turn with no usable page (before, the model was given an
  empty web block); `search_and_answer` raises `NoWebPages` for it.

## Acceptance criteria

- Search raising an error (including a missing search library): stages "searching" then "writing", then the note
  as the first words, then the answer; no web block in the prompt; nothing cached.
- No result, or every result left out: the "no usable pages" note and no web block in the prompt.
- The model failing on the results: the "could not be used" note, with the stage announced once.
- The blocking path: the note for a failing search (also with an image), for no pages, and for a discarded
  hijacked answer; with a working search the web answer unchanged.
- Through the chat route: the stages arrive before the note, the stored assistant message holds the note and the
  answer, and neither the next turn's history nor the training output holds the note.
- The note wording names only the model when no wiki page applied.

## Known limits

- The note is plain text in the saved message, not a separate field, so a model or a page could print the same
  words; stripping the exact leading note from what is fed back limits what that can do, and the page renders
  it as part of the answer.
- The wording for "search failed" does not say which step failed (the search engine, a page read).

## Tests

`tests/test_web_turn_stream.py`, and `tests/test_prompt_fit.py` (its web fit tests now return a result).
