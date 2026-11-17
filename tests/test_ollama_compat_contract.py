"""What Anthill reads from the Ollama engine, checked against replies from each version we know about (#227).

Each directory under tests/fixtures/ollama/<version>/ holds one reply per endpoint and a provenance.json that
labels every file `recorded` (a real reply, with the date and machine) or `docs` (written by hand from the
documented examples and source). A `docs` fixture shows the shape the documentation promises; only a `recorded`
one shows what an engine did. To record a new version run tests/fixtures/ollama/record_fixtures.py against it.

Model-free: nothing here calls an engine. The suite runs Anthill's own parsers over every fixture it finds, so
adding a directory adds a column to every test.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import httpx
import pytest

from anthill.cache import embedder
from anthill.inference import ollama as ollama_mod
from anthill.inference.base import Message
from anthill.inference.ollama import OllamaBackend
from anthill.web.app import _parse_ollama_pull_line

ROOT = Path(__file__).parent / "fixtures" / "ollama"
VERSIONS = sorted(p.name for p in ROOT.iterdir() if (p / "provenance.json").is_file())

# conftest stubs these for every test (no live model); keep the real ones to restore here.
_REAL = {
    name: getattr(OllamaBackend, name)
    for name in ("chat", "chat_with_tools", "installed_models", "chat_with_confidence")
}


@pytest.fixture(autouse=True)
def _real_ollama_methods(monkeypatch):
    for name, fn in _REAL.items():
        monkeypatch.setattr(OllamaBackend, name, fn)
    ollama_mod._CTX_WINDOW_CACHE.clear()
    ollama_mod._CAPABILITIES_CACHE.clear()


def provenance(version: str) -> dict:
    return json.loads((ROOT / version / "provenance.json").read_text())


def fixture(version: str, name: str):
    """A fixture file, parsed. Skips the test when this version has no such file."""
    path = ROOT / version / name
    if not path.is_file():
        pytest.skip(f"{version} has no {name}")
    if name.endswith(".ndjson"):
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return json.loads(path.read_text())


def label(version: str, name: str) -> str:
    entry = provenance(version)["files"][name]
    return entry if isinstance(entry, str) else entry["label"]


def _response(payload, status=200, url="http://x/api"):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", url))


def _backend() -> OllamaBackend:
    return OllamaBackend("http://x", "m", num_ctx=4096)


MSGS = [Message("user", "hi")]


class _Stream:
    def __init__(self, lines):
        self._lines, self.status_code, self.is_success = lines, 200, True

    def iter_lines(self):
        yield from self._lines


# -- provenance --------------------------------------------------------------------------------------


def test_there_is_a_fixture_set_for_the_pinned_version_and_a_newer_one():
    from anthill.inference.ollama import _OLLAMA_VERSION

    assert _OLLAMA_VERSION in VERSIONS  # the engine we ship has a column
    assert any(v != _OLLAMA_VERSION for v in VERSIONS)  # and so does one we might move to


@pytest.mark.parametrize("version", VERSIONS)
def test_every_fixture_is_labelled_and_nothing_is_unlabelled(version, capsys):
    prov = provenance(version)
    on_disk = {p.name for p in (ROOT / version).iterdir() if p.name != "provenance.json"}
    assert on_disk == set(prov["files"]), (
        "a fixture file has no provenance entry, or an entry has no file"
    )
    with capsys.disabled():
        for name, entry in sorted(prov["files"].items()):
            kind = entry if isinstance(entry, str) else entry["label"]
            where = prov.get("machine", "") if kind == "recorded" else entry["source"]
            print(f"\n  ollama {version:8} {name:20} {kind:9} {where}", end="")
    for name in on_disk:
        assert label(version, name) in ("recorded", "docs")
    if any(label(version, n) == "recorded" for n in on_disk):
        assert prov.get("recorded_on") and prov.get("machine")
    if any(label(version, n) == "docs" for n in on_disk):
        assert all(isinstance(e, str) or e.get("source") for e in prov["files"].values()), (
            "a docs fixture must say what it was written from"
        )


def test_each_endpoint_has_a_recorded_fixture_in_at_least_one_version():
    # A `docs` fixture is never the only evidence for an endpoint.
    for name in (
        "chat.json",
        "chat_stream.ndjson",
        "chat_tools.json",
        "chat_vision.json",
        "embed.json",
        "show_chat.json",
        "ps.json",
        "tags.json",
    ):
        assert any(
            name in provenance(v)["files"] and label(v, name) == "recorded" for v in VERSIONS
        ), f"{name} was never recorded from a real engine"


# -- chat: streamed and not --------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_chat_reply_is_read(monkeypatch, version):
    payload = fixture(version, "chat.json")
    monkeypatch.setattr(
        httpx, "post", lambda url, json=None, timeout=None, **kw: _response(payload)
    )
    be = _backend()
    assert be.chat(MSGS) == payload["message"]["content"]
    turn = be.last_turn
    assert turn is not None
    assert turn.prompt_tokens == payload.get("prompt_eval_count")
    assert turn.decode_tokens == payload.get("eval_count")
    assert turn.load_ms is not None  # load_duration is documented and recorded at every version


@pytest.mark.parametrize("version", VERSIONS)
def test_the_cached_token_count_is_what_the_engine_reported_or_none(monkeypatch, version):
    payload = fixture(version, "chat.json")
    monkeypatch.setattr(
        httpx, "post", lambda url, json=None, timeout=None, **kw: _response(payload)
    )
    be = _backend()
    be.chat(MSGS)
    assert be.last_turn.cached_tokens == payload.get("prompt_eval_cached_count")
    # api/types.go has no such field at v0.30.10 (it arrives in v0.33.3); a recorded 0.40.x reply has it.
    if version == "0.30.10":
        assert "prompt_eval_cached_count" not in payload and be.last_turn.cached_tokens is None
    if label(version, "chat.json") == "recorded" and version.startswith("0.40"):
        assert be.last_turn.cached_tokens is not None


@pytest.mark.parametrize("version", VERSIONS)
def test_streamed_chat_is_read(monkeypatch, version):
    chunks = fixture(version, "chat_stream.ndjson")
    lines = [json.dumps(c) for c in chunks]

    @contextlib.contextmanager
    def fake_stream(method, url, **kw):
        yield _Stream(lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    be = _backend()
    text = "".join(be.chat_stream(MSGS))
    assert text == "".join(c.get("message", {}).get("content", "") for c in chunks)
    assert text.strip()
    assert chunks[-1]["done"] is True
    assert be.last_turn is not None and be.last_turn.first_token_ms is not None
    assert be.last_turn.decode_tokens == chunks[-1].get("eval_count")


@pytest.mark.parametrize("version", VERSIONS)
def test_a_tool_call_comes_back_in_the_shape_the_agent_reads(monkeypatch, version):
    payload = fixture(version, "chat_tools.json")
    monkeypatch.setattr(
        httpx, "post", lambda url, json=None, timeout=None, **kw: _response(payload)
    )
    reply = _backend().chat_with_tools(MSGS, tools=[])
    calls = reply["message"]["tool_calls"]
    assert calls and isinstance(calls[0]["function"]["name"], str)
    assert isinstance(calls[0]["function"]["arguments"], dict)


@pytest.mark.parametrize("version", VERSIONS)
def test_an_image_is_sent_and_the_reply_is_read(monkeypatch, version):
    payload = fixture(version, "chat_vision.json")
    sent = {}

    def post(url, json=None, timeout=None, **kw):
        sent.update(json)
        return _response(payload)

    monkeypatch.setattr(httpx, "post", post)
    text = _backend().chat([Message("user", "what is this?", images=["QUJD"])])
    assert text.strip() == payload["message"]["content"].strip()
    assert sent["messages"][0]["images"] == ["QUJD"]


# -- embeddings --------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_an_embedding_is_read_and_normalised(monkeypatch, version):
    payload = fixture(version, "embed.json")
    monkeypatch.setattr(
        httpx, "post", lambda url, json=None, timeout=None, **kw: _response(payload)
    )
    vec = embedder.embed("hello")
    assert len(vec) == len(payload["embeddings"][0])
    assert abs(float((vec**2).sum()) - 1.0) < 1e-5


# -- show, ps, tags, delete --------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("name", ["show_chat.json", "show_vision.json"])
def test_show_gives_the_window_the_capabilities_and_the_attention_shape(monkeypatch, version, name):
    show = fixture(version, name)
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None, **kw: _response(show))
    be = _backend()
    info = show["model_info"]
    trained = next(v for k, v in info.items() if k.endswith(".context_length"))
    assert be.trained_context_window() == trained
    assert be._capabilities(None) == show["capabilities"]
    assert be.can_use_tools() == ("tools" in show["capabilities"] or not show["capabilities"])
    # The attention shape a memory estimate needs (layers, heads, embedding) is present as plain numbers.
    arch = info["general.architecture"]
    for key in ("block_count", "attention.head_count", "embedding_length"):
        assert isinstance(info[f"{arch}.{key}"], int) and info[f"{arch}.{key}"] > 0


@pytest.mark.parametrize("version", VERSIONS)
def test_a_vision_model_says_so_in_its_capabilities(version):
    show = fixture(version, "show_vision.json" if version == "0.40.7" else "show_chat.json")
    assert "vision" in show["capabilities"]


@pytest.mark.parametrize("version", VERSIONS)
def test_a_model_that_is_not_installed_is_not_an_exception(monkeypatch, version):
    missing = fixture(version, "show_missing.json")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json=None, timeout=None, **kw: _response(
            missing["body"], status=missing["status"]
        ),
    )
    be = _backend()
    assert be.trained_context_window("no-such-model:1b") == 0
    assert be._capabilities("no-such-model:1b") is None
    assert be.can_use_tools("no-such-model:1b") is True  # unreadable keeps the old behaviour


@pytest.mark.parametrize("version", VERSIONS)
def test_running_models_are_listed_with_their_allocation(monkeypatch, version):
    ps = fixture(version, "ps.json")
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, **kw: _response(ps))
    be = _backend()
    assert be.resident_models() == {m["name"] for m in ps["models"]}
    for entry in ps["models"]:
        # What a memory report reads: the allocation, the part in GPU memory, and (when the version has it)
        # the window. A version without context_length is not an error.
        assert isinstance(entry["size"], int) and entry["size"] > 0
        assert isinstance(entry["size_vram"], int) and 0 <= entry["size_vram"] <= entry["size"]
        assert "context_length" not in entry or isinstance(entry["context_length"], int)


@pytest.mark.parametrize("version", VERSIONS)
def test_installed_models_are_listed_with_their_sizes(monkeypatch, version):
    tags = fixture(version, "tags.json")
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, **kw: _response(tags))
    listed = _backend().installed_models_with_sizes()
    assert listed == [{"name": m["name"], "size_bytes": m["size"]} for m in tags["models"]]
    assert all(m["size_bytes"] > 0 for m in listed)


@pytest.mark.parametrize("version", VERSIONS)
def test_delete_is_true_on_success_and_false_when_the_model_is_not_there(monkeypatch, version):
    codes = fixture(version, "delete.json")

    def request(method, url, json=None, timeout=None, **kw):
        status = codes["ok"]["status"] if json["name"] == "here:1b" else codes["missing"]["status"]
        return httpx.Response(status, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    be = _backend()
    assert be.delete_model("here:1b") is True
    assert be.delete_model("gone:1b") is False


# -- `ollama pull` progress: the text the app parses -------------------------------------------------


def _pull_parts(frames: list[str]) -> list[bytes]:
    """The app reads the command's merged output and splits it on CR or LF; do the same."""
    return [p for p in re.split(rb"[\r\n]+", "".join(frames).encode("utf-8")) if p]


