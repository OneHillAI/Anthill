# Spec: MCP integrations had no path for a Solo account

Status: implemented. Lane: `pillar:feature`.

## Problem

Founder report, 2026-10-01: "the mcp connectors... where is this functionality? it was built in, now
it seems gone. also the app connector." The backend was never removed - `/connectors/mcp` (the full
gallery: Filesystem, Google Drive, Microsoft 365, Notion, Slack, Discord, GitHub, Linear, Jira and
Confluence, Sentry, Git repository, SQLite, Web fetch, plus "Expose Anthill over MCP (server)") is
intact and only requires admin, which every Solo account's own user already is.

What was actually gone was any UI path to it for a Solo account. The only link anywhere
(`personalize.html`'s Organisation tab, "See everything in Manage organisation &rarr;") only rendered
once an account had converted to a multi-user Organisation (`is_org_deployment`); a Solo account's
Organisation tab showed nothing but a "Set up an organisation" prompt. `_sidebar.html`'s own comment
claimed "Integrations is admin-managed there [Settings]; members reach it through an admin" - true for
an org, but there was no "there" for Solo to reach at all. The route itself was always reachable by
typing `/connectors/mcp` directly; it just had zero discoverable entry point, which is indistinguishable
from "gone" for a user who does not already know the URL.

## Fix

Integrations is now its own top-level Settings tab - `Model | Knowledge | Personality | Privacy | This
device | Integrations | Organisation` - matching the existing tab bar's own design language exactly
(same `.settings-tabs` link, same `.st-panel`, same `set-cardh` + status pill + `set-note` + outline-
button card shape as the "Cloud & model" card in the Organisation tab), rather than folded into
Organisation or added to the main sidebar rail. It is gated the same way Organisation already is
(admin-only, via `user.role == 'admin'`), which costs a Solo account nothing since its one user always
has that role, and correctly still requires an admin in a multi-member org.

The card shows a live status pill ("Not set up" / "N connected", via a new `integrations_connected`
count in `personalize()`'s context - an `MCPServer` count filtered to `status == "approved"`) and a
"Manage integrations" button to the existing `/connectors/mcp` page - the full gallery itself is not
duplicated inline, matching how the Organisation tab's own "People" / "Cloud & model" cards link out to
their full pages rather than embedding them (`/connectors/mcp`'s OAuth flow, server test/approve, and
consumer-token management do not fit inside `/personalize`'s own outer `<form>` without recreating the
nested-form bug class this codebase already avoids elsewhere).

The sidebar's Settings sub-rail (shown when `/personalize` is the active page) gets the matching
`<a href="/personalize#integrations">Integrations</a>` entry, same as every other tab.

`/connectors/mcp`'s own "back" link (`_back_nav_for` in `app.py`) previously always pointed to
`/settings/org` ("Manage organisation") via the generic `_ORG_SUBSETTINGS` list - correct when this
page was only reachable through the org hub, actively misleading now that Solo links in directly from
its own Settings tab. `/connectors/mcp` gets its own case ahead of that fallback: back to
`/personalize#integrations` ("Settings"). The org hub (`/settings/org`) still also links to
`/connectors/mcp` for anyone arriving that way; both paths work, `/connectors/mcp` just now owns its
own correct default.

## Verified live, not just in the gallery

The gallery rendering is not the same as the feature working. Live-tested end-to-end in a real browser
against a real dev server, with the `mcp` Python extra installed (`pip install -e ".[mcp]"` - not
installed by default; the page's own "MCP runtime is not installed here" banner already discloses this):

- **Filesystem** (zero credentials needed): Add (folder path) -> Test -> the backend spawned a real
  `npx -y @modelcontextprotocol/server-filesystem <path>` subprocess over stdio and reported back 14
  real tools (`read_file`, `read_text_file`, `search_files`, ...) -> Approve succeeded. A genuine,
  complete connect-and-activate cycle, not a mocked one.
- **Google Drive** (OAuth, "Requires provider setup"): clicking it shows its own honest banner -
  "This connector needs setup on the provider side first... Needs Google Cloud OAuth credentials.
  Follow the setup guide to authorise, then Test." - Anthill does not ship a pre-registered OAuth
  client for any provider; an admin must register their own app with that provider first. This is
  the catalog's documented, correct behavior, not a bug this change touches.
- **Slack / GitHub** ("Connect now", token-based): same code path as Filesystem, just with a pasted
  bot token / PAT the admin creates themselves instead of a folder path - not independently live-
  tested here (would need a real, disposable token), but nothing in the flow differs from the
  Filesystem case just verified.

## Verification

New `tests/test_integrations_discoverable.py` (a true Solo fixture - `deployment_topology="solo"`, not
an org - the gap the existing `test_nav_roles.py` suite's own fixture never exercised, which is why
this regression shipped untested): the Integrations tab and its link to `/connectors/mcp` render on
`/personalize` for Solo; a Solo admin can actually load `/connectors/mcp` (200, gallery present); the
Settings sub-rail carries the matching link; `/connectors/mcp`'s back link points to Settings, not
"Manage organisation"; the status pill reads "Not set up" with none configured and "N connected" once
an approved `MCPServer` row exists. Full suite: 2819 passed, 8 skipped. `ruff check` / `ruff format
--check` and the em/en-dash slop gate clean on all three touched files.

## UI/UX review (2026-10-01)

The UI/UX design session reviewed this tab plus `mcp.html` (the gallery it links to) live, as a Solo
admin. One finding was in scope here and fixed: every other Settings card header on this page has a
`?` help-pop explaining its jargon inline (9 of them, via `grep`) - Integrations was the one exception,
and "MCP" is exactly the term that needed it. Added, matching the existing pattern exactly; locked in
by a new assertion in `test_solo_settings_has_an_integrations_tab_linking_to_connectors`.

Three further findings are about `mcp.html` itself - pre-existing, not introduced by this change, but
now reaching a wider (and more likely non-technical) audience precisely because this fix makes the page
easier to find. Out of scope for this PR; left for the founder to prioritize separately:

1. The "MCP runtime is not installed here... `pip install -e \".[mcp]\"`" banner is developer-facing
   language that could reach a non-technical Solo user if the packaged desktop app ever ships without
   the `mcp` extra.
2. "Expose Anthill over MCP (server)" (consumers, scoped tokens, review mode, access log) is a
   different feature from what "Manage integrations" promises (connecting Slack/Drive/etc TO Anthill,
   not exposing Anthill's own brain outward) and has zero visual separation from the connector gallery
   above it - a user just wanting to connect Slack scrolls through org-brain-exposure config that does
   not apply to them.
3. Slack is badged "Connect now" (no warning) but actually requires the same out-of-band setup as
   Google Drive's honestly-badged "Requires provider setup" - creating your own Slack bot/app at
   api.slack.com and pasting its token, with "Open the setup guide" linking to a developer-facing raw
   GitHub README. The badge's real distinction is "paste credentials you already have" vs. "OAuth
   redirect," not "easy" vs. "hard," and Slack's own copy contradicts its badge.
