"""Mint a GitHub issue from an accepted proposal (ASDD public surface P4a). The issue carries the spec,
attribution, and reference code AS DATA (never a diff); minting is a human, admin-only, accepted-only
step, and a no-op when the contribution repo/token is not configured. No live GitHub calls (stubbed)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod

# ── the issue body (unit) ───────────────────────────────────────────────────────


def test_build_issue_body_reference_is_data_not_a_diff():
    from anthill.contribute import build_issue_body

    body = build_issue_body(
        spec="## Problem\nNo dark mode",
        kind="feature",
        proposer_handle="@grace",
        proposer_provider="x",
        reference_code="body { background: #000 }",
    )
    assert "## Problem" in body  # the spec
    assert "@grace" in body and "via x" in body  # attribution
    assert "reference only" in body.lower() and "NOT a diff" in body  # code framed as data
    assert "body { background: #000 }" in body  # the reference code is present, as data
    assert "diff --git" not in body and "```diff" not in body  # never a patch/diff
    assert "automated" in body.lower()  # the agent-authored disclosure


def test_build_issue_body_no_code_no_via_for_inapp():
    from anthill.contribute import build_issue_body

    body = build_issue_body(
        spec="s", kind="bug", proposer_handle="Ada", proposer_provider="inapp", reference_code=None
    )
    assert "Reference code" not in body  # no code section when none attached
    assert "via inapp" not in body  # in-app proposer isn't tagged with a provider


def test_contrib_repo_config_reads_env(monkeypatch):
    from anthill.contribute import contrib_repo_config

    monkeypatch.delenv("ANTHILL_CONTRIB_REPO", raising=False)
    monkeypatch.delenv("ANTHILL_CONTRIB_GITHUB_TOKEN", raising=False)
    assert contrib_repo_config() == (None, None)
    monkeypatch.setenv("ANTHILL_CONTRIB_REPO", "OneHillAI/Anthill")
    monkeypatch.setenv("ANTHILL_CONTRIB_GITHUB_TOKEN", "tok")
    assert contrib_repo_config() == ("OneHillAI/Anthill", "tok")


# ── the mint route ──────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch, status="accepted"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import ContributionProposal, Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.flush()
    p = ContributionProposal(
        org_id=org.id,
        created_by=member.id,
        source="board",
        proposer_provider="x",
        proposer_handle="@grace",
        kind="feature",
        title="Dark mode",
        idea="dark mode",
        spec="## Problem\nNo dark mode",
        status=status,
    )
    s.add(p)
    s.commit()
    ids = {"org": org.id, "admin": admin.id, "member": member.id, "p": p.id}
    admin_c = TestClient(app_mod.app)
    admin_c.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    member_c = TestClient(app_mod.app)
    member_c.cookies.set("session_token", make_token(member.id, org.id, "member"))
    return app_mod, admin_c, member_c, ids


def test_mint_creates_issue_when_configured(tmp_path, monkeypatch):
    from anthill.web.db import AuditLog, ContributionProposal

    app_mod, admin_c, _m, ids = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "anthill.contribute.contrib_repo_config", lambda: ("OneHillAI/Anthill", "tok")
    )
    captured = {}

    def fake_mint(repo, token, title, body, labels=None):
        captured.update(repo=repo, title=title, body=body, labels=labels)
        return {"number": 42, "url": "https://github.com/OneHillAI/Anthill/issues/42"}

    monkeypatch.setattr("anthill.contribute.mint_issue", fake_mint)
    r = admin_c.post(f"/contribute/{ids['p']}/mint", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/contribute?minted=1"
    p = (
        app_mod._SessionFactory()
        .query(ContributionProposal)
        .filter(ContributionProposal.id == ids["p"])
        .first()
    )
    assert p.status == "minted" and p.issue_number == 42 and "issues/42" in p.issue_url
    assert captured["repo"] == "OneHillAI/Anthill" and "@grace" in captured["body"]
    assert (
        app_mod._SessionFactory()
        .query(AuditLog)
        .filter(AuditLog.event == "contribution.minted")
        .count()
        == 1
    )


def test_mint_noop_when_unconfigured(tmp_path, monkeypatch):
    from anthill.web.db import ContributionProposal

    app_mod, admin_c, _m, ids = _app(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.contribute.contrib_repo_config", lambda: (None, None))
    r = admin_c.post(f"/contribute/{ids['p']}/mint", follow_redirects=False)
    assert r.headers["location"] == "/contribute?minted=unconfigured"
    p = (
        app_mod._SessionFactory()
        .query(ContributionProposal)
        .filter(ContributionProposal.id == ids["p"])
        .first()
    )
    assert p.status == "accepted" and p.issue_number is None  # unchanged, nothing posted


def test_mint_only_accepted(tmp_path, monkeypatch):
    _app_mod, admin_c, _m, ids = _app(tmp_path, monkeypatch, status="submitted")
    monkeypatch.setattr("anthill.contribute.contrib_repo_config", lambda: ("o/r", "tok"))
    r = admin_c.post(f"/contribute/{ids['p']}/mint", follow_redirects=False)
    assert r.headers["location"] == "/contribute?minted=notaccepted"


def test_mint_member_forbidden(tmp_path, monkeypatch):
    _app_mod, _admin_c, member_c, ids = _app(tmp_path, monkeypatch)
    assert member_c.post(f"/contribute/{ids['p']}/mint", follow_redirects=False).status_code == 403
