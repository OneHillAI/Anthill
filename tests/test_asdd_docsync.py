"""The documentation agent proposes paste-ready docs and checks them; a run that did not happen says so.

Two layers, both with no network and no model:

- the renderer (`docsync-render.py`) on its own: the heading uses the real PR number, title and merge date;
  the entry is checked against the project's rules; a changelog fragment is wanted only when product code
  changed; a doc edit is kept only if its sentence is really in the file; numbers the PR never mentions are
  flagged; en and em dashes become hyphens;
- the real runner (`docsync.sh`) in an isolated git repo with only `goose` stubbed: the roster model, the
  recipe parameters, either spelling of the endpoint URL, the per-role endpoint and key, one audit record per
  run, and honest reports when the agent is not wired, absent, or leaves no usable result.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_FILES = [
    ".github/asdd/operate/docsync.sh",
    ".github/asdd/operate/docsync-render.py",
    "cli/audit.py",
    "cli/operate-guard.py",
    "cli/operate-run.py",
    "cli/resolve-model.sh",
    "recipes/documentation.yaml",
]

_WIRED = {
    "ASDD_MODEL_URL": "https://api.example.ai/v1/chat/completions",
    "ASDD_RUNTIME_TOKEN": "sekret-key-123",
}

_IMPACT_LOG = "# System Impact Log\n\n## 2026-09\n\n### An older entry\n**System impact:** older.\n"
# Built from code points so this file never contains the characters the style gate bans.
EN_DASH, EM_DASH = chr(0x2013), chr(0x2014)
_GUIDE = "# Guide\n\nFolders group your chats.\n"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """An isolated git repo holding what docsync.sh needs, plus one change touching product code."""
    r = tmp_path / "repo"
    for f in _FILES:
        dst = r / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    (r / ".asdd.yml").write_text('models:\n  documentation: "doc-model"\n')
    (r / "docs").mkdir(exist_ok=True)
    (r / "docs/SYSTEM_IMPACT_LOG.md").write_text(_IMPACT_LOG)
    (r / "docs/guide.md").write_text(_GUIDE)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    (r / "anthill/web").mkdir(parents=True)
    (r / "anthill/web/x.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "change")
    (r / ".asdd-work").mkdir()
    (r / ".asdd-work/pr.json").write_text(
        json.dumps(
            {
                "number": 58,
                "title": "Consolidate chat folders into projects",
                "merged_at": "2026-10-03T09:38:51Z",
            }
        )
    )
    (r / ".asdd-work/pr-body.md").write_text(
        "Folders become projects. Migration 5 clears folder_id."
    )
    return r


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def _goose(repo: Path, body: str) -> Path:
    d = repo / "stubbin"
    d.mkdir(exist_ok=True)
    g = d / "goose"
    g.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{repo}/goose.argv"\n'
        f'printf "HOST=%s\\nPATH=%s\\nKEY=%s\\n" "$OPENAI_HOST" "$OPENAI_BASE_PATH" "$OPENAI_API_KEY" > "{repo}/goose.env"\n'
        + body
    )
    g.chmod(0o755)
    return d


def _result(repo: Path, payload: dict) -> str:
    p = repo / "result.json"
    p.write_text(json.dumps({"action": "docs.propose", "verdict": "proposed", "payload": payload}))
    return f'mkdir -p .asdd-work && cp "{p}" .asdd-work/operate-result.json\n'


_GOOD = {
    "impact": {
        "system_impact": "Chats are now grouped by project, not by folder.",
        "surface": "`anthill/web/x.py`",
        "user_visible": "yes: folders are gone",
        "footprint": "refactor; removes the Folder model",
    },
    "changelog": {"category": "changed", "text": "Chats are now grouped by project."},
    "doc_edits": [
        {
            "path": "docs/guide.md",
            "sentence": "Folders group your chats.",
            "fix": "Projects group your chats.",
        }
    ],
}


def _run(repo: Path, extra_env=None, path_dirs=(), ref=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ASDD_", "OPENAI_"))}
    env["PATH"] = ":".join([*(str(p) for p in path_dirs), env["PATH"]])
    env.update(extra_env or {})
    out = repo / "report.md"
    r = subprocess.run(
        ["bash", str(repo / ".github/asdd/operate/docsync.sh"), ref or _head(repo), str(out)],
        env=env,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    return r, out.read_text() if out.exists() else ""


# --- the runner -------------------------------------------------------------------------------------


def test_a_good_proposal_is_laid_out_with_the_real_pr_facts(repo):
    r, report = _run(repo, _WIRED, [_goose(repo, _result(repo, _GOOD))])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "proposed doc updates for PR #58" in report
    assert "### PR #58 - Consolidate chat folders into projects - merged 2026-10-03" in report
    assert "directly under the `## 2026-09` heading" in report
    assert "Create `changelog.d/58.changed.md`" in report
    assert "In `docs/guide.md`, replace:" in report and "Projects group your chats." in report
    assert "doc-model" in report  # the roster's documentation model
    assert "Check before pasting" not in report


def test_the_runner_passes_the_recipes_parameters_and_the_roster_model(repo):
    _run(repo, _WIRED, [_goose(repo, _result(repo, _GOOD))])
    argv = (repo / "goose.argv").read_text().split("\n")
    params = [argv[i + 1] for i, a in enumerate(argv) if a == "--params"]
    assert f"change_ref={_head(repo)}" in params and "instructed_by=asdd-docsync" in params
    assert argv[argv.index("--model") + 1] == "doc-model"
    assert argv[argv.index("--provider") + 1] == "openai"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.ai/v1",  # the bare base: Goose used to get BASE_PATH=v1 and 404
        "https://api.example.ai/v1/",
        "https://api.example.ai/v1/chat/completions",
    ],
)
def test_goose_always_gets_host_and_the_full_chat_completions_path(repo, url):
    _run(repo, {**_WIRED, "ASDD_MODEL_URL": url}, [_goose(repo, _result(repo, _GOOD))])
    env = (repo / "goose.env").read_text()
    assert "HOST=https://api.example.ai\n" in env and "PATH=v1/chat/completions\n" in env


def test_the_per_role_endpoint_and_key_win_over_the_shared_pair(repo):
    env = {
        **_WIRED,
        "ASDD_MODEL_URL__DOCUMENTATION": "https://docs.example.ai/v1",
        "ASDD_RUNTIME_TOKEN__DOCUMENTATION": "docs-key-9",
    }
    _run(repo, env, [_goose(repo, _result(repo, _GOOD))])
    seen = (repo / "goose.env").read_text()
    assert "HOST=https://docs.example.ai\n" in seen and "KEY=docs-key-9\n" in seen


def test_a_run_with_no_result_says_it_did_not_complete_and_redacts_the_key(repo):
    body = 'echo "401 Unauthorized for key sekret-key-123" >&2\nexit 3\n'
    r, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert r.returncode == 0  # advisory: the workflow still posts the report
    assert "did not run to completion" in report and "goose exit code 3" in report
    assert "[redacted]" in report and "sekret-key-123" not in report
    assert "Wire the model" not in report  # it IS wired


def test_a_result_with_no_usable_impact_entry_is_reported_not_rendered(repo):
    body = _result(repo, {"impact": {"system_impact": "only one field"}})
    _, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert "did not run to completion" in report and "no usable impact-log entry" in report
    assert "### PR #58" not in report


def test_an_unwired_runner_says_no_documentation_agent_ran(repo):
    r, report = _run(repo, {}, [_goose(repo, "exit 0\n")])
    assert r.returncode == 0
    assert "NO DOCUMENTATION AGENT RAN" in report and "not wired" in report
    assert not (repo / "goose.argv").exists()  # Goose was never started


def test_a_missing_goose_says_no_documentation_agent_ran(repo):
    bare = repo / "bare"
    bare.mkdir()
    for tool in (
        "bash",
        "python3",
        "git",
        "dirname",
        "cat",
        "grep",
        "tail",
        "head",
        "printf",
        "mkdir",
        "mv",
        "rm",
        "env",
        "sed",
        "awk",
        "tr",
        "date",
    ):
        src = shutil.which(tool)
        if src:
            (bare / tool).symlink_to(src)
    out = repo / "report.md"
    r = subprocess.run(
        [str(bare / "bash"), str(repo / ".github/asdd/operate/docsync.sh"), _head(repo), str(out)],
        env={"PATH": str(bare), **_WIRED},
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (
        "NO DOCUMENTATION AGENT RAN" in out.read_text()
        and "Goose is not installed" in out.read_text()
    )


def test_every_run_leaves_exactly_one_audit_record(repo):
    _run(repo, _WIRED, [_goose(repo, _result(repo, _GOOD))])
    ledger = (repo / ".asdd-work/audit.jsonl").read_text().strip().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["agent"]["role"] == "documentation"
    shutil.rmtree(repo / ".asdd-work/audit.jsonl", ignore_errors=True)
    (repo / ".asdd-work/audit.jsonl").unlink()
    _run(repo, {}, [_goose(repo, "exit 0\n")])  # unwired: still one record
    ledger = (repo / ".asdd-work/audit.jsonl").read_text().strip().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["agent"]["role"] == "documentation"


# --- the renderer on its own -------------------------------------------------------------------------


def _renderer():
    spec = importlib.util.spec_from_file_location(
        "docsync_render", ROOT / ".github/asdd/operate/docsync-render.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(
    tmp_path,
    *,
    corpus="Folders become projects. Migration 5.",
    changed=("anthill/web/x.py",),
    log=_IMPACT_LOG,
    root=None,
):
    (tmp_path / "corpus.txt").write_text(corpus)
    (tmp_path / "changed.txt").write_text("\n".join(changed))
    (tmp_path / "log.md").write_text(log)
    return argparse.Namespace(
        ref="abcdef1234",
        pr="58",
        title="Consolidate chat folders into projects",
        date="2026-10-03",
        corpus=str(tmp_path / "corpus.txt"),
        changed=str(tmp_path / "changed.txt"),
        impact_log=str(tmp_path / "log.md"),
        model="m",
        root=str(root or tmp_path),
    )


def test_invented_numbers_and_a_bad_footprint_label_are_flagged(tmp_path):
    mod = _renderer()
    payload = {
        "impact": {
            "system_impact": "4 tests were updated and 2 browser tests added.",
            "surface": "`anthill/web/x.py`",
            "user_visible": "maybe",
            "footprint": "reductive; removes things",
        }
    }
    out = mod.render(payload, _args(tmp_path))
    assert "### Check before pasting" in out
    assert "may have invented them: 2, 4" in out
    assert (
        "footprint line must start with one of" in out and "should start with 'yes' or 'no'" in out
    )


def test_a_number_the_pr_does_mention_is_not_flagged(tmp_path):
    mod = _renderer()
    payload = {"impact": {**_GOOD["impact"], "system_impact": "Migration 5 clears folder_id."}}
    assert "invented" not in mod.render(payload, _args(tmp_path))


def test_a_changelog_fragment_is_only_wanted_when_product_code_changed(tmp_path):
    mod = _renderer()
    docs_only = mod.render(_GOOD, _args(tmp_path, changed=("docs/guide.md", "tests/test_x.py")))
    assert "No changelog fragment needed" in docs_only and "changelog.d/58" not in docs_only
    product = mod.render(_GOOD, _args(tmp_path))
    assert "`changelog.d/58.changed.md`" in product
    no_fragment = mod.render({"impact": _GOOD["impact"]}, _args(tmp_path))
    assert "did not propose a usable one" in no_fragment
    bad_cat = mod.render({**_GOOD, "changelog": {"category": "yolo", "text": "x"}}, _args(tmp_path))
    assert "did not propose a usable one" in bad_cat


def test_a_changelog_marker_is_stripped_and_flagged(tmp_path):
    mod = _renderer()
    payload = {**_GOOD, "changelog": {"category": "added", "text": "## Heading text here"}}
    out = mod.render(payload, _args(tmp_path))
    assert "Heading text here" in out and "## Heading text here" not in out
    assert "stripped" in out


def test_a_doc_edit_is_kept_only_if_its_sentence_is_in_the_file(tmp_path):
    mod = _renderer()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").write_text(_GUIDE)
    good = {
        **_GOOD,
        "doc_edits": [
            {"path": "docs/guide.md", "sentence": "Folders group your chats.", "fix": "Projects."}
        ],
    }
    assert "In `docs/guide.md`, replace:" in mod.render(good, _args(tmp_path))
    bad = {
        **_GOOD,
        "doc_edits": [
            {"path": "docs/guide.md", "sentence": "A sentence that is not there.", "fix": "x"}
        ],
    }
    out = mod.render(bad, _args(tmp_path))
    assert "In `docs/guide.md`" not in out and "Dropped a proposed edit" in out
    missing = {**_GOOD, "doc_edits": [{"path": "docs/nope.md", "sentence": "x", "fix": "y"}]}
    assert "Dropped a proposed edit" in mod.render(missing, _args(tmp_path))
    escape = {**_GOOD, "doc_edits": [{"path": "../etc/passwd.md", "sentence": "x", "fix": "y"}]}
    out = mod.render(escape, _args(tmp_path))
    assert "replace:" not in out and "not inside the repository" in out


def test_en_and_em_dashes_become_hyphens_and_text_is_capped(tmp_path):
    mod = _renderer()
    payload = {
        "impact": {
            **_GOOD["impact"],
            "system_impact": "One " + EN_DASH + " two " + EM_DASH + " three. " + "x" * 5000,
        }
    }
    out = mod.render(payload, _args(tmp_path))
    assert EN_DASH not in out and EM_DASH not in out
    assert "One - two - three." in out and len(out) < 4000


def test_the_entry_goes_under_the_newest_month_heading(tmp_path):
    mod = _renderer()
    log = "# Log\n\n## 2026-10\n\n### new\n\n## 2026-09\n\n### old\n"
    assert "directly under the `## 2026-10` heading" in mod.render(_GOOD, _args(tmp_path, log=log))


def test_a_result_with_no_usable_entry_renders_nothing(tmp_path):
    mod = _renderer()
    assert mod.render({"impact": {"system_impact": "only this"}}, _args(tmp_path)) is None
    assert mod.render({}, _args(tmp_path)) is None


def test_the_workflow_runs_after_merge_only_and_passes_the_role_settings():
    text = (ROOT / ".github/workflows/asdd-docsync.yml").read_text()
    on = text.split("\npermissions:")[0]
    assert "push:" in on and "branches: [main]" in on and "workflow_dispatch:" in on
    assert "pull_request" not in on  # the agent has a shell and the key: merged code only
    assert "git merge-base --is-ancestor" in text
    assert "ASDD_RUNTIME_TOKEN__DOCUMENTATION" in text and "ASDD_MODEL_URL__DOCUMENTATION" in text
