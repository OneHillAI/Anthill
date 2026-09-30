"""Regression tests for the security fixes in fix/security-c1-c2-h6-idor.

C1  read_file is confined to the agent's own files dir (no arbitrary local read -> no secret exfil).
H1  created files are org-scoped; a download never crosses tenants.
H2  /tasks/{id}/cancel and /run-now are org-scoped (no cross-tenant task control).
C2  the Lambda/vLLM serving endpoint requires an API key (never keyless).
H6  the direct web fetch refuses internal / SSRF targets.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod

# ── C1: read_file confinement ─────────────────────────────────────────────────


def test_c1_read_file_confined_to_base_dir(tmp_path):
    from anthill.agent import tools

    base = tmp_path / "files"
    base.mkdir()
    (base / "ok.txt").write_text("hello world")
    (tmp_path / "secret.env").write_text("ANTHILL_JWT_SECRET=leak")

    # a plain basename inside the dir reads fine
    assert "hello world" in tools._read_file("ok.txt", base_dir=base)
    # traversal, absolute, and parent-dir paths are all refused (they resolve outside base)
    for evil in ("../secret.env", "/etc/passwd", str(tmp_path / "secret.env"), "../../.env"):
        assert "access denied" in tools._read_file(evil, base_dir=base), evil
    # a missing basename is a clean not-found, not an error leak
    assert "no such file" in tools._read_file("nope.txt", base_dir=base)


def test_c1_make_tools_binds_read_file_to_owner(tmp_path, monkeypatch):
    from anthill.agent import tools

    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path))
    (tmp_path / "leak.env").write_text("SECRET")
    read_file = {t.name: t for t in tools.make_tools(owner="7")}["read_file"].fn
    # the owner's dir is <FILES_DIR>/7 - the agent cannot escape it to read ../leak.env or /etc/*
    assert "access denied" in read_file("../leak.env")
    assert "access denied" in read_file("/etc/hosts")


# ── H1: created files are org-scoped ──────────────────────────────────────────


def test_h1_files_owner_key():
    from anthill.web import app as webapp

    assert webapp._files_owner({"org": 7}) == "7"
    assert webapp._files_owner({}) == "shared"


def test_h1_files_target_does_not_cross_owners(tmp_path, monkeypatch):
    from anthill.web import app as webapp

    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path))
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "report-abcd1234.pdf").write_text("org-1 only")

    assert webapp._files_target("report-abcd1234.pdf", "1") is not None  # owner sees own file
    assert webapp._files_target("report-abcd1234.pdf", "2") is None  # another org: not served
    assert webapp._files_target("../1/report-abcd1234.pdf", "2") is None  # traversal refused


# ── Within-org per-user file isolation (closes the #432 residual) ─────────────


def test_files_owner_is_per_user_within_an_org():
    from anthill.agent.tools import files_owner
    from anthill.web import app as webapp

    # per-user key = org + user; two members of the SAME org never share an owner dir
    assert files_owner(7, 5) == "7-u5"
    assert files_owner(7, 5) != files_owner(7, 9)
    assert files_owner(7) == "7"  # no end-user (e.g. a2a) -> org-only fallback, unchanged
    a = webapp._files_owner({"org": 7, "sub": "5"})
    b = webapp._files_owner({"org": 7, "sub": "9"})
    assert a == "7-u5" and a != b


def test_within_org_user_cannot_read_another_users_file(tmp_path, monkeypatch):
    from anthill.agent.tools import _files_dir
    from anthill.web import app as webapp

    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path))
    user_a, user_b = {"org": 7, "sub": "5"}, {"org": 7, "sub": "9"}
    fa = _files_dir(webapp._files_owner(user_a)) / "report-abcd1234.pdf"
    fa.write_text("A's private file")
    # A sees its own file; B (same org, different user) does NOT - the residual is closed
    assert webapp._files_target("report-abcd1234.pdf", webapp._files_owner(user_a)) is not None
    assert webapp._files_target("report-abcd1234.pdf", webapp._files_owner(user_b)) is None


# ── H2/H1 end-to-end via the app (two orgs) ───────────────────────────────────


def _two_org_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    ids = {}
    for tag in ("a", "b"):
        org = Organization(name=tag.upper(), slug=tag)
        s.add(org)
        s.flush()
        member = User(org_id=org.id, email=f"m@{tag}.com", role="member", active=True)
        s.add(member)
        s.flush()
        ids[tag] = {"org": org.id, "member": member.id}
    task = db_mod.ScheduledTask(org_id=ids["a"]["org"], title="A task", goal="do a thing")
    s.add(task)
    s.commit()
    ids["task_id"] = task.id
    return TestClient(app_mod.app), app_mod, ids


def _auth(client, uid, org_id):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, "member"))


def test_h1_download_is_not_cross_tenant(tmp_path, monkeypatch):
    client, _app_mod, ids = _two_org_app(tmp_path, monkeypatch)

    _auth(client, ids["a"]["member"], ids["a"]["org"])
    r = client.post("/chat/download", data={"content": "org A secret", "format": "md"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert client.get(url).status_code == 200  # org A can download its own file

    _auth(client, ids["b"]["member"], ids["b"]["org"])  # switch to the other org
    assert client.get(url).status_code == 404  # org B cannot download org A's file


def test_h2_task_control_is_org_scoped(tmp_path, monkeypatch):
    client, app_mod, ids = _two_org_app(tmp_path, monkeypatch)
    task_id = ids["task_id"]

    _auth(client, ids["b"]["member"], ids["b"]["org"])  # a DIFFERENT org's user
    client.post(f"/tasks/{task_id}/cancel", follow_redirects=False)
    client.post(f"/tasks/{task_id}/run-now", follow_redirects=False)

    s = app_mod._SessionFactory()
    task = s.get(db_mod.ScheduledTask, task_id)
    assert task.status != "cancelled"  # org B could not cancel org A's task
    assert task.status != "pending" or task.next_run_at is None  # nor force-run it


# ── C2: Lambda vLLM endpoint requires a key ───────────────────────────────────


def test_c2_vllm_startup_script_sets_api_key():
    from anthill.hosting.lambda_provision import vllm_startup_script

    script = vllm_startup_script("qwen2.5:3b", "s3cret-key")
    assert "--api-key 's3cret-key'" in script  # the server is no longer keyless
    assert "--model 'qwen2.5:3b'" in script


# ── H6: SSRF guard on the direct web fetch ────────────────────────────────────


def test_h6_host_public_guard():
    from anthill.search.web import _host_is_public

    assert _host_is_public("8.8.8.8") is True  # a public, routable IP
    for internal in ("127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "::1", ""):
        assert _host_is_public(internal) is False, internal


def test_h6_fetch_direct_refuses_internal_targets():
    from anthill.search import web

    for url in (
        "http://169.254.169.254/latest/meta-data/",  # cloud instance metadata
        "http://127.0.0.1:8000/",
        "http://localhost/admin",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            web._fetch_direct(url)


# ── SSRF DNS-rebinding: the connection is pinned to the validated IP ───────────


def _fake_getaddrinfo(*ips):
    return lambda host, *a, **k: [(2, 1, 6, "", (ip, 0)) for ip in ips]


def test_ssrf_resolve_pinned_rejects_private(monkeypatch):
    import socket

    from anthill.search import web

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(ValueError):
        web._resolve_pinned("metadata.evil.test")


def test_ssrf_resolve_pinned_rejects_mixed_public_private(monkeypatch):
    import socket

    from anthill.search import web

    # a public AND a private answer -> refuse (a rebind could hand back either)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8", "10.0.0.5"))
    with pytest.raises(ValueError):
        web._resolve_pinned("rebind.evil.test")


def test_ssrf_resolve_pinned_returns_public_ip(monkeypatch):
    import socket

    from anthill.search import web

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    assert web._resolve_pinned("dns.google") == "8.8.8.8"


def test_ssrf_safe_get_connects_to_the_pinned_ip(monkeypatch):
    """_safe_get resolves + validates ONCE and connects to that pinned IP; it never lets the client
    re-resolve the hostname (which is where DNS rebinding lives)."""
    import socket

    from anthill.search import web

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    seen = {}

    class _Resp:
        is_redirect = False

        @property
        def headers(self):
            return {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return _Resp()

    def _pinned(ip, timeout):
        seen["ip"] = ip
        return _Client()

    monkeypatch.setattr(web, "_pinned_client", _pinned)
    web._safe_get("https://example.com/x", timeout=5, headers={})
    assert seen["ip"] == "93.184.216.34"  # connected to the validated IP, not a fresh re-resolution
