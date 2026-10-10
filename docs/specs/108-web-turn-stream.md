# Spec: A web-search turn shows its steps and streams its answer

Status: implemented. Lane: `pillar:feature`. Source: issue #108; founder, 2026-10-09.

## Problem

1. A chat turn that searched the web ran the blocking `ask()`. `can_stream` in `chat_stream` was false whenever web
   search was in play, so nothing appeared until the whole answer was written, and then all of it appeared at once.
   Measured on an M4 Mac with 16 GB, Ollama 0.24.0 and qwen3.5:9b, through the app with a fresh server, Thinking
   off: a short question took 59.9 s with nothing to read, a long one (a four-day trip plan) 136 s.
2. Where the time goes (script on the same code, no wiki, one run): planning the search 3.1 s, the search and five
   page reads 4.7 s (each page 0.15 to 0.33 s, so reading them in parallel would buy little), writing the answer
   35 s. With Thinking left to the model the same turn took 311 s, nearly all of it the answer step; another run
   through the app took 73.5 s, because how long the model thinks varies a lot from run to run.
3. The route called the planner (`decide_web`), the classifier, the task parser and `ask()` directly inside an
   `async` function, so while such a call ran the server's event loop was held and no other request could be
   answered. Memory recall and the cache lookup also run there, but each is one embedding call.
4. The blocking path existed because a streamed answer cannot be taken back, and `ask()` can check an answer for a
   hijack before showing it. That check looks at the question and the wiki context, not at web text, so it was
   never a defence against a hostile web page; the fence and the system rule around the web results were.

## Requirements

1. `ask_stream(..., web_search=True, search_query=...)` runs the web step itself. Before the first word it yields
   `Stage` markers, in this order: `searching`; `reading` with the number of results; `left_out` with a count,
   only when results were kept out of the prompt (requirement 3); then `thinking` or `writing`. `thinking` is
   yielded only when Thinking is not off, the backend is Ollama and Ollama reports that the model can think
   (`OllamaBackend.can_think`, from `/api/show` capabilities; unreadable counts as no); otherwise `writing`. Then
   the answer streams. A turn without `web_search` yields no marker.
2. The steps are those of `search_and_answer`, shared through `web_search`, `read_pages`, `drop_injected` and
   `web_messages`: the search uses the planned query, each result page is read, and the prompt is built as before
   (wiki context, memory, history, the fenced `WEB RESULTS` block, the question), fitted to the window of the model
   that answers (the routed model, as `ask()` does), as in #109. `search_and_answer`, which returns the whole
   answer, differs only by requirement 3.
3. A streamed answer cannot be taken back, so:
   - a question that carries an instruction-override pattern, or a wiki context that does, still goes to the
     blocking `ask()` (with its web search and Thinking choice), as before;
   - a web result whose text carries such a pattern (title, address, snippet or the page text that goes into the
     prompt) is left out of the prompt, and the rest are used. `search_and_answer`, the blocking path, does the
     same, so a hostile result no longer reaches the model on either path. The old output check looked at the
     question and the wiki context, not at web text. A result is judged by a pattern, so a benign page about
     prompt injection can be left out too; the streamed path therefore tells the page how many results were left
     out (requirement 8). The blocking path leaves them out without saying so, because `ask()` returns only the
     answer, the slugs and the cache flag.
4. A turn that searches the web never reads or writes the semantic cache and never asks or publishes to the
   organisation index: `ask()` skips every cache and org-index step when `web_search` is true (so a turn whose
   search failed is not cached either), `ask_stream` stores nothing for a web answer, and the chat route does not
   look the question up in the cache when the turn searches the web, so a cached answer never stands in for the
   search. The streamed web answer gets no "go deeper" suggestion; the blocking `ask()` may still append one to
   a web answer, as before.
5. If the search fails (offline, rate-limited), the turn answers from the wiki and the model, as `ask()` does.
   If the model fails before the first word of a web answer (the engine refuses the call, or a reasoning model
   spends its whole answer budget thinking and says nothing), the same prompt is tried once more with Thinking
   off, as `ask()` does; if that fails too, the turn answers from the wiki and the model without the web. An
   error after the first word is raised, because words are already on the page. The answer budget
   (`ANSWER_MAX_TOKENS`) counts thinking tokens, so this retry is what keeps a long thinking phase from leaving
   the user with nothing.
6. The Thinking choice reaches the streamed web answer (`think=False` for an Ollama model when Thinking is off).
7. `chat_stream` sends each marker as `data: {"meta": {"stage": <name>, "count": <n>}}`, before the first token.
   It no longer takes the blocking path for a web turn. The planner call (`decide_web`), the intent classifier, the
   task parser and the remaining blocking `ask()` (image, cloud, organisation server, injection-suspect question)
   run in a worker thread, off the event loop. Not moved: memory recall and the cache lookup (one embedding call
   each), grading an answer for escalation (`grade_answer_locally`) and extracting memory after an answer
   (`mem.extract`), which are model or embedding calls that still run on the loop. The claim is therefore that
   the model calls on the way to an answer no longer hold it, not that nothing does.
8. The chat page turns a stage into one line under the ants (`#build-status`): "Searching the web…", "Reading N
   pages…" ("Reading 1 page…", "No web results. Writing the answer…"), "Thinking, then writing the answer…" and
   "Writing the answer…". An unknown stage shows nothing. The first word clears the line. A `left_out` stage is
   not a status line: it adds "N web results left out" (with an explanation as its tooltip) to the line of small
   notes under the finished answer.

## Acceptance criteria

- A web turn yields `searching`, `reading` and `thinking` or `writing`, in order and before any word, and then
  streams the model's words (`tests/test_web_turn_stream.py`). `thinking` appears only for an Ollama model that
  can think with Thinking not off; an empty search still answers and says "(no web results found)" in the prompt.
- The web prompt is fitted to the routed model's window, not the base model's.
- The pages are read after the search and before the model call, with the planned query; the prompt holds the
  fenced web block, the question and the wiki page.
- A hostile result, or a hostile page body, is absent from the prompt on the streamed and on the blocking path
  while the clean results and a benign page that merely mentions "instructions" stay and the answer still
  streams, and the count of results left out is announced. A hostile question, and a clean question over a hostile
  wiki context, go to `ask()` with `web_search` and `search_query`.
- A turn that searches the web does not read or write the semantic cache or the organisation index in `ask()`, does
  not store a streamed web answer, and the route does not read the cache for it (while a local turn still does).
  A failed search answers from the wiki and the model without a web block. A model error or an empty answer
  before the first word is retried without thinking and then falls back to the wiki answer; an error after the
  first word is raised. Leaving during the steps ends the turn before the model is called.
- Thinking off reaches the streamed web answer; `search_and_answer` still returns a whole answer.
- On the route, the stage events arrive before the first token and are never part of the answer; a web turn does
  not call the blocking `ask()`; the planner, the classifier, the task parser and the blocking `ask()` run with no
  event loop running in their thread.
- In the browser, the stage text is exact for each stage; with the stream held open, the line follows the steps and
  is gone at the first word, not at the end; and the left-out count shows under the answer
  (`tests/browser/test_chat_web_stage_browser.py`).
- The PR holds before and after times, measured through the app: when the first step appears, when the first
  word appears, when the answer is complete.

## Out of scope

- Reading the result pages in parallel (each read took 0.15 to 0.33 s).
- A check of the streamed web answer against a hijack after the fact. The defence is the fence, the system rule
  and leaving out a result that carries an override pattern.
- Agents, scheduled tasks, the council and deep research.
- Whether Thinking is on or off by default (`docs/specs/chat-thinking-toggle.md`).
