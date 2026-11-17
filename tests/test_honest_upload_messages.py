"""What the wiki says about an upload is what happened to it. The banner names what read the document (or
says it was filed as written) instead of an "indexed by" label taken from settings, the page says it is an
AI summary and links the original under the name it was uploaded with, and the list of supported types
comes from the one set the upload route checks. Model-free: the model is a fake backend."""

import pytest
from bs4 import BeautifulSoup

from anthill.web import upload_status as ups
from tests.test_upload_status import NOTE, _login, _setup, _upload


def _uploaded(tmp_path, monkeypatch, name="bakery.md", data=NOTE):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    r = _upload(client, name, data)
    return app_mod, client, ids, r.headers["location"]


@pytest.mark.usefixtures("summarise_every_note")
def test_the_banner_names_the_model_that_read_the_document(tmp_path, monkeypatch):
    _app, client, _ids, location = _uploaded(tmp_path, monkeypatch)
    assert "saved=added" in location and "via=" not in location

    html = client.get(location).text
    banner = BeautifulSoup(html, "html.parser").select_one("#wiki-upload-result .alert-ok")
    text = " ".join(banner.get_text().split())
    assert "added to this wiki." in text
    assert "Read by qwen2.5:3b, which wrote the page from your document." in text
    assert "The original is kept under Files." in text
    assert "indexed by" not in html
    assert banner.select_one('a[href^="?tab=files"]') is not None


def test_the_banner_says_a_short_note_was_filed_as_written(tmp_path, monkeypatch):
    # No `summarise_every_note`: a short note takes the path where no model is used (#181).
    _app, client, _ids, location = _uploaded(tmp_path, monkeypatch)

    text = " ".join(
        BeautifulSoup(client.get(location).text, "html.parser")
        .select_one("#wiki-upload-result .alert-ok")
        .get_text()
        .split()
    )
    assert "Filed exactly as written: no AI rewrote it." in text
    assert "Read by" not in text


@pytest.mark.usefixtures("summarise_every_note")
def test_how_it_was_read_is_only_for_the_persons_own_upload_of_that_page(tmp_path, monkeypatch):
    app_mod, _client, ids, location = _uploaded(tmp_path, monkeypatch)
    slug = location.split("slug=")[1].split("&")[0]
    uid, org_id, _role = ids["owner"]
    mate_id = ids["mate"][0]
    db = app_mod._SessionFactory()
    try:
        where = {"org_id": org_id, "scope": "personal", "team_id": None}
        assert "Read by qwen2.5:3b" in ups.how_it_was_read(db, user_id=uid, slug=slug, **where)
        assert ups.how_it_was_read(db, user_id=uid, slug="no-such-page", **where) == ""
        assert ups.how_it_was_read(db, user_id=mate_id, slug=slug, **where) == ""
    finally:
        db.close()


def test_the_banner_is_not_shown_for_a_page_the_person_did_not_upload(tmp_path, monkeypatch):
    # The words come from the person's own record, so a hand-made link says nothing about a page.
    _app, client, _ids, _location = _uploaded(tmp_path, monkeypatch)
    html = client.get("/wiki?saved=added&slug=made-up&title=Made+up").text
    banner = BeautifulSoup(html, "html.parser").select_one("#wiki-upload-result .alert-ok")
    text = " ".join(banner.get_text().split())
    assert "Read by" not in text and "Filed exactly" not in text and "indexed by" not in html


def test_the_unsupported_message_and_the_supported_list_come_from_one_place(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    r = _upload(client, "program.exe", b"MZ")
    assert "error=unsupported" in r.headers["location"]

    html = client.get(r.headers["location"]).text
    sentence = app_mod._upload_types_text()
    for ext in (".docx", ".pptx", ".xlsx", ".html", ".md", ".txt", ".pdf", ".png", ".webp"):
        assert ext in sentence
    assert f"Unsupported file type. {sentence}" in html  # the message, and
    assert html.count(sentence) == 2  # the standing line under "Add a document"


@pytest.mark.usefixtures("summarise_every_note")
def test_the_page_says_it_is_an_ai_summary_and_links_the_original(tmp_path, monkeypatch):
    _app, client, _ids, location = _uploaded(tmp_path, monkeypatch, "my notes.md")
    slug = location.split("slug=")[1].split("&")[0]

    r = client.get(f"/wiki/page/{slug}")
    meta = BeautifulSoup(r.text, "html.parser").select_one("p.text-muted")
    text = " ".join(meta.get_text().split())
    assert text.startswith("AI summary written by qwen2.5:3b on ")
    assert "from my notes.md." in text  # the name it was uploaded with, not the hashed one
    assert "It is not the text of your document; the original is kept under Files." in text
    link = meta.select_one("a")
    assert link is not None and link["href"].startswith("/wiki/raw/my%20notes-")
    assert "-" in link["href"] and link.get_text() == "my notes.md"

    original = client.get(link["href"])
    assert original.status_code == 200 and original.content == NOTE  # the link opens the real file


@pytest.mark.parametrize(
    ("kept_as", "uploaded_as"),
    [
        ("report-1a2b3c4d5e6f.pdf", "report.pdf"),
        ("my notes-1a2b3c4d5e6f.md", "my notes.md"),
        ("archive.tar-1a2b3c4d5e6f.gz", "archive.tar.gz"),
        ("noext-1a2b3c4d5e6f", "noext"),
        ("plain.md", "plain.md"),  # never kept under a hash: left as it is
        ("not-a-hash-zzzzzzzzzzzz.md", "not-a-hash-zzzzzzzzzzzz.md"),
        ("", ""),
    ],
)
def test_the_original_name_is_the_one_uploaded(kept_as, uploaded_as):
    from anthill.web.app import _original_name

    assert _original_name(kept_as) == uploaded_as
