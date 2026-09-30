"""Optional Meilisearch retrieval: gating, parsing, indexing, fallback (no network)."""

from anthill.search import meili


class _FakeResp:
    def __init__(self, payload=None):
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _clear(monkeypatch):
    for var in ("MEILI_URL", "MEILI_API_KEY", "MEILI_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_disabled_without_url(monkeypatch):
    _clear(monkeypatch)
    assert meili.enabled() is False
    assert meili.search_slugs("anything") == []


def test_enabled_with_url(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MEILI_URL", "http://localhost:7700")
    assert meili.enabled() is True


def test_search_slugs_parses_hit_ids(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MEILI_URL", "http://localhost:7700/")
    monkeypatch.setenv("MEILI_API_KEY", "secret")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["auth"] = headers.get("Authorization")
        captured["body"] = json
        return _FakeResp(
            {
                "hits": [
                    {"id": "vacation-policy", "title": "Vacation policy"},
                    {"id": "remote-work", "title": "Remote work"},
                ]
            }
        )

    monkeypatch.setattr(meili.httpx, "post", fake_post)
    slugs = meili.search_slugs("how many vacation days", k=2)

    assert slugs == ["vacation-policy", "remote-work"]
    assert captured["url"] == "http://localhost:7700/indexes/anthill-wiki/search"
    assert captured["auth"] == "Bearer secret"
    assert captured["body"] == {"q": "how many vacation days", "limit": 2}


def test_search_slugs_empty_on_error(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MEILI_URL", "http://localhost:7700")

    def boom(*a, **k):
        raise RuntimeError("meili down")

    monkeypatch.setattr(meili.httpx, "post", boom)
    assert meili.search_slugs("q") == []


def test_no_auth_header_when_keyless(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MEILI_URL", "http://localhost:7700")
    monkeypatch.setattr(
        meili.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: _FakeResp({"hits": []}),
    )
    assert "Authorization" not in meili._headers()


def test_index_pages_posts_documents(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("MEILI_URL", "http://localhost:7700")
    p = tmp_path / "vacation-policy.md"
    p.write_text("# Vacation policy\n\n25 days per year.")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["docs"] = json
        return _FakeResp({"taskUid": 1})

    monkeypatch.setattr(meili.httpx, "post", fake_post)
    assert meili.index_pages([p]) is True
    assert captured["url"] == "http://localhost:7700/indexes/anthill-wiki/documents"
    assert captured["docs"][0]["id"] == "vacation-policy"
    assert captured["docs"][0]["title"] == "Vacation policy"
    assert "25 days" in captured["docs"][0]["content"]


def test_index_pages_noop_when_disabled(monkeypatch, tmp_path):
    _clear(monkeypatch)
    p = tmp_path / "x.md"
    p.write_text("# X\n\nbody")
    assert meili.index_pages([p]) is False


def test_relevant_pages_uses_meili_hits(monkeypatch, tmp_path):
    """The ask() retrieval path returns Meili hits without touching embeddings."""
    _clear(monkeypatch)
    from anthill.wiki import ask
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path)
    ws.init()
    ws.write_page("Vacation policy", "# Vacation policy\n\n25 days per year.")
    ws.write_page("Remote work", "# Remote work\n\nFully remote.")

    monkeypatch.setattr(meili, "enabled", lambda: True)
    monkeypatch.setattr(meili, "search_slugs", lambda q, k=3, index=None: ["vacation-policy"])

    pages = ask._relevant_pages(ws, "how many vacation days", k=3)
    assert [p.stem for p in pages] == ["vacation-policy"]
