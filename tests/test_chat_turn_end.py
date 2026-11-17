"""A chat turn ends when its answer ends. Every few exchanges Anthill extracts durable memory from the
chat, which is a full model call. It used to run before the stream's [DONE] event, so the page stayed
busy (and the whole server blocked) for a call the answer did not need. It now runs after [DONE] has
been sent, off the event loop. Model-free: the route is driven at the ASGI boundary, so the test sees
what the client would see and in which order."""

import asyncio
import threading
from urllib.parse import urlencode

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.memory as mem_mod
import anthill.web.app as app_mod
import anthill.wiki.ask as wiki_ask
from anthill.web import db as db_mod
from anthill.web.crypto import make_token
from anthill.web.db import (
    ChatMessage,
    Conversation,
    MemoryItem,
    Organization,
    OrgSettings,
    User,
)


def _world(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="a@a.com", role="admin", active=True)
    s.add_all([user, OrgSettings(org_id=org.id, deployment_topology="solo")])
    s.flush()
    conv = Conversation(user_id=user.id, org_id=org.id, plane="solo", title="t")
    s.add(conv)
    s.flush()
    # Enough earlier messages that this turn is one on which memory is extracted.
    for i in range(6):
        s.add(
            ChatMessage(
                conversation_id=conv.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"earlier message {i}",
            )
        )
    s.commit()
    ids = (user.id, org.id, conv.id)
    s.close()
    return ids


async def _run_turn(token: str, conv_id: int, on_done_sent) -> None:
    """Call the chat route as a server would and report the moment the [DONE] event is sent."""
    finished = asyncio.Event()
    requests = 0

    async def receive():
        nonlocal requests
        requests += 1
        if requests == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and b"[DONE]" in message.get("body", b""):
            on_done_sent()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/chat/{conv_id}/stream",
        "raw_path": f"/chat/{conv_id}/stream".encode(),
        "query_string": urlencode({"message": "what should I cook tonight"}).encode(),
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"session_token={token}".encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    try:
        await app_mod.app(scope, receive, send)
    finally:
        finished.set()


def test_memory_is_extracted_after_done_is_sent_and_off_the_event_loop(tmp_path, monkeypatch):
    uid, org_id, conv_id = _world(tmp_path, monkeypatch)

    def _fake_ask_stream(ws, message, backend, **kw):
        yield "Pasta."

    monkeypatch.setattr(wiki_ask, "ask_stream", _fake_ask_stream)

    seen: dict = {"extract_started": False, "extract_on_loop": None}

    def _on_event_loop_thread() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _fake_extract(text, backend, **k):
        seen["extract_started"] = True
        seen["extract_on_loop"] = _on_event_loop_thread()
        seen["extract_thread"] = threading.current_thread().name
        return ["The user likes pasta"]

    monkeypatch.setattr(mem_mod, "extract", _fake_extract)
    monkeypatch.setattr(mem_mod, "embed_text", lambda text: None)

    def _done_sent():
        seen["started_when_done_sent"] = seen["extract_started"]

    asyncio.run(_run_turn(make_token(uid, org_id, "admin"), conv_id, _done_sent))

    assert "started_when_done_sent" in seen, "the stream never sent [DONE]"
    assert seen["started_when_done_sent"] is False, "memory extraction ran before [DONE] was sent"
    # It still happens, and not on the thread that serves every other request.
    assert seen["extract_started"] is True
    assert seen["extract_on_loop"] is False
    s = app_mod._SessionFactory()
    assert [m.text for m in s.query(MemoryItem).all()] == ["The user likes pasta"]
    s.close()
