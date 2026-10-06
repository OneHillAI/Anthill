"""The Windows CI shares one sidecar build and runs only the jobs a change needs.

The workflow runs on GitHub, so a unit test cannot run it; these tests pin what must stay true: the changed-files rules
(scripts/windows_ci_areas.py), that the backend is built once, that every job that needs it gets it from the shared
artifact, and that the workflow's own path filter cannot drift from the rules.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "windows_ci_areas.py"


@pytest.fixture(scope="module")
def areas():
    spec = importlib.util.spec_from_file_location("windows_ci_areas", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load((WORKFLOWS / "windows-ci.yml").read_text())


def _on(doc: dict) -> dict:
    return doc.get("on") or doc.get(True)  # YAML reads a bare `on:` key as the boolean True


def test_ollama_or_scheduler_changes_need_only_the_backend_job(areas):
    assert areas.areas(["anthill/inference/ollama.py"]) == {"backend": True, "shell": False}
    assert areas.areas(["anthill/web/scheduler.py", "README.md"]) == {
        "backend": True,
        "shell": False,
    }


def test_installer_and_update_check_changes_need_only_the_shell_jobs(areas):
    assert areas.areas(["scripts/windows_update_check.py"]) == {"backend": False, "shell": True}
    assert areas.areas(["tests/test_windows_installer_check.py"]) == {
        "backend": False,
        "shell": True,
    }


def test_shell_and_sidecar_build_changes_need_everything(areas):
    for path in (
        "src-tauri/src/lib.rs",
        "src-tauri/tauri.conf.json",
        "scripts/build-sidecar.sh",
        "anthill/desktop.py",
    ):
        assert areas.areas([path]) == {"backend": True, "shell": True}, path


def test_unrelated_changes_need_nothing(areas):
    assert areas.areas(["README.md", "docs/releasing.md", "anthill/web/app.py", ""]) == {
        "backend": False,
        "shell": False,
    }
    assert areas.areas([]) == {"backend": False, "shell": False}


def test_a_folder_rule_does_not_match_a_sibling_with_the_same_prefix(areas):
    assert areas.areas(["src-tauri-notes.md"]) == {"backend": False, "shell": False}


def test_the_command_line_reads_paths_and_prints_github_output(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="docs/x.md\nanthill/hosting/sizing.py\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.splitlines() == ["backend=true", "shell=false"]
    everything = subprocess.run(
        [sys.executable, str(SCRIPT), "--all"], capture_output=True, text=True, check=True
    ).stdout
    assert everything.splitlines() == ["backend=true", "shell=true"]


def test_the_jobs_are_the_shared_build_and_the_three_that_use_it(ci):
    assert list(ci["jobs"]) == ["changes", "sidecar", "backend", "installer", "update"]


def test_the_sidecar_is_built_in_exactly_one_job_in_all_the_windows_pull_request_workflows(ci):
    builders = []
    for path in sorted(WORKFLOWS.glob("windows-*.yml")):
        doc = yaml.safe_load(path.read_text())
        for name, job in doc["jobs"].items():
            if any("build-sidecar.sh" in str(step.get("run", "")) for step in job.get("steps", [])):
                builders.append(f"{path.name}:{name}")
    assert builders == ["windows-ci.yml:sidecar"]


def test_the_old_per_check_workflows_are_gone():
    for old in ("windows-build.yml", "windows-installer.yml", "windows-update.yml"):
        assert not (WORKFLOWS / old).exists(), old


def test_every_job_that_needs_the_sidecar_waits_for_it_and_downloads_the_artifact(ci):
    for name in ("backend", "installer", "update"):
        job = ci["jobs"][name]
        assert "sidecar" in job["needs"], name
        downloads = [s for s in job["steps"] if "download-artifact" in s.get("uses", "")]
        assert len(downloads) == 1 and downloads[0]["with"]["name"] == "windows-sidecar", name
    uploads = [s for s in ci["jobs"]["sidecar"]["steps"] if "upload-artifact" in s.get("uses", "")]
    assert len(uploads) == 1 and uploads[0]["with"]["name"] == "windows-sidecar"
    assert uploads[0]["with"]["if-no-files-found"] == "error"


def test_the_jobs_run_only_when_their_area_changed(ci):
    assert "outputs.backend == 'true'" in ci["jobs"]["backend"]["if"]
    for name in ("installer", "update"):
        assert "outputs.shell == 'true'" in ci["jobs"][name]["if"], name
    both = ci["jobs"]["sidecar"]["if"]
    assert "outputs.backend == 'true'" in both and "outputs.shell == 'true'" in both


def test_artifact_actions_are_pinned_to_a_commit(ci):
    text = (WORKFLOWS / "windows-ci.yml").read_text()
    for action in ("upload-artifact", "download-artifact", "setup-uv"):
        lines = [ln for ln in text.splitlines() if action in ln and "uses:" in ln]
        assert lines and all(re.search(r"@[0-9a-f]{40}\b", ln) for ln in lines), action


def test_the_workflow_path_filter_lists_every_rule(ci, areas):
    expected = areas.workflow_paths()
    on = _on(ci)
    assert on["pull_request"]["paths"] == expected
    assert on["push"]["paths"] == expected  # a push to main is judged by the same rules
