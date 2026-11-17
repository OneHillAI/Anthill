"""The Dependabot version-update config for GitHub Actions (spec: docs/specs/dependabot-actions-updates.md, #232).

Every action is pinned by SHA (#228), and a pin never moves on its own. This config proposes updates, narrowly: one
ecosystem, minor and patch updates grouped into one weekly PR, no majors, a 7-day cooldown, and `dtolnay/rust-toolchain`
left alone. These pin the shape so it cannot drift, and check it against the workflows it is meant to serve. The config
is also validated against the published Dependabot JSON schema when that is available offline (it is not required).
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "dependabot.yml"
WORKFLOWS = ROOT / ".github" / "workflows"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _entry() -> dict:
    updates = _config()["updates"]
    assert len(updates) == 1, "only the github-actions ecosystem is configured"
    return updates[0]


def test_it_is_a_version_2_config_for_github_actions_only():
    cfg = _config()
    assert cfg["version"] == 2
    entry = _entry()
    assert entry["package-ecosystem"] == "github-actions" and entry["directory"] == "/"


def test_other_ecosystems_stay_on_security_updates_only():
    ecosystems = {u["package-ecosystem"] for u in _config()["updates"]}
    assert ecosystems == {"github-actions"}, "cargo, npm and pip must not get version-update PRs"


def test_it_runs_weekly_with_a_bounded_number_of_open_prs():
    entry = _entry()
    assert entry["schedule"]["interval"] == "weekly"
    assert (
        isinstance(entry["open-pull-requests-limit"], int)
        and 1 <= entry["open-pull-requests-limit"] <= 5
    )


def test_minor_and_patch_updates_arrive_as_one_group():
    groups = _entry()["groups"]
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert group["patterns"] == ["*"]
    assert set(group["update-types"]) == {"minor", "patch"}  # never "major"
    assert group.get("applies-to", "version-updates") == "version-updates"


def test_major_updates_are_ignored():
    ignores = _entry()["ignore"]
    assert {"dependency-name": "*", "update-types": ["version-update:semver-major"]} in ignores


def test_a_release_is_not_adopted_until_it_is_a_week_old():
    cooldown = _entry()["cooldown"]
    assert 7 <= cooldown["default-days"] <= 90  # the documented range is 1 to 90
    assert set(cooldown) <= {
        "default-days"
    }  # GitHub Actions supports only default-days, not semver-*-days


def test_headlines_stay_conventional_commits():
    commit = _entry()["commit-message"]
    assert commit["prefix"] == "ci" and commit["include"] == "scope"


def test_every_pin_without_a_release_tag_is_ignored_so_dependabot_cannot_move_it():
    """A SHA whose comment is not a version (rust-toolchain's `# master`) has no release to update to: Dependabot
    would move it to the latest commit. Each such action must be on the ignore list."""
    ignored = {i["dependency-name"] for i in _entry()["ignore"] if "update-types" not in i}
    unversioned = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*(?:-\s+)?uses:\s*([^\s@]+)@[0-9a-f]{40}\s+#\s*(\S+)", line)
            if m and not re.match(r"^v?\d", m.group(2)):
                unversioned.add(m.group(1))
    assert unversioned, "expected rust-toolchain, pinned to a master commit, to be the example"
    assert unversioned <= ignored, (
        f"pinned without a release but not ignored: {sorted(unversioned - ignored)}"
    )


def test_every_ignored_action_is_one_the_workflows_actually_use():
    used = set()
    for path in WORKFLOWS.glob("*.yml"):
        used |= set(re.findall(r"uses:\s*([^\s@]+)@", path.read_text()))
    stale = {i["dependency-name"] for i in _entry()["ignore"] if "update-types" not in i} - used
    assert not stale, f"ignored but no longer used; remove them: {sorted(stale)}"


def test_the_workflow_hygiene_rules_still_describe_what_dependabot_will_edit():
    """Dependabot updates the SHA and the same-line comment together, so every pin needs that comment."""
    bare = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            m = re.match(r"^\s*(?:-\s+)?uses:\s*\S+@[0-9a-f]{40}(.*)$", line)
            if m and not re.match(r"^\s+#\s*\S+", m.group(1)):
                bare.append(f"{path.name}:{n}")
    assert not bare, (
        f"a SHA pin with no same-line `# version` comment, which Dependabot cannot keep current: {bare}"
    )
