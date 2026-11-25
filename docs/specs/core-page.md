# Spec: the core page, in place of memory (slice a: the page and its reading)

Status: proposed (build plan step 2.3, which is built in three slices, see below). Lane: `pillar:knowledge`. Builds on
`docs/specs/typed-context-builder.md` (a `core_page` block carries its item and shows in the record) and
`docs/specs/one-page-write.md` (one function writes a page, keeps the earlier version and refuses a stale save).

## Problem

What Anthill "knows about me" lives in three places that a person cannot see or correct as one thing: the persona text of "make it yours"
(`User.profile`), the memory rows distilled from chats and recalled by similarity (`MemoryItem`), and the line the agent `remember` tool appends to a log. A memory has no page, no history and no id; the
answer record can say that "memory" was given but not which memory. The architecture (`knowledge-pillar/architecture.md`) replaces all of it
with one small page the person owns: the **core page**.

## The three slices

- **2.3a (this spec): the page and its reading.** A personal core page exists, the profile moves onto it, it is read into the guidance of
  the owner's own sessions, and the answer record lists it as an item.
- **2.3b: "Remember this".** Adding a line to the core page from an answer or from the agent, with a preview and no silent write.
- **2.3c: the memory archive and selective import.** Existing memory is exported to a file, never deleted, and offered for import line by
  line.

Memory recall, automatic distillation and the saved-answer cache are **not** switched off here. That is step 2.4 and waits for the
independent evaluation. After 2.3a the old memory and the core page both feed an answer.

## Requirements (2.3a)

1. **One page.** The core page is the page with the reserved slug `core-page` in the person's personal wiki, created on first use. It is a
   normal page: it has a record (id, version, hash), an edit that keeps the earlier version, and it is private to its owner. It holds two
   headings, `## Profile` and `## Preferences`, and is capped at about 800 tokens: a save over the cap is refused with the number to cut.
2. **Not knowledge.** The core page is never retrieved as a knowledge page: it is left out of the list of pages an answer may read, of the
   search and vector indexes and of the document passages. It is given to the model only as guidance, below.
3. **The profile moves onto it.** The first time a person's core page is needed and `User.profile` is not empty, the profile text becomes the
   `## Profile` section of a new core page. `User.profile` is left in place (nothing is deleted, and the person can go back). From then on the
   "make it yours" form edits the `## Profile` section of the core page and no longer writes `User.profile`. The text the model receives
   for a person whose only content is the migrated profile is byte for byte what it was.
4. **Read only in the owner's own sessions.** The core page is read into the guidance of a turn exactly when the profile is today: when the
   session is allowed personal context for its owner (`plane_inf.use_personal_context`): the owner's plain chat and chat agent mode. A team,
   project or organisation chat, the Slack and Discord route and the widget do not read it. Scheduled tasks and persistent agents do not read
   the profile today and do not read the core page. There is no setting that widens this in this slice (per-session permission is step 4.2).
5. **A typed block with its item.** The guidance part that comes from the core page is a `core_page` block carrying the page's item (id,
   version, hash, space). The answer record lists it as an item ("Your core page") and no longer lists the profile as a hash-only block.
   A core page that cannot name its item is refused and logged, as any knowledge block.
6. **Nothing is asked of the person.** A person who never opens the core page, never used "make it yours" and has no profile gets no core
   page and no change in their answers.
7. **Words.** Strings say "core page", "used in" and "given", never "caused".

## Acceptance

- A person with a profile gets a core page whose `## Profile` section is that text, and the prompt text of their next answer is byte for
  byte the old one. `User.profile` is unchanged.
- Editing the core page changes the next answer from the next turn; two saves at once do not lose text (the one write function).
- A core page is not in `retrievable_pages()`, the search results or the document passages, and a question that matches its words does not
  retrieve it.
- A team chat, a project chat, an organisation-plane turn, Slack, Discord, the widget, a task and a persistent agent do not receive it; the
  owner's personal chat and chat agent mode do.
- The answer record of a personal turn lists the core page with its id and version; the record of a team turn does not.
- A save over the cap is refused, and the page is unchanged.
- A person with no profile and no core page: no page is created by asking a question.
- The full suite passes; the tests check behaviour (a phrase on the core page reaches the owner's prompt and not another session's).

## Not in this slice

- "Remember this" and the agent's `remember` as a proposal with a preview (2.3b).
- The memory archive and import (2.3c); a screen for the core page (the UI/UX session, after this data exists); suggesting that a clear
  correction or a repeated preference be saved (it needs a trigger that does not exist yet).
- Group or organisation preferences, and per-session permission to read a core page elsewhere (Move 4).

## Known limits

- The core page is read by the same rule as the profile today. Tasks and persistent agents recall memory today but not the profile, so they
  do not get the core page in this slice; whether they should is a decision for step 4.2.
- Memory still reaches answers until 2.4, so for a time what the system "remembers" is the core page and the memory page together.
- The cap is counted in characters divided by four, not by the model's tokenizer.
