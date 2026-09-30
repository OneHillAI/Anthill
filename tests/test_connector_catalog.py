"""Connector catalog: data integrity, the doc-source filter, the pure registry-sync diff,
and the gallery route (renders tiles + stores catalog_id on add)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.connectors import catalog_by_id, doc_source_ids, load_catalog, validate_entry
from anthill.web import db
from anthill.web.db import MCPServer, Organization, User

# ── catalog data integrity ──────────────────────────────────────────────────────


def test_catalog_loads_and_every_entry_is_valid():
    entries = load_catalog()
    assert len(entries) >= 10, "expected a seeded catalog"
    for e in entries:
        assert validate_entry(e) == [], f"{e.get('id')}: {validate_entry(e)}"


def test_catalog_ids_unique():
    ids = [e["id"] for e in load_catalog()]
    assert len(ids) == len(set(ids))


def test_headline_connectors_present():
    ids = {e["id"] for e in load_catalog()}
    assert {"google-drive", "microsoft-365", "github"} <= ids


def test_doc_source_ids_are_the_file_providers():
    docs = doc_source_ids()
    assert "google-drive" in docs and "microsoft-365" in docs  # storage providers
    assert "sentry" not in docs  # not a document source


def test_catalog_by_id():
    assert catalog_by_id("github")["name"] == "GitHub"
    assert catalog_by_id("does-not-exist") is None
    assert catalog_by_id("") is None


def test_discord_connector_is_addable_via_mcp():
    # Engagement platforms: Slack was already present; Discord is now connectable too (community tier).
    d = catalog_by_id("discord")
    assert d is not None and validate_entry(d) == []
    assert d["category"] == "communication" and d["transport"] == "stdio"
    assert d["tier"] == "community"  # honest: a third-party server, not the official one
    env_targets = [f.get("env") for f in d["auth"]["fields"]]
    assert (
        "DISCORD_TOKEN" in env_targets
    )  # the bot token is injected as an env var, encrypted at rest
    # Community package is version-pinned so a later release can't silently change what runs with the token.
    assert "@" in d["command"].rsplit("discord-mcp", 1)[1]  # e.g. ...discord-mcp@2.0.0


def test_validate_entry_flags_problems():
    assert "missing id" in validate_entry({"name": "x"})
    assert "http entry has no endpoint" in validate_entry(
        {
            "id": "x",
            "name": "X",
            "category": "web",
            "summary": "s",
            "tier": "verified",
            "transport": "http",
        }
    )
    assert "bad tier 'gold'" in validate_entry(
        {
            "id": "x",
            "name": "X",
            "category": "web",
            "summary": "s",
            "tier": "gold",
            "transport": "stdio",
            "command": "c",
        }
    )


# ── registry-sync diff (pure core) ──────────────────────────────────────────────


def test_sync_diff_detects_drift_and_in_sync():
    import importlib.util
    from pathlib import Path

    spec_path = Path(__file__).resolve().parent.parent / "scripts" / "sync_catalog.py"
    spec = importlib.util.spec_from_file_location("sync_catalog", spec_path)
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    index = sync.build_index(
        [
            {"name": "ns/here", "remotes": [{"url": "https://new.example/mcp"}]},
            {"name": "ns/stable", "remotes": [{"url": "https://stable.example/mcp"}]},
        ]
    )
    local = [
        {
            "id": "moved",
            "upstream": "ns/here",
            "transport": "http",
            "endpoint": "https://old.example/mcp",
        },
        {
            "id": "stable",
            "upstream": "ns/stable",
            "transport": "http",
            "endpoint": "https://stable.example/mcp",
        },
        {"id": "gone", "upstream": "ns/missing", "transport": "stdio", "command": "x"},
        {"id": "custom", "transport": "stdio", "command": "x"},  # no upstream -> skipped
    ]
    changes = sync.diff_catalog(local, index)
    kinds = {(c["id"], c["kind"]) for c in changes}
    assert ("moved", "endpoint_changed") in kinds
    assert ("gone", "missing_upstream") in kinds
    assert all(c["id"] != "stable" for c in changes)  # in sync
    assert all(c["id"] != "custom" for c in changes)  # no upstream to reconcile


# ── gallery route ───────────────────────────────────────────────────────────────


def _admin_client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod, o.id


def test_connectors_page_renders_gallery(tmp_path):
    c, _app, _org = _admin_client(tmp_path)
    r = c.get("/connectors/mcp")
    assert r.status_code == 200
    body = r.text
    assert "Add a connector" in body
    assert "Google Drive" in body  # a catalog tile
    assert "pickConnector(" in body  # gallery wiring
    assert 'id="catalog-form"' in body  # the guided submit form


def test_add_from_catalog_stores_catalog_id(tmp_path):
    c, app_mod, org_id = _admin_client(tmp_path)
    r = c.post(
        "/mcp/servers",
        data={
            "name": "Filesystem",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-filesystem /tmp/docs",
            "catalog_id": "filesystem",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    row = s.query(MCPServer).filter(MCPServer.org_id == org_id).one()
    assert row.catalog_id == "filesystem"
    assert row.status == "pending"
