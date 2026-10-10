"""The org wiki belongs to its organisation (docs/specs/org-wiki-per-org.md).

The organisation of the first admin keeps the existing ``org-wiki`` folder in place (the owner is recorded in
the folder); every other organisation has its own folder under ``org-wikis``. Every org-scope route resolves the folder from the caller's organisation, so one
organisation can never read, list, export or write another's pages. Model-free.
"""

import io
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.wiki.workspace import (
    claim_legacy_org_wiki,
    legacy_org_wiki_owner,
    legacy_org_wiki_workspace,
    org_wiki_root,
    workspace_for,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org-wiki"))
    monkeypatch.delenv("ANTHILL_ORG_WIKIS", raising=False)
    monkeypatch.setenv(
        "ANTHILL_HOST", "127.0.0.1"
    )  # a desktop: sign-ups come from the machine itself
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path


# ── the path rules ──────────────────────────────────────────────────────────────


def test_the_owner_of_the_legacy_folder_keeps_it_and_other_organisations_get_their_own(env):
    legacy = env / "org-wiki"
    assert claim_legacy_org_wiki(3) is True
    assert legacy_org_wiki_owner() == 3
    assert org_wiki_root(3) == legacy
    assert org_wiki_root(4) == env / "org-wikis" / "org-4"
    assert org_wiki_root(5) == env / "org-wikis" / "org-5"
    assert org_wiki_root(4) != org_wiki_root(5)


def test_another_organisations_folder_is_never_inside_the_legacy_folder(env):
    """Exporting or backing up the legacy folder must not include another organisation's pages."""
    legacy = (env / "org-wiki").resolve()
    other = org_wiki_root(9).resolve()
    assert legacy not in other.parents and other != legacy


def test_the_owner_is_recorded_once_and_never_changed(env):
    assert claim_legacy_org_wiki(2) is True
    assert claim_legacy_org_wiki(7) is False
    assert legacy_org_wiki_owner() == 2


def test_before_an_owner_is_recorded_organisation_one_owns_the_legacy_folder(env):
    assert legacy_org_wiki_owner() is None
    assert org_wiki_root(1) == env / "org-wiki"
    assert org_wiki_root(2) == env / "org-wikis" / "org-2"


def test_the_org_scope_needs_an_organisation(env):
    """Leaving the organisation out would silently pick another organisation's folder, so it is an error."""
    with pytest.raises(ValueError):
        workspace_for("org")
    with pytest.raises(ValueError):
        workspace_for("org", user_id=3)
    assert legacy_org_wiki_workspace().root == env / "org-wiki"  # the explicit system accessor


def test_the_org_wikis_folder_can_be_moved_by_an_environment_variable(env, monkeypatch):
    monkeypatch.setenv("ANTHILL_ORG_WIKIS", str(env / "elsewhere"))
    assert org_wiki_root(4) == env / "elsewhere" / "org-4"


def test_only_org_scope_depends_on_the_organisation(env):
    assert workspace_for("team", team_id=5, org_id=1).root == workspace_for("team", team_id=5).root
    assert (
        workspace_for("personal", user_id=5, org_id=2).root
        == workspace_for("personal", user_id=5).root
    )


# Modules that build a Workspace straight from a path they were handed (a command-line path, a path the
# server chose for an agent run, another service) or that walk every organisation's folder on purpose.
_DIRECT_WORKSPACE_OK = {
    "cli.py",
    "agent/tools.py",
    "node_agent/app.py",
    "orchestrator/app.py",
    "wiki/workspace.py",
}


def calls_missing_org_id(root: Path) -> list[str]:
    """Every place under ``root`` that picks an organisation's wiki or skill folder without naming the
    organisation, found by walking each module's syntax tree (not by matching text):

    - ``workspace_for`` for the org scope or a scope that is not a literal, and ``write_skill`` with a
      scope, and ``run_wiki_workspace``: ``org_id`` must be given, not ``None`` and not hidden in ``**kwargs``;
    - ``org_wiki_root`` called with nothing or ``None``;
    - a ``Workspace(...)`` built directly, outside the modules that are allowed to.

    It checks calls. It does not look at code that reads the ``ANTHILL_ORG_WIKI`` variable itself (backup, the
    page index and the backend page do so on purpose).
    """
    import ast

    offenders: list[str] = []

    def org_id_given(call: ast.Call) -> bool:
        for k in call.keywords:
            if k.arg == "org_id":
                return not (isinstance(k.value, ast.Constant) and k.value.value is None)
        return False  # absent, or only inside **kwargs

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        in_workspace_module = rel == "wiki/workspace.py"
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else ""
            )
            where = f"{rel}:{node.lineno}"
            if name == "workspace_for" and not in_workspace_module:
                first = node.args[0] if node.args else None
                scope_kw = next((k.value for k in node.keywords if k.arg == "scope"), None)
                scope = first if first is not None else scope_kw
                literal = scope.value if isinstance(scope, ast.Constant) else None
                if literal in ("team", "personal"):
                    continue  # these scopes do not depend on the organisation
                if not org_id_given(node):
                    offenders.append(f"{where} workspace_for")
            elif name == "write_skill":
                scope_kw = next((k.value for k in node.keywords if k.arg == "scope"), None)
                if scope_kw is None or (
                    isinstance(scope_kw, ast.Constant) and scope_kw.value is None
                ):
                    continue  # a built-in skill: no organisation
                if not org_id_given(node):
                    offenders.append(f"{where} write_skill")
            elif name == "run_wiki_workspace" and not org_id_given(node):
                offenders.append(f"{where} run_wiki_workspace")
            elif name == "org_wiki_root" and not in_workspace_module:
                first = node.args[0] if node.args else None
                if first is None or (isinstance(first, ast.Constant) and first.value is None):
                    offenders.append(f"{where} org_wiki_root")
            elif name == "Workspace" and rel not in _DIRECT_WORKSPACE_OK:
                offenders.append(f"{where} Workspace")
    return offenders


