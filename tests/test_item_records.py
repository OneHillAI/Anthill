"""Item records for sources and pages (#272, slice 1a of #270): a page's record rides in its front matter and
survives a save, a source's record is a sidecar next to ``raw/``, and neither ever fails an upload or a save.
Model-free."""

import hashlib
import json

import pytest
import yaml

from anthill.lifecycle.version import tag_page
from anthill.wiki import items, okf
from anthill.wiki.items import ItemRecord, RecordError
from anthill.wiki.workspace import Workspace

RAW = "report-1a2b3c4d5e6f.pdf"


def _ws(tmp_path, name="user-1", scope="personal") -> Workspace:
    ws = Workspace(tmp_path / name, scope=scope)
    ws.init()
    return ws


def _record_of(ws, slug):
    return items.read_page_record((ws.wiki / f"{slug}.md").read_text())


def _model_page(source=RAW) -> str:
    return tag_page(
        "# Report\n\nRevenue grew in March.\n", "qwen2.5:3b", on="2026-11-01", source=source
    )


# ── the record itself ─────────────────────────────────────────────────────────


def test_a_record_round_trips_through_its_mapping():
    rec = ItemRecord(
        kind="page", space="team", space_ref="team-5", name="a", origin="own", derived_from=["x"]
    )
    again = ItemRecord.from_dict(rec.to_dict())
    assert again == rec
    assert (
        len(rec.id) == 32 and rec.id != ItemRecord(kind="page", space="team").id
    )  # random, not derived


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "folder"},
        {"origin": "robot"},
        {"review_state": "maybe"},
        {"lifecycle": "gone"},
        {"version": 0},
        {"version": "1"},
        {"id": ""},
        {"derived_from": "x"},
    ],
)
def test_an_impossible_value_is_refused(change):
    data = ItemRecord(kind="page", space="personal").to_dict() | change
    with pytest.raises(RecordError):
        ItemRecord.from_dict(data)


def test_a_record_that_is_not_a_mapping_is_refused():
    with pytest.raises(RecordError):
        ItemRecord.from_dict(["not", "a", "mapping"])


# ── a page's record survives a save ───────────────────────────────────────────


def test_a_new_page_gets_a_record_with_the_fields_a_later_step_needs(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page())
    rec = _record_of(ws, "report")
    assert (
        rec.kind == "page"
        and rec.space == "personal"
        and rec.space_ref == "user-1"
        and rec.name == "report"
    )
    assert (
        rec.origin == "model-written"
        and rec.review_state == "unchecked"
        and rec.lifecycle == "active"
    )
    assert rec.version == 1 and rec.parent_hash == "" and rec.learned
    body = (ws.wiki / "report.md").read_text()
    assert rec.content_hash == items.body_hash(body)


@pytest.mark.parametrize(
    "markdown,review,origin,state",
    [
        (tag_page("# N\n\nA note.", "verbatim", on="2026-11-01", source=RAW), "", "own", "checked"),
        ("# Hand written\n\nNo provenance at all.", "", "unknown", "unchecked"),
        (_model_page(), "approved", "model-written", "checked"),
    ],
)
def test_origin_and_review_state_come_from_what_the_page_says(
    tmp_path, markdown, review, origin, state
):
    ws = _ws(tmp_path)
    ws.write_page("p", markdown, review=review)
    rec = _record_of(ws, "p")
    assert (rec.origin, rec.review_state) == (origin, state)


def test_saving_the_same_page_again_keeps_its_id_and_version(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page())
    first = _record_of(ws, "report")
    ws.write_page("report", _model_page())
    assert _record_of(ws, "report") == first


def test_a_changed_body_bumps_the_version_and_remembers_what_it_replaced(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page())
    first = _record_of(ws, "report")
    ws.write_page("report", "# Report\n\nRevenue fell in April.\n")
    second = _record_of(ws, "report")
    assert (
        second.id == first.id and second.origin == first.origin and second.learned == first.learned
    )
    assert second.version == 2 and second.parent_hash == first.content_hash
    assert second.content_hash != first.content_hash


def test_a_page_written_before_records_gets_one_on_its_next_save(tmp_path):
    ws = _ws(tmp_path)
    (ws.wiki / "old.md").write_text("# Old\n\nA page from before.\n")
    assert items.read_page_record((ws.wiki / "old.md").read_text()) is None
    ws.write_page("old", "# Old\n\nA page from before.\n")
    assert _record_of(ws, "old").version == 1


def test_a_damaged_record_is_not_carried_forward_and_does_not_stop_the_save(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page())
    path = ws.wiki / "report.md"
    path.write_text(path.read_text().replace("origin: model-written", "origin: robot"))
    ws.write_page("report", _model_page())
    assert _record_of(ws, "report").origin == "model-written"


def test_other_front_matter_is_kept_beside_the_record(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page(), review="approved", tier="gold")
    fm = yaml.safe_load(items.split_front_matter((ws.wiki / "report.md").read_text())[0])
    assert fm["x-anthill-scope"] == "personal" and fm["x-anthill-review"] == "approved"
    assert fm["x-anthill-tier"] == "gold" and items.FRONT_MATTER_KEY in fm


def test_the_record_survives_okf_parsing_and_export(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page())
    text = (ws.wiki / "report.md").read_text()
    assert okf.parse_okf(text).item["id"] == _record_of(ws, "report").id
    exported = okf.from_wiki_page(text, slug="report")
    assert items.read_page_record(exported).id == _record_of(ws, "report").id


