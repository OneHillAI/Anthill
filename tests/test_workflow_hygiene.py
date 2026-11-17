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

# `uses:` references allowed to stay on a tag or branch, per file. Empty: every action is pinned by full commit
# SHA, so a mutable tag can no longer change what a release or publish job runs. An exception goes here, with a
# reason in a comment, and is reviewed like any other change to this file.
UNPINNED_USES: dict[str, int] = {}
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


def test_every_action_is_pinned_by_full_commit_sha():
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
    assert not shrank, f"UNPINNED_USES lists exceptions that no longer exist; remove them: {shrank}"


def test_every_pin_says_which_version_it_is():
    """A bare SHA cannot be read or reviewed; the trailing comment names the release it is."""
    bare = []
    for path in FILES:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            m = re.match(r"^\s*(?:-\s+)?uses:\s*(\S+@[0-9a-f]{40})(.*)$", line)
            if m and not re.match(r"^\s+#\s*\S+", m.group(2)):
                bare.append(f"{path.name}:{n}")
    assert not bare, f"a SHA pin with no `# version` comment: {bare}"


def test_a_sha_pinned_rust_toolchain_names_its_toolchain():
    """The action reads the toolchain from the ref name (@stable). Pinned by SHA that is lost, and on the master
    commits the input is required, so every use must pass it."""
    uses = [
        (path.name, s)
        for path in FILES
        for s in _steps(path)
        if str(s.get("uses", "")).startswith("dtolnay/rust-toolchain@")
    ]
    assert uses, "expected the desktop workflows to use dtolnay/rust-toolchain"
    for name, step in uses:
        assert SHA.search(step["uses"]), (
            f"{name}: rust-toolchain must be pinned by SHA, not {step['uses']}"
        )
        assert (step.get("with") or {}).get("toolchain"), (
            f"{name}: a SHA-pinned rust-toolchain needs toolchain:"
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