def test_no_code_picks_an_org_folder_without_naming_the_organisation():
    assert calls_missing_org_id(Path(__file__).resolve().parents[1] / "anthill") == []


def test_the_guard_catches_each_bad_form_and_passes_the_good_ones(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "bad.py").write_text(
        "workspace_for('org')\n"  # 1 missing
        "workspace_for('org', org_id=None)\n"  # 2 explicit None
        "workspace_for('org', **kwargs)\n"  # 3 hidden in kwargs
        "workspace_for(scope, team_id=1)\n"  # 4 dynamic scope, missing
        "write_skill('a', scope='org')\n"  # 5 scoped skill write
        "run_wiki_workspace(db, plane_inf=p)\n"  # 6
        "org_wiki_root(None)\n"  # 7
        "org_wiki_root()\n"  # 8
        "Workspace(Path('x'))\n"  # 9 direct construction
        "ws.workspace_for('org')\n"  # 10 attribute call
    )
    (pkg / "good.py").write_text(
        "workspace_for('org', org_id=1)\n"
        "workspace_for('team', team_id=1)\n"
        "workspace_for('personal', user_id=1)\n"
        "workspace_for(scope, team_id=1, org_id=org.id)\n"
        "write_skill('a')\n"  # a built-in skill
        "write_skill('a', scope='org', org_id=1)\n"
        "run_wiki_workspace(db, plane_inf=p, org_id=1)\n"
        "org_wiki_root(org_id)\n"
    )
    found = calls_missing_org_id(pkg)
    assert [f.split(" ", 1)[1] for f in found] == [
        "workspace_for",
        "workspace_for",
        "workspace_for",
        "workspace_for",
        "write_skill",
        "run_wiki_workspace",
        "org_wiki_root",
        "org_wiki_root",
        "Workspace",
        "workspace_for",
    ]
    assert all(f.startswith("bad.py:") for f in found)


# ── two organisations through the real routes ───────────────────────────────────


