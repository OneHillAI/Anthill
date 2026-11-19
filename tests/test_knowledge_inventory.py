"""The inventory of a wiki's knowledge items, and the backed-up, reversible way to give them records (#272, slice 1a
of #270). Model-free: nothing here may call a model."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from anthill.lifecycle.version import tag_page
from anthill.web.review_status import review_status_for as _review_status_for
from anthill.wiki import inventory as inv
from anthill.wiki import items
from anthill.wiki.workspace import Workspace

A = "report-1a2b3c4d5e6f.pdf"  # a normal upload: original, kept text, page
B = "bakery-bbbbbbbbbbbb.md"  # a note filed as uploaded
HELD = "held-cccccccccccc.pdf"  # its page waits for a review decision
REJECTED = "rejected-dddddddddddd.pdf"  # its page was rejected
ORPHAN = "lonely-eeeeeeeeeeee.pdf"  # no page, no review: possibly a failed upload
TWIN = "report-copy-1a2b3c4d5e6f.pdf"  # the same content hash as A under another name
LONE_TEXT = "ghost-ffffffffffff.pdf"  # a kept text whose original is gone
MISSING = "missing-999999999999.pdf"  # named by a page, not kept


def _build(tmp_path, name="user-1") -> Workspace:
    ws = Workspace(tmp_path / name, scope="personal")
    ws.init()
    for raw in (A, HELD, REJECTED, ORPHAN, TWIN):
        (ws.raw / raw).write_bytes(f"%PDF-1.4 {raw}".encode())
    (ws.raw / B).write_text("# Bakery interview\n\nThe owner bakes at 4am.\n")
    ws.text.mkdir()
    (ws.text / f"{A}.txt").write_text("Revenue grew in March.")
    (ws.text / f"{HELD}.txt").write_text("Held text.")
    (ws.text / f"{LONE_TEXT}.txt").write_text("No original for this.")
    pages = {
        "report": tag_page(
            "# Report\n\nRevenue grew in March.\n", "qwen2.5:3b", on="2026-11-01", source=A
        ),
        "bakery": tag_page(
            "# Bakery\n\nThe owner bakes at 4am.\n", "verbatim", on="2026-11-01", source=B
        ),
        "no-provenance": "# Hand made\n\nWritten by hand.\n",
        "missing-source": tag_page(
            "# Gone\n\nIts original is not kept.\n", "qwen2.5:3b", on="2026-11-01", source=MISSING
        ),
    }
    for slug, md in pages.items():
        (ws.wiki / f"{slug}.md").write_text(md)  # as written before records existed: no record
    (ws.wiki / "bad-front.md").write_text("---\ntype: [unclosed\n---\n\n# Bad\n")
    (ws.wiki / "garbled.md").write_text(
        "# Garbled\n\nBody.\n\n<!-- anthill: model= ingested= -->\n"
    )
    return ws


def _tree(root: Path, skip=("_backups",)) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in skip for part in p.relative_to(root).parts):
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _fm(text: str) -> dict:
    return yaml.safe_load(items.split_front_matter(text)[0]) or {}


REVIEWS = {HELD: "pending", REJECTED: "rejected"}


# ── the dry run ───────────────────────────────────────────────────────────────


def test_the_report_names_each_kind_of_leftover(tmp_path):
    ws = _build(tmp_path)
    rep = inv.inventory(ws, review_status=REVIEWS)
    f = rep.findings
    assert (rep.pages, rep.sources, rep.texts) == (6, 6, 3)
    assert any(HELD in e for e in f["original_held_or_pending"])
    assert any(REJECTED in e for e in f["original_rejected"])
    assert any(ORPHAN in e and "failed" in e for e in f["original_no_page_no_review"])
    assert any(TWIN in e for e in f["original_no_page_no_review"])
    assert f["text_without_original"] == [LONE_TEXT]
    assert any(A in e and TWIN in e for e in f["duplicate_originals"])
    assert any("missing-source" in e for e in f["page_source_missing"])
    assert f["page_without_provenance"] == ["no-provenance"]
    assert any("garbled" in e for e in f["provenance_unreadable"])
    assert [q["path"] for q in rep.quarantined] == ["wiki/bad-front.md"]
    assert len(rep.would_add_pages) == 5 and len(rep.would_add_sources) == 6


def test_without_review_rows_a_leftover_is_not_called_held_or_failed(tmp_path):
    ws = _build(tmp_path)
    rep = inv.inventory(ws, review_status=None)
    assert rep.reviews_known is False
    assert (
        "original_held_or_pending" not in rep.findings
        and "original_no_page_no_review" not in rep.findings
    )
    assert HELD in " ".join(rep.findings["original_no_page"])
    assert "review rows were not read" in rep.to_text()


def test_a_dry_run_changes_nothing(tmp_path):
    ws = _build(tmp_path)
    before = _tree(tmp_path)
    inv.inventory(ws, review_status=REVIEWS)
    assert _tree(tmp_path) == before and not (tmp_path / "_backups").exists()


def test_the_report_reads_as_text_and_as_json(tmp_path):
    rep = inv.inventory(_build(tmp_path), review_status=REVIEWS)
    text = rep.to_text()
    assert "pages 6" in text and "apply would add records to 5 page(s) and 6 original(s)" in text
    assert json.loads(inv.render_json([rep]))[0]["counts"]["sources"] == 6


@pytest.mark.parametrize(
    "folder,expected",
    [
        ("team-5", ("team", "team-5")),
        ("user-2", ("personal", "user-2")),
        ("org-3", ("org", "org-3")),
    ],
)
def test_the_space_of_a_folder_comes_from_where_it_is(tmp_path, folder, expected):
    assert inv.space_of_root(tmp_path / folder) == expected


def test_the_legacy_folders_are_told_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    assert inv.space_of_root(tmp_path / "org") == ("org", "")
    assert inv.space_of_root(tmp_path / "workspace") == ("personal", "")


# ── apply ─────────────────────────────────────────────────────────────────────


def test_apply_adds_records_and_changes_no_page_body(tmp_path):
    ws = _build(tmp_path)
    before = {p.stem: p.read_text() for p in ws.pages()}
    res = inv.apply_records(ws, review_status=REVIEWS)
    assert res.changed_pages == 5 and res.changed_sources == 6 and res.backup and res.manifest
    for slug, old in before.items():
        new = (ws.wiki / f"{slug}.md").read_text()
        if slug in ("bad-front",):
            assert new == old  # damaged: left alone
            continue
        old_front, old_rest = items.split_front_matter(old)
        _, new_rest = items.split_front_matter(new)
        assert new_rest == (old_rest if old_front is not None else old)  # the body, byte for byte
        assert items.body_hash(new) == items.body_hash(old)
        gained = set(_fm(new)) - set(_fm(old) if old_front is not None else {"type"})
        assert gained == {items.FRONT_MATTER_KEY}


def test_apply_keeps_the_front_matter_that_was_there(tmp_path):
    from anthill.wiki import okf

    ws = _build(tmp_path)
    legacy = okf.OkfPage(
        title="Kept",
        body="# Kept\n\nBody.\n",
        scope="personal",
        review="approved",
        tier="gold",
        tags=["a", "b"],
        extra={"resource": "x"},
    )
    path = ws.wiki / "kept.md"
    path.write_text(okf.to_okf(legacy))
    before = path.read_text()
    inv.apply_records(ws)
    after = path.read_text()
    assert after.startswith(
        before.split("\n---\n")[0]
    )  # the front matter that was there, byte for byte
    fm_after = _fm(after)
    fm_after.pop(items.FRONT_MATTER_KEY)
    assert fm_after == _fm(before)
    assert items.read_page_record(after).review_state == "checked"  # the page said "approved"


def test_the_records_say_what_can_be_proved(tmp_path):
    ws = _build(tmp_path)
    inv.apply_records(ws, review_status=REVIEWS)
    pages = {p.name: p for p in items.list_items(ws).pages}
    sources = {s.name: s for s in items.list_items(ws).sources}
    assert pages["report"].origin == "model-written" and pages["report"].review_state == "unchecked"
    assert pages["bakery"].origin == "own" and pages["bakery"].review_state == "checked"
    assert (
        pages["no-provenance"].origin == "unknown"
        and pages["no-provenance"].review_state == "unchecked"
    )
    assert pages["report"].derived_from == [sources[A].id]
    assert pages["missing-source"].derived_from == [f"name:{MISSING}"]
    assert sources[A].review_state == "checked" and sources[A].origin == "unknown"
    assert sources[HELD].review_state == "proposed" and sources[REJECTED].review_state == "rejected"
    assert sources[ORPHAN].review_state == "proposed"
    assert all(s.content_hash == items.file_hash(ws.raw / s.name) for s in sources.values())
    assert all(
        p.space == "personal" and p.space_ref == "user-1" and p.version == 1 for p in pages.values()
    )
    assert len({r.id for r in [*pages.values(), *sources.values()]}) == len(pages) + len(sources)


def test_a_backup_is_written_first_and_can_be_read(tmp_path):
    import tarfile

    ws = _build(tmp_path)
    res = inv.apply_records(ws)
    with tarfile.open(res.backup, "r:gz") as tar:
        names = set(tar.getnames())
    assert "wiki/report.md" in names and "wiki/bad-front.md" in names
    assert not any(
        n.startswith("raw/") or n.startswith("text/") for n in names
    )  # apply never touches those
    assert Path(res.backup).parent == tmp_path / "_backups"


def test_apply_stops_when_the_backup_cannot_be_made(tmp_path, monkeypatch):
    ws = _build(tmp_path)
    before = _tree(tmp_path)

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(inv, "backup_workspace", broken)
    with pytest.raises(OSError):
        inv.apply_records(ws)
    assert _tree(tmp_path) == before


def test_applying_twice_changes_nothing_the_second_time(tmp_path):
    ws = _build(tmp_path)
    inv.apply_records(ws)
    after = _tree(tmp_path)
    backups = sorted((tmp_path / "_backups").iterdir())
    again = inv.apply_records(ws)
    assert again.nothing_to_do and not again.backup
    assert _tree(tmp_path) == after and sorted((tmp_path / "_backups").iterdir()) == backups


def test_a_damaged_page_and_a_damaged_record_are_skipped_and_reported(tmp_path):
    ws = _build(tmp_path)
    (ws.wiki / "report.md").write_text(
        "---\ntype: Concept\nx-anthill-item:\n  id: abc\n  kind: page\n  space: personal\n  origin: robot\n---\n\n# R\n"
    )
    sidecar = ws.root / "records" / "sources" / f"{A}.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("{not json")
    snapshot = {
        k: (ws.root / k).read_bytes()
        for k in ("wiki/report.md", "wiki/bad-front.md", f"records/sources/{A}.json")
    }
    rep = inv.inventory(ws, review_status=REVIEWS)
    assert {q["path"] for q in rep.quarantined} >= {
        "wiki/report.md",
        "wiki/bad-front.md",
        f"records/sources/{A}.json",
    }
    inv.apply_records(ws, review_status=REVIEWS)
    assert {
        k: (ws.root / k).read_bytes() for k in snapshot
    } == snapshot  # never repaired, never used
    found = items.list_items(ws)
    assert "report" not in [p.name for p in found.pages] and A not in [
        s.name for s in found.sources
    ]


# ── undo ──────────────────────────────────────────────────────────────────────


def test_undo_puts_the_tree_back_byte_for_byte(tmp_path):
    ws = _build(tmp_path)
    before = _tree(tmp_path)
    res = inv.apply_records(ws, review_status=REVIEWS)
    assert _tree(tmp_path) != before
    out = inv.undo_records(ws, Path(res.manifest))
    assert out.restored_pages == 5 and out.removed_sources == 6 and out.skipped == []
    after = _tree(tmp_path)
    after.pop(f"user-1/records/{Path(res.manifest).name}.undone", None)
    assert after == before
    assert not (ws.root / "records" / "sources").exists()


def test_undo_leaves_a_page_that_changed_since(tmp_path):
    ws = _build(tmp_path)
    res = inv.apply_records(ws)
    (ws.wiki / "report.md").write_text((ws.wiki / "report.md").read_text() + "\nEdited since.\n")
    out = inv.undo_records(ws, Path(res.manifest))
    assert out.restored_pages == 4 and any("report" in s for s in out.skipped)
    assert "Edited since." in (ws.wiki / "report.md").read_text()


# ── the records live in the files, not in a database ──────────────────────────


def test_losing_everything_rebuildable_loses_no_record(tmp_path):
    import shutil

    ws = _build(tmp_path)
    inv.apply_records(ws, review_status=REVIEWS)
    (ws.root / ".cache").mkdir()
    (ws.root / ".cache" / "page_vectors.sqlite3").write_bytes(b"vectors")
    (ws.root / ".cache" / "doc_passages.sqlite3").write_bytes(b"passages")
    before = items.list_items(ws)
    shutil.rmtree(ws.root / ".cache")
    after = items.list_items(ws)
    assert after.pages == before.pages and after.sources == before.sources


# ── no model, no database needed ──────────────────────────────────────────────


def test_no_model_is_called(tmp_path, monkeypatch):
    from anthill.inference.ollama import OllamaBackend

    def never(*a, **k):
        raise AssertionError("the inventory called a model")

    for method in ("chat", "chat_stream", "chat_with_tools", "chat_with_confidence"):
        monkeypatch.setattr(OllamaBackend, method, never)
    ws = _build(tmp_path)
    res = inv.apply_records(ws, review_status=REVIEWS)
    inv.inventory(ws, review_status=REVIEWS)
    inv.undo_records(ws, Path(res.manifest))


# ── the review rows ───────────────────────────────────────────────────────────


def _db(tmp_path, rows):
    from sqlalchemy.orm import sessionmaker

    from anthill.web import db as db_mod

    path = tmp_path / "app.db"
    engine = db_mod.get_engine(path)
    db_mod.create_tables(engine)
    s = sessionmaker(bind=engine)()
    s.add(db_mod.Organization(id=1, name="Acme", slug="acme"))
    s.flush()
    for uid in (2,):
        s.add(db_mod.User(id=uid, org_id=1, email=f"u{uid}@acme.com", role="member", active=True))
    s.flush()
    s.add(db_mod.Team(id=5, org_id=1, name="Bakers", slug="bakers", owner_id=2))
    s.flush()
    for row in rows:
        s.add(db_mod.WikiReview(org_id=1, slug="x", **row))
    s.commit()
    s.close()
    return path


def _content(source):
    return tag_page("# X\n\nBody.\n", "qwen2.5:3b", on="2026-11-01", source=source)


def test_review_rows_are_matched_to_the_workspace_they_were_proposed_into(tmp_path):
    db = _db(
        tmp_path,
        [
            {"content": _content(HELD), "target_scope": "team", "team_id": 5, "status": "pending"},
            {
                "content": _content(REJECTED),
                "target_scope": "personal",
                "proposed_by": 2,
                "status": "rejected",
            },
            {"content": _content(A), "target_scope": "org", "status": "approved"},
            {
                "content": _content("later-111111111111.pdf"),
                "target_scope": "team",
                "team_id": 5,
                "status": "rejected",
            },
            {
                "content": _content("later-111111111111.pdf"),
                "target_scope": "team",
                "team_id": 5,
                "status": "pending",
            },
        ],
    )
    assert _review_status_for(db, tmp_path / "team-5") == {
        HELD: "pending",
        "later-111111111111.pdf": "pending",
    }
    assert _review_status_for(db, tmp_path / "user-2") == {REJECTED: "rejected"}
    assert _review_status_for(db, tmp_path / "user-3") == {}
    assert _review_status_for(db, tmp_path / "org-1") == {A: "approved"}


def test_review_rows_cannot_be_read_for_the_legacy_wiki_or_without_a_database(tmp_path):
    db = _db(tmp_path, [])
    assert _review_status_for(db, tmp_path / "workspace") is None
    assert _review_status_for(tmp_path / "no.db", tmp_path / "team-5") is None
    assert _review_status_for(None, tmp_path / "team-5") is None


# ── the command ───────────────────────────────────────────────────────────────


def _run(*args):
    from anthill.cli import app

    return CliRunner().invoke(app, ["knowledge", "inventory", *args])


def test_the_command_is_a_dry_run_unless_told_otherwise(tmp_path):
    ws = _build(tmp_path)
    before = _tree(tmp_path)
    res = _run("--root", str(ws.root), "--db", str(tmp_path / "none.db"))
    assert res.exit_code == 0, res.output
    assert "pages 6" in res.output and "dry run: nothing was written" in res.output
    assert _tree(tmp_path) == before


def test_the_command_applies_and_undoes(tmp_path):
    ws = _build(tmp_path)
    before = _tree(tmp_path)
    res = _run("--root", str(ws.root), "--db", str(tmp_path / "none.db"), "--apply")
    assert res.exit_code == 0, res.output
    assert "Undo with --undo" in res.output
    manifest = next((ws.root / "records").glob("inventory-*.json"))
    undo = _run("--undo", str(manifest))
    assert undo.exit_code == 0, undo.output
    after = _tree(tmp_path)
    after.pop(f"user-1/records/{manifest.name}.undone", None)
    assert after == before


def test_the_command_prints_json_and_skips_a_folder_that_is_not_there(tmp_path):
    ws = _build(tmp_path)
    res = _run(
        "--root",
        str(ws.root),
        "--root",
        str(tmp_path / "team-9"),
        "--db",
        str(tmp_path / "none.db"),
        "--json",
    )
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output


# ── what apply and undo keep, and what a dry run leaves alone (#306) ──────────


def _a_page(ws: Workspace) -> Path:
    return ws.wiki / "report.md"  # a page that gets a record


def test_apply_and_undo_keep_the_permissions_a_page_had(tmp_path):
    ws = _build(tmp_path)
    page = _a_page(ws)
    page.chmod(0o600)
    res = inv.apply_records(ws, review_status=REVIEWS)
    assert page.stat().st_mode & 0o777 == 0o600
    inv.undo_records(ws, Path(res.manifest))
    assert page.stat().st_mode & 0o777 == 0o600


def test_a_page_that_is_a_symlink_is_left_alone_by_apply_and_undo(tmp_path):
    ws = _build(tmp_path)
    target = _a_page(ws)
    link = ws.wiki / "alias.md"
    link.symlink_to(target.name)
    before = target.read_bytes()
    res = inv.apply_records(ws, review_status=REVIEWS)
    assert link.is_symlink() and link.resolve() == target.resolve()
    assert any(s.startswith("alias: a symbolic link") for s in res.skipped)
    assert items.read_page_record(target.read_text()) is not None  # the real page got its record
    assert target.read_bytes() != before
    out = inv.undo_records(ws, Path(res.manifest))
    assert (
        link.is_symlink()
        and out.restored_pages >= 1
        and not any("alias" in s and "symbolic" not in s for s in out.skipped)
    )


def test_a_dry_run_reads_the_database_read_only_and_leaves_its_bytes_alone(tmp_path):
    ws = _build(tmp_path)
    db = _db(tmp_path / "data", [])
    before = db.read_bytes()
    status = _review_status_for(db, ws.root)
    assert status is None or isinstance(status, dict)
    assert (
        db.read_bytes() == before
    )  # SQLite may keep a -wal/-shm pair beside a WAL database while it is open
    import sqlalchemy as sa

    ro = sa.create_engine(f"sqlite:///file:{db}?mode=ro&uri=true", connect_args={"uri": True})
    with ro.connect() as conn, pytest.raises(sa.exc.OperationalError):
        conn.execute(sa.text("CREATE TABLE should_not_exist (x INTEGER)"))
    ro.dispose()
