# Spec: A web answer is easier for a small model to get right

Status: implemented. Lane: `pillar:feature`. Builds on `web-source-labels` and `web-search-failure-notice`.

## Problem

Most people run a small local model, and a larger one does not fit their memory, so the answer to a weak web
answer cannot be "use a bigger model". On the small models the web search worked, but the answers were weak
(measured on the four models installed on a build machine, three questions, real searches):

- the default model wrote real links in 2 of 6 runs (the others wrote none, or `[[Page]]` pseudo links);
- nothing told the model the date, so a month-old page was reported as "today's news";
- a news question was answered from topic pages (Reuters, Google News, CNBC) that list stories but contain none,
  and a small model sometimes invented headlines to fill the gap;
- a "latest version" question was answered from a blog or an older release note as often as from python.org.

## Requirements

The choices made in code read the member's own question (`member_question`), not a query a planner rewrote and
not the text of a "redo with web search", which carries the earlier answer.

1. **Today's date.** The web prompt starts with "Today's date is about <date>" (the server's date; it can
   differ from the member's by a day) and tells the model to use it to judge how recent a source is and to say
   so when the newest source is too old for the question.
2. **Real sources, made by code, after the checks.** After a web answer, code lists the pages the model was
   given under "Web sources consulted", up to four, each with its site beside the title
   (`- [Title](address) - site`). The list is added **after** the hijack check on the model's own text (the
   whole-answer path returns the answer and the list separately, `answer_from_web`, so the check never sees the
   list: with it attached the short-echo and low-overlap rules no longer fire) and after the "go deeper"
   suggestion. An address the answer already links or writes out is left out: scheme and host are compared
   ignoring case, the path exactly, a trailing slash ignored, and a longer address is not a match. A pair of
   parentheses inside an address belongs to it (Wikipedia's `Python_(programming_language)`), and parentheses
   and percent escapes are compared in their encoded form, so `(` and `%28` are the same. Titles come
   from the web and are cleaned (control, format and bidirectional characters, backslashes and Markdown markers
   removed). Only plain http(s) addresses of at most 500 characters with a parsable host and no name or
   password in front of it are linked; the host is shown and linked in its ASCII form (`xn--` for a non-ASCII
   host), so a look-alike host cannot read as the real site. It applies to the streamed answer (appended as the
   last chunk, so it is saved with the conversation) and the whole-answer path. A failed or non-web turn, a
   discarded (hijacked) answer, and an answer that already cites every page have none. The list is code-made
   text: `strip_web_note` removes it when it ends the text (where code puts it; a block of the same shape inside
   the answer is left alone), together with the failure note, from the history fed to the next turn (also in
   the command-line chat), the grader input, training output, the memory transcript, the skill-draft transcript
   and filed pages.
3. **Better pages in, fewer.** A result is usable when it has some text and an address that can be linked; a
   turn with none is a turn with no usable pages (`NoWebPages`). At most four go in. For a news question (the
   words news, headline, breaking or happening) front pages and named sections give way to article pages when
   there are any. For a "latest/newest/current/stable/release/version/download" question, the official site of
   a project the question names comes first, from a short list (python.org, nodejs.org, rust-lang.org, go.dev,
   postgresql.org, ubuntu.com, debian.org, kubernetes.io, docker.com, djangoproject.com, react.dev, php.net,
   ruby-lang.org, wordpress.org, mozilla.org, ollama.com); a site only called python.xyz or paris.fr is not
   official, and a question about a library, package, module, plugin or API puts nothing first.
4. **No made-up headlines.** When the question is about news and every result is a front page or named
   section, the prompt says so and tells the model to say plainly that no headline is shown and to point to
   those pages.
5. **No headlines to give: the model is not asked.** When the member makes an explicit request for today's
   headlines or news (a short question that opens with a request word such as what, show, give, any, is there;
   ends on "news" or "headlines", optionally with today, tonight, right now or this morning; says today,
   tonight, right now, this morning or breaking; and is not about writing, code, the meaning of the words or a
   named site such as Hacker News) and every result is a site's front page, one named section of it (/technology/, /news/index.html,
   /category/ai/), or a Google News topic or search page, the answer is fixed text made by code and the model is
   not called: no news articles were found, headlines are not guessed, and the pages found are listed under
   "News pages found". An address with a date, with a query string that names a page (?v=, ?id=, ?p=; language
   and tracking parameters are ignored, `q` on a Google News search page is allowed), with more than one level
   below a section, or with a single path word that is not a named section (/press-release) is not a front
   page, and neither are short article addresses such as apnews.com/article/x, bbc.com/news/123,
   ft.com/content/id or peps.python.org/pep-0668/. What was left out is announced before the answer. The fixed
   answer is made by code and signalled explicitly (`Stage("fixed")` on the streamed path, also when the streamed
   turn is handed to the blocking `ask()` for a council or an instruction in the question or the wiki text, and a
   `FixedAnswer` string from `ask()`), not recognised by its words: no wiki page is reported for it, it is not
   kept as a training example, no memory is distilled from that turn, it is not escalated by `ask()`'s hybrid
   policy or filed as a wiki page by `ask(save=True)`, and in Automated mode it is not graded or escalated to a
   provider. (Without any linkable page the
   text is a plain sentence; a turn cannot reach that, because such results are not usable, so it is only a
   guard.)

## Acceptance criteria

- The prompt contains the date; the sources list holds real links from the results the prompt used, cleaned,
  and nothing for a failed or non-web turn; the page selection and the official-site rule behave as above.
- Measured on the same four models and questions as before the change (2 runs each, real searches): real links
  in the answer 12 to 22 of 24 runs; python.org linked in the Python answers 4 to 8 of 8; the right version
  (3.15.0) 6 to 7 of 8; invented `[[wiki links]]` 8 to 6 of 24. For the headlines question with only topic pages,
  with point 4 alone, the default and the 9B model said plainly that no headline was shown in 6 of 6 runs, the
  2B model invented a headline in 1 of 3, and the 7B model listed page descriptions as headlines. With point 5,
  10 of 12 runs (4 models, 3 runs each) got the fixed answer in about 6 s instead of 20 to 30 s, and in the
  other 2 the search itself had failed and the failure note appeared; none of the 12 invented a headline.

## Known limits

- The rules are decided from the address and the words of the question, so they can be wrong for an unusual one.
  A real article at a short address is treated as a topic page for a news question, and kept only if no
  article page is present.
- The date and the "too old" instruction are an instruction; a small model can still ignore them.
- The sources list names the pages the model was given, not only the ones it used.
- A news question still depends on the search engine returning articles; reading the stories behind a topic
  page is not done here.
- The list of official sites is short on purpose; a project that is not on it gets no site put first.
- The fixed answer never looks at the text read from the pages. It does not fire for "top stories today", "latest
  headlines" or "what's in the news?" (the safe direction: the model is asked). A front page whose text is a
  list of stories, such as a news aggregator's, would still get it if the question asked for today's news.
- The phrases "web results" and "wiki context" are rewritten in web text whatever the case, because a heading
  can be written in any case.
- The fixed answer is English text, as the other fixed notices are.
- When Automated mode adds a provider's answer after a web answer, the list of pages is no longer at the end of
  the saved message, so it stays in the history fed to later turns of that conversation.

## Tests

`tests/test_web_answer_grounding.py`, `tests/test_web_source_labels.py` and `tests/test_web_turn_stream.py`. Existing web-turn tests compare the answer without the sources list.
