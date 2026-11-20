"""An item with no valid record is not used in an answer, and a wiki from before records is given its records
once, automatically, so it keeps working (build plan step 1.1). Model-free."""

import hashlib
import threading
from pathlib import Path

import pytest

import anthill.wiki.ask as ask_mod
from anthill.lifecycle.version import tag_page
from anthill.wiki import doc_index, inventory, items
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import (  # noqa: F401
    HANDBOOK,
    MILL,
    RAW,
    SUMMARY,
    _Backend,
    embeds,
)

PAGE = "hollis-bakery-operations-handbook"
HELD = "held-bbbbbbbbbbbb.pdf"


def _tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "_backups" not in p.parts
    }


def _legacy(root: Path) -> Workspace:
    """A wiki made before records: pages and originals exist and nothing has a record."""
    ws = Workspace(root, scope="personal")
    ws.init()
    ws.text.mkdir(exist_ok=True)
    (ws.raw / RAW).write_bytes(b"%PDF-1.4 handbook")
    (ws.text / f"{RAW}.txt").write_text(HANDBOOK, encoding="utf-8")
    (ws.raw / HELD).write_bytes(b"%PDF-1.4 held")
    (ws.text / f"{HELD}.txt").write_text(
        "The held document says the vault key is in drawer nine.", encoding="utf-8"
    )
    (ws.wiki / f"{PAGE}.md").write_text(
        tag_page(SUMMARY, "qwen2.5:3b", on="2026-11-01", source=RAW)
    )
    (ws.wiki / "notes.md").write_text("# Notes\n\nA page of my own.\n")
    doc_index.sync_workspace(ws.root)
    items._upgrade_tried.discard(str(ws.root))
    inventory.UPGRADE_ERRORS.pop(str(ws.root), None)
    return ws


def _bodies(ws: Workspace) -> dict:
    from anthill.common.text import strip_frontmatter

    return {p.stem: strip_frontmatter(p.read_text()).strip() for p in ws.pages()}


# ── the rule ──────────────────────────────────────────────────────────────────


def test_no_record_is_not_allowed():
    assert items.is_retrievable(None) is False


def test_a_page_and_an_original_made_after_the_upgrade_without_a_record_are_not_used(tmp_path):
    ws = _legacy(tmp_path / "w")
    items.ensure_records(ws.root)  # the wiki has been prepared
    (ws.wiki / "late.md").write_text("# Late\n\nNo record.\n")
    (ws.raw / "late-cccccccccccc.pdf").write_bytes(b"x")
    assert "late" not in [p.stem for p in ws.retrievable_pages()]
    assert "late-cccccccccccc.pdf" in items.hidden_sources(ws.root)
    assert PAGE in [p.stem for p in ws.retrievable_pages()]  # what was prepared is still used


def test_a_hand_broken_record_is_quarantined_not_used_and_not_repaired(tmp_path):
    ws = _legacy(tmp_path / "w")
    items.ensure_records(ws.root)
    page = ws.wiki / "notes.md"
    page.write_text(
        "---\nx-anthill-item:\n  id: a\n  kind: page\n  lifecycle: gone\n---\n\n# Notes\n"
    )
    before = page.read_bytes()
    assert "notes" not in [p.stem for p in ws.retrievable_pages()]
    assert [q.path for q in items.list_items(ws).quarantined if "notes" in q.path]
    assert page.read_bytes() == before  # never repaired in place


