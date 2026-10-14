"""The docs-only fast path in ci.yml and pr-validation.yml skips only what a docs-only PR cannot break.

The workflows run on GitHub, so these tests pin what must stay true: the four "Detect docs-only change"
steps run one identical script, both workflows carry the same allow-list and carve-out list, every file
in the carve-out list exists, every file the app or a test reads at runtime is carved out, and the real
step script (run here with bash against a scratch git repo) classifies the tricky diffs correctly.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FILES = ("ci.yml", "pr-validation.yml")
STEP_NAME = "Detect docs-only change"


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _steps() -> list[tuple[str, dict, dict]]:
    found = []
    for name in FILES:
        doc = _doc(name)
        for job, spec in doc["jobs"].items():
            for step in spec.get("steps", []):
                if step.get("name") == STEP_NAME:
                    found.append((f"{name}:{job}", step, doc.get("env", {})))
    return found


def _allow() -> re.Pattern:
    # The workflow's ERE uses the POSIX class [:space:]; Python spells it \s.
    return re.compile(_doc("ci.yml")["env"]["DOCS_ONLY_ALLOW"].replace("[:space:]", r"\s"))


def _carved() -> list[str]:
    return _doc("ci.yml")["env"]["DOCS_ONLY_CARVE_OUT"].splitlines()


def test_the_four_steps_share_one_script_and_never_expand_github_context_into_the_shell():
    steps = _steps()
    assert [s[0] for s in steps] == [
        "ci.yml:test",
        "ci.yml:browser",
        "pr-validation.yml:dep-audit",
        "pr-validation.yml:security",
    ]
    assert len({s[1]["run"] for s in steps}) == 1
    for where, step, _ in steps:
        assert "${{" not in step["run"], where
        assert step["env"]["BASE_REF"] == "${{ github.base_ref }}", where
        assert "--no-renames" in step["run"] and "--name-status" in step["run"], where


def test_both_workflows_carry_the_same_allow_list_and_carve_out():
    ci, prv = _doc("ci.yml")["env"], _doc("pr-validation.yml")["env"]
    for key in ("DOCS_ONLY_ALLOW", "DOCS_ONLY_CARVE_OUT"):
        assert ci[key] == prv[key], key


def test_every_carved_file_exists_and_would_otherwise_count_as_docs():
    allow = _allow()
    for path in _carved():
        assert (ROOT / path).is_file(), f"{path} is in DOCS_ONLY_CARVE_OUT but no longer exists"
        assert allow.search(path), f"{path} is carved out but is not in the allow-list anyway"


def test_ordinary_docs_are_still_docs_only():
    allow, carved = _allow(), set(_carved())
    for path in ("docs/specs/x.md", "changelog.d/1.added.md", "docs-site/README.md"):
        assert allow.search(path) and path not in carved, path
    for path in ("docs/evil.py", "docs/specs/run.sh", "changelog.d/evil.yml", "docs/a b.md"):
        assert not allow.search(path), path


def test_every_published_docs_site_page_is_carved_out():
    js = (ROOT / "docs-site" / "scripts" / "collect-docs.js").read_text()
    sources = re.findall(r'\[\s*"([^"]+)"\s*,\s*"[^"]+"\s*\]', js)
    assert sources, "could not read the FILES whitelist in collect-docs.js"
    assert set(sources) <= set(_carved())


def test_every_repo_doc_the_app_or_a_test_reads_is_carved_out():
    # Path joins from the repo root to a doc-shaped file: the app's `_HERE.parent.parent / "docs" /
    # "x.md"` and a test's `ROOT / "docs/x.md"` or `ROOT / "CHANGELOG.md"`. Writes into tmp_path are
    # not repo files and do not match the ROOT-style prefixes.
    pattern = re.compile(
        r'(?:ROOT|_HERE\.parent\.parent)\s*/\s*"(docs"\s*/\s*"[^"]+\.md|[^"]+\.md)"'
    )
    allow, carved = _allow(), set(_carved())
    readers: dict[str, str] = {}
    for base in ("anthill", "tests", "scripts"):
        for path in (ROOT / base).rglob("*.py"):
            if path == Path(__file__).resolve():
                continue  # this file's own comments name example paths
            for match in pattern.finditer(path.read_text(errors="replace")):
                rel = re.sub(r'"\s*/\s*"', "/", match.group(1))
                if allow.search(rel):
                    readers[rel] = str(path.relative_to(ROOT))
    for name in ("how-it-works.md", "setup.md"):  # app.py's _HELP_DOCS joins these by name
        readers.setdefault(f"docs/{name}", "anthill/web/app.py")
    missing = {rel: who for rel, who in readers.items() if rel not in carved}
    assert not missing, f"read at runtime but not in DOCS_ONLY_CARVE_OUT: {missing}"


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("git"), reason="needs bash and git"
)
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("edit-doc", "true"),
        ("rename-code-to-doc", "false"),
        ("edit-carved", "false"),
        ("symlink-over-doc", "false"),
        ("non-ascii-name", "false"),
    ],
)
def test_the_real_step_script_classifies_tricky_diffs(tmp_path, change, expected):
    _, step, wf_env = _steps()[0]
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    repo = tmp_path / "repo"
    (repo / "anthill").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "anthill" / "mod.py").write_text("x = 1\n")
    (repo / "docs" / "guide.md").write_text("# guide\n")
    (repo / "docs" / "setup.md").write_text("# setup\n")
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=repo, check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
    if change == "edit-doc":
        (repo / "docs" / "guide.md").write_text("# guide, reworded\n")
    elif change == "rename-code-to-doc":
        subprocess.run([*git, "mv", "anthill/mod.py", "docs/mod.md"], cwd=repo, check=True)
    elif change == "edit-carved":
        (repo / "docs" / "setup.md").write_text("# setup, reworded\n")
    elif change == "symlink-over-doc":
        (repo / "docs" / "guide.md").unlink()
        os.symlink("../anthill/mod.py", repo / "docs" / "guide.md")
    elif change == "non-ascii-name":
        (repo / "docs" / "café.md").write_text("cafe\n")
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "change"], cwd=repo, check=True)

    output = tmp_path / "github_output"
    output.write_text("")
    script = tmp_path / "step.sh"
    script.write_text(step["run"])
    env = {**os.environ, **wf_env, "BASE_REF": "main", "EVENT_NAME": "pull_request"}
    env["GITHUB_OUTPUT"] = str(output)
    subprocess.run(["bash", "-e", str(script)], cwd=repo, env=env, check=True, capture_output=True)
    assert output.read_text().splitlines()[0] == f"docs_only={expected}"


def test_the_readme_that_the_package_reads_is_carved_out():
    """pyproject.toml declares the readme, so every install in the gated jobs reads it: it must not be fast-pathed."""
    # read with a pattern, not tomllib: the test matrix includes Python 3.10, which has no tomllib
    match = re.search(
        r'^readme\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE
    )
    assert match, "pyproject.toml no longer declares a readme file: update this test"
    assert match.group(1) in _carved()
