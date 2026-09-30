"""Plane-aware execution routing (P2): solo -> local model + personal context; org -> shared
endpoint + org wiki with personal context excluded. Org without a backend raises (never local)."""

from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.plane_routing import PlaneUnavailable, plane_inference


@dataclass
class _Cfg:
    org_backend_status: str = ""
    org_model_endpoint: str = ""
    org_model: str = ""
    org_model_key_enc: str = ""
    org_provider: str = ""
    org_provision_key_enc: str = ""
    ollama_url: str = ""
    ollama_model: str = ""
    local_serve_url: str = ""
    local_serve_model: str = ""
    solo_compute: str = "local"
    deployment_topology: str = "org"  # matches OrgSettings' real column default


def _decrypt(token: str) -> str:
    return "PLAINTEXT-" + token


# ── pure routing ─────────────────────────────────────────────────────────────────────


def test_solo_routes_to_local_with_personal_context():
    pi = plane_inference(
        "solo", _Cfg(ollama_url="http://x:11434", ollama_model="qwen2.5:7b"), decrypt=_decrypt
    )
    assert pi.plane == "solo" and pi.backend == "ollama"
    assert pi.base_url == "http://x:11434" and pi.model == "qwen2.5:7b"
    assert pi.api_key is None
    assert pi.wiki_scope == "personal" and pi.use_personal_context is True


def test_solo_falls_back_to_defaults_when_cfg_blank():
    pi = plane_inference("solo", _Cfg(), decrypt=_decrypt)
    assert pi.base_url.startswith("http://localhost:11434") and pi.model


def test_solo_routes_to_local_finetune_when_one_is_served():
    # A promoted on-device fine-tune is served via the local mlx-lm OpenAI endpoint -> Solo uses it
    # (still local, personal context kept) instead of plain Ollama.
    cfg = _Cfg(
        ollama_url="http://x:11434",
        ollama_model="qwen2.5:7b",
        local_serve_url="http://127.0.0.1:11435/v1",
        local_serve_model="mlx-community/Qwen2.5-7B-Instruct-4bit",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt)
    assert pi.plane == "solo" and pi.backend == "openai"
    assert pi.base_url == "http://127.0.0.1:11435/v1"
    assert pi.model == "mlx-community/Qwen2.5-7B-Instruct-4bit"
    assert pi.api_key is None
    assert pi.wiki_scope == "personal" and pi.use_personal_context is True


def test_solo_cloud_routes_to_own_endpoint_keeping_personal_context():
    # Solo compute = cloud: a Solo run executes on the user's own configured cloud endpoint (a big open
    # model), NOT the on-device one. It is the user's own cloud, so personal context + personal wiki are
    # kept and the run is not marked ephemeral (unlike the personal-mode borrow of a shared org model).
    cfg = _Cfg(
        solo_compute="cloud",
        org_backend_status="validated",
        org_model_endpoint="https://my-gpu.example/v1",
        org_model="qwen2.5:72b",
        org_model_key_enc="ENC",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt)
    assert pi.plane == "solo" and pi.backend == "openai"
    assert pi.base_url == "https://my-gpu.example/v1" and pi.model == "qwen2.5:72b"
    assert pi.api_key == "PLAINTEXT-ENC"
    assert pi.wiki_scope == "personal" and pi.use_personal_context is True
    assert pi.ephemeral is False  # your own cloud - not a borrow, nothing forced ephemeral


def test_solo_cloud_falls_back_to_local_when_no_endpoint():
    # cloud is selected but no backend is connected -> quietly run on the local model (never a hard
    # failure for a Solo run; cloud is an enhancement).
    cfg = _Cfg(solo_compute="cloud", ollama_url="http://x:11434", ollama_model="qwen2.5:7b")
    pi = plane_inference("solo", cfg, decrypt=_decrypt)
    assert pi.plane == "solo" and pi.backend == "ollama" and pi.base_url == "http://x:11434"


def test_solo_cloud_beats_the_org_account_ephemeral_path():
    # solo_compute=cloud is the user's OWN VPC (not a shared org model), so it takes precedence over the
    # org-account "Solo chat on the org model, ephemeral" path and is NOT marked ephemeral.
    cfg = _Cfg(
        solo_compute="cloud",
        org_backend_status="validated",
        org_model_endpoint="https://my-gpu.example/v1",
        org_model="qwen2.5:72b",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt)
    assert pi.backend == "openai" and pi.ephemeral is False


