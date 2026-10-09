"""Acknowledge a task result that needs review without rewriting the verifier verdict.

Covers the column migration for legacy flagged rows, who may post the action, and run history:
the latest flagged result clears the Tasks-list warning, an older run cannot clear a newer one.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, ScheduledTask, TaskRun, User


def _engine(tmp_path, name="t.db"):
    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return eng


def _people(session):
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    owner = User(
        org_id=org.id, email="owner@acme.com", display_name="Owner", role="member", active=True
    )
    viewer = User(org_id=org.id, email="viewer@acme.com", role="member", active=True)
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    session.add_all([owner, viewer, admin])
    session.commit()
    return org, owner, viewer, admin


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    eng = _engine(tmp_path, "app.db")
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    session = app_mod._SessionFactory()
    org, owner, viewer, admin = _people(session)
    client = TestClient(app_mod.app)
    ids = {"org": org.id, "owner": owner.id, "viewer": viewer.id, "admin": admin.id}
    return client, ids, app_mod


def _auth(client, uid, org_id, role):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def _flagged_task(app_mod, ids, *, created_by, plane="solo", runs=1):
    session = app_mod._SessionFactory()
    task = ScheduledTask(
        org_id=ids["org"],
        created_by=created_by,
        title="Digest",
        goal="summarise the week",
        schedule="daily",
        status="done",
        plane=plane,
        last_result="a thin answer",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
        run_count=runs,
    )
    session.add(task)
    session.flush()
    created = []
    for index in range(runs):
        run = TaskRun(
            org_id=ids["org"],
            task_id=task.id,
            trigger="scheduled",
            status="ok",
            result=f"result {index}",
            verify_needs_review=True,
            verify_reason="goal mismatch",
            verify_confidence="0.20",
            finished_at=datetime(2030, 9, 1, 10, index, tzinfo=timezone.utc),
        )
        session.add(run)
        created.append(run)
    session.commit()
    return task.id, [run.id for run in created]


def _post_review(client, app_mod, task_id, run_id, uid):
    token = app_mod._task_review_token(uid, task_id, run_id)
    return client.post(
        f"/tasks/{task_id}/review-result",
        data={"run_id": str(run_id), "csrf_token": token},
        follow_redirects=False,
    )


def _task_row(app_mod, task_id):
    return app_mod._SessionFactory().get(ScheduledTask, task_id)


def _run_row(app_mod, run_id):
    return app_mod._SessionFactory().get(TaskRun, run_id)


def test_review_columns_migrate_onto_legacy_flagged_rows(tmp_path):
    from anthill.web.migrate import ensure_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, "
                "verify_needs_review BOOLEAN NOT NULL DEFAULT 0, "
                "verify_reason VARCHAR(400) NOT NULL DEFAULT '', "
                "verify_confidence VARCHAR(8) NOT NULL DEFAULT '')"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id INTEGER, "
                "status VARCHAR(12) NOT NULL DEFAULT 'ok', "
                "verify_needs_review BOOLEAN NOT NULL DEFAULT 0, "
                "verify_reason VARCHAR(400) NOT NULL DEFAULT '', "
                "verify_confidence VARCHAR(8) NOT NULL DEFAULT '')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_tasks (id, verify_needs_review, verify_reason, "
                "verify_confidence) VALUES (1, 1, 'goal mismatch', '0.20')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO task_runs (id, task_id, status, verify_needs_review, "
                "verify_reason, verify_confidence) VALUES (7, 1, 'ok', 1, 'goal mismatch', '0.20')"
            )
        )
    added = set(ensure_columns(eng))
    assert {
        "scheduled_tasks.result_reviewed_by",
        "scheduled_tasks.result_reviewed_at",
        "task_runs.reviewed_by",
        "task_runs.reviewed_at",
    } <= added
    with eng.connect() as conn:
        task = conn.execute(
            text(
                "SELECT verify_needs_review, verify_reason, result_reviewed_by, "
                "result_reviewed_at FROM scheduled_tasks WHERE id=1"
            )
        ).one()
        run = conn.execute(
            text(
                "SELECT verify_needs_review, verify_reason, reviewed_by, reviewed_at "
                "FROM task_runs WHERE id=7"
            )
        ).one()
    assert task.verify_needs_review == 1 and task.verify_reason == "goal mismatch"
    assert task.result_reviewed_by is None and task.result_reviewed_at is None
    assert run.verify_needs_review == 1 and run.verify_reason == "goal mismatch"
    assert run.reviewed_by is None and run.reviewed_at is None


def test_create_tables_restores_review_columns_without_clearing_legacy_flags(tmp_path):
    eng = _engine(tmp_path, "upgrade.db")
    session = sessionmaker(bind=eng)()
    org, owner, _viewer, _admin = _people(session)
    task = ScheduledTask(
        org_id=org.id,
        created_by=owner.id,
        title="Legacy",
        goal="g",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
    )
    session.add(task)
    session.flush()
    run = TaskRun(
        org_id=org.id,
        task_id=task.id,
        status="ok",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
    )
    session.add(run)
    session.commit()
    task_id, run_id = task.id, run.id
    session.close()

    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE scheduled_tasks DROP COLUMN result_reviewed_by"))
        conn.execute(text("ALTER TABLE scheduled_tasks DROP COLUMN result_reviewed_at"))
        conn.execute(text("ALTER TABLE task_runs DROP COLUMN reviewed_by"))
        conn.execute(text("ALTER TABLE task_runs DROP COLUMN reviewed_at"))
    db_mod.create_tables(eng)

    assert {"result_reviewed_by", "result_reviewed_at"} <= {
        column["name"] for column in inspect(eng).get_columns("scheduled_tasks")
    }
    assert {"reviewed_by", "reviewed_at"} <= {
        column["name"] for column in inspect(eng).get_columns("task_runs")
    }
    assert {column.name for column in TaskRun.__table__.columns} == {
        column["name"] for column in inspect(eng).get_columns("task_runs")
    }
    with eng.connect() as conn:
        task = conn.execute(
            text(
                "SELECT verify_needs_review, verify_reason, result_reviewed_by "
                "FROM scheduled_tasks WHERE id=:id"
            ),
            {"id": task_id},
        ).one()
        run = conn.execute(
            text(
                "SELECT verify_needs_review, verify_reason, reviewed_by FROM task_runs WHERE id=:id"
            ),
            {"id": run_id},
        ).one()
    assert task.verify_needs_review == 1 and task.verify_reason == "goal mismatch"
    assert task.result_reviewed_by is None
    assert run.verify_needs_review == 1 and run.verify_reason == "goal mismatch"
    assert run.reviewed_by is None


def test_writer_can_mark_the_latest_flagged_result_reviewed(tmp_path, monkeypatch):
    from anthill.web.db import AuditLog

    client, ids, app_mod = _app(tmp_path, monkeypatch)
    task_id, run_ids = _flagged_task(app_mod, ids, created_by=ids["owner"])
    _auth(client, ids["owner"], ids["org"], "member")

    page = client.get(f"/tasks/{task_id}/result").text
    listing = client.get("/tasks").text
    assert "Mark reviewed" in page and "Mark reviewed" in listing
    assert "needs review" in page and "goal mismatch" in page

    response = _post_review(client, app_mod, task_id, run_ids[0], ids["owner"])
    assert response.status_code == 302

    task = _task_row(app_mod, task_id)
    run = _run_row(app_mod, run_ids[0])
    assert task.verify_needs_review is False  # list warning cleared
    assert task.verify_reason == "goal mismatch"  # verdict text kept
    assert task.verify_confidence == "0.20"
    assert run.verify_needs_review is True  # original verdict stays flagged
    assert run.verify_reason == "goal mismatch"
    assert run.status == "ok"
    assert run.reviewed_by == ids["owner"] and run.reviewed_at is not None

    reviewed = client.get(f"/tasks/{task_id}/result").text
    assert "reviewed" in reviewed and "goal mismatch" in reviewed
    assert "Owner" in reviewed
    assert "Mark reviewed" not in reviewed
    assert "result needs review" not in client.get("/tasks").text
    audit = (
        app_mod._SessionFactory()
        .query(AuditLog)
        .filter(AuditLog.event == "task.result_reviewed")
        .one()
    )
    assert f"id={task_id} run={run_ids[0]}" in audit.detail
    assert audit.user_id == ids["owner"]


def test_older_acknowledgement_cannot_clear_a_newer_warning(tmp_path, monkeypatch):
    client, ids, app_mod = _app(tmp_path, monkeypatch)
    task_id, run_ids = _flagged_task(app_mod, ids, created_by=ids["owner"], runs=2)
    older, newer = run_ids
    _auth(client, ids["owner"], ids["org"], "member")

    response = _post_review(client, app_mod, task_id, older, ids["owner"])
    assert response.status_code == 302

    task = _task_row(app_mod, task_id)
    assert task.verify_needs_review is True
    assert _run_row(app_mod, older).reviewed_by == ids["owner"]
    assert _run_row(app_mod, older).verify_needs_review is True
    assert _run_row(app_mod, newer).reviewed_at is None
    assert _run_row(app_mod, newer).verify_needs_review is True

    page = client.get(f"/tasks/{task_id}/result").text
    assert "reviewed" in page and "needs review" in page
    assert "result needs review" in client.get("/tasks").text

    assert _post_review(client, app_mod, task_id, newer, ids["owner"]).status_code == 302
    assert _task_row(app_mod, task_id).verify_needs_review is False
    assert _run_row(app_mod, newer).verify_needs_review is True
    assert _run_row(app_mod, newer).reviewed_by == ids["owner"]


def test_legacy_flagged_task_without_a_run_can_be_acknowledged(tmp_path, monkeypatch):
    client, ids, app_mod = _app(tmp_path, monkeypatch)
    session = app_mod._SessionFactory()
    task = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["owner"],
        title="Pre-history",
        goal="g",
        status="done",
        last_result="old answer",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
    )
    session.add(task)
    session.commit()
    task_id = task.id
    _auth(client, ids["owner"], ids["org"], "member")

    assert "Mark reviewed" in client.get(f"/tasks/{task_id}/result").text
    assert _post_review(client, app_mod, task_id, 0, ids["owner"]).status_code == 302

    fresh = _task_row(app_mod, task_id)
    assert fresh.verify_needs_review is False
    assert fresh.verify_reason == "goal mismatch"
    assert fresh.verify_confidence == "0.20"
    assert fresh.result_reviewed_by == ids["owner"] and fresh.result_reviewed_at is not None
    assert app_mod._SessionFactory().query(TaskRun).count() == 0
    page = client.get(f"/tasks/{task_id}/result").text
    assert "reviewed" in page and "goal mismatch" in page
    assert "result needs review" not in client.get("/tasks").text


def test_legacy_acknowledgement_does_not_clear_a_run_that_appeared(tmp_path, monkeypatch):
    client, ids, app_mod = _app(tmp_path, monkeypatch)
    task_id, run_ids = _flagged_task(app_mod, ids, created_by=ids["owner"])
    _auth(client, ids["owner"], ids["org"], "member")

    response = _post_review(client, app_mod, task_id, 0, ids["owner"])
    assert response.status_code == 302
    assert _task_row(app_mod, task_id).verify_needs_review is True
    assert _task_row(app_mod, task_id).result_reviewed_at is None
    assert _run_row(app_mod, run_ids[0]).reviewed_at is None


def test_review_is_csrf_protected(tmp_path, monkeypatch):
    client, ids, app_mod = _app(tmp_path, monkeypatch)
    task_id, run_ids = _flagged_task(app_mod, ids, created_by=ids["owner"], runs=2)
    _auth(client, ids["owner"], ids["org"], "member")

    missing = client.post(
        f"/tasks/{task_id}/review-result",
        data={"run_id": str(run_ids[1])},
        follow_redirects=False,
    )
    swapped = client.post(
        f"/tasks/{task_id}/review-result",
        data={
            "run_id": str(run_ids[1]),
            "csrf_token": app_mod._task_review_token(ids["owner"], task_id, run_ids[0]),
        },
        follow_redirects=False,
    )
    forged = client.post(
        f"/tasks/{task_id}/review-result",
        data={"run_id": str(run_ids[1]), "csrf_token": "not-a-token"},
        follow_redirects=False,
    )
    other_user = client.post(
        f"/tasks/{task_id}/review-result",
        data={
            "run_id": str(run_ids[1]),
            "csrf_token": app_mod._task_review_token(ids["viewer"], task_id, run_ids[1]),
        },
        follow_redirects=False,
    )
    get = client.get(f"/tasks/{task_id}/review-result", follow_redirects=False)

    assert missing.status_code == 403
    assert swapped.status_code == 403
    assert forged.status_code == 403
    assert other_user.status_code == 403
    assert get.status_code == 405
    assert _task_row(app_mod, task_id).verify_needs_review is True
    assert _run_row(app_mod, run_ids[1]).reviewed_at is None


def test_review_rejects_unauthorized_and_shared_read_only_viewers(tmp_path, monkeypatch):
    from anthill.web.db import Team, TeamMembership

    client, ids, app_mod = _app(tmp_path, monkeypatch)
    solo_id, solo_runs = _flagged_task(app_mod, ids, created_by=ids["owner"])
    org_id, org_runs = _flagged_task(app_mod, ids, created_by=ids["owner"], plane="org")
    session = app_mod._SessionFactory()
    team = Team(org_id=ids["org"], name="Proj", slug="proj", owner_id=ids["owner"])
    session.add(team)
    session.flush()
    session.add(
        TeamMembership(team_id=team.id, user_id=ids["viewer"], status="active", role="member")
    )
    session.commit()
    team_task = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["owner"],
        title="Team digest",
        goal="g",
        plane="team",
        team_id=team.id,
        status="done",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
    )
    session.add(team_task)
    session.flush()
    team_run = TaskRun(
        org_id=ids["org"],
        task_id=team_task.id,
        status="ok",
        result="team result",
        verify_needs_review=True,
        verify_reason="goal mismatch",
        verify_confidence="0.20",
    )
    session.add(team_run)
    session.commit()
    team_id, team_run_id = team_task.id, team_run.id

    from fastapi.testclient import TestClient

    anonymous = TestClient(app_mod.app)
    assert (
        anonymous.post(
            f"/tasks/{solo_id}/review-result",
            data={"run_id": str(solo_runs[0])},
            follow_redirects=False,
        ).status_code
        == 303
    )

    _auth(client, ids["viewer"], ids["org"], "member")
    assert (
        client.post(
            f"/tasks/{solo_id}/review-result",
            data={
                "run_id": str(solo_runs[0]),
                "csrf_token": app_mod._task_review_token(ids["viewer"], solo_id, solo_runs[0]),
            },
            follow_redirects=False,
        ).status_code
        == 404
    )
    org_page = client.get(f"/tasks/{org_id}/result").text
    assert "needs review" in org_page and "Mark reviewed" not in org_page
    assert "Mark reviewed" not in client.get("/tasks").text
    assert _post_review(client, app_mod, org_id, org_runs[0], ids["viewer"]).status_code == 403
    assert _post_review(client, app_mod, team_id, team_run_id, ids["viewer"]).status_code == 403
    assert _task_row(app_mod, org_id).verify_needs_review is True
    assert _task_row(app_mod, team_id).verify_needs_review is True
    assert _run_row(app_mod, org_runs[0]).reviewed_at is None

    _auth(client, ids["admin"], ids["org"], "admin")
    assert "Mark reviewed" in client.get(f"/tasks/{org_id}/result").text
    assert _post_review(client, app_mod, org_id, org_runs[0], ids["admin"]).status_code == 302
    assert _task_row(app_mod, org_id).verify_needs_review is False
    assert _run_row(app_mod, org_runs[0]).reviewed_by == ids["admin"]
    assert _run_row(app_mod, org_runs[0]).verify_needs_review is True
