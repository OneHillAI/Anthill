# Chat & your knowledge base

## Chat

Ask a question and Anthill answers from your own wiki, with citations, not from the open internet.
It remembers the conversation. It also judges on its own how hard to think. A simple question gets
a fast answer. A multi-step one runs deeper automatically, if your model can use tools. Ask in
plain words for more, like "go deeper" or "check the web". Or ask it to do research and get back a
cited report from several sources.

Ask it to *make* something, like "draft an onboarding email" or "give me this as a spreadsheet",
and it proposes the result for you to confirm before anything is created. Ask for something
recurring, like "every morning, summarize my inbox", and it proposes a scheduled task instead.

Anthill also looks in the text it kept from your uploaded documents. If a passage matches your
question, the model is given up to two passages of your original words, marked as the original,
before the page's summary, which is marked as a summary that may contain errors. Under an answer,
**Documents in context** and **Pages in context** list what the model was given. They don't show
which parts the answer relied on. A question that no document answers is not slowed down.

You can attach an image to a message. Anthill reads it with a local vision model. No setup needed.

Under the message box sits a row of three buttons. **Web search** turns web search on or off for
this chat. **All knowledge** chooses which wikis an answer may draw on. **Thinking** turns on
deeper reasoning for harder questions, at the cost of a longer wait; it's off by default so answers
start fast, and not every model supports it. All three are set here, chat by chat. Web search and
Thinking also have a default you set once under **Settings**.

A web search shows its sources. If search isn't available, Anthill says the answer came from your
wiki and the model alone. Searching sends only your question to a search provider, never your
documents, so turn it off for anything that must stay on your machine.

Under each answer a small label says where it ran: **On this device** when nothing left your machine,
or **Sent over the network** when a model on another machine answered. The pages listed under **Pages
in context** are links; click one to open that page in a new tab. A small **⏱** line gives the time
to the first word and the speed of the answer, as measured on the model server. A figure the engine
does not report is left blank.

### Group chats with a project

Group chats into a **project** to keep them together. Open a project to start a chat inside it, or
move an existing chat in from its menu at the top. A chat you move keeps its messages, and moving
it back out never deletes anything. A project on a Solo install stays on your machine; in an
organisation, Anthill asks before moving a chat with existing messages into a project that runs on
a shared model, since those messages would then be sent there. A chat in a project stays yours: other
members of the project do not see it.

## Your knowledge base (the wiki)

Upload a document or paste text and Anthill turns it into a structured page: a title, a summary,
key facts, and links to related pages. A short plain-text note is the exception. A `.md` or `.txt` file
of up to 1,500 characters is filed exactly as written, under its own first heading, with nothing
summarised. A longer one is still summarised, but the page keeps the note's own first heading as its
title and its quoted passages word for word. Anthill keeps the wiki itself tidy in the background,
fixing broken links and flagging duplicates.

Under **Add a document**, **Recent uploads** lists your latest ten uploads to that wiki, newest
first, with what happened to each one: *Reading*, *Added*, *Waiting for your review*, *Held for your
decision*, *Waiting for the model to finish downloading*, *Timed out*, *Couldn't read* or
*Interrupted*. Each says what to do next. A document that waited for a model download is added by
itself when the download finishes. An upload that timed out offers **Try again with a longer limit**,
which uses the file Anthill already saved, so you don't choose it again. Only you see your own list.

A PDF is read within limits: up to 200 pages, 25 MB and 10 minutes of reading. A PDF over the size
Anthill reads without asking needs you to tick **Confirm larger PDF processing** and choose it
again. Beyond the limits nothing is added, and the message suggests splitting the file.

If an upload is the same file as a page already in the wiki, lands on an existing page's title, or
looks like a newer version of a page, Anthill writes nothing and holds it in **Needs review**, under
**Knowledge**. For a duplicate or a title clash you choose **Keep existing**, **Replace existing** or
**Keep both**, and one is marked as recommended. For a possible newer version the card shows the old
and new text side by side with the changed words marked, and you choose to replace the old page, keep
both, or keep the old page and discard the new one. Replacing writes over the old page's text; the
file you uploaded earlier stays under **Files**. **Needs review** also holds pages the automatic check
flagged and your AI's proposals, with one count that also shows on **Knowledge** in the sidebar.
While an upload is waiting there, or after it is rejected or removed, Anthill does not use it in
answers: not in chat, by agents, in scheduled tasks or in cached answers. An upload made before this
rule existed may still be read until the install owner runs `anthill knowledge inventory --apply`,
which makes a backup first and can be undone.

The wiki has three tabs:

- **Pages**. The knowledge itself. Browse, search, add, or research a topic into new pages. Each page
  says how it was made: "AI summary written by" the model that read the document, or "The note as
  uploaded" for a note filed as written, with a link to the original file.
- **Principles**. Your own (or your org's) writing rules and house style. Applied automatically
  whenever Anthill answers or drafts something in that scope.
- **Files**. Every original document you've uploaded, with its size and date, and a link to
  download it back. For a PDF, Word, PowerPoint, Excel or HTML file there is also a **text read from
  it** link, which opens the text Anthill extracted, so you can check what your AI actually saw.
  Nothing is kept for a note, which already is the text, or for a scan or picture where no text could
  be read. The page Anthill wrote from it is a summary, not a replacement.

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
independently saves matching content, or an admin approves it in **Needs review**. You
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
