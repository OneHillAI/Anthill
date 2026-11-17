"""A short plain-text note is filed as the page itself, not summarised (#181). A small model asked to
summarise a four-line interview note reworded a quote and invented facts (#150); a note this short has
little to condense, so ingest makes no model call at all: the note's own H1 is the title (else the file
name) and its text is the body, unchanged. Larger sources, PDFs and images keep the summarise path.
Model-free: the backend either raises on any call or records it."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas
from test_wiki_duplicate_uploads import NOTE, _pending, _setup, _upload
from test_wiki_ui import _client

from anthill.inference.base import BackendError
from anthill.lifecycle.renovate import renovate
from anthill.lifecycle.version import read_provenance, stale_pages, tag_page
from anthill.wiki.ingest import _VERBATIM_NOTE_MAX_CHARS, ingest
from anthill.wiki.review import outline_change
from anthill.wiki.workspace import Workspace, workspace_for

COURIER_NOTE = (
    "# Interview: courier (P11), 19 Sep 2026\n"
    "Client: Pierogi&Co delivery app redesign (confidential)\n"
    "- Map pins inaccurate in Gdansk old town; addresses missing gate codes.\n"
    '- Wants a single "can\'t deliver" button with reason codes.\n'
    '- Quote: "The app thinks every street is a straight line."\n'
)


class _NoModel:
    """A backend that must never be asked anything."""

    model = "test-model"

    def chat(self, messages, **kwargs):
        raise AssertionError("a short note must not reach the model")


class _Recording:
    model = "test-model"

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return "# Summary\n\nA model-written summary.\n"


def _wiki(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    return ws


def _ingest(ws, tmp_path, name: str, text: str, backend=None) -> tuple[str, str]:
    source = tmp_path / name
    source.write_text(text)
    captured: dict = {}
    ingest(
        ws,
        source,
        backend or _NoModel(),
        on_write=lambda title, page: captured.update(title=title, page=page),
    )
    return captured["title"], captured["page"]


def _body(page: str) -> str:
    return page.split("\n\n<!-- anthill:")[0]


def test_a_short_note_becomes_the_page_with_no_model_call(tmp_path):
    ws = _wiki(tmp_path)
    title, page = _ingest(ws, tmp_path, "courier.md", COURIER_NOTE)
    assert title == "Interview: courier (P11), 19 Sep 2026"
    assert _body(page) == COURIER_NOTE.strip()


def test_ingest_without_a_review_gate_writes_the_note_to_the_wiki(tmp_path):
    ws = _wiki(tmp_path)
    source = tmp_path / "courier.md"
    source.write_text(COURIER_NOTE)
    path = ingest(ws, source, _NoModel())
    assert path is not None and path.exists()
    assert COURIER_NOTE.strip() in path.read_text()
    assert "courier-p11" in path.name


def test_a_note_without_an_h1_is_titled_with_its_file_name(tmp_path):
    ws = _wiki(tmp_path)
    title, page = _ingest(ws, tmp_path, "standup-moved.txt", "Standup is now at 10am.\n")
    assert title == "standup-moved"
    assert _body(page) == "# standup-moved\n\nStandup is now at 10am."


def test_quotes_and_wording_are_byte_identical(tmp_path):
    ws = _wiki(tmp_path)
    _, page = _ingest(ws, tmp_path, "courier.md", COURIER_NOTE)
    assert '- Quote: "The app thinks every street is a straight line."' in page
    assert "## Quotes" not in page
    assert '- Wants a single "can\'t deliver" button with reason codes.' in page


def test_the_users_own_related_section_and_links_are_not_rewritten(tmp_path):
    note = (
        "# Plan\n\nSee [[Not Yet Written]] for context.\n\n"
        "## Related\n- [[Not Yet Written]]\n- [[Also Missing]]\n\n## Later\nMore text.\n"
    )
    ws = _wiki(tmp_path)
    _, page = _ingest(ws, tmp_path, "plan.md", note)
    assert _body(page) == note.strip()


def test_the_page_is_tagged_as_verbatim_with_its_content_addressed_source(tmp_path):
    ws = _wiki(tmp_path)
    _, page = _ingest(ws, tmp_path, "courier.md", COURIER_NOTE)
    prov = read_provenance(page)
    assert prov is not None
    assert prov["model"] == "verbatim" and "pipeline_model" not in prov
    assert prov["source"].startswith("courier-") and prov["source"].endswith(".md")
    assert (ws.raw / prov["source"]).read_text() == COURIER_NOTE


def test_a_note_at_the_threshold_is_verbatim_and_one_character_over_is_summarised(tmp_path):
    ws = _wiki(tmp_path)
    at_limit = "# Long\n\n" + "a" * (_VERBATIM_NOTE_MAX_CHARS - len("# Long\n\n"))
    assert len(at_limit) == _VERBATIM_NOTE_MAX_CHARS
    _, page = _ingest(ws, tmp_path, "at.md", at_limit)
    assert _body(page) == at_limit

    backend = _Recording()
    title, page = _ingest(ws, tmp_path, "over.md", at_limit + "a", backend)
    assert len(backend.calls) == 1
    assert title == "Long"  # the source H1 still wins on the summarise path (#150)
    assert "A model-written summary." in page
    assert read_provenance(page)["model"] == "test-model"


def test_an_empty_note_still_takes_the_summarise_path(tmp_path):
    backend = _Recording()
    _ingest(_wiki(tmp_path), tmp_path, "blank.md", "  \n", backend)
    assert len(backend.calls) == 1


def test_a_pdf_still_goes_through_the_model(tmp_path):
    source = tmp_path / "short.pdf"
    canvas = Canvas(str(source), pagesize=letter)
    canvas.drawString(72, 700, "Invoice 451 was received.")
    canvas.save()
    backend = _Recording()
    captured: dict = {}
    ingest(_wiki(tmp_path), source, backend, on_write=lambda t, p: captured.update(title=t, page=p))
    assert len(backend.calls) == 1
    assert read_provenance(captured["page"])["model"] == "test-model"


def test_an_image_still_goes_through_the_vision_path(tmp_path):
    source = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), "white").save(source)
    with pytest.raises(BackendError, match="vision model"):
        ingest(_wiki(tmp_path), source, _NoModel(), on_write=lambda t, p: None)


def test_the_same_short_note_uploaded_twice_is_held(tmp_path, monkeypatch):
    client, app_mod, _backend, uid, org_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod, "_backend_from_cfg", lambda cfg: _NoModel())
    ws = workspace_for("personal", user_id=uid)
    no_h1 = b"The owner bakes at 4am and hates the orders app.\n"

    assert "saved=added" in _upload(client, _backend, "bakery.md", NOTE)
    assert "saved=added" in _upload(client, _backend, "bakery-two.txt", no_h1)
    before = {p.name: p.read_text() for p in ws.pages()}

    assert "saved=held" in _upload(client, _backend, "bakery.md", NOTE)
    assert "saved=held" in _upload(client, _backend, "bakery-two.txt", no_h1)

    assert {p.name: p.read_text() for p in ws.pages()} == before
    assert len(_pending(app_mod, org_id)) == 2


def test_the_page_view_says_it_is_the_note_as_uploaded(tmp_path, monkeypatch):
    c, app_mod, _org, uid = _client(tmp_path, monkeypatch, role="member")
    try:
        ws = workspace_for("personal", user_id=uid)
        ws.init()
        source = tmp_path / "courier.md"
        source.write_text(COURIER_NOTE)
        ingest(ws, source, _NoModel())
        text = ws.pages()[0].read_text()
        stamp = read_provenance(text)["date"]
        name = read_provenance(text)["source"]

        r = c.get(f"/wiki/page/{ws.pages()[0].stem}")
        assert r.status_code == 200
        meta = BeautifulSoup(r.text, "html.parser").select_one("p.text-muted")
        assert meta.get_text(" ", strip=True) == f"The note as uploaded on {stamp} from {name}."
        assert "Written by" not in r.text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_a_verbatim_page_is_never_stale_and_renovation_leaves_it_alone(tmp_path):
    ws = _wiki(tmp_path)
    source = tmp_path / "courier.md"
    source.write_text(COURIER_NOTE)
    path = ingest(ws, source, _NoModel())
    ws.write_page("Older", tag_page("# Older\n\nBody.", "old:1"))
    before = path.read_text()

    assert [p.name for p, _ in stale_pages(ws.wiki, "new:1")] == ["older.md"]

    for only_stale in (True, False):
        report = renovate(ws, _Recording(), "new:1", only_stale=only_stale, dry_run=True)
        assert path.stem not in report.regenerated
    report = renovate(ws, _NoModel(), "new:1", only_stale=False)
    assert path.stem in report.unchanged
    assert path.read_text() == before


def test_a_note_without_links_passes_the_review_gate(tmp_path):
    ws = _wiki(tmp_path)
    title, page = _ingest(ws, tmp_path, "courier.md", COURIER_NOTE)
    assert outline_change(_NoModel(), ws, title, page, scope="personal").flags == []


def test_a_note_with_its_own_dangling_link_is_flagged_like_a_hand_typed_page(tmp_path):
    """The note's text is not rewritten, so a [[link]] to a page that does not exist reaches the gate
    exactly as it would if typed by hand: flagged for review, never silently delinked."""
    ws = _wiki(tmp_path)
    ws.write_page("Delivery", "# Delivery\n\nParcels.")
    title, page = _ingest(
        ws, tmp_path, "plan.md", "# Plan\n\nSee [[Delivery]] and [[Not Yet Written]].\n"
    )
    outline = outline_change(_NoModel(), ws, title, page, scope="personal")
    assert outline.flags == ["broken_links"]
    assert "Not Yet Written" in outline.text
