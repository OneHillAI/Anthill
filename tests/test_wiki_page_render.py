"""A wiki page is shown as a rendered document: the provenance comment never reaches the body, the body goes
through the same sanitised Markdown renderer as task results, and `[[links]]` point at pages that exist."""

from bs4 import BeautifulSoup
from test_wiki_ui import _client

from anthill.lifecycle.version import tag_page

NOTE = '# Interview: courier (P11), 19 Sep 2026\n\n- Quote: "A <b>straight</b> line"\n\nSee [[other-note]] and [[missing-note]].\n'


def _write(ws, slug, md):
    ws.wiki.mkdir(parents=True, exist_ok=True)
    (ws.wiki / f"{slug}.md").write_text(md)


def _page(tmp_path, monkeypatch, **kw):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, _org, uid = _client(tmp_path, monkeypatch, role="member")
    ws = workspace_for("personal", user_id=uid)
    _write(ws, "interview", tag_page(NOTE, "qwen2.5:3b", on="2026-10-21", source="note one.md"))
    _write(ws, "other-note", "# Other note\n")
    return c, app_mod, uid


def test_wiki_page_hides_the_provenance_comment_and_shows_a_metadata_line(tmp_path, monkeypatch):
    c, app_mod, _uid = _page(tmp_path, monkeypatch)
    try:
        r = c.get("/wiki/page/interview")
        assert r.status_code == 200
        assert "<!-- anthill" not in r.text and "anthill: model" not in r.text
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.select_one("p.text-muted")
        assert meta is not None
        assert (
            meta.get_text(" ", strip=True)
            == "Written by qwen2.5:3b on 2026-10-21 from note one.md."
        )
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_page_body_is_escaped_text_for_the_sanitised_renderer(tmp_path, monkeypatch):
    c, app_mod, _uid = _page(tmp_path, monkeypatch)
    try:
        soup = BeautifulSoup(c.get("/wiki/page/interview").text, "html.parser")
        assert soup.select_one("pre.wiki-content") is None
        body = soup.select_one("div[data-md].task-md-raw")
        assert body is not None
        assert body.find("b") is None  # the note's own tag stays text, so the no-JS view is escaped
        assert "A <b>straight</b> line" in body.get_text()
        assert body.get_text().startswith("# Interview: courier (P11), 19 Sep 2026")
        scripts = [s.get("src") for s in soup.find_all("script") if s.get("src")]
        assert "/static/marked.min.js" in scripts and "/static/purify.min.js" in scripts
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_page_links_only_to_pages_that_exist(tmp_path, monkeypatch):
    c, app_mod, _uid = _page(tmp_path, monkeypatch)
    try:
        text = (
            BeautifulSoup(c.get("/wiki/page/interview").text, "html.parser")
            .select_one("[data-md]")
            .get_text()
        )
        assert "[other-note](/wiki/page/other-note?scope=personal)" in text
        assert (
            "[[" not in text and "missing-note" in text and "(/wiki/page/missing-note" not in text
        )
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_page_link_target_is_a_slug_never_the_raw_text(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid = _page(tmp_path, monkeypatch)
    try:
        _write(
            workspace_for("personal", user_id=uid),
            "evil",
            "# Evil\n\n[[//evil.example/x]] [[Other Note]]\n",
        )
        text = (
            BeautifulSoup(c.get("/wiki/page/evil").text, "html.parser")
            .select_one("[data-md]")
            .get_text()
        )
        assert "(//evil" not in text and "(/wiki/page/other-note?scope=personal)" in text
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_wiki_index_caption_reads_as_a_sentence(tmp_path, monkeypatch):
    c, app_mod, *_ = _client(tmp_path, monkeypatch, role="member")
    try:
        text = (
            BeautifulSoup(c.get("/wiki").text, "html.parser")
            .select_one("#wiki-pages-card span.text-muted")
            .get_text()
        )
        assert text.startswith("The wiki documents in this scope. Take a tour")
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_a_missing_link_is_shown_as_literal_text_not_markdown(tmp_path, monkeypatch):
    from anthill.wiki.workspace import workspace_for

    c, app_mod, uid = _page(tmp_path, monkeypatch)
    try:
        _write(workspace_for("personal", user_id=uid), "odd", "# Odd\n\n[[**loud** _x_ <i>]]\n")
        raw = BeautifulSoup(c.get("/wiki/page/odd").text, "html.parser").select_one("[data-md]")
        assert raw is not None
        assert "\\*\\*loud\\*\\* \\_x\\_ \\<i\\>" in raw.get_text()
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
