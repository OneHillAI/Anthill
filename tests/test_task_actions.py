"""Tasks list actions (#91, #95): each action says what it does, shows only for a viewer who may use it, and
cancelling or reactivating a task is explicit. Model-free."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from bs4 import BeautifulSoup
from test_task_run_history import _app, _auth

from anthill.web import task_occurrences
from anthill.web.db import (
    Organization,
    ScheduledTask,
    TaskOccurrence,
    TaskRun,
    Team,
    TeamMembership,
    User,
)


def _rows(client, token_user=None):
    soup = BeautifulSoup(client.get("/tasks").text, "html.parser")
    out = {}
    for tr in soup.select("table.tasks-list tbody tr"):
        if tr.get("id", "").startswith("q-"):
            continue
        title = tr.find("b").get_text(strip=True)
        controls = []
        for el in tr.select("td[data-label='Actions'] button, td[data-label='Actions'] a"):
            controls.append(el.get("aria-label") or el.get_text(strip=True))
        out[title] = {
            "controls": controls,
            "view_only": "View only" in tr.get_text(),
            "queue_panel": soup.find(id=f"q-{tr.find_all('form')[0]['action'].split('/')[2]}")
            if tr.find_all("form")
            else None,
        }
    return out


_SHORT = {
    "Edit task": "Edit",
    "Add follow-up to": "Add follow-up",
    "Reactivate task": "Reactivate",
}


def _names(controls):
    """The short action names (the task's title is the part after the colon)."""
    out = []
    for c in controls:
        head = c.split(":")[0].strip()
        out.append(_SHORT.get(head, head))
    return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    c = User(org_id=ids["org"], email="c@acme.com", role="member", active=True)
    s.add(c)
    s.flush()
    ids["c"] = c.id
    team = Team(org_id=ids["org"], name="Proj", slug="proj", owner_id=ids["b"])
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=ids["c"], status="active", role="member"))
    now = datetime.now(timezone.utc)

    def task(title, creator, **kw):
        base = {
            "org_id": ids["org"],
            "created_by": creator,
            "title": title,
            "goal": "g",
            "schedule": "daily",
            "status": "pending",
            "plane": "solo",
            "last_result": "done it",
        }
        base.update(kw)
        t = ScheduledTask(**base)
        s.add(t)
        s.flush()
        return t

    tasks = {
        "active": task("T active", ids["b"]),
        "running": task("T running", ids["b"], status="running"),
        "cancelled": task("T cancelled", ids["b"], status="cancelled"),
        "done_once": task("T done once", ids["b"], status="done", schedule="once"),
        "org": task("T org", ids["b"], plane="org"),
        "team": task("T team", ids["b"], plane="team", team_id=team.id),
    }
    s.add(
        TaskRun(
            org_id=ids["org"],
            task_id=tasks["running"].id,
            trigger="manual",
            status="running",
            started_at=now,
        )
    )
    s.commit()
    ids["tasks"] = {k: v.id for k, v in tasks.items()}
    return client, ids, app_mod


# ── the action matrix: task state, for the person who made it ──────────────────────────────────


