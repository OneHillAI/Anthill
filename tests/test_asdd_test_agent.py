"""The post-merge test agent runner reports what actually happened.

Runs the real `.github/asdd/operate/test.sh` in an isolated copy of the files it needs, with only the
`goose` command stubbed (no network, no model). Pins that:

- a PASS and a FAIL are reported from the agent's structured result, with counts and failing cases;
- a run that left no result says it did not run to completion, with the model key redacted;
- an unwired or Goose-less runner says "NO TEST AGENT RAN" and never reads like a result;
- the roster's test_runner model, and the per-role endpoint and key, are what the agent runs on;
- either spelling of the endpoint URL reaches Goose as host + the full chat-completions path
  (the kit's template, and the docs agent before it, sent Goose a bare /v1 and got a 404);
- the recipe's parameter is the one the runner passes (the kit's template passed the wrong name).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_FILES = [
    ".github/asdd/operate/test.sh",
    "cli/audit.py",
    "cli/operate-guard.py",
    "cli/operate-run.py",
    "cli/resolve-model.sh",
    "recipes/test-runner.yaml",
]

_ASDD_YML = """models:
  developer: "dev-model"
  test_runner: "runner-model"
"""


@pytest.fixture
def repo(tmp_path):
    """An isolated tree holding just what test.sh needs."""
    r = tmp_path / "repo"
    for f in _FILES:
        dst = r / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    (r / ".asdd.yml").write_text(_ASDD_YML)
    return r


def _goose(repo: Path, body: str) -> Path:
    """Put a stub `goose` first on PATH. It logs its argv and provider env, then runs `body`."""
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


def _result(repo: Path, obj: dict) -> str:
    """goose-stub body that writes the agent's structured result file."""
    p = repo / "result.json"
    p.write_text(json.dumps(obj))
    return f'mkdir -p .asdd-work && cp "{p}" .asdd-work/operate-result.json\n'


def _run(repo: Path, extra_env=None, path_dirs=()):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ASDD_", "OPENAI_"))  # a clean slate: only what the test sets
    }
    env["PATH"] = ":".join([*(str(p) for p in path_dirs), env["PATH"]])
    env.update(extra_env or {})
    out = repo / "report.md"
    r = subprocess.run(
        ["bash", str(repo / ".github/asdd/operate/test.sh"), "abc123", str(out)],
        env=env,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    return r, out.read_text() if out.exists() else ""


_WIRED = {
    "ASDD_MODEL_URL": "https://api.example.ai/v1/chat/completions",
    "ASDD_RUNTIME_TOKEN": "sekret-key-123",
}


def test_a_pass_is_reported_from_the_structured_result(repo):
    body = _result(
        repo,
        {
            "verdict": "pass",
            "reasoning": "all green",
            "payload": {"tested": "pytest", "passed": 2849, "failed": 0, "failing": []},
        },
    )
    r, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "result for `abc123`" in report
    assert "**PASS**" in report
    assert "Passed: 2849 / Failed: 0" in report
    assert "runner-model" in report  # the roster's test_runner model, named in the report


def test_a_fail_lists_the_failing_cases(repo):
    body = _result(
        repo,
        {
            "verdict": "fail",
            "reasoning": "two regressions",
            "payload": {
                "tested": "pytest",
                "passed": 10,
                "failed": 2,
                "failing": ["tests/test_a.py::t1", "tests/test_b.py::t2"],
            },
        },
    )
    _, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert "**FAIL**" in report
    assert "`tests/test_a.py::t1`" in report and "`tests/test_b.py::t2`" in report


def test_an_unusable_verdict_is_not_called_a_pass(repo):
    body = _result(repo, {"verdict": "yolo", "payload": {}})
    _, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert "NO VERDICT" in report
    assert "**PASS**" not in report


def test_a_run_with_no_result_says_it_did_not_complete_and_redacts_the_key(repo):
    body = 'echo "401 Unauthorized for key sekret-key-123" >&2\nexit 3\n'
    r, report = _run(repo, _WIRED, [_goose(repo, body)])
    assert r.returncode == 0  # advisory: the workflow still posts the report
    assert "did not run to completion" in report
    assert "goose exit code 3" in report
    assert "[redacted]" in report and "sekret-key-123" not in report
    assert "PASS" not in report


def test_an_unwired_runner_says_no_test_agent_ran(repo):
    r, report = _run(repo, {}, [_goose(repo, "exit 0\n")])
    assert r.returncode == 0
    assert "NO TEST AGENT RAN" in report
    assert "not wired" in report
    assert not (repo / "goose.argv").exists()  # Goose was never started


def test_a_missing_goose_says_no_test_agent_ran(repo):
    # The shared test PATH may hold a real goose; point PATH at a dir with only the essentials.
    bare = repo / "bare"
    bare.mkdir()
    for tool in (
        "bash",
        "python3",
        "dirname",
        "cat",
        "grep",
        "tail",
        "printf",
        "mkdir",
        "env",
        "sed",
        "awk",
        "tr",
    ):
        src = shutil.which(tool)
        if src:
            (bare / tool).symlink_to(src)
    env = {"PATH": str(bare), **_WIRED}
    out = repo / "report.md"
    r = subprocess.run(
        [str(bare / "bash"), str(repo / ".github/asdd/operate/test.sh"), "abc123", str(out)],
        env=env,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NO TEST AGENT RAN" in out.read_text()
    assert "Goose is not installed" in out.read_text()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.ai/v1",  # the base form: Goose used to get BASE_PATH=v1 and 404
        "https://api.example.ai/v1/",
        "https://api.example.ai/v1/chat/completions",
    ],
)
def test_goose_always_gets_host_and_the_full_chat_completions_path(repo, url):
    body = _result(repo, {"verdict": "pass", "payload": {}})
    _run(repo, {**_WIRED, "ASDD_MODEL_URL": url}, [_goose(repo, body)])
    env = (repo / "goose.env").read_text()
    assert "HOST=https://api.example.ai\n" in env
    assert "PATH=v1/chat/completions\n" in env


