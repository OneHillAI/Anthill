"""The catalog publish step never touches the landing site (spec: model-catalog-trust, "Publishing touches only
the catalog").

The step once mirrored a folder over the deploy repo with ``rsync --delete``. The deploy repo's root is also the
live anthill.run landing site, so on 2026-10-04 the job deleted the site and left a placeholder. These tests run
the REAL step from the workflow against a local copy of a deploy repo that holds a landing site. No network.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalog.yml"
CATALOG = "model-catalog.json"
SIGNATURE = "model-catalog.json.sigstore.json"
SITE = {
    "index.html": "<html>the real landing page</html>",
    "features.html": "<html>features</html>",
    "chat-widget.js": "// widget",
    "llms.txt": "llms",
    "_redirects": "/old /new 301",
    "functions/api/chat.js": "// chat",
    "functions/api/contact.js": "// contact",
}

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the step is a bash script")


_IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


def _env(*, identity):
    """Git ignores the machine's own config. The seed commits get an identity from the environment; the
    publish step must not, so that it commits as the identity it sets for itself, as it does in CI."""
    env = {k: v for k, v in os.environ.items() if k not in _IDENTITY}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    if identity:
        env.update({k: "seed" if k.endswith("NAME") else "seed@example.org" for k in _IDENTITY})
    return env


def _git(cwd, *args):
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=_env(identity=True), check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _write(base, files):
    for name, text in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _publish_script():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["refresh"]["steps"]
    step = next(s for s in steps if s.get("name", "").startswith("Publish the signed catalog"))
    return step["run"]


def _world(tmp_path, *, old_catalog):
    """A bare 'GitHub' repo holding the landing site, a clone of it as deploy/, and a run folder with www/."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    files = dict(SITE)
    if old_catalog:
        files[CATALOG] = "old catalog"
        files[SIGNATURE] = "old signature"
    _write(seed, files)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "the live site")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    run = tmp_path / "run"
    _git(tmp_path, "clone", "-q", str(origin), str(run / "deploy"))
    # What the Anthill repo's www/ holds: the new catalog and signature plus a placeholder site.
    _write(
        run / "www",
        {
            CATALOG: "new catalog",
            SIGNATURE: "new signature",
            "index.html": "<html>placeholder</html>",
            "_headers": "/*\n  X-Test: 1",
            "functions/api/health.js": "// health",
            "README.md": "dev notes",
        },
    )
    return origin, run


def _publish(run):
    return subprocess.run(
        ["bash", "-c", _publish_script()],
        cwd=run,
        env=_env(identity=False),
        capture_output=True,
        text=True,
    )


def _in_origin(origin, path):
    return _git(origin, "show", f"main:{path}")


def _origin_files(origin):
    return set(_git(origin, "ls-tree", "-r", "--name-only", "main").splitlines())


@pytest.mark.parametrize("old_catalog", [False, True])
def test_publishing_adds_only_the_catalog_and_keeps_the_whole_site(tmp_path, old_catalog):
    origin, run = _world(tmp_path, old_catalog=old_catalog)
    done = _publish(run)
    assert done.returncode == 0, done.stdout + done.stderr
    for name, text in SITE.items():
        assert _in_origin(origin, name) == text, f"{name} was changed or removed"
    assert _in_origin(origin, CATALOG) == "new catalog"
    assert _in_origin(origin, SIGNATURE) == "new signature"
    assert _origin_files(origin) == set(SITE) | {CATALOG, SIGNATURE}, "something else was published"


def test_the_catalog_and_its_signature_go_in_one_commit_by_the_bot(tmp_path):
    origin, run = _world(tmp_path, old_catalog=False)
    before = _git(origin, "rev-list", "--count", "main")
    assert _publish(run).returncode == 0
    assert int(_git(origin, "rev-list", "--count", "main")) == int(before) + 1
    changed = _git(origin, "diff-tree", "--no-commit-id", "--name-status", "-r", "main")
    assert {line.split("\t")[1] for line in changed.splitlines()} == {CATALOG, SIGNATURE}
    assert _git(origin, "log", "-1", "--format=%an <%ae>|%s", "main") == (
        "anthill-catalog-bot <dev@onehill.org>|Publish signed model catalog"
    )


def test_nothing_is_pushed_when_the_deploy_repo_is_already_current(tmp_path):
    origin, run = _world(tmp_path, old_catalog=False)
    _write(run / "deploy", {CATALOG: "new catalog", SIGNATURE: "new signature"})
    _git(run / "deploy", "add", "-A")
    _git(run / "deploy", "commit", "-q", "-m", "already published")
    _git(run / "deploy", "push", "-q", "origin", "HEAD:main")
    before = _git(origin, "rev-parse", "main")
    done = _publish(run)
    assert done.returncode == 0 and "already up to date" in done.stdout
    assert _git(origin, "rev-parse", "main") == before


@pytest.mark.parametrize("damage", ["modify", "delete"])
def test_it_stops_before_pushing_if_anything_else_is_staged(tmp_path, damage):
    origin, run = _world(tmp_path, old_catalog=False)
    deploy = run / "deploy"
    if damage == "modify":
        (deploy / "index.html").write_text("replaced")
        _git(deploy, "add", "index.html")
    else:
        _git(deploy, "rm", "-q", "features.html")
    before = _git(origin, "rev-parse", "main")
    done = _publish(run)
    assert done.returncode != 0
    assert "Refusing to publish" in done.stdout + done.stderr
    assert _git(origin, "rev-parse", "main") == before, "a damaging change was pushed"


def test_the_step_does_not_mirror_a_folder_or_stage_everything():
    script = _publish_script()
    assert "rsync" not in script and "--delete" not in script
    assert "git add -A" not in script and "git add ." not in script
