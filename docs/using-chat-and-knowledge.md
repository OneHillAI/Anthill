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
**What this answer was given** lists each page and document the model was given, with the wiki it
came from, the page's version or the document's page numbers, and a link that opens it. A page a
model wrote that nobody has checked is labelled **AI-written, unchecked**; it still answers. The
list doesn't show which parts the answer relied on. Under it, an **Also given** list names your rules,
your profile (the core page), the remembered notes that were given, how many earlier messages of the
chat went in, and any web pages, marked **From the web (not checked)** and shown as links. A last line
says which model answered and in which mode. It never shows the text of your rules or messages. An
answer from agent mode lists the pages the agent's own search and read steps returned, and Slack
answers, task runs and agent runs keep the same list. The chat page also lists everything used across
the chat under **Used in this chat**. Answers from before this was added show the two plain lines
**Documents in context** and **Pages in context** instead. A question that no document answers is
not slowed down.

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
or **Sent over the network** when a model on another machine answered. The items listed under **What
this answer was given** are links; click one to open it in a new tab. A small **⏱** line gives the time
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
file you uploaded earlier stays in the **Library**. **Needs review** also holds pages the automatic check
flagged, pages an agent proposed (marked as written by an agent) and your AI's proposals, with one count
that also shows on **Knowledge** in the sidebar.
While an upload is waiting there, or after it is rejected or removed, Anthill does not use it in
answers: not in chat, by agents, in scheduled tasks or in cached answers. An upload made before this
rule existed may still be read until the install owner runs `anthill knowledge inventory --apply`,
which makes a backup first and can be undone.

The wiki has two tabs, **Library** and **Principles**.

- **Library**. Every page and every original document you've uploaded, in one list. Search by title,
  show only pages or only documents, or show only pages marked **AI-written, unchecked**. A page made
  from a document links to it, and the document links back to the pages made from it. Each page says how
  it was made: "AI summary written by" the model that read the document, or "The note as uploaded" for a
  note filed as written. For a PDF, Word, PowerPoint, Excel or HTML file there is also a **text read from
  it** link, which opens the text Anthill extracted, so you can check what your AI actually saw. Nothing
  is kept for a note, which already is the text, or for a scan or picture where no text could be read.
  The page Anthill wrote from a document is a summary, not a replacement. Adding a document, importing
  from a connected service and researching a topic into new pages are on the same page.
- **Principles**. Your own (or your org's) writing rules and house style. Applied automatically
  whenever Anthill answers or drafts something in that scope.

Choose **Details** on a row, or **About this page** on a page, to see what it is, where it came from and
when, its version, the document it was made from or the pages made from it, and **Used in**: your own
chats and the runs of the tasks and agents you created whose answer was given this page or document,
newest first, with the version it was given and whether it has changed or been removed since. Only your
own answers are listed. Looking changes nothing.

If a page is wrong, open it and choose **Edit this page** to correct the text, or **Edit** on its row.
Your text replaces the page and the earlier text is kept; the page then says it was edited by a person,
and a model upgrade never rewrites it. A page that has been changed has a **History** link. It lists the
page as it is now and each earlier version with its day and who wrote it. Open one to read it and to see
how it differs from the page now, and choose **Restore this version** to make it the page again as a new
version, so nothing is erased. If you save from an out-of-date copy of a page, the save is refused and
your text stays on the edit screen, with links to the page as it is now and to its history. You can edit
pages in your own wiki, in a team wiki if you own the team, and in the organisation wiki if you're an
admin.

To remove something, choose **Remove** on its row in the Library, or **Remove this page** on a page. You
can remove in the wikis where you can edit. Anthill first shows what goes and what stays. Removing a
document removes the document and the text Anthill read from it, and also the pages a model made only
from it that nobody has checked. Pages that have been checked, or may have other sources, stay, and a
page you edited or approved says **Source removed** in the Library and on the page. Removing a page keeps
the document it was made from. Answers stop using what you removed at once. Anthill also clears the
page's search entry and the answers it saved from earlier questions, and the page's earlier versions
move into **Removed** with it. It waits under **Removed**, below the Library, with the days left.
**Restore** brings it back with its earlier versions, **Delete now** deletes it for good, and anything
left is deleted after 30 days. A delete takes it out of Anthill and its search. It cannot remove copies
you already exported, backups, or anything a model was already trained on.

A wiki made before this version is prepared once, automatically, the first time Anthill reads it: it
backs up first, changes no page text, and says so on the wiki page, with how to undo it. Anthill uses a
page or document in an answer only if it has a record that says it is active and approved; if a wiki cannot
be prepared, it says why at the top of the wiki and tries again at the next start.

Sharing knowledge beyond your own space, into a project or org wiki, always goes through
**review** first. You see exactly what would be written before it's approved.

## Snippets

A snippet is a piece of a chat answer you deliberately keep, rather than waiting for Anthill to
decide it's worth a page on its own. Select the text, or tap the save icon on a message, and it
becomes a wiki page immediately. It comes with a short note on why it's relevant to the question
that produced it. It also becomes one of your own approved ("gold") examples, which is what
personal training draws on. Saving a snippet guarantees it's kept, rather than hoping a good answer
gets noticed on its own.

A snippet trains the *shared* team or org model only when an admin approves it in **Needs review**;
other people saving matching content does not move it. You
can edit, tag, filter, or delete any snippet. You can also push one to the wiki manually at any
time.

## Memory

Anthill quietly distills durable facts and preferences from your chats, tasks, and agent runs. No
marking anything is required. Each memory links back to where it came from, so you can always see
why Anthill remembers something.

You're always in control. Search, edit, or delete any memory. Add one directly. Pause
**Auto-remember** whenever you want; memories already learned keep working even while paused. That
switch is only about remembering facts: your answered chats are still kept on this device, privately, as
possible training examples whether it is on or paused, and nothing trains a model until you decide.
Mark anything **keep personal** so it's never shared, even if it comes up for someone else too.
That's reversible any time.

A memory stays personal, however many other people hold a similar one. An admin can promote one to
the shared project or org level by hand. Any memory
can be turned into a full wiki page proposal when it deserves more than a one-line fact.

### Your core page

Your profile and standing preferences live on one private page, the page called `core-page` in your
personal wiki. Once it exists it shows in your own Library as a page titled **Core page**, and you can
open and edit it like any page; an edit keeps the earlier version. It has two parts, **Profile** and **Preferences**. What you write under **Settings**, on
the **Personality** tab ("How your AI sounds"), is saved to its Profile. The page is kept short, about 800
tokens, so it can go into every answer: a save over that size is refused and says how much to cut. It is
given to the model as guidance and is never used as a source of facts. It is read only in your own chats:
your plain chat, chat agent mode, and a project chat on a Solo account. It is not read in team or
organisation chats, Slack, Discord, the widget, scheduled tasks or persistent agents. The list under an
answer shows when it was given. If you never set a profile, you get no core page and nothing changes.

In chat agent mode the agent's **remember** tool proposes one line for the Preferences part. The line
waits in **Needs review**, marked as written by an agent, and it is not on your page, or in any answer,
until you approve it. The tool is not offered where your core page cannot be read. Adding a line yourself,
with a preview, is not on a screen yet.

### What Anthill remembered, kept in a file

The first time you chat after this version, your own memory items are written once to a readable file, and
a data file, in the `archive` folder of your personal wiki. Nothing is deleted or changed, and memory works
as before: automatic memory and saved answers are still on in this release. Choosing items from the
archive to add to your core page is not on a screen yet.
