"""Dashboard activation checklist gains a 'Set up your organization' step (P3): it is the first
step for an org deployment until the backend is validated, and is absent for a solo deployment."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _client(
    tmp_path,
    monkeypatch,
    *,
    topology="org",
    activated=False,
    role="admin",
    local_model_chosen=False,
    solo_compute="local",
):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

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
    user = User(org_id=org.id, email=f"{role}@acme.com", role=role, active=True)
    cfg = OrgSettings(
        org_id=org.id,
        deployment_topology=topology,
        local_model_chosen=local_model_chosen,
        solo_compute=solo_compute,
    )
    if activated:
        cfg.org_backend_status = "validated"
        cfg.org_model_endpoint = "https://gpu.acme.example/v1"
    s.add_all([user, cfg])
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, role))
    return client


def test_dashboard_shows_vitals_tiles_and_cores(tmp_path, monkeypatch):
    # The dashboard leads with a greeting + model-status pill, four real stat tiles (answers / training /
    # wiki / memory), and the two "cores" (model council + living wiki). Counts come from the route (real
    # data), never mocked - a fresh install honestly reads 0.
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    page = client.get("/").text
    assert "model-pill" in page  # the model-status pill
    for label in ("Answers given", "Training examples", "Wiki entries", "Memory items"):
        assert label in page, label  # the four stat tiles
    # A single model is not a "council" (founder report, 2026-09-30: "There is no council with 16 GB
    # of memory") - the heading only says "Model council" once 2+ models are actually configured.
    assert ">Model<" in page and ">Living wiki<" in page  # the two cores
    assert ">0<" in page  # a fresh install reads real zeros, not placeholder numbers


def test_org_setup_step_shown_first_until_activated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, topology="org", activated=False)
    page = client.get("/").text
    assert "Set up your organization" in page
    assert 'href="/settings/organization"' in page  # the step's CTA
    # it is the first activation step, ahead of inviting the team
    assert page.index("Set up your organization") < page.index("Invite your team")


def test_org_setup_step_absent_for_solo(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, topology="solo", activated=False)
    page = client.get("/").text
    assert "Set up your organization" not in page  # solo has no org backend to set up


def test_org_setup_step_done_when_activated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, topology="org", activated=True)
    page = client.get("/").text
    # the step is present but completed: no "Set up" CTA button for that row (the org page is still
    # linked from the sidebar - the rail link, with a different class, is unrelated to the step's CTA)
    assert "Set up your organization" in page
    assert 'href="/settings/organization" class="btn btn-primary btn-sm"' not in page


def test_org_setup_step_no_dead_end_link_for_non_admin_member(tmp_path, monkeypatch):
    # A non-admin member can't reach the admin-only /settings/organization, so they get "Ask an
    # admin" text instead of a link that would just 403 (issue: previously members got no CTA at all).
    client = _client(
        tmp_path,
        monkeypatch,
        topology="org",
        activated=False,
        role="member",
        local_model_chosen=True,
    )
    page = client.get("/").text
    assert "Set up your organization" in page
    assert "Ask an admin" in page
    assert 'href="/settings/organization"' not in page


def _set_council(monkeypatch, models, *, pulling=""):
    """Write a council (index 0 = lead) onto the single OrgSettings row and return the served lead."""
    import json

    import anthill.web.app as app_mod

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.org_council_members = json.dumps([{"model": m} for m in models])
    cfg.ollama_model = models[0] if models else ""
    cfg.local_model_pulling = pulling
    s.commit()


def test_dashboard_pill_and_council_card_reflect_the_whole_council(tmp_path, monkeypatch):
    # Founder: "it says which model on your machine but we have 3 models." The header pill and the
    # Model council card must reflect the whole council (lead + count), not just ollama_model.
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    _set_council(monkeypatch, ["gemma2:9b", "qwen2.5:7b", "llama3.1:8b"])
    page = client.get("/").text
    assert "Council of 3" in page  # the pill names the council, not one model
    for m in ("gemma2:9b", "qwen2.5:7b", "llama3.1:8b"):
        assert m in page  # every member is listed on the council card
    assert "&middot; lead" in page  # the lead is marked


def test_dashboard_solo_shows_workspace_not_organization(tmp_path, monkeypatch):
    # A solo account has no organisation - the old dashboard showed an "Organization" card (with name +
    # role) and a "Settings" quick action that just duplicated the left rail. Both are gone for solo.
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    _set_council(monkeypatch, ["gemma2:9b"])
    page = client.get("/").text
    assert "<h3>Your workspace</h3>" in page
    assert "<h3>Organization</h3>" not in page
    assert "Manage users" not in page  # no team to manage in a solo account
    # the Settings quick action (which duplicated the rail) is gone; the rail's own Settings link stays
    assert 'href="/settings"' not in page


def test_dashboard_council_status_tracks_download_state(tmp_path, monkeypatch):
    # Founder saw "Model council running" while the banner said the model was still downloading. The
    # council status is "Preparing" while a local pull is in flight, and "Running" once it is done.
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    _set_council(monkeypatch, ["gemma2:9b"], pulling="gemma2:9b")
    assert 'id="council-status">Preparing' in client.get("/").text
    _set_council(monkeypatch, ["gemma2:9b"], pulling="")
    assert 'id="council-status">Running' in client.get("/").text


def test_dashboard_shows_no_inference_provider_by_default(tmp_path, monkeypatch):
    # Founder feedback 2026-09-28: "we don't show which inference provider we have connected" - the
    # Model council card now states inference-provider status too, mirroring Settings' own
    # "Where your AI runs" card (compute location + inference provider, side by side).
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    page = client.get("/").text
    assert "No inference provider attached" in page
    assert 'href="/personalize"' in page


def test_dashboard_shows_attached_inference_provider(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.escalation_provider = "berget"
    s.commit()
    page = client.get("/").text
    assert "Berget AI attached" in page
    assert "No inference provider attached" not in page


def test_dashboard_active_now_hidden_with_nothing_going_on(tmp_path, monkeypatch):
    # A fresh account has no tasks, agents, or chats yet - the "Active now" card must not render an
    # empty shell.
    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    page = client.get("/").text
    assert "Active now" not in page


def test_dashboard_shows_upcoming_task_and_recent_chat(tmp_path, monkeypatch):
    # Founder feedback 2026-09-28: the dashboard needed "something more active - tasks that are
    # currently scheduled or coming up next, chats that are open and need your attention."
    from datetime import datetime, timedelta, timezone

    import anthill.web.app as app_mod
    from anthill.web.db import Conversation, ScheduledTask

    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    user = s.query(User).first()
    task = ScheduledTask(
        org_id=org.id,
        created_by=user.id,
        title="Weekly digest",
        goal="Summarize the week",
        schedule="weekly",
        status="pending",
        plane="solo",
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    conv = Conversation(org_id=org.id, user_id=user.id, title="Pricing question")
    s.add_all([task, conv])
    s.commit()
    page = client.get("/").text
    assert "Active now" in page
    assert "Tasks &amp; agents" in page and "Weekly digest" in page
    assert "Recent chats" in page and "Pricing question" in page


def test_dashboard_shows_running_task(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask, TaskRun

    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    user = s.query(User).first()
    task = ScheduledTask(
        org_id=org.id,
        created_by=user.id,
        title="Long research job",
        goal="Go deep",
        schedule="once",
        status="running",
        plane="solo",
    )
    s.add(task)
    s.flush()
    s.add(TaskRun(org_id=org.id, task_id=task.id, status="running"))
    s.commit()
    page = client.get("/").text
    assert "badge-running" in page and "Long research job" in page


def test_dashboard_does_not_leak_another_users_solo_task(tmp_path, monkeypatch):
    # A solo-plane task is private to its creator (same rule as /tasks) - a second user's personal
    # task must never appear on this user's dashboard.
    from datetime import datetime, timedelta, timezone

    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask

    client = _client(tmp_path, monkeypatch, topology="solo", role="admin", local_model_chosen=True)
    s = app_mod._SessionFactory()
    org = s.query(Organization).first()
    other = User(org_id=org.id, email="other@acme.com", role="member", active=True)
    s.add(other)
    s.flush()
    s.add(
        ScheduledTask(
            org_id=org.id,
            created_by=other.id,
            title="Someone else's private task",
            goal="Not yours",
            schedule="once",
            status="pending",
            plane="solo",
            next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    s.commit()
    page = client.get("/").text
    assert "Someone else's private task" not in page


def test_solo_compute_setup_step_shown_and_hidden(tmp_path, monkeypatch):
    # Solo users previously got no compute-setup CTA at all (the checklist lived entirely under the
    # org-admin branch). Picking "cloud" in the wizard doesn't mean a backend is actually connected
    # yet, so the step stays until it is - this is the case a not-yet-chosen solo account never even
    # reaches (the pre-existing local_model_chosen gate redirects it to /setup/model first).
    (tmp_path / "not-connected").mkdir()
    (tmp_path / "local").mkdir()
    not_connected = _client(
        tmp_path / "not-connected",
        monkeypatch,
        topology="solo",
        role="admin",
        local_model_chosen=True,
        solo_compute="cloud",
    )
    page = not_connected.get("/").text
    assert "Set up your compute" in page
    assert 'href="/setup/model"' in page

    local = _client(
        tmp_path / "local",
        monkeypatch,
        topology="solo",
        role="admin",
        local_model_chosen=True,
        solo_compute="local",
    )
    # The checklist also gained a separate "Connect an inference provider" step (that one's own
    # test is test_dashboard_activation_prompts_to_connect_an_inference_provider below) - mark it
    # done too so the whole "Finish setting up" card can fully hide, isolating this test to the
    # compute step it actually means to check.
    import anthill.web.app as app_mod

    s = app_mod._SessionFactory()
    cfg = s.query(OrgSettings).first()
    cfg.escalation_provider = "berget"
    s.commit()
    page = local.get("/").text
    assert "Set up your compute" not in page


def test_dashboard_activation_prompts_to_connect_an_inference_provider(tmp_path, monkeypatch):
    # Setup can finish without an inference provider attached (the wizard no longer blocks on a
    # keyless pick); this is where that gets picked back up instead of being forgotten.
    client = _client(
        tmp_path,
        monkeypatch,
        topology="solo",
        role="admin",
        local_model_chosen=True,
        solo_compute="local",
    )
    page = client.get("/").text
    assert "Connect an inference provider" in page
    assert 'href="/personalize#model"' in page
