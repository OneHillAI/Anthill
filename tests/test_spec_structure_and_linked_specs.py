"""Spec structure at intake, and linked specs in the review data (spec: docs/specs/176-diff-gates-and-spec-structure.md).

1. A spec a PR ADDS must have a problem statement, a description of the change and a way to verify it, under
   the headings this project's specs use. Only new specs are checked (39 of the 214 specs in the tree would not
   pass, and editing one of those must not fail).
2. The spec lens could not see an existing spec the description only LINKS. linked-specs.sh now copies those
   from the trusted base checkout into the review data, and generic.sh puts them inside the fenced block.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GATE = str(ROOT / ".github" / "asdd" / "intake-check.sh")
LINKED = str(ROOT / ".github" / "asdd" / "linked-specs.sh")
GENERIC = str(ROOT / ".github" / "asdd" / "runtime" / "generic.sh")
GOOD = "# Spec: a thing\n\n## Problem\nIt is broken.\n\n## Requirements\n1. Fix it.\n\n## Acceptance\n- Works.\n"


def _workdir(tmp_path, specs=None):
    (tmp_path / "body.md").write_text("- [x] Authored by a **human**.\n")
    (tmp_path / "commits.txt").write_bytes(b"feat: a\n\nSigned-off-by: Ada <ada@x.io>\n\x00")
    (tmp_path / "labels.txt").write_text("chore\n")
    (tmp_path / "changed.txt").write_text("")
    (tmp_path / "meta.env").write_text("pr_number=7\nhead_sha=deadbeef\n")
    if specs is not None:
        d = tmp_path / "specs"
        d.mkdir()
        for name, text in specs.items():
            (d / name).write_text(text)
    return tmp_path


def _intake(workdir):
    out = workdir / "intake.json"
    subprocess.run(
        ["/bin/bash", GATE, str(workdir), str(out)], capture_output=True, text=True, cwd=str(ROOT)
    )
    return json.loads(out.read_text())


def test_a_well_formed_new_spec_passes(tmp_path):
    data = _intake(_workdir(tmp_path, {"thing.md": GOOD}))
    assert data["spec_structure_ok"] is True and data["passed"] is True


def test_each_missing_section_is_named_so_the_author_can_fix_it_in_one_pass(tmp_path):
    data = _intake(
        _workdir(tmp_path, {"thing.md": "# Spec\n\nJust some prose and a plan, no headings.\n"})
    )
    assert data["spec_structure_ok"] is False and data["passed"] is False
    text = " ".join(data["problems"])
    assert "docs/specs/thing.md" in text
    assert (
        "a problem statement" in text
        and "the change itself" in text
        and "a way to verify it" in text
    )


def test_only_the_missing_section_is_reported(tmp_path):
    no_verify = "## Problem\nx\n\n## Requirements\ny\n"
    data = _intake(_workdir(tmp_path, {"thing.md": no_verify}))
    text = " ".join(data["problems"])
    assert "a way to verify it" in text
    assert "a problem statement" not in text and "the change itself" not in text


def test_the_headings_this_project_actually_uses_are_accepted(tmp_path):
    variants = {
        "fix.md": "## Problem\na\n\n## Fix\nb\n\n## Verification\nc\n",
        "numbered.md": "## 1. Introduction\na\n\n## 2. Requirements\nb\n\n## 4. Tasks\nc\n",
        "h3.md": "### Background\na\n\n### Design\nb\n\n### Acceptance criteria\nc\n",
        "upper.md": "## PROBLEM\na\n\n## REQUIREMENTS\nb\n\n## ACCEPTANCE\nc\n",
    }
    data = _intake(_workdir(tmp_path, variants))
    assert data["spec_structure_ok"] is True, data["problems"]


def test_real_specs_from_this_repo_pass(tmp_path):
    names = [
        "159-chat-status-text.md",
        "windows-support.md",
        "settings-version-display.md",
        "chat-one-working-indicator.md",
        "ai-stack-guided-layout.md",
        "177-workflow-and-dependency-guards.md",
        "mandatory-spec-gate.md",
    ]
    specs = {
        n: (ROOT / "docs" / "specs" / n).read_text()
        for n in names
        if (ROOT / "docs/specs" / n).exists()
    }
    assert len(specs) >= 5
    data = _intake(_workdir(tmp_path, specs))
    assert data["spec_structure_ok"] is True, data["problems"]


def test_no_specs_directory_means_the_check_is_inert(tmp_path):
    data = _intake(_workdir(tmp_path))
    assert data["spec_structure_ok"] is True and data["passed"] is True


def test_one_bad_spec_among_good_ones_fails_and_names_only_it(tmp_path):
    data = _intake(_workdir(tmp_path, {"good.md": GOOD, "bad.md": "# Spec\nnothing here\n"}))
    text = " ".join(data["problems"])
    assert data["passed"] is False and "bad.md" in text and "good.md" not in text


def test_the_workflow_collects_only_added_plain_named_specs(tmp_path):
    """Run the workflow's own snippet in a scratch repo: an added spec is copied, an edited one is not."""
    doc = yaml.safe_load((ROOT / ".github/workflows/asdd-intake.yml").read_text())
    run = next(
        s["run"]
        for s in doc["jobs"]["intake"]["steps"]
        if "Collect PR content" in s.get("name", "")
    )
    lines = run.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "mkdir -p .asdd-work/specs")
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "done")
    snippet = "\n".join(lines[start : end + 1])

    def git(*a):
        return subprocess.run(
            ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", *a],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "docs/specs/sub").mkdir(parents=True)
    (tmp_path / "docs/specs/old.md").write_text("old\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "docs/specs/old.md").write_text("old edited\n")
    (tmp_path / "docs/specs/new.md").write_text(GOOD)
    (tmp_path / "docs/specs/sub/nested.md").write_text(GOOD)
    (tmp_path / "docs/specs/has space.md").write_text(GOOD)
    (tmp_path / "docs/other.md").write_text(GOOD)
    git("add", "-A")
    git("commit", "-qm", "pr")
    head = git("rev-parse", "HEAD")
    (tmp_path / ".asdd-work").mkdir()
    (tmp_path / ".asdd-work/changed.txt").write_text(
        git("diff", "--name-status", base + ".." + head) + "\n"
    )
    subprocess.run(
        ["bash", "-c", snippet], cwd=tmp_path, env={**os.environ, "HEAD_SHA": head}, check=True
    )
    assert sorted(p.name for p in (tmp_path / ".asdd-work/specs").iterdir()) == ["new.md"]


# --- linked-specs.sh -------------------------------------------------------------------------------


def _repo_with_specs(tmp_path, **files):
    for name, text in files.items():
        p = tmp_path / "docs" / "specs" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def _linked(tmp_path, body):
    (tmp_path / "body.md").write_text(body)
    out = tmp_path / "linked.md"
    r = subprocess.run(
        ["/bin/bash", LINKED, "body.md", str(out)], cwd=tmp_path, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    return out.read_text()


def test_an_existing_linked_spec_is_included_once(tmp_path):
    _repo_with_specs(tmp_path, **{"a.md": "SPEC-A\n"})
    out = _linked(tmp_path, "Spec: docs/specs/a.md and again docs/specs/a.md\n")
    assert out.count("### docs/specs/a.md") == 1 and "SPEC-A" in out


def test_only_a_plain_existing_child_of_docs_specs_can_be_named(tmp_path):
    _repo_with_specs(tmp_path, **{"a.md": "SPEC-A\n", "sub/c.md": "NESTED\n"})
    (tmp_path / "README.md").write_text("SECRET-README\n")
    (tmp_path / "docs" / "specs" / "link.md").symlink_to(tmp_path / "README.md")
    body = (
        "docs/specs/../../README.md docs/specs/nope.md docs/specs/sub/c.md docs/specs/link.md "
        "docs/specs/a.md/../../../README.md /etc/passwd docs/specs/a.md"
    )
    out = _linked(tmp_path, body)
    assert "SPEC-A" in out
    for forbidden in ("SECRET-README", "NESTED", "nope.md", "passwd", "link.md"):
        assert forbidden not in out


def test_each_spec_and_the_total_are_capped(tmp_path):
    _repo_with_specs(tmp_path, **{f"s{i}.md": f"S{i}\n" + "x" * 40000 for i in range(4)})
    out = _linked(tmp_path, " ".join(f"docs/specs/s{i}.md" for i in range(4)))
    assert len(out.encode()) < 62000
    assert out.count("### docs/specs/") == 2  # 30000 each: a third would pass the 60000 total


def test_no_reference_or_no_body_gives_an_empty_file(tmp_path):
    _repo_with_specs(tmp_path, **{"a.md": "A\n"})
    assert _linked(tmp_path, "no spec reference here") == ""
    out = tmp_path / "out.md"
    r = subprocess.run(
        ["/bin/bash", LINKED, "missing.md", str(out)], cwd=tmp_path, capture_output=True
    )
    assert r.returncode == 0 and out.read_text() == ""


# --- the review prompt -----------------------------------------------------------------------------


def _prompt(tmp_path, linked=None):
    work = tmp_path / "work"
    work.mkdir()
    (work / "meta.env").write_text(
        "pr_number=7\nbase_sha=" + "b" * 40 + "\nhead_sha=" + "a" * 40 + "\n"
    )
    (work / "title.txt").write_text("a title")
    (work / "body.md").write_text("a body")
    (work / "changes.diff").write_text("diff --git a/x b/x\n")
    if linked is not None:
        (work / "linked-specs.md").write_text(linked)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ASDD_")}
    env.update(ASDD_WORKDIR=str(work), ASDD_OUT=str(tmp_path / "review.json"), ASDD_ROOT=str(ROOT))
    r = subprocess.run(["bash", GENERIC], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return (work / "prompt-main.txt").read_text()


def test_linked_specs_go_inside_the_fenced_untrusted_block(tmp_path):
    prompt = _prompt(tmp_path, linked="### docs/specs/a.md\n```markdown\nSPEC-A-TEXT\n```\n")
    fence = re.search(r"<<<(ASDD_UNTRUSTED_DATA_[0-9a-f]+)", prompt).group(1)
    inside = prompt.split(f"<<<{fence}")[1].split(fence)[0]
    assert "## Linked specs" in inside and "SPEC-A-TEXT" in inside
    assert "SPEC-A-TEXT" not in prompt.split(f"<<<{fence}")[0]  # never in the trusted instructions


def test_without_linked_specs_the_prompt_has_no_such_section(tmp_path):
    header = "## Linked specs (existing specs"  # the data section; the lens instruction only mentions the name
    assert header not in _prompt(tmp_path)
    shutil.rmtree(tmp_path / "work")
    assert header not in _prompt(tmp_path, linked="")


def test_the_spec_lens_is_told_where_linked_specs_appear():
    text = (ROOT / ".github/asdd/agents/review-spec.md").read_text()
    assert 'under "Linked specs"' in text


def _collect_snippet():
    doc = yaml.safe_load((ROOT / ".github/workflows/pr-review.yml").read_text())
    run = next(
        s["run"]
        for s in doc["jobs"]["review"]["steps"]
        if "Collect PR content" in s.get("name", "")
    )
    lines = run.splitlines()
    start = next(
        i
        for i, ln in enumerate(lines)
        if ln.strip().startswith("if [ -f .github/asdd/linked-specs.sh")
    )
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start : end + 1])


def test_the_review_workflow_collects_them_from_the_base_checkout():
    text = (ROOT / ".github/workflows/pr-review.yml").read_text()
    assert "bash .github/asdd/linked-specs.sh .asdd-work/body.md .asdd-work/linked-specs.md" in text


def test_the_collection_step_is_inert_when_the_script_is_not_on_the_base_branch_yet(tmp_path):
    """The PR that ADDS linked-specs.sh runs against a base checkout without it: the step must not fail."""
    (tmp_path / ".asdd-work").mkdir()
    (tmp_path / ".asdd-work/body.md").write_text("Spec: docs/specs/a.md\n")
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _collect_snippet()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (
        r.stderr
    )  # no .github/asdd/linked-specs.sh here: nothing to run, nothing fails
    assert not (tmp_path / ".asdd-work/linked-specs.md").exists()


def test_the_collection_step_runs_the_script_once_it_exists(tmp_path):
    shutil.copytree(
        ROOT / ".github/asdd",
        tmp_path / ".github/asdd",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / "docs/specs").mkdir(parents=True)
    (tmp_path / "docs/specs/a.md").write_text("SPEC-A\n")
    (tmp_path / ".asdd-work").mkdir()
    (tmp_path / ".asdd-work/body.md").write_text("Spec: docs/specs/a.md\n")
    r = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", _collect_snippet()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "SPEC-A" in (tmp_path / ".asdd-work/linked-specs.md").read_text()
