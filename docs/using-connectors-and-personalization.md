# Connectors & personalization

## Connecting your tools

Slack, GitHub, Notion, and other tools connect to Anthill as **MCP servers**. MCP is
the standard way agents reach external tools and data. An admin picks one from a curated catalog,
or adds a custom server by URL or command. Every new connection starts **pending**. An admin tests
it, then approves it, before any agent can use its tools. Every call is logged.

Google Drive is in the catalog as a community connector. It can only search file names. It cannot
read or import documents yet.

If you're not an admin, the **Integrations** page shows what's already approved, and lets you
request a new one. It connects only once an admin reviews it.

The reverse is also possible. Anthill can expose your own wiki to an external tool over MCP. Cache
and memory are not offered in this version. This is off by default. Each external caller is registered as its own named
**consumer**, with its own scoped, revocable access, limited to just what it needs. By default,
every query it makes is held for admin approval before it's answered. Everything is recorded in an
access log.

## Personalization

Under **Settings**, on the **Personality** tab ("How your AI sounds"), tell Anthill who you are and how
you like answers. Give it your role, the context you work in, tone, format, and anything it should
avoid. It assembles that into an editable profile, saved to your private core page (see
[Chat & your knowledge base](/using-chat-and-knowledge)). The profile is read in your own chats, in chat
agent mode, and in a project chat on a Solo account. It is not read in team or organisation chats, Slack,
Discord, the widget, scheduled tasks or persistent agents. The page is kept short, about 800 tokens, and
is never used as a source of facts.

The same settings area covers where your model runs, what the wiki and memory draw on, and your
privacy choices. You decide whether your own approved answers train your model, whether personal
details are scrubbed first, and whether web search is on by default or only when you ask.
