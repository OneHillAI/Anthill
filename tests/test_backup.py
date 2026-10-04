"""Backup + restore: full-archive round-trip, pre-migration snapshots (+ pruning), embedding and
restoring the fine-tuned Ollama model, the migration-hook wiring, and the admin page."""

import json
import os
import shutil
import sqlite3
import tarfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill import backup as bk
from anthill import desktop
from anthill.web import db
from anthill.web.db import Organization, User


def _setup_data_dir(tmp_path, monkeypatch):
    """Point the env at a fresh data dir with a real DB + wiki + files + skills + secrets."""
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("ANTHILL_HOME", str(home))
    monkeypatch.setenv("ANTHILL_DB", str(home / "anthill.db"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(home / "workspace"))
    monkeypatch.setenv("ANTHILL_FILES_DIR", str(home / "files"))
    monkeypatch.setenv("ANTHILL_SKILLS_DIR", str(home / "skills"))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(home / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(home / "org-wiki"))
    con = sqlite3.connect(str(home / "anthill.db"))
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    (home / "workspace").mkdir()
    (home / "workspace" / "index.md").write_text("# wiki\n")
    (home / "files").mkdir()
    (home / "files" / "a.txt").write_text("file\n")
    (home / "skills").mkdir()
    (home / "skills" / "s.md").write_text("# skill\n")
    (home / "secrets.env").write_text("ANTHILL_ENCRYPTION_KEY=abc\n")
    return home


def test_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    home = _setup_data_dir(tmp_path, monkeypatch)
    res = bk.create_backup(include_model=False)
    assert res.path.exists()
    assert {"db", "workspace", "files", "skills", "secrets"} <= set(res.includes)

    # Mutate everything, then restore should undo it back to the captured state.
    (home / "workspace" / "index.md").write_text("CHANGED\n")
    (home / "skills" / "s.md").unlink()
    con = sqlite3.connect(str(home / "anthill.db"))
    con.execute("INSERT INTO t VALUES (2)")
    con.commit()
    con.close()

    rr = bk.restore_backup(res.path)
    assert {"db", "workspace", "files", "skills", "secrets"} <= set(rr.restored)
    assert (home / "workspace" / "index.md").read_text() == "# wiki\n"  # reverted
    assert (home / "skills" / "s.md").exists()  # the deleted file is back
    con = sqlite3.connect(str(home / "anthill.db"))
    n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    con.close()
    assert n == 1  # the post-backup insert is gone
    assert rr.safety_backup and rr.safety_backup.exists()  # current state was saved first


_WIKI_FILES = {
    "wikis/user-1/wiki/p.md": "# personal page\n",
    "wikis/user-1/raw/doc-abc123.pdf": "%PDF original upload\n",
    "wikis/team-2/wiki/t.md": "# project page\n",
    "org-wiki/wiki/o.md": "# org page\n",
    "org-wiki/raw/orig.pdf": "%PDF org original\n",
}


def _packaged_home(tmp_path, monkeypatch):
    """A home laid out the way the packaged app does it: desktop._apply_paths roots every path var,
    the per-user/project wikis and the org wiki included, at one data dir."""
    monkeypatch.setattr(
        os, "environ", dict(os.environ)
    )  # _apply_paths writes env; undone at teardown
    home = tmp_path / "Anthill"
    home.mkdir()
    desktop._apply_paths(home, force=True)
    con = sqlite3.connect(str(home / "anthill.db"))
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    (home / "secrets.env").write_text("ANTHILL_ENCRYPTION_KEY=abc\n")
    for rel, body in _WIKI_FILES.items():
        f = home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return home


def _names(archive):
    with tarfile.open(archive) as t:
        return {m.name for m in t.getmembers() if m.isfile()}


def test_backup_includes_every_wiki_in_the_packaged_layout(tmp_path, monkeypatch):
    _packaged_home(tmp_path, monkeypatch)
    res = bk.create_backup(include_model=False)
    assert {"wikis", "org_wiki"} <= set(res.includes)
    names = _names(res.path)
    for rel in _WIKI_FILES:
        assert f"data/{rel}" in names  # pages and the original uploads, personal, project and org


def test_restore_brings_back_lost_wikis(tmp_path, monkeypatch):
    home = _packaged_home(tmp_path, monkeypatch)
    res = bk.create_backup(include_model=False)

    shutil.rmtree(home / "wikis")
    shutil.rmtree(home / "org-wiki")
    rr = bk.restore_backup(res.path)

    assert {"wikis", "org_wiki"} <= set(rr.restored)
    for rel, body in _WIKI_FILES.items():
        assert (home / rel).read_text() == body


def test_restore_is_point_in_time_and_the_safety_copy_keeps_the_newer_wiki_page(
    tmp_path, monkeypatch
):
    home = _packaged_home(tmp_path, monkeypatch)
    res = bk.create_backup(include_model=False)
    newer = home / "wikis/user-1/wiki/newer.md"
    newer.write_text("# written after the backup\n")

    rr = bk.restore_backup(res.path)

    assert not newer.exists()  # the wiki rolls back with the database
    assert "data/wikis/user-1/wiki/newer.md" in _names(rr.safety_backup)  # and is not lost for good


def test_restoring_an_older_backup_leaves_the_wikis_alone(tmp_path, monkeypatch):
    home = _packaged_home(tmp_path, monkeypatch)
    wikis, org = home / "wikis", home / "org-wiki"
    held = tmp_path / "held"
    held.mkdir()
    wikis.rename(held / "wikis")
    org.rename(held / "org-wiki")
    old_style = bk.create_backup(tmp_path / "old.tar.gz", include_model=False)  # no wiki members
    (held / "wikis").rename(wikis)
    (held / "org-wiki").rename(org)

    rr = bk.restore_backup(old_style.path)

    assert "wikis" not in rr.restored and "org_wiki" not in rr.restored
    for rel, body in _WIKI_FILES.items():
        assert (home / rel).read_text() == body  # never wiped by an archive that has no wikis


def test_restore_rejects_a_non_backup(tmp_path, monkeypatch):
    _setup_data_dir(tmp_path, monkeypatch)
    junk = tmp_path / "not-a-backup.tar.gz"
    junk.write_bytes(b"not a tar")
    try:
        bk.restore_backup(junk)
        raised = False
    except Exception:
        raised = True
    assert raised  # never silently "restores" garbage over the data dir


def test_snapshot_db_keeps_last_n(tmp_path, monkeypatch):
    home = _setup_data_dir(tmp_path, monkeypatch)
    for i in range(5):
        bk.snapshot_db(reason=f"r{i}", keep=3)
    snaps = sorted((home / "snapshots").glob("anthill-*.db"))
    assert len(snaps) == 3  # pruned to the most recent 3
    # the secrets sidecar travels with each snapshot (so a snapshot is restorable)
    assert all(s.with_name(s.name + ".secrets.env").exists() for s in snaps)


def test_ollama_model_embedded_and_restored(tmp_path, monkeypatch):
    _setup_data_dir(tmp_path, monkeypatch)
    oll = tmp_path / "ollama"
    monkeypatch.setenv("OLLAMA_MODELS", str(oll))
    name = "org1-model-v2"
    man_dir = oll / "manifests" / "registry.ollama.ai" / "library" / name
    man_dir.mkdir(parents=True)
    (oll / "blobs").mkdir()
    (oll / "blobs" / "sha256-aaa").write_text("CONFIG")
    (oll / "blobs" / "sha256-bbb").write_text("WEIGHTS")
    (man_dir / "latest").write_text(
        json.dumps({"config": {"digest": "sha256:aaa"}, "layers": [{"digest": "sha256:bbb"}]})
    )

    res = bk.create_backup(include_model=True, model_names=[name])
    assert name in res.models and "ollama_models" in res.includes

    import shutil

    shutil.rmtree(oll)  # simulate losing the Ollama store
    rr = bk.restore_backup(res.path)
    assert name in rr.models
    assert (oll / "blobs" / "sha256-aaa").read_text() == "CONFIG"
    assert (oll / "blobs" / "sha256-bbb").read_text() == "WEIGHTS"
    assert (man_dir / "latest").exists()


def test_missing_model_does_not_abort_backup(tmp_path, monkeypatch):
    _setup_data_dir(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "empty-ollama"))
    res = bk.create_backup(include_model=True, model_names=["org1-model-v9"])
    assert res.models == [] and "db" in res.includes  # core backup still succeeds


def test_ensure_columns_snapshot_hook(tmp_path):
    """The migration hook fires once when columns are pending, and not when the schema is current."""
    from anthill.web.migrate import ensure_columns

    old = tmp_path / "old.db"
    con = sqlite3.connect(str(old))
    con.execute(
        "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR, slug VARCHAR, created_at DATETIME)"
    )
    con.execute(
        "CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id INTEGER, ollama_model VARCHAR)"
    )
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{old}")
    db.create_tables(engine)

    calls = []
    added = ensure_columns(engine, before_change=lambda cols: calls.append(list(cols)))
    assert added and len(calls) == 1 and calls[0]  # fired once, with the pending column names

    calls.clear()
    added2 = ensure_columns(engine, before_change=lambda cols: calls.append(cols))
    assert added2 == [] and calls == []  # nothing pending -> hook not called


def test_backup_page_renders_for_admin(tmp_path):
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
    r = c.get("/backup")
    assert r.status_code == 200 and "Backup" in r.text
