"""Every place the bundled Ollama version is written down agrees (#227), so the later bump is one mechanical PR.

The places: `_OLLAMA_VERSION` in anthill/inference/ollama.py (behind the macOS download URL) and
`OLLAMA_VERSION` in scripts/build-app.sh; the Windows engine has its own pin, `_OLLAMA_WINDOWS_VERSION`, which
was not moved with macOS (#264). The URL assertions are in tests/test_ollama_windows.py."""

import re
from pathlib import Path

from anthill.inference import ollama

ROOT = Path(__file__).resolve().parent.parent


def _build_script_version() -> str:
    match = re.search(
        r'^OLLAMA_VERSION="([^"]+)"', (ROOT / "scripts/build-app.sh").read_text(), re.M
    )
    assert match, "scripts/build-app.sh no longer sets OLLAMA_VERSION"
    return match.group(1)


def test_the_python_pin_and_the_build_script_pin_agree():
    assert _build_script_version() == ollama._OLLAMA_VERSION


def test_the_download_urls_are_built_from_the_pin():
    assert f"/v{ollama._OLLAMA_VERSION}/" in ollama._OLLAMA_DARWIN_URL
    assert f"/v{ollama._OLLAMA_WINDOWS_VERSION}/" in ollama._OLLAMA_WINDOWS_URL


def test_the_url_assertions_in_the_windows_tests_name_the_pin():
    text = (ROOT / "tests/test_ollama_windows.py").read_text()
    windows = set(re.findall(r'"/v(\d+\.\d+\.\d+)/ollama-windows-amd64\.zip"', text))
    macos = set(re.findall(r'"/v(\d+\.\d+\.\d+)/ollama-darwin\.tgz"', text))
    assert windows == {ollama._OLLAMA_WINDOWS_VERSION}, (
        "a Windows bump must change that assertion in the same PR"
    )
    assert macos == {ollama._OLLAMA_VERSION}, (
        "a macOS bump must change that assertion in the same PR"
    )


def test_the_two_platform_pins_are_what_the_spec_says():
    # macOS moved to 0.40.16 (#264); Windows is not tested on 0.40.x and stays on 0.30.10.
    assert ollama._OLLAMA_VERSION == "0.40.16"
    assert ollama._OLLAMA_WINDOWS_VERSION == "0.30.10"


def test_one_function_picks_the_platforms_pin(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "darwin")
    assert ollama.pinned_engine_version() == ollama._OLLAMA_VERSION
    monkeypatch.setattr(sys, "platform", "win32")
    assert ollama.pinned_engine_version() == ollama._OLLAMA_WINDOWS_VERSION


def test_the_catalogue_check_reads_the_platforms_own_pin(monkeypatch):
    import sys

    from anthill.hosting import sizing

    monkeypatch.setattr(sys, "platform", "darwin")
    assert sizing.bundled_engine_version() == (0, 40, 16)
    monkeypatch.setattr(sys, "platform", "win32")
    assert sizing.bundled_engine_version() == (
        0,
        30,
        10,
    )  # Windows stays on the engine it was tested on


def test_no_other_version_of_the_pin_hides_in_the_workflows_or_docker_files():
    # The pin is only in the two code places above. A third place (a workflow, a Dockerfile) would drift.
    pins = []
    for path in [
        *ROOT.glob(".github/workflows/*.yml"),
        *ROOT.glob("Dockerfile*"),
        *ROOT.glob("docker/*"),
    ]:
        if path.is_file() and re.search(
            r"ollama[^\n]{0,40}\d+\.\d+\.\d+", path.read_text(errors="ignore"), re.I
        ):
            pins.append(path.name)
    assert pins == [], f"Ollama version found in {pins}: add it to the pin audit"
