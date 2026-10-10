"""OKF (Open Knowledge Format) export/parse for wiki pages + Anthill governance extensions.

First slice of the OKF-adoption plan: prove the format mapping end to end - an existing Anthill page
exports to OKF-conformant markdown, the governance metadata rides as `x-anthill-*` extensions, OKF's
"preserve unknown keys" rule holds on round-trip, and conformance is enforced. Pure functions, no
change to the live wiki.
"""

import pytest

from anthill.wiki import okf


def test_export_existing_wiki_page_is_okf_conformant():
    page = "# Billing model\n\nWe bill monthly.\n\n## Related\n[[invoices]] [[Tax Rules]]\n"
    out = okf.from_wiki_page(
        page, slug="billing-model", scope="org", review="approved", tier="gold"
    )
    assert out.startswith("---\n")
    assert okf.is_conformant(out)
    fm, body = okf._split_frontmatter(out)
    # OKF core: required type + recommended title/timestamp/description
    assert fm["type"] and fm["title"] == "Billing model" and fm["timestamp"]
    assert fm["description"] == "We bill monthly."
    # governance extensions present as x-anthill-* (the published profile)
    assert fm["x-anthill-scope"] == "org"
    assert fm["x-anthill-review"] == "approved"
    assert fm["x-anthill-tier"] == "gold"
    # [[slug]] wikilinks become OKF bundle-absolute links, slugified
    assert "[invoices](/invoices)" in body and "[Tax Rules](/tax-rules)" in body
    assert "[[" not in body


def test_roundtrip_preserves_governance_and_unknown_keys():
    page = okf.OkfPage(
        type="Playbook",
        title="Onboarding",
        body="Do X.",
        scope="team",
        review="proposed",
        tier="silver",
        sources=["https://a"],
        extra={"resource": "doc://x", "custom": 1},  # foreign OKF keys
    )
    back = okf.parse_okf(okf.to_okf(page))
    assert back.type == "Playbook" and back.title == "Onboarding" and back.body == "Do X."
    assert back.scope == "team" and back.review == "proposed" and back.tier == "silver"
    assert back.sources == ["https://a"]
    # OKF requires consumers to PRESERVE unrecognized fields
    assert back.extra.get("resource") == "doc://x" and back.extra.get("custom") == 1


def test_conformance_requires_a_non_empty_type():
    assert not okf.is_conformant("---\ntitle: x\n---\n\nbody")  # no type
    assert not okf.is_conformant("just markdown, no frontmatter")
    assert okf.is_conformant("---\ntype: Concept\n---\n\nbody")


def test_version_tracks_the_adopted_spec():
    assert okf.OKF_VERSION == "0.1"


# ── Phase 1: export a wiki scope as an OKGF bundle ───────────────────────────────────────────────


def test_export_bundle_pages_conformant_index_versioned():
    pages = [
        ("billing-model", "# Billing model\n\nWe bill monthly.\n", 0.0),
        ("invoices", "# Invoices\n\nNet 30.\n", 0.0),
    ]
    files = okf.export_bundle(pages, scope="org", log_md="# Log\n", principles_md="Be terse.")
    assert {"billing-model.md", "invoices.md", "index.md", "log.md", "PRINCIPLES.md"} <= set(files)
    for slug in ("billing-model", "invoices"):
        page = files[f"{slug}.md"]
        assert okf.is_conformant(page)
        assert "x-anthill-scope: org" in page and "x-anthill-review: approved" in page  # governance
    assert 'okf_version: "0.1"' in files["index.md"]
    assert "[Billing model](/billing-model)" in files["index.md"]


def _admin_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "orgwiki"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    s.add_all(
        [
            User(org_id=o.id, email="admin@a.com", role="admin", active=True),
            User(org_id=o.id, email="member@a.com", role="member", active=True),
        ]
    )
    s.commit()
    rows = {u.email: u for u in app_mod._SessionFactory().query(User).all()}
    return TestClient(app_mod.app), app_mod, o.id, rows


