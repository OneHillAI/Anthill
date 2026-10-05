"""The fake Ollama used by scripts/smoke_frozen_chat.py must keep speaking the routes the app uses,
or the frozen-binary release gate would fail for the wrong reason."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_frozen_chat.py"


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("smoke_frozen_chat", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fake(smoke):
    server, port = smoke.start_fake_ollama()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_fake_reports_the_embedding_model_so_the_semantic_cache_turns_on(fake):
    names = {m["name"] for m in httpx.get(f"{fake}/api/tags").json()["models"]}
    assert "bge-m3:latest" in names and "qwen2.5:3b" in names


def test_fake_embeds_deterministically_to_a_1024_d_unit_vector(fake):
    body = {"model": "bge-m3", "input": "hello"}
    a = httpx.post(f"{fake}/api/embed", json=body).json()["embeddings"][0]
    b = httpx.post(f"{fake}/api/embed", json=body).json()["embeddings"][0]
    assert a == b and len(a) == 1024
    assert abs(sum(v * v for v in a) - 1.0) < 1e-6


def test_fake_streams_a_chat_answer_as_ndjson_that_ends_with_done(smoke, fake):
    with httpx.stream(
        "POST", f"{fake}/api/chat", json={"model": "qwen2.5:3b", "messages": []}
    ) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1]["done"] is True
    text = "".join(e["message"]["content"] for e in events).strip()
    assert text == smoke.ANSWER


def test_fake_answers_the_non_streaming_chat_and_the_other_routes(smoke, fake):
    one = httpx.post(f"{fake}/api/chat", json={"model": "qwen2.5:3b", "stream": False}).json()
    assert one["done"] is True and one["message"]["content"] == smoke.ANSWER
    assert httpx.post(f"{fake}/api/show", json={"model": "qwen2.5:3b"}).status_code == 200
    assert httpx.get(f"{fake}/api/ps").json() == {"models": []}


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in binary is a shell script")
def test_run_smoke_launches_a_binary_given_by_a_relative_path(smoke, tmp_path, monkeypatch, capsys):
    # Regression: scripts/build-sidecar.sh passes `dist/anthill-server`. The sidecar is started in its own
    # throwaway working directory, so an unresolved relative path made the launch raise FileNotFoundError
    # and the v0.12.5 desktop build failed at this gate before it ever ran a chat.
    (tmp_path / "dist").mkdir()
    fake = tmp_path / "dist" / "anthill-server"
    fake.write_text("#!/bin/sh\necho PORT=1\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    code = smoke.run_smoke("dist/anthill-server", timeout=3)  # must not raise
    out = capsys.readouterr().out
    assert code == 1  # the fake never serves, so the chat cannot pass ...
    assert "never became ready" in out  # ... but the process WAS launched and waited on
    assert "not found" not in out


def test_run_smoke_reports_a_missing_binary_clearly(smoke, tmp_path, capsys):
    assert smoke.run_smoke(str(tmp_path / "nope"), timeout=1) == 1
    assert "not found or not executable" in capsys.readouterr().out


class _Launcher:
    """Stands in for the bootloader process the smoke script kills."""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_backend_that_keeps_serving_after_its_launcher_dies_is_reported(smoke):
    server, port = smoke.start_fake_ollama()
    base = f"http://127.0.0.1:{port}"
    try:
        # The fake answers /login with 404, which still proves something is serving there.
        assert smoke._backend_outlives_launcher(_Launcher(), base, wait=1.0) is True
    finally:
        server.shutdown()
        server.server_close()
    assert smoke._backend_outlives_launcher(_Launcher(), base, wait=1.0) is False


class _Process:
    def __init__(self, exited: bool):
        self._exited = exited

    def poll(self):
        return 1 if self._exited else None


def test_real_ollama_mode_reports_a_backend_that_died_while_downloading(smoke, tmp_path):
    why = smoke._prepare_real_ollama(tmp_path, "tiny:1b", _Process(exited=True), engine_wait=5)
    assert why and "exited" in why


def test_real_ollama_mode_gives_up_when_the_engine_never_arrives(smoke, tmp_path):
    why = smoke._prepare_real_ollama(tmp_path, "tiny:1b", _Process(exited=False), engine_wait=0.1)
    assert why and "never finished downloading Ollama" in why
