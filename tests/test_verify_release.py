"""scripts/verify_release.py: what a release must hold for each platform, checked without a network."""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_release.py"


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("verify_release", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAC_FILES = [
    "Anthill.Beta_1.1.0-rc.1_aarch64.dmg",
    "Anthill.Beta_aarch64.app.tar.gz",
    "Anthill.Beta_aarch64.app.tar.gz.sig",
    "latest.json",
]
WINDOWS_FILES = [
    "Anthill.Beta_1.1.0-rc.1_x64-setup.exe",
    "Anthill.Beta_1.1.0-rc.1_x64-setup.exe.sig",
]
BASE = "https://github.com/OneHillAI/Anthill/releases/download/v1.1.0-rc.1/"


def latest_json(*, windows: bool = True, mac: bool = True) -> dict:
    platforms = {}
    if mac:
        platforms["darwin-aarch64"] = {
            "signature": "sig",
            "url": BASE + "Anthill.Beta_aarch64.app.tar.gz",
        }
    if windows:
        platforms["windows-x86_64"] = {
            "signature": "sig",
            "url": BASE + "Anthill.Beta_1.1.0-rc.1_x64-setup.exe",
        }
    return {"version": "1.1.0-rc.1", "platforms": platforms}


def test_a_complete_release_has_no_problems(verify):
    assert verify.problems(MAC_FILES + WINDOWS_FILES, latest_json(), ["macos", "windows"]) == []


def test_a_mac_only_release_is_complete_for_macos_but_not_for_windows(verify):
    assert verify.problems(MAC_FILES, latest_json(windows=False), ["macos"]) == []
    found = verify.problems(MAC_FILES, latest_json(windows=False), ["macos", "windows"])
    assert any("windows" in p and "-setup" in p for p in found)
    assert any("no 'windows-x86_64' entry" in p for p in found)


def test_windows_files_without_a_manifest_entry_are_reported(verify):
    found = verify.problems(MAC_FILES + WINDOWS_FILES, latest_json(windows=False), ["windows"])
    assert found == ["windows: latest.json has no 'windows-x86_64' entry"]


def test_an_unsigned_manifest_entry_is_reported(verify):
    latest = latest_json()
    latest["platforms"]["windows-x86_64"]["signature"] = ""
    found = verify.problems(MAC_FILES + WINDOWS_FILES, latest, ["windows"])
    assert found == ["windows: the 'windows-x86_64' entry in latest.json has no signature"]


def test_an_entry_pointing_at_a_missing_file_is_reported(verify):
    found = verify.problems([*MAC_FILES, WINDOWS_FILES[1]], latest_json(), ["windows"])
    assert any("-setup" in p and "no file matching" in p for p in found)
    assert any("which is not on the release" in p for p in found)


def test_a_url_with_an_escaped_name_still_matches(verify):
    latest = latest_json()
    latest["platforms"]["windows-x86_64"]["url"] = BASE + "Anthill%20Beta_1.1.0-rc.1_x64-setup.exe"
    names = [
        *MAC_FILES,
        "Anthill Beta_1.1.0-rc.1_x64-setup.exe",
        "Anthill Beta_1.1.0-rc.1_x64-setup.exe.sig",
    ]
    assert verify.problems(names, latest, ["windows"]) == []


def test_no_manifest_at_all_is_reported(verify):
    assert (
        verify.problems(WINDOWS_FILES, None, ["windows"])[0]
        == "no latest.json (the update manifest)"
    )


def test_api_style_assets_are_read_by_name(verify):
    assets = [{"name": n, "size": 1} for n in MAC_FILES + WINDOWS_FILES]
    assert verify.problems(assets, latest_json(), ["macos", "windows"]) == []


def test_the_command_line_exit_codes_offline(verify, tmp_path, capsys):
    assets = tmp_path / "assets.json"
    latest = tmp_path / "latest.json"
    assets.write_text(json.dumps(MAC_FILES + WINDOWS_FILES))
    latest.write_text(json.dumps(latest_json()))
    both = [
        "--assets",
        str(assets),
        "--latest",
        str(latest),
        "--platform",
        "macos",
        "--platform",
        "windows",
    ]
    assert verify.main(both) == 0
    assert "OK" in capsys.readouterr().out
    latest.write_text(json.dumps(latest_json(windows=False)))
    assert verify.main(both) == 1
    assert "MISSING" in capsys.readouterr().err
    assert verify.main(["--platform", "windows"]) == 2  # neither --tag nor --assets
