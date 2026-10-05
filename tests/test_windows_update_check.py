"""The helpers of scripts/windows_update_check.py (the manifest, the version match and the local feed), on every platform."""

import importlib.util
import urllib.request
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "windows_update_check.py"


@pytest.fixture(scope="module")
def update():
    spec = importlib.util.spec_from_file_location("windows_update_check", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_manifest_points_both_windows_keys_at_the_new_installer(update):
    manifest = update.build_manifest(
        "0.9.2", "http://127.0.0.1:8765/Anthill_0.9.2_x64-setup.exe", "c2ln\n"
    )
    assert manifest["version"] == "0.9.2"
    for key in ("windows-x86_64", "windows-x86_64-nsis"):
        assert manifest["platforms"][key] == {
            "signature": "c2ln",
            "url": "http://127.0.0.1:8765/Anthill_0.9.2_x64-setup.exe",
        }


@pytest.mark.parametrize("reported", ["0.9.2", "0.9.2.0", " 0.9.2.0\r\n", "0.9.2.00"])
def test_a_version_with_trailing_zero_parts_still_matches(update, reported):
    assert update.version_matches(reported, "0.9.2")


@pytest.mark.parametrize("reported", ["0.9.1", "0.9.1.0", "0.9.20", "0.9.2.1", "1.0.9.2", ""])
def test_other_versions_do_not_match(update, reported):
    assert not update.version_matches(reported, "0.9.2")


def test_the_local_feed_serves_files_and_remembers_what_was_asked_for(update, tmp_path):
    (tmp_path / "latest.json").write_text('{"version": "0.9.2"}')
    (tmp_path / "Anthill_0.9.2_x64-setup.exe").write_bytes(b"installer")
    feed = update.Feed(tmp_path, 0).start()  # port 0: any free port
    port = feed._server.server_address[1]
    try:
        assert not feed.asked("latest.json")
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/latest.json?x=1", timeout=5).read()
        assert b"0.9.2" in body
        assert feed.asked("latest.json")
        assert not feed.asked("Anthill_0.9.2_x64-setup.exe")
        assert (
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/Anthill_0.9.2_x64-setup.exe", timeout=5
            ).read()
            == b"installer"
        )
        assert feed.asked("Anthill_0.9.2_x64-setup.exe")
    finally:
        feed.stop()