def test_export_route_returns_an_okgf_bundle(tmp_path, monkeypatch):
    import io
    import tarfile

    from anthill.wiki.workspace import workspace_for

    client, _app, org_id, rows = _admin_client(tmp_path, monkeypatch)
    ws = workspace_for("org", org_id=org_id)
    ws.init()
    ws.write_page(
        "Billing model", "# Billing model\n\nWe bill monthly.\n\n## Related\n[[invoices]]\n"
    )

    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(rows["admin@a.com"].id, org_id, "admin"))
    r = client.get("/wiki/export.okgf.tgz")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    files = {}
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
        for m in tf.getmembers():
            files[m.name] = tf.extractfile(m).read().decode()
    assert "billing-model.md" in files and "index.md" in files
    assert okf.is_conformant(files["billing-model.md"])
    assert "x-anthill-scope: org" in files["billing-model.md"]
    assert "[invoices](/invoices)" in files["billing-model.md"]  # wikilink -> OKF link
    assert 'okf_version: "0.1"' in files["index.md"]


def test_export_route_org_is_admin_only(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token

    client, _app, org_id, rows = _admin_client(tmp_path, monkeypatch)
    client.cookies.set("session_token", make_token(rows["member@a.com"].id, org_id, "member"))
    assert client.get("/wiki/export.okgf.tgz").status_code == 403


# ── Phase 2: OKGF is the native on-disk format ───────────────────────────────────────────────────


def _ws(tmp_path, monkeypatch, scope="org"):
    from anthill.wiki.workspace import workspace_for

    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "personal"))
    ws = workspace_for(scope, org_id=1)  # organisation 1 owns the folder
    ws.init()
    return ws


def test_write_page_stores_native_okgf(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, "org")
    path = ws.write_page("Billing model", "# Billing model\n\nWe bill monthly.\n\n[[invoices]]\n")
    text = path.read_text()
    assert okf.is_conformant(text)
    fm, body = okf._split_frontmatter(text)
    assert fm["type"] == "Concept" and fm["title"] == "Billing model" and fm["timestamp"]
    assert fm["x-anthill-scope"] == "org"  # stamped from the workspace scope
    assert "[[invoices]]" in body  # authoring shorthand kept on disk (only export converts it)


def test_write_page_does_not_double_wrap(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, "org")
    once = ws.write_page("Note", "# Note\n\nBody.\n").read_text()
    twice = ws.write_page("Note", once).read_text()  # re-save the already-frontmattered page
    assert twice.count("\n---\n") == once.count("\n---\n") == 1
    assert okf.is_conformant(twice) and "x-anthill-scope: org" in twice


def test_central_readers_ignore_frontmatter(tmp_path, monkeypatch):
    from anthill.common.text import first_h1, outbound_links, strip_frontmatter

    ws = _ws(tmp_path, monkeypatch, "org")
    text = ws.write_page(
        "Billing", "# Billing\n\nPay up.\n\n## Related\n[[invoices]]\n"
    ).read_text()
    assert first_h1(text) == "Billing"  # title found past the frontmatter
    assert "invoices" in outbound_links(text)  # links found past the frontmatter
    assert "x-anthill-scope" not in strip_frontmatter(text)  # the body carries no YAML


def test_migrate_to_okgf_is_idempotent(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, "org")
    (ws.wiki / "legacy.md").write_text("# Legacy\n\nOld page.\n")  # a pre-OKGF page on disk
    assert ws.migrate_to_okgf() == 1
    text = (ws.wiki / "legacy.md").read_text()
    assert okf.is_conformant(text) and "x-anthill-scope: org" in text
    assert ws.migrate_to_okgf() == 0  # already migrated -> no-op


def test_export_reexports_a_native_okgf_page(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, "org")
    ws.write_page("Billing", "# Billing\n\nPay.\n\n## Related\n[[invoices]]\n", tier="gold")
    exported = okf.from_wiki_page((ws.wiki / "billing.md").read_text(), slug="billing")
    assert okf.is_conformant(exported)
    assert exported.count("\n---\n") == 1  # exactly one frontmatter block (no double-wrap)
    assert "x-anthill-tier: gold" in exported and "x-anthill-scope: org" in exported
    assert "[invoices](/invoices)" in exported and "[[invoices]]" not in exported


