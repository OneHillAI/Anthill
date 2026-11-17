"""Package boundaries hold: the lower layers never import the layers above them.

ARCHITECTURE.md separates the node from the orchestrator (a compromised node "cannot impersonate the
orchestrator") and keeps the wiki, inference and shared code beneath the web app. Nothing enforced it, and the
spec lens was asked to notice by reading diffs. This walks every `anthill/**/*.py` with `ast`, including
imports inside functions, and fails on a forbidden edge. Static: no import of the app, no model.

Three rules, each true today:

- `common` is a leaf: it imports no other anthill package.
- The domain packages (`wiki`, `inference`, `hybrid`, `node_agent`) never import the HTTP app (`web`) or the
  `orchestrator`, and the `orchestrator` never imports `node_agent`: node and orchestrator talk over the wire.
- Only the packages in WEB_IMPORTERS may import `anthill.web`. Those are the existing exceptions (the CLI and
  desktop shell sit above it; `training`, `agent` and `cloud` reach into it and are not yet cleaned up). The set
  can only shrink.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "anthill"

DOMAIN = {"wiki", "inference", "hybrid", "node_agent"}
FORBIDDEN = {
    "common": None,  # None = no anthill package at all
    **{name: {"web", "orchestrator"} for name in DOMAIN},
    "orchestrator": {"node_agent"},
}

# Remove an entry in the same PR that removes its last `anthill.web` import. Never add one.
WEB_IMPORTERS = {"agent", "cli", "cloud", "desktop", "training"}


def top_package(path: Path) -> str:
    """`anthill/web/app.py` -> `web`; `anthill/cli.py` -> `cli`."""
    return path.relative_to(PKG).parts[0].removesuffix(".py")


def imported_packages(source: str, module: list[str], is_init: bool) -> set[str]:
    """Top-level anthill packages a module imports, wherever the import statement sits.

    `module` is the dotted module path as parts (["anthill", "web", "app"]); relative imports resolve
    against it.
    """
    package = module if is_init else module[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "anthill" and len(parts) > 1:
                    found.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            full = base + (node.module.split(".") if node.module else [])
            if not full or full[0] != "anthill":
                continue
            if len(full) > 1:
                found.add(full[1])
            else:  # `from anthill import web`: the imported names are the packages
                found.update(a.name for a in node.names if not a.name.startswith("_"))
    return found


def import_graph() -> dict[str, dict[str, list[str]]]:
    """importing package -> imported package -> the files that do it."""
    graph: dict[str, dict[str, list[str]]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(ROOT)
        src = top_package(path)
        if src == "__init__":
            continue
        module = list(rel.with_suffix("").parts)
        is_init = module[-1] == "__init__"
        if is_init:
            module = module[:-1]
        for dst in imported_packages(path.read_text(encoding="utf-8"), module, is_init):
            if dst != src:
                graph.setdefault(src, {}).setdefault(dst, []).append(str(rel))
    return graph


@pytest.fixture(scope="module")
def graph():
    return import_graph()


def test_the_walker_found_the_real_graph(graph):
    assert len(graph) >= 15
    assert "common" in graph["wiki"] and "inference" in graph["hybrid"]  # known edges exist


def test_common_imports_no_other_package(graph):
    assert graph.get("common", {}) == {}, f"anthill.common must stay a leaf: {graph.get('common')}"


@pytest.mark.parametrize("pkg", sorted(p for p, bad in FORBIDDEN.items() if bad))
def test_lower_packages_do_not_import_the_layers_above(graph, pkg):
    hits = {dst: files for dst, files in graph.get(pkg, {}).items() if dst in FORBIDDEN[pkg]}
    assert not hits, f"anthill.{pkg} must not import {sorted(FORBIDDEN[pkg])}: {hits}"


def test_only_known_packages_import_the_web_app(graph):
    importers = {src for src, dsts in graph.items() if "web" in dsts}
    new = importers - WEB_IMPORTERS
    assert not new, (
        f"{sorted(new)} import anthill.web. Move the shared code down (for example into common) "
        f"instead of reaching up: {[graph[p]['web'][:2] for p in sorted(new)]}"
    )
    gone = WEB_IMPORTERS - importers
    assert not gone, f"{sorted(gone)} no longer import anthill.web; remove them from WEB_IMPORTERS"


# --- the walker itself ------------------------------------------------------------------------------


def _found(source, module="anthill.wiki.review", is_init=False):
    return imported_packages(source, module.split("."), is_init)


def test_walker_sees_absolute_relative_and_in_function_imports():
    assert _found("import anthill.web.app") == {"web"}
    assert _found("from anthill.web import db") == {"web"}
    assert _found("from anthill import web, cache") == {"web", "cache"}
    assert _found("from ..web import db") == {"web"}  # anthill/wiki/review.py -> anthill.web
    assert _found("from .. import orchestrator") == {"orchestrator"}
    assert _found("from . import okf") == {"wiki"}
    assert _found("def f():\n    from anthill.orchestrator.registry import R\n") == {"orchestrator"}
    assert _found("import os\nfrom pathlib import Path\nimport yaml") == set()
    assert _found("from anthill import __version__") == set()


def test_walker_resolves_relative_imports_from_a_package_init():
    assert _found("from .review import x", module="anthill.wiki", is_init=True) == {"wiki"}
    assert _found("from ...web import db", module="anthill.wiki.sub", is_init=True) == {"web"}
    assert _found("from ..web import db", module="anthill.wiki.sub", is_init=True) == {"wiki"}


def test_a_forbidden_edge_would_be_reported():
    graph = {"wiki": {"web": ["anthill/wiki/review.py"]}}
    hits = {d: f for d, f in graph["wiki"].items() if d in FORBIDDEN["wiki"]}
    assert hits == {"web": ["anthill/wiki/review.py"]}
