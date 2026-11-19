# Spec: a Solo account and an Org account are strictly separate

Status: implemented. Lane: `pillar:privacy`. Related: `docs/specs/mcp-integrations-tab-for-solo.md`, `docs/specs/one-model-per-account-solo-in-org.md`, `docs/specs/solo-cloud-endpoint.md`, `docs/specs/settings-solo-home-consolidation.md`.

## Problem

An account is Solo or Org by its topology (`OrgSettings.deployment_topology`, read through `normalize_topology`). In
practice the app decided more than it should by whether a backend had ever been configured (`planes.is_org_mode`),
and decided nothing at all for the org-only pages.

- A Solo account that connects its own cloud endpoint (`POST /personalize/cloud`, `org_backend_status` "validated")
  got Org behaviour: `POST /chat/new` accepted `plane=org`, the sidebar offered "+ Org chat", the chat offered an
  "Org wiki" scope, project chats ran on the endpoint and read the org wiki, agents could be created in the Org plane,
  and `POST /users/invite` and `POST /teams/{id}/invite` worked.
- Every org-only page was gated by `_require_admin` only. A Solo owner is the admin of his one-person organisation,
  so he passed. Solo was hidden from the navigation, not blocked.

The maintainer's rule: a Solo user gets no Org chat, no org agents and no user invites. He can create an organisation
(the one-way `POST /personalize/organization/setup`) and then has it. His own cloud endpoint is still used to run a
more capable model and to host his wiki, and that does not make the account an Org.

## Requirements

1. Org features key on topology. `plane_routing.is_org_account(cfg)` is true when the normalised topology is `org`
   (also with no settings row, the column default). `shares_org_model(cfg)` is `planes.is_org_mode(cfg)` and
   `is_org_account(cfg)`; `org_plane_ready(cfg)` is `is_org_account(cfg)` and `planes.org_available(cfg)`;
   `account_plane(plane, cfg)` turns a stored `org` plane into `solo` for a Solo account. `planes.is_org_mode` keeps
   its meaning (a backend was configured) wherever the code is about the backend.
2. `_require_org_topology` returns 404 for a Solo account; `_require_org_admin` stacks it before `_require_admin`.
   Every ORG-ONLY route below uses `_require_org_admin`. An Org account keeps today's behaviour: an anonymous caller
   is sent to `/login`, a member gets 403, an admin reaches the handler. A legacy `local` topology counts as Solo.
3. A Solo account, with or without its own validated endpoint:
   - `POST /chat/new` with `plane=org` creates a Solo chat; the sidebar has no "+ Org chat" and the chat has no "Org
     wiki" scope; a stored `org`-plane chat, task or agent runs as Solo.
   - `agents.html` offers no Organisation plane and `POST /agents/create` and `POST /chat/agent` create a Solo agent.
   - `POST /users/invite` is 404 and `POST /teams/{id}/invite` is refused with the existing `solo_no_members` redirect.
   - The org wiki scope is not reachable through the shared wiki routes (`scope=org` is 404 on read, export and
     import and 403 on write), snippets filed "to wiki" go to the personal wiki, and the skill form offers no
     Organisation scope.
4. A Solo account's own turns are unchanged. `plane_inference("solo", ...)` still runs on the endpoint when
   `solo_compute == "cloud"` (personal wiki, personal context, not ephemeral) and on the local model otherwise. A
   per-turn escalation ("use the cloud model", `escalate=True`) runs that turn on the endpoint but stays a Solo
   turn: the account's own wiki, personal context and no shared org cache (an Org account still escalates to the org
   plane); with no endpoint connected it says so instead of staying local. A project chat of a Solo
   account runs on its own cloud when `solo_compute == "cloud"` (the project wiki, personal context kept, `prefer_local`
   honoured) and on the local model otherwise; it no longer runs on the endpoint reading the org wiki.
5. Where a template showed an org-only control to a Solo account it is hidden or re-pointed: "Promote to org" on
   Memory, the "In an org, your Solo chats run on the org model" note on Settings, the Local model page's link to the
   org's Cloud and model page, and the "set up an organisation" links on the project pages (now `/personalize#org`,
   where the one-way flow lives). The "review queue" link after a flagged personal upload points at the personal queue.
6. `scheduler.py` includes legacy `local` rows in the Solo training sweep (a raw `== "solo"` compare skipped them).