def test_prefer_local_forces_a_solo_cloud_run_onto_the_local_model():
    # The offline "use my local model now" choice: a Solo-cloud account whose VPC is unreachable runs on
    # the on-device model instead. The wiki is always local, so it is a seamless, weaker-model fallback -
    # the model swaps, the personal wiki does not.
    cfg = _Cfg(
        solo_compute="cloud",
        org_backend_status="validated",
        org_model_endpoint="https://my-gpu.example/v1",
        org_model="qwen2.5:72b",
        org_model_key_enc="ENC",
        ollama_url="http://x:11434",
        ollama_model="qwen2.5:7b",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt, prefer_local=True)
    assert pi.plane == "solo" and pi.backend == "ollama"
    assert pi.base_url == "http://x:11434" and pi.model == "qwen2.5:7b"
    assert pi.wiki_scope == "personal" and pi.use_personal_context is True  # same local wiki


def test_prefer_local_overrides_the_org_account_org_model():
    # In an org account a Solo chat runs on the org model; prefer_local (the offline "use local now"
    # choice) forces it onto the on-device model instead.
    cfg = _Cfg(
        org_backend_status="validated",
        org_model_endpoint="https://my-gpu.example/v1",
        org_model="qwen2.5:72b",
        ollama_url="http://x:11434",
        ollama_model="qwen2.5:7b",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt, prefer_local=True)
    assert pi.backend == "ollama" and pi.base_url == "http://x:11434"


def test_prefer_local_is_ignored_for_an_org_plane():
    # There is no local option at org scale; prefer_local never diverts an Org run to the local model.
    cfg = _Cfg(
        org_backend_status="validated",
        org_model_endpoint="https://my-gpu.example/v1",
        org_model="qwen2.5:72b",
    )
    pi = plane_inference("org", cfg, decrypt=_decrypt, prefer_local=True)
    assert pi.plane == "org" and pi.backend == "openai"
    assert pi.base_url == "https://my-gpu.example/v1"


def test_unknown_plane_routes_to_solo():
    assert plane_inference("nonsense", _Cfg(), decrypt=_decrypt).plane == "solo"
    assert plane_inference(None, _Cfg(), decrypt=_decrypt).plane == "solo"


def test_org_routes_to_endpoint_excluding_personal_context():
    cfg = _Cfg(
        org_backend_status="validated",
        org_model_endpoint="https://gpu.acme.example/v1",
        org_model="qwen2.5:32b",
        org_model_key_enc="ENC",
    )
    pi = plane_inference("org", cfg, decrypt=_decrypt)
    assert pi.plane == "org" and pi.backend == "openai"
    assert pi.base_url == "https://gpu.acme.example/v1" and pi.model == "qwen2.5:32b"
    assert pi.api_key == "PLAINTEXT-ENC"  # decrypted via the injected fn
    assert (
        pi.wiki_scope == "org" and pi.use_personal_context is False
    )  # privacy: no personal context


def test_org_without_a_validated_backend_raises():
    with pytest.raises(PlaneUnavailable):
        plane_inference("org", _Cfg(org_backend_status="planned"), decrypt=_decrypt)
    with pytest.raises(PlaneUnavailable):
        plane_inference("org", None, decrypt=_decrypt)


def test_org_validated_but_no_endpoint_raises():
    # status validated but the endpoint URL is missing -> nothing to call -> unavailable
    with pytest.raises(PlaneUnavailable):
        plane_inference(
            "org", _Cfg(org_backend_status="validated", org_model_endpoint=""), decrypt=_decrypt
        )


def test_org_bad_key_decrypt_degrades_to_no_key():
    def _boom(token: str) -> str:
        raise ValueError("bad key")

    cfg = _Cfg(
        org_backend_status="validated", org_model_endpoint="https://e/v1", org_model_key_enc="ENC"
    )
    pi = plane_inference("org", cfg, decrypt=_boom)
    assert pi.api_key is None  # a decrypt failure never blocks routing


def test_runpod_org_uses_the_provision_key_when_model_key_is_empty():
    # RunPod's inference endpoint authenticates with the SAME key as provisioning, and provisioning
    # never copies it into org_model_key. Without this fallback, chat hits RunPod with no token -> 401.
    cfg = _Cfg(
        org_backend_status="provisioned",
        org_model_endpoint="https://api.runpod.ai/v2/abc/openai/v1",
        org_model="Qwen2.5 32B",
        org_model_key_enc="",  # never set by provisioning
        org_provider="runpod",
        org_provision_key_enc="RPKEY",
    )
    pi = plane_inference("org", cfg, decrypt=_decrypt)
    assert pi.api_key == "PLAINTEXT-RPKEY"  # the RunPod provision key, used for inference auth


