"""#683 requirement 2 + "snippets are self-added wiki elements": a captured snippet must become a
real, retrievable page in the user's PERSONAL wiki immediately, not a separate silo only reachable by
clicking "-> Wiki" on the /snippets list. Before this fix, `anthill/wiki/ask.py` never knew about
`Snippet` rows at all (it reads pages off disk via `Workspace.pages()`), so a captured snippet was
collected but never grounded into a later answer - confirmed by `grep -rn "Snippet" anthill/wiki/`
returning nothing, both before and after this change (the bridge is in the web layer, not the wiki
layer: `snippet_save()` now also calls the existing `propose_wiki_write()`)."""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, Snippet, User, WikiReview


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    # Real per-scope directories on disk (workspace_for reads these env vars), isolated per test.
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org-wiki"))
    # The independent verifier cross-check hits a local Ollama for its model list (best-effort, never
    # raises) - stub it out so tests are fast and deterministic regardless of what's running locally,
    # matching tests/test_provenance.py's pattern for the same call.
    monkeypatch.setattr(app_mod, "_verify_wiki_write", lambda *a, **k: None)

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return c, app_mod, user.id, org.id


def test_captured_snippet_becomes_a_retrievable_personal_wiki_page(tmp_path, monkeypatch):
    from anthill.wiki.ask import _keyword_fallback
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        r = c.post(
            "/snippets/save",
            data={
                "content": "PostgreSQL was chosen for the billing service because of its JSON support.",
                "tags": "billing,db",
                "question": "which database for billing?",
                "source": "chat",
                "source_ref": "conv:1/msg:1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["wiki_applied"] is True  # clean personal-scope write -> auto-applied
        assert body["wiki_slug"]

        ws = workspace_for("personal", user_id=uid)
        pages = ws.pages()
        matches = [p for p in pages if p.stem == body["wiki_slug"]]
        assert matches, f"expected a page {body['wiki_slug']}.md, got {[p.stem for p in pages]}"
        assert "PostgreSQL was chosen for the billing service" in matches[0].read_text()

        # ask.py's page-loading path: `_relevant_pages` falls back to this keyword ranking whenever
        # embeddings are unavailable/uninformative - it is exactly the function used at answer time,
        # not a reimplementation, so this proves the page is actually reachable by retrieval.
        hits = _keyword_fallback(ws, "what database did we choose for billing?", k=3)
        assert body["wiki_slug"] in {p.stem for p in hits}
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_slug_stays_consistent_across_personal_to_org_promotion(tmp_path, monkeypatch):
    import anthill.wiki.review as review_mod
    from anthill.wiki.workspace import workspace_for

    c, app_mod, _uid, _org_id = _client(tmp_path, monkeypatch)
    try:
        # Org-scope review runs a real model pass (`outline_change`'s scope in ("team", "org")
        # branch); force it clean so the promotion auto-applies deterministically regardless of
        # whether a local model is installed in the test environment - this test is about slug
        # consistency, not about the review model's judgement.
        monkeypatch.setattr(
            review_mod,
            "outline_change",
            lambda *a, **k: SimpleNamespace(flags=[], text="ok", recommendation="approve"),
        )
        r = c.post(
            "/snippets/save",
            data={"content": "Kafka is the event pipeline.", "tags": "infra", "source": "chat"},
        )
        body = r.json()
        snip_id = body["id"]
        original_slug = body["wiki_slug"]
        assert original_slug

        # Edit the snippet's tags AFTER capture. `_snippet_wiki_body`'s slug is derived from the
        # first tag - if the promotion route recomputed it from the snippet's CURRENT tags instead
        # of reusing `snip.wiki_slug`, this rename would fork a second, drifting page rather than
        # promoting the one page the user already has.
        er = c.post(
            f"/snippets/{snip_id}/edit",
            data={"content": "Kafka is the event pipeline.", "tags": "renamed"},
        )
        assert er.status_code in (200, 302)

        pr = c.post(f"/snippets/{snip_id}/wiki", data={"target_scope": "org"})
        assert pr.status_code in (200, 302)

        org_ws = workspace_for("org")
        org_slugs = {p.stem for p in org_ws.pages()}
        assert original_slug in org_slugs, f"expected {original_slug} in {org_slugs}"
        assert not any(s.startswith("renamed") for s in org_slugs)  # no forked second page

        s = app_mod._SessionFactory()
        snip = s.get(Snippet, snip_id)
        assert snip.wiki_slug == original_slug
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_flagged_personal_write_is_queued_not_lost(tmp_path, monkeypatch):
    """A personal-scope capture is not unconditionally auto-applied - `outline_change()`'s mechanical
    checks (e.g. a dangling [[link]] or a near-duplicate title) still queue it for the proposer's own
    review. The snippet row itself must never be lost either way."""
    import anthill.wiki.review as review_mod

    c, app_mod, _uid, org_id = _client(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            review_mod,
            "outline_change",
            lambda *a, **k: SimpleNamespace(flags=["broken_links"], text="flagged"),
        )
        r = c.post("/snippets/save", data={"content": "Some content.", "tags": "x"})
        body = r.json()
        assert body["wiki_applied"] is False
        assert body["id"]

        s = app_mod._SessionFactory()
        rev = s.query(WikiReview).filter(WikiReview.org_id == org_id).first()
        assert rev is not None
        assert rev.target_scope == "personal" and rev.status == "pending"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_snippet_to_wiki_still_works_for_a_fresh_snippet_without_a_prior_wiki_slug():
    """Unit-level: `_snippet_wiki_body` must not require `wiki_slug` to already be set (a snippet
    saved before this change, or one whose capture-time write failed, still promotes correctly)."""
    from anthill.web.app import _snippet_wiki_body

    snip = SimpleNamespace(
        id=7, tags="pricing", rationale="Why it matters.", content="Body text.", wiki_slug=None
    )
    slug, content = _snippet_wiki_body(snip)
    assert slug == "pricing-7"
    assert "Body text." in content and "Why it matters." in content
