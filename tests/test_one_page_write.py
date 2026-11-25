"""A page is written in one place, whole, with its earlier version kept (build plan step 3.1). Model-free.

Two saves at once never lose text, a save made from an out-of-date copy is refused, an edit made outside the app is
noticed and never overwritten, and a page is never written without a record."""

import os
import threading
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from anthill.web.db import WikiReview
from anthill.wiki import items, page_store
from anthill.wiki.page_store import StaleSave, page_hash
from anthill.wiki.workspace import Workspace
from tests.test_upload_status import _login, _setup

V1 = "# Ferry\n\nThe ferry leaves at nine.\n"
V2 = "# Ferry\n\nThe ferry leaves at ten.\n"
V3 = "# Ferry\n\nThe ferry leaves at eleven.\n"


def _ws(tmp_path, scope="personal") -> Workspace:
    ws = Workspace(tmp_path / "w", scope=scope)
    ws.init()
    return ws


def _text(ws: Workspace, slug="ferry") -> str:
    return (ws.wiki / f"{slug}.md").read_text()


def _outside_edit(ws: Workspace, old: str, new: str, slug="ferry") -> None:
    """What a person does in a text editor: change the words, leave the front matter as the app wrote it."""
    path = ws.wiki / f"{slug}.md"
    path.write_text(path.read_text().replace(old, new))


def _kept(ws: Workspace, slug="ferry") -> list[str]:
    return [p.read_text() for _n, p in page_store.versions(ws, slug)]


# ── whole, or not at all ──────────────────────────────────────────────────────


