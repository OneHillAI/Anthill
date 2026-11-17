"""Every place the bundled Ollama version is written down agrees (#227), so the later bump is one mechanical PR.

The places: `_OLLAMA_VERSION` in anthill/inference/ollama.py (also behind the macOS and Windows download URLs),
`OLLAMA_VERSION` in scripts/build-app.sh, and the two URL assertions in tests/test_ollama_windows.py. The bump
itself is not made here."""

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
    assert f"/v{ollama._OLLAMA_VERSION}/" in ollama._OLLAMA_WINDOWS_URL


def test_the_url_assertions_in_the_windows_tests_name_the_pin():
    text = (ROOT / "tests/test_ollama_windows.py").read_text()
    versions = set(
        re.findall(r'"/v(\d+\.\d+\.\d+)/ollama-(?:windows-amd64\.zip|darwin\.tgz)"', text)
    )
    assert versions == {ollama._OLLAMA_VERSION}, (
        "a bump must change those two assertions in the same PR"
    )


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
