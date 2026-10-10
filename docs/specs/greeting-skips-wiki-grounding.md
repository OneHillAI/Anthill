# Spec: A greeting or a thank-you is answered without looking anything up in the wiki

Status: implemented. Lane: `pillar:knowledge`. Source: founder, 2026-10-09 (end-to-end check of the chat).

## Problem

1. Every chat turn is grounded in the wiki: the three pages that score highest against the message go into the
   prompt, provided each scores at least `MIN_GROUNDING_SIM` (0.35). A greeting has nothing to look up, but on a
   wiki of long pages it still clears that floor. Measured with bge-m3 on 25 of Anthill's own pages: "hello"
   scored 0.46 against `how-it-works`, "hi there" 0.47, "thanks!" 0.42, "good morning" 0.44, "ok" 0.52 (against
   `okgf`). A real question scored 0.52 against its page, so no floor separates the two.
2. Each of those turns carried three pages in the prompt: 860 to 1,035 tokens of reference text, 1,281 to 1,401
   tokens in the whole prompt. At about 165 tokens a second on a 16 GB Mac that is five to six seconds of
   reading before the first word of "hi", and the pages distract the model (for "ok" the reply began "It looks
   like you might be testing the system"). Through the chat route with `think=false` and `web=false` passed
   explicitly (what a new account gets once `thinking-off-by-default` is merged; on main today a new account has
   web search on and also pays for a planner call), on that wiki, "good morning", "thanks!", "hi there" and "how
   are you?" took 7.3 to 8.1 s to their first word.

## Requirements

1. `is_small_talk(message, history=None)` (`anthill/agent/intent.py`) is true for a bare greeting, thank-you,
   acknowledgement, farewell or check-in: one to three phrases from the fixed lists below and nothing else, at
   most 60 characters. It matches whole phrases, not words, so "how are you" is small talk and "how much is it?"
   is not. The text is NFC-normalised and case-folded first ("ß" matches "ss", capitals do not matter, and the
   decomposed form of "ö" works). Letters, spaces and ordinary punctuation (`' - . , ! ? ; :` and the Spanish `¿`
   and `¡`) are allowed; a digit or any other symbol makes it a normal message, so "hello 123", "how much is
   2+2?" and an emoji are not small talk. The lists are the whole coverage, English first and a few common
   German, French, Spanish and Italian ones:
   - greetings: hello, hi, hey (each also "there"; hello and hi also "again"), hiya, howdy, yo, good morning,
     good afternoon, good evening, good night, good day; hallo, servus, moin, grüß dich (also "grüss dich"),
     grüezi, guten Morgen, guten Tag, guten Abend; salut, bonjour; hola, buenos días, buenas tardes, buenas
     noches; buongiorno, buona sera;
   - thanks: thanks (also "a lot", "so much", "again"), thank you (also "very much", "so much", "again"), many
     thanks, thx, ty, cheers; danke (also "schön", "sehr"), vielen Dank; merci, merci beaucoup; gracias,
     muchas gracias; grazie, grazie mille;
   - acknowledgements: ok, okay, k, cool, great, nice, perfect, awesome, alright, got it, understood, sounds good,
     will do, yes, no, yep, nope, yeah, sure, fine;
   - farewells: bye, goodbye, see you (also "later", "soon"), see ya; tschüss, adieu, adiós, arrivederci, ciao;
   - check-ins: how are you (also "doing", "today"), how is it going, how's it going, what's up; wie geht's,
     wie geht es dir; comment ça va, ça va; cómo estás.
   Accents are optional where the plain form is common ("adios", "ca va", "como estas"). Every other greeting, in
   any language, is retrieved as before.
2. A message that holds an acknowledgement phrase (yes, no, ok, sure, fine ... on its own or with thanks or a
   greeting: "yes thanks", "sure, thanks!", "hi yes") is usually a reply to the assistant's last turn. It is
   small talk only when it has no question mark anywhere ("ok?", "fine?", "Nice?" and "yes?!" are questions) and
   the last assistant turn in the conversation, if there is one, did not end in a question. That turn is judged by
   its final sentence, with the "go deeper" suggestion that `ask()` appends and trailing markup or `!` `.` after the
   `?` ignored. "yes" or "yes thanks" to "Shall I look into the refund policy?" is not small talk: `decide_web`
   plans as before (so it still searches when the assistant offered to) and the turn is built as any other
   message. Retrieval uses only the message itself, so "yes" brings the pages that score highest against that one
   word; the history, which still goes to the model, is what gives it meaning. Such a reply neither reads nor
   writes the semantic cache or the organisation index (`is_acknowledgement_reply`), because "yes" means
   something different in every conversation. Thanks, greetings and check-ins on their own do not depend on the
   history.
3. `ask()` and `ask_stream()` (`anthill/wiki/ask.py`) treat a small-talk message as having nothing to look up:
   no page is retrieved, in any workspace, so the prompt has no reference block and the grounding slugs reported
   to the page are empty; the question is not embedded; the semantic cache is neither read nor written; and the
   organisation index is not asked. The chat route skips its own cache lookup for small talk. `decide_web`
   returns "do not search" for small talk without calling the planner, so a capable model does not pay a planner
   call and a small one does not search the web for "hello".
4. `ask()` serves the chat, the Slack and Discord answers, the embeddable widget, the MCP organisation tool, the
   node agent's `/ask` and the command line, so all of them answer a bare greeting this way.
5. Every other message is retrieved exactly as before. Memory recall, the profile, the history and web search for
   a message that is not small talk are untouched.

## Known limits

- Memory recall (one embedding call) and the history summary, when the history is long, still run for small talk.
- A greeting answered and cached on main before this change is still replayed by the semantic cache for a near
  variant that is not small talk by these rules. Small talk itself no longer reads the cache. The MCP tool's own
  cache path was not checked.
- Greetings that are not in the lists are retrieved as before.
- Only the listed forms count: "how are you doing today?" (both "doing" and "today") is not on the list and is
  retrieved as before.
- A check-in or a thank-you on its own is small talk whatever the assistant just asked ("how are you?" after
  "Shall I search?" skips the lookup), and so is an acknowledgement whose last assistant turn ended on a
  statement after a question earlier in the same turn; only the final sentence is read.

## Acceptance criteria

- Every listed phrase is small talk, in capitals and with a bang as well, and the Spanish `¿ ¡`, "ß" and
  decomposed Unicode forms work. Messages that add any other word, contain a digit or a symbol, are empty, are
  longer than 60 characters or have more than three phrases are not (`tests/test_small_talk_grounding.py`,
  including "how much is it?", "how many are there?", "is it up?", "Morgen?", "hello 123", "ok 404", "Is it
  9.5?", "how much is 2+2?" and the acknowledgements that end in a question mark).
- An acknowledgement after an assistant question is not small talk, alone or with thanks or a greeting ("yes",
  "yes thanks", "yes, thank you", "sure, thanks!", "ok thanks", "hi yes", "yes?!"), and `decide_web` still plans
  for it; after a statement, or with no history, it is small talk. The "go deeper" suggestion at the end of an
  assistant turn does not hide or invent a question. Such a reply neither reads nor writes a cache or the
  organisation index.
- A streamed and a blocking greeting send no `REFERENCE MATERIAL` block in any workspace, and `on_context`
  receives an empty list; a question still sends the block. With web search on, the blocking web answer gets no
  wiki context for a greeting.
- `decide_web` makes no planner call for small talk and still plans for a question.
- Small talk neither embeds the question, nor reads or writes the semantic cache, nor asks the organisation
  index, in `ask()`, `ask_stream()` and the chat route; a real question still does.
- The PR holds the first-word time of greetings and of a real question, before and after, measured through the
  chat route, on a wiki of long pages, with the real model, and says which settings were used.

## Out of scope

- Tuning `MIN_GROUNDING_SIM`. No single floor separates a greeting from a question on a wiki whose pages are all
  about the same system.
- Messages about the conversation itself ("why didn't you tell me that"). `is_conversational` keeps its job of
  stopping a web search; those turns are still grounded, because a real question can contain the same words.
