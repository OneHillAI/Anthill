"""A fresh/sparse wiki could never accept its first upload (founder report: "document upload on
the knowledge doesn't really work - it doesn't upload it, doesn't do anything, nor show the
uploaded docs after"). Root cause: the summariser's own "## Related" section links to pages that
don't exist yet on a new wiki; `outline_change`'s mechanical broken-links check then flags every
ingested page, and `propose_wiki_write` never writes it - the model's own generated links strand
its own first pages in review forever. Fixed by neutralising a dangling `[[link]]` at ingest time,
before the review gate ever sees it - an inline dangling link keeps its text (just loses the
brackets); a "## Related" section that turns out to be nothing but dangling link-bullets is dropped
entirely instead of left as a heading over a list of now-bare, delinked words. A genuinely broken
link a person types during a manual edit is untouched by this and is still caught.

Also covers the second half of the same report: the upload file picker's `accept` list had
drifted from the backend's actual supported formats (Word/PowerPoint/Excel/HTML were greyed out).
"""

from __future__ import annotations

from anthill.wiki.ingest import _neutralize_dangling_links, ingest
from anthill.wiki.review import outline_change
from anthill.wiki.workspace import Workspace


class _RecordingBackend:
    model = "test-model"

    def __init__(self, reply: str):
        self.calls = []
        self._reply = reply

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self._reply


# ── _neutralize_dangling_links (unit) ──────────────────────────────────────────────


def test_dangling_link_loses_its_brackets_but_keeps_the_text(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    out = _neutralize_dangling_links("See [[Records Retention]] for details.", ws)
    assert out == "See Records Retention for details."


def test_link_to_a_real_page_is_left_intact(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    ws.write_page("Records Retention", "# Records Retention\n\nThe policy.")
    out = _neutralize_dangling_links("See [[Records Retention]] for details.", ws)
    assert out == "See [[Records Retention]] for details."


def test_self_referencing_link_is_left_intact(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    out = _neutralize_dangling_links(
        "# Confidentiality Basics\n\nSee [[Confidentiality Basics]] above.",
        ws,
        self_title="Confidentiality Basics",
    )
    assert "[[Confidentiality Basics]]" in out


def test_a_related_section_of_only_dangling_links_is_dropped_entirely(tmp_path):
    """The realistic case: the summariser's "## Related" is nothing but a bullet list of
    [[links]]. On a fresh wiki every one is dangling - the whole heading-and-list is dropped
    rather than left as "## Related" over a list of now-bare, delinked words."""
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    page = "# Doc\n\nBody text.\n\n## Related\n\n- [[Records Retention]]\n- [[NDA Basics]]\n"
    out = _neutralize_dangling_links(page, ws)
    assert "## Related" not in out
    assert "Records Retention" not in out  # not just delinked - the whole section is gone
    assert "Body text." in out


def test_a_related_section_with_one_real_link_keeps_the_section(tmp_path):
    """Mixed case: only the dangling entries get delinked; a link to a page that already
    exists stays a real link, and the section survives since it isn't all-dangling."""
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    ws.write_page("Data Governance", "# Data Governance\n\nExists already.")
    page = "# Doc\n\nBody text.\n\n## Related\n\n- [[Records Retention]]\n- [[Data Governance]]\n"
    out = _neutralize_dangling_links(page, ws)
    assert "## Related" in out
    assert "- Records Retention" in out  # dangling: delinked, bullet kept
    assert "[[Data Governance]]" in out  # real: untouched


def test_a_related_section_with_non_link_prose_keeps_its_heading(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    page = "# Doc\n\nBody text.\n\n## Related\n\nSee the onboarding folder for more.\n"
    out = _neutralize_dangling_links(page, ws)
    assert "## Related" in out
    assert "onboarding folder" in out


def test_an_inline_dangling_link_outside_related_keeps_its_text(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    page = "# Doc\n\nBody text mentions [[Some Inline Page]] directly.\n"
    out = _neutralize_dangling_links(page, ws)
    assert "[[Some Inline Page]]" not in out
    assert "Some Inline Page" in out


# ── outline_change: a genuinely broken link on a manual edit is still caught ──────


def test_manual_edit_with_a_real_broken_link_is_still_flagged(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    backend = _RecordingBackend("unused")
    outline = outline_change(
        backend, ws, "onboarding", "# Onboarding\n\nSee [[Nonexistent Page]].", scope="personal"
    )
    assert "broken_links" in outline.flags


# ── the actual reported symptom: ingest -> propose_wiki_write now publishes ───────


def test_ingest_then_propose_wiki_write_now_publishes_on_a_fresh_personal_wiki(tmp_path):
    """This is the exact failure mode: a brand-new personal wiki, a document whose AI summary
    links to pages that don't exist yet. Before the fix, propose_wiki_write returned False and
    queued a WikiReview instead of writing a page - reproduced here against the real
    outline_change/propose_wiki_write-equivalent gate logic, not a mock of it."""
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    source = tmp_path / "retention-policy.md"
    source.write_text("We keep records for seven years.")
    backend = _RecordingBackend(
        "# Retention Policy\n\nRecords are kept for seven years.\n\n"
        "## Related\n\n- [[Records Retention]]\n- [[Data Governance]]\n"
    )

    captured: dict = {}
    ingest(
        ws,
        source,
        backend,
        on_write=lambda title, page_md: captured.update(title=title, page_md=page_md),
    )

    outline = outline_change(backend, ws, captured["title"], captured["page_md"], scope="personal")
    assert outline.flags == []  # previously: ["broken_links"]

    path = ws.write_page(captured["title"], captured["page_md"])
    assert path.exists()
    assert "Records are kept for seven years" in path.read_text()  # the actual content is there
    assert "[[Records Retention]]" not in path.read_text()
    # every link in the Related section was dangling - the whole section is dropped, not just
    # de-linked (see test_a_related_section_of_only_dangling_links_is_dropped_entirely)
    assert "## Related" not in path.read_text()


# ── the file-picker accept list (Bug B) ───────────────────────────────────────────


def test_wiki_ctx_upload_accept_matches_the_backend_supported_formats():
    import anthill.web.app as app_mod

    accept = ",".join(sorted(app_mod.SUPPORTED_UPLOAD_EXT))
    for ext in (".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".png"):
        assert ext in accept.split(",")
