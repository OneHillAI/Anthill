"""What Anthill remembered is kept in a file once and offered for import onto the core page (build plan step 2.3, slice c;
docs/specs/memory-archive.md). Model-free."""

import hashlib
import json

import anthill.web.app as app_mod
from anthill.web.db import MemoryItem, User
from anthill.web.recall import recall_memory
from anthill.wiki import core_page, items, memory_archive
from anthill.wiki.workspace import workspace_for
from tests.test_answers_use_document_text import MILL, chat, embeds  # noqa: F401

HELLO = "Hello there, how are you today?"


def _ids():
    s = app_mod._SessionFactory()
    try:
        user = s.query(User).filter(User.email == "a@a.com").one()
        return user.id, user.org_id
    finally:
        s.close()


def _add_rows(*rows):
    """rows: (user_id, scope, kind, text). Returns the ids in order."""
    s = app_mod._SessionFactory()
    uid, org = _ids()
    other = s.query(User).filter(User.email == "other@a.com").first()
    if other is None:
        other = User(org_id=org, email="other@a.com", role="member", active=True)
        s.add(other)
        s.flush()
    made = []
    for who, scope, kind, text in rows:
        row = MemoryItem(
            org_id=org, user_id=uid if who == "me" else other.id, scope=scope, kind=kind, text=text
        )
        s.add(row)
        s.flush()
        made.append(row.id)
    s.commit()
    s.close()
    return made


def _rows_in_db():
    s = app_mod._SessionFactory()
    try:
        return [
            (r.id, r.user_id, r.scope, r.kind, r.text)
            for r in s.query(MemoryItem).order_by(MemoryItem.id)
        ]
    finally:
        s.close()


def _tree_hash(path):
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.glob("*"))
        if p.is_file()
    }


# ---- the archive ----------------------------------------------------------------------------------------------------------


def test_the_first_personal_turn_keeps_the_persons_own_rows_in_a_file_and_changes_no_row(chat):  # noqa: F811
    mine = _add_rows(
        ("me", "personal", "preference", "Prefers metric units."),
        ("me", "personal", "fact", "Runs a bakery in Leeds."),
    )
    _add_rows(
        ("other", "personal", "fact", "Someone else's private fact."),
        ("me", "team", "fact", "A team row."),
        ("me", "org", "fact", "An org row."),
    )
    before = _rows_in_db()
    chat.ask(HELLO)
    ws = workspace_for("personal", user_id=_ids()[0])
    rows = memory_archive.read_rows(ws)
    assert [r["id"] for r in rows] == mine and [r["text"] for r in rows] == [
        "Prefers metric units.",
        "Runs a bakery in Leeds.",
    ]
    assert all(set(r) == {"id", "text", "kind", "source", "created"} for r in rows)  # no embedding
    day = json.loads((ws.root / "archive" / memory_archive.MARKER).read_text())["file"]
    md = (ws.root / "archive" / day.replace(".json", ".md")).read_text()
    assert "Prefers metric units." in md and "Someone else" not in md and "An org row" not in md
    assert _rows_in_db() == before  # no row was changed or removed
    same = _tree_hash(ws.root / "archive")
    chat.ask(HELLO)
    assert _tree_hash(ws.root / "archive") == same  # once


def test_a_person_with_no_memory_gets_an_empty_archive_and_the_marker(chat):  # noqa: F811
    chat.ask(HELLO)
    ws = workspace_for("personal", user_id=_ids()[0])
    assert memory_archive.archived(ws) and memory_archive.read_rows(ws) == []


def test_recall_returns_what_it_returned_before_the_archive(chat):  # noqa: F811
    _add_rows(("me", "personal", "preference", "Prefers metric units."))
    uid, org = _ids()
    s = app_mod._SessionFactory()
    before = recall_memory(s, org, uid, "which units do I prefer?")
    chat.ask(HELLO)
    assert recall_memory(s, org, uid, "which units do I prefer?") == before
    s.close()


def test_a_turn_not_allowed_personal_context_writes_no_archive(chat, monkeypatch):  # noqa: F811
    import dataclasses

    from anthill.web import plane_routing

    real = plane_routing.plane_inference
    monkeypatch.setattr(
        plane_routing,
        "plane_inference",
        lambda *a, **k: dataclasses.replace(real(*a, **k), use_personal_context=False),
    )
    _add_rows(("me", "personal", "fact", "A private fact."))
    chat.ask(HELLO)
    assert not memory_archive.archived(workspace_for("personal", user_id=_ids()[0]))


