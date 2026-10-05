"""Ollama on Windows: find it, download it, start it, and read the machine's memory.

Most of these run on every platform by pretending to be Windows for the one comparison that decides the
branch, so a change cannot quietly break macOS or Windows. The tests that need the real Windows system calls
are skipped elsewhere. The macOS tests at the bottom pin what Phase B must not change.
"""

import hashlib
import importlib.util
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from anthill import platform_layer
from anthill.hosting import sizing
from anthill.inference import ollama

ON_WINDOWS = sys.platform == "win32"


def _zip_bytes(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"\x00")
    return buf.getvalue()


def _writes(blob: bytes, calls: list[str] | None = None):
    """A stand-in for the streaming download: writes ``blob`` and reports its real SHA-256."""

    def stream(url: str, dest: Path) -> str:
        if calls is not None:
            calls.append(url)
        dest.write_bytes(blob)
        return hashlib.sha256(blob).hexdigest()

    return stream


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """Behave like Windows for the platform comparison, with an empty data dir and nothing installed."""
    monkeypatch.setattr(ollama.sys, "platform", "win32")
    monkeypatch.setattr(ollama.sys, "frozen", False, raising=False)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(ollama.shutil, "which", lambda name: None)
    return tmp_path


@pytest.fixture
def fresh():
    """The Ollama module as written. A developer's plugin that hides a real Ollama from the test run
    replaces it on the shared module, so these tests load their own copy of the module."""
    spec = importlib.util.spec_from_file_location("anthill.inference._ollama_copy", ollama.__file__)
    assert spec and spec.loader
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    return copy


def _real_runtime() -> bytes:
    return _zip_bytes("ollama.exe", "lib/ollama/cuda_v12/ggml-cuda.dll")


