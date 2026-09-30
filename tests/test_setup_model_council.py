"""Phase 6b2: the compute/model/council step. Capacity-based suggestion (a 3-member family-diverse
local council when memory, speed, AND disk all allow it, else the single smartest model) built on
recommend_by_family - NOT suggest_regional_council, which is for a geographically-diverse REMOTE
council and is unrelated to local hardware capacity (a real mistake an earlier draft made).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.council.engine import resolve_council_backends
from anthill.hosting import sizing
from anthill.web import db
from anthill.web.db import Organization, OrgSettings, User


def _pick(family, params_b, tag, intelligence=50.0):
    m = sizing.Model(
        name=f"{family} {tag}",
        params_b=params_b,
        ollama_tag=tag,
        family=family,
        origin="Test, X",
        intelligence=intelligence,
        active_b=params_b,
    )
    return sizing.FamilyPick(family=family, origin="Test, X", recommended=m, download_gb=params_b)


def test_suggest_local_setup_council_when_3_families_fit(monkeypatch):
    picks = [_pick("a", 8, "a:8b"), _pick("b", 7, "b:7b"), _pick("c", 4, "c:4b")]
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: picks)
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: True)
    monkeypatch.setattr(sizing, "onprem_council_fits_on_disk", lambda params: True)
    s = sizing.suggest_local_setup(128.0, "apple")
    assert s.mode == "council"
    assert len(s.members) == 3


def test_suggest_local_setup_single_when_memory_fails(monkeypatch):
    picks = [_pick("a", 8, "a:8b"), _pick("b", 7, "b:7b"), _pick("c", 4, "c:4b")]
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: picks)
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: False)
    monkeypatch.setattr(sizing, "onprem_council_fits_on_disk", lambda params: True)
    s = sizing.suggest_local_setup(16.0, "apple")
    assert s.mode == "single"
    assert len(s.members) == 1
    assert (
        s.members[0].family == "a"
    )  # the strongest family, per recommend_by_family's own ordering


def test_suggest_local_setup_single_when_disk_fails(monkeypatch):
    picks = [_pick("a", 8, "a:8b"), _pick("b", 7, "b:7b"), _pick("c", 4, "c:4b")]
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: picks)
    monkeypatch.setattr(sizing, "onprem_council_fits", lambda params: True)
    monkeypatch.setattr(sizing, "onprem_council_fits_on_disk", lambda params: False)
    s = sizing.suggest_local_setup(64.0, "apple")
    assert s.mode == "single"
    assert len(s.members) == 1


def test_suggest_local_setup_single_when_fewer_than_3_families_fit(monkeypatch):
    picks = [
        _pick("a", 8, "a:8b"),
        sizing.FamilyPick(family="b", origin="Test, X", recommended=None, download_gb=0.0),
    ]  # only one family actually fits
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: picks)
    # onprem_council_fits/on_disk must not even be consulted with fewer than 3 candidates
    monkeypatch.setattr(
        sizing, "onprem_council_fits", lambda params: (_ for _ in ()).throw(AssertionError())
    )
    s = sizing.suggest_local_setup(16.0, "apple")
    assert s.mode == "single"
    assert len(s.members) == 1


def test_suggest_local_setup_nothing_fits(monkeypatch):
    monkeypatch.setattr(sizing, "recommend_by_family", lambda *a, **k: [])
    s = sizing.suggest_local_setup(2.0, "apple")
    assert s.mode == "single"
    assert s.members == []


def _client(tmp_path, *, topology="solo", chosen=True):
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
    s.add(User(org_id=o.id, email="a@a.com", role="admin", active=True))
    s.add(OrgSettings(org_id=o.id, deployment_topology=topology, local_model_chosen=chosen))
    s.commit()
    u = app_mod._SessionFactory().query(User).first()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_setup_shows_the_two_tier_chooser_with_attachment(tmp_path):
    # Compound-compute-tiers spec: two base ownership tiers (not three) - an inference provider is no
    # longer a standalone competing tier, only an optional attachment on either base tier. Both tier
    # cards are always visible (id="cc-tierseg" wraps both, per the founder-approved mockup) rather
    # than a segmented switch hiding one; the inference-provider attachment is its own always-visible
    # card (not a checkbox someone could miss), and clicking a provider card both selects and attaches
    # it - no separate toggle.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="cc-tierseg"' in body  # wraps both always-visible tier cards
    assert "Your machine" in body and "Your cloud" in body
    assert (
        "Where does your model run?" in body
    )  # setup has no prior summary, so it keeps the heading
    assert "Your inference provider" in body  # the always-visible attachment card
    assert "ownershipindex.ai" in body  # inference providers link to their AOI grade
    assert 'name="provider_api_key"' in body  # the cloud tier's own required connect flow
    assert 'name="escalation_api_key"' in body  # the optional attachment's own connect flow


def test_compute_cards_tell_one_graduated_capability_story(tmp_path):
    # Founder: "local or cloud, plus an inference provider next to it, combines into the expert one."
    # Two base chips (Graduate/Professional), each upgrading to Expert once a provider is attached -
    # not three tiers competing for the same "top" label.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="cc-chip-local"' in body and "Graduate" in body  # Your machine, base
    assert 'id="cc-chip-cloud"' in body and "Professional" in body  # Your cloud, base
    assert "Expert" in body  # the shared upgraded label, set once a provider is attached


def test_provider_connect_shows_where_to_create_a_key(tmp_path):
    # Founder: "show them also how to create an API key afterward." A bare signup link isn't enough -
    # each provider needs a verified deep link to its OWN key-management page (not just its marketing
    # site) plus a concrete one-line "how". Berget/Infercom especially: their old links went to generic
    # marketing homepages, not anywhere a key could actually be created.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert "console.berget.ai" in body  # not the bare berget.ai marketing homepage
    assert "cloud.infercom.ai/apis" in body  # the real, verified API-keys page
    assert "console.groq.com/keys" in body
    assert "keyurl" in body and "ccFindProv" in body  # the post-select "how to get your key" wiring


# The standalone "provider" primary-compute tier (a hosted inference provider AS the lead, no local
# model, a multi-model council all sharing one endpoint+key) is retired by the compound-compute-tiers
# spec - _apply_solo_compute no longer accepts compute=="provider" at all. Its replacement, an
# inference provider ATTACHED to the local/cloud base tier purely for escalation on hard questions
# (never a council member, per _council_builder.html's "escalation never changes which models form the
# council"), is covered below and gets fuller coverage in the dedicated backend test suite.


def test_escalation_attachment_on_local_tier_stores_provider_and_key(tmp_path):
    # Attaching an inference provider (Groq) to the "local" base tier stores it as the ESCALATION
    # path, not the primary backend - org_model/org_provider (the local tier's own fields) are
    # untouched, only the new escalation_* columns are written.
    from anthill.web.crypto import decrypt

    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_secret",
            "escalation_mode": "automated",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "groq"
    assert cfg.escalation_mode == "automated"
    assert (
        cfg.escalation_provider_key_enc and decrypt(cfg.escalation_provider_key_enc) == "gsk_secret"
    )
    assert not cfg.org_provider  # the local tier's own primary-backend fields stay untouched


def test_escalation_attachment_without_a_key_does_not_block_setup(tmp_path):
    # Founder correction (2026-09-28): picking a provider card in the WIZARD without pasting a key
    # must not fail the whole submit - that would silently drop the compute/model choice too, since
    # the escalation check used to run before either was applied. Setup just proceeds with nothing
    # attached; the account finishes connecting it later from Settings (surfaced on the dashboard).
    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "berget",
            "escalation_api_key": "",
        },
        follow_redirects=False,
    )
    assert "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert not cfg.escalation_provider
    assert cfg.local_model_chosen  # the rest of the choice still went through, not silently dropped


def test_escalation_attachment_requires_key_in_settings(tmp_path):
    # Settings (unlike the wizard) is a deliberate, standalone save - a bad attachment there should
    # still error rather than silently discard what was just typed.
    c, _ = _client(tmp_path, topology="solo", chosen=True)
    r = c.post(
        "/personalize/compute",
        data={"compute": "local", "escalation_provider": "berget", "escalation_api_key": ""},
        follow_redirects=False,
    )
    assert "error=escalation_provider" in r.headers["location"]


def test_escalation_attachment_resave_with_blank_key_reuses_the_saved_one(tmp_path):
    # Re-saving (e.g. flipping the mode) without retyping the key keeps the SAME provider's
    # already-saved key - mirrors personalize_cloud_connect's "blank keeps the saved key" convention.
    from anthill.web.crypto import decrypt

    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_secret",
            "escalation_mode": "ask",
        },
        follow_redirects=False,
    )
    r = c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "groq",
            "escalation_api_key": "",  # blank - not retyped
            "escalation_mode": "automated",  # only the mode changed
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_mode == "automated"
    assert decrypt(cfg.escalation_provider_key_enc) == "gsk_secret"  # unchanged


def test_escalation_attachment_switching_provider_without_a_key_keeps_the_old_one_in_setup(
    tmp_path,
):
    # The old key belongs to the OLD provider - switching to a different one with a blank key must
    # never silently keep authenticating against the new provider with the wrong credential. In the
    # wizard (strict=False) this no longer errors the whole submit though - it just leaves the
    # existing attachment as-is, same end state as the strict Settings version below.
    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_secret",
        },
        follow_redirects=False,
    )
    r = c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "berget",  # different provider, no new key
            "escalation_api_key": "",
        },
        follow_redirects=False,
    )
    assert "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "groq"  # unchanged - the failed switch didn't half-apply


def test_escalation_attachment_switching_provider_requires_a_fresh_key_in_settings(tmp_path):
    # Settings is a deliberate, standalone save - this guarantee still errors there.
    from anthill.web.crypto import encrypt

    c, app_mod = _client(tmp_path, topology="solo", chosen=True)
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.escalation_provider = "groq"
    cfg.escalation_provider_key_enc = encrypt("gsk_secret")
    s.commit()
    r = c.post(
        "/personalize/compute",
        data={
            "compute": "local",
            "escalation_provider": "berget",  # different provider, no new key
            "escalation_api_key": "",
        },
        follow_redirects=False,
    )
    assert "error=escalation_provider" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "groq"  # unchanged - the failed save didn't half-apply


def test_escalation_attachment_can_be_cleared(tmp_path):
    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    c.post(
        "/setup/model",
        data={
            "compute": "local",
            "choice": "qwen2.5:7b",
            "escalation_provider": "groq",
            "escalation_api_key": "gsk_secret",
            "escalation_mode": "automated",
        },
        follow_redirects=False,
    )
    r = c.post(
        "/setup/model",
        data={"compute": "local", "choice": "qwen2.5:7b", "escalation_provider": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == ""
    assert cfg.escalation_provider_key_enc == ""
    assert cfg.escalation_mode == "ask"  # reset to the default, not left stale


def test_escalation_attachment_also_works_on_the_cloud_tier(tmp_path):
    # The attachment is orthogonal to which base tier is active - cloud must accept it too, not just
    # local, and the cloud tier's OWN provider/key must stay independent of the attachment's.
    from anthill.web.crypto import decrypt

    c, app_mod = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "cloud",
            "cloud_provider": "runpod",
            "provider_api_key": "rp_secret",
            "escalation_provider": "infercom",
            "escalation_api_key": "inf_secret",
            "escalation_mode": "automated",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 200)
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.escalation_provider == "infercom"
    assert decrypt(cfg.escalation_provider_key_enc) == "inf_secret"
    assert (
        cfg.org_provider == "runpod"
    )  # the cloud tier's own provider, untouched by the attachment
    assert decrypt(cfg.org_provision_key_enc) == "rp_secret"


def test_setup_model_hydrates_an_already_configured_escalation_attachment(tmp_path):
    # /setup/model shares _compute_chooser.html with /personalize, which had a real, destructive
    # hydration gap fixed in PR #770 review (resaving silently cleared an existing attachment, since
    # the widget always started from blank defaults). Nothing blocks an already-configured account
    # from revisiting THIS route too (bookmark, browser back, an error-redirect loop) - it needs the
    # same real-state hydration, not just /personalize.
    from anthill.web.crypto import encrypt
    from anthill.web.db import OrgSettings as OrgSettingsModel

    c, app_mod = _client(tmp_path, topology="solo", chosen=True)
    session = app_mod._SessionFactory()
    cfg = session.query(OrgSettingsModel).first()
    cfg.solo_compute = "cloud"
    cfg.org_provider = "lambda"
    cfg.escalation_provider = "groq"
    cfg.escalation_provider_key_enc = encrypt("gsk_secret")
    cfg.escalation_mode = "automated"
    session.commit()

    body = c.get("/setup/model").text
    assert "ccHydrate('cloud', 'lambda', 'groq', 'automated')" in body


def test_cloud_tier_ui_supports_a_real_multi_model_council(tmp_path):
    # Founder + Dev Council (2026-08-05): "Your cloud" moved from single-model-only (last round) to a
    # real multi-instance council - provision_council_member/teardown_council_member already exist and
    # are tested on the org admin path, just not yet wired into Solo's cloud branch. The UI ships ahead
    # of that backend wiring landing (ccCloudSingle removed entirely), on a branch not yet merged.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert (
        "ccCloudSingle" not in body
    )  # the old single-select restriction is gone, not just disabled
    # The standing "each model runs on its own dedicated cloud GPU" paragraph was removed entirely
    # (founder report, 2026-09-29: restated what the tier card above it already says) - the cloud
    # tier's real capability now lives only in the tier card's own "Good fit for" line and the
    # boundary box's "Stays in your cloud account" cap, not a second explanatory sentence here.
    assert "a GPU pod you rent" in body
    assert 'id="cloud-council-disclosure"' in body  # cost/GPU-count disclosure exists in markup


def test_cloud_provider_records_plan_and_autopicks_gpu(tmp_path, monkeypatch):
    # Picking a self-provisioning cloud provider (RunPod) + API key + model records the plan and
    # auto-picks a GPU (the user never sees a GPU dropdown), then kicks provisioning off-thread.
    import anthill.web.app as app_mod
    from anthill.web.crypto import decrypt

    started = {}
    monkeypatch.setattr(
        app_mod, "_start_cloud_provision", lambda org_id: started.setdefault("id", org_id) or True
    )
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "cloud",
            "cloud_provider": "runpod",
            "provider_api_key": "rp_secret",
            "choice": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.org_provider == "runpod"
    assert cfg.org_provision_key_enc and decrypt(cfg.org_provision_key_enc) == "rp_secret"
    assert cfg.org_model == "qwen2.5:7b"
    assert cfg.org_gpu  # a GPU tier was auto-picked for the model
    assert cfg.solo_compute == "cloud"
    assert started.get("id") == cfg.org_id  # provisioning was kicked off


def test_cloud_council_end_to_end_through_the_real_setup_route(tmp_path, monkeypatch):
    # Complements Dev Council's direct _apply_solo_compute unit tests (test_solo_cloud_council_multi_
    # instance.py) with a real HTTP round-trip: the actual form fields a browser posts (council_models
    # in list order + council_lead out of band) must survive _order_council's reordering and reach the
    # multi-instance provisioning path correctly - not just when called directly with an
    # already-correctly-ordered `council` list.
    import json
    import threading

    import anthill.web.app as app_mod
    import anthill.web.provision_run as provision_run_mod

    class _SyncThread:
        def __init__(self, target, daemon=None, name=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_start_cloud_provision", lambda org_id: True)
    calls = []

    def _fake_provision_member(eng, org_id, member_index, **kw):
        calls.append((member_index, kw.get("expected_provider"), kw.get("expected_model")))
        return "provisioned"

    monkeypatch.setattr(provision_run_mod, "provision_council_member", _fake_provision_member)

    c, app_mod2 = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "cloud",
            "cloud_provider": "runpod",
            "provider_api_key": "rp_secret",
            "council_models": ["qwen3.5:9b", "gemma3:4b", "qwen2.5:7b"],  # list order
            "council_lead": "gemma3:4b",  # ticked first, NOT topmost - _order_council must reorder
        },
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error" not in r.headers["location"]
    cfg = app_mod2._SessionFactory().query(OrgSettings).first()
    members = json.loads(cfg.org_council_members)
    assert [m["model"] for m in members] == ["gemma3:4b", "qwen3.5:9b", "qwen2.5:7b"]  # lead first
    assert cfg.org_model == "gemma3:4b"  # the lead, on the legacy singleton column too
    # both reviewers (index 1, 2) were actually handed to provision_council_member - not just recorded
    # in org_council_members and silently left unprovisioned (the exact bug this whole arc started from)
    assert sorted(calls) == [
        (1, "runpod", "qwen3.5:9b"),
        (2, "runpod", "qwen2.5:7b"),
    ]


def test_local_council_builder_sets_up_a_multi_model_council(tmp_path, monkeypatch):
    # The setup model step is a COUNCIL builder, not a single-model picker: ticking two models sets up a
    # 2-member council (index 0 = lead) and pulls them both.
    import json

    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    pulled = []
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda org_id, tag, **k: pulled.append(tag))
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda *a, **k: None)
    # A 2+-member local council is gated by RAM floor + fit-check (see the floor/gate tests below) -
    # mock a machine comfortably above the floor so this test exercises the pull/save path, not the gate.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (48.0, "gpu"))
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={"compute": "local", "council_models": ["qwen2.5:7b", "llama3.1:8b"]},
        follow_redirects=False,
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    members = json.loads(cfg.org_council_members or "[]")
    assert [m["model"] for m in members] == ["qwen2.5:7b", "llama3.1:8b"]  # 2-member council
    assert cfg.ollama_model == "qwen2.5:7b"  # index 0 is the lead
    assert set(pulled) == {"qwen2.5:7b", "llama3.1:8b"}  # both pulled


def test_council_builder_renders_with_nothing_preselected(tmp_path):
    # Founder correction (2026-09-28): "it preselects one model... it offers no choice then" - the
    # model step is a real, deliberate choice, not something already decided by the time the user gets
    # there. No checkbox (local or cloud) is checked on load; Continue starts disabled until one is.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert "Which model do you run?" in body and 'name="council_models"' in body
    local_section = body[body.index('id="council-local"') : body.index('id="council-cloud"')]
    assert local_section.count('class="csel csel-local"') >= 1  # local tier's selectable models
    assert local_section.count(" checked onchange") == 0  # nothing preselected
    assert 'id="mp-continue" class="btn btn-primary" disabled' in body


def test_model_step_is_tier_aware(tmp_path):
    # The council builder has two lists toggled by tier: #council-local (fits this machine) and
    # #council-cloud (the bigger models cloud/inference unlock), switched by ccCouncilTier.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="council-local"' in body and 'id="council-cloud"' in body
    assert 'class="csel csel-cloud"' in body  # the bigger-models list exists
    assert "ccCouncilTier" in body  # picking a tier swaps the list
    # the cloud list is hidden by default (local is the default tier) and its boxes are disabled so
    # they never submit until that tier is picked
    assert 'id="council-cloud" hidden' in body
    assert 'class="csel csel-cloud" disabled' in body


def test_council_builder_below_ram_floor_shows_single_model_ui(tmp_path, monkeypatch):
    # Founder: "super simple and super logical" - a machine that can't run a local council should say so
    # up front (server-rendered), not let the user check a 2nd box and find out on submit. Mirrors
    # _LOCAL_COUNCIL_RAM_FLOOR_GB's server-side refusal in the UI itself.
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # under the 24GB floor
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="local-floor-banner"' in body
    assert "at least 24 GB" in body and "about 16 GB" in body
    assert "ccLocalSingle = true" in body


def test_council_builder_between_floor_and_recommended_allows_multi_no_banner(
    tmp_path, monkeypatch
):
    # 24-48GB: council IS allowed (clears the hard floor) - no "single model" banner - but the JS
    # constants for the graduated speed caveat must still be correct so the client shows the stronger
    # "not recommended below 48GB" warning once 2+ models are picked (client-side, so asserted via the
    # rendered constants rather than executing JS here).
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (32.0, "gpu"))
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="local-floor-banner"' not in body
    assert "ccLocalSingle = false" in body
    assert "CC_MEM_GB = 32.0" in body
    assert "CC_FLOOR_GB = 24.0" in body
    assert "CC_RECOMMENDED_GB = 48.0" in body


def test_council_builder_above_recommended_no_floor_banner(tmp_path, monkeypatch):
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (64.0, "gpu"))
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'id="local-floor-banner"' not in body
    assert "ccLocalSingle = false" in body
    assert "CC_MEM_GB = 64.0" in body


def test_cloud_models_are_the_full_self_serve_catalog(tmp_path):
    # cloud_models = the whole self-serve catalog (no local memory limit), so a cloud/inference user can
    # pick models bigger than their machine fits - never fewer than the fitting-locally set.
    from anthill.web.app import _model_picker_view

    view = _model_picker_view(None)
    cloud = view["cloud_models"]
    assert cloud and all(m.get("tag") and "params_b" in m for m in cloud)
    fitting_local = [m for fam in view["families"] for m in fam["models"] if m["fits"]]
    assert len(cloud) >= len(fitting_local)


def test_council_lead_is_the_model_ticked_first_not_the_topmost(tmp_path, monkeypatch):
    # Bug fix: the lead is the model the user ticks FIRST (selection order, carried in council_lead),
    # not whichever sits highest in the list. council_models arrives in list order.
    import json

    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    monkeypatch.setattr(app_mod, "_start_model_pull", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda *a, **k: None)
    # A 3-member local council is gated by the RAM floor (see the floor/gate tests below) - mock a
    # machine comfortably above it so this test exercises lead ordering, not the gate.
    monkeypatch.setattr(sizing, "local_hardware", lambda: (48.0, "gpu"))
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    c.post(
        "/setup/model",
        data={
            "compute": "local",
            "council_models": ["qwen2.5:7b", "llama3.1:8b", "gemma2:9b"],  # list order
            "council_lead": "gemma2:9b",  # but the user ticked this one first
        },
        follow_redirects=False,
    )
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    members = [m["model"] for m in json.loads(cfg.org_council_members or "[]")]
    assert members[0] == "gemma2:9b"  # the first-ticked model leads, despite being last in the list
    assert cfg.ollama_model == "gemma2:9b"


def test_local_council_below_ram_floor_is_refused(tmp_path, monkeypatch):
    # A machine under _LOCAL_COUNCIL_RAM_FLOOR_GB can't save a multi-model LOCAL council at all - not
    # even one that would technically fit in RAM - because running 2-3 models concurrently on one shared
    # memory bus is slow well before it stops fitting (Anthill Dev Council finding). A single model is
    # unaffected (checked in the next test).
    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # under the 24GB floor
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={
            "compute": "local",
            "council_models": ["qwen3.5:0.8b", "gemma3:1b"],
        },  # tiny; would fit
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=local_council_floor" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    import json as _json

    assert cfg is None or _json.loads(cfg.org_council_members or "[]") == []  # nothing committed


def test_local_council_below_ram_floor_still_allows_a_single_model(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    monkeypatch.setattr(app_mod, "_start_model_pull", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda *a, **k: None)
    monkeypatch.setattr(sizing, "local_hardware", lambda: (16.0, "apple"))  # under the 24GB floor
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={"compute": "local", "council_models": ["qwen3.5:0.8b"]},  # a single model - the floor
        # only gates 2+ members
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "qwen3.5:0.8b"


def test_local_council_above_floor_but_too_big_to_fit_is_refused(tmp_path, monkeypatch):
    # Above the RAM floor, the PRECISE per-model fit gate (mirroring the org admin's council editor)
    # still applies: two real models whose summed 4-bit memory footprint exceeds the machine's budget
    # are refused, even though the machine clears the coarse 24GB floor.
    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (24.0, "gpu"))  # clears the floor...
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        # gpt-oss:20b (20B) + qwen3.5:27b (27B) summed 4-bit cost exceeds a 24GB budget
        data={"compute": "local", "council_models": ["gpt-oss:20b", "qwen3.5:27b"]},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=local_council_too_big" in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    import json as _json

    assert cfg is None or _json.loads(cfg.org_council_members or "[]") == []  # nothing committed


def test_local_council_above_floor_and_fitting_is_saved(tmp_path, monkeypatch):
    import json

    import anthill.web.app as app_mod
    from anthill.hosting import sizing

    monkeypatch.setattr(app_mod, "_start_model_pull", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda *a, **k: None)
    monkeypatch.setattr(sizing, "local_hardware", lambda: (48.0, "gpu"))
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    r = c.post(
        "/setup/model",
        data={"compute": "local", "council_models": ["qwen3.5:0.8b", "gemma3:1b"]},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error" not in r.headers["location"]
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    members = [m["model"] for m in json.loads(cfg.org_council_members or "[]")]
    assert members == ["qwen3.5:0.8b", "gemma3:1b"]


def test_available_models_lists_the_council_lead_first():
    # Powers the agent model selectors (a picker, not a blank field): the account's open models, lead
    # first, deduped. Built from a lightweight stand-in for OrgSettings so it stays model-free.
    import json
    import types

    from anthill.web.app import _available_models

    cfg = types.SimpleNamespace(
        org_council_members=json.dumps(
            [{"model": "gemma2:9b"}, {"model": "qwen2.5:7b"}, {"model": "gemma2:9b"}]
        ),
        ollama_model="gemma2:9b",
    )
    assert _available_models(cfg) == ["gemma2:9b", "qwen2.5:7b"]  # lead first, deduped
    # No council configured yet: fall back to the single served model.
    solo = types.SimpleNamespace(org_council_members="", ollama_model="llama3.1:8b")
    assert _available_models(solo) == ["llama3.1:8b"]


def test_available_models_falls_back_to_org_model_for_hosted_single_model_accounts():
    # A single model on the cloud/inference-provider tier is recorded in org_model, NOT ollama_model
    # (_apply_solo_compute's "cloud"/"provider" branches never write org_council_members for a lone
    # member, and never touch ollama_model either - that field is local-tier-only). Without this
    # fallback, a hosted single-model account's agent selector would show nothing to pick.
    import types

    from anthill.web.app import _available_models

    hosted = types.SimpleNamespace(org_council_members="", ollama_model="", org_model="qwen2.5:7b")
    assert _available_models(hosted) == ["qwen2.5:7b"]


def test_available_models_moves_the_lead_to_front_even_if_present_elsewhere():
    # Defensive: don't assume _members_from_cfg's index 0 is always the lead - move whichever model
    # ollama_model/org_model names to the front regardless of its position in the council list.
    import json
    import types

    from anthill.web.app import _available_models

    cfg = types.SimpleNamespace(
        org_council_members=json.dumps(
            [{"model": "qwen2.5:7b"}, {"model": "llama3.1:8b"}, {"model": "gemma2:9b"}]
        ),
        ollama_model="gemma2:9b",  # the lead, but last in the stored member list
    )
    assert _available_models(cfg) == ["gemma2:9b", "qwen2.5:7b", "llama3.1:8b"]


def test_order_council_puts_the_lead_first():
    from anthill.web.app import _order_council

    assert _order_council(["a", "b", "c"], "c") == ["c", "a", "b"]  # lead below -> promoted
    assert _order_council(["a", "b", "c"], "a") == ["a", "b", "c"]  # lead already first
    assert _order_council(["a", "b"], "") == ["a", "b"]  # no lead -> list order
    assert _order_council(["a", "b"], "z") == ["a", "b"]  # lead not in list -> ignored
    assert _order_council(["a", "a", "b", "c", "d"], "b") == [
        "b",
        "a",
        "c",
    ]  # dedup + cap 3, lead first


def test_smallest_gpu_for_model_picks_a_fitting_tier():
    from anthill.hosting import sizing
    from anthill.web.app import _smallest_gpu_for_model

    small = _smallest_gpu_for_model(7.0)  # a 7B fits a small GPU
    big = _smallest_gpu_for_model(70.0)  # a 70B needs a bigger one
    tiers = {t.key: t.vram_gb for t in sizing.GPU_TIERS}
    assert small in tiers and big in tiers
    assert tiers[small] <= tiers[big]  # bigger model -> at least as large a GPU


def test_smallest_gpu_for_model_falls_back_to_the_largest_when_nothing_fits():
    # A model too big for every tier must fall back to the LARGEST GPU (best-effort), not the smallest.
    # The old code did min() over the full list and silently picked the smallest - guaranteed to fail
    # to load after provisioning (and paying for) the GPU.
    from anthill.hosting import sizing
    from anthill.web.app import _smallest_gpu_for_model

    picked = _smallest_gpu_for_model(10_000.0)  # bigger than any single-GPU tier can serve
    largest = max(sizing.GPU_TIERS, key=lambda t: t.vram_gb)
    assert picked == largest.key


def test_local_option_hidden_for_org_accounts(tmp_path):
    c, _ = _client(tmp_path, topology="org")
    body = c.get("/setup/model").text
    assert "This machine (local)" not in body
    assert "Self-provisioned cloud" in body
    assert "Self-hosted Mac mini" in body


def test_local_option_shown_for_solo_accounts(tmp_path):
    # Solo accounts get the 2-tier chooser, whose local tier reads "Your machine".
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert "Your machine" in body and 'id="cc-tierseg"' in body


def test_setup_model_list_is_a_council_builder_with_fit_and_origin(tmp_path):
    # The setup model step is a council builder (checkboxes posting council_models), with per-model fit
    # verdicts and the origin/sovereignty filter - fitting models on top.
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert 'class="mp-list"' in body and 'name="council_models"' in body  # council checkboxes
    assert 'class="fit' in body  # per-model fit verdict
    assert "Model origin:" in body and "mpFilter" in body  # the origin filter + its handler
    for region in (">US<", ">EU<", ">China<", ">Other<"):
        assert region in body, region


def test_setup_model_list_collapses_models_too_big_for_this_machine(tmp_path, monkeypatch):
    # Fitting models are the selectable council; models too big for THIS machine are NOT council
    # checkboxes - they drop into a collapsed "too big" section (run them in your cloud instead).
    from anthill.hosting import sizing

    monkeypatch.setattr(sizing, "local_hardware", lambda: (8.0, "apple"))  # a small 8GB box
    c, _ = _client(tmp_path, topology="solo", chosen=False)
    body = c.get("/setup/model").text
    assert "Models too big for this machine" in body  # the collapsed section
    assert 'class="mp-row nofit"' in body  # too-big rows are display-only, not council checkboxes


def test_solo_cloud_without_a_provider_is_rejected(tmp_path):
    # The Solo cloud tier now goes through the 2-tier chooser: a provider + API key are required. A bare
    # compute=cloud (no provider) is rejected rather than silently doing nothing. (The full happy path
    # is test_cloud_provider_records_plan_and_autopicks_gpu.)
    c, _ = _client(tmp_path, chosen=False)  # _client defaults to solo topology
    r = c.post("/setup/model", data={"compute": "cloud"}, follow_redirects=False)
    assert r.status_code == 302
    assert "error=provider" in r.headers["location"]


def test_solo_mac_mini_compute_choice_routes_to_personal_connect(tmp_path):
    c, app_mod = _client(tmp_path, chosen=False)
    r = c.post("/setup/model", data={"compute": "mac_mini"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/personalize#model"
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.local_model_chosen is True
    assert cfg.solo_compute == "mac_mini"


def test_org_cloud_compute_choice_still_goes_to_admin_backend(tmp_path):
    # A genuine org configures its shared backend on the admin page, so its cloud choice still routes
    # there - the split is topology-driven.
    c, app_mod = _client(tmp_path, topology="org", chosen=False)
    r = c.post("/setup/model", data={"compute": "cloud"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/settings/organization"
    assert app_mod._SessionFactory().query(OrgSettings).first().solo_compute == "cloud"


def test_accepting_a_council_suggestion_registers_a_resolvable_council(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path)
    suggestion = sizing.LocalSetupSuggestion(
        mode="council",
        members=[_pick("a", 8, "a:8b"), _pick("b", 7, "b:7b"), _pick("c", 4, "c:4b")],
        hw_label="128 GB apple",
    )
    # suggest_local_setup is imported LOCALLY inside model_picker_post (matching this codebase's own
    # convention), so it must be patched on its source module, not on app_mod.
    monkeypatch.setattr(sizing, "suggest_local_setup", lambda *a, **k: suggestion)
    pulled = []
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: pulled.append((tag, k)))
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)

    r = c.post(
        "/setup/model", data={"compute": "local", "accept_suggestion": "1"}, follow_redirects=False
    )
    assert r.status_code == 302 and r.headers["location"] == "/"

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    assert cfg.ollama_model == "a:8b"  # the lead
    assert cfg.local_model_chosen is True

    # proves it actually resolves as a real council, not just that JSON was saved
    resolved = resolve_council_backends(cfg, decrypt=lambda x: x)
    assert len(resolved) == 3
    assert [r.index for r in resolved] == [0, 1, 2]

    # all 3 were queued for download, none racing to auto-activate over the lead we set explicitly
    assert {tag for tag, _ in pulled} == {"a:8b", "b:7b", "c:4b"}
    assert all(k.get("activate_when_done") is False for _tag, k in pulled)


def test_accepting_a_single_suggestion_does_not_touch_org_council_members(tmp_path, monkeypatch):
    c, app_mod = _client(tmp_path)
    pick = _pick("a", 8, "a:8b")
    suggestion = sizing.LocalSetupSuggestion(mode="single", members=[pick], hw_label="16 GB apple")
    monkeypatch.setattr(sizing, "suggest_local_setup", lambda *a, **k: suggestion)
    # model_picker_post validates `choice` against the real LOCAL_CATALOG (never pulls an
    # unrecognized tag) - patch it to include this test's synthetic pick so that check passes.
    monkeypatch.setattr(sizing, "LOCAL_CATALOG", (pick.recommended,))
    monkeypatch.setattr(app_mod, "_start_model_pull", lambda oid, tag, **k: None)
    monkeypatch.setattr(app_mod, "_maybe_autopull_vision", lambda oid: None)

    r = c.post(
        "/setup/model", data={"compute": "local", "accept_suggestion": "1"}, follow_redirects=False
    )
    assert r.status_code == 302
    cfg = app_mod._SessionFactory().query(OrgSettings).first()
    assert cfg.ollama_model == "a:8b"
    assert (
        cfg.org_council_members == "[]"
    )  # unchanged default - single model needs no council entry