def test_okgf_spec_is_published_at_docs_okgf():
    # The OKGF spec is reachable in-app (markdown rendered client-side), not just a file in the repo.
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    r = TestClient(app_mod.app).get("/docs/okgf")
    assert r.status_code == 200
    assert "Open Knowledge and Governance Format" in r.text  # the spec content is embedded
    assert "marked.min.js" in r.text and "DOMPurify" in r.text  # rendered client-side, no new dep


# ── Phase 3: import an OKGF/OKF bundle into the review gate ───────────────────────────────────────


def _tgz(files: dict) -> bytes:
    """Pack name -> content into a gzipped tar, keeping unsafe names verbatim (for the guard tests)."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_bundle_reads_content_skips_reserved_and_unsafe():
    good = okf.to_okf(
        okf.OkfPage(type="Concept", title="Good", body="Hi.", extra={"resource": "doc://x"})
    )
    bundle = _tgz(
        {
            "good.md": good,
            "index.md": "# Index\n",  # reserved bundle metadata
            "log.md": "# Log\n",  # reserved
            "PRINCIPLES.md": "Be terse.\n",  # reserved
            "../evil.md": "pwned",  # path traversal -> skipped
            "notes.txt": "not markdown",  # non-md -> skipped
        }
    )
    pages = dict(okf.parse_bundle(bundle))
    assert "good" in pages
    assert pages["good"].title == "Good"
    assert pages["good"].extra.get("resource") == "doc://x"  # unknown OKF key preserved
    for skipped in ("evil", "index", "log", "principles", "notes"):
        assert skipped not in pages


@pytest.mark.knowledge_invariant
def test_import_route_queues_pending_and_resets_foreign_provenance(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token
    from anthill.web.db import WikiReview

    client, app_mod, org_id, rows = _admin_client(tmp_path, monkeypatch)
    client.cookies.set("session_token", make_token(rows["admin@a.com"].id, org_id, "admin"))
    # a page that CLAIMS it is approved + signed - we must not trust that on import
    foreign = okf.to_okf(
        okf.OkfPage(
            type="Concept",
            title="Pricing",
            body="Net 30.",
            scope="org",
            review="approved",
            signature="ZmFrZQ==",
            extra={"resource": "doc://x"},
        )
    )
    plain = okf.to_okf(okf.OkfPage(type="Concept", title="Refunds", body="14 days."))
    bundle = _tgz({"pricing.md": foreign, "refunds.md": plain, "index.md": "# Index\n"})

    r = client.post(
        "/wiki/import.okgf",
        files={"file": ("wiki.okgf.tgz", bundle, "application/gzip")},
        data={"scope": "org"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 2 and body["status"] == "pending-review"

    revs = {x.slug: x for x in app_mod._SessionFactory().query(WikiReview).all()}
    assert set(revs) == {"pricing", "refunds"}
    for rev in revs.values():
        assert rev.status == "pending" and rev.target_scope == "org"  # never auto-published
        assert "imported-foreign" in rev.flags
    # foreign approval + signature are NOT trusted: reset to proposed, signature dropped
    assert "x-anthill-review: proposed" in revs["pricing"].content
    assert "x-anthill-review: approved" not in revs["pricing"].content
    assert "x-anthill-signature" not in revs["pricing"].content
    assert "resource: doc://x" in revs["pricing"].content  # provenance preserved


def test_import_route_org_is_admin_only(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token

    client, _app, org_id, rows = _admin_client(tmp_path, monkeypatch)
    client.cookies.set("session_token", make_token(rows["member@a.com"].id, org_id, "member"))
    bundle = _tgz({"x.md": okf.to_okf(okf.OkfPage(type="Concept", title="X", body="y"))})
    r = client.post(
        "/wiki/import.okgf",
        files={"file": ("w.tgz", bundle, "application/gzip")},
        data={"scope": "org"},
    )
    assert r.status_code == 403


def test_import_route_rejects_non_bundle(tmp_path, monkeypatch):
    from anthill.web.crypto import make_token

    client, _app, org_id, rows = _admin_client(tmp_path, monkeypatch)
    client.cookies.set("session_token", make_token(rows["admin@a.com"].id, org_id, "admin"))
    r = client.post(
        "/wiki/import.okgf",
        files={"file": ("junk.tgz", b"not a tar at all", "application/gzip")},
        data={"scope": "org"},
    )
    assert r.status_code == 400