def test_explicit_model_key_takes_precedence_over_the_provision_key():
    cfg = _Cfg(
        org_backend_status="validated",
        org_model_endpoint="https://e/v1",
        org_model_key_enc="MODELKEY",
        org_provider="runpod",
        org_provision_key_enc="RPKEY",
    )
    pi = plane_inference("org", cfg, decrypt=_decrypt)
    assert pi.api_key == "PLAINTEXT-MODELKEY"  # an explicit model key wins


# ── /chat/new plane gating ───────────────────────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

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
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.flush()
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client, app_mod, org.id


def _latest_conv(app_mod):
    return (
        app_mod._SessionFactory()
        .query(db_mod.Conversation)
        .order_by(db_mod.Conversation.id.desc())
        .first()
    )


def test_chat_new_defaults_to_solo(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/chat/new", follow_redirects=False)
    assert _latest_conv(app_mod).plane == "solo"


def test_chat_new_org_without_backend_falls_back_to_solo(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/chat/new", data={"plane": "org"}, follow_redirects=False)
    assert _latest_conv(app_mod).plane == "solo"  # no validated backend -> solo


def test_chat_new_org_with_validated_backend_is_org(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == org_id).first()
    cfg.org_backend_status = "validated"
    cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.commit()
    client.post("/chat/new", data={"plane": "org"}, follow_redirects=False)
    assert _latest_conv(app_mod).plane == "org"


def test_settings_persists_solo_compute(tmp_path, monkeypatch):
    # The Solo-compute choice (local | cloud) persists; the control now lives on the Solo settings home,
    # and an invalid/absent value is ignored (the saved choice is preserved - never trust an odd form
    # value into routing, and never let saving the advanced knobs reset a cloud choice back to local).
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, OrgSettings, User

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
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    s.add(admin)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="solo"))
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(admin.id, org.id, "admin"))

    # choosing a compute tier via the Solo chooser (/personalize/compute) persists solo_compute. Using
    # the cloud tier sets solo_compute=cloud (real provisioning is mocked out elsewhere; this test only
    # cares that the value persists and survives an unrelated /settings save below).
    c.post(
        "/personalize/compute",
        data={"compute": "cloud", "cloud_provider": "runpod", "provider_api_key": "rp_x"},
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    assert cfg.solo_compute == "cloud"

    # saving the advanced /settings knobs (no compute field) must NOT reset the cloud choice
    c.post("/settings", data={"cache_threshold": "0.9"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    assert cfg.solo_compute == "cloud"  # preserved, not reset to local


def test_org_stream_without_backend_surfaces_unavailable(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    # an Org conversation whose backend is gone streams the unavailable notice and never calls a model
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="org")
    s.add(conv)
    s.commit()
    cid = conv.id
    r = client.get(f"/chat/{cid}/stream", params={"message": "hi"})
    assert r.status_code == 200
    assert r.text.count("not connected") == 1  # one PlaneUnavailable error reached the stream
    assistant = (
        app_mod._SessionFactory()
        .query(db_mod.ChatMessage)
        .filter_by(conversation_id=cid, role="assistant")
        .one()
    )
    assert assistant.content.startswith("⚠️ Generation failed - ")
    assert "not connected" in assistant.content
    page = BeautifulSoup(client.get(f"/chat/{cid}").text, "html.parser")
    message = page.select_one(f'.msg[data-id="{assistant.id}"]')
    assert message is not None
    assert "Answered on this device" not in str(message)
    assert "Answered by" not in str(message)


def test_org_escalation_forces_org_plane_on_a_solo_chat(tmp_path, monkeypatch):
    # "Redo with the org model" (org=true) escalates a SOLO conversation's turn to the org plane.
    # With no backend connected, it must surface the not-connected notice - proving it routed to org,
    # not the local model.
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="solo")
    s.add(conv)
    s.commit()
    cid = conv.id
    r = client.get(f"/chat/{cid}/stream", params={"message": "hi", "escalate_org": "true"})
    assert r.status_code == 200
    assert (
        "not connected" in r.text
    )  # escalation routed to the org plane (which is unavailable here)


def _solo_topology_connected_backend(app_mod, org_id):
    # A solo-topology account (deployment_topology="solo") that connected its own endpoint but kept
    # solo_compute="local" as its default - the exact target scenario for #278's escalation engine
    # (Decision 0 keeps this account on local by default; only an explicit redo phrase escalates).
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == org_id).first()
    cfg.deployment_topology = "solo"
    cfg.org_backend_status = "validated"
    cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    cfg.solo_compute = "local"
    s.commit()


def test_redo_phrase_forces_org_plane_on_a_followup(tmp_path, monkeypatch):
    # #278: "use the cloud model" on a FOLLOW-UP (a conversation with a prior assistant turn) forces
    # THIS turn to the connected endpoint instead of local - proving the natural-language redo path
    # actually takes effect (unlike the pre-existing "web"/"deep" redo detection, which ran too late to
    # matter - see the app.py fix this PR ships alongside it). Evidence: the stream tries the connected
    # endpoint's URL, not Ollama - a plain solo chat on this account would try Ollama (Decision 0).
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _solo_topology_connected_backend(app_mod, org_id)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="solo")
    s.add(conv)
    s.flush()
    s.add(
        db_mod.ChatMessage(conversation_id=conv.id, role="assistant", content="an earlier answer")
    )
    s.commit()
    cid = conv.id
    r = client.get(f"/chat/{cid}/stream", params={"message": "use the cloud model"})
    assert r.status_code == 200
    assert "gpu.acme.example" in r.text  # tried the connected endpoint, not Ollama
    assert "Can't reach Ollama" not in r.text