def test_a_failure_to_make_a_record_never_stops_a_page_being_saved_and_it_still_has_one(
    tmp_path, monkeypatch
):
    ws = _ws(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("no record today")

    monkeypatch.setattr(items, "source_name_of", boom)
    path = ws.write_page("report", _model_page())
    assert path.is_file() and "Revenue grew in March." in path.read_text()
    rec = items.read_page_record(path.read_text())
    assert rec is not None and rec.name == "report" and items.is_retrievable(rec)


# ── a source's record is a sidecar ────────────────────────────────────────────


def test_a_kept_original_gets_a_sidecar_that_is_not_in_raw(tmp_path):
    ws = _ws(tmp_path)
    (ws.raw / RAW).write_bytes(b"%PDF-1.4 revenue")
    rec = items.ensure_source_record(ws, ws.raw / RAW)
    assert (
        rec.kind == "source"
        and rec.name == RAW
        and rec.origin == "own"
        and rec.review_state == "proposed"
    )
    assert rec.content_hash == hashlib.sha256(b"%PDF-1.4 revenue").hexdigest()
    sidecar = ws.root / "records" / "sources" / f"{RAW}.json"
    assert json.loads(sidecar.read_text())["id"] == rec.id
    assert [p.name for p in ws.raw.iterdir()] == [RAW]  # nothing was added to raw/


def test_asking_again_returns_the_same_record(tmp_path):
    ws = _ws(tmp_path)
    (ws.raw / RAW).write_bytes(b"x")
    first = items.ensure_source_record(ws, ws.raw / RAW)
    assert items.ensure_source_record(ws, ws.raw / RAW).id == first.id


def test_a_missing_original_has_no_record_and_no_error(tmp_path):
    ws = _ws(tmp_path)
    assert items.ensure_source_record(ws, ws.raw / "gone-1a2b3c4d5e6f.pdf") is None


def test_a_damaged_sidecar_is_never_overwritten(tmp_path):
    ws = _ws(tmp_path)
    (ws.raw / RAW).write_bytes(b"x")
    sidecar = ws.root / "records" / "sources" / f"{RAW}.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("{not json")
    assert items.ensure_source_record(ws, ws.raw / RAW) is None
    assert sidecar.read_text() == "{not json"


def test_a_page_made_from_an_original_links_to_it_and_approves_it(tmp_path):
    ws = _ws(tmp_path)
    (ws.raw / RAW).write_bytes(b"x")
    src = items.ensure_source_record(ws, ws.raw / RAW)
    assert src.review_state == "proposed"  # no page has landed yet
    ws.write_page("report", _model_page())
    assert _record_of(ws, "report").derived_from == [src.id]
    assert items.read_source_record(ws, RAW).review_state == "checked"


def test_a_page_whose_original_is_not_kept_names_it_without_a_record(tmp_path):
    ws = _ws(tmp_path)
    ws.write_page("report", _model_page(source="gone-1a2b3c4d5e6f.pdf"))
    assert _record_of(ws, "report").derived_from == ["name:gone-1a2b3c4d5e6f.pdf"]
    assert not (ws.root / "records").exists()


def test_reading_a_workspace_finds_records_and_what_has_none(tmp_path):
    ws = _ws(tmp_path)
    (ws.raw / RAW).write_bytes(b"x")
    (ws.raw / "other-aaaaaaaaaaaa.pdf").write_bytes(b"y")
    items.ensure_source_record(ws, ws.raw / RAW)
    ws.write_page("report", _model_page())
    (ws.wiki / "legacy.md").write_text("# Legacy\n\nNo front matter.\n")
    found = items.list_items(ws)
    assert [p.name for p in found.pages] == ["report"] and [s.name for s in found.sources] == [RAW]
    assert found.unrecorded_pages == ["legacy"] and found.unrecorded_sources == [
        "other-aaaaaaaaaaaa.pdf"
    ]
    assert found.quarantined == []


# ── an upload creates the records, through the real routes ────────────────────


def test_ingest_keeps_a_source_record_and_a_linked_page_record(tmp_path):
    from anthill.wiki import ingest as ingest_mod
    from tests.test_upload_status import _Backend

    ws = _ws(tmp_path)
    src = tmp_path / "bakery.md"
    src.write_text("# Bakery interview\n\n" + "The owner bakes at four in the morning. " * 40)
    page = ingest_mod.ingest(ws, src, _Backend())
    rec = items.read_page_record(page.read_text())
    sources = items.list_items(ws).sources
    assert (
        len(sources) == 1
        and sources[0].name.startswith("bakery-")
        and sources[0].name.endswith(".md")
    )
    assert rec.derived_from == [sources[0].id] and sources[0].review_state == "checked"


@pytest.mark.usefixtures("summarise_every_note")
def test_an_upload_through_the_web_route_leaves_records(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for
    from tests.test_upload_status import _login, _setup, _upload

    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    assert _upload(client).status_code in (302, 303)
    ws = workspace_for("personal", user_id=ids["owner"][0], org_id=ids["owner"][1])
    found = items.list_items(ws)
    assert len(found.pages) == 1 and len(found.sources) == 1
    assert found.pages[0].derived_from == [found.sources[0].id]
    assert (
        found.unrecorded_pages == [] and found.unrecorded_sources == [] and found.quarantined == []
    )
    # The record is bookkeeping: the page a person opens does not show it.
    page = client.get(f"/wiki/page/{found.pages[0].name}")
    assert page.status_code == 200
    assert "x-anthill-item" not in page.text and found.pages[0].id not in page.text
    assert found.pages[0].content_hash not in page.text


def test_a_page_that_starts_with_a_byte_order_mark_still_has_its_front_matter():
    front, rest = items.split_front_matter(chr(0xFEFF) + "---\ntype: Concept\n---\n\n# T\n")
    assert front == "type: Concept" and rest == "\n# T\n"