def _setup(env):
    eng = create_engine(f"sqlite:///{env / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    first = Organization(name="First", slug="first")
    second = Organization(name="Second", slug="second")
    s.add_all([first, second])
    s.flush()
    a1 = User(org_id=first.id, email="a1@first.com", role="admin", active=True)
    a2 = User(org_id=second.id, email="a2@second.com", role="admin", active=True)
    m2 = User(org_id=second.id, email="m2@second.com", role="member", active=True)
    s.add_all([a1, a2, m2])
    s.commit()
    clients = {}
    for key, u in (("a1", a1), ("a2", a2), ("m2", m2)):
        c = TestClient(app_mod.app)
        c.cookies.set("session_token", make_token(u.id, u.org_id, u.role))
        clients[key] = c
    return app_mod, first.id, second.id, clients


def _page(org_id, slug, text):
    ws = workspace_for("org", org_id=org_id)
    ws.init()
    ws.write_page(slug, text)
    return ws


def test_a_second_organisation_cannot_read_the_first_organisations_pages(env):
    app_mod, first, _second, c = _setup(env)
    app_mod._claim_legacy_org_wiki()  # what start-up does: the first admin's org keeps the folder
    _page(first, "payroll", "# Payroll\nSalary bands for the first organisation\n")
    ws1 = workspace_for("org", org_id=first)
    (ws1.raw).mkdir(parents=True, exist_ok=True)
    (ws1.raw / "secret.txt").write_text("source document of the first organisation")

    assert c["a1"].get("/wiki/page/payroll?scope=org").status_code == 200
    assert c["a2"].get("/wiki/page/payroll?scope=org").status_code == 404
    assert c["m2"].get("/wiki/page/payroll?scope=org").status_code == 404
    assert c["a1"].get("/wiki/raw/secret.txt?scope=org").status_code == 200
    assert c["a2"].get("/wiki/raw/secret.txt?scope=org").status_code == 404


def test_the_org_pages_list_and_the_export_hold_only_the_callers_own_pages(env):
    app_mod, first, second, c = _setup(env)
    app_mod._claim_legacy_org_wiki()
    _page(first, "first-only", "# First only\nBelongs to the first organisation\n")
    _page(second, "second-only", "# Second only\nBelongs to the second organisation\n")

    listing = c["a2"].get("/wiki/org").text
    assert "second-only" in listing and "first-only" not in listing
    listing1 = c["a1"].get("/wiki/org").text
    assert "first-only" in listing1 and "second-only" not in listing1

    def exported(client):
        r = client.get("/wiki/export.okgf.tgz?scope=org")
        assert r.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            return (
                " ".join(tar.getnames())
                + " "
                + " ".join(
                    tar.extractfile(m).read().decode("utf-8", "replace")
                    for m in tar.getmembers()
                    if m.isfile()
                )
            )

    out2 = exported(c["a2"])
    assert "Second only" in out2 and "first organisation" not in out2
    out1 = exported(c["a1"])
    assert "First only" in out1 and "second organisation" not in out1


def test_a_page_written_by_the_second_organisation_never_lands_in_the_first_folder(env):
    app_mod, _first, second, _c = _setup(env)
    app_mod._claim_legacy_org_wiki()
    ws2 = _page(second, "mine", "# Mine\nsecond\n")
    assert str(ws2.root).startswith(str(env / "org-wikis"))
    assert not (env / "org-wiki" / "wiki" / "mine.md").exists()
    assert (env / "org-wikis" / f"org-{second}" / "wiki" / "mine.md").is_file()


def test_the_export_filename_and_scope_checks_still_apply(env):
    """A member (not an admin) still cannot export the org wiki, whichever organisation they are in."""
    _app_mod, _first, _second, c = _setup(env)
    assert c["m2"].get("/wiki/export.okgf.tgz?scope=org").status_code == 403


# ── an existing install keeps its pages ─────────────────────────────────────────


def test_a_migrated_install_keeps_its_pages_with_the_first_admins_organisation(env):
    """Pages written before this change sit in ``org-wiki``. After start-up the first admin's organisation
    owns that folder and still sees them; the second organisation sees nothing of them."""
    app_mod, first, _second, c = _setup(env)
    legacy = legacy_org_wiki_workspace()  # how the pages were written before: no organisation named
    legacy.init()
    legacy.write_page("handbook", "# Handbook\nOld page from before the change\n")

    assert legacy_org_wiki_owner() is None
    app_mod._claim_legacy_org_wiki()
    assert legacy_org_wiki_owner() == first

    assert c["a1"].get("/wiki/page/handbook?scope=org").status_code == 200
    assert "handbook" in c["a1"].get("/wiki/org").text
    assert c["a2"].get("/wiki/page/handbook?scope=org").status_code == 404
    assert "handbook" not in c["a2"].get("/wiki/org").text


def test_start_up_does_not_hand_the_folder_to_a_later_organisation(env):
    app_mod, first, _second, _c = _setup(env)
    app_mod._claim_legacy_org_wiki()
    app_mod._claim_legacy_org_wiki()
    assert legacy_org_wiki_owner() == first


def test_a_fresh_install_records_its_first_organisation_after_it_is_committed(env, monkeypatch):
    eng = create_engine(f"sqlite:///{env / 'f.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    import anthill.web.app as app_mod

    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    seen = []
    real_claim = app_mod._claim_legacy_org_wiki

    def spy():
        # What a separate connection sees at the moment the owner is recorded: the account is committed.
        other = app_mod._SessionFactory()
        seen.append(other.query(db_mod.Organization).count())
        other.close()
        real_claim()

    monkeypatch.setattr(app_mod, "_claim_legacy_org_wiki", spy)
    visitor = TestClient(app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = visitor.post(
        "/setup",
        data={"admin_email": "first@example.com", "admin_password": "longenoughpw1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert seen == [1]
    assert legacy_org_wiki_owner() == 1
    second = TestClient(
        app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    ).post(
        "/setup",
        data={"admin_email": "second@example.com", "admin_password": "longenoughpw1"},
        follow_redirects=False,
    )
    assert second.status_code in (302, 303)  # a second organisation really was created
    assert legacy_org_wiki_owner() == 1  # a later organisation never takes the folder


def test_the_owner_marker_is_created_exclusively_and_leaves_no_temporary_file(env, monkeypatch):
    assert claim_legacy_org_wiki(4) is True
    folder = env / "org-wiki"
    assert (folder / ".owner-org").read_text().strip() == "4"
    assert (
        list(folder.glob(".owner-org.*")) == []
    )  # the uniquely named temporary file is cleaned up

    # A second claim that slips in after the first one's check cannot overwrite the marker.
    real_owner = legacy_org_wiki_owner
    monkeypatch.setattr("anthill.wiki.workspace.legacy_org_wiki_owner", lambda: None)
    assert claim_legacy_org_wiki(9) is False
    monkeypatch.setattr("anthill.wiki.workspace.legacy_org_wiki_owner", real_owner)
    assert legacy_org_wiki_owner() == 4
    assert list(folder.glob(".owner-org.*")) == []


def test_two_claims_running_together_leave_exactly_one_owner(env):
    import threading

    results = []

    def claim(org_id):
        results.append(claim_legacy_org_wiki(org_id))

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert legacy_org_wiki_owner() in range(1, 9)
    assert list((env / "org-wiki").glob(".owner-org.*")) == []


def test_a_filesystem_without_hard_links_falls_back_to_an_exclusive_create(env, monkeypatch):
    import errno
    import os

    def no_links(src, dst):
        raise OSError(errno.EPERM, "hard links are not supported here")

    monkeypatch.setattr(os, "link", no_links)
    assert claim_legacy_org_wiki(6) is True
    assert legacy_org_wiki_owner() == 6
    assert claim_legacy_org_wiki(7) is False  # the fallback is exclusive too
    assert legacy_org_wiki_owner() == 6
    assert list((env / "org-wiki").glob(".owner-org.*")) == []


def test_an_empty_marker_left_by_a_crash_is_replaced_once_by_the_fallback(env, monkeypatch):
    import errno
    import os

    folder = env / "org-wiki"
    folder.mkdir()
    (folder / ".owner-org").write_text("")  # a crash between create and write
    monkeypatch.setattr(os, "link", lambda s, d: (_ for _ in ()).throw(OSError(errno.EPERM, "x")))
    assert claim_legacy_org_wiki(3) is True
    assert legacy_org_wiki_owner() == 3


def test_a_failure_to_record_the_owner_is_logged_and_not_raised(env, monkeypatch, caplog):
    import logging

    (env / "org-wiki").write_text("a file where the folder should be")
    with caplog.at_level(logging.WARNING):
        assert claim_legacy_org_wiki(5) is False
    assert "could not record the owner" in caplog.text
    assert legacy_org_wiki_owner() is None


def test_each_start_up_step_has_its_own_try_block():
    """The claim, the shared-wiki notice and the personal-wiki migration run in separate try blocks, so one
    failing never skips the next."""
    import inspect

    import anthill.web.app as app_mod

    src = inspect.getsource(app_mod._startup)
    steps = [
        "_claim_legacy_org_wiki()",
        "_note_shared_org_wiki_upgrade()",
        "_migrate_legacy_personal_wiki()",
    ]
    for step in steps:
        i = src.index(step)
        before = src[:i].rindex("try:")
        after = src.index("except", i)
        assert not any(other in src[before:after] for other in steps if other != step), step


# ── an upgraded install with several organisations ──────────────────────────────


def _admin_id(app_mod, org_id):
    s = app_mod._SessionFactory()
    try:
        return s.query(db_mod.User).filter(db_mod.User.org_id == org_id).first().id
    finally:
        s.close()


def _legacy(env):
    from anthill.wiki.workspace import legacy_org_wiki_workspace as legacy_ws

    ws = legacy_ws()
    ws.init()
    return ws


def test_on_an_upgraded_install_the_other_organisation_starts_with_an_empty_org_wiki(env):
    """Before the upgrade every organisation shared one org wiki folder. It stays with the organisation
    recorded as its owner; the other organisation starts empty and its new writes are separate. Nothing is
    moved or deleted."""
    app_mod, _first, second, c = _setup(env)
    legacy = _legacy(env)  # how the pages were written before: one shared folder
    legacy.write_page("handbook", "# Handbook\nWritten before the upgrade, by anyone.\n")
    legacy.write_page("roadmap", "# Roadmap\nAlso from before.\n")
    app_mod._claim_legacy_org_wiki()  # the first admin's organisation owns it
    app_mod._note_shared_org_wiki_upgrade()

    # the owner sees everything it saw before
    assert c["a1"].get("/wiki/page/handbook?scope=org").status_code == 200
    assert "handbook" in c["a1"].get("/wiki/org").text
    # the other organisation sees nothing from the old folder
    assert c["a2"].get("/wiki/page/handbook?scope=org").status_code == 404
    listing = c["a2"].get("/wiki/org").text
    assert "handbook" not in listing and "roadmap" not in listing
    export = c["a2"].get("/wiki/export.okgf.tgz?scope=org")
    assert b"Written before the upgrade" not in export.content

    # its new writes are its own, separate from the owner's
    ws2 = workspace_for("org", org_id=second)
    ws2.init()
    ws2.write_page("fresh", "# Fresh\nWritten after the upgrade by the second organisation.\n")
    assert c["a2"].get("/wiki/page/fresh?scope=org").status_code == 200
    assert c["a1"].get("/wiki/page/fresh?scope=org").status_code == 404
    assert str(ws2.root).startswith(str(env / "org-wikis"))

    # nothing was moved or deleted
    assert sorted(p.stem for p in legacy.pages()) == ["handbook", "roadmap"]


def test_a_fresh_install_never_claims_a_shared_folder_existed(env):
    """Two sign-ups on a new install, a page written, a restart: there was no shared folder before, so
    nobody gets a notice and no audit row is written (the rows would tell other tenants a page count)."""
    import anthill.web.app as app_mod
    from anthill.web import db as dbm
    from anthill.web.db import AuditLog

    eng = create_engine(f"sqlite:///{env / 'f.db'}", connect_args={"check_same_thread": False})
    dbm.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    client = TestClient(app_mod.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    for email in ("one@first.com", "two@second.com"):
        r = client.post(
            "/setup",
            data={"admin_email": email, "admin_password": "longenoughpw1"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.text[:200]
    s = app_mod._SessionFactory()
    assert s.query(dbm.Organization).count() == 2  # both sign-ups created an organisation
    first = s.query(dbm.Organization).order_by(dbm.Organization.id).first().id
    s.close()
    assert legacy_org_wiki_owner() == first
    _page(first, "handbook", "# Handbook\nWritten after the install, by the first organisation.\n")

    app_mod._note_shared_org_wiki_upgrade()  # what a restart does

    s = app_mod._SessionFactory()
    assert s.query(AuditLog).filter(AuditLog.event == "org_wiki.shared_before_upgrade").count() == 0
    s.close()
    assert (
        (legacy_org_wiki_workspace().root / app_mod.UPGRADE_NOTICE_MARKER)
        .read_text()
        .startswith("organisations=1 fresh=1")
    )
    for org_id in (first, first + 1):
        assert app_mod.org_wiki_upgrade_notice(org_id) is False


def test_an_empty_or_unreadable_owner_marker_is_replaced(env):
    legacy = env / "org-wiki"
    legacy.mkdir()
    (legacy / ".owner-org").write_text("")  # left by a crash
    assert legacy_org_wiki_owner() is None
    assert claim_legacy_org_wiki(4) is True
    assert legacy_org_wiki_owner() == 4
    (legacy / ".owner-org").write_text("not a number\n")
    assert legacy_org_wiki_owner() == 4 or legacy_org_wiki_owner() is None
    # a readable owner is never replaced
    (legacy / ".owner-org").write_text("4\n")
    assert claim_legacy_org_wiki(9) is False and legacy_org_wiki_owner() == 4


def test_the_notice_marker_is_created_before_the_audit_rows_and_only_once(env):
    from anthill.web.db import AuditLog

    app_mod, _first, _second, _c = _setup(env)
    legacy = _legacy(env)
    legacy.write_page("handbook", "# Handbook\nx\n")
    app_mod._claim_legacy_org_wiki()
    marker = legacy.root / app_mod.UPGRADE_NOTICE_MARKER
    assert marker.exists()
    before = marker.read_text()
    app_mod._note_shared_org_wiki_upgrade()
    app_mod._note_shared_org_wiki_upgrade()
    assert marker.read_text() == before
    s = app_mod._SessionFactory()
    assert s.query(AuditLog).filter(AuditLog.event == "org_wiki.shared_before_upgrade").count() == 2
    s.close()


def test_the_owners_admin_is_told_in_plain_words_and_nobody_else_is(env):
    app_mod, _first, _second, c = _setup(env)
    legacy = _legacy(env)
    legacy.write_page("handbook", "# Handbook\nx\n")
    app_mod._claim_legacy_org_wiki()
    app_mod._note_shared_org_wiki_upgrade()
    owner_page = c["a1"].get("/wiki/org").text
    assert "every organisation on this install used the same organisation wiki" in owner_page
    assert "Nothing was deleted" in owner_page
    other_page = c["a2"].get("/wiki/org").text
    assert "used the same organisation wiki" not in other_page


def test_one_audit_row_per_organisation_with_its_id_and_counts_only(env, caplog):
    import logging

    from anthill.web.db import AuditLog

    app_mod, first, second, _c = _setup(env)
    legacy = _legacy(env)
    legacy.write_page("secret-merger-plan", "# Secret merger plan\nx\n")
    legacy.write_page("handbook", "# Handbook\ny\n")
    app_mod._claim_legacy_org_wiki()
    with caplog.at_level(logging.INFO):
        app_mod._note_shared_org_wiki_upgrade()
    s = app_mod._SessionFactory()
    rows = (
        s.query(AuditLog)
        .filter(AuditLog.event == "org_wiki.shared_before_upgrade")
        .order_by(AuditLog.org_id)
        .all()
    )
    s.close()
    assert [r.org_id for r in rows] == [first, second]  # one per organisation, each with its id
    assert "role=owner" in rows[0].detail and "role=other" in rows[1].detail
    assert all("pages_in_the_previous_shared_folder=2" in r.detail for r in rows)
    everything = " ".join(r.detail for r in rows) + caplog.text
    assert "secret-merger-plan" not in everything and "handbook" not in everything  # no page names


def test_the_notice_and_the_audit_rows_happen_once(env):
    from anthill.web.db import AuditLog

    app_mod, _first, _second, _c = _setup(env)
    _legacy(env).write_page("handbook", "# Handbook\nx\n")
    app_mod._claim_legacy_org_wiki()
    for _ in range(3):
        app_mod._note_shared_org_wiki_upgrade()
    s = app_mod._SessionFactory()
    assert s.query(AuditLog).filter(AuditLog.event == "org_wiki.shared_before_upgrade").count() == 2
    s.close()


def test_a_one_organisation_install_gets_no_notice_and_only_two_small_files(env):
    """The upgrade of an install with a single organisation: every page stays where it is, nobody is told
    anything, and the only additions are the owner marker and the note that the check ran."""
    eng = create_engine(f"sqlite:///{env / 'one.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    import anthill.web.app as app_mod
    from anthill.web.db import AuditLog, Organization, User

    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Only", slug="only")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="a@only.com", role="admin", active=True))
    s.commit()
    org_id = org.id
    s.close()
    legacy = _legacy(env)
    legacy.write_page("kept", "# Kept\nx\n")
    (legacy.root / ".cache").mkdir()
    (legacy.root / ".cache" / "answers.db").write_text("x")
    before = sorted(p.name for p in legacy.root.iterdir())

    app_mod._claim_legacy_org_wiki()
    app_mod._note_shared_org_wiki_upgrade()
    app_mod._note_shared_org_wiki_upgrade()

    after = sorted(p.name for p in legacy.root.iterdir())
    assert sorted(set(after) - set(before)) == [".owner-org", ".upgrade-notice"]
    assert set(before) <= set(after) and (legacy.wiki / "kept.md").is_file()
    assert (legacy.root / ".cache" / "answers.db").exists()
    assert not (env / "org-wikis").exists()
    assert app_mod.org_wiki_upgrade_notice(org_id) is False
    s = app_mod._SessionFactory()
    assert s.query(AuditLog).filter(AuditLog.event == "org_wiki.shared_before_upgrade").count() == 0
    s.close()


def test_an_organisation_created_after_the_upgrade_is_not_told_anything(env):
    """The notice is about what happened at the upgrade, so a second organisation that appears later (the
    check ran with one organisation) gets no notice and no audit row."""
    from anthill.web.db import AuditLog

    app_mod, _first, second, _c = _setup(env)
    s = app_mod._SessionFactory()
    s.query(db_mod.User).filter(db_mod.User.org_id == second).delete()
    s.query(db_mod.Organization).filter(db_mod.Organization.id == second).delete()
    s.commit()
    s.close()
    _legacy(env).write_page("handbook", "# Handbook\nx\n")
    app_mod._claim_legacy_org_wiki()
    app_mod._note_shared_org_wiki_upgrade()  # one organisation: the check records that it ran
    s = app_mod._SessionFactory()
    later = db_mod.Organization(name="Later", slug="later")
    s.add(later)
    s.commit()
    s.close()
    app_mod._note_shared_org_wiki_upgrade()
    s = app_mod._SessionFactory()
    assert s.query(AuditLog).filter(AuditLog.event == "org_wiki.shared_before_upgrade").count() == 0
    s.close()


# ── the gallery route writes into the caller's own organisation ─────────────────


def test_adopting_a_gallery_skill_for_the_org_writes_into_that_organisations_folder(
    env, monkeypatch
):
    app_mod, _first, second, c = _setup(env)
    app_mod._claim_legacy_org_wiki()
    monkeypatch.setattr(
        app_mod, "_propose_scoped", lambda *a, **k: True
    )  # a clean write: applied now
    r = c["a2"].post(
        "/skills/gallery/brand-guidelines/adopt", data={"scope": "org"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert (
        env / "org-wikis" / f"org-{second}" / "skills" / "brand-guidelines" / "SKILL.md"
    ).is_file()
    assert not (
        env / "org-wiki" / "skills" / "brand-guidelines"
    ).exists()  # the owner's folder is untouched
    r = c["a1"].post(
        "/skills/gallery/brand-guidelines/adopt", data={"scope": "org"}, follow_redirects=False
    )
    assert (env / "org-wiki" / "skills" / "brand-guidelines" / "SKILL.md").is_file()


# ── other readers of the org wiki ───────────────────────────────────────────────


def test_the_page_index_covers_every_organisations_folder(env):
    from anthill.wiki.page_index import workspace_roots

    _page(1, "a", "# A\nx\n")
    _page(2, "b", "# B\ny\n")
    roots = {r.resolve() for r in workspace_roots()}
    assert (env / "org-wiki").resolve() in roots
    assert (env / "org-wikis" / "org-2").resolve() in roots


def _data_home(env, monkeypatch):
    import sqlite3

    home = env / "home"
    home.mkdir()
    for key, sub in (
        ("ANTHILL_HOME", ""),
        ("ANTHILL_WORKSPACE", "workspace"),
        ("ANTHILL_FILES_DIR", "files"),
        ("ANTHILL_SKILLS_DIR", "skills"),
        ("ANTHILL_WIKI_ROOT", "wikis"),
        ("ANTHILL_ORG_WIKI", "org-wiki"),
    ):
        monkeypatch.setenv(key, str(home / sub) if sub else str(home))
    monkeypatch.setenv("ANTHILL_DB", str(home / "anthill.db"))
    con = sqlite3.connect(str(home / "anthill.db"))
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    (home / "secrets.env").write_text("ANTHILL_ENCRYPTION_KEY=abc\n")
    return home


def test_a_backup_is_the_whole_install_and_a_restore_brings_back_every_organisations_wiki(
    env, monkeypatch
):
    from anthill import backup

    home = _data_home(env, monkeypatch)
    monkeypatch.delenv("ANTHILL_ORG_WIKIS", raising=False)
    _page(1, "one", "# One\nfirst\n")
    _page(2, "two", "# Two\nsecond\n")
    res = backup.create_backup(include_model=False)
    assert {"org_wiki", "org_wikis"} <= set(res.includes)

    import shutil

    shutil.rmtree(home / "org-wiki")
    shutil.rmtree(home / "org-wikis")
    rr = backup.restore_backup(res.path)
    assert {"org_wiki", "org_wikis"} <= set(rr.restored)
    assert "first" in (home / "org-wiki" / "wiki" / "one.md").read_text()
    assert "second" in (home / "org-wikis" / "org-2" / "wiki" / "two.md").read_text()


def test_restoring_an_archive_from_before_org_wikis_moves_the_current_org_wikis_aside(
    env, monkeypatch
):
    import tarfile

    from anthill import backup

    home = _data_home(env, monkeypatch)
    _page(1, "one", "# One\nfirst\n")
    res = backup.create_backup(include_model=False)
    # An archive made before the change has no org-wikis tree.
    old = env / "old.tar.gz"
    with tarfile.open(res.path) as src, tarfile.open(old, "w:gz") as dst:
        for m in src.getmembers():
            if m.name.startswith("data/org-wikis"):
                continue
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    _page(2, "two", "# Two\nsecond, written after the archive\n")

    rr = backup.restore_backup(old)
    assert "org_wiki" in rr.restored and "org_wikis_moved_aside" in rr.restored
    assert rr.moved_aside is not None and rr.moved_aside.name.startswith(
        "org-wikis.before-restore-"
    )
    assert (
        rr.moved_aside / "org-2" / "wiki" / "two.md"
    ).is_file()  # named in the result, kept intact
    assert not (home / "org-wikis").exists()
    aside = [p for p in home.iterdir() if p.name.startswith("org-wikis.before-restore-")]
    assert (
        len(aside) == 1 and (aside[0] / "org-2" / "wiki" / "two.md").is_file()
    )  # kept, not deleted


def test_the_data_paths_include_the_per_organisation_folder(env, monkeypatch):
    from anthill import backup

    home = _data_home(env, monkeypatch)
    monkeypatch.delenv("ANTHILL_ORG_WIKIS", raising=False)
    assert backup.data_paths()["org_wikis"] == home / "org-wikis"
    assert ("org_wikis", "data/org-wikis") in backup._TREES


def test_the_backend_page_shows_the_folder_of_the_callers_organisation(env):
    app_mod, _first, second, c = _setup(env)
    app_mod._claim_legacy_org_wiki()
    one = c["a1"].get("/backend")
    two = c["a2"].get("/backend")
    assert one.status_code == 200 and two.status_code == 200
    assert str(env / "org-wiki") in one.text
    assert str(env / "org-wikis" / f"org-{second}") in two.text
    assert str(env / "org-wikis" / f"org-{second}") not in one.text


def test_the_backup_page_names_a_folder_that_a_restore_moved_aside(env):
    _app_mod, _first, _second, c = _setup(env)
    page = c["a1"].get("/backup?restored=1&aside=org-wikis.before-restore-20261010-021500").text
    assert "org-wikis.before-restore-20261010-021500" in page and "nothing deleted" in page
    assert "org-wikis.before-restore" not in c["a1"].get("/backup").text
