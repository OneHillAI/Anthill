"""The core-pillar invariant markers still cover a real set of tests.

`asdd-invariants.yml` runs `pytest -m "privacy_invariant or knowledge_invariant or model_invariant"`. If a
marker is deleted from a file, or typed wrong, that job still goes green on whatever is left, so coverage can
drain away without anything failing. This counts the tagged tests with `ast` (no collection, so it is fast)
and requires each marker to keep a floor, and every marker that looks like an invariant marker to be a registered one.

Raise a floor when you tag more tests. Lower one only when you delete tests on purpose.
"""

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest already pulls tomli there
    import tomli as tomllib

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

# A little under today's counts (privacy 50, knowledge 23, model 19), so removing a whole file's tags fails
# and pruning one obsolete test does not.
FLOORS = {"privacy_invariant": 40, "knowledge_invariant": 18, "model_invariant": 15}


def _mark_names(node: ast.expr) -> set[str]:
    """`pytest.mark.x`, `pytest.mark.x(...)`, or a list/tuple of them -> {"x"}."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return set().union(*(_mark_names(e) for e in node.elts)) if node.elts else set()
    if isinstance(node, ast.Call):
        node = node.func
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    ):
        return {node.attr}
    return set()


def marked_tests(source: str) -> list[tuple[str, set[str]]]:
    """Every test function (or method) in the source with the marks that apply to it."""
    tree = ast.parse(source)
    module_marks: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            module_marks |= _mark_names(node.value)
    found: list[tuple[str, set[str]]] = []

    def visit(body, inherited: set[str], in_class: bool) -> None:
        for node in body:
            decorators = (
                set().union(*(_mark_names(d) for d in node.decorator_list))
                if hasattr(node, "decorator_list")
                else set()
            )
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit(node.body, inherited | decorators, True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                found.append((node.name, inherited | decorators))

    visit(tree.body, module_marks, False)
    return found


def _counts() -> dict[str, int]:
    counts = dict.fromkeys(FLOORS, 0)
    used: dict[str, int] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        for _, marks in marked_tests(path.read_text(encoding="utf-8")):
            for m in marks:
                if "invar" in m:  # catches a typo such as privacy_invarient
                    used[m] = used.get(m, 0) + 1
    counts.update(used)
    return counts


def _registered() -> set[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    return {
        m.split(":")[0].strip() for m in cfg["markers"] if m.split(":")[0].endswith("_invariant")
    }


def test_each_invariant_marker_keeps_its_floor():
    counts = _counts()
    short = {
        m: (counts.get(m, 0), floor) for m, floor in FLOORS.items() if counts.get(m, 0) < floor
    }
    assert not short, (
        f"invariant tests went missing (now, floor): {short}. Was a marker removed from a test file?"
    )


def test_every_invariant_marker_in_use_is_registered_and_has_a_floor():
    counts = _counts()
    unknown = set(counts) - _registered()
    assert not unknown, f"unregistered invariant marker (a typo?): {sorted(unknown)}"
    assert set(FLOORS) == _registered(), (
        "FLOORS and the markers in pyproject.toml must name the same set"
    )


# --- the counter itself ---------------------------------------------------------------------------


def test_counter_reads_module_class_and_function_marks():
    src = (
        "import pytest\n"
        "pytestmark = pytest.mark.privacy_invariant\n"
        "def test_a(): pass\n"
        "@pytest.mark.model_invariant\n"
        "def test_b(): pass\n"
        "@pytest.mark.parametrize('x', [1, 2])\n"
        "def test_c(x): pass\n"
        "def helper(): pass\n"
        "@pytest.mark.knowledge_invariant\n"
        "class TestD:\n"
        "    def test_e(self): pass\n"
        "    def not_a_test(self): pass\n"
    )
    got = dict(marked_tests(src))
    assert got["test_a"] == {"privacy_invariant"}
    assert got["test_b"] == {"privacy_invariant", "model_invariant"}
    assert got["test_c"] == {"privacy_invariant", "parametrize"}
    assert got["test_e"] == {"privacy_invariant", "knowledge_invariant"}
    assert "helper" not in got and "not_a_test" not in got


def test_counter_reads_a_list_of_module_marks_and_ignores_other_assignments():
    src = (
        "import pytest\n"
        "pytestmark = [pytest.mark.privacy_invariant, pytest.mark.skipif(False, reason='x')]\n"
        "other = pytest.mark.model_invariant\n"
        "def test_a(): pass\n"
    )
    assert dict(marked_tests(src))["test_a"] == {"privacy_invariant", "skipif"}