def test_a_failed_write_never_stops_the_answer_and_is_tried_again_next_turn(chat, monkeypatch):  # noqa: F811
    _add_rows(("me", "personal", "fact", "A private fact."))
    real = memory_archive.write
    calls = {"n": 0}

    def flaky(ws, rows, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("read-only disk")
        return real(ws, rows, **k)

    monkeypatch.setattr(memory_archive, "write", flaky)
    chat.ask(HELLO)
    ws = workspace_for("personal", user_id=_ids()[0])
    assert chat.state["prompts"] and not memory_archive.archived(
        ws
    )  # the answer was given, the archive was not made
    chat.ask(HELLO)
    assert memory_archive.archived(ws) and [r["text"] for r in memory_archive.read_rows(ws)] == [
        "A private fact."
    ]


# ---- import ---------------------------------------------------------------------------------------------------------------


def _archived_world(chat, *rows):  # noqa: F811
    ids = _add_rows(*rows)
    chat.ask(HELLO)
    return ids, workspace_for("personal", user_id=_ids()[0])


def test_the_import_list_leaves_out_what_is_already_on_the_page_and_names_sensitive_items(chat):  # noqa: F811
    (a, b, _c), ws = _archived_world(
        chat,
        ("me", "personal", "preference", "Prefers metric units."),
        ("me", "personal", "fact", "Reach me at sam@example.com"),
        ("me", "personal", "fact", "Runs a bakery."),
    )
    core_page.save(ws, preferences="- runs a BAKERY.")
    listed = chat.client.get("/core-page/memory-import").json()
    assert listed["archived"] is True
    by_id = {r["id"]: r for r in listed["rows"]}
    assert (
        set(by_id) == {a, b}
        and by_id[b]["sensitive"] == ["an email address"]
        and by_id[a]["sensitive"] == []
    )
    assert by_id[a]["line"] == "Prefers metric units."


def test_importing_chosen_rows_adds_exactly_those_lines_in_one_new_version(chat):  # noqa: F811
    (a, b, c), ws = _archived_world(
        chat,
        ("me", "personal", "preference", "Prefers metric units."),
        ("me", "personal", "fact", "Runs a bakery."),
        ("me", "personal", "fact", "Not chosen."),
    )
    core_page.save(ws, profile="I teach piano.", preferences="- Runs a bakery.")
    v1 = items.read_page_record(core_page.path_of(ws).read_text()).version
    r = chat.client.post("/core-page/memory-import", data={"ids": [a, b, 999]}).json()
    assert (
        r["added"] == [a]
        and r["present"] == [b]
        and r["unknown"] == [999]
        and r["no_room"] == []
        and r["saved"] is True
    )
    assert core_page.read(ws) == ("I teach piano.", "- Runs a bakery.\n- Prefers metric units.")
    assert items.read_page_record(core_page.path_of(ws).read_text()).version == v1 + 1
    rest = chat.client.get("/core-page/memory-import").json()["rows"]
    assert [x["id"] for x in rest] == [c]  # the one not chosen is still on offer
    assert len(memory_archive.read_rows(ws)) == 3  # and still in the archive


def test_rows_that_do_not_fit_under_the_cap_are_named_and_not_added(chat):  # noqa: F811
    big = ["x" * 60 + f" {i:02d}" for i in range(80)]
    ids, ws = _archived_world(chat, *[("me", "personal", "fact", t) for t in big])
    r = chat.client.post("/core-page/memory-import", data={"ids": ids}).json()
    assert (
        r["added"]
        and r["no_room"]
        and set(r["added"]) | set(r["no_room"]) == set(ids)
        and r["saved"] is True
    )
    assert (
        len(core_page.path_of(ws).read_text()) <= core_page.CAP_CHARS + 600
    )  # the page (with its record) stays near the cap
    assert core_page.read(ws)[1].count("\n") + 1 == len(r["added"])


def test_a_long_row_is_one_line_of_at_most_the_limit_and_the_archive_keeps_the_full_text(chat):  # noqa: F811
    (a,), ws = _archived_world(chat, ("me", "personal", "fact", "long " * 100))
    chat.client.post("/core-page/memory-import", data={"ids": [a]})
    assert len(core_page.read(ws)[1]) <= core_page.MAX_LINE + 2
    assert memory_archive.read_rows(ws)[0]["text"] == "long " * 100


def test_importing_nothing_changes_nothing(chat):  # noqa: F811
    _, ws = _archived_world(chat, ("me", "personal", "fact", "A fact."))
    r = chat.client.post("/core-page/memory-import", data={}).json()
    assert r["added"] == [] and r["saved"] is False and not core_page.exists(ws)
