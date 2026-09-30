from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..cache.store import CacheStore
from ..config import Config
from ..inference.base import BackendError, build_backend
from ..mesh_auth import mesh_headers, require_mesh
from ..wiki.ask import ask
from ..wiki.workspace import Workspace

# Configured via environment, same as the CLI.
WORKSPACE_PATH = Path(os.environ.get("ANTHILL_WORKSPACE", "workspace"))
NODE_ID = os.environ.get("ANTHILL_NODE_ID", "local")
ORG_URL = os.environ.get("ANTHILL_ORG_URL", "")
HEARTBEAT_INTERVAL = int(os.environ.get("ANTHILL_HEARTBEAT_INTERVAL", "5"))

app = FastAPI(title="anthill node agent", version="0.1.2")
_cache_store: CacheStore | None = None
_load: int = 0
_load_lock = threading.Lock()


def _store() -> CacheStore:
    global _cache_store
    if _cache_store is None:
        db_path = WORKSPACE_PATH / ".cache"
        db_path.mkdir(parents=True, exist_ok=True)
        _cache_store = CacheStore(db_path)
    return _cache_store


def _workspace() -> Workspace:
    ws = Workspace(WORKSPACE_PATH)
    if not ws.exists():
        ws.init()
    return ws


# ── health ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"node_id": NODE_ID, "load": _load, "status": "ok"}


# ── peer cache fetch ──────────────────────────────────────────────────────────


_CACHE_ID = re.compile(r"^[0-9a-f]{1,64}$")


@app.get("/cache/fetch/{entry_id}")
def fetch_cached(entry_id: str, _: None = Depends(require_mesh)):
    # Cache ids are sha256-hex prefixes (cache/store.py). Reject anything else so entry_id can't break
    # out of the LanceDB filter string - e.g. "x' OR '1'='1" would otherwise match/leak every row.
    if not _CACHE_ID.match(entry_id):
        raise HTTPException(status_code=404, detail="entry not found on this node")
    store = _store()
    try:
        results = store._table.search(None).where(f"id = '{entry_id}'").to_list()
    except Exception:
        results = []
    if not results:
        raise HTTPException(status_code=404, detail="entry not found on this node")
    row = results[0]
    return {"id": row["id"], "answer": row["answer"]}


# ── OpenAI-compatible chat completions ────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float = 0.2


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    """OpenAI-compatible endpoint.

    The last user message is treated as the question. Wiki context and the
    semantic cache (local + org) are applied transparently before any
    inference call. Drop-in for Cursor, Continue, or any OpenAI-SDK client
    pointing at http://localhost:{port}.
    """
    global _load
    user_messages = [m for m in req.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=422, detail="no user message in request")
    question = user_messages[-1].content

    with _load_lock:
        _load += 1
    try:
        cfg = Config.from_env()
        if req.model:
            cfg.model = req.model
        backend = build_backend(cfg)
        answer, slugs, cache_hit = ask(_workspace(), question, backend, org_url=ORG_URL)
    except BackendError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        with _load_lock:
            _load = max(0, _load - 1)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "model": req.model or os.environ.get("ANTHILL_MODEL", "qwen2.5:3b"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
        "anthill": {"cache_hit": cache_hit, "wiki_slugs": slugs, "node_id": NODE_ID},
    }


@app.get("/v1/models")
def list_models():
    model = os.environ.get("ANTHILL_MODEL", "qwen2.5:3b")
    return {
        "object": "list",
        "data": [{"id": model, "object": "model", "owned_by": "anthill"}],
    }


# ── heartbeat loop ────────────────────────────────────────────────────────────


def _heartbeat_loop(org_url: str, node_id: str, base_url: str, model: str) -> None:
    with httpx.Client(timeout=10) as client:
        for attempt in range(10):
            try:
                client.post(
                    f"{org_url}/nodes/register",
                    json={
                        "node_id": node_id,
                        "base_url": base_url,
                        "model": model,
                    },
                    headers=mesh_headers(),
                )
                break
            except httpx.HTTPError:
                time.sleep(min(2**attempt, 30))

        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            try:
                with _load_lock:
                    current_load = _load
                client.post(
                    f"{org_url}/nodes/heartbeat",
                    json={
                        "node_id": node_id,
                        "load": current_load,
                    },
                    headers=mesh_headers(),
                )
            except httpx.HTTPError:
                pass


@app.on_event("startup")
def start_heartbeat() -> None:
    if not ORG_URL:
        return
    base_url = os.environ.get("ANTHILL_NODE_BASE_URL", "http://localhost:9001")
    model = os.environ.get("ANTHILL_MODEL", "qwen2.5:3b")
    t = threading.Thread(
        target=_heartbeat_loop,
        args=(ORG_URL, NODE_ID, base_url, model),
        daemon=True,
    )
    t.start()
