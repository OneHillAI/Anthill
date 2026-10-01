# Connectors & personalization

## Connecting your tools

Slack, GitHub, Google Drive, Notion, and other tools connect to Anthill as **MCP servers** (MCP is
the standard way agents reach external tools and data) - an admin picks one from a curated catalog,
or adds a custom server by URL or command. Every new connection starts **pending**: an admin tests
it, then approves it, before any agent can use its tools, and every call is logged. If you're not an
admin, the **Integrations** page shows what's already approved and lets you request a new one - it
connects only once an admin reviews it.

The reverse is also possible: Anthill can expose your own wiki, cache, and memory *to* an external
tool over MCP (off by default). Each external caller is registered as its own named **consumer**
with its own scoped, revocable access - limited to just what it needs - and by default every query
it makes is held for admin approval before it's answered, with everything recorded in an access log.

## Personalization

Under **Settings**, tell Anthill who you are and how you like answers - your role, the context you
work in, tone, format, and anything it should avoid - and it assembles that into an editable profile
that guides every response from then on. The same settings area covers where your model runs, what
the wiki and memory draw on, and your privacy choices: whether your own approved answers train your
model, whether personal details are scrubbed first, and whether web search is on by default or only
when you ask.
