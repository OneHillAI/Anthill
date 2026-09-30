# Spec: interaction service, Stage 1b (Discord inbound)

Status: proposed
Lane: `pillar:feature`
Implements: OneHillAI/ASDD PR #45 build brief (D2), Stage 1b (Discord). Builds on Stage 1
([interaction-service-stage1.md](interaction-service-stage1.md), Anthill PR #500).
Relates to: [`anthill/web/discord_bot.py`](../../anthill/web/discord_bot.py),
[`anthill/contribute/interaction.py`](../../anthill/contribute/interaction.py),
`anthill/web/app.py` (`_route_interaction`, `_org_plane_answer`, `/discord/interactions`).

## 1. Introduction

Stage 1 wired the members interaction service on Slack. This adds the second surface: Discord. A member
runs `/anthill <message>` in the org's members Discord and gets the same behaviour: a question is answered
from the org wiki, an idea/bug/request is routed into the contribution intake, and every reply discloses
it is automated.

The shipped Discord piece is an outbound connector (MCP); this adds inbound. Discord inbound is an
interactions webhook: Discord signs each request with Ed25519, and requires a response within 3 seconds.

## 2. Requirements

- **R1** WHEN a member runs `/anthill <message>` in the configured members guild, THE SYSTEM SHALL route
  it through the shared interaction logic (`_route_interaction`): an idea into the contribution intake, a
  question answered from the org wiki, disclosed on every reply. Same behaviour as Slack.
- **R2** THE SYSTEM SHALL verify Discord's Ed25519 request signature with the app's public key and reject
  (401) any request that fails, before trusting the payload. It SHALL answer the PING handshake with a
  PONG.
- **R3** THE trust boundary SHALL be the configured members guild: a command from any other guild is
  refused. (Stage 1 is members-only; per-Discord-user identity is a later refinement.)
- **R4** THE SYSTEM SHALL ack within Discord's 3s window with a DEFERRED response and produce the reply
  from a background thread that edits it, since answering or drafting a spec calls the model.
- **R5** No secret is stored: the public key verifies the signature and the per-request interaction token
  authorizes the reply edit.

## 3. Design

- `_slack_answer` is refactored to share two helpers with Discord: `_org_plane_answer` (the org-plane wiki
  answer) and `_route_interaction` (classify, intake-or-answer, disclose). Slack keeps its
  thread-conversation memory in a small answer closure; the routing and answering are now common.
- `anthill/web/discord_bot.py`: `verify_signature` (Ed25519 via `cryptography`, fail-closed),
  `command_text`, and `edit_response` (PATCH the deferred `@original` message). No new dependency.
- `DiscordApp` (a dedicated self-creating table, no migration, nothing secret): `application_id`,
  `public_key`, `guild_id`, `enabled`.
- `POST /discord/interactions`: verify the signature, answer PING with PONG, and for a command in the
  configured guild, ack DEFERRED and `_spawn(_discord_respond, ...)`. The worker resolves a representative
  org member (an admin) for the org-plane context and attribution, runs `_route_interaction`, and edits
  the reply.

## 4. Tasks

- [ ] `anthill/web/db.py`: `DiscordApp` model (R5).
- [ ] `anthill/web/discord_bot.py`: signature verify + response helpers (R2, R4).
- [ ] `anthill/web/app.py`: `_org_plane_answer` + `_route_interaction` (shared); `/discord/interactions`
  + `_discord_respond` (R1-R4); refactor `_slack_answer` onto the shared helpers.
- [ ] `tests/test_discord_bot.py`: signature (valid/invalid), PING, idea-to-intake, question, wrong guild.
- [ ] `CHANGELOG.md`: the user-facing entry.

## 5. Out of scope

- **Stage 2: public / anonymous surfaces.** Anti-bot gated and execution-free per the B5 rule; waits on
  the framework's execution-free recipe variant and anti-bot contract.
- **Per-Discord-user identity.** Stage 1b uses the guild as the trust boundary and a representative member
  for context; mapping a Discord user to a specific member (and richer attribution) is a later refinement.
- **The admin UI to configure the Discord app.** This adds the inbound endpoint and model; a settings
  page to enter the application id / public key / guild is a follow-up (the connector settings surface).
