"""Inbound Discord bot: a member runs `/anthill <message>` in the org's members Discord and gets an
answer grounded in the org wiki, or an idea routed into the contribution intake. The conversational
surface, distinct from the OUTBOUND Discord MCP connector (which lets an agent read/post as a tool).

Discord signs every interaction request with Ed25519; we verify it with the app's public key before
trusting anything. We must respond within 3 seconds, so we ack with a DEFERRED response and then edit it
with the real reply once the model has run. No bot token is needed: the per-request interaction token
authorizes editing the response.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"

# Discord interaction request types.
TYPE_PING = 1
TYPE_APPLICATION_COMMAND = 2
# Discord interaction response types.
RESP_PONG = 1
RESP_DEFERRED_MESSAGE = 5


def verify_signature(public_key_hex: str, timestamp: str, body: bytes, signature_hex: str) -> bool:
    """Verify Discord's Ed25519 signature over (timestamp + body). Fails CLOSED: any error is False."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pk.verify(bytes.fromhex(signature_hex), (timestamp or "").encode() + (body or b""))
        return True
    except Exception:
        return False


def command_text(interaction: dict) -> str:
    """The free-text argument of a `/anthill <message>` slash command (first non-empty option value)."""
    for opt in (interaction.get("data") or {}).get("options") or []:
        val = opt.get("value")
        if val:
            return str(val)
    return ""


def edit_response(application_id: str, token: str, content: str) -> None:
    """Replace the deferred interaction response with the final reply. Never raises."""
    url = f"{API}/webhooks/{application_id}/{token}/messages/@original"
    data = json.dumps({"content": (content or "")[:1900]}).encode()
    req = urllib.request.Request(
        url, data=data, method="PATCH", headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


def register_command(application_id: str, guild_id: str, bot_token: str) -> tuple[bool, str]:
    """Register the `/anthill` guild slash command with Discord, one time. Uses the bot token only for
    this call; it is NOT stored. Returns (ok, message)."""
    url = f"{API}/applications/{application_id}/guilds/{guild_id}/commands"
    cmd = {
        "name": "anthill",
        "type": 1,
        "description": "Ask your organization's assistant, or drop an idea or bug",
        "options": [
            {"name": "message", "type": 3, "description": "your question or idea", "required": True}
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(cmd).encode(),
        method="POST",
        headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True, "registered"
    except urllib.error.HTTPError as e:
        return False, f"discord returned {e.code}"
    except Exception as e:
        return False, str(e)[:80]
