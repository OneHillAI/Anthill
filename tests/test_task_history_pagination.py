"""#96: the task result page pages its run history and states the real counts. Model-free."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from bs4 import BeautifulSoup
from test_task_run_history import _app

from anthill.web.app import TASK_HISTORY_PAGE_SIZE

BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)  # in the past, as real finished runs are


def _task_with_runs(tmp_path, monkeypatch, n, *, same_instant=False, run_count=None):
    """A task with n recorded runs whose results are run-001 (oldest) .. run-nnn (newest)."""
    import anthill.web.app as app_mod
    from anthill.web.db import ScheduledTask, TaskRun

    client, ids = _app(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    t = ScheduledTask(
        org_id=ids["org"],
        created_by=ids["a"],
        title="Hist",
        goal="g",
        schedule="daily",
        status="pending",
        run_count=n if run_count is None else run_count,
    )
    s.add(t)
    s.flush()
    for i in range(1, n + 1):
        s.add(
            TaskRun(
                org_id=ids["org"],
                task_id=t.id,
                trigger="scheduled",
                status="ok",
                result=f"run-{i:03d}",
                finished_at=BASE if same_instant else BASE + timedelta(minutes=i),
            )
        )
    s.commit()
    return client, t.id


def _page(client, task_id, page=None):
    url = f"/tasks/{task_id}/result" + ("" if page is None else f"?page={page}")
    r = client.get(url)
    assert r.status_code == 200
    soup = BeautifulSoup(r.text, "html.parser")
    runs = re.findall(r"run-\d{3}", " ".join(d.get_text() for d in soup.find_all("details")))
    count = soup.find(id="history-count")
    pager = soup.find(id="history-pager")
    return {
        "runs": runs,
        "count": " ".join(count.get_text().split()) if count else "",
        "pager": pager,
        "pager_text": " ".join(pager.get_text().split()) if pager else "",
        "newer": bool(pager and "Newer runs" in pager.get_text()),
        "older": bool(pager and "Older runs" in pager.get_text()),
        "open_first": bool(soup.find("details", attrs={"open": True})),
    }


def test_page_size_is_25():
    assert TASK_HISTORY_PAGE_SIZE == 25


def test_empty_history(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 0)
    p = _page(client, tid)
    assert p["runs"] == [] and p["pager"] is None
    assert "0 runs" in p["count"]
    assert "No runs recorded yet" in client.get(f"/tasks/{tid}/result").text


def test_single_run_is_singular_with_no_pager(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 1)
    p = _page(client, tid)
    assert p["runs"] == ["run-001"] and p["pager"] is None
    assert p["count"].endswith("1 run")


def test_exactly_one_full_page_has_no_pager(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 25)
    p = _page(client, tid)
    assert len(p["runs"]) == 25 and p["pager"] is None
    assert p["count"].endswith("25 runs") and "showing" not in p["count"]
    assert p["runs"][0] == "run-025" and p["runs"][-1] == "run-001"


def test_26_runs_split_25_and_1_with_honest_counts(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 26)
    first = _page(client, tid)
    assert len(first["runs"]) == 25
    assert first["runs"][0] == "run-026" and first["runs"][-1] == "run-002"  # newest first
    assert "showing 1 to 25 of 26 runs" in first["count"]
    assert first["older"] and not first["newer"]
    assert "Page 1 of 2" in first["pager_text"]
    assert first["open_first"]  # the newest run is expanded on the first page

    second = _page(client, tid, 2)
    assert second["runs"] == ["run-001"]
    assert "showing 26 to 26 of 26 runs" in second["count"]
    assert second["newer"] and not second["older"]
    assert "Page 2 of 2" in second["pager_text"]
    assert not second["open_first"]  # nothing is auto-expanded on older pages


def test_pages_cover_every_run_once_in_order(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 60)
    seen = []
    for page in (1, 2, 3):
        seen += _page(client, tid, page)["runs"]
    assert seen == [
        f"run-{i:03d}" for i in range(60, 0, -1)
    ]  # no duplicates, no gaps, newest first
    assert len(_page(client, tid, 3)["runs"]) == 10


def test_runs_finishing_at_the_same_instant_are_never_repeated_or_skipped(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 60, same_instant=True)
    seen = []
    for page in (1, 2, 3):
        seen += _page(client, tid, page)["runs"]
    assert sorted(seen) == [f"run-{i:03d}" for i in range(1, 61)]
    assert len(set(seen)) == 60
    assert seen == [f"run-{i:03d}" for i in range(60, 0, -1)]  # ties fall back to newest id first


@pytest.mark.parametrize("page", [0, -5, 999, "abc"])
def test_out_of_range_page_is_bounded(tmp_path, monkeypatch, page):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 30)
    r = client.get(f"/tasks/{tid}/result?page={page}")
    if page == "abc":
        assert r.status_code == 422  # not an integer: rejected, never an unbounded query
        return
    assert r.status_code == 200
    p = _page(client, tid, page)
    assert 1 <= len(p["runs"]) <= 25
    expected = "run-030" if page in (0, -5) else "run-005"  # 0/-5 -> page 1, 999 -> last page
    assert p["runs"][0] == expected


def test_total_counts_recorded_runs_not_the_run_count_column(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 26, run_count=3)
    assert "of 26 runs" in _page(client, tid)["count"]
    assert "Runs: 26" in " ".join(
        BeautifulSoup(client.get(f"/tasks/{tid}/result").text, "html.parser").get_text().split()
    )


def _running_run_like_the_app(tid, org_id):
    """A running TaskRun built the way task_occurrences.claim() builds it: status running, a start time and
    finished_at=None, which the column default turns into the claim time."""
    from anthill.web.db import TaskRun

    return TaskRun(
        org_id=org_id,
        task_id=tid,
        trigger="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
        finished_at=None,
    )


def _first_history_entry(client, tid, page_no):
    soup = BeautifulSoup(client.get(f"/tasks/{tid}/result?page={page_no}").text, "html.parser")
    card = soup.find(id="history-count").find_parent(class_="card")
    return card, card.find("details")


def test_a_running_run_built_like_the_app_does_is_first_and_on_page_one(tmp_path, monkeypatch):
    import anthill.web.app as app_mod

    client, tid = _task_with_runs(tmp_path, monkeypatch, 30)
    s = app_mod._SessionFactory()
    s.add(_running_run_like_the_app(tid, 1))
    s.commit()
    _, entry = _first_history_entry(client, tid, 1)
    assert "running" in entry.get_text()  # leads page 1, where the live banner and auto-refresh are
    assert entry.has_attr("open")
    assert _page(client, tid)["runs"][0] == "run-030"  # the newest finished run follows it
    assert "of 31 runs" in _page(client, tid)["count"]
    card2, _ = _first_history_entry(client, tid, 2)
    assert "running" not in card2.get_text()


def test_a_stored_empty_finish_time_is_ordered_first_as_a_guard(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.db import TaskRun

    client, tid = _task_with_runs(tmp_path, monkeypatch, 30)
    s = app_mod._SessionFactory()
    run = _running_run_like_the_app(tid, 1)
    s.add(run)
    s.commit()
    s.query(TaskRun).filter(TaskRun.id == run.id).update(
        {"finished_at": None}
    )  # a state the app never writes
    s.commit()
    _, entry = _first_history_entry(client, tid, 1)
    assert "running" in entry.get_text() and entry.has_attr("open")


def test_summary_and_heading_show_the_same_run_count(tmp_path, monkeypatch):
    client, tid = _task_with_runs(tmp_path, monkeypatch, 26, run_count=3)
    text = " ".join(
        BeautifulSoup(client.get(f"/tasks/{tid}/result").text, "html.parser").get_text().split()
    )
    assert "Runs: 26" in text and "of 26 runs" in text
    assert "Runs: 3" not in text
