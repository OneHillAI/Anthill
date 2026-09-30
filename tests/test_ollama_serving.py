"""Local Ollama server lifecycle: ensure_serving starts `ollama serve` when it's installed
but not running, so the local model works on a bare-binary install (no Ollama.app).

All boundaries (reachability probe, process spawn, sleep, binary finder) are injected - no
real server, process, or wall-clock wait.
"""

from anthill.inference import ollama

_LOCAL = "http://localhost:11434"


def test_already_up_does_nothing():
    spawns = []
    ok = ollama.ensure_serving(
        _LOCAL, reachable=lambda u: True, spawn=lambda b: spawns.append(b), sleep=lambda s: None
    )
    assert ok is True
    assert spawns == []  # never starts a second server


def test_starts_server_when_down_then_comes_up():
    state = {"up": False, "spawns": 0}

    def spawn(_bin):
        state["spawns"] += 1
        state["up"] = True  # the spawned server starts answering

    ok = ollama.ensure_serving(
        _LOCAL,
        wait_s=2.0,
        find_bin=lambda: "/Users/x/bin/ollama",
        reachable=lambda u: state["up"],
        spawn=spawn,
        sleep=lambda s: None,
    )
    assert ok is True
    assert state["spawns"] == 1


def test_returns_false_if_it_never_comes_up():
    spawns = []
    ok = ollama.ensure_serving(
        _LOCAL,
        wait_s=1.0,
        find_bin=lambda: "/Users/x/bin/ollama",
        reachable=lambda u: False,
        spawn=lambda b: spawns.append(b),
        sleep=lambda s: None,
    )
    assert ok is False
    assert len(spawns) == 1  # tried to start it once, then gave up


def test_not_installed_does_not_spawn():
    spawns = []
    ok = ollama.ensure_serving(
        _LOCAL,
        find_bin=lambda: None,
        reachable=lambda u: False,
        spawn=lambda b: spawns.append(b),
        sleep=lambda s: None,
    )
    assert ok is False
    assert spawns == []


def test_remote_url_is_never_auto_started():
    spawns = []
    ok = ollama.ensure_serving(
        "http://192.168.1.50:11434",
        find_bin=lambda: "/Users/x/bin/ollama",
        reachable=lambda u: False,
        spawn=lambda b: spawns.append(b),
        sleep=lambda s: None,
    )
    assert ok is False
    assert spawns == []  # a remote host is not ours to manage


def test_find_ollama_bin_prefers_home_bin(monkeypatch):
    monkeypatch.setattr(ollama.os.path, "expanduser", lambda p: "/home/u/bin/ollama")
    monkeypatch.setattr(ollama.os.path, "exists", lambda p: p == "/home/u/bin/ollama")
    monkeypatch.setattr(ollama.os, "access", lambda p, m: True)
    assert ollama.find_ollama_bin() == "/home/u/bin/ollama"


def test_find_ollama_bin_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(ollama.sys, "frozen", False, raising=False)
    monkeypatch.setattr(ollama.os.path, "exists", lambda p: False)
    monkeypatch.setattr(ollama.shutil, "which", lambda name: "/usr/local/bin/ollama")
    assert ollama.find_ollama_bin() == "/usr/local/bin/ollama"


def test_bundled_ollama_preferred_when_frozen(monkeypatch):
    # Packaged app: the runtime bundled at sys._MEIPASS/ollama-runtime/ollama wins over PATH.
    monkeypatch.setattr(ollama.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ollama.sys, "_MEIPASS", "/Anthill.app/Contents/Frameworks", raising=False)
    bundled = "/Anthill.app/Contents/Frameworks/ollama-runtime/ollama"
    monkeypatch.setattr(ollama.os.path, "exists", lambda p: p == bundled)
    monkeypatch.setattr(ollama.os, "access", lambda p, m: p == bundled)
    monkeypatch.setattr(ollama.shutil, "which", lambda name: "/usr/local/bin/ollama")
    assert ollama.bundled_ollama_bin() == bundled
    assert ollama.find_ollama_bin() == bundled  # bundled beats ~/bin and PATH


