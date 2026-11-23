# Spec: nothing moves to a wider space on its own

Status: implemented. Lane: `pillar:knowledge`. A memory, a training example and a snippet are private to the person who made
them. They stay where they were made unless that person, or an admin approving a snippet into the wiki, moves them.

## Problem

A memory, a training example and a snippet were moved to the team or the whole organisation by Anthill itself when other people held,
reused or saved something similar. What a person made privately became shared without their action.

## Requirements

1. **A memory stays where its owner made it**, however many other people hold a similar one. `maybe_corroborate` and its three
   callers (chat, tasks, agents) are removed; the notice "a memory is now shared" goes with them. An admin can still promote a memory
   by hand.
2. **A training example keeps its scope when another person is given its answer.** `promote_to_silver` still raises the example's
   quality and no longer changes its scope. The organisation's training export (`scope=org`) holds only examples that were moved
   on purpose.
3. **A snippet and its training example stay personal when other people save the same text.** `save_snippet` no longer promotes to the
   team or the organisation; `promote_snippet`, the admin's approval into the wiki, still does, for the snippet and every matching
   one. The `org_threshold` argument is removed.
4. **The text the person reads matches.** The Memory and Snippets pages, the user guide and the model guide no longer say these move
   on their own.
5. **A user who does nothing** is unaffected: memories, snippets and examples are made, recalled and listed as before.

## Known limits

- Nothing is moved back: a memory, snippet or example that was already shared by the earlier behaviour stays shared until an admin
  changes it.
- Sharing a memory or example with other people on purpose, with a preview, is separate work.
- Memory recall is unchanged in this step and is replaced in a later step.

## Acceptance

`tests/test_nothing_widens_on_its_own.py`: three people finish the same task and each memory stays personal and owned by them, none
becomes team or organisation wide; an example keeps its scope when another person reuses its answer and the organisation export is
empty; three people saving the same snippet leave all three snippets and their examples personal; an admin's approval still moves
a snippet and its examples. Older tests that expected the automatic move were rewritten to expect the opposite.

## Not verified here

Not run on a real install with real memories. The reworded pages have not been seen on screen.
