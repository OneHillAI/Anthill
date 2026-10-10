# Spec: Thinking and web-search controls in chat

Status: implemented; revised 2026-10-09 (Thinking and web search are both off by default for a new account). Lane: `pillar:feature`. Source: founder, 2026-10-07, 2026-10-08 and 2026-10-09.

## Problem

1. A local model that thinks before it answers (for example Qwen3.5 9B) thinks silently before its first visible
   word. Measured on an M4 Mac with 16 GB, directly against Ollama: with the model default, 350 tokens of
   thinking had still produced no visible answer after 28 s; with thinking off the first word arrived after
   0.6 s. Anthill leaves thinking at the model default, so a user waits with nothing to read.
2. Web search has a Settings default (`User.web_access_on`) that seeds the chat's Web search checkbox, but one
   browser-wide saved value (`anthill_web_on`) then overrides it for every chat, so a chat cannot differ from
   another and a later change of the Settings default has no effect.
3. The founder decided that most questions are about live information, so web search should be on by default.
   That reverses the older Solo default of off (`docs/specs/solo-web-search-default-off.md`, now superseded), and
   the pages must then say honestly that a search sends the question to a search provider. The founder reversed
   this decision on 2026-10-09: see Problem 5.
4. A new user is not told that thinking and web search exist, what they are set to, or where to change them.
5. With both defaults on, a new user can wait minutes (revision of 2026-10-09). Measured on an M4 Mac with 16 GB,
   Ollama 0.24.0 and qwen3.5:9b, one run each, before issue #108 made web answers stream (see
   `docs/specs/108-web-turn-stream.md`, added in the same change as this revision). A plain question shows its
   first word after about 2.6 s with Thinking off and after 29 to 31 s with it on (through the app). A question
   that searched the web was then answered in one piece, so the user saw nothing until the whole answer was
   written. Through the app, Thinking off: 59.9 s for a short question and 136 s for a long one (a four-day trip
   plan), with the first word at the end; earlier app runs of the trip plan took 117 s with Thinking off and 131 s
   with it on. In a script run of the same code with no wiki: 42.8 s for a short question with Thinking off (3.1 s
   to plan the search, 4.7 s to search and read five pages, 35 s to write the answer) and 311 s with Thinking left
   to the model. How long the thinking runs varies from run to run. Searching costs seconds; thinking costs
   seconds to minutes; writing a long answer costs about a minute. The founder's decision on 2026-10-09: nobody
   should wait minutes by default, so web search and Thinking both start off. The first-use notice (Problem 4) is
   unchanged: it appears once per account, says that both are off, and lets the user switch either on right
   there. A user who turns web search on gets the streamed web answer of issue #108.

## Requirements

1. The chat box shows a Thinking button next to Options. The label reads "Thinking on" or "Thinking off", and the
   button is highlighted while it is off.
2. Defaults live in Settings. Thinking is under Model, in a card "How it answers". Web access stays under Privacy.
   New accounts start with web search OFF and Thinking OFF. Existing accounts keep the values they have, which is
   how they have behaved until now: the web column is added to an old database as off and the Thinking column as
   on, so the change reaches new accounts only. A user turns either on in Settings or, for one chat, with the
   Thinking button and the Web search checkbox. Organisation chats were always on for web search.
3. Each chat can change both for itself: the Thinking button and the Web search checkbox in Options save the
   choice for that chat only, in the browser, keyed by the chat. A chat with no choice of its own follows the
   Settings default, so changing the default applies to it at once. For web search the chats that follow the
   default are the ones that run on the local model: a Solo chat, and a project chat (plane "team") in an
   install with no organisation server. A project chat on the shared model and an organisation chat always start
   on, and a changed web default never rewrites them. The server decides this once per chat. The Thinking default
   applies to every chat, organisation chats included.
4. The old browser-wide web value is imported once, in the privacy-safe direction only: when a browser still holds
   `anthill_web_on` with the value off, "off" is saved as the account default, so a user who had web search off in
   that browser keeps it off. A stored "on" is only removed, never promoted: it would turn web search on for the
   account on every device. The key is removed either way, so the old value never overrides anything again.