def test_bundled_ollama_none_when_not_frozen(monkeypatch):
    monkeypatch.setattr(ollama.sys, "frozen", False, raising=False)
    assert ollama.bundled_ollama_bin() is None


# ── first-run Ollama download (the Tauri app ships without the engine) ───────────────────────────


def _fake_ollama_tgz() -> bytes:
    """A tiny tar.gz shaped like the real ollama-darwin.tgz: an `ollama` binary + a sibling lib."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in (("ollama", b"#!/bin/sh\necho ollama\n"), ("lib/runner.so", b"\x00")):
            data = io.BytesIO(body)
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, data)
    return buf.getvalue()


def test_download_ollama_extracts_into_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    blob = _fake_ollama_tgz()
    # verify=False since the fake tgz won't match the pinned production checksum
    path = ollama.download_ollama(fetch=lambda url: blob, verify=False)
    assert path == str(tmp_path / "ollama-runtime" / "ollama")
    assert (tmp_path / "ollama-runtime" / "ollama").exists()
    assert ollama.managed_ollama_bin() == path  # now discoverable in the data dir


def test_download_ollama_rejects_a_bad_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    # verify=True with bytes that don't match the pinned sha256 -> nothing extracted, never run
    assert ollama.download_ollama(fetch=lambda url: b"not the real tarball", verify=True) is None
    assert not (tmp_path / "ollama-runtime" / "ollama").exists()


def test_download_ollama_noops_off_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.sys, "platform", "linux")
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    assert ollama.download_ollama(fetch=lambda url: _fake_ollama_tgz(), verify=False) is None


def test_find_ollama_bin_uses_the_downloaded_engine(tmp_path, monkeypatch):
    # not frozen (no bundled), no ~/bin, nothing on PATH -> the data-dir download is used
    monkeypatch.setattr(ollama.sys, "platform", "darwin")
    monkeypatch.setattr(ollama.sys, "frozen", False, raising=False)
    monkeypatch.setenv("ANTHILL_HOME", str(tmp_path))
    # point ~/bin at a nonexistent path instead of patching os.path.exists (that would break tarfile)
    monkeypatch.setattr(ollama.os.path, "expanduser", lambda p: str(tmp_path / "nope" / "ollama"))
    monkeypatch.setattr(ollama.shutil, "which", lambda name: None)  # nothing on PATH
    assert ollama.find_ollama_bin() is None
    ollama.download_ollama(fetch=lambda url: _fake_ollama_tgz(), verify=False)
    assert ollama.find_ollama_bin() == str(tmp_path / "ollama-runtime" / "ollama")


# ── installed_models/resident_models: never raise (issue: a malformed response from whatever is on
# the configured URL must degrade to "nothing installed", not 500 the request that called it) ──────


def test_installed_models_survives_a_non_json_response(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")  # e.g. an HTML error page on a 200

    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _Resp())
    assert ollama.OllamaBackend(_LOCAL, "").installed_models() == []


def test_installed_models_with_sizes_returns_name_and_size_pairs(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "models": [
                    {"name": "mistral:7b", "size": 4_400_000_000},
                    {"name": "qwen3.5:9b", "size": 6_100_000_000},
                ]
            }

    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _Resp())
    got = ollama.OllamaBackend(_LOCAL, "").installed_models_with_sizes()
    assert got == [
        {"name": "mistral:7b", "size_bytes": 4_400_000_000},
        {"name": "qwen3.5:9b", "size_bytes": 6_100_000_000},
    ]


def test_installed_models_with_sizes_survives_a_non_json_response(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _Resp())
    assert ollama.OllamaBackend(_LOCAL, "").installed_models_with_sizes() == []


def test_resident_models_survives_a_non_json_response(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(ollama.httpx, "get", lambda *a, **k: _Resp())
    assert ollama.OllamaBackend(_LOCAL, "").resident_models() == set()
