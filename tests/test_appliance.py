"""Mac always-on appliance: GPU/session detection, LaunchAgent plist, LAN URL, status.

Pure helpers are tested directly with injected boundaries (no host calls); the routes are
tested through the app with an admin client. Nothing here touches launchd or pmset.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.hosting import appliance
from anthill.web import db as db_mod

# ── network ─────────────────────────────────────────────────────────────────────


def test_lan_url_with_injected_ip():
    assert appliance.lan_url(8000, ip="192.168.1.50") == "http://192.168.1.50:8000"
    assert appliance.lan_url(8000, ip="") == ""  # no address detected -> empty
    assert appliance.primary_lan_ip(resolve=lambda: "10.0.0.4") == "10.0.0.4"


# ── GPU / session ───────────────────────────────────────────────────────────────


def test_is_apple_silicon():
    assert appliance.is_apple_silicon(system="Darwin", machine="arm64") is True
    assert appliance.is_apple_silicon(system="Darwin", machine="x86_64") is False
    assert appliance.is_apple_silicon(system="Linux", machine="arm64") is False


def test_session_kind_reads_launchctl():
    assert appliance.session_kind(runner=lambda cmd: "Aqua\n") == "Aqua"


def test_gpu_metal_only_in_a_gui_session():
    # Apple Silicon + a logged-in (Aqua) session -> Metal/GPU.
    g = appliance.gpu_backend(apple=True, session="Aqua")
    assert g.backend == "metal" and g.accelerated is True

    # Apple Silicon but no GUI session (a daemon / ssh) -> CPU, with the fix spelled out.
    g = appliance.gpu_backend(apple=True, session="Background")
    assert g.backend == "cpu" and g.accelerated is False
    assert "auto-login" in g.detail and "LaunchAgent" in g.detail

    # Not Apple Silicon -> CPU, no Metal.
    g = appliance.gpu_backend(apple=False)
    assert g.backend == "cpu" and g.accelerated is False


# ── LaunchAgent ─────────────────────────────────────────────────────────────────


def test_web_server_args_binds_all_interfaces():
    assert appliance.web_server_args(port=8000) == [
        "anthill",
        "web",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]


def test_launchagent_plist_shape():
    plist = appliance.launchagent_plist(
        program_args=appliance.web_server_args(port=8000),
        stdout_path="/tmp/anthill.log",
        env={"ANTHILL_DB": "/data/anthill.db"},
    )
    assert plist.startswith("<?xml")
    assert plist.endswith("\n")
    assert f"<string>{appliance.LAUNCHAGENT_LABEL}</string>" in plist
    assert "<string>0.0.0.0</string>" in plist
    assert "<key>RunAtLoad</key>\n  <true/>" in plist
    assert "<key>KeepAlive</key>\n  <true/>" in plist  # restarts on crash + at boot
    assert "<string>/tmp/anthill.log</string>" in plist
    assert "<key>ANTHILL_DB</key>" in plist


def test_launchagent_plist_escapes_xml():
    plist = appliance.launchagent_plist(program_args=["anthill", "web", "--note", "a & b <x>"])
    assert "a &amp; b &lt;x&gt;" in plist
    assert "a & b <x>" not in plist


def test_launchagent_path_uses_home():
    p = appliance.launchagent_path(home="/Users/anthill")
    assert p == "/Users/anthill/Library/LaunchAgents/org.onehill.anthill.plist"


def test_is_service_loaded_parses_launchctl_list():
    out = "PID\tStatus\tLabel\n123\t0\torg.onehill.anthill\n-\t0\tcom.apple.other\n"
    assert appliance.is_service_loaded(runner=lambda cmd: out) is True
    assert appliance.is_service_loaded("com.nope", runner=lambda cmd: out) is False
    assert appliance.is_service_loaded(runner=lambda cmd: "PID\tStatus\tLabel\n") is False


# ── status assembly ─────────────────────────────────────────────────────────────


def test_appliance_status_assembles_without_host_calls():
    st = appliance.appliance_status(
        port=8000,
        model="qwen2.5:32b",
        tunnel_url="https://x.trycloudflare.com",
        home="/Users/anthill",
        lan_ip="192.168.1.50",
        apple=True,
        session="Aqua",
        runner=lambda cmd: "",  # launchctl list -> nothing loaded
    )
    assert st.is_apple_silicon is True
    assert st.gpu.accelerated is True
    assert st.lan_url == "http://192.168.1.50:8000"
    assert st.tunnel_url == "https://x.trycloudflare.com"
    assert st.model == "qwen2.5:32b"
    assert st.service_loaded is False
    assert st.plist_path.endswith("/Library/LaunchAgents/org.onehill.anthill.plist")


# ── routes ──────────────────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "admin": admin.id, "member": member.id}


def _auth(client, uid, org_id, role="admin"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_appliance_page_renders(tmp_path, monkeypatch):
    client, _m, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings/appliance")
    assert r.status_code == 200
    body = r.text
    assert "Always-on appliance" in body
    assert "LaunchAgent" in body
    assert "pmset" in body  # the power-settings recipe is shown


def test_appliance_plist_download(tmp_path, monkeypatch):
    client, _m, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["admin"], ids["org"])
    r = client.get("/settings/appliance/launchagent.plist")
    assert r.status_code == 200
    assert "org.onehill.anthill.plist" in r.headers.get("content-disposition", "")
    assert r.text.startswith("<?xml")
    assert "<string>0.0.0.0</string>" in r.text


def test_appliance_requires_admin(tmp_path, monkeypatch):
    client, _m, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["member"], ids["org"], role="member")
    assert client.get("/settings/appliance", follow_redirects=False).status_code == 403
    assert (
        client.get("/settings/appliance/launchagent.plist", follow_redirects=False).status_code
        == 403
    )


# ── installer: sizing ────────────────────────────────────────────────────────────


def test_total_memory_gb_reads_sysctl():
    thirty_two = str(32 * 1024**3)
    assert round(appliance.total_memory_gb(runner=lambda cmd: thirty_two + "\n")) == 32


def test_recommended_ollama_tag_sizes_to_ram():
    # smartest that fits a 32 GB Mac mini (a 30B-class MoE beats a bigger-but-duller dense model)
    assert appliance.recommended_ollama_tag(32) == "glm-4.7-flash"
    assert appliance.recommended_ollama_tag(0) == "qwen3.5:0.8b"  # unknown RAM -> safe default


# ── installer: file + launchctl boundaries ───────────────────────────────────────


def test_write_launchagent_writes_file(tmp_path):
    path = str(tmp_path / "LaunchAgents" / "org.onehill.anthill.plist")
    out = appliance.write_launchagent("<plist/>", path=path)
    assert out == path
    with open(path) as fh:
        assert fh.read() == "<plist/>"


def test_load_launchagent_unloads_then_loads():
    calls = []
    appliance.load_launchagent("/x.plist", runner=lambda cmd: calls.append(cmd) or "")
    assert calls == [
        ["launchctl", "unload", "/x.plist"],
        ["launchctl", "load", "/x.plist"],
    ]


def test_apply_power_settings_runs_each_pmset():
    seen = []
    res = appliance.apply_power_settings(runner=lambda cmd: seen.append(cmd) or 0)
    assert len(res) == len(appliance.PMSET_SETTINGS)
    assert all(ok for _cmd, ok in res)
    assert all(c[:2] == ["sudo", "pmset"] for c in seen)


# ── installer: full orchestration (all side effects injected) ────────────────────


def test_install_appliance_writes_loads_pulls_and_reports():
    written = {}
    launchctl = []
    pulled = []
    res = appliance.install_appliance(
        port=8000,
        home="/Users/anthill",
        mem_gb=32,  # -> sizes to glm-4.7-flash
        lan_ip="192.168.1.50",
        apple=True,
        session="Aqua",
        writer=lambda p, t: written.update({p: t}),
        runner=lambda cmd: launchctl.append(cmd) or "",
        puller=lambda tag: pulled.append(tag) or True,
    )
    assert res.model == "glm-4.7-flash"
    assert res.pulled is True and pulled == ["glm-4.7-flash"]
    assert res.lan_url == "http://192.168.1.50:8000"
    assert res.gpu.accelerated is True
    # the plist was written to the right path and binds all interfaces
    path = "/Users/anthill/Library/LaunchAgents/org.onehill.anthill.plist"
    assert path in written and "<string>0.0.0.0</string>" in written[path]
    assert ["launchctl", "load", path] in launchctl
    # power not applied -> the pmset + auto-login + FileVault steps are surfaced
    joined = " ".join(res.next_steps)
    assert "pmset" in joined and "auto-login" in joined.lower() and "FileVault" in joined
    assert res.power_applied is False


def test_install_appliance_apply_power_and_explicit_model():
    res = appliance.install_appliance(
        port=8000,
        home="/Users/anthill",
        model="qwen2.5:14b",
        pull=False,
        apply_power=True,
        apple=True,
        session="Aqua",
        writer=lambda p, t: None,
        runner=lambda cmd: "",
        power_runner=lambda cmd: 0,  # pmset succeeds
    )
    assert res.model == "qwen2.5:14b" and res.pulled is False
    assert res.power_applied is True
    assert all("pmset" not in s for s in res.next_steps)  # already applied -> not listed
