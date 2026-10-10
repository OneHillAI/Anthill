# Spec: Members get a fixed message when something fails

Status: implemented. Lane: `pillar:platform`.

## Problem

Several routes put the text of an exception into their answer. For a signed-in member, or a caller holding an
MCP consumer or agent token, that text can carry a path on the server (an operating system error names the
file it could not open). A failure should say what went wrong in plain words and keep the detail in the server
log, where the operator reads it.

## Requirements

- MCP server (`mcp/server.py`): a failing exposed tool or agent tool answers `Tool failed.`; the exception is
  logged. A refusal from our own permission check keeps its reason, and so does a bad request to an agent tool
  (`ValueError`, answered as `Invalid params: <reason>`), because that text is written by us.
- Outbound MCP calls (`mcp/client.py call_tool_raw`): the string handed back to the agent loop and to the
  document-source listing is `MCP call failed (<tool>).`; the exception is logged. When the remote MCP server
  itself answered with an error, its own message is kept (`MCP call failed (<tool>): <message>`); only
  exceptions raised on this server are hidden.
- Document preview (`multimodal/files.py preview_html`): a failed preview shows "Preview unavailable - use the
  download link."; the exception is logged.
- Chat answer export (`POST /chat/download`): a missing optional library answers "this format needs an
  optional library that is not installed on this server" and any other failure answers "could not create the
  document", both with status 500; the exception is logged. An unsupported format is still named (400).
- Provider model discovery (`POST /settings/escalation/discover-models`, open to any member): the kind of
  failure picks a fixed sentence (401 or 403: the key was not accepted; 429: rate limited; connection or
  timeout: could not reach the provider; anything else: could not list the models); the exception is logged
  with its traceback.
- Diagnostics that only an admin can reach and that explain the admin's own configuration are unchanged.

## Known limits

These routes still return or store the exception text and are not changed here:

- `/chat/{conv_id}/stream`: a generation failure is streamed to the member and saved in the conversation.
- Agent tool results and approvals: `Tool.call` returns `ERROR: <text>` to the model, which can repeat it.
- `POST /wiki/import.okgf`: a bundle that cannot be read answers with the parser's message.
- The node agent `/ask` route.

They are separate changes because the model, the saved history or the caller depends on the text.

## Acceptance criteria

- For each route above, an error whose text names a server path is absent from the response and present in
  the log.
- A permission refusal from the agent-to-agent check still shows its reason.
- An unsupported export format is still reported by name.

## Tests

`tests/test_no_raw_errors.py`.