def test_every_way_of_making_an_item_gives_it_a_record(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    page = ws.write_page("fresh", "# Fresh\n\nText.\n")
    assert items.read_page_record(page.read_text()) is not None
    (ws.raw / "doc-dddddddddddd.pdf").write_bytes(b"x")
    assert items.ensure_source_record(ws, ws.raw / "doc-dddddddddddd.pdf") is not None
    assert "fresh" in [p.stem for p in ws.retrievable_pages()]
    assert (
        items.read_source_record(ws, "doc-dddddddddddd.pdf") is not None
    )  # a new original has its record


# ── a legacy wiki keeps working ───────────────────────────────────────────────


def test_a_legacy_wiki_loses_nothing_after_the_upgrade_and_no_page_text_changes(tmp_path):
    ws = _legacy(tmp_path / "w")
    before_text = _bodies(ws)
    assert items.list_items(ws).unrecorded_pages and items.list_items(ws).unrecorded_sources
    pages = [p.stem for p in ws.retrievable_pages()]  # the first read does the upgrade
    assert sorted(pages) == sorted(before_text) and items.hidden_sources(ws.root) == frozenset()
    assert _bodies(ws) == before_text  # the page text is exactly what it was
    found = items.list_items(ws)
    assert not found.unrecorded_pages and not found.unrecorded_sources and not found.quarantined
    assert (ws.root.parent / "_backups").is_dir() and (
        ws.root / "records" / ".upgraded.json"
    ).is_file()


@pytest.mark.usefixtures("embeds")
def test_the_first_question_on_a_legacy_wiki_is_answered_from_its_pages_and_documents(tmp_path):
    ws = _legacy(tmp_path / "w")
    backend = _Backend()
    list(ask_mod.ask_stream(ws, MILL, backend))
    assert (
        "Brandt mill" in backend.prompts[0] and "Flurf" in backend.prompts[0]
    )  # document passage and page


def test_it_runs_once_and_a_second_start_does_nothing(tmp_path):
    ws = _legacy(tmp_path / "w")
    first = inventory.ensure_upgraded(ws)
    assert first["pages"] == 2 and first["sources"] == 2
    tree = _tree(ws.root.parent)
    assert inventory.ensure_upgraded(ws) == {} and _tree(ws.root.parent) == tree
    assert len(list((ws.root.parent / "_backups").glob("*.tar.gz"))) == 1


def test_the_upgrade_can_be_undone_byte_for_byte(tmp_path):
    ws = _legacy(tmp_path / "w")
    before = _tree(ws.root)
    info = inventory.ensure_upgraded(ws)
    assert _tree(ws.root) != before
    inventory.undo_records(ws, ws.root / "records" / info["manifest"])
    after = {k: v for k, v in _tree(ws.root).items() if not k.startswith("records/")}
    assert after == {k: v for k, v in before.items() if not k.startswith("records/")}


def test_a_waiting_or_rejected_upload_stays_out_when_the_review_rows_say_so(tmp_path):
    ws = _legacy(tmp_path / "w")
    inventory.ensure_upgraded(ws, review_status={HELD: "pending"})
    assert HELD in items.hidden_sources(ws.root) and RAW not in items.hidden_sources(ws.root)
    ws2 = _legacy(tmp_path / "w2")
    inventory.ensure_upgraded(ws2, review_status={HELD: "rejected"})
    assert HELD in items.hidden_sources(ws2.root)


def test_questions_asked_at_once_on_a_legacy_wiki_all_see_its_pages(tmp_path):
    ws = _legacy(tmp_path / "w")
    results: list[int] = []
    gate = threading.Barrier(8)

    def ask():
        gate.wait()
        results.append(len(ws.retrievable_pages()))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [2] * 8  # none of them sees a half-prepared wiki


# ── when it cannot run ────────────────────────────────────────────────────────


def test_when_the_upgrade_cannot_run_old_items_stay_out_and_the_person_is_told_why(
    tmp_path, monkeypatch
):
    ws = _legacy(tmp_path / "w")
    monkeypatch.setattr(
        inventory,
        "backup_workspace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only disk")),
    )
    assert ws.retrievable_pages() == [] and items.hidden_sources(ws.root) >= {RAW, HELD}
    notice = items.upgrade_notice(ws.root)
    assert (
        "could not prepare this wiki" in notice
        and "read-only disk" in notice
        and "next start" in notice
    )
    assert not (ws.root / "records" / ".upgraded.json").exists()
    # the next start tries again, and this time it can
    monkeypatch.undo()
    items._upgrade_tried.discard(str(ws.root))
    assert sorted(p.stem for p in ws.retrievable_pages()) == sorted(["notes", PAGE])
    assert items.upgrade_notice(ws.root).startswith("Anthill prepared this wiki")


def test_the_person_is_told_once_that_the_wiki_was_prepared(tmp_path):
    ws = _legacy(tmp_path / "w")
    inventory.ensure_upgraded(ws)
    first = items.upgrade_notice(ws.root)
    assert "2 pages and 2 documents now have records" in first and "no page text changed" in first
    assert "anthill knowledge inventory --undo records/" in first
    assert items.upgrade_notice(ws.root) == ""  # said once


def test_a_wiki_with_nothing_to_prepare_says_nothing_and_changes_nothing(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page("fresh", "# Fresh\n\nText.\n")
    before = {k: v for k, v in _tree(ws.root).items() if not k.startswith("records/.upgraded")}
    items.ensure_records(ws.root)
    assert items.upgrade_notice(ws.root) == "" and not (ws.root.parent / "_backups").exists()
    assert {
        k: v for k, v in _tree(ws.root).items() if not k.startswith("records/.upgraded")
    } == before


def test_a_folder_that_is_not_a_workspace_is_left_alone(tmp_path):
    stray = tmp_path / "nowhere"
    items.ensure_records(stray)
    assert not stray.exists()
