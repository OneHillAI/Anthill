"""The page-vector index is built BEFORE the first question (docs/specs/wiki-page-vector-index.md).

At app start, and as each page is saved, the pages are embedded in the background, so the first question
finds them ready. A wiki with no pages costs nothing. Model-free: a fake embedder counts every call.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from anthill.cache import embedder as emb
from anthill.wiki import ask as ask_mod
from anthill.wiki import page_index
from anthill.wiki.workspace import Workspace


class Fake:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> np.ndarray:
        self.calls.append(text)
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(16).astype(np.float32)
        return v / np.linalg.norm(v)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(emb, "embed", f.embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    return f


def _ws(tmp_path: Path, n: int, name: str = "w") -> Workspace:
    ws = Workspace(tmp_path / name)
    ws.init()
    for i in range(n):
        (ws.wiki / f"page-{i}.md").write_text(f"# Page {i}\n\nBody of page {i}. " + "x" * 50)
    return ws


def test_empty_or_missing_wiki_costs_nothing(tmp_path, fake):
    assert page_index.warm_workspace(tmp_path / "does-not-exist") == 0
    empty = Workspace(tmp_path / "empty")
    empty.init()
    assert page_index.warm_workspace(empty.root) == 0
    assert fake.calls == []
    assert not (
        empty.root / ".cache" / "page_vectors.sqlite3"
    ).exists()  # nothing built, nothing created


def test_warm_embeds_each_page_once_and_the_first_question_embeds_none(tmp_path, fake):
    ws = _ws(tmp_path, 6)
    assert page_index.warm_workspace(ws.root) == 6
    assert len(fake.calls) == 6
    assert page_index.warm_workspace(ws.root) == 0  # a second pass finds everything ready
    fake.calls.clear()
    q = fake.embed("a question")
    fake.calls.clear()
    picked = ask_mod._rank_by_embedding(
        ws.pages(), "a question", 3, q, roots=[ws.root], whole_wiki=True
    )
    assert fake.calls == []  # the very first question embeds no page: the index was built before it
    assert len(picked) <= 3


def test_warm_matches_the_text_ranking_looks_up(tmp_path, fake):
    ws = _ws(tmp_path, 3)
    page_index.warm_workspace(ws.root)
    fake.calls.clear()
    # ranking uses the same text and the same keys, so every page is a hit (no embed call at all)
    ask_mod._rank_by_embedding(
        ws.pages(), "q", 3, fake.embed("q"), roots=[ws.root], whole_wiki=True
    )
    assert [c for c in fake.calls if c != "q"] == []


def test_edit_and_delete_are_picked_up_on_the_next_pass(tmp_path, fake):
    ws = _ws(tmp_path, 4)
    page_index.warm_workspace(ws.root)
    fake.calls.clear()
    (ws.wiki / "page-1.md").write_text("# Page 1\n\nChanged body")
    (ws.wiki / "page-3.md").unlink()
    assert page_index.warm_workspace(ws.root) == 1  # only the edited page is embedded again
    assert fake.calls == ["# Page 1\n\nChanged body"]
    import sqlite3

    con = sqlite3.connect(str(ws.root / ".cache" / "page_vectors.sqlite3"))
    paths = {r[0] for r in con.execute("SELECT path FROM page_vectors_v1")}
    con.close()
    assert paths == {
        "wiki/page-0.md",
        "wiki/page-1.md",
        "wiki/page-2.md",
    }  # the deleted page's row is gone


def test_embedder_errors_never_reach_the_caller(tmp_path, monkeypatch):
    ws = _ws(tmp_path, 3)
    monkeypatch.setattr(emb, "available", lambda: True)

    def boom(text):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(emb, "embed", boom)
    assert page_index.warm_workspace(ws.root) == 0  # swallowed, nothing stored
    assert page_index.warm_page(ws.root, ws.wiki / "page-0.md") is False


def test_warm_all_needs_the_embedding_model_and_the_switch(tmp_path, monkeypatch, fake):
    ws = _ws(tmp_path, 3)
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(ws.root))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "none"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "none-org"))
    monkeypatch.setattr(page_index, "ENABLED", False)
    assert page_index.warm_all(delay=0, sleep=lambda s: None) == 0  # switched off
    monkeypatch.setattr(page_index, "ENABLED", True)
    monkeypatch.setattr(emb, "available", lambda: False)
    assert (
        page_index.warm_all(delay=0, sleep=lambda s: None, waits=(1, 2)) == 0
    )  # never ready: gives up, no error
    assert fake.calls == []
    monkeypatch.setattr(emb, "available", lambda: True)
    assert page_index.warm_all(delay=0, sleep=lambda s: None) == 3


def test_workspace_roots_finds_org_personal_user_and_team_wikis(tmp_path, monkeypatch):
    org = _ws(tmp_path, 1, "org")
    legacy = _ws(tmp_path, 1, "legacy")
    wikis = tmp_path / "wikis"
    user = Workspace(wikis / "user-7")
    user.init()
    team = Workspace(wikis / "team-2")
    team.init()
    (wikis / "stray-dir").mkdir()
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(org.root))
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(legacy.root))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(wikis))
    roots = {r.resolve() for r in page_index.workspace_roots()}
    assert roots == {
        org.root.resolve(),
        legacy.root.resolve(),
        user.root.resolve(),
        team.root.resolve(),
    }


def test_saving_a_page_queues_it_when_enabled_and_not_when_disabled(tmp_path, monkeypatch):
    seen: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        page_index,
        "_pending",
        type("Q", (), {"put_nowait": lambda self, item: seen.append(item)})(),
    )
    monkeypatch.setattr(page_index, "_ensure_worker", lambda: None)
    ws = Workspace(tmp_path / "w")
    ws.init()
    monkeypatch.setattr(page_index, "ENABLED", False)
    ws.write_page("Off", "# Off\n\nbody")
    assert seen == []
    monkeypatch.setattr(page_index, "ENABLED", True)
    saved = ws.write_page("Saved", "# Saved\n\nbody")
    assert seen == [(ws.root, saved)]


def test_a_saved_page_is_embedded_by_the_worker_step(tmp_path, fake):
    ws = _ws(tmp_path, 0)
    path = ws.write_page("Fresh", "# Fresh\n\nSome new knowledge")
    assert page_index.warm_page(ws.root, path) is True
    assert len(fake.calls) == 1
    fake.calls.clear()
    assert page_index.warm_page(ws.root, path) is True  # already there: no second embed
    assert fake.calls == []


def test_the_suite_keeps_background_warming_off_by_default():
    assert page_index.ENABLED is False


def test_warm_all_waits_for_the_model_to_come_up_and_then_stops_trying(tmp_path, monkeypatch, fake):
    ws = _ws(tmp_path, 2)
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(ws.root))
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "none"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "none-org"))
    monkeypatch.setattr(page_index, "ENABLED", True)
    sleeps: list[float] = []
    answers = iter([False, False, True])  # the model is still downloading, then it is ready
    monkeypatch.setattr(emb, "available", lambda: next(answers))
    assert page_index.warm_all(delay=0, sleep=sleeps.append, waits=(1, 2, 3)) == 2
    assert sleeps == [0, 0, 1, 2]  # the start-up delay, then a try, then two waits
    sleeps.clear()
    monkeypatch.setattr(emb, "available", lambda: False)
    assert page_index.warm_all(delay=0, sleep=sleeps.append, waits=(1, 2, 3)) == 0
    assert sleeps == [0, 0, 1, 2, 3]  # bounded: it stops after the last wait


def test_a_page_saved_while_the_model_downloads_is_not_lost(tmp_path, monkeypatch, fake):
    ws = _ws(tmp_path, 0)
    path = ws.write_page("Early", "# Early\n\nSaved before the model was ready")
    sleeps: list[float] = []
    answers = iter([False, True])
    monkeypatch.setattr(emb, "available", lambda: next(answers, True))
    assert (
        page_index.warm_page_with_retry(ws.root, path, sleep=sleeps.append, waits=(5, 10)) is True
    )
    assert sleeps == [0, 5]
    assert len(fake.calls) == 1
    monkeypatch.setattr(emb, "available", lambda: False)
    assert page_index.warm_page_with_retry(ws.root, path, sleep=lambda s: None, waits=(1,)) is False


def test_one_unreadable_page_does_not_stop_the_rest(tmp_path, fake):
    ws = _ws(tmp_path, 3)
    (ws.wiki / "bad.md").write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
    assert (
        page_index.warm_workspace(ws.root) == 3
    )  # the three good pages are embedded, the bad one skipped


def test_progress_is_saved_in_batches_not_only_at_the_end(tmp_path, monkeypatch):
    import sqlite3

    from anthill.cache import page_vectors

    monkeypatch.setattr(page_vectors, "SAVE_BATCH", 4)
    ws = _ws(tmp_path, 10)
    db = ws.root / ".cache" / "page_vectors.sqlite3"
    seen_rows: list[int] = []

    def embed(text: str) -> np.ndarray:
        n = 0
        if db.exists():
            con = sqlite3.connect(str(db))
            n = con.execute("SELECT count(*) FROM page_vectors_v1").fetchone()[0]
            con.close()
        seen_rows.append(n)
        return np.ones(4, dtype=np.float32)

    monkeypatch.setattr(emb, "embed", embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    assert page_index.warm_workspace(ws.root) == 10
    assert (
        seen_rows[3] == 0 and seen_rows[4] == 4 and seen_rows[8] == 8
    )  # rows land every 4 pages, mid-run


def test_the_worker_loop_embeds_what_it_is_given(tmp_path, monkeypatch):
    import queue

    ws = _ws(tmp_path, 0)
    path = ws.write_page("Queued", "# Queued\n\nbody")
    done: list[tuple] = []
    monkeypatch.setattr(page_index, "warm_page_with_retry", lambda root, p: done.append((root, p)))
    q: queue.Queue = queue.Queue()
    q.put((ws.root, path))
    page_index._run_worker(q, once=True)
    assert done == [(ws.root, path)]


def test_a_real_worker_thread_is_started_on_demand(monkeypatch):
    import threading

    started = threading.Event()
    monkeypatch.setattr(page_index, "_worker", None)
    monkeypatch.setattr(page_index, "_run_worker", lambda: started.set())
    page_index._ensure_worker()
    assert started.wait(2)
    assert page_index._worker is not None and page_index._worker.daemon is True
    assert page_index._worker.name == "anthill-page-index"


def test_app_startup_starts_the_warm_up_thread_when_enabled(monkeypatch):
    import threading

    import anthill.web.app as app_mod

    ran = threading.Event()
    monkeypatch.setattr(page_index, "warm_all", lambda: ran.set())
    monkeypatch.setattr(page_index, "ENABLED", False)
    app_mod._warm_page_index_in_background()
    assert not ran.wait(0.3)  # switched off: nothing starts
    monkeypatch.setattr(page_index, "ENABLED", True)
    app_mod._warm_page_index_in_background()
    assert ran.wait(2)


def test_a_model_that_never_comes_up_does_not_stall_every_saved_page(tmp_path, monkeypatch, fake):
    ws = _ws(tmp_path, 0)
    first = ws.write_page("First", "# First\n\nbody")
    second = ws.write_page("Second", "# Second\n\nbody")
    monkeypatch.setattr(page_index, "_gave_up", False)
    monkeypatch.setattr(emb, "available", lambda: False)
    sleeps: list[float] = []
    assert (
        page_index.warm_page_with_retry(ws.root, first, sleep=sleeps.append, waits=(5, 10)) is False
    )
    assert sleeps == [0, 5, 10]  # the first page uses the whole retry schedule
    sleeps.clear()
    assert (
        page_index.warm_page_with_retry(ws.root, second, sleep=sleeps.append, waits=(5, 10))
        is False
    )
    assert sleeps == []  # the next page checks once and does not wait again
    monkeypatch.setattr(emb, "available", lambda: True)
    assert (
        page_index.warm_page_with_retry(ws.root, second, sleep=sleeps.append, waits=(5, 10)) is True
    )
    assert (
        sleeps == []
    )  # the model is back: no waiting, and the next page gets the full schedule again
    assert page_index._gave_up is False


def test_a_full_queue_drops_the_page_instead_of_growing_or_raising(tmp_path, monkeypatch):
    import queue

    ws = _ws(tmp_path, 0)
    monkeypatch.setattr(page_index, "ENABLED", True)
    monkeypatch.setattr(page_index, "_pending", queue.Queue(maxsize=1))
    monkeypatch.setattr(page_index, "_ensure_worker", lambda: None)
    page_index.warm_page_soon(ws.root, ws.wiki / "a.md")
    page_index.warm_page_soon(ws.root, ws.wiki / "b.md")  # full: dropped quietly
    assert page_index._pending.qsize() == 1


def test_an_unreadable_page_keeps_its_row_until_it_is_really_gone(tmp_path, fake):
    import os
    import sqlite3

    if os.geteuid() == 0:
        pytest.skip("root can read any file")
    ws = _ws(tmp_path, 3)
    page_index.warm_workspace(ws.root)
    locked = ws.wiki / "page-1.md"
    locked.chmod(0)
    try:
        page_index.warm_workspace(ws.root)
    finally:
        locked.chmod(0o644)
    con = sqlite3.connect(str(ws.root / ".cache" / "page_vectors.sqlite3"))
    paths = {r[0] for r in con.execute("SELECT path FROM page_vectors_v1")}
    con.close()
    assert "wiki/page-1.md" in paths  # a transient read failure does not look like a deleted page
