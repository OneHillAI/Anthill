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

## UI/UX review (2026-10-01) - all findings fixed

The UI/UX design session reviewed this tab plus `mcp.html` (the gallery it links to) live, as a Solo
admin, and found four issues. Founder direction: fix all of them, folded into this same PR.

**1. Missing jargon tooltip.** Every other Settings card header on this page has a `?` help-pop
explaining its term inline (9 of them, via `grep`) - Integrations was the one exception, and "MCP" is
exactly the term that needed it. Added, matching the existing pattern exactly; locked in by a new
assertion in `test_solo_settings_has_an_integrations_tab_linking_to_connectors`.

**2. Developer-facing runtime-missing banner.** "The MCP runtime is not installed here... `pip install
-e ".[mcp]"`" is pip-install language a non-technical Solo user has no way to act on, especially in a
packaged desktop app with no terminal. The packaged sidecar always bundles the `mcp` extra
(`scripts/build-sidecar.sh` builds its lockfile with `--extra mcp`), so seeing this banner there would
mean something in the install is actually broken, not that the user needs to run a command. `mcp_page()`
now passes `is_packaged` (`sys.frozen`, the same signal `anthill/inference/ollama.py` already uses for
this distinction) and the template branches: a packaged install gets "reinstall Anthill / let us know"
guidance; an unpackaged dev checkout keeps the original pip-install message, which is the only place
it's actually actionable.

**3. "Expose Anthill over MCP (server)" had zero visual separation from the connector gallery.** It is
a different feature from what "Manage integrations" promises - exposing Anthill's own org brain OUT to
other tools, not connecting a tool IN - and a user just wanting to add Slack scrolled straight through
consumer tokens, review mode, and an access log that don't apply to them. Wrapped in a collapsed
`<details>` ("Advanced: expose Anthill over MCP"), not removed (a Solo account can still want it), and
left auto-open when `cfg.mcp_server_enabled` is already true so an admin who has it on doesn't lose
sight of their own active config.

**4. Slack's "Connect now" badge was dishonest - and so, it turned out, were four others in the
opposite direction.** Slack needs a bot token you create yourself at api.slack.com (same shape of
prerequisite as any OAuth provider, just copy-paste instead of redirect), but was badged the same
green "Connect now" as truly zero-setup connectors like Filesystem. The badge (`mcp.html`) is driven
entirely by each catalog entry's `guided` boolean, which turned out to just be `auth.type != "oauth"`
- conflating "no setup at all" (`auth.type: "none"`) with "paste a credential you still have to go get"
(`auth.type: "token"`). Checking `anthill/web/mcp_oauth.py`'s own docstring surfaced the mirror-image
bug: Notion, Linear and Sentry are `oauth` + `http` transport, which means Anthill's RFC 7591 Dynamic
Client Registration makes them genuinely one-click with zero admin setup (confirmed by `mcp.html`'s own
`isOAuthHttp` JS branch, which already shows "No setup needed" for them, contradicting their own tile
badge) - they were wrongly badged "Requires provider setup". Atlassian is also `oauth` + `http` but
keeps its existing `guided: false`: its own catalog note documents that one-click OAuth isn't live for
it yet (SSE transport, not yet supported), a real, separate exception. Corrected in
`anthill/connectors/catalog.json`: `github`/`slack`/`discord` (`token`) flip to `guided: false`;
`notion`/`linear`/`sentry` (`oauth`+`http`, no caveat) flip to `guided: true`; `google-drive`/
`microsoft-365` (`oauth`+`stdio`, genuinely need a self-registered app) and `atlassian` (documented
exception) are unchanged. Added a `note` to GitHub's entry (it lacked one, unlike Slack/Discord)
explaining the PAT it needs. `tests/test_stdio_env.py`'s `test_slack_catalog_entry_is_one_click_...`
encoded the old, wrong assumption directly (`assert slack["guided"] is True`) - renamed and corrected
to assert the honest value instead.

Live-verified after all four fixes: Slack now shows the amber "needs setup first" warning (matching
Google Drive's); Notion shows "One-click connect... No setup needed" (matching its new green badge);
the "Advanced" section renders collapsed by default and expands to its full form correctly.