@pytest.mark.parametrize("version", VERSIONS)
def test_pull_progress_is_parsed_from_the_redrawn_output(version):
    frames = fixture(version, "pull_cli.json")["frames"]
    parsed = [_parse_ollama_pull_line(p.decode("utf-8", "replace")) for p in _pull_parts(frames)]
    downloading = [p for p in parsed if p and p.get("phase") == "downloading"]
    assert {
        "percent": 45,
        "phase": "downloading",
        "downloaded": "1.2 GB",
        "total": "2.6 GB",
        "speed": "12 MB/s",
        "eta": "1m50s",
    } in downloading
    assert any(p["percent"] == 100 and "speed" not in p for p in downloading)  # a finished layer
    assert any(p and p.get("phase") == "verifying" for p in parsed)  # "verifying sha256 digest"
    assert all(p["percent"] <= 100 for p in parsed if p)


@pytest.mark.parametrize("version", VERSIONS)
def test_spinner_and_status_lines_are_not_mistaken_for_progress(version):
    for text in ("pulling manifest ⠋ ", "pulling manifest ", "writing manifest ", "success "):
        parsed = _parse_ollama_pull_line(text)
        assert (
            parsed is None or parsed.get("phase") == "verifying"
        )  # only the verify/write lines count


def test_the_cloud_suggestion_failure_text_of_a_newer_engine_is_not_progress():
    text = '"qwen3.6" is available as a cloud model. Try:\n  ollama pull qwen3.6:cloud'
    assert all(_parse_ollama_pull_line(line) is None for line in text.splitlines())
