"""Tasks (the scheduled second surface) now keep a per-run history (TaskRun), like Agents.

The scheduler tick opens a TaskRun in ``running`` before the run and closes it after; a startup sweep
closes any row orphaned by a restart; the task result page renders the history. Model-free.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _engine(tmp_path, name="t.db"):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    _s = sessionmaker(bind=eng)()
    seed_org_and_users(_s, user_ids=())  # tasks here use created_by=None; org 1 must exist
    _s.commit()
    _s.close()
    return eng


def _seed_due_task(tmp_path, monkeypatch, **kw):
    """A task due to run now, created_by=None so the tick's personal-memory + push blocks are skipped
    (keeps the test model-free). Returns (engine, Session, task_id)."""
    from anthill.web import scheduler
    from anthill.web.db import ScheduledTask

    eng = _engine(tmp_path)
    S = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = S()
    base = {
        "org_id": 1,
        "created_by": None,
        "title": "T",
        "goal": "g",
        "schedule": "once",
        "status": "pending",
        "next_run_at": datetime.now(timezone.utc),
    }
    base.update(kw)
    t = ScheduledTask(**base)
    s.add(t)
    s.commit()
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda t, r, db: None)
    return eng, S, t.id


def test_tick_records_a_task_run(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import TaskRun

    eng, S, tid = _seed_due_task(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, "_run_task", lambda t, db: "did the task")
    scheduler._tick(eng)

    runs = S().query(TaskRun).filter(TaskRun.task_id == tid).all()
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "ok" and r.result == "did the task"
    assert r.trigger == "scheduled"  # directly seeded due work is a scheduled occurrence
    assert r.finished_at is not None and r.duration_ms is not None


def test_tick_records_an_error_task_run(tmp_path, monkeypatch):
    from anthill.web import scheduler
    from anthill.web.db import TaskRun

    eng, S, tid = _seed_due_task(tmp_path, monkeypatch)

    def _boom(t, db):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(scheduler, "_run_task", _boom)
    scheduler._tick(eng)

    r = S().query(TaskRun).filter(TaskRun.task_id == tid).one()
    assert r.status == "error" and "kaboom" in r.error and r.result == ""
    assert r.verify_needs_review is False  # a failed run leaves no verdict


def test_sweep_closes_stale_running_task_runs(tmp_path):
    from anthill.web import scheduler
    from anthill.web.db import ScheduledTask, TaskRun

    eng = _engine(tmp_path, "s.db")
    S = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = S()
    t = ScheduledTask(org_id=1, title="T", goal="g", schedule="once", status="running")
    s.add(t)
    s.commit()
    s.add(
        TaskRun(
            org_id=1,
            task_id=t.id,
            status="running",
            started_at=datetime.now(timezone.utc),
            finished_at=None,
        )
    )
    s.commit()
    scheduler._sweep_stale_running_runs(eng)  # simulates a restart while a run was in flight
    r = S().query(TaskRun).filter(TaskRun.task_id == t.id).one()
    assert r.status == "error" and "interrupted" in r.error and r.finished_at is not None


# ── the result page renders the history ─────────────────────────────────────────


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = _engine(tmp_path, "app.db")
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="a@acme.com", role="admin", active=True)
    member = User(org_id=org.id, email="b@acme.com", role="member", active=True)
    s.add_all([admin, member])
    s.commit()
    client = TestClient(app_mod.app)
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return client, {"org": org.id, "a": admin.id, "b": member.id}


def _auth(client, uid, org_id, role):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_task_result_page_localizes_task_and_history_timestamps(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask, TaskRun

    client, ids = _app(tmp_path, monkeypatch)
    last_run = datetime(2030, 9, 1, 10, 30, tzinfo=timezone.utc)
    next_run = datetime(2030, 9, 2, 10, 30, tzinfo=timezone.utc)
    history_run = datetime(2030, 9, 1, 10, 35, tzinfo=timezone.utc)
    s = app_mod._SessionFactory()
    task = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Daily report",
        goal="g",
        schedule="daily",
        timezone="Europe/Madrid",
        status="pending",
        last_run_at=last_run,
        next_run_at=next_run,
        run_count=1,
    )
    s.add(task)
    s.flush()
    s.add(
        TaskRun(
            org_id=ids["org"],
            task_id=task.id,
            trigger="scheduled",
            status="ok",
            result="done",
            finished_at=history_run,
        )
    )
    s.commit()

    page = BeautifulSoup(client.get(f"/tasks/{task.id}/result").text, "html.parser")
    timestamps = {node["data-utc"]: node.get_text(strip=True) for node in page.select("[data-utc]")}

    assert timestamps == {
        last_run.isoformat(): "2030-09-01 10:30 UTC",
        next_run.isoformat(): "2030-09-02 10:30 UTC",
        history_run.isoformat(): "2030-09-01 10:35 UTC",
    }


def test_task_result_page_running_label_requires_active_task_run(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup

    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask, TaskRun

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    stale = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Stale running task",
        goal="g",
        schedule="daily",
        status="running",
    )
    active = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Active running task",
        goal="g",
        schedule="daily",
        status="running",
    )
    next_run = datetime(2030, 9, 2, 10, 30, tzinfo=timezone.utc)
    active_with_future = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Active task with future run",
        goal="g",
        schedule="daily",
        status="running",
        next_run_at=next_run,
    )
    s.add_all([stale, active, active_with_future])
    s.flush()
    s.add_all(
        [
            TaskRun(
                org_id=ids["org"],
                task_id=task.id,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            for task in (active, active_with_future)
        ]
    )
    s.commit()

    def task_summary(task_id):
        page = BeautifulSoup(client.get(f"/tasks/{task_id}/result").text, "html.parser")
        return " ".join(page.find("b", string="Next run:").parent.get_text(" ", strip=True).split())

    assert "Next run: Not scheduled" in task_summary(stale.id)
    assert "Next run: Running now" in task_summary(active.id)
    assert "Next run: 2030-09-02 10:30 UTC" in task_summary(active_with_future.id)


def test_task_result_page_shows_run_history(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask, TaskRun

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    t = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Rep",
        goal="g",
        schedule="once",
        status="done",
        run_count=1,
    )
    s.add(t)
    s.flush()
    s.add(
        TaskRun(
            org_id=ids["org"],
            task_id=t.id,
            trigger="manual",
            status="ok",
            result="a distinctive task result",
            finished_at=datetime.now(timezone.utc),
        )
    )
    s.commit()
    page = client.get(f"/tasks/{t.id}/result").text
    assert "Run history" in page and "a distinctive task result" in page


def test_tasks_list_uses_the_right_semantic_badge_per_status(tmp_path, monkeypatch):
    # A running task was previously marked with badge-admin (the wrong class entirely - meant for
    # admin-role tags), and a failed one shared badge-member with a merely-cancelled task, losing the
    # distinction between "this needs your attention" and "you cancelled this yourself" (founder ask
    # 2026-09-28: color should encode meaning).
    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add_all(
        [
            ScheduledTask(
                org_id=ids["org"],
                created_by=ids["a"],
                title="Running task",
                goal="g",
                schedule="once",
                status="running",
            ),
            ScheduledTask(
                org_id=ids["org"],
                created_by=ids["a"],
                title="Failed task",
                goal="g",
                schedule="once",
                status="failed",
            ),
            ScheduledTask(
                org_id=ids["org"],
                created_by=ids["a"],
                title="Cancelled task",
                goal="g",
                schedule="once",
                status="cancelled",
            ),
        ]
    )
    s.commit()
    page = client.get("/tasks").text
    assert "badge-admin" not in page
    assert "badge-running" in page
    assert "badge-danger" in page
    assert "badge-member" in page  # cancelled keeps the neutral tan, unlike failed


# ── P1: task defaults (the per-run cost cap) ────────────────────────────────────


def test_tasks_settings_save_and_clamp(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import OrgSettings

    client, ids = _app(tmp_path, monkeypatch)

    def _cfg():
        return (
            app_mod._SessionFactory()
            .query(OrgSettings)
            .filter(OrgSettings.org_id == ids["org"])
            .first()
        )

    client.post("/tasks/settings", data={"task_max_steps": "7"}, follow_redirects=False)
    assert _cfg().task_max_steps == 7
    client.post("/tasks/settings", data={"task_max_steps": "999"}, follow_redirects=False)
    assert _cfg().task_max_steps == 50  # clamped to the cost-rail cap


def test_task_defaults_card_is_admin_only(tmp_path, monkeypatch):
    client, ids = _app(tmp_path, monkeypatch)
    assert "Task settings for administrators" in client.get("/tasks").text  # admin (default auth)
    _auth(client, ids["b"], ids["org"], "member")
    assert "Task settings for administrators" not in client.get("/tasks").text


def test_task_result_page_shows_an_error_latest_result_as_plain_text(tmp_path, monkeypatch):
    # #89: a latest result that starts with "ERROR:" is a failure, not Markdown. It stays literal text under
    # the error badge and is never offered to the Markdown renderer.
    from bs4 import BeautifulSoup

    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    t = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Err",
        goal="g",
        schedule="once",
        status="failed",
        last_result="ERROR: boom **not bold** <b>x</b>",
    )
    s.add(t)
    s.commit()
    soup = BeautifulSoup(client.get(f"/tasks/{t.id}/result").text, "html.parser")
    box = soup.select_one("pre#task-result.task-error")
    assert box is not None and box.get_text() == "ERROR: boom **not bold** <b>x</b>"
    assert box.find("b") is None and "data-md" not in box.attrs
    assert soup.select_one(".badge-danger") is not None
    assert (
        soup.find(id="task-result-src") is not None
    )  # the stored text is still available to Save as snippet