## Route table

Every route of `anthill/web/app.py` (256; there are no other routers). Evidence is the template and line where a
Solo-visible page links to or posts to the route, or the reason there is none. SOLO-OK and SHARED routes are
unguarded. SHARED routes are reachable from both account types and keep their org branches behind the checks above.
ORG-ONLY routes are 404 for a Solo account (43 by `_require_org_admin`, 1 refused in its handler, 7 session-less
receivers that are not gated). UNDECIDED routes are left unguarded and listed for the maintainer.

Counts: SOLO-OK 64, SHARED 134, ORG-ONLY 51, UNDECIDED 7.

| Class | Routes | Evidence |
|---|---|---|
| SOLO-OK | `GET /sw.js`<br>`GET /push/key`<br>`POST /push/subscribe`<br>`POST /push/test`<br>`GET /notifications` | base.html:114,223,227,231; _sidebar.html:94 |
| SOLO-OK | `GET /setup`<br>`POST /setup`<br>`GET /setup/account-type`<br>`POST /setup/account-type`<br>`GET /setup/model`<br>`POST /setup/model`<br>`GET /local-model/status` | setup.html:47; setup_account_type.html:11; model_picker.html:61,70,94; dashboard.html:365 |
| SOLO-OK | `GET /login`<br>`POST /login`<br>`GET /logout`<br>`GET /forgot`<br>`POST /forgot`<br>`GET /reset/{token}`<br>`POST /reset/{token}`<br>`GET /verify/{token}`<br>`POST /verify/resend`<br>`GET /auth/google`<br>`GET /auth/microsoft` | login.html:19,26,49; _sidebar.html:256; verify_sent.html:16 |
| SOLO-OK | `GET /account`<br>`POST /account/name`<br>`POST /account/password`<br>`GET /profile`<br>`GET /profiles`<br>`POST /profiles`<br>`POST /profiles/{profile_id}/edit`<br>`POST /profiles/{profile_id}/delete` | _sidebar.html:73,74,250; profile.html:55,72 |
| SOLO-OK | `GET /`<br>`GET /onboarding/status`<br>`POST /onboarding/done`<br>`GET /walkthroughs/status`<br>`POST /walkthroughs/done` | _sidebar.html:93; tour.js:21; walkthrough.js:5,15 |
| SOLO-OK | `GET /personalize`<br>`POST /personalize`<br>`POST /personalize/organization/setup`<br>`POST /personalize/council`<br>`POST /personalize/escalation-consent-revoke`<br>`POST /personalize/cloud`<br>`POST /personalize/compute` | personalize.html:141-145,529; _inference_provider.html:279; _sidebar.html:215 |
| SOLO-OK | `GET /settings`<br>`POST /settings`<br>`POST /privacy-pack/install`<br>`GET /privacy-pack/status`<br>`POST /settings/chat-defaults`<br>`POST /settings/escalation/discover-models`<br>`POST /settings/training/test` | personalize.html:381; settings.html:37,58; chat.html:688; _inference_provider.html:110,319; training.html:261 |
| SOLO-OK | `GET /models`<br>`POST /models/benchmark`<br>`GET /models/benchmark-status`<br>`POST /models/pull`<br>`GET /models/pull-status`<br>`POST /models/delete` | personalize.html:578; _council_builder.html:235; models.html:75,111,145; docs/specs/settings-solo-home-consolidation.md |
| SOLO-OK | `GET /training`<br>`POST /training/config`<br>`GET /training/export`<br>`POST /training/run` | personalize.html:294,553; training.html:67,180,253 |
| SOLO-OK | `GET /docs/how-it-works`<br>`GET /docs/setup`<br>`GET /docs/okgf`<br>`GET /lite` | dashboard.html:313 |
| SHARED | `GET /audit`<br>`POST /settings/digest`<br>`GET /metrics`<br>`POST /metrics/estimates` | dashboard.html:288,312; metrics.html:139; audit.html:37; app.py _SOLO_SIDEBAR_PAGES |
| SHARED | `GET /teams`<br>`POST /teams`<br>`GET /teams/{team_id}`<br>`GET /teams/{team_id}/settings`<br>`POST /teams/{team_id}/rename`<br>`POST /teams/{team_id}/wiki-connect`<br>`POST /teams/{team_id}/delete`<br>`POST /teams/{team_id}/accept`<br>`POST /teams/{team_id}/members/{uid}/remove` | _sidebar.html:188,212; teams.html:35; team_detail.html:142; docs/specs/solo-project-framing.md |
| SHARED | `GET /integrations`<br>`GET /connectors/mcp`<br>`POST /mcp/servers`<br>`POST /mcp/servers/request`<br>`POST /mcp/servers/{sid}/test`<br>`GET /mcp/servers/{sid}/oauth/start`<br>`GET /connectors/mcp/oauth/callback`<br>`POST /mcp/servers/{sid}/approve`<br>`POST /mcp/servers/{sid}/reject`<br>`POST /mcp/servers/{sid}/disable`<br>`POST /mcp/servers/{sid}/remove`<br>`POST /mcp`<br>`POST /connectors/mcp/expose`<br>`POST /connectors/mcp/rotate-token`<br>`POST /connectors/mcp/consumers/add`<br>`POST /connectors/mcp/consumers/{cid}/approve`<br>`POST /connectors/mcp/consumers/{cid}/revoke` | personalize.html:415; mcp.html:141-146,181,245; integrations.html:34; docs/specs/mcp-integrations-tab-for-solo.md |
| SHARED | `GET /wiki`<br>`GET /wiki/review/personal`<br>`POST /wiki/review/{rid}/approve`<br>`POST /wiki/review/{rid}/keep-both`<br>`POST /wiki/review/{rid}/reject`<br>`GET /knowledge/suggestions`<br>`GET /wiki/export.okgf.tgz`<br>`POST /wiki/import.okgf`<br>`GET /wiki/team/{team_id}`<br>`GET /wiki/page/{slug}`<br>`GET /wiki/raw/{name}`<br>`POST /wiki/upload`<br>`POST /wiki/upload/confirm`<br>`GET /wiki/connector-files`<br>`POST /wiki/import-connector`<br>`POST /research`<br>`POST /research/batch`<br>`POST /wiki/principles` | _sidebar.html:211; _wiki_tabs.html:14; _knowledge_tabs.html:12; wiki.html:57,77,109,142,165,181 |
| SHARED | `GET /chat`<br>`GET /chat/history`<br>`GET /chat/{conv_id}`<br>`POST /chat/new`<br>`GET /chat/plane/status`<br>`POST /chat/{conv_id}/delete`<br>`POST /chat/{conv_id}/pin`<br>`POST /chat/{conv_id}/rename`<br>`POST /chat/{conv_id}/project`<br>`POST /chat/{conv_id}/attach`<br>`GET /chat/{conv_id}/thinking-support`<br>`GET /chat/{conv_id}/stream`<br>`POST /chat/{conv_id}/thumbs`<br>`POST /chat/download`<br>`POST /chat/schedule`<br>`POST /chat/suggest`<br>`POST /chat/agent`<br>`POST /chat/{conv_id}/escalate-confirm`<br>`POST /chat/{conv_id}/ask-provider-now` | _sidebar.html:99,118-125,133,151,181,190; chat.html:403,882,1214,1493,1507,1519,1639 |
| SHARED | `GET /memory`<br>`POST /memory/add`<br>`POST /memory/auto-toggle`<br>`POST /memory/{mid}/keep-personal`<br>`POST /memory/{mid}/edit`<br>`POST /memory/{mid}/delete`<br>`POST /memory/{mid}/promote/team`<br>`POST /memory/{mid}/wiki`<br>`POST /snippets/save`<br>`GET /snippets`<br>`POST /snippets/{snip_id}/wiki`<br>`POST /snippets/{snip_id}/delete`<br>`POST /snippets/{snip_id}/edit` | _knowledge_tabs.html:9,10; memory.html:36,53; chat.html:1925; snippets.html:20,55 |
| SHARED | `GET /files/{name}`<br>`GET /files/{name}/preview`<br>`POST /help/ask`<br>`POST /help/file` | chat.html:775,799; base.html:174,198 |
| SHARED | `GET /skills`<br>`POST /skills/draft`<br>`POST /skills/draft-from-chat`<br>`POST /skills/create`<br>`POST /skills/validate`<br>`POST /skills/check-trigger`<br>`POST /skills/proposed/{pid}/accept`<br>`POST /skills/proposed/{pid}/reject`<br>`POST /skills/autolearn-toggle`<br>`POST /skills/{slug}/delete`<br>`POST /skills/refine`<br>`GET /skills/{slug}/raw`<br>`GET /skills/{slug}/assets/{asset_path:path}`<br>`GET /skills/gallery`<br>`POST /skills/gallery/{slug}/adopt` | wiki.html:34; skills.html:18,29,49,228,288,350; app.py _SOLO_SIDEBAR_PAGES |
| SHARED | `GET /contribute`<br>`POST /contribute`<br>`POST /contribute/{pid}/triage`<br>`POST /contribute/{pid}/decide`<br>`POST /contribute/{pid}/mint` | _sidebar.html:259; base.html:201; chat.html:1525; contribute.html:104-135 |
| SHARED | `GET /tasks`<br>`POST /tasks/settings`<br>`POST /tasks/draft`<br>`POST /tasks/{task_id}/edit`<br>`POST /tasks/{task_id}/review-cadence`<br>`POST /tasks/{task_id}/review-result`<br>`POST /tasks/create`<br>`POST /tasks/{task_id}/cancel`<br>`POST /tasks/{task_id}/reactivate`<br>`POST /tasks/{task_id}/run-now`<br>`POST /tasks/{task_id}/queue`<br>`GET /tasks/{task_id}/result` | _sidebar.html:193; tasks.html:36,327 (is_solo wording); dashboard.html:164 |
| SHARED | `GET /agents`<br>`POST /agents/settings`<br>`POST /agents/create`<br>`GET /agents/{aid}`<br>`POST /agents/{aid}/edit`<br>`POST /agents/{aid}/run-now`<br>`POST /agents/{aid}/toggle`<br>`POST /agents/{aid}/delete`<br>`POST /agents/{aid}/approvals/{apid}/approve`<br>`POST /agents/{aid}/approvals/{apid}/reject` | _sidebar.html:194; agents.html:18,63 (is_solo wording); dashboard.html:171 |
| ORG-ONLY | `GET /users`<br>`POST /users/invite`<br>`POST /users/{uid}/role`<br>`POST /users/{uid}/deactivate`<br>`POST /users/{uid}/reset` | org_settings_hub.html:50; users.html:46; personalize.html:433 (org branch only); dashboard.html:282 (`not solo` branch) |
| ORG-ONLY | `GET /settings/org`<br>`GET /settings/general`<br>`GET /settings/automation`<br>`POST /settings/automation`<br>`GET /settings/slack`<br>`POST /settings/slack`<br>`GET /settings/discord`<br>`POST /settings/discord`<br>`GET /slack/oauth/start`<br>`GET /slack/oauth/callback`<br>`GET /settings/events`<br>`POST /settings/events`<br>`POST /settings/events/imap/test` | org_settings_hub.html:60-69; _org_tabs.html:5; settings_automation.html:13-35; settings_remote.html:77,100,156-159; personalize.html:442 (org branch only); no Solo page links to any of them |
| ORG-ONLY | `GET /settings/organization`<br>`POST /settings/organization`<br>`GET /settings/organization/wiki`<br>`POST /settings/organization/wiki`<br>`POST /settings/organization/cluster`<br>`POST /settings/organization/name`<br>`POST /settings/organization/aws`<br>`POST /settings/organization/connect`<br>`POST /settings/organization/discover-models`<br>`POST /settings/organization/provision-key`<br>`POST /settings/organization/hf-token`<br>`POST /settings/organization/provision`<br>`POST /settings/organization/teardown`<br>`POST /settings/organization/council/provision`<br>`POST /settings/organization/council/teardown`<br>`POST /settings/organization/council/review-toggle`<br>`POST /settings/aws/test`<br>`POST /settings/aws/provision-test` | settings_organization.html:26,163,243-273,584-632,735-769,823,913; personalize.html:438 (org branch only). Solo links to it (team_detail.html:126, team_settings.html:26, models.html:21) are re-pointed |
| ORG-ONLY | `GET /wiki/org`<br>`GET /agent-access`<br>`POST /agent-access/create`<br>`POST /agent-access/{aid}/scopes`<br>`POST /agent-access/{aid}/toggle`<br>`POST /agent-access/{aid}/mcp-token`<br>`POST /memory/{mid}/promote` | org_settings_hub.html:85; agent_access.html:46; memory.html:96 (button now Org only); no Solo page links to /wiki/org |
| ORG-ONLY | `POST /teams/{team_id}/invite` | team_detail.html:109-120 (Org only); refused in the handler with the existing solo_no_members redirect |
| ORG-ONLY | `POST /webhooks/{source}`<br>`POST /discord/interactions`<br>`POST /slack/events`<br>`POST /slack/command`<br>`GET /invite/{token}`<br>`POST /invite/{token}`<br>`POST /api/metrics` | no session (signature, token or mesh auth); configured only through the gated pages above, so inert for a Solo account; not gated |
| SHARED | `GET /settings/remote`<br>`POST /settings/remote`<br>`POST /settings/remote/stop`<br>`GET /settings/remote/status`<br>`POST /settings/signup`<br>`POST /settings/install-owner`<br>`GET /settings/appliance`<br>`GET /settings/appliance/launchagent.plist` | install-level (host) controls: who may join the whole install, who owns it, remote access and the Mac appliance. They decide things for the installation, not for one organisation, and only the install owner may use them, so a Solo install owner keeps them (kept at `_require_admin` plus the existing `is_install_owner` checks). The Solo interface does not link to them. |
| UNDECIDED | `GET /backend` | training.html:50 links it from a page a Solo reaches; org_settings_hub.html:64 |
| UNDECIDED | `GET /backup`<br>`GET /backup/export`<br>`POST /backup/restore` | no Solo page linked it (settings_general.html:28, org_settings_hub.html:65, _org_tabs.html:7), but it is the only UI to back up a person's own install; since #301 Settings, This device tab, links it for an admin |
| UNDECIDED | `GET /wiki/review` | org review queue; dashboard.html:294 links it for any admin with pending org drafts |
| UNDECIDED | `POST /settings/cloud/test`<br>`POST /models/refresh-catalog` | no caller found in templates or scripts |

