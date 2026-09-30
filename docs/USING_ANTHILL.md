# Using Anthill

Anthill is a private knowledge base with a chat that answers from it, running on an open model you
control. Your data and the model stay on infrastructure you choose.

## Your workspace

Everyone starts with a **Solo** space: just you, nothing to set up. You can create an
**Organization** later, from the dashboard, when you want to share work with a team - nothing
about Solo asks you to think about that until you do.

- **Solo** is your private space - your own wiki, your own model. Choose **Local** (runs on your
  machine, works offline) or **Cloud** (your own connected server, for a bigger model) under
  **Solo settings**.
- **Projects** group a set of chats, tasks, and agents around their own wiki - useful for keeping
  one topic or client separate from everything else. A project can optionally also read your
  broader wiki; it never writes to it.
- **Organizations** are the shared tier: one wiki, one model, invited members, multiple projects.
  Needs a connected server - your own machine, an on-prem box, or your own cloud.

Moving knowledge from a smaller space into a bigger shared one (Solo into an org wiki, say) always
goes through a review step first, so nothing reaches a wider audience by accident.

### Multiple people on one device: profiles

A **profile** is a fully separate account on the same install - its own chats, wiki, and data,
sharing nothing with any other profile. Use it to keep, say, work and personal completely apart on
one machine. Your name and a colored chip in the sidebar always show which profile you're in;
click it to switch or manage profiles.

## Chat

Ask a question and Anthill answers from your own wiki, with citations, not from the open internet.
It remembers the conversation, and judges on its own how hard to think: a simple question gets a
fast answer, a multi-step one runs deeper automatically. Ask in plain words for more - "go deeper",
"check the web" - or ask it to do research and get back a cited report from several sources.

Ask it to *make* something ("draft an onboarding email", "give me this as a spreadsheet") and it
proposes the result for you to confirm before anything is created. Ask for something recurring
("every morning, summarize my inbox") and it proposes a scheduled task instead.

You can attach an image to a message and Anthill reads it with a local vision model - no setup
needed.

## Your knowledge base (the wiki)

Upload a document or paste text and Anthill turns it into a structured page: title, summary, key
facts, links to related pages. A good chat answer can be saved back as a page too. Anthill keeps
the wiki itself tidy in the background.

Sharing knowledge beyond your own space (into a team or org wiki) always goes through **review**
first - you see exactly what would be written before it's approved.

## Tasks and agents

**Tasks** are work Anthill does on a schedule or on demand - a daily digest, a weekly report -
described in plain words. They keep your local time zone, including through daylight saving, and
you can run one immediately or let it run on its own schedule.

**Agents** are a named worker with a standing job ("an employee with a role") that runs toward it
on its own, using your wiki, memory, and skills, and reports back. Anything consequential - sending
an email, writing to a shared wiki - is held for your approval first; it's never done silently.

Results are automatically double-checked before you see them, and anything that doesn't hold up is
flagged for your review rather than presented as fact.

## Skills

A skill is a reusable instruction set for something you do repeatedly - Anthill can propose one on
its own after an agent does a good job, or you can write one directly. Skills are scoped to you, a
team, or the whole org, and an agent picks the right one automatically when a task matches.

## Memory

Anthill quietly remembers durable facts from your chats and tasks so it doesn't need re-explaining.
You're always in control: view, edit, or delete anything on the **Memory** page, pause it entirely,
or mark something **keep personal** so it's never shared even if it comes up for others too.

## Choosing your model

Anthill lists the current open models ranked by how smart a model your hardware can actually run,
not just the biggest name - an efficient model can beat a larger one. The list refreshes on demand,
never automatically, and works fine offline once you've picked one. Once you have a few answers
you trust, you can benchmark any model against your current one before switching.

## Staying informed

A notification bell in the sidebar collects what needs you - a finished task, an agent paused for
approval, a team invite - so nothing depends on you remembering to check.

## Privacy and control

Anthill is local-first with open weights on your own infrastructure. Your Solo space is fully
private and works offline. Nothing reaches a shared team or org wiki without a review step. If you
ever turn on cloud escalation for a hard question, only that question - stripped of personal
information - is sent, and it's logged. Your knowledge and skills are stored in open formats, so
nothing is locked into Anthill.
