"""The personal wiki is per-user (user-<id>). Plain chat used to read the single-node
ANTHILL_WORKSPACE while agent mode read user-<id>, so the same user saw different personal wikis.
Plain chat now resolves per-user too; this one-time migration folds an existing install's legacy
ANTHILL_WORKSPACE pages into the creator/admin's per-user wiki so nothing disappears from chat.
"""

from anthill.wiki.workspace import Workspace, migrate_legacy_personal_wiki, workspace_for


def _legacy_and_root(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-workspace"
    root = tmp_path / "wikis"
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(legacy))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(root))
    return legacy, root


def test_personal_scope_resolves_per_user_vs_single_node(tmp_path, monkeypatch):
    legacy, root = _legacy_and_root(tmp_path, monkeypatch)
    # no user -> the single-node default; a user -> user-<id> under the wiki root
    assert workspace_for("personal").root == legacy
    assert workspace_for("personal", user_id=7).root == root / "user-7"


def test_migration_copies_legacy_pages_into_the_per_user_wiki(tmp_path, monkeypatch):
    _legacy_and_root(tmp_path, monkeypatch)
    legacy_ws = workspace_for("personal")  # ANTHILL_WORKSPACE
    legacy_ws.init()
    legacy_ws.write_page("Refund policy", "# Refund policy\n\nThe refund window is 45 days.\n")

    copied = migrate_legacy_personal_wiki(7)
    assert copied == 1

    target = workspace_for("personal", user_id=7)
    page = target.wiki / "refund-policy.md"
    assert page.exists()
    assert "45 days" in page.read_text()


def test_migration_is_idempotent_and_leaves_the_legacy_dir_intact(tmp_path, monkeypatch):
    legacy, _root = _legacy_and_root(tmp_path, monkeypatch)
    legacy_ws = workspace_for("personal")
    legacy_ws.init()
    legacy_ws.write_page("Note", "# Note\n\nKeep me.\n")

    assert migrate_legacy_personal_wiki(7) == 1
    assert (legacy / ".personal-migrated").exists()  # marker dropped
    assert (legacy_ws.wiki / "note.md").exists()  # copy, not move: legacy still has it
    # a second run does nothing (the marker short-circuits it)
    assert migrate_legacy_personal_wiki(7) == 0


def test_migration_never_overwrites_an_existing_per_user_page(tmp_path, monkeypatch):
    _legacy_and_root(tmp_path, monkeypatch)
    legacy_ws = workspace_for("personal")
    legacy_ws.init()
    legacy_ws.write_page("Note", "# Note\n\nLEGACY version.\n")
    # the user already has a per-user page with the same slug - it must win
    target = workspace_for("personal", user_id=7)
    target.init()
    target.write_page("Note", "# Note\n\nPER-USER version.\n")

    assert migrate_legacy_personal_wiki(7) == 0  # nothing copied (would have clobbered)
    assert "PER-USER version" in (target.wiki / "note.md").read_text()


def test_migration_is_a_noop_when_there_are_no_legacy_pages(tmp_path, monkeypatch):
    legacy, _root = _legacy_and_root(tmp_path, monkeypatch)
    Workspace(legacy, scope="personal").init()  # initialised but empty (no pages)
    assert migrate_legacy_personal_wiki(7) == 0
    assert (legacy / ".personal-migrated").exists()  # still marks itself done