Where the maintainer's earlier audit listed a route as org-only and a Solo page links to it, the rule "a Solo link
makes it SOLO-OK" won: `/models*` and `/training*` (reached from Settings and the model picker), `/settings` and
`/privacy-pack/*` (Settings), `/contribute/*` (the help menu) and `/tasks/settings`, `/agents/settings` (their pages
word them for a Solo account).

## Known limits

- The UNDECIDED routes stay open to a Solo owner: `/backend` (linked from the Training page), `/backup*` (no Solo
  page linked it, yet it is the only UI to back up a person's own install; Settings, This device tab, links it since #301), `GET /wiki/review` (the org queue, linked
  from the dashboard attention list), `POST /settings/cloud/test` and `POST /models/refresh-catalog` (no caller).
- The session-less receivers (`/webhooks/{source}`, `/discord/interactions`, `/slack/events`, `/slack/command`,
  `/invite/{token}`, `/api/metrics`) cannot be gated by session. They act only on settings that the gated pages
  write, so a Solo account that never configured them is unaffected; one that did before this change keeps them.
- `POST /webhooks/{source}` still resolves its plane with `plane_inference("org", ...)`, and the Integrations page
  (shared with Solo) still shows its "expose Anthill over MCP" card with org wording.
- `anthill/training/model_select.py` reads the topology with a raw `== "solo"` compare after `.strip().lower()`, so it
  misses a legacy `local` row. It is a protected path and is left as it is.
- A Solo account's old `org`-plane chats stay in the database as `org` and still show the org dot in the rail; they run as Solo.

## Acceptance

- A Solo admin, with or without a validated endpoint, gets 404 on every ORG-ONLY route; an Org admin reaches each
  of them as before, an Org member gets 403, an anonymous caller is sent to `/login`.
- A Solo account with a validated endpoint: `plane=org` gives a Solo chat, no "+ Org chat" or "Org wiki", no Organisation
  agent plane, `/users/invite` is 404, `/teams/{id}/invite` is refused; its Solo chat still resolves to its endpoint with
  the personal wiki and its personal wiki pages still open.
- An Org account with a backend is unchanged: Org chat, Org wiki scope, Organisation agents and invites all work.
- Tests: `tests/test_solo_org_separation.py` (model-free). `tests/test_invite_only_signup.py` now signs its owner up as
  an Org (`topology=org`), because its pages are org-only and the default sign-up is Solo.
