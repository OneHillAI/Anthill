"""app.py's _maybe_pull_embedding_model(): first-run background pull of Ollama's bge-m3 model so the
semantic cache and wiki-page ranking (anthill/cache/embedder.py) stop degrading to keyword-only once
this has run. Not org-scoped - a shared, install-level resource, unlike a chat model choice - so no DB
setup is needed here, unlike test_mlx_serving.py's org-scoped autostart tests.
"""

import threading

import anthill.web.app as app_mod
from anthill.cache import embedder as emb
from anthill.inference import ollama as ollama_mod

# tests/conftest.py's autouse _no_live_ollama fixture stubs this to a no-op (a real bug it exists to
# prevent: this function's own fallback - shelling out to `ollama pull` when the model looks
# unavailable - isn't an httpx call, so the fixture's transport-level Ollama block can't reach it;
# without the stub, any test spinning up a FastAPI TestClient on a machine with real Ollama installed
# triggers a genuine ~1.2GB download). Captured here, at collection time (before that fixture ever
# runs), so these tests - which specifically want to exercise the real logic - can restore it.
_REAL_MAYBE_PULL_EMBEDDING_MODEL = app_mod._maybe_pull_embedding_model


class _SyncThread:
    """Runs the target synchronously on .start() - makes the fire-and-forget pull thread
    deterministic to assert on, matching the pattern used for Solo's cloud-provisioning tests."""

    def __init__(self, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def test_skips_the_pull_when_the_model_is_already_available(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_maybe_pull_embedding_model", _REAL_MAYBE_PULL_EMBEDDING_MODEL)
    monkeypatch.setattr(emb, "available", lambda: True)

    def _boom():
        raise AssertionError("find_ollama_bin should not run when the model is already available")

    monkeypatch.setattr(ollama_mod, "find_ollama_bin", _boom)

    app_mod._maybe_pull_embedding_model()  # must not raise


def test_pulls_bge_m3_when_not_yet_available(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_maybe_pull_embedding_model", _REAL_MAYBE_PULL_EMBEDDING_MODEL)
    monkeypatch.setattr(emb, "available", lambda: False)
    monkeypatch.setattr(ollama_mod, "find_ollama_bin", lambda: "/usr/local/bin/ollama")
    monkeypatch.setattr(ollama_mod, "ensure_serving", lambda: True)

    captured = {}

    def _fake_run(argv, timeout):
        captured["argv"] = argv
        captured["timeout"] = timeout

        class _Result:
            returncode = 0

        return _Result()

    import subprocess

    monkeypatch.setattr(subprocess, "run", _fake_run)

    app_mod._maybe_pull_embedding_model()
    assert captured["argv"] == ["/usr/local/bin/ollama", "pull", "bge-m3"]
    assert captured["timeout"] == 7200


def test_does_nothing_when_ollama_binary_is_missing(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_maybe_pull_embedding_model", _REAL_MAYBE_PULL_EMBEDDING_MODEL)
    monkeypatch.setattr(emb, "available", lambda: False)
    monkeypatch.setattr(ollama_mod, "find_ollama_bin", lambda: None)

    import subprocess

    def _boom(*a, **k):
        raise AssertionError("subprocess.run should not be called with no ollama binary")

    monkeypatch.setattr(subprocess, "run", _boom)

    app_mod._maybe_pull_embedding_model()  # must not raise


def test_does_nothing_when_ollama_never_comes_up(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_maybe_pull_embedding_model", _REAL_MAYBE_PULL_EMBEDDING_MODEL)
    monkeypatch.setattr(emb, "available", lambda: False)
    monkeypatch.setattr(ollama_mod, "find_ollama_bin", lambda: "/usr/local/bin/ollama")
    monkeypatch.setattr(ollama_mod, "ensure_serving", lambda: False)

    import subprocess

    def _boom(*a, **k):
        raise AssertionError("subprocess.run should not be called when Ollama never comes up")

    monkeypatch.setattr(subprocess, "run", _boom)

    app_mod._maybe_pull_embedding_model()  # must not raise


def test_never_raises_even_on_an_unexpected_error(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(app_mod, "_maybe_pull_embedding_model", _REAL_MAYBE_PULL_EMBEDDING_MODEL)

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(emb, "available", _boom)
    app_mod._maybe_pull_embedding_model()  # must not raise - startup can never fail on this
