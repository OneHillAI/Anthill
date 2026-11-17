"""The mypy grandfather list in pyproject.toml can only shrink.

`[[tool.mypy.overrides]] ignore_errors = true` exempts modules that carry old type debt, with the note
"remove a module from this list once it is annotated". Nothing stopped a PR from adding a module instead,
and a renamed module would silently fall off the list and back under strict checking, or a typo would
exempt nothing. This pins the list to today's modules.
"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest already pulls tomli there
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

# Remove a line here in the same PR that removes the module from pyproject.toml. Never add one.
GRANDFATHERED = {
    "anthill.web.app",
    "anthill.web.agents",
    "anthill.web.snippets",
    "anthill.web.scheduler",
    "anthill.web.provision_run",
    "anthill.web.mcp_store",
    "anthill.web.knowledge_registry",
    "anthill.agent.executor",
    "anthill.agent.tools",
    "anthill.training.export",
    "anthill.training.collect",
    "anthill.training.executor",
    "anthill.multimodal.files",
    "anthill.search.web",
    "anthill.cloud.aws",
    "anthill.lifecycle.renovate",
    "anthill.mcp.server",
    "anthill.cli",
}


def _ignored_modules() -> set[str]:
    overrides = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mypy"]["overrides"]
    return {m for o in overrides if o.get("ignore_errors") for m in o["module"]}


def test_no_module_is_added_to_the_ignore_list():
    added = _ignored_modules() - GRANDFATHERED
    assert not added, (
        f"{sorted(added)} would skip mypy. Annotate the module instead; the list only shrinks "
        "(see the comment above [[tool.mypy.overrides]] in pyproject.toml)"
    )


def test_baseline_is_lowered_when_a_module_leaves_the_list():
    gone = GRANDFATHERED - _ignored_modules()
    assert not gone, f"{sorted(gone)} left the mypy ignore list; remove them from GRANDFATHERED too"


def test_every_ignored_module_exists():
    missing = [
        m
        for m in sorted(_ignored_modules())
        if not (ROOT / (m.replace(".", "/") + ".py")).exists()
        and not (ROOT / m.replace(".", "/") / "__init__.py").exists()
    ]
    assert not missing, (
        f"mypy ignore list names modules that do not exist (renamed or deleted?): {missing}"
    )
