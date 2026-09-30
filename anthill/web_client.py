"""A thin HTTP client for talking to a running Anthill server - the org plane from the terminal.

The desktop/web app keeps the org's shared model + wiki behind a FastAPI server. This client lets
`anthill chat --org` reach it: log in with form credentials (capturing the session cookie), open an
org-plane conversation, and stream answers from the server's SSE chat endpoint. The server owns the
conversation history, so the client stays stateless beyond the session cookie + the conversation id.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import httpx


class OrgClientError(RuntimeError):
    """A problem talking to the Anthill server (login, conversation, or stream)."""


class OrgClient:
    """Chat against a running Anthill server over its HTTP API."""

    def __init__(self, base_url: str, *, timeout: float = 120.0, transport=None) -> None:
        self.base = base_url.rstrip("/")
        # follow_redirects=False: login and /chat/new both answer with a 302 we read directly (the
        # cookie / Location) rather than chase. transport is injectable so tests can mock the server.
        self._client = httpx.Client(
            base_url=self.base, timeout=timeout, follow_redirects=False, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OrgClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def login(self, email: str, password: str) -> None:
        """Exchange credentials for a session cookie. Raises OrgClientError on bad credentials."""
        try:
            r = self._client.post("/login", data={"email": email, "password": password})
        except httpx.HTTPError as e:
            raise OrgClientError(f"can't reach {self.base}: {e}") from e
        # Success = a 302 that sets the session cookie; a bad login re-renders the page with 401.
        if r.status_code != 302 or not self._client.cookies.get("session_token"):
            raise OrgClientError("login failed - check your email and password")

    def new_conversation(self, *, plane: str = "org") -> int:
        """Open a conversation on the given plane; return its id. The server downgrades an org
        conversation to solo if no org backend is connected, so confirm the plane separately if needed."""
        try:
            r = self._client.post("/chat/new", data={"plane": plane})
        except httpx.HTTPError as e:
            raise OrgClientError(f"can't reach {self.base}: {e}") from e
        if r.status_code == 401:
            raise OrgClientError("not logged in")
        m = re.search(r"/chat/(\d+)", r.headers.get("location", ""))
        if not m:
            raise OrgClientError("the server did not start a conversation")
        return int(m.group(1))

    def stream(
        self, conv_id: int, message: str, *, web: bool = False, escalate_org: bool = False
    ) -> Iterator[tuple[str, object]]:
        """Stream one turn from the server's SSE endpoint. Yields ``('token', str)`` for answer chunks,
        ``('slugs', list)`` for the grounding pages, and ``('error', str)`` for a server-side error.
        Stops at the ``[DONE]`` sentinel. ``escalate_org`` mirrors the server's own per-turn "redo with
        the connected org/cloud/provider backend" parameter (#278) - lets a script re-answer a turn on
        the stronger backend without going through Chat's natural-language redo phrase."""
        params = {
            "message": message,
            "web": "true" if web else "false",
            "escalate_org": "true" if escalate_org else "false",
        }
        try:
            with self._client.stream("GET", f"/chat/{conv_id}/stream", params=params) as resp:
                if resp.status_code != 200:
                    raise OrgClientError(f"server returned {resp.status_code}")
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "token" in evt:
                        yield ("token", evt["token"])
                    elif "error" in evt:
                        yield ("error", evt["error"])
                    elif isinstance(evt.get("meta"), dict) and "wiki_slugs" in evt["meta"]:
                        yield ("slugs", evt["meta"]["wiki_slugs"])
        except httpx.HTTPError as e:
            raise OrgClientError(f"stream failed: {e}") from e
