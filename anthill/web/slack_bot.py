"""Inbound Slack bot: your team @mentions Anthill (or uses /anthill, or DMs it) and gets an answer
grounded in the org wiki, posted back in-thread.

This module is the pure Slack side: request-signature verification (reused from ingest_push), the
minimal Slack Web API calls (auth.test / users.info / chat.postMessage), and message parsing. The
answer orchestration lives in app.py (it needs the plane router + ask()), and runs in a daemon thread
so the webhook can ack Slack in under 3s. httpx only (already a dependency); no Slack SDK.

Separate from the OUTBOUND Slack MCP connector (which lets the agent read/post as a tool). This is the
conversational surface: people talking TO Anthill from Slack.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import httpx

from .ingest_push import verify_slack  # reuse the v0 request-signature + replay check

SLACK_API = "https://slack.com/api"
_MENTION = re.compile(r"<@[A-Z0-9]+>")

# Bot scopes the app needs, used both in the OAuth install URL and documented for the manual manifest.
BOT_SCOPES = [
    "app_mentions:read",
    "chat:write",
    "commands",
    "users:read",
    "users:read.email",
    "im:history",
    "im:read",
]


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The Slack "Add to Slack" v2 authorize URL. The admin is sent here; Slack sends them back to
    redirect_uri with a code we exchange for the bot token."""
    q = urlencode(
        {
            "client_id": client_id,
            "scope": ",".join(BOT_SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://slack.com/oauth/v2/authorize?{q}"


def oauth_access(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """Exchange an OAuth code for the workspace install: returns Slack's oauth.v2.access JSON
    ({'ok', 'access_token' (the xoxb bot token), 'bot_user_id', 'team': {'id'}, ...}). Never raises."""
    try:
        r = httpx.post(
            f"{SLACK_API}/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def auth_test(bot_token: str) -> dict:
    """Validate a bot token and learn the bot's own identity: returns Slack's auth.test JSON
    ({'ok', 'user_id', 'team_id', 'team', ...}). Used on connect to store bot_user_id + team_id and to
    tell the admin the token works. Never raises."""
    try:
        r = httpx.post(
            f"{SLACK_API}/auth.test",
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=10,
        )
        return r.json()
    except Exception as e:  # network / bad json -> report not-ok, never crash the request
        return {"ok": False, "error": str(e)}


def user_email(bot_token: str, slack_user_id: str) -> str:
    """The Slack user's profile email (needs the users:read.email scope), or "" if unavailable. This is
    how a Slack user is mapped to an Anthill member - so only real org members get answers."""
    try:
        r = httpx.get(
            f"{SLACK_API}/users.info",
            headers={"Authorization": f"Bearer {bot_token}"},
            params={"user": slack_user_id},
            timeout=10,
        )
        d = r.json()
        if not d.get("ok"):
            return ""
        return (((d.get("user") or {}).get("profile")) or {}).get("email", "") or ""
    except Exception:
        return ""


def post_message(bot_token: str, channel: str, text: str, thread_ts: str = "") -> None:
    """Post a reply as the bot, optionally threaded under the triggering message. Best-effort."""
    payload: dict = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        httpx.post(
            f"{SLACK_API}/chat.postMessage",
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            content=json.dumps(payload),
            timeout=15,
        )
    except Exception:
        pass


def respond_url(response_url: str, text: str, *, in_channel: bool = True) -> None:
    """Post a slash-command reply via its response_url (no bot token needed). in_channel makes it
    visible to the whole channel rather than only the invoker. Best-effort."""
    try:
        httpx.post(
            response_url,
            json={"response_type": "in_channel" if in_channel else "ephemeral", "text": text},
            timeout=15,
        )
    except Exception:
        pass


def strip_mention(text: str) -> str:
    """The question with any <@bot> mention removed and whitespace tidied."""
    return _MENTION.sub("", text or "").strip()


def question_from_event(event: dict, bot_user_id: str) -> str | None:
    """The user's question for an app_mention or a direct-message event, or None to ignore.

    Ignores the bot's own posts and any bot/system message (bot_id / subtype), so the bot never talks
    to itself or reacts to joins/edits. Handles two trigger types: an app_mention in a channel, and a
    plain message in a DM (channel_type == 'im')."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    if event.get("bot_id") or event.get("subtype"):
        return None
    if bot_user_id and event.get("user") == bot_user_id:
        return None
    if etype == "app_mention":
        return strip_mention(event.get("text") or "") or None
    if etype == "message" and event.get("channel_type") == "im":
        return (event.get("text") or "").strip() or None
    return None


__all__ = [
    "auth_test",
    "post_message",
    "question_from_event",
    "respond_url",
    "strip_mention",
    "user_email",
    "verify_slack",
]
