"""An answer's list of sources carries the documents it was given as `doc:<stored name> (page n)` entries next to
the page slugs. Every consumer of that list separates them: the terminal shows a document under the name the
person uploaded, and the OpenAI-compatible endpoint keeps `wiki_slugs` to pages and gives documents their own
list. No `doc:` prefix and no hashed file name reaches a person or an API client."""

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import anthill.cli as cli
import anthill.node_agent.app as node_app
from anthill.wiki.workspace import Workspace

SOURCES = ["flurf-vs-brandt", "doc:hollis-bakery-handbook-1a2b3c4d5e6f.pdf (page 1, 2)"]


def _wiki(tmp_path) -> str:
    Workspace(tmp_path / "w").init()
    return str(tmp_path / "w")


def _assert_clean(output: str) -> None:
    assert "doc:" not in output and "1a2b3c4d5e6f" not in output


def test_the_terminal_ask_shows_a_document_under_the_name_it_was_uploaded_with(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())
    monkeypatch.setattr(cli.ask_mod, "ask", lambda *a, **k: ("The Brandt mill.", SOURCES, False))
    r = CliRunner().invoke(cli.app, ["-w", _wiki(tmp_path), "ask", "which mill?", "--no-smart"])
    assert r.exit_code == 0, r.output
    assert "context: flurf-vs-brandt; documents: hollis-bakery-handbook.pdf (page 1, 2)" in r.output
    _assert_clean(r.output)


def test_the_terminal_chat_and_its_context_command_do_the_same(tmp_path, monkeypatch):
    def _stream(ws, q, backend, **kw):
        kw["on_context"](SOURCES)
        yield "The Brandt mill."

    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())
    monkeypatch.setattr(cli.ask_mod, "ask_stream", _stream)
    r = CliRunner().invoke(
        cli.app, ["-w", _wiki(tmp_path), "chat", "--no-smart"], input="which mill?\n/context\n"
    )
    assert r.exit_code == 0, r.output
    assert "context: flurf-vs-brandt; documents: hollis-bakery-handbook.pdf (page 1, 2)" in r.output
    assert (
        "last context: flurf-vs-brandt; documents: hollis-bakery-handbook.pdf (page 1, 2)"
        in r.output
    )
    _assert_clean(r.output)


def test_a_document_alone_is_shown_without_a_leading_separator(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())
    monkeypatch.setattr(cli.ask_mod, "ask", lambda *a, **k: ("8 percent.", [SOURCES[1]], False))
    r = CliRunner().invoke(cli.app, ["-w", _wiki(tmp_path), "ask", "price?", "--no-smart"])
    assert "context: documents: hollis-bakery-handbook.pdf (page 1, 2)" in r.output
    _assert_clean(r.output)


def test_the_openai_compatible_endpoint_keeps_wiki_slugs_to_pages_and_lists_documents_apart(
    monkeypatch,
):
    monkeypatch.setattr(node_app, "build_backend", lambda cfg: object())
    monkeypatch.setattr(node_app, "_workspace", lambda: None)
    monkeypatch.setattr(node_app, "ask", lambda *a, **k: ("The Brandt mill.", SOURCES, False))
    r = TestClient(node_app.app).post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "which mill?"}]}
    )
    assert r.status_code == 200
    meta = r.json()["anthill"]
    assert meta["wiki_slugs"] == ["flurf-vs-brandt"]  # what the field has always held: wiki pages
    assert meta["documents"] == ["hollis-bakery-handbook.pdf (page 1, 2)"]
    _assert_clean(str(meta))
