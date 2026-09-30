"""Meilisearch retrieval is isolated per workspace. Previously every scope upserted into one flat
index keyed by slug alone, so a personal 'roadmap' and the org 'roadmap' collided (last write wins)
and polluted each other's ranking. Each workspace now uses its own index, keyed on its root."""

from __future__ import annotations

from anthill.wiki.workspace import Workspace


def test_each_workspace_gets_a_distinct_stable_index(tmp_path):
    a = Workspace(tmp_path / "user-1")
    b = Workspace(tmp_path / "user-2")
    org = Workspace(tmp_path / "org-wiki")
    names = {a.meili_index, b.meili_index, org.meili_index}
    assert len(names) == 3  # no two scopes share an index
    assert Workspace(tmp_path / "user-1").meili_index == a.meili_index  # stable for the same root
    for n in names:
        assert n.startswith("anthill-wiki-")
        assert n.removeprefix("anthill-wiki-").isalnum()  # a valid Meili index uid


def test_search_and_index_target_the_workspaces_own_index(tmp_path, monkeypatch):
    import anthill.search.meili as meili

    monkeypatch.setenv("MEILI_URL", "http://localhost:7700")
    urls: list[str] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": []}

    monkeypatch.setattr(meili.httpx, "post", lambda url, **kw: (urls.append(url), _Resp())[1])

    ws = Workspace(tmp_path / "user-7")
    meili.search_slugs("q", 3, index=ws.meili_index)
    assert ws.meili_index in urls[-1]  # queried this workspace's own index

    other = Workspace(tmp_path / "org-wiki")
    meili.search_slugs("q", 3, index=other.meili_index)
    assert other.meili_index in urls[-1] and ws.meili_index not in urls[-1]  # not the other scope's
