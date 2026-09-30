# Spec: the "Web search" toggle must gate every path that can reach the internet

Status: implemented. Lane: `pillar:model`.

## Problem

The chat input's "Web search" checkbox only ever affected the plain-chat path's own decision to
search (`decide_web`/`auto_web` in `anthill/web/app.py`). Agent mode - reached either explicitly or
silently, when the router judges a question needs more depth (`agent_auto`, #421) - always included
`web_search` and `fetch_url` in its toolset via `anthill.agent.tools.make_tools()`, regardless of the
toggle. A user who turned the toggle off, or never saw it because the router silently routed them into
agent mode, could still have their question searched on the open internet.

## Fix

- `make_tools()` gains an `exclude: set[str] | None` parameter; a new `WEB_TOOLS` constant
  (`{"web_search", "fetch_url"}`) names the two tools that reach the internet - matching the existing
  `tool_scope()` "web" scope exactly, so this tracks that taxonomy rather than duplicating it.
- `chat_stream`'s agent-mode branch passes `exclude=None if web_effective else WEB_TOOLS` - the same
  per-turn flag the non-agent path already uses, so one flag now governs both paths consistently.

## Deliberately not touched

- Standing Agents (`agents_run.py`) and Scheduled Tasks (`scheduler.py`) always get the full toolset,
  governed instead by their own `governance` field (approval-gating, not tool availability) and the
  persisted, org-wide `AgentPrincipal`/scopes system. Neither has a per-run "Web search" toggle to
  wire this to, and this change does not add one - out of scope for what was reported.
- The `AgentPrincipal`/scopes mechanism itself is untouched: it is a DB-persisted, org-wide governance
  identity (an admin's standing choice of what the "chat-agent" identity may ever do), not a
  per-message setting. Routing the toggle through it instead of `make_tools(exclude=...)` would have
  meant one user's single unchecked box mutating every other org member's agent-mode permissions.

## Verification

`tests/test_agent_web_tools_toggle.py`: `make_tools()` includes both web tools by default and drops
exactly those two (nothing else) when excluded; the excluded set matches `tool_scope()`'s own "web"
scope; an end-to-end `/chat/{id}/stream` call with `agent_mode=true` confirms the actual tools handed
to `AgentExecutor` omit `web_search`/`fetch_url` when `web=false` and include them when `web=true`.
Confirmed red without the fix (the toggle-off test fails, reproducing the original report) and green
with it. Full suite (2780 passed) and lint/mypy unaffected.
