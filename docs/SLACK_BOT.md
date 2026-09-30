# Slack bot: ask Anthill from Slack

Let your team ask your organization's Anthill from Slack. They **@mention** the bot in a channel, **DM**
it, or run **`/anthill <question>`**, and it answers from your org wiki, in-thread, on your org model.
Personal context is never used (org plane), and only people who are **members of your org on Anthill**
(matched by their Slack email) get answers - everyone else is told to ask an admin for an invite.

This is the inbound/conversational surface. It is separate from the outbound Slack *connector* (which
lets an agent read and post to Slack as a tool).

## Before you start

- Your Anthill backend must be reachable from Slack over **HTTPS**. On a server/VPC that is its public
  URL. Testing from a laptop or Mac mini: put a tunnel in front of it, e.g.
  `cloudflared tunnel --url http://localhost:<port>` or `ngrok http <port>`, and use the tunnel URL
  below. (Find the port in the desktop app logs, or set `ANTHILL_PORT`.)
- Your org's model must be connected (Settings -> Cloud & model). The bot answers on the **org** model.

## 1. Create the Slack app

Go to <https://api.slack.com/apps> -> **Create New App** -> **From an app manifest**, pick your
workspace, and paste this (replace `https://YOUR-ANTHILL` with your backend URL):

```yaml
display_information:
  name: Anthill
features:
  bot_user:
    display_name: Anthill
    always_online: true
  slash_commands:
    - command: /anthill
      url: https://YOUR-ANTHILL/slack/command
      description: Ask your organization's Anthill
      usage_hint: "[question]"
oauth_config:
  redirect_urls:
    - https://YOUR-ANTHILL/slack/oauth/callback
  scopes:
    bot:
      - app_mentions:read
      - chat:write
      - commands
      - users:read
      - users:read.email
      - im:history
      - im:read
settings:
  event_subscriptions:
    request_url: https://YOUR-ANTHILL/slack/events
    bot_events:
      - app_mention
      - message.im
```

`users:read.email` is what lets Anthill match a Slack user to their Anthill member account. `im:history`
+ `message.im` enable DMs to the bot.

## 2. Connect it in Anthill (one-click install - recommended)

In Anthill: **Settings -> Slack bot**. From the Slack app's **Basic Information -> App Credentials**,
copy the **Client ID**, **Client Secret**, and **Signing Secret** into the "Install with one click"
form and **Save**. Then click **Add to Slack**: you approve the install on Slack's screen and Anthill
fetches the bot token for you (no copy-paste) and turns the bot on. The page also shows the OAuth
Redirect URL - make sure it is in the app's **OAuth & Permissions -> Redirect URLs** (the manifest above
adds it).

Prefer to do it by hand? Expand **"Or connect by pasting a bot token manually"**: install the app to
your workspace yourself (OAuth & Permissions), then paste the **Bot User OAuth Token** (`xoxb-...`) and
the **Signing Secret**, and tick **Enable**.

Either way, the two request URLs on the page (`/slack/events` and `/slack/command`) must match what is in
the manifest.

When you set the Event Subscriptions Request URL in Slack, Slack sends a one-time verification request;
Anthill answers the handshake automatically, so it should show **Verified** immediately (as long as the
backend is reachable and the bot is enabled with its signing secret saved).

## 3. Use it

- In a channel the app is in: `@Anthill what is our refund policy?` -> it replies in a thread.
- Direct message the bot the same way.
- Anywhere: `/anthill what is our refund policy?` -> it posts the answer in that channel.

## Notes & limits

- **Membership = authorization.** A Slack user who is not an active member of the org (by email) gets a
  polite "ask an admin to invite you" instead of an answer. Invite them in **Settings -> Users** (any
  email domain is allowed).
- **Privacy.** Slack questions run on the org plane: grounded in the org (and the member's team) wikis,
  never personal memory or the personal wiki.
- **Threaded follow-ups have context.** Each Slack thread maps to one conversation, so a reply in a
  thread remembers the earlier turns (a channel mention is answered in its thread; a DM is one ongoing
  conversation). A slash command is a one-shot with no thread. The context is kept inside Anthill (no
  extra Slack scopes; nothing is re-fetched from Slack).
- **Secrets** (bot token, signing secret) are stored encrypted at rest (AES-GCM), like every other
  Anthill secret.
