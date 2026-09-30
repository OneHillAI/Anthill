from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..mesh_auth import require_mesh
from ..wiki.lint import lint as wiki_lint
from ..wiki.workspace import Workspace
from .central_index import CentralIndex
from .registry import NodeRegistry

ORG_WIKI_PATH = Path(os.environ.get("ANTHILL_ORG_WIKI", "/data/org-wiki"))
CENTRAL_INDEX_PATH = Path(os.environ.get("ANTHILL_CENTRAL_INDEX", "/data/central-index"))

app = FastAPI(title="anthill orchestrator", version="0.1.2")
registry = NodeRegistry()
_org_wiki: Workspace | None = None
_central_index: CentralIndex | None = None


def _wiki() -> Workspace:
    global _org_wiki
    if _org_wiki is None:
        _org_wiki = Workspace(ORG_WIKI_PATH)
        if not _org_wiki.exists():
            _org_wiki.init()
    return _org_wiki


def _index() -> CentralIndex:
    global _central_index
    if _central_index is None:
        _central_index = CentralIndex(CENTRAL_INDEX_PATH)
    return _central_index


# ── health ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {
        "status": "ok",
        "nodes": len(registry.live_nodes()),
        "cached_answers": _index().count(),
    }


# ── node registry ─────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    node_id: str
    base_url: str
    model: str


@app.post("/nodes/register", status_code=201)
def register_node(req: RegisterRequest, _: None = Depends(require_mesh)):
    registry.register(req.node_id, req.base_url, req.model)
    return {"registered": req.node_id}


class HeartbeatRequest(BaseModel):
    node_id: str
    load: int = 0


@app.post("/nodes/heartbeat")
def heartbeat(req: HeartbeatRequest, _: None = Depends(require_mesh)):
    if not registry.heartbeat(req.node_id, load=req.load):
        raise HTTPException(status_code=404, detail="unknown node - register first")
    return {"ok": True}


@app.get("/nodes")
def list_nodes():
    return [
        {
            "node_id": n.node_id,
            "base_url": n.base_url,
            "model": n.model,
            "load": n.load,
            "alive": n.is_alive(),
        }
        for n in registry.all_nodes()
    ]


# ── inference router ──────────────────────────────────────────────────────────


class RouteRequest(BaseModel):
    model: str


@app.post("/route")
def route(req: RouteRequest, _: None = Depends(require_mesh)):
    """Return the least-loaded live node that serves `model`.

    The calling node uses this to forward prompts when it's overloaded or
    when it doesn't run the requested model locally.
    """
    node = registry.route(req.model)
    if node is None:
        raise HTTPException(
            status_code=503,
            detail=f"no live node available for model '{req.model}'",
        )
    return {"node_id": node.node_id, "base_url": node.base_url, "load": node.load}


# ── central semantic cache index (§7.2) ───────────────────────────────────────


class PublishRequest(BaseModel):
    node_id: str
    embedding: list[float]  # 1024-dim BGE-M3; prompt text is NEVER sent here
    answer: str


@app.post("/cache/publish", status_code=201)
def publish_to_index(req: PublishRequest, _: None = Depends(require_mesh)):
    if len(req.embedding) != 1024:
        raise HTTPException(status_code=422, detail="embedding must be 1024-dim")
    idx = _index()
    row_id = idx.publish(req.node_id, req.embedding, req.answer)
    return {"id": row_id}


class SearchRequest(BaseModel):
    embedding: list[float]
    threshold: float = 0.93


@app.post("/cache/search")
def search_index(req: SearchRequest, _: None = Depends(require_mesh)):
    if len(req.embedding) != 1024:
        raise HTTPException(status_code=422, detail="embedding must be 1024-dim")
    hits = _index().search(req.embedding, req.threshold)
    return {"hits": hits}


@app.get("/cache/stats")
def cache_stats():
    return {"total_entries": _index().count(), "nodes": len(registry.live_nodes())}


# ── org wiki ──────────────────────────────────────────────────────────────────


@app.get("/wiki/pages")
def list_pages():
    ws = _wiki()
    return [{"slug": p.stem, "url": f"/wiki/pages/{p.stem}"} for p in ws.pages()]


@app.get("/wiki/pages/{slug}")
def get_page(slug: str):
    ws = _wiki()
    path = ws.wiki / f"{slug}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"page '{slug}' not found")
    return {"slug": slug, "content": path.read_text()}


class PromoteRequest(BaseModel):
    slug: str
    content: str
    promoted_by: str
    reason: str = ""


@app.post("/wiki/promote", status_code=201)
def promote_page(req: PromoteRequest, _: None = Depends(require_mesh)):
    """Accept a promoted wiki page from a node agent (§7.4).

    Phase 3 policy: any registered node may promote. Ed25519-signed
    manifests and per-tenant policy rules are the next step.
    """
    ws = _wiki()
    live_ids = {n.node_id for n in registry.live_nodes()}
    if req.promoted_by not in live_ids:
        raise HTTPException(
            status_code=403,
            detail="promoting node is not registered or timed out",
        )
    path = ws.write_page(req.slug, req.content)
    ws.rebuild_index()
    ws.append_log("promote", req.slug)
    return {"slug": req.slug, "path": str(path.relative_to(ws.root))}


@app.get("/wiki/lint")
def lint_wiki():
    findings = wiki_lint(_wiki())
    return [{"kind": f.kind, "page": f.page, "detail": f.detail} for f in findings]