def test_redo_phrase_on_a_fresh_conversation_does_not_escalate(tmp_path, monkeypatch):
    # The SAME phrase on a conversation with NO prior assistant turn is just a fresh message, not a
    # redo cue - it must stay on the local default (Decision 0), never try the connected endpoint.
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _solo_topology_connected_backend(app_mod, org_id)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="solo")
    s.add(conv)
    s.commit()
    cid = conv.id
    r = client.get(f"/chat/{cid}/stream", params={"message": "use the cloud model"})
    assert r.status_code == 200
    assert "gpu.acme.example" not in r.text
    assert "Can't reach Ollama" in r.text  # stayed on the local default, as a fresh message should


def test_redo_phrase_with_nothing_connected_stays_local(tmp_path, monkeypatch):
    # A follow-up redo phrase with NO backend connected at all must not force org (there'd be nothing
    # to route to) - it falls through and the turn stays on the local default.
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="solo")
    s.add(conv)
    s.flush()
    s.add(
        db_mod.ChatMessage(conversation_id=conv.id, role="assistant", content="an earlier answer")
    )
    s.commit()
    cid = conv.id
    r = client.get(f"/chat/{cid}/stream", params={"message": "use the cloud model"})
    assert r.status_code == 200
    assert "Can't reach Ollama" in r.text


def test_org_model_rerun_button_is_retired(tmp_path, monkeypatch):
    # #421: the manual "org model" re-run button is gone even with an org backend connected. Under one
    # model per account there is no bigger model to escalate to per turn, so the "Not satisfied? answer
    # again on the bigger model" lever (and its dead JS) is removed; the router decides depth.
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _validate_org_backend(app_mod, org_id)
    s = app_mod._SessionFactory()
    conv = db_mod.Conversation(org_id=org_id, user_id=1, plane="solo")
    s.add(conv)
    s.flush()
    s.add(db_mod.ChatMessage(conversation_id=conv.id, role="assistant", content="an answer"))
    s.commit()
    body = client.get(f"/chat/{conv.id}").text
    assert "redoFromDom(this,'org')" not in body  # the org re-run handler is gone
    assert "org model</button>" not in body  # and its button
    assert "ORG_AVAILABLE" not in body  # the dead JS const is gone


# ── chat sidebar plane split ─────────────────────────────────────────────────────────


def _validate_org_backend(app_mod, org_id):
    s = app_mod._SessionFactory()
    cfg = s.query(db_mod.OrgSettings).filter(db_mod.OrgSettings.org_id == org_id).first()
    cfg.org_backend_status = "validated"
    cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.commit()


def test_chat_list_is_flat_without_org_backend(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)
    client.post("/chat/new", follow_redirects=False)
    conv = _latest_conv(app_mod)
    page = client.get(f"/chat/{conv.id}").text
    assert "+ New chat" in page  # the simple single list
    # no plane split surfaced: no section headers, no per-plane new-chat forms. Match the header markup
    # (class="rail-conv-section"), not a bare substring - the rail's search/sort script names the class.
    assert 'class="rail-conv-section"' not in page
    assert 'name="plane" value="org"' not in page