def test_windows_download_unpacks_the_verified_runtime_into_the_data_dir(windows, monkeypatch):
    blob = _real_runtime()
    monkeypatch.setattr(ollama, "_OLLAMA_WINDOWS_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(ollama, "_http_download", _writes(blob))
    path = ollama._download_ollama_windows()
    runtime = windows / "home" / "ollama-runtime"
    assert path == str(runtime / "ollama.exe")
    assert (runtime / "ollama.exe").exists()
    assert (runtime / "lib" / "ollama" / "cuda_v12" / "ggml-cuda.dll").exists()
    assert not (windows / "home" / "ollama-runtime.partial").exists()  # staging is cleaned up


def test_windows_download_dispatches_from_download_ollama(windows, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ollama, "_http_download", _writes(_real_runtime(), calls))
    assert ollama.download_ollama(verify=False) == str(
        windows / "home" / "ollama-runtime" / "ollama.exe"
    )
    assert calls == [ollama._OLLAMA_WINDOWS_URL]
    assert ollama._OLLAMA_WINDOWS_URL.endswith("/v0.30.10/ollama-windows-amd64.zip")


def test_windows_download_rejects_a_bad_checksum_and_leaves_nothing(windows, monkeypatch):
    monkeypatch.setattr(ollama, "_http_download", _writes(_real_runtime()))
    assert ollama._download_ollama_windows() is None  # does not match the pinned checksum
    assert not (windows / "home" / "ollama-runtime").exists()
    assert not (windows / "home" / "ollama-runtime.partial").exists()


def test_windows_download_needs_the_executable_in_the_archive(windows, monkeypatch):
    monkeypatch.setattr(ollama, "_http_download", _writes(_zip_bytes("lib/ollama/x.dll")))
    assert ollama._download_ollama_windows(verify=False) is None
    assert not (windows / "home" / "ollama-runtime").exists()


def test_windows_download_replaces_an_incomplete_earlier_attempt(windows, monkeypatch):
    stale = windows / "home" / "ollama-runtime"
    stale.mkdir(parents=True)
    (stale / "half-written.dll").write_bytes(b"\x00")  # a runtime with no ollama.exe
    monkeypatch.setattr(ollama, "_http_download", _writes(_real_runtime()))
    assert ollama._download_ollama_windows(verify=False) == str(stale / "ollama.exe")
    assert not (stale / "half-written.dll").exists()


def test_windows_download_is_skipped_when_the_disk_is_nearly_full(windows, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ollama, "_http_download", _writes(_real_runtime(), calls))
    monkeypatch.setattr(
        ollama.shutil,
        "disk_usage",
        lambda p: type("U", (), {"free": 1024**3, "total": 1, "used": 1})(),
    )
    assert ollama._download_ollama_windows(verify=False) is None
    assert calls == []  # never started a gigabyte download it could not finish


def test_windows_download_does_not_download_twice(windows, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ollama, "_http_download", _writes(_real_runtime(), calls))
    first = ollama._download_ollama_windows(verify=False)
    Path(first).chmod(
        0o755
    )  # the executable bit only matters off Windows, where this runs as a test
    assert ollama._download_ollama_windows(verify=False) == first
    assert len(calls) == 1


def test_windows_find_prefers_the_managed_engine_then_the_official_install(
    windows, monkeypatch, fresh
):
    local = windows / "local" / "Programs" / "Ollama"
    local.mkdir(parents=True)
    (local / "ollama.exe").write_bytes(b"\x00")
    monkeypatch.setattr(fresh, "managed_ollama_bin", lambda: None)
    assert fresh.find_ollama_bin() == str(local / "ollama.exe")
    monkeypatch.setattr(fresh, "managed_ollama_bin", lambda: "C:/data/ollama-runtime/ollama.exe")
    assert fresh.find_ollama_bin() == "C:/data/ollama-runtime/ollama.exe"


def test_windows_find_falls_back_to_the_path(windows, monkeypatch, fresh):
    monkeypatch.setattr(fresh, "managed_ollama_bin", lambda: None)
    monkeypatch.setattr(ollama.os.path, "expanduser", lambda p: str(windows / "nope" / "ollama"))
    monkeypatch.setattr(ollama.shutil, "which", lambda name: "C:/tools/ollama.exe")
    assert fresh.find_ollama_bin() == "C:/tools/ollama.exe"


def test_windows_managed_dir_defaults_to_the_local_app_data_folder(monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "win32")
    monkeypatch.delenv("ANTHILL_HOME", raising=False)
    monkeypatch.setattr(
        ollama, "user_data_dir", lambda name, appauthor: f"C:/Users/u/AppData/Local/{name}"
    )
    assert ollama._managed_ollama_dir() == Path("C:/Users/u/AppData/Local/Anthill/ollama-runtime")
    assert ollama._ollama_exe_name() == "ollama.exe"


# ── starting it, and the console window ────────────────────────────────────────────────────────────


def test_serve_is_started_detached_through_the_platform_layer(monkeypatch):
    seen = {}

    def popen(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs

    monkeypatch.setattr(ollama.subprocess, "Popen", popen)
    ollama._spawn_ollama_serve("/x/ollama")
    assert seen["cmd"] == ["/x/ollama", "serve"]
    for key, value in platform_layer.detached_process_kwargs().items():
        assert seen["kwargs"][key] == value


@pytest.mark.skipif(not ON_WINDOWS, reason="needs the Windows process-creation flags")
def test_windows_flags_hide_the_window_and_detach():
    hidden = platform_layer.hidden_window_kwargs()
    assert hidden == {"creationflags": subprocess.CREATE_NO_WINDOW}
    detached = platform_layer.detached_process_kwargs()["creationflags"]
    assert detached & subprocess.DETACHED_PROCESS
    assert detached & subprocess.CREATE_NO_WINDOW
    assert detached & subprocess.CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in platform_layer.detached_process_kwargs()


@pytest.mark.skipif(ON_WINDOWS, reason="the macOS and Linux flags")
def test_other_platforms_keep_exactly_the_old_process_arguments():
    assert platform_layer.hidden_window_kwargs() == {}
    assert platform_layer.detached_process_kwargs() == {"start_new_session": True}


# ── reading the machine's memory ───────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not ON_WINDOWS, reason="uses the Windows memory call")
def test_windows_memory_is_read_from_the_system():
    total, available = platform_layer.memory_gb()
    assert total > 1 and 0 < available <= total
    ram, kind = sizing.local_hardware()
    assert ram > 1 and kind in (
        "apple",
        "gpu",
    )  # a real figure, not 0.0, so the picker offers real models
    assert sizing.free_mem_gb() > 0


@pytest.mark.skipif(ON_WINDOWS, reason="Windows has the real call")
def test_memory_gb_is_none_off_windows():
    assert platform_layer.memory_gb() is None


def test_windows_ram_and_free_memory_come_from_the_platform_layer(monkeypatch):
    monkeypatch.setattr(sizing.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sizing, "memory_gb", lambda: (32.0, 11.5))
    assert sizing._posix_ram_gb() == 32.0
    assert sizing.free_mem_gb() == 11.5
    monkeypatch.setattr(sizing, "memory_gb", lambda: None)
    assert sizing._posix_ram_gb() is None
    assert sizing.free_mem_gb() is None


# ── what Phase B must not change on macOS ──────────────────────────────────────────────────────────


def test_macos_engine_download_and_paths_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.delenv("ANTHILL_HOME", raising=False)
    monkeypatch.setattr(ollama.Path, "home", classmethod(lambda cls: tmp_path))
    assert (
        ollama._managed_ollama_dir()
        == tmp_path / "Library" / "Application Support" / "Anthill" / "ollama-runtime"
    )
    assert ollama._ollama_exe_name() == "ollama"
    assert ollama._OLLAMA_DARWIN_URL.endswith("/v0.30.10/ollama-darwin.tgz")
    assert (
        ollama._OLLAMA_DARWIN_SHA256
        == "ad8a4d2918ed09480b8160419570602b4f49e48c9e3792efb601c0f54619e48e"
    )


def test_macos_never_takes_the_windows_download(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    called: list[str] = []
    monkeypatch.setattr(ollama, "_download_ollama_windows", lambda **kw: called.append("windows"))
    ollama.download_ollama(fetch=lambda url: b"not a tarball", verify=False)
    assert called == []


def test_macos_find_order_is_unchanged(tmp_path, monkeypatch, fresh):
    # managed engine, then ~/bin/ollama, then PATH - and no Windows install folder in the list
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.setattr(ollama.sys, "frozen", False, raising=False)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    windows_install = tmp_path / "local" / "Programs" / "Ollama"
    windows_install.mkdir(parents=True)
    (windows_install / "ollama.exe").write_bytes(b"\x00")
    monkeypatch.setattr(ollama.os.path, "expanduser", lambda p: str(tmp_path / "nope" / "ollama"))
    monkeypatch.setattr(ollama.shutil, "which", lambda name: "/usr/local/bin/ollama")
    assert fresh.find_ollama_bin() == "/usr/local/bin/ollama"
