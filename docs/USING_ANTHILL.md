# Your workspace

Anthill is a private knowledge base with a chat that answers from it, running on an open model you
control. Your data and the model stay on infrastructure you choose. This section covers how to use
it day to day:

- **Your workspace** (this page) - Solo, Projects, Organizations, profiles, and privacy.
- **[Chat & your knowledge base](/using-chat-and-knowledge)** - chat, the wiki, snippets, memory.
- **[Automating work](/using-automation)** - tasks, agents, skills, notifications.
- **[Models & compute](/using-models-and-compute)** - choosing a model, running a council,
  cloud compute, escalating hard questions, training your own model.
- **[Connectors & personalization](/using-connectors-and-personalization)** - external tools,
  and tuning how Anthill talks to you.

## Solo, Projects, and Organizations

Everyone starts with a **Solo** space: just you, nothing to set up. You can create an
**Organization** later, from the dashboard, when you want to share work with a team - nothing
about Solo asks you to think about that until you do. Converting to an organization is one-way:
once you've set one up, there's no path back to a plain Solo account.

- **Solo** is your private space - your own wiki, your own model. Choose **Local** (runs on your
  machine, works offline) or **Cloud** (your own connected server, for a bigger model) under
  **Solo settings**.
- **Projects** group a set of chats, tasks, and agents around their own wiki - useful for keeping
  one topic or client separate from everything else. On a Solo account a project is yours alone;
  inside an organization, the same kind of project becomes a shared space you invite members into
  - it's the middle tier between your personal knowledge and the whole org's. A project can
  optionally also read its parent's wiki (your Solo wiki, or the org wiki) read-only; it never
  writes anywhere but its own. Deleting a project keeps its chats, tasks, and agents as your own
  Solo items - only the shared boundary and its memberships go away.
- **Organizations** are the shared tier: one wiki, one model, invited members, multiple projects.
  An admin connects or provisions the org's model and compute once (your own machine, an on-prem
  box, or a cloud GPU), and from then on the org's wiki, its model, and its training all run
  together on that same account. A project's owner approves anything proposed for promotion out of
  that project's own wiki review queue.

Moving knowledge from a smaller space into a bigger shared one (Solo into an org wiki, a project
into the org, say) always goes through a review step first, so nothing reaches a wider audience by
accident.

## Multiple people on one device: profiles

A **profile** is a fully separate account on the same install - its own chats, wiki, models, and
data, sharing nothing with any other profile. Use it to keep, say, work and personal completely
apart on one machine. Your name and a colored chip in the sidebar always show which profile you're
in; click it to switch or manage profiles. In the desktop app, one profile is open per window at a
time; from a terminal, each profile runs from its own `anthill web --profile <id>` command.

## Privacy and control

Anthill is local-first with open weights on your own infrastructure. Your Solo space is fully
private and works offline. Nothing reaches a shared project or org wiki without a review step. If
you ever turn on cloud escalation for a hard question, only that question - stripped of personal
information - is sent, and it's logged. Your knowledge and skills are stored in open formats, so
nothing is locked into Anthill.
