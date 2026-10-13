# Chat & your knowledge base

## Chat

Ask a question and Anthill answers from your own wiki, with citations, not from the open internet.
It remembers the conversation. It also judges on its own how hard to think. A simple question gets
a fast answer. A multi-step one runs deeper automatically, as long as your model can use tools; on a
model that cannot, it gets the normal answer instead. A bare greeting, thank-you or short
acknowledgement ("hi", "thanks", "ok", "yes") is answered without looking anything up in your wiki, so it
comes back quickly; "yes", "ok" or "yes thanks" after a question of its own is treated as a normal message, not as a greeting. Ask in plain words for more, like "go
deeper" or "check the web". Or ask it to do research and get back a cited report from several
sources.

Ask it to *make* something, like "draft an onboarding email" or "give me this as a spreadsheet",
and it proposes the result for you to confirm before anything is created. Ask for something
recurring, like "every morning, summarize my inbox", and it proposes a scheduled task instead.

You can attach an image to a message. Anthill reads it with a local vision model. No setup needed.

Under the message box sits a row of three buttons. **Web search** turns web search on or off for this
chat. **All knowledge** opens a list where you choose which wikis the answer may draw on, for example
**Personal only**; the button then shows your choice. **Thinking** is described below.

While an answer is being written, small ants run inside the reply with a line that says what is
happening, for example "Searching the web". The words of the answer appear above them, and the ants
are gone once the answer is complete. When your inference provider is answering, the ants wear capes and
the line names the provider, for example "Checking with Infercom".

Some local models think before they answer, and you see nothing until they finish. That can take ten
seconds or more on a laptop, and minutes when a web search is involved. Thinking is off by default for
a new account, so a plain answer starts in seconds. The **Thinking** button under the message box turns it on for
a chat: click it to read "Thinking on" and the model reasons more carefully on hard questions, at the
cost of a longer wait. Click again to turn it back off. It only changes models that run on this machine and
that can think. For a model that cannot (Gemma and Mistral, for example) the button reads "No thinking
mode", is greyed out and does nothing, because the answer is the same either way. Models that always think
(gpt-oss, for one) cannot turn thinking fully off. A question that searches the web shows what it is doing ("Searching
the web", "Reading 5 pages", "Writing the answer") and then streams its answer word by word, like any other. With
Thinking on, the first word still waits until the model has finished thinking. If the web search cannot be used
(you are offline, the search is blocked, or it finds nothing usable), the answer starts with a short note that it
comes from your wiki and the model alone, not from the web. When the search works, the answer ends with
up to four of the pages the model was given, as links under "Web sources consulted" (the ones the answer
does not already cite). The model is told today's date, so it can judge how recent a source is and say so
when the newest one is too old for the question. It is also told to keep web text apart from your wiki, so a
page from a site such as Wikipedia is not presented as one of your wiki pages. Both are instructions to the
model, and a small model can still get them wrong. If you ask for today's headlines or news and the search finds only
the front pages and sections of news sites, which list stories but contain none, Anthill says so plainly and
lists those pages under "News pages found" instead of asking the model, which could make headlines up. If a web result carries an
instruction to the assistant, Anthill leaves it out of the answer and says how many it left out under the answer.

Thinking and web search each have a default and a choice for every chat. For a new account both are off,
so answers start quickly; turn either one on when you want it. An account that existed before this
changed keeps the values it had. Set
the defaults in **Settings**: Thinking under **Model**, Web access under **Privacy**. A web search
sends the text of your question to a search provider, so turn it off if a question must not leave
your machine.
In a chat, the Thinking button and the Web search button under the message box change them for that chat only, and Anthill
remembers the choice for that chat. A chat you have not changed follows the default, so changing the
default changes those chats too. The first time you open Chat, Agents or Tasks, a short notice tells you
what the defaults are and lets you change them. With web search off, nothing is searched on the web unless
you ask for it in plain words as a follow-up, for example "check the web".

### Group chats with a project

A project is the one way to group chats. Chats in no project sit under **Unfiled** in the sidebar.
Each project has its own group there, even before it has a chat, and a **+** that starts a new chat
inside it. Create a project from **Projects** in the sidebar, or with **+ New project** at the bottom of the
chat list.

Each group in the sidebar shows its 10 most recent chats. The rest fold behind **Show N more**, and
**Show fewer** folds them again. Pinned chats and the chat you are in always show. While you type in
**Search chats**, every match shows. **Search / see all chats** opens the full list.

To put a chat in a project, use the project menu at the top of the chat. You can do it before the
first message or after the chat already has history. The messages stay as they are. Choose **No project**
to take it out again. The chat goes back to Unfiled and is never deleted.

A project on a Solo install runs on your own machine, like any other chat. If your organization's cloud
model serves the project, Anthill asks before it moves a chat that already has messages into it, because
from the next message on those earlier messages are sent to that model. A chat in a project stays yours:
other members of the project do not see it.

## Your knowledge base (the wiki)

Upload a document or paste text and Anthill turns it into a structured page: a title, a summary,
key facts, and links to related pages. Anthill keeps the wiki itself tidy in the background,
fixing broken links and flagging duplicates. The wiki has three tabs:

- **Pages**. The knowledge itself. Browse, search, add, or research a topic into new pages.
- **Principles**. Your own (or your org's) writing rules and house style. Applied automatically
  whenever Anthill answers or drafts something in that scope.
- **Files**. Every original document you've uploaded, with its size and date, and a link to
  download it back. The page Anthill wrote from it is a summary, not a replacement.

Sharing knowledge beyond your own space, into a project or org wiki, always goes through
**review** first. You see exactly what would be written before it's approved.

## Snippets

A snippet is a piece of a chat answer you deliberately keep, rather than waiting for Anthill to
decide it's worth a page on its own. Select the text, or tap the save icon on a message, and it
becomes a wiki page immediately. It comes with a short note on why it's relevant to the question
that produced it. It also becomes one of your own approved ("gold") examples, which is what
personal training draws on. Saving a snippet guarantees it's kept, rather than hoping a good answer
gets noticed on its own.

A snippet only trains the *shared* team or org model once it's corroborated. Either another person
independently saves matching content, or an admin approves it through the wiki review queue. You
can edit, tag, filter, or delete any snippet. You can also push one to the wiki manually at any
time.

## Memory

Anthill quietly distills durable facts and preferences from your chats, tasks, and agent runs. No
marking anything is required. Each memory links back to where it came from, so you can always see
why Anthill remembers something.

You're always in control. Search, edit, or delete any memory. Add one directly. Pause the whole
auto-learning pipeline whenever you want; memories already learned keep working even while paused.
Mark anything **keep personal** so it's never shared, even if it comes up for someone else too.
That's reversible any time.

When the same fact surfaces for two or more people, it's promoted automatically to the shared
project or org level and marked **shared by N**. An admin can also promote one by hand. Any memory
can be turned into a full wiki page proposal when it deserves more than a one-line fact.
