"""The canary erasure test (build plan step 3.2). A document with a phrase nothing else contains is ingested and asked about; it is
removed; the same question is asked again on each route and the phrase is looked for in what the model is given, in the answers, in
the tools' results, and in every file of the wiki folder outside the removed-items folder; then the document is restored and the
phrase comes back. Model-free: the model and the embedder are fakes.

The routes asked are: the blocking answer (chat, widget, Slack), the streaming answer, the MCP wiki tool, the agent tools (agent
mode and tasks), and the command-line document command. A route not named here is not claimed."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import anthill.wiki.ask as ask_mod
from anthill.agent.tools import _read_wiki, _search_wiki
from anthill.wiki import doc_index, ingest, items, removal
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import _Backend, embeds  # noqa: F401

PHRASE = "The zinnober kestrel nests beneath the harbour crane."
QUESTION = "Where does the zinnober kestrel nest?"
OTHER_PHRASE = "The quokka sanctuary opens at dawn."
OTHER_QUESTION = "When does the quokka sanctuary open at dawn?"
SUMMARY = f"# Kestrel notes\n\nA short report. {PHRASE}\n"
OTHER = f"# Quokka notes\n\nA short report. {OTHER_PHRASE}\n"

pytestmark = pytest.mark.usefixtures("embeds", "summarise_every_note")


class _Summariser:
    """Writes the page for whatever document it is shown: its first line is the page, as a small model would."""

    model = "fake"

    def chat(self, messages, **k):
        text = "\n".join(m.content for m in messages)
        if "review a proposed change" in text:
            return '{"summary": "ok", "flags": []}'
        return SUMMARY if "zinnober" in text else OTHER


def _wiki(tmp_path: Path) -> tuple[Workspace, str, str]:
    ws = Workspace(tmp_path / "wiki-root", scope="personal")
    ws.init()
    names = []
    for stem, text in (("kestrel-report", PHRASE), ("quokka-report", OTHER_PHRASE)):
        src = tmp_path / f"{stem}.txt"
        src.write_text(f"A short report.\n\n{text}\n", encoding="utf-8")
        ingest.ingest(ws, src, _Summariser())
        names.append(next(p.name for p in ws.raw.iterdir() if p.name.startswith(stem)))
    doc_index.sync_workspace(ws.root)
    return ws, names[0], names[1]


def _reword(ws: Workspace, slug: str, old: str, new: str, *, by_person: bool = False) -> None:
    """Save the page again with some words changed, as a model (or, with ``by_person``, a person) would, keeping its source."""
    from anthill.common.text import strip_frontmatter

    text = strip_frontmatter((ws.wiki / f"{slug}.md").read_text())
    ws.write_page(slug, text.replace(old, new), by_person=by_person)


def _page_with(ws: Workspace, phrase: str) -> str:
    return next(p.stem for p in ws.pages() if phrase in p.read_text())


def _routes(ws: Workspace, question: str, monkeypatch, tmp_path) -> dict[str, str]:
    """Everything each route gives the model or hands back for one question, as text."""
    seen: dict[str, str] = {}

    backend = _Backend()
    answer = ask_mod.ask(ws, question, backend)[0]
    seen["blocking answer (chat, widget, Slack)"] = "\n".join([*backend.prompts, answer])

    backend = _Backend()
    streamed = "".join(c for c in ask_mod.ask_stream(ws, question, backend) if isinstance(c, str))
    seen["streaming answer"] = "\n".join([*backend.prompts, streamed])

    from anthill.web import mcp_store

    backend = _Backend()
    monkeypatch.setattr("anthill.inference.base.build_backend", lambda cfg: backend)
    monkeypatch.setattr("anthill.wiki.workspace.workspace_for", lambda *a, **k: ws)
    seen["MCP wiki tool"] = "\n".join(
        [mcp_store._answer(_NoCfgDb(), 1, "wiki", question), *backend.prompts]
    )

    hits = _search_wiki(question, str(ws.root), k=10)
    pages = [_read_wiki(p.stem, str(ws.root)) for p in ws.pages()]
    seen["agent tools (agent mode, tasks)"] = json.dumps([hits, pages])

    from anthill import cli

    prompts: list[str] = []

    class _Fake:
        def chat(self, messages, **k):
            prompts.append("\n".join(m.content for m in messages))
            return "# Summary\n\ndone\n"

    monkeypatch.setattr(cli, "build_backend", lambda cfg: _Fake())
    out = tmp_path / "out.md"
    res = CliRunner().invoke(cli.app, ["-w", str(ws.root), "generate", "summarise", "-o", str(out)])
    assert res.exit_code == 0
    seen["command-line document command"] = "\n".join([*prompts, out.read_text()])
    return seen


class _NoCfgDb:
    """The part of a database session the MCP wiki tool reads: no settings row."""

    def query(self, *_a):
        return self

    def filter(self, *_a):
        return self

    def first(self):
        return None


def _files_holding(ws: Workspace, phrase: str) -> list[str]:
    """Every file of the wiki folder outside the removed-items folder that contains the phrase, indexes and logs included."""
    needle = phrase.encode()
    found = []
    for p in sorted(ws.root.rglob("*")):
        if not p.is_file() or removal.TRASH_DIR in p.relative_to(ws.root).parts:
            continue
        try:
            if needle in p.read_bytes():
                found.append(str(p.relative_to(ws.root)))
        except OSError:
            continue
    return found


def test_before_removal_the_phrase_is_found_on_every_route_asked(tmp_path, monkeypatch):
    ws, _doc, _other = _wiki(tmp_path)
    for route, text in _routes(ws, QUESTION, monkeypatch, tmp_path).items():
        assert PHRASE in text, route
    assert _files_holding(ws, PHRASE)  # and in the files, so the check below can see it


def test_after_removal_the_phrase_appears_nowhere_and_restore_brings_it_back(tmp_path, monkeypatch):
    ws, doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    for route, text in _routes(
        ws, QUESTION, monkeypatch, tmp_path
    ).items():  # asked first, so any copy is made
        assert PHRASE in text, route

    rec = removal.remove_original(ws, doc)
    assert page in rec.pages  # the page the model made goes with the document

    for route, text in _routes(ws, QUESTION, monkeypatch, tmp_path).items():
        assert PHRASE not in text, route
    assert _files_holding(ws, PHRASE) == [] and _files_holding(ws, "zinnober") == []

    for route, text in _routes(ws, OTHER_QUESTION, monkeypatch, tmp_path).items():
        assert OTHER_PHRASE in text, route  # the other document is untouched

    removal.restore(ws, rec.id)
    assert _files_holding(ws, PHRASE)
    for route, text in _routes(ws, QUESTION, monkeypatch, tmp_path).items():
        assert PHRASE in text, route


def test_the_removed_page_leaves_the_vector_index_when_it_is_removed(tmp_path):
    from anthill.cache import page_vectors

    ws, doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    index = page_vectors.PageVectorIndex(ws.root / ".cache")
    rel = f"wiki/{page}.md"
    texts = [(f"wiki/{p.name}", p.read_text()) for p in ws.pages()]
    index.vectors(texts, lambda t: doc_index_embed(t), "m")
    assert rel in _vector_keys(ws)
    removal.remove_original(ws, doc)
    assert rel not in _vector_keys(ws)  # not only after the 30 days: at once


def doc_index_embed(text: str):
    from anthill.cache import embedder as emb

    return emb.embed(text)


def _vector_keys(ws: Workspace) -> set[str]:
    import sqlite3

    from anthill.cache import page_vectors

    path = ws.root / ".cache" / page_vectors.DB_NAME
    if not path.is_file():
        return set()
    with sqlite3.connect(path) as con:
        return {r[0] for r in con.execute(f"SELECT path FROM {page_vectors.TABLE}")}  # nosec B608


def test_a_page_a_person_approved_stays_and_says_its_source_was_removed(tmp_path):
    ws, doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    _reword(ws, page, "short report", "short, checked report", by_person=True)
    plan = removal.plan_original(ws, doc)
    assert plan.stays == [page] and plan.goes == []
    removal.remove_original(ws, doc)
    assert (ws.wiki / f"{page}.md").is_file()
    assert items.source_removed(ws, (ws.wiki / f"{page}.md").read_text()) is True
    assert items.is_retrievable(items.read_page_record((ws.wiki / f"{page}.md").read_text()))


def test_a_page_whose_source_is_there_is_not_tagged(tmp_path):
    ws, _doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    assert items.source_removed(ws, (ws.wiki / f"{page}.md").read_text()) is False
    ws.write_page("a-note", "# A note\n\nWritten by me.\n", by_person=True)
    assert (
        items.source_removed(ws, (ws.wiki / "a-note.md").read_text()) is False
    )  # it never had a source


def test_restoring_the_document_clears_the_tag(tmp_path):
    ws, doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    _reword(ws, page, "short report", "short, checked report", by_person=True)
    rec = removal.remove_original(ws, doc)
    assert items.source_removed(ws, (ws.wiki / f"{page}.md").read_text()) is True
    removal.restore(ws, rec.id)
    assert items.source_removed(ws, (ws.wiki / f"{page}.md").read_text()) is False


def test_the_earlier_versions_of_a_removed_page_go_with_it_and_come_back(tmp_path):
    ws, doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    _reword(ws, page, "nests", "roosts")  # the first text is now history
    assert _files_holding(ws, "nests beneath")  # in history/
    rec = removal.remove_original(ws, doc)
    assert _files_holding(ws, "nests beneath") == [] and _files_holding(ws, "roosts") == []
    removal.restore(ws, rec.id)
    assert _files_holding(ws, "nests beneath")


def test_a_page_removed_on_its_own_takes_its_earlier_versions_too(tmp_path):
    ws, _doc, _other = _wiki(tmp_path)
    page = _page_with(ws, PHRASE)
    _reword(ws, page, "nests", "roosts")
    rec = removal.remove_page(ws, page)
    assert not (ws.root / "history" / page).exists()
    removal.restore(ws, rec.id)
    assert (ws.root / "history" / page).is_dir()


class _Echo:
    """Answers with the whole prompt, so an answer holds whatever the model was given and a cached answer would carry it."""

    model = "fake"

    def chat(self, messages, **k):
        return "\n".join(m.content for m in messages)

    def chat_stream(self, messages, **k):
        yield self.chat(messages)


def test_with_the_answer_cache_on_a_cached_answer_is_not_served_after_removal(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ANTHILL_DISABLE_SEMANTIC_CACHE", raising=False)
    ws, doc, _other = _wiki(tmp_path)
    first = ask_mod.ask(ws, QUESTION, _Echo())[0]
    assert PHRASE in first
    assert ask_mod.ask(ws, QUESTION, _Backend())[0] == first  # the answer was saved and is served
    removal.remove_original(ws, doc)
    again = ask_mod.ask(ws, QUESTION, _Echo())[0]
    streamed = "".join(c for c in ask_mod.ask_stream(ws, QUESTION, _Echo()) if isinstance(c, str))
    assert PHRASE not in again and PHRASE not in streamed
    assert _files_holding(ws, PHRASE) == []


# ── the tag, in the Library and on the page ───────────────────────────────────


def _approved_page_whose_document_was_removed(ws: Workspace) -> tuple[str, removal.Removed]:
    doc = next(p.name for p in ws.raw.iterdir() if p.name.startswith("kestrel-report"))
    page = _page_with(ws, PHRASE)
    _reword(ws, page, "short report", "short, checked report", by_person=True)
    return page, removal.remove_original(ws, doc)


def test_the_library_tags_a_page_whose_document_was_removed_and_not_one_whose_document_is_there(
    tmp_path,
):
    from anthill.web import library

    ws, _doc, _other = _wiki(tmp_path)
    kept = lambda: [  # noqa: E731
        {"name": p.name, "size": 1, "mtime": 0, "has_text": True} for p in sorted(ws.raw.iterdir())
    ]
    rows = {r["title"]: r for r in library.build(ws, kept(), scope="personal")}
    assert all("Source removed" not in r["chips"] for r in rows.values())
    _page, rec = _approved_page_whose_document_was_removed(ws)
    rows = {r["title"]: r for r in library.build(ws, kept(), scope="personal")}
    assert rows["Kestrel notes"]["chips"].count("Source removed") == 1
    assert "Source removed" not in rows["Quokka notes"]["chips"]
    removal.restore(ws, rec.id)
    rows = {r["title"]: r for r in library.build(ws, kept(), scope="personal")}
    assert "Source removed" not in rows["Kestrel notes"]["chips"]


def test_the_page_view_says_the_source_was_removed_until_it_is_restored(tmp_path, monkeypatch):
    from tests.test_remove_an_item import A, _personal, _soup
    from tests.test_upload_status import _login, _setup

    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    assert _soup(client, "/wiki/page/ferry-checked").find(id="source-removed") is None
    rec = removal.remove_original(ws, A)  # "ferry-checked" was approved by a person, so it stays
    page = _soup(client, "/wiki/page/ferry-checked")
    assert page.find(id="source-removed") is not None
    text = " ".join(page.find(id="source-removed").get_text().split())
    assert (
        "The document this page was made from (" in text
        and ") was removed. This page stays because" in text
    )
    assert page.find(id="source-removed").find("a")["href"].endswith("#wiki-removed")
    assert _soup(client, "/wiki/page/notes").find(id="source-removed") is None  # no source, no tag
    removal.restore(ws, rec.id)
    assert _soup(client, "/wiki/page/ferry-checked").find(id="source-removed") is None


def test_a_page_that_names_an_original_that_was_never_kept_is_not_tagged(tmp_path):
    from anthill.lifecycle.version import tag_page

    ws, _doc, _other = _wiki(tmp_path)
    ws.write_page(
        "old-page",
        tag_page("# Old page\n\nFrom before originals were kept.\n", "m", source="never-kept.pdf"),
    )
    assert items.source_removed(ws, (ws.wiki / "old-page.md").read_text()) is False