def test_an_owner_sees_the_actions_that_fit_each_state(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    rows = _rows(client)
    active = _names(rows["T active"]["controls"])
    assert active == [
        "View result",
        "Edit",
        "Run now",
        "Add follow-up",
        "Cancel future runs",
    ]
    running = _names(rows["T running"]["controls"])
    assert "Run now" not in running and "Run again" not in running  # one run at a time
    assert "Cancel future runs" in running and "Add follow-up" in running
    cancelled = _names(rows["T cancelled"]["controls"])
    assert cancelled == [
        "View result",
        "Reactivate",
    ]  # no Run now, no follow-up, no Edit, no cancel
    done_once = _names(rows["T done once"]["controls"])
    assert "Run again" in done_once and "Run now" not in done_once


def test_the_labels_name_the_task_so_repeated_buttons_are_distinguishable(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    controls = _rows(client)["T active"]["controls"]
    assert "Run now: T active" in controls
    assert "Cancel future runs: T active" in controls
    assert "Add follow-up to: T active" in controls
    assert "Edit task: T active" in controls


# ── who may use what: viewer types ────────────────────────────────────────────────────────────


def test_a_peer_member_does_not_see_a_solo_task_at_all(world):
    client, ids, _app_mod = world
    _auth(client, ids["c"], ids["org"], "member")
    rows = _rows(client)
    assert "T active" not in rows and "T cancelled" not in rows


def test_a_read_only_member_of_an_org_task_gets_no_controls_they_cannot_use(world):
    client, ids, _ = world
    _auth(client, ids["c"], ids["org"], "member")
    controls = _names(_rows(client)["T org"]["controls"])
    assert controls == ["View result"]  # no edit, run, follow-up or cancel


def test_an_admin_may_change_an_org_task_someone_else_made(world):
    client, _ids, _app_mod = world  # the default session is the admin
    controls = _names(_rows(client)["T org"]["controls"])
    assert {"Edit", "Run now", "Add follow-up", "Cancel future runs"} <= set(controls)


def test_a_project_member_may_run_a_team_task_but_not_edit_or_cancel_it(world):
    client, ids, _ = world
    _auth(client, ids["c"], ids["org"], "member")
    controls = _names(_rows(client)["T team"]["controls"])
    assert "Run now" in controls and "Add follow-up" in controls
    assert (
        "Edit" not in controls
        and "Cancel future runs" not in controls
        and "Reactivate" not in controls
    )


def test_a_task_with_no_result_and_no_rights_says_view_only(world):
    client, ids, app_mod = world
    s = app_mod._SessionFactory()
    s.query(ScheduledTask).filter(ScheduledTask.id == ids["tasks"]["org"]).update(
        {"last_result": None}
    )
    s.commit()
    _auth(client, ids["c"], ids["org"], "member")
    assert _rows(client)["T org"]["view_only"] is True


# ── cancelling is explicit, and says what it does ────────────────────────────────────────────


def _cancel_message(client, title):
    soup = BeautifulSoup(client.get("/tasks").text, "html.parser")
    button = soup.find("button", attrs={"aria-label": f"Cancel future runs: {title}"})
    return button["data-confirm"], button.get("data-confirm-ok")


def test_the_cancel_confirmation_says_it_stops_future_runs_and_keeps_the_history(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    text, ok = _cancel_message(client, "T active")
    assert "future runs" in text and "history is kept" in text and "nothing is deleted" in text
    assert "not interrupted" not in text  # nothing is running
    assert ok == "Cancel future runs"


def test_the_cancel_confirmation_for_a_running_task_says_the_active_run_is_not_stopped(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    text, _ = _cancel_message(client, "T running")
    assert "not interrupted" in text and "result is not kept" in text


def test_cancelling_a_running_task_does_not_stop_the_run_and_its_result_is_not_published(world):
    """What the confirmation promises, checked on the scheduler's own bookkeeping: cancel marks the run, the
    claim is no longer current, so finishing is refused and nothing is published."""
    _, ids, app_mod = world
    s = app_mod._SessionFactory()
    s.query(TaskRun).delete()  # start from a task with a real claimed run, not the seeded row
    s.query(TaskOccurrence).filter(TaskOccurrence.task_id == ids["tasks"]["active"]).delete()
    s.commit()
    task = s.get(ScheduledTask, ids["tasks"]["active"])
    now = datetime.now(timezone.utc)
    task_occurrences.add(s, task, kind="manual", due_at=now)
    s.commit()
    claim = task_occurrences.claim(s, task.id, now)
    assert claim is not None and claim.run.status == "running"
    run_id, occurrence_id = claim.run.id, claim.occurrence.id
    task_occurrences.cancel(s, task)
    s.commit()
    run = s.get(TaskRun, run_id)
    assert run.status == "running" and run.cancel_requested is True  # still executing, only marked
    assert (
        task_occurrences.finalize(s, occurrence_id, run_id, outcome="done") is False
    )  # not published


# ── Run now and Add follow-up no longer bring a cancelled task back ─────────────────────────


def _manual_count(app_mod, task_id):
    s = app_mod._SessionFactory()
    return (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task_id, TaskOccurrence.kind == "manual")
        .count()
    )


def test_run_now_on_a_cancelled_task_is_refused_and_does_not_reactivate_it(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["cancelled"]
    r = client.post(f"/tasks/{tid}/run-now", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/tasks?notice=cancelled"
    s = app_mod._SessionFactory()
    assert s.get(ScheduledTask, tid).status == "cancelled"
    assert _manual_count(app_mod, tid) == 0
    page = client.get(r.headers["location"]).text
    assert "That task is cancelled. Reactivate it first" in page


def test_add_follow_up_on_a_cancelled_task_is_refused_and_does_not_reactivate_it(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["cancelled"]
    r = client.post(f"/tasks/{tid}/queue", data={"instruction": "more"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/tasks?notice=cancelled"
    assert app_mod._SessionFactory().get(ScheduledTask, tid).status == "cancelled"


def test_reactivate_turns_the_schedule_back_on_without_running_anything(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["cancelled"]
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/tasks"
    assert app_mod._SessionFactory().get(ScheduledTask, tid).status == "pending"
    assert _manual_count(app_mod, tid) == 0  # nothing was run
    controls = _names(_rows(client)["T cancelled"]["controls"])
    assert "Reactivate" not in controls and "Run now" in controls  # it is an ordinary task again


def test_reactivate_is_for_writers_only(world):
    client, ids, app_mod = world
    _auth(client, ids["c"], ids["org"], "member")
    tid = ids["tasks"]["cancelled"]
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=not_allowed"
    assert app_mod._SessionFactory().get(ScheduledTask, tid).status == "cancelled"


def test_reactivating_a_task_that_is_not_cancelled_says_so(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    r = client.post(f"/tasks/{ids['tasks']['active']}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=not_cancelled"


@pytest.mark.parametrize("action", ["run-now", "cancel", "review-cadence"])
def test_a_refused_action_shows_a_message_instead_of_failing_silently(world, action):
    client, ids, _ = world
    _auth(client, ids["c"], ids["org"], "member")
    r = client.post(f"/tasks/{ids['tasks']['org']}/{action}", follow_redirects=False)
    if action == "run-now":  # a read-only member may not run an org task
        assert r.headers["location"] == "/tasks?notice=not_allowed_run"
        assert "run that task" in client.get(r.headers["location"]).text
    else:
        assert r.headers["location"] == "/tasks?notice=not_allowed"
        assert "change that task" in client.get(r.headers["location"]).text


def test_a_notice_is_a_fixed_message_never_the_text_in_the_address(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    page = client.get("/tasks?notice=<script>alert(1)</script>").text
    assert "alert(1)" not in page
    assert 'class="alert alert-warn mb-2"' not in page  # an unknown code shows no notice at all


# ── Reactivate runs nothing now (round 5) ───────────────────────────────────────────────────────


def _cancelled_with(app_mod, task_id, *, cadence_due=None, follow_up=None):
    """The active task with a cadence occurrence at ``cadence_due`` (and optionally a follow-up), cancelled."""
    s = app_mod._SessionFactory()
    task = s.get(ScheduledTask, task_id)
    s.query(TaskOccurrence).filter(TaskOccurrence.task_id == task_id).delete()
    task.next_run_at = None
    task_occurrences.create_initial(s, task, cadence_due)
    if follow_up:
        task_occurrences.add(
            s, task, kind="queued", due_at=datetime.now(timezone.utc), inputs=[follow_up]
        )
    task_occurrences.cancel(s, task)
    s.commit()
    s.close()


def test_reactivating_a_task_whose_run_fell_due_while_cancelled_runs_nothing(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["active"]
    past = datetime.now(timezone.utc) - timedelta(days=2)
    _cancelled_with(app_mod, tid, cadence_due=past)
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.status_code == 302
    s = app_mod._SessionFactory()
    now = datetime.now(timezone.utc)
    assert task_occurrences.claim(s, tid, now) is None  # nothing is claimable now
    task = s.get(ScheduledTask, tid)
    assert task.status == "pending" and task.next_run_at is not None
    assert task_occurrences._aware(task.next_run_at) > now  # the next future slot
    pending = (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == tid, TaskOccurrence.status == "pending")
        .all()
    )
    assert len(pending) == 1 and task_occurrences._aware(pending[0].due_at) > now
    assert _manual_count(app_mod, tid) == 0


def test_reactivating_keeps_a_future_scheduled_run_as_it_was(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["active"]
    future = datetime.now(timezone.utc) + timedelta(days=3)
    _cancelled_with(app_mod, tid, cadence_due=future)
    client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    s = app_mod._SessionFactory()
    task = s.get(ScheduledTask, tid)
    assert task_occurrences._aware(task.next_run_at) == future.replace(
        microsecond=future.microsecond
    )


def test_reactivating_merges_a_due_follow_up_into_the_next_scheduled_run(world, monkeypatch):
    """The follow-up rides with the scheduled run: one occurrence at the next slot carries it, and when the
    slot arrives the task runs once, with the follow-up in that run."""
    from anthill.web import scheduler

    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["active"]
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _cancelled_with(app_mod, tid, cadence_due=past, follow_up="check the totals")
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=reactivated_merged"
    page = client.get(r.headers["location"]).text
    assert "Nothing was run now" in page and "will run with its next scheduled run" in page
    assert "or for Run now" not in page
    s = app_mod._SessionFactory()
    now = datetime.now(timezone.utc)
    assert task_occurrences.claim(s, tid, now) is None  # nothing claimable now
    pending = (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == tid, TaskOccurrence.status == "pending")
        .all()
    )
    assert len(pending) == 1 and pending[0].kind == "scheduled"  # one occurrence, not two
    assert json.loads(pending[0].inputs) == ["check the totals"]
    assert task_occurrences._aware(pending[0].due_at) > now
    slot_id = pending[0].id

    # The slot arrives: a single run carries the follow-up.
    s.query(TaskOccurrence).filter(TaskOccurrence.id == slot_id).update(
        {TaskOccurrence.due_at: now - timedelta(minutes=1)}
    )
    s.commit()
    goals = []

    def run(task, _db):
        goals.append(scheduler._effective_goal(task))
        return "done"

    monkeypatch.setattr(scheduler, "_run_task", run)
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
    scheduler._tick(app_mod._engine)
    scheduler._tick(app_mod._engine)
    assert len(goals) == 1 and "check the totals" in goals[0]
    assert s.query(TaskRun).filter(TaskRun.task_id == tid).count() == 1


def test_a_follow_up_with_no_scheduled_run_to_join_is_held_and_runs_as_its_own_run(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["done_once"]
    s = app_mod._SessionFactory()
    s.get(ScheduledTask, tid).status = "pending"
    s.commit()
    s.close()
    _cancelled_with(
        app_mod,
        tid,
        cadence_due=datetime.now(timezone.utc) - timedelta(hours=3),
        follow_up="check the totals",
    )
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=reactivated_held"
    page = client.get(r.headers["location"]).text
    assert "no next scheduled run" in page and "held until you use Run now or Add follow-up" in page
    s = app_mod._SessionFactory()
    held = (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == tid, TaskOccurrence.status == "paused")
        .all()
    )
    assert len(held) == 1 and json.loads(held[0].inputs) == ["check the totals"]
    assert task_occurrences.claim(s, tid, datetime.now(timezone.utc)) is None
    s.close()
    client.post(f"/tasks/{tid}/run-now", follow_redirects=False)  # an explicit action resumes it
    s = app_mod._SessionFactory()
    pending = (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == tid, TaskOccurrence.status == "pending")
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(o.kind, json.loads(o.inputs)) for o in pending] == [
        ("queued", ["check the totals"]),
        ("manual", []),
    ]  # the held follow-up is its own run, beside the manual one


def test_a_one_time_task_whose_date_passed_stays_pending_with_no_next_run(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["done_once"]
    s = app_mod._SessionFactory()
    s.get(ScheduledTask, tid).status = "pending"
    s.commit()
    s.close()
    _cancelled_with(app_mod, tid, cadence_due=datetime.now(timezone.utc) - timedelta(hours=3))
    client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    s = app_mod._SessionFactory()
    task = s.get(ScheduledTask, tid)
    assert task.status == "pending" and task.next_run_at is None
    assert task_occurrences.claim(s, tid, datetime.now(timezone.utc)) is None


def test_reactivate_is_refused_while_a_cancelled_run_is_still_running(world):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["cancelled"]
    s = app_mod._SessionFactory()
    s.add(
        TaskRun(
            org_id=ids["org"],
            task_id=tid,
            trigger="scheduled",
            status="running",
            started_at=datetime.now(timezone.utc),
            cancel_requested=True,
        )
    )
    s.commit()
    s.close()
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=still_cancelling"
    assert "still finishing" in client.get(r.headers["location"]).text
    assert app_mod._SessionFactory().get(ScheduledTask, tid).status == "cancelled"


# ── the ledger refuses a cancelled task itself ───────────────────────────────────────────────────


def test_the_ledger_refuses_run_now_and_a_follow_up_when_the_task_was_cancelled_elsewhere(world):
    _, ids, app_mod = world
    tid = ids["tasks"]["active"]
    mine = app_mod._SessionFactory()
    stale = mine.get(ScheduledTask, tid)  # this session still believes it is pending
    assert stale.status == "pending"
    other = app_mod._SessionFactory()
    other.query(ScheduledTask).filter(ScheduledTask.id == tid).update({"status": "cancelled"})
    other.commit()
    other.close()
    now = datetime.now(timezone.utc)
    with pytest.raises(task_occurrences.TaskCancelled):
        task_occurrences.run_now(mine, stale, now)
    mine.rollback()
    with pytest.raises(task_occurrences.TaskCancelled):
        task_occurrences.queue_input(mine, stale, "more", now)
    mine.rollback()
    assert app_mod._SessionFactory().get(ScheduledTask, tid).status == "cancelled"
    assert _manual_count(app_mod, tid) == 0


# ── refusal codes ────────────────────────────────────────────────────────────────────────────────


def test_a_task_that_stops_being_available_mid_request_says_lost_access(world, monkeypatch):
    client, ids, app_mod = world
    _auth(client, ids["b"], ids["org"], "member")
    tid = ids["tasks"]["active"]
    real = app_mod._task_for_operate
    calls = {"n": 0}

    def flaky(db, task_id, org, user):
        calls["n"] += 1
        return real(db, task_id, org, user) if calls["n"] == 1 else None

    monkeypatch.setattr(app_mod, "_task_for_operate", flaky)
    r = client.post(f"/tasks/{tid}/run-now", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=lost_access"
    calls["n"] = 0
    r = client.post(f"/tasks/{tid}/queue", data={"instruction": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=lost_access"
    assert "no longer have access" in client.get("/tasks?notice=lost_access").text


def test_add_follow_up_without_rights_redirects_with_a_notice_instead_of_a_404(world):
    client, ids, _ = world
    _auth(client, ids["c"], ids["org"], "member")
    r = client.post(
        f"/tasks/{ids['tasks']['org']}/queue", data={"instruction": "x"}, follow_redirects=False
    )
    assert r.status_code == 302 and r.headers["location"] == "/tasks?notice=not_allowed_run"


# ── names, titles, dialog, notice role ───────────────────────────────────────────────────────────


def test_accessible_names_start_with_the_visible_text(world):
    client, ids, app_mod = world
    s = app_mod._SessionFactory()
    flagged = s.get(ScheduledTask, ids["tasks"]["active"])
    flagged.cadence_needs_review = True
    flagged.cadence_review_reason = "check"
    s.commit()
    s.close()
    _auth(client, ids["b"], ids["org"], "member")
    soup = BeautifulSoup(client.get("/tasks").text, "html.parser")
    for button in soup.select("td[data-label='Actions'] button, td[data-label='Actions'] a"):
        label = button.get("aria-label")
        visible = button.get_text(" ", strip=True)
        if label and visible:
            base = visible.split(" (")[0]
            assert label.startswith(base), (label, visible)
    names = [b.get("aria-label") for b in soup.select("td[data-label='Actions'] button")]
    assert "Mark schedule reviewed: T active" in names


def test_the_run_titles_are_true(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    page = client.get("/tasks").text
    assert "Runs it once now. A date or schedule already chosen still applies." in page
    assert "its status goes pending, then running, then done" in page
    assert "instead of waiting for its time" not in page
    assert "It stays complete" not in page


def test_the_cancel_dialog_says_what_stays_and_offers_keep_task(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    soup = BeautifulSoup(client.get("/tasks").text, "html.parser")
    button = soup.find("button", attrs={"aria-label": "Cancel future runs: T active"})
    assert button["data-confirm-cancel"] == "Keep task"
    assert "Anything it already did stays" in button["data-confirm"]


def test_a_refusal_notice_is_an_alert(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    soup = BeautifulSoup(client.get("/tasks?notice=cancelled").text, "html.parser")
    box = soup.find(attrs={"role": "alert"})
    assert box is not None and "That task is cancelled" in box.get_text()


# ── the result page ──────────────────────────────────────────────────────────────────────────────


def _run_again(client, tid):
    soup = BeautifulSoup(client.get(f"/tasks/{tid}/result").text, "html.parser")
    return soup.find("button", attrs={"aria-label": lambda v: v and v.startswith("Run again")})


def test_run_again_on_the_result_page_is_shown_only_when_it_will_work(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    shown = _run_again(client, ids["tasks"]["active"])
    assert shown is not None and shown["aria-label"] == "Run again: T active"
    assert "pending, then running, then done" in shown["title"]
    assert _run_again(client, ids["tasks"]["cancelled"]) is None
    assert _run_again(client, ids["tasks"]["running"]) is None
    assert _run_again(client, ids["tasks"]["done_once"]) is not None


def test_run_again_is_hidden_from_someone_who_may_not_operate_the_task(world):
    client, ids, _ = world
    _auth(
        client, ids["c"], ids["org"], "member"
    )  # a member who can see the org task but not run it
    assert _run_again(client, ids["tasks"]["org"]) is None
    # a member of the project may run its task
    assert _run_again(client, ids["tasks"]["team"]) is not None


def test_a_run_cancelled_while_it_works_is_recorded_with_a_plain_text(tmp_path, monkeypatch):
    from test_task_run_history import _seed_due_task

    from anthill.web import scheduler

    eng, session_factory, tid = _seed_due_task(tmp_path, monkeypatch)

    def cancelled_midway(task, db):
        other = session_factory()
        task_occurrences.cancel(other, other.get(ScheduledTask, tid))
        other.commit()
        other.close()
        return "a late result"

    monkeypatch.setattr(scheduler, "_run_task", cancelled_midway)
    scheduler._tick(eng)
    s = session_factory()
    run = s.query(TaskRun).filter(TaskRun.task_id == tid).one()
    assert run.status == "error"
    assert run.error == "Cancelled while it was running. Its result was not kept."
    assert s.get(ScheduledTask, tid).last_result != "a late result"


# ── who may reactivate, and the titles ───────────────────────────────────────────────────────────


def test_reactivate_is_refused_to_a_project_member_a_read_only_member_and_another_organisation(
    world,
):
    client, ids, app_mod = world
    tid = ids["tasks"]["cancelled"]
    s = app_mod._SessionFactory()
    s.get(ScheduledTask, ids["tasks"]["team"]).status = "cancelled"
    s.commit()
    # a member of the project may run its tasks but not change them
    _auth(client, ids["c"], ids["org"], "member")
    r = client.post(f"/tasks/{ids['tasks']['team']}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=not_allowed"
    assert s.get(ScheduledTask, ids["tasks"]["team"]).status == "cancelled"
    # a read-only member of an organisation task
    s.get(ScheduledTask, ids["tasks"]["org"]).status = "cancelled"
    s.commit()
    r = client.post(f"/tasks/{ids['tasks']['org']}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=not_allowed"
    # another organisation's admin cannot reach this organisation's task at all
    other = Organization(name="Other", slug="other")
    s.add(other)
    s.flush()
    stranger = User(org_id=other.id, email="x@other.com", role="admin", active=True)
    s.add(stranger)
    s.commit()
    _auth(client, stranger.id, other.id, "admin")
    r = client.post(f"/tasks/{tid}/reactivate", follow_redirects=False)
    assert r.headers["location"] == "/tasks?notice=not_allowed"
    s.expire_all()
    assert s.get(ScheduledTask, tid).status == "cancelled"
    assert s.get(ScheduledTask, ids["tasks"]["org"]).status == "cancelled"


def test_the_reactivate_title_matches_what_it_does(world):
    client, ids, _ = world
    _auth(client, ids["b"], ids["org"], "member")
    soup = BeautifulSoup(client.get("/tasks").text, "html.parser")
    button = soup.find("button", attrs={"aria-label": "Reactivate task: T cancelled"})
    title = button["title"]
    assert "does not run anything now" in title
    assert "follow-ups you queued before run with its next scheduled run" in title
    assert "dropped" not in title


def test_the_escalation_help_points_to_where_the_settings_are(world, monkeypatch):
    client, ids, app_mod = world
    monkeypatch.setattr(app_mod, "_escalation_available", lambda db, org: True)
    _auth(client, ids["b"], ids["org"], "member")
    page = client.get("/tasks").text
    assert "Task defaults above" not in page
    assert "Task settings for administrators, below the list" in page
    assert "connected backend" not in page and "connected cloud model" in page


def test_the_result_page_names_start_with_the_visible_text(world):
    client, ids, app_mod = world
    s = app_mod._SessionFactory()
    s.add(
        TaskRun(
            org_id=ids["org"],
            task_id=ids["tasks"]["active"],
            trigger="scheduled",
            status="done",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            verify_needs_review=True,
        )
    )
    s.commit()
    _auth(client, ids["b"], ids["org"], "member")
    soup = BeautifulSoup(client.get(f"/tasks/{ids['tasks']['active']}/result").text, "html.parser")
    labels = [
        b.get("aria-label")
        for b in soup.find_all("button")
        if b.get_text(strip=True) == "Mark reviewed"
    ]
    assert labels and all(label.startswith("Mark reviewed: ") for label in labels)


def test_a_lost_claim_that_was_not_a_cancel_keeps_its_own_error_text(tmp_path, monkeypatch):
    from test_task_run_history import _seed_due_task

    from anthill.web import scheduler

    eng, session_factory, tid = _seed_due_task(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "late result")
    real_finalize = task_occurrences.finalize
    calls = []

    def lose_the_first_claim(*args, **kwargs):
        calls.append(1)
        return False if len(calls) == 1 else real_finalize(*args, **kwargs)

    monkeypatch.setattr(task_occurrences, "finalize", lose_the_first_claim)
    scheduler._tick(eng)
    s = session_factory()
    first = s.query(TaskRun).filter(TaskRun.task_id == tid).order_by(TaskRun.id).first()
    assert first.status == "error" and first.cancel_requested is False
    assert first.error == "stale attempt lost occurrence ownership"