def test_a_write_that_fails_part_way_leaves_the_old_page_whole(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    before = _text(ws)

    def crash(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        ws.write_page("ferry", V2)
    monkeypatch.undo()
    assert _text(ws) == before
    assert sorted(p.name for p in ws.wiki.iterdir()) == [
        "ferry.md"
    ]  # no half page, no temporary file


def test_a_page_read_while_it_is_being_rewritten_is_always_a_whole_page(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    stop = threading.Event()
    torn: list[str] = []

    def read():
        while not stop.is_set():
            text = _text(ws)
            if not text.endswith("THE END.\n") and "Departure" in text:
                torn.append(text[-40:])

    reader = threading.Thread(target=read)
    reader.start()
    for i in range(60):
        ws.write_page("ferry", f"# Ferry\n\nDeparture {i}.\n" + "More text. " * 3000 + "\nTHE END.")
    stop.set()
    reader.join()
    assert not torn


# ── the earlier version is kept ───────────────────────────────────────────────


def test_each_write_keeps_the_text_it_replaced(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    first = _text(ws)
    ws.write_page("ferry", V2)
    second = _text(ws)
    ws.write_page("ferry", V3)
    assert _kept(ws) == [first, second]  # whole files, oldest first, record included
    assert "The ferry leaves at eleven." in _text(ws)


def test_writing_the_same_text_again_keeps_nothing_new(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    ws.write_page("ferry", V1)
    ws.write_page("ferry", V1)
    assert _kept(ws) == []


def test_history_is_not_a_page(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    ws.write_page("ferry", V2)
    assert [p.stem for p in ws.pages()] == ["ferry"]
    assert [p.stem for p in ws.retrievable_pages()] == ["ferry"]


# ── a save made from an old copy is refused ───────────────────────────────────


def test_a_save_made_from_an_out_of_date_copy_is_refused_and_the_page_stays(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    opened = page_hash(_text(ws))
    ws.write_page("ferry", V2, by_person=True, base_hash=opened)  # someone saves first
    with pytest.raises(StaleSave) as err:
        ws.write_page("ferry", V3, by_person=True, base_hash=opened)
    assert err.value.outside is False
    assert "ten" in _text(ws) and "eleven" not in _text(ws)


def test_two_saves_at_once_never_lose_text(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    opened = page_hash(_text(ws))
    n = 12
    gate = threading.Barrier(n)
    won: list[int] = []
    refused: list[int] = []

    def save(i: int):
        gate.wait()
        try:
            ws.write_page("ferry", f"# Ferry\n\nDeparture {i}.\n", by_person=True, base_hash=opened)
            won.append(i)
        except StaleSave:
            refused.append(i)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(won) == 1 and len(refused) == n - 1  # one was applied, every other was told
    assert f"Departure {won[0]}." in _text(ws)
    assert any("nine" in k for k in _kept(ws))  # the text it replaced is still there


# ── an edit made outside the app ──────────────────────────────────────────────


def test_an_edit_made_outside_the_app_is_recorded_as_a_persons_and_kept_by_a_model_write(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    before = items.read_page_record(_text(ws))
    _outside_edit(ws, "nine", "half past eight")
    with pytest.raises(StaleSave) as err:
        ws.write_page(
            "ferry", "# Ferry\n\nThe ferry leaves at noon.\n"
        )  # a model, an import, a task
    assert err.value.outside is True
    text = _text(ws)
    assert "half past eight" in text and "noon" not in text
    rec = items.read_page_record(text)
    assert rec.id == before.id and rec.version == before.version + 1
    assert rec.origin == "person-edited" and rec.review_state == "checked"
    assert rec.parent_hash == before.content_hash and rec.content_hash == page_hash(text)


def test_a_person_saving_from_the_outside_edit_replaces_it_and_it_stays_in_history(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    _outside_edit(ws, "nine", "half past eight")
    seen = _text(ws)
    ws.write_page("ferry", V2, by_person=True, base_hash=page_hash(seen))
    assert "ten" in _text(ws)
    assert any("half past eight" in k for k in _kept(ws))  # the outside edit was kept, not lost
    assert (
        items.read_page_record(_text(ws)).version == 3
    )  # the original, the outside edit, this save


def test_a_person_saving_from_a_copy_older_than_the_outside_edit_is_refused(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    opened = page_hash(_text(ws))
    _outside_edit(ws, "nine", "half past eight")
    with pytest.raises(StaleSave) as err:
        ws.write_page("ferry", V2, by_person=True, base_hash=opened)
    assert err.value.outside is True and "half past eight" in _text(ws)


def test_a_page_that_matches_its_record_is_not_called_edited(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    assert page_store.outside_edit(_text(ws)) is None
    ws.write_page("ferry", V2)  # a model write over an untouched page goes through
    assert "ten" in _text(ws)


def test_a_legacy_page_with_no_record_is_not_taken_for_an_outside_edit(tmp_path):
    ws = _ws(tmp_path)
    (ws.wiki / "ferry.md").write_text(V1)
    ws.write_page("ferry", V2)
    assert "ten" in _text(ws) and items.read_page_record(_text(ws)) is not None
    assert any("nine" in k for k in _kept(ws))


# ── a record is always written ────────────────────────────────────────────────


def test_a_page_whose_record_cannot_be_made_still_gets_one(tmp_path, monkeypatch):
    ws = _ws(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("no record today")

    monkeypatch.setattr(items, "source_name_of", boom)
    ws.write_page("ferry", V1)
    rec = items.read_page_record(_text(ws))
    assert rec is not None and items.is_retrievable(rec)


def test_a_page_with_a_broken_record_gets_a_good_one_on_its_next_write(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("ferry", V1)
    path = ws.wiki / "ferry.md"
    path.write_text(path.read_text().replace("lifecycle: active", "lifecycle: sideways"))
    ws.write_page("ferry", V2)
    assert items.is_retrievable(items.read_page_record(_text(ws)))


# ── through the app ───────────────────────────────────────────────────────────


def _personal(ids) -> Workspace:
    root = Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{ids['owner'][0]}"
    ws = Workspace(root, scope="personal")
    ws.init()
    ws.write_page("ferry", V1)
    return ws


def _form(client, slug="ferry"):
    soup = BeautifulSoup(client.get(f"/wiki/page/{slug}/edit").text, "html.parser")
    return soup.find("textarea").get_text(), soup.find("input", {"name": "base_hash"})["value"]


def _save(client, text, base, slug="ferry"):
    return client.post(
        f"/wiki/page/{slug}/edit",
        data={"content": text, "scope": "personal", "base_hash": base},
        follow_redirects=False,
    )


def test_a_save_in_the_edit_screen_made_after_another_save_is_refused_with_a_message(
    tmp_path, monkeypatch
):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    _shown, opened = _form(client)
    assert _save(client, "# Ferry\n\nThe ferry leaves at ten.\n", opened).status_code == 303
    res = _save(
        client, "# Ferry\n\nThe ferry leaves at eleven.\n", opened
    )  # a second tab, same start
    assert res.status_code == 409
    plain = " ".join(BeautifulSoup(res.text, "html.parser").get_text().split())
    assert "was changed by someone else after you opened it" in plain
    assert "eleven" in plain  # their text is still in the box
    assert "ten" in _text(ws) and "eleven" not in _text(ws)


def test_a_save_after_an_outside_edit_is_refused_and_says_so(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    _shown, opened = _form(client)
    _outside_edit(ws, "nine", "half past eight")
    res = _save(client, "# Ferry\n\nThe ferry leaves at ten.\n", opened)
    assert res.status_code == 409
    assert "changed outside Anthill" in " ".join(
        BeautifulSoup(res.text, "html.parser").get_text().split()
    )
    assert "half past eight" in _text(ws)


def test_a_save_from_the_current_form_goes_through_and_keeps_the_old_text(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    shown, opened = _form(client)
    assert _save(client, shown.replace("nine", "ten"), opened).status_code == 303
    assert "ten" in _text(ws) and any("nine" in k for k in _kept(ws))


def test_a_model_proposal_over_a_page_changed_outside_the_app_waits_for_a_person(
    tmp_path, monkeypatch
):
    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _outside_edit(ws, "nine", "half past eight")
    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    try:
        applied = app_mod.propose_wiki_write(
            s,
            org_id=org_id,
            proposed_by=uid,
            slug="ferry",
            content="# Ferry\n\nThe ferry is cancelled.\n",
            target_scope="personal",
            source="a task",
        )
        rows = s.query(WikiReview).all()
    finally:
        s.close()
    assert applied is False and len(rows) == 1 and "page-changed" in rows[0].flags
    assert "half past eight" in _text(ws) and "cancelled" not in _text(ws)
