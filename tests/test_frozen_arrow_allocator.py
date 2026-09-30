"""Regression tests for the frozen-sidecar chat crash.

Arrow's default allocator (mimalloc, pyarrow 25) segfaulted inside the PyInstaller-frozen desktop
sidecar the first time a worker thread allocated through pyarrow, which killed every chat turn that
reached the semantic cache (lancedb). `anthill/__init__.py` now selects the system allocator, the
sidecar build runs `--selfcheck-cache`, and `SemanticCache` degrades to a no-op when its store cannot
open. See docs/specs/frozen-arrow-allocator-crash.md.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_PROBE = "import anthill, pyarrow as pa; print(pa.default_memory_pool().backend_name)"


def _pool_backend(extra_env: dict[str, str] | None = None) -> str:
    """The Arrow pool a fresh interpreter ends up with after `import anthill`."""
    env = {k: v for k, v in os.environ.items() if k != "ARROW_DEFAULT_MEMORY_POOL"}
    env.update(extra_env or {})
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importing_anthill_selects_the_system_arrow_allocator():
    assert _pool_backend() == "system"


def test_an_operator_override_of_the_allocator_is_respected():
    import pyarrow as pa

    if "mimalloc" not in pa.supported_memory_backends():
        pytest.skip("this pyarrow build has no mimalloc backend to select")
    assert _pool_backend({"ARROW_DEFAULT_MEMORY_POOL": "mimalloc"}) == "mimalloc"


def test_selfcheck_cache_passes_on_a_worker_thread(capsys):
    from anthill import desktop

    assert desktop._selfcheck_cache() == 0
    assert "worker-thread add/search OK" in capsys.readouterr().out


def test_semantic_cache_degrades_to_a_noop_when_its_store_cannot_open(tmp_path, monkeypatch):
    from anthill.cache import cache as cache_mod

    def _boom(_path):
        raise RuntimeError("native stack unavailable")

    monkeypatch.setattr(cache_mod, "CacheStore", _boom)
    c = cache_mod.SemanticCache(db_path=tmp_path / ".c")
    assert c.lookup("anything at all") is None
    c.store("a question", "an answer")  # must not raise
    assert c.size() == 0


def test_the_kill_switch_never_touches_the_native_store(tmp_path, monkeypatch):
    from anthill.cache import cache as cache_mod

    opened: list[object] = []
    monkeypatch.setenv("ANTHILL_DISABLE_SEMANTIC_CACHE", "1")
    monkeypatch.setattr(cache_mod, "CacheStore", lambda p: opened.append(p))
    c = cache_mod.SemanticCache(db_path=tmp_path / ".c")
    assert opened == []
    assert c.lookup("x") is None
    assert c.size() == 0


def test_selfcheck_cache_fails_when_arrow_is_not_on_the_system_allocator(monkeypatch, capsys):
    import pyarrow as pa

    from anthill import desktop

    class _Pool:
        backend_name = "mimalloc"

    monkeypatch.setattr(pa, "default_memory_pool", lambda: _Pool())
    assert desktop._selfcheck_cache() == 1
    out = capsys.readouterr().out
    assert "mimalloc" in out and "must use 'system'" in out
