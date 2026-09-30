"""Tasks list at scale (#294 sibling): /tasks paginates and shows each task's scope, so an org with
many scheduled tasks no longer renders them all in one flat table with no plane visible."""

from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.crypto import make_token


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

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
    user = User(org_id=org.id, email="u@acme.com", role="admin", active=True)
    s.add(user)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))
    return client, app_mod, org.id


def _mktask(app_mod, org_id, title, plane="solo", age=0, **fields):
    from anthill.web.db import ScheduledTask, User

    s = app_mod._SessionFactory()
    try:
        # A task always has an owner in practice (created via /tasks/create). Own it by the org's user so
        # it shows in that user's list (tasks are owner-scoped, #597), not an unrealistic ownerless task.
        owner = s.query(User).filter(User.org_id == org_id).first()
        # A distinct created_at per task keeps the ordering deterministic (newest first).
        when = datetime.now(timezone.utc) - timedelta(seconds=age)
        t = ScheduledTask(
            org_id=org_id,
            created_by=owner.id if owner else None,
            title=title,
            goal="do the thing",
            plane=plane,
            created_at=when,
            **fields,
        )
        s.add(t)
        s.commit()
        return t.id
    finally:
        s.close()


def test_tasks_are_paginated(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    for i in range(45):  # more than one 40-per-page page
        _mktask(app_mod, org_id, f"Task number {i:02d}", age=i)
    r1 = client.get("/tasks")
    assert r1.status_code == 200
    assert "Task number 00" in r1.text  # newest, page 1
    assert "Task number 44" not in r1.text  # oldest is off page 1
    assert "Page 1 of 2" in r1.text
    r2 = client.get("/tasks?page=2")
    assert r2.status_code == 200
    assert "Task number 44" in r2.text  # reachable on page 2


def test_task_scope_is_shown(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    _mktask(app_mod, org_id, "An org-scope task", plane="org")
    r = client.get("/tasks")
    assert r.status_code == 200
    assert "Scope" in r.text  # the new column header
    assert "Org" in r.text  # the task's plane, previously invisible in the list


def test_task_list_shows_localizable_next_run(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    next_run = datetime(2030, 9, 2, 10, 30, tzinfo=timezone.utc)
    _mktask(
        app_mod,
        org_id,
        "Daily briefing",
        schedule="daily",
        timezone="Europe/Madrid",
        next_run_at=next_run,
    )

    page = BeautifulSoup(client.get("/tasks").text, "html.parser")
    row = page.find("b", string="Daily briefing").find_parent("tr")
    schedule = row.select("td")[1]
    timestamp = schedule.find(attrs={"data-utc": next_run.isoformat()})

    assert "Next run:" in schedule.get_text(" ", strip=True)
    assert timestamp.get_text(strip=True) == "09-02 10:30 UTC"


def test_task_list_explains_next_run_state(tmp_path, monkeypatch):
    from anthill.web.db import TaskRun

    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    stale_next_run = datetime(2030, 9, 2, 10, 30, tzinfo=timezone.utc)
    cases = (
        ("Completed one-off", "once", "done", "Complete", stale_next_run),
        ("Cancelled daily task", "daily", "cancelled", "Cancelled", stale_next_run),
        ("Unscheduled one-off", "once", "pending", "Not scheduled", None),
        ("Stale running task", "daily", "running", "Not scheduled", None),
        ("Active running task", "daily", "running", "Running now", None),
        (
            "Active task with future run",
            "daily",
            "running",
            "09-02 10:30 UTC",
            stale_next_run,
        ),
    )
    task_ids = {}
    for age, (title, schedule, status, _, next_run) in enumerate(cases):
        task_ids[title] = _mktask(
            app_mod,
            org_id,
            title,
            schedule=schedule,
            status=status,
            next_run_at=next_run,
            age=age,
        )

    session = app_mod._SessionFactory()
    session.add_all(
        [
            TaskRun(
                org_id=org_id,
                task_id=task_ids[title],
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            for title in ("Active running task", "Active task with future run")
        ]
    )
    session.commit()
    session.close()

    page = BeautifulSoup(client.get("/tasks").text, "html.parser")
    for title, _, _, expected, _ in cases:
        row = page.find("b", string=title).find_parent("tr")
        schedule = " ".join(row.select("td")[1].get_text(" ", strip=True).split())
        assert f"Next run: {expected}" in schedule


def test_expanded_queue_row_spans_the_task_table(tmp_path, monkeypatch):
    client, app_mod, org_id = _app(tmp_path, monkeypatch)
    task_id = _mktask(app_mod, org_id, "Task with queued follow-ups")

    page = BeautifulSoup(client.get("/tasks").text, "html.parser")
    table = page.select_one("table")
    queue_cell = page.select_one(f"tr#q-{task_id} > td")

    assert queue_cell["colspan"] == str(len(table.select("thead th")))