def test_the_runner_passes_the_recipes_own_parameter_and_the_roster_model(repo):
    """The kit's template passed `change_ref`; the recipe's parameter is `pr`, so the run would fail."""
    recipe = (repo / "recipes/test-runner.yaml").read_text()
    assert "key: pr" in recipe
    body = _result(repo, {"verdict": "pass", "payload": {}})
    _run(repo, _WIRED, [_goose(repo, body)])
    argv = (repo / "goose.argv").read_text().split("\n")
    assert argv[argv.index("--params") + 1] == "pr=abc123"
    assert argv[argv.index("--model") + 1] == "runner-model"
    assert argv[argv.index("--provider") + 1] == "openai"


def test_the_per_role_endpoint_and_key_win_over_the_shared_pair(repo):
    body = _result(repo, {"verdict": "pass", "payload": {}})
    env = {
        **_WIRED,
        "ASDD_MODEL_URL__TEST_RUNNER": "https://runner.example.ai/v1",
        "ASDD_RUNTIME_TOKEN__TEST_RUNNER": "runner-key-456",
    }
    _run(repo, env, [_goose(repo, body)])
    seen = (repo / "goose.env").read_text()
    assert "HOST=https://runner.example.ai\n" in seen
    assert "KEY=runner-key-456\n" in seen


def test_a_real_run_leaves_one_audit_record_and_a_dry_run_leaves_one_too(repo):
    body = _result(repo, {"verdict": "pass", "payload": {"tested": "pytest"}})
    _run(repo, _WIRED, [_goose(repo, body)])
    ledger = (repo / ".asdd-work/audit.jsonl").read_text().strip().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["agent"]["role"] == "test-runner"

    shutil.rmtree(repo / ".asdd-work")
    _run(repo, {}, [_goose(repo, "exit 0\n")])  # unwired
    ledger = (repo / ".asdd-work/audit.jsonl").read_text().strip().splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["agent"]["role"] == "test-runner"


def test_the_workflow_only_runs_on_main_and_never_on_pull_requests():
    text = (ROOT / ".github/workflows/asdd-test.yml").read_text()
    on = text.split("\njobs:")[0]
    assert "push:" in on and "branches: [main]" in on
    assert "pull_request" not in on  # the tester has a shell and the key: merged code only
    assert "git merge-base --is-ancestor" in text  # a hand-started run must name a commit on main
