"""`anthill chat` - the interactive, multi-turn, wiki-grounded terminal REPL.

The CLI version of the chat box: a normal turn streams via `ask_mod.ask_stream` (the local path),
accumulating conversation history across turns; slash-commands are handled without a model. These tests
drive the REPL through CliRunner (piped stdin) with the stream mocked, so they assert the REPL's
behavior - history accumulation, streaming to stdout, slash-commands, /save - without needing a model.
`file_as_page` is exercised for real against a temp workspace.
"""

from typer.testing import CliRunner

import anthill.cli as cli
from anthill.wiki import ask as ask_mod
from anthill.wiki.workspace import Workspace


def _wiki(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    return tmp_path / "w"


def _fake_stream(record=None, answer_for=lambda q: f"answer to {q}"):
    """A stand-in for ask_mod.ask_stream: records the call, fires on_context, yields the answer."""

    def _stream(ws, q, backend, **kw):
        if record is not None:
            record.append({"q": q, "history": list(kw.get("history") or [])})
        on_ctx = kw.get("on_context")
        if on_ctx:
            on_ctx(["pagea"])
        yield answer_for(q)

    return _stream


def test_chat_accumulates_history_across_turns(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli.ask_mod, "ask_stream", _fake_stream(record=calls))
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())

    # No /exit needed - EOF on the piped input ends the loop.
    r = CliRunner().invoke(
        cli.app, ["-w", str(_wiki(tmp_path)), "chat", "--no-smart"], input="first\nsecond\n"
    )
    assert r.exit_code == 0, r.output
    assert len(calls) == 2
    assert calls[0]["history"] == []  # first turn: no prior context
    # second turn carries the first exchange as (role, content) pairs
    assert calls[1]["history"] == [("user", "first"), ("assistant", "answer to first")]


def test_chat_streams_the_answer_to_stdout(tmp_path, monkeypatch):
    def _stream(ws, q, backend, **kw):
        if kw.get("on_context"):
            kw["on_context"]([])
        yield from ["Hel", "lo!"]

    monkeypatch.setattr(cli.ask_mod, "ask_stream", _stream)
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())

    r = CliRunner().invoke(
        cli.app, ["-w", str(_wiki(tmp_path)), "chat", "--no-smart"], input="hi\n"
    )
    assert r.exit_code == 0, r.output
    assert "Hello!" in r.output  # the streamed chunks are written to stdout as they arrive


def test_chat_slash_commands_do_not_call_the_model(tmp_path, monkeypatch):
    calls = []

    def _stream(*a, **k):
        calls.append(1)
        yield "x"

    monkeypatch.setattr(cli.ask_mod, "ask_stream", _stream)
    monkeypatch.setattr(cli.ask_mod, "ask", lambda *a, **k: (calls.append(1), ("x", [], False))[1])
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())

    r = CliRunner().invoke(
        cli.app,
        ["-w", str(_wiki(tmp_path)), "chat", "--no-smart"],
        input="/web\n/reset\n/bogus\n/exit\n",
    )
    assert r.exit_code == 0, r.output
    assert calls == []  # pure slash-commands never reach the model


def test_chat_save_files_the_last_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.ask_mod, "ask_stream", _fake_stream(answer_for=lambda q: f"A:{q}"))
    saved = {}
    monkeypatch.setattr(
        cli.ask_mod,
        "file_as_page",
        lambda ws, q, a, b, **k: (saved.update(q=q, a=a), "Some Title")[1],
    )
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())

    r = CliRunner().invoke(
        cli.app, ["-w", str(_wiki(tmp_path)), "chat", "--no-smart"], input="first\n/save\n/exit\n"
    )
    assert r.exit_code == 0, r.output
    assert saved == {"q": "first", "a": "A:first"}  # /save files the prior turn's Q/A


def test_chat_save_with_nothing_yet_is_a_noop(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli.ask_mod, "file_as_page", lambda *a, **k: called.append(1))
    monkeypatch.setattr(cli, "build_backend", lambda cfg: object())

    r = CliRunner().invoke(
        cli.app, ["-w", str(_wiki(tmp_path)), "chat", "--no-smart"], input="/save\n/exit\n"
    )
    assert r.exit_code == 0, r.output
    assert called == []  # nothing answered yet -> nothing to file


def test_file_as_page_writes_a_wiki_page(tmp_path):
    ws = Workspace(tmp_path / "w2")
    ws.init()

    class _Backend:
        def chat(self, messages, **k):
            return "# Picked Postgres\n\nWe chose Postgres for its JSONB support."

    title = ask_mod.file_as_page(ws, "what db did we pick?", "We chose Postgres.", _Backend())
    assert title == "Picked Postgres"
    slugs = [p.stem for p in ws.pages()]
    assert any("postgres" in s for s in slugs)  # the page landed on disk
