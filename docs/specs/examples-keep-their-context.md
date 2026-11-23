# Spec: a training example keeps the context its answer was written with

Status: implemented. Lane: `pillar:knowledge`. Builds on `docs/specs/answer-manifest.md` (the record of what an answer was given).

## Problem

A chat answer was kept as a possible training example only while automatic memory was on, and without the items the answer was
written from. The example could not say what knowledge it was made from, and a setting about remembering facts also decided whether
conversations were kept as examples.

## Requirements

1. **An example is recorded for every successful model answer** in the chat, whatever the automatic-memory switch says. It is still not
   recorded for a failed turn, for text made by code, or for an ephemeral personal-mode run. It is kept on this device, personal to
   the person who asked, as before; nothing trains on it until the person decides.
2. **It keeps its context.** The example's `context` holds, as JSON, the id, version, hash, kind, space and name or slug of each item the
   answer was given (from the answer record), and the narrowest space of those items: personal if any item is personal, else team, else
   organisation, and personal when nothing was given. Never the text of an item. An answer that was given nothing keeps an empty context.
3. **Its scope does not widen.** The example stays personal; the narrowest space is recorded in its context for whatever later decides
   how it may be used.
4. **The switch says what it is.** The Memory page says the switch is only about remembering facts, and that answered chats are kept on
   this device, privately, as possible training examples whether it is on or paused.

## Known limits

- Examples from chats before this change have no context.
- Only the main chat records examples; tasks and agents are unchanged.
- Pausing automatic memory no longer stops examples being kept on this device. Deleting one is a separate piece of work.

## Acceptance

`tests/test_examples_keep_their_context.py`: an example holds the ids, versions and spaces of the page and the document its answer was given
and none of their text; it is recorded with automatic memory on, paused and resumed, and stays personal; a failed turn records nothing; a
question that used nothing keeps an example with no context; the narrowest space of mixed items; the Memory page says what the switch covers.

## Not verified here

Not run on a real install. The Memory page text has not been seen on screen.
