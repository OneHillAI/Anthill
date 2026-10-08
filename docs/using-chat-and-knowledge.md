# Chat & your knowledge base

## Chat

Ask a question and Anthill answers from your own wiki, with citations, not from the open internet.
It remembers the conversation. It also judges on its own how hard to think. A simple question gets
a fast answer. A multi-step one runs deeper automatically. Ask in plain words for more, like "go
deeper" or "check the web". Or ask it to do research and get back a cited report from several
sources.

Ask it to *make* something, like "draft an onboarding email" or "give me this as a spreadsheet",
and it proposes the result for you to confirm before anything is created. Ask for something
recurring, like "every morning, summarize my inbox", and it proposes a scheduled task instead.

You can attach an image to a message. Anthill reads it with a local vision model. No setup needed.

Some local models think before they answer, and you see nothing until they finish. That can take ten
seconds or more on a laptop. The **Thinking** button under the message box turns this off. Click it
to read "Thinking off" and the answer comes sooner, at the cost of less careful reasoning on hard
questions. Click again to turn thinking back on. It only changes models that run on this machine and
that can think. Other models answer the same either way, and so do models that always think (gpt-oss,
for one, cannot turn thinking fully off). A question that searches the web shows its answer once it is complete,
word by word only when it does not search, and Thinking off shortens that wait too.

Thinking and web search each have a default and a choice for every chat. Both are on by default for a new
account. Set
the defaults in **Settings**: Thinking under **Model**, Web access under **Privacy**. A web search
sends the text of your question to a search provider, so turn it off if a question must not leave
your machine.
In a chat, the Thinking button and the **Options** button change them for that chat only, and Anthill
remembers the choice for that chat. A chat you have not changed follows the default, so changing the
default changes those chats too. The first time you open Chat, Agents or Tasks, a short notice tells you
what the defaults are and lets you change them. Even with web search off, Anthill may search the web on
its own when a question clearly needs live information, and it marks that answer with a globe.

### Group chats with a project

A project is the one way to group chats. Chats in no project sit under **Unfiled** in the sidebar.
Each project has its own group there, even before it has a chat, and a **+** that starts a new chat
inside it. Create a project from **Projects** in the sidebar, or with **+ New project** at the bottom of the
chat list.

To put a chat in a project, use the **Project** menu at the top of the chat. You can do it before the
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
