"""The connected-document layer behind wiki import + the file pickers: tool resolution
(catalog mapping and the live-tool heuristic), tolerant result parsing, and the
approved-doc-source filter. The MCP runtime is faked so no server is needed."""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db, docsource
from anthill.web.db import MCPServer, Organization


def _server(catalog_id="", transport="stdio"):
    return SimpleNamespace(
        catalog_id=catalog_id, headers_enc="", transport=transport, url="", command="x"
    )


# ── parsing is tolerant of JSON and plain text ──────────────────────────────────


def test_parse_listing_json_strings_objects_and_text():
    assert docsource._parse_listing('["a.md", "b.md"]') == [
        {"label": "a.md", "ref": "a.md"},
        {"label": "b.md", "ref": "b.md"},
    ]
    objs = docsource._parse_listing('[{"name": "Plan", "id": "42"}, {"path": "/x/y.txt"}]')
    assert objs == [{"label": "Plan", "ref": "42"}, {"label": "/x/y.txt", "ref": "/x/y.txt"}]
    lines = docsource._parse_listing("- one\n- two\n\nthree")
    assert lines == [
        {"label": "one", "ref": "one"},
        {"label": "two", "ref": "two"},
        {"label": "three", "ref": "three"},
    ]
    assert docsource._parse_listing("") == []


# ── tool resolution: catalog mapping wins, else a heuristic over live tool names ──


def test_resolve_tool_uses_catalog_mapping(monkeypatch):
    monkeypatch.setattr(
        docsource,
        "catalog_by_id",
        lambda cid: {"files": {"read": {"tool": "read_file", "arg": "path"}}} if cid else None,
    )
    assert docsource._resolve_tool(_server("filesystem"), "read") == ("read_file", "path")


def test_resolve_tool_heuristic_from_live_tools(monkeypatch):
    monkeypatch.setattr(docsource, "catalog_by_id", lambda cid: {})  # no mapping
    monkeypatch.setattr(
        docsource,
        "list_tools_raw",
        lambda s, h: [
            {"name": "search-pages", "inputSchema": {"properties": {"query": {}}}},
            {"name": "get-page", "inputSchema": {"properties": {"page_id": {}}}},
        ],
    )
    assert docsource._resolve_tool(_server("notion"), "search") == ("search-pages", "query")
    assert docsource._resolve_tool(_server("notion"), "read") == ("get-page", "page_id")


def test_resolve_tool_none_when_no_match(monkeypatch):
    monkeypatch.setattr(docsource, "catalog_by_id", lambda cid: {})
    monkeypatch.setattr(docsource, "list_tools_raw", lambda s, h: [{"name": "do_thing"}])
    assert docsource._resolve_tool(_server("x"), "read") == (None, "")


# ── list / read go through the resolved tool and stay tolerant ──────────────────


def test_list_documents_calls_resolved_tool(monkeypatch):
    monkeypatch.setattr(docsource, "mcp_available", lambda: True)
    monkeypatch.setattr(docsource, "catalog_by_id", lambda cid: {})
    monkeypatch.setattr(
        docsource,
        "list_tools_raw",
        lambda s, h: [{"name": "search", "inputSchema": {"properties": {"q": {}}}}],
    )
    seen = {}

    def fake_call(server, headers, tool, args):
        seen["tool"], seen["args"] = tool, args
        return '["doc1", "doc2"]'

    monkeypatch.setattr(docsource, "call_tool_raw", fake_call)
    docs = docsource.list_documents(_server("notion"), query="roadmap")
    assert seen == {"tool": "search", "args": {"q": "roadmap"}}
    assert docs == [{"label": "doc1", "ref": "doc1"}, {"label": "doc2", "ref": "doc2"}]


def test_list_documents_empty_when_unavailable_or_no_tool(monkeypatch):
    monkeypatch.setattr(docsource, "mcp_available", lambda: False)
    assert docsource.list_documents(_server("notion"), "q") == []
    monkeypatch.setattr(docsource, "mcp_available", lambda: True)
    monkeypatch.setattr(docsource, "catalog_by_id", lambda cid: {})
    monkeypatch.setattr(docsource, "list_tools_raw", lambda s, h: [{"name": "noop"}])
    assert docsource.list_documents(_server("notion"), "q") == []


def test_read_document(monkeypatch):
    monkeypatch.setattr(docsource, "mcp_available", lambda: True)
    monkeypatch.setattr(
        docsource,
        "catalog_by_id",
        lambda cid: {"files": {"read": {"tool": "read_file", "arg": "path"}}},
    )
    monkeypatch.setattr(docsource, "call_tool_raw", lambda s, h, t, a: f"BODY:{t}:{a['path']}")
    assert docsource.read_document(_server("filesystem"), "/x.md") == "BODY:read_file:/x.md"
    assert docsource.read_document(_server("filesystem"), "") == ""  # no ref


# ── only approved document-source connectors are offered ────────────────────────


def test_doc_source_servers_filters_to_approved_doc_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(docsource, "doc_source_ids", lambda: {"filesystem", "google-drive"})
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [
            MCPServer(org_id=o.id, name="Files", catalog_id="filesystem", status="approved"),
            MCPServer(org_id=o.id, name="Drive", catalog_id="google-drive", status="approved"),
            MCPServer(
                org_id=o.id, name="GH", catalog_id="github", status="approved"
            ),  # not a doc source
            MCPServer(org_id=o.id, name="Pending", catalog_id="filesystem", status="pending"),
        ]
    )
    s.commit()
    names = sorted(x.name for x in docsource.doc_source_servers(s, o.id))
    assert names == ["Drive", "Files"]
