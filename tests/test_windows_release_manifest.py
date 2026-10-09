"""scripts/windows_release_manifest.py: adding the Windows entry to a release's latest.json, without a network."""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "windows_release_manifest.py"
URL = "https://github.com/OneHillAI/Anthill/releases/download/v1.2.3/Anthill_1.2.3_x64-setup.exe"


@pytest.fixture(scope="module")
def manifest():
    spec = importlib.util.spec_from_file_location("windows_release_manifest", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest() -> dict:
    return {
        "version": "1.2.3",
        "notes": "x",
        "pub_date": "2026-01-01T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {"signature": "mac-sig", "url": "https://example.test/a.app.tar.gz"},
            "darwin-aarch64-app": {
                "signature": "mac-sig",
                "url": "https://example.test/a.app.tar.gz",
            },
        },
    }


def test_the_windows_entries_are_added_and_the_macos_ones_are_kept(manifest):
    old = _latest()
    merged = manifest.merge(old, version="1.2.3", url=URL, signature="win-sig\n")
    assert merged["platforms"]["darwin-aarch64"] == old["platforms"]["darwin-aarch64"]
    for key in ("windows-x86_64", "windows-x86_64-nsis"):
        assert merged["platforms"][key] == {"signature": "win-sig", "url": URL}
    assert (
        merged["version"] == "1.2.3"
        and merged["notes"] == "x"
        and merged["pub_date"] == old["pub_date"]
    )
    assert "windows-x86_64" not in old["platforms"]  # the input is not changed


@pytest.mark.parametrize(
    "change, why",
    [
        (lambda d: d.update(version="1.2.2"), "version"),
        (lambda d: d.update(platforms={}), "macOS entry"),
        (lambda d: d.pop("platforms"), "macOS entry"),
        (lambda d: d.update(platforms={"windows-x86_64": {}}), "macOS entry"),
    ],
)
def test_a_manifest_that_is_not_the_macos_jobs_for_this_version_is_refused(manifest, change, why):
    latest = _latest()
    change(latest)
    with pytest.raises(manifest.Refused, match=why):
        manifest.merge(latest, version="1.2.3", url=URL, signature="s")


def test_an_empty_signature_or_a_plain_http_url_is_refused(manifest):
    with pytest.raises(manifest.Refused, match="signature"):
        manifest.merge(_latest(), version="1.2.3", url=URL, signature=" \n")
    with pytest.raises(manifest.Refused, match="https"):
        manifest.merge(_latest(), version="1.2.3", url=URL.replace("https", "http"), signature="s")


def test_the_command_line_writes_the_file_or_refuses(manifest, tmp_path, capsys):
    latest, sig, out = (
        tmp_path / "latest.json",
        tmp_path / "x.sig",
        tmp_path / "new" / "latest.json",
    )
    latest.write_text(json.dumps(_latest()))
    sig.write_text("win-sig\n")
    args = [
        "--latest",
        str(latest),
        "--version",
        "1.2.3",
        "--url",
        URL,
        "--signature-file",
        str(sig),
        "--out",
        str(out),
    ]
    assert manifest.main(args) == 0
    assert json.loads(out.read_text())["platforms"]["windows-x86_64"]["signature"] == "win-sig"
    assert "OK" in capsys.readouterr().out
    out.unlink()
    assert manifest.main([*args[:3], "9.9.9", *args[4:]]) == 1
    assert "REFUSED" in capsys.readouterr().err
    assert not out.exists()  # nothing is written when it refuses