def test_chat_list_splits_when_org_backend_available(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _validate_org_backend(app_mod, org_id)
    client.post("/chat/new", data={"plane": "solo"}, follow_redirects=False)  # explicit Solo chat
    conv = _latest_conv(app_mod)
    page = client.get(f"/chat/{conv.id}").text
    assert "rail-conv-section" in page  # the home section headers render
    assert (
        "Personal" in page
    )  # the Personal home header (the three homes: Personal/Project/Organization)
    # both new-chat actions, gated on availability (matched by their hidden plane inputs)
    assert 'name="plane" value="solo"' in page and 'name="plane" value="org"' in page
    assert "plane-dot-solo" in page  # the Solo conversation carries the distinct marker


def test_team_chat_gets_its_own_project_home(tmp_path, monkeypatch):
    # No homeless chats: a team-plane chat appears under its project's heading (the team name), never
    # mislabelled as a Personal (solo) chat. team chats alone are enough to surface the home headings.
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    uid = s.query(db_mod.User).filter(db_mod.User.email == "u@acme.com").first().id
    team = db_mod.Team(org_id=org_id, name="Platform", slug="platform", owner_id=uid)
    s.add(team)
    s.flush()
    s.add(db_mod.TeamMembership(team_id=team.id, user_id=uid, role="owner", status="active"))
    s.add(
        db_mod.Conversation(
            org_id=org_id, user_id=uid, title="Team sync", plane="team", team_id=team.id
        )
    )
    s.commit()
    conv = _latest_conv(app_mod)
    page = client.get(f"/chat/{conv.id}").text
    assert 'rail-conv-section">Platform<' in page  # the project heading is the team name
    assert "plane-dot-team" in page  # the team chat carries the distinct team marker
    # grouped as its project, not Personal: the row title suffix is " (Platform)", not " (Personal)"
    assert "Team sync (Platform)" in page


def test_new_chat_defaults_to_the_private_solo_plane_even_with_backend(tmp_path, monkeypatch):
    # A new chat defaults to the SOLO (private) plane even when the org backend is connected. In an org
    # account that Solo chat runs on the org model (one model per account) but stays private; a SHARED
    # org chat is explicit ("+ Org chat" or per-message escalation).
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _validate_org_backend(app_mod, org_id)
    client.post("/chat/new", follow_redirects=False)
    assert _latest_conv(app_mod).plane == "solo"


def test_new_chat_defaults_to_solo_without_backend(tmp_path, monkeypatch):
    client, app_mod, _ = _app(tmp_path, monkeypatch)  # no org backend validated
    client.post("/chat/new", follow_redirects=False)
    assert _latest_conv(app_mod).plane == "solo"


# ── org reachability probe (offline gating) ──────────────────────────────────────────


def test_reachability_unavailable_without_backend():
    from anthill.web.plane_routing import org_reachability

    d = org_reachability(_Cfg(), decrypt=_decrypt)
    assert d["available"] is False and d["state"] == "unavailable"


def test_reachability_ready_when_endpoint_answers():
    from anthill.web.plane_routing import org_reachability

    cfg = _Cfg(org_backend_status="validated", org_model_endpoint="https://e/v1")
    d = org_reachability(cfg, decrypt=_decrypt, list_models=lambda: ["m1", "m2"])
    assert d["available"] is True and d["state"] == "ready" and "2 model" in d["detail"]


def test_reachability_unreachable_when_probe_fails():
    from anthill.web.plane_routing import org_reachability

    def _boom():
        raise OSError("refused")

    cfg = _Cfg(org_backend_status="validated", org_model_endpoint="https://e/v1")
    d = org_reachability(cfg, decrypt=_decrypt, list_models=_boom)
    assert d["available"] is True and d["state"] == "unreachable"
    assert "refused" not in d["detail"]  # the raw error is not leaked to the client


def test_org_api_key_runpod_falls_back_to_provision_key():
    from anthill.web.plane_routing import _org_api_key

    # Provisioned RunPod backend: org_model_key is empty, the provision key carries the token.
    cfg = _Cfg(org_provider="runpod", org_model_key_enc="", org_provision_key_enc="RPKEY")
    assert _org_api_key(cfg, _decrypt) == "PLAINTEXT-RPKEY"


def test_org_api_key_explicit_model_key_wins():
    from anthill.web.plane_routing import _org_api_key

    cfg = _Cfg(org_provider="runpod", org_model_key_enc="MODELKEY", org_provision_key_enc="RPKEY")
    assert _org_api_key(cfg, _decrypt) == "PLAINTEXT-MODELKEY"


def test_org_api_key_no_fallback_for_non_runpod_or_unset():
    from anthill.web.plane_routing import _org_api_key

    # Only RunPod reuses the provision key; another provider with no model key has no key.
    assert _org_api_key(_Cfg(org_provider="aws", org_provision_key_enc="RPKEY"), _decrypt) is None
    assert _org_api_key(_Cfg(), _decrypt) is None  # nothing configured


def test_reachability_passes_provision_key_for_runpod(monkeypatch):
    """org_reachability must probe a provisioned RunPod endpoint WITH the provision key.

    Regression: the probe read only org_model_key (empty after provisioning) -> hit RunPod with no
    token -> 401 -> state 'unreachable' -> the chat UI grayed out the usable Org button half a
    second after it loaded.
    """
    from anthill.hosting import endpoint as ep_mod
    from anthill.web import plane_routing

    captured = {}

    def _fake_list_models(ep):
        captured["api_key"] = ep.api_key
        return ["m1"]

    monkeypatch.setattr(ep_mod, "_list_models", _fake_list_models)
    cfg = _Cfg(
        org_backend_status="provisioned",
        org_model_endpoint="https://api.runpod.ai/v2/abc/openai/v1",
        org_model_key_enc="",  # never set by provisioning
        org_provider="runpod",
        org_provision_key_enc="RPKEY",
    )
    d = plane_routing.org_reachability(cfg, decrypt=_decrypt)  # default probe -> our fake
    assert d["state"] == "ready"
    assert captured["api_key"] == "PLAINTEXT-RPKEY"  # provision key used, not an empty token


def test_plane_status_route_reports_unavailable(tmp_path, monkeypatch):
    client, _, _ = _app(tmp_path, monkeypatch)  # OrgSettings exists but no backend connected
    r = client.get("/chat/plane/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["state"] == "unavailable"


# ── P4: personal mode on the org model ───────────────────────────────────────────────


def _org_cfg():
    return _Cfg(
        org_backend_status="validated",
        org_model_endpoint="https://gpu.acme.example/v1",
        org_model="qwen2.5:32b",
        org_model_key_enc="ENC",
    )


def test_org_account_solo_chat_runs_on_the_org_model_private():
    # Finding A / one model per account: in an org account (a shared backend is configured), a Solo chat
    # runs on the ORG model + the personal (private) wiki, ephemeral - not shared/retained/trained. This
    # is now the DEFAULT (the old personal-mode-on-org opt-in is retired).
    pi = plane_inference("solo", _org_cfg(), decrypt=_decrypt)
    assert pi.plane == "solo" and pi.backend == "openai"  # the org model...
    assert pi.base_url == "https://gpu.acme.example/v1" and pi.model == "qwen2.5:32b"
    assert pi.api_key == "PLAINTEXT-ENC"
    assert pi.use_personal_context is True and pi.wiki_scope == "personal"  # ...private wiki
    assert pi.ephemeral is True  # not shared/retained/trained


def test_solo_without_a_backend_is_plain_local():
    # A Solo-local account (no org backend) -> the on-device model, not ephemeral.
    pi = plane_inference("solo", _Cfg(ollama_model="m"), decrypt=_decrypt)
    assert pi.backend == "ollama" and pi.ephemeral is False


def test_solo_topology_with_a_connected_endpoint_stays_local_by_default():
    # #278: a solo-topology account that connected its own RunPod/inference-provider endpoint but kept
    # solo_compute="local" as its cost-saving default must NOT be swept into ONE-MODEL-PER-ACCOUNT
    # sharing - unlike a genuine org account (test_org_account_solo_chat_runs_on_the_org_model_private
    # above), it stays on the local model. Otherwise the escalation engine has no local answer to ever
    # judge or escalate from - every turn would already be running on the connected endpoint.
    cfg = _Cfg(
        deployment_topology="solo",
        org_backend_status="validated",
        org_model_endpoint="https://api.berget.ai/v1",
        org_model="gpt-oss-120b",
        org_model_key_enc="ENC",
        ollama_model="m",
        solo_compute="local",
    )
    pi = plane_inference("solo", cfg, decrypt=_decrypt)
    assert pi.backend == "ollama" and pi.ephemeral is False


def test_org_plane_excludes_personal_context():
    # An Org conversation always uses the org model with personal context excluded (privacy invariant).
    pi = plane_inference("org", _org_cfg(), decrypt=_decrypt)
    assert pi.plane == "org" and pi.use_personal_context is False and pi.ephemeral is False
