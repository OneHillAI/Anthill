# Spec: the answer panel lists everything the answer was given

Status: implemented. Lane: `pillar:knowledge`. Issue: #351. Builds on `answer-manifest.md` (#281) and
`typed-context-builder.md` (build plan step 2.1). Part of the knowledge update, epic #270 (the screen side of
step 2.2). It only reads the stored record.

## Problem

The panel under an answer ("What this answer was given") listed the pages and documents the model was given. Since
the typed blocks of step 2.1 the record kept with an answer also holds the rules and the profile that applied, any
remembered notes, the context an agent run started with, each earlier message of the chat that went into the
prompt, and the web pages a web answer was given. None of it was shown, so "what did my AI see?" had half an answer.

## Fix

1. Below the list of pages and documents the panel has an "Also given" list, one line for each kind of thing the
   record holds: "Your rules", "Your profile", "Notes Anthill remembered", "The context this run started with",
   "N earlier messages in this chat" (counted), and "From the web (not checked)" for each web page.
2. A web page is a link to its address only for an http or https address; any other address is shown as text.
3. A line at the bottom says "Answered by <model>, in chat" or "in agent mode" when the record has the model and a
   route the screen knows; with a model and an unknown route it says only "Answered by <model>".
4. No hash, length or id is shown. The wording is "given", never "caused" or "relied on".
5. A record with only pages and documents, a record from before the blocks existed and a damaged record look as they
   did. A kind of block the screen does not know is left out. The live panel and the reloaded panel are made by the
   same code.

## Acceptance

- A record with rules, profile, memory, context, three earlier turns and a web page shows each line in the words above.
- A record with only items, or without blocks, shows no "Also given" and no "Answered by" line.
- An unknown block kind is not shown; a `javascript:` or `file:` address is text, never a link.
- Text from the record is escaped.
- The live panel equals the reloaded panel for the same record.
- Looked at on a running copy at desktop widths, light and dark.

## Not here

Storing the record for tasks and agents and the view from an item to the answers that used it (step 2.2); the text
of a rule, the profile or a turn (the record holds none).
