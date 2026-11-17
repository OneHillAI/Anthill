"""Mechanical workflow checks that used to rest on the AI security lens alone.

Two drifts are guarded across every file in .github/workflows/:

* an attacker-controlled GitHub context expanded straight into a `run:` script (script injection), and
  any other `${{ }}` in a `run:` that is not on the reviewed list below;
* a `uses:` reference pinned to a movable tag or branch instead of a full commit SHA.

The tag-pinned references that exist today are grandfathered per file and can only go down, so a new
workflow, or a new step in an old one, has to pin by SHA. Model-free and offline.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
FILES = sorted(WORKFLOWS.glob("*.yml"))

EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")

# Contexts a stranger controls (PR and issue text, branch names, commit and comment bodies). Never
# acceptable inside `run:`; pass them through `env:` and read the variable instead.
UNTRUSTED = re.compile(
    r"github\.(head_ref|event\.(issue|pull_request|comment|review|review_comment|discussion|pages|commits|"
    r"head_commit|workflow_run)\b[^\s]*\.(title|body|head\.ref|head\.label|default_branch|message|name|email|"
    r"page_name|head_branch)|event\.(issue|pull_request)\.head\.repo\.\w+)"
)

# Other expressions in `run:` that were reviewed and are safe: tag pushes by maintainers, the PR's base
# branch, a boolean dispatch input, and step outputs. (file, expression) -> occurrences.
REVIEWED_RUN_EXPRESSIONS = {
    ("desktop-promote.yml", "steps.resolve.outputs.beta_sha"): 1,
    ("desktop-promote.yml", "steps.resolve.outputs.release_sha"): 1,
    ("desktop-release.yml", "github.ref_name"): 1,
    ("pr-validation.yml", "github.base_ref"): 1,
    ("refresh-model-catalog.yml", "inputs.force_publish"): 1,
    ("release.yml", "github.ref_name"): 2,
}

# `uses:` references still on a tag or branch, per file. Lower a number (or drop the entry) when you pin
# one by SHA; the test fails if the number is higher than the file's real count or lower than it.
UNPINNED_USES = {
    "asdd-automerge.yml": 1,
    "backend-image.yml": 3,
    "desktop-beta-windows.yml": 6,
    "desktop-beta.yml": 6,
    "desktop-promote.yml": 2,
    "desktop-release-windows.yml": 5,
    "desktop-release.yml": 6,
    "docs-deploy.yml": 2,
    "pr-validation.yml": 2,
    "refresh-model-catalog.yml": 1,
    "release.yml": 3,
    "security-audit.yml": 3,
    "trainer-image.yml": 3,
    "windows-ci.yml": 11,
}
SHA = re.compile(r"@[0-9a-f]{40}$")


def _steps(path: Path):
    doc = yaml.safe_load(path.read_text())
    for job in (doc.get("jobs") or {}).values():
        yield from job.get("steps") or []


def test_workflows_were_found():
    assert len(FILES) >= 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_untrusted_context_in_run_scripts(path):
    bad = [
        expr
        for step in _steps(path)
        for expr in EXPR.findall(step.get("run") or "")
        if UNTRUSTED.search(expr)
    ]
    assert not bad, (
        f"{path.name}: attacker-controlled {bad} inside run:; pass it through env: instead"
    )


def test_run_expressions_match_reviewed_list():
    seen: dict[tuple[str, str], int] = {}
    for path in FILES:
        for step in _steps(path):
            for expr in EXPR.findall(step.get("run") or ""):
                seen[(path.name, expr)] = seen.get((path.name, expr), 0) + 1
    new = {k: v for k, v in seen.items() if v > REVIEWED_RUN_EXPRESSIONS.get(k, 0)}
    assert not new, (
        "new ${{ }} expression inside a run: script. Pass it through env: and read the variable; if it is "
        f"safe by construction, add it to REVIEWED_RUN_EXPRESSIONS with a reason: {new}"
    )
    stale = {k: v for k, v in REVIEWED_RUN_EXPRESSIONS.items() if seen.get(k, 0) < v}
    assert not stale, f"reviewed expressions that no longer exist; remove them: {stale}"


def test_unpinned_uses_only_go_down():
    seen = {}
    for path in FILES:
        refs = [s["uses"] for s in _steps(path) if s.get("uses")]
        count = sum(1 for u in refs if not u.startswith(("./", "docker://")) and not SHA.search(u))
        if count:
            seen[path.name] = count
    grew = {f: (n, UNPINNED_USES.get(f, 0)) for f, n in seen.items() if n > UNPINNED_USES.get(f, 0)}
    assert not grew, (
        f"actions pinned to a tag or branch; pin them by full commit SHA (now, allowed): {grew}"
    )
    shrank = {f: (seen.get(f, 0), n) for f, n in UNPINNED_USES.items() if seen.get(f, 0) < n}
    assert not shrank, (
        f"fewer tag-pinned actions than the baseline; lower UNPINNED_USES (now, baseline): {shrank}"
    )


def test_injection_pattern_catches_the_classic_cases():
    for expr in (
        "github.head_ref",
        "github.event.pull_request.title",
        "github.event.pull_request.head.ref",
        "github.event.issue.body",
        "github.event.comment.body",
        "github.event.head_commit.message",
        "github.event.commits[0].message",
        "github.event.workflow_run.head_branch",
    ):
        assert UNTRUSTED.search(expr), expr
    for expr in (
        "github.base_ref",
        "github.ref_name",
        "secrets.X",
        "steps.a.outputs.b",
        "inputs.force_publish",
    ):
        assert not UNTRUSTED.search(expr), expr