5. Where Thinking off takes effect. The chat page sends the choice with each message as the `think` query
   parameter on `GET /chat/{id}/stream` (default on, so a caller that sends nothing keeps the model's own
   behaviour; the chat page always sends the user's choice). Off:
   - makes the streamed local answer ask Ollama for `think: false` for that turn;
   - reaches the intent classifier (`classify`), which runs before the first word on every turn whose text looks
     like an action ("make", "report", "every" ...), and the history summariser, which runs on each turn of a chat
     whose history exceeds the context budget, and the task parser, a second structured call on a schedule-style
     turn. With Thinking on all of them keep today's behaviour, including the
     classifier's `harmful` judgement (the deterministic harmful-pattern check runs either way);
   - reaches the blocking answer path, which a turn uses when it searches the web (now the common case), has an
     image, escalates to a cloud model, uses an organisation server, or carries an injection-suspect question:
     the local Ollama answer and the answer composed from web results both get `think=False`;
   - On sends nothing and the model decides, exactly as before.
   Only Ollama receives the flag, so it changes nothing for chats that run on a cloud or organisation model or on an
   OpenAI-compatible endpoint (the button, the notice and the Settings row say "only affects models running on this
   machine"). Models that cannot think ignore it (verified against Ollama 0.24; the desktop app bundles Ollama
   0.30.10, which was not checked). Models that always think ignore `think: false` too: gpt-oss, for one, takes only
   thinking levels in Ollama and cannot turn thinking fully off.
6. The web-search planning call (`plan_web_query`, which decides whether to search and writes the query) never
   thinks, for every user. It is a small structured decision under a token cap, so hidden thinking only adds delay
   and can use up the cap and leave no JSON.
7. A turn that searches the web shows its steps and streams its answer (issue #108,
   `docs/specs/108-web-turn-stream.md`). Before #108 it was answered in one piece. Thinking off still shortens
   the wait for the first word, because a model that thinks does so before the first word.
8. The first time a user opens chat, agents or tasks (whichever comes first) a one-time notice states the current
   Thinking and Web search defaults, lets the user change either right there (saved at once as the default), says
   how to change them for one chat (Thinking button and Options) or for every chat (Settings, with a link to each
   tab), and notes that Anthill may still search the web by itself for a question that clearly needs live
   information. In an organisation account the web part says "in your personal chats", because organisation chats
   always search the web. On the Agents and Tasks pages it also says that agents and scheduled tasks keep their own settings.
   It stays until the user clicks "Got it", changes a switch in it, or opens Settings from it, and that last save
   survives the navigation. Existing users see it once after the update.
9. `POST /settings/chat-defaults` (signed-in users only) accepts JSON with `thinking_on` and `web_access`
   booleans and `seen: true`, ignores anything that is not a real boolean, and returns the saved values.
10. Honest wording, and the box decides. Where web search is on, the Solo header says "Web search is on for this
    chat" and the Settings card "What leaves your setup" says that the text of a question is sent to a search
    provider (and keeps "Your cloud" when that applies). "Nothing leaves it" is claimed only while web search is
    off for the chat, and since 2026-10-10 that is true of web search: a question that clearly needs live
    information no longer turns the web on by itself (founder decision, 2026-10-10; the `needs_web_hint` branch of
    `chat_stream` was removed, and with the box off the agent path gets no web tools, as before). The one way a
    question can still be searched with the box off is the user asking for it in plain words as a follow-up
    ("check the web", `redo_mode` "web"), which is an explicit request. A cloud escalation still needs its own
    consent. Agents and scheduled tasks keep their own settings, and organisation chats keep their own rule (web
    search always on); neither is described by the Solo header.

## Out of scope

- The defaults govern chats. Agent runs, scheduled tasks, the council and deep research keep their own thinking
  behaviour, and an agent's web access stays with its own permissions. Applying the defaults there is a follow-up.
  Some chat turns are handed by Anthill itself to its multi-step agent ("Looking into this more thoroughly",
  `looks_deep`); those are agent runs and ignore Thinking.
- Streaming the answer of a turn that searches the web (requirement 7) is issue #108, `docs/specs/108-web-turn-stream.md`.
- No extra "Thinking..." indicator: the chat already shows its own progress animation.
- Adaptive thinking chosen by the task router.

## Acceptance criteria

- `GET /chat/{id}/stream` without `think`, or with `think=true`, calls the answer functions with `think=None`;
  with `think=false` it calls them with `think=False`, for a plain question and for a web-search question.
- The planner call carries `think=False`; `json_chat` passes `think` and falls back for backends that do not take it.
- With Thinking off the intent classifier, the history summariser and the task parser carry `think=False`; with it
  on they send nothing. A harmful create request is still refused with Thinking off, both on the model's flag and
  on the deterministic pattern alone.
- A Solo chat and a project chat in an install with no organisation server follow the web default (checkbox,
  page default and the notice hook); a project chat on the shared model and an organisation chat start on and are
  never rewritten.
- `ask(...)` passes `think=False` to the web-composed answer and to the local answer, and sends nothing when
  `think` is unset.
- A new user has Thinking off, Web access off and has not seen the notice. The notice renders on chat, agents and
  tasks until acknowledged, then never again, and the Agents and Tasks versions say that agents and tasks keep
  their own settings.
- `POST /settings/chat-defaults` saves booleans, ignores other types, rejects bad JSON, and needs a session.
- The chat page seeds the Thinking button and the Web search checkbox from the saved defaults; an organisation chat
  stays on for web search and follows the Thinking default.
- An existing database gains the two new columns with their defaults and keeps its web value.
- A new account has Thinking off and web search off; an old database keeps Thinking on and its web value; the chat
  page, its Thinking button, its Web search checkbox, the Solo header and the first-use notice all show both off for
  a new account, and on once they are turned on.
- In a real browser: a chat remembers its own Thinking and Web choices across reloads, another chat is
  unaffected, a chat without a choice follows the default, and the next message is requested with `think=false`
  when Thinking is off. The notice appears once, saves the default it changes, hides on "Got it", and still saves
  when Settings is opened from it. An old browser-wide "off" becomes the account default once, an old "on" is only
  removed. A changed web default is applied to a personal chat without its own choice and never to an
  organisation chat.
