# Spec: wiki ingest keeps a note's title, quotes and attribution

Status: implemented. Lane: `pillar:knowledge`. Issue: #150. Related: #181 (extract-only mode for short notes), #149 (de-duplication), #152.

## Problem

The wiki presents ingested pages as the user's own knowledge, and chat then answers from them. Ingest asked the
model for "an H1 naming the topic", "a one-sentence summary" and "the concrete facts and claims", and told it
nothing about quotes, attribution or adding facts. Nothing checked the result. With `qwen2.5:3b`, a UX
researcher's three short interview notes came back changed (#150):

- A participant's verbatim quote became a plain statement: `Quote: "The app thinks every street is a straight
  line."` became `The app incorrectly assumes every street is a straight line.`
- Who wanted what moved: a courier's request became "the client wants a single 'can't deliver' button".
- The page title was invented ("Orders App Redesign") instead of the note's own H1, so re-ingesting the same file
  could give a different title.
- Facts were added: "has been redesigned", "customer feedback and support tickets".

For research notes a quote is evidence, so this silently corrupts the user's own record.

## Requirements

1. The ingest prompt tells the model to copy quoted passages word for word, to keep who said, wanted or did what,
   to add nothing the source does not say, and to reuse the source's own title. The worked example no longer
   states a confident decision (it invited the model to write one).
2. For plain-text sources (`.md`, `.txt`) the page title is the source's first H1 when it has one, whatever the
   model wrote. With no source H1 the model's title is kept.
3. For plain-text sources, every quoted passage in the source (straight or curly double quotes, at least two words
   and 12 characters, on one line) appears in the page word for word. Any the model did not keep are added
   unchanged under a `## Quotes` section placed before `## Related`. A quote the model kept is not repeated.
4. The connector import (`/wiki/import-connector`) adds a heading to an untitled document only when the user typed a
   title, so that title becomes the page title. With no typed title it passes the text through untouched and the
   model titles it as before. (It used to add a heading made from the file name, which requirement 2 would now
   promote to the page title.)
5. Nothing else about the page changes: structure, link handling, provenance tag and the review gate are as before.

## Known limits

- A reworded line the model wrote next to the restored quote stays on the page. The guard restores the evidence;
  it cannot tell which of the model's sentences are wrong. Attribution and invented facts depend on the prompt and
  the model, and are not checked. #181 asks whether short notes should skip summarising altogether.
- The page path comes from the title, so two different notes with the same H1 write to the same page. That was
  possible with model-chosen titles too, but a source H1 makes it deterministic. De-duplication and collision
  handling belong to #149.
- Only `.md` and `.txt` are covered. PDFs, Office files and images go through the same prompt but have no title or
  quote guard, because their extracted text is not the author's own layout.
- Quotes are found by their marks only. Block quotes without marks, and single-quoted speech, are not detected.

## Acceptance

- Ingesting the courier note from #150 with a backend that returns the reworded page gives the title
  `Interview: courier (P11), 19 Sep 2026` and a `## Quotes` section holding the exact quote, ahead of `## Related`.
- A page that already holds the quote verbatim gets no `## Quotes` section and no duplicate.
- A source with no H1 keeps the model's title.
- The prompt contains the fidelity rules and no longer contains the PostgreSQL example.
- A connector import with a typed title gets that title; one without keeps the model's title.
- Tests: `tests/test_wiki_ingest_fidelity.py` and `tests/test_wiki_import_connector.py` (model-free).
