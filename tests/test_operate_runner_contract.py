"""Each operate runner and the recipe it runs must agree, or a "wired" agent never really starts.

The runners (`.github/asdd/operate/*.sh`) hand a recipe its parameters with `--param K=V` (through
`cli/operate-run.py`) or `--params K=V` (straight to `goose run`); Goose refuses a run whose parameters do not match what the recipe declares, and the failure was once
hidden (the kit's test runner passed `change_ref` to a recipe that takes `pr`). The per-runner tests pin the
values; this one derives the contract from the files themselves, so renaming a parameter on either side, or
adding a runner without registering it, fails here instead of in production. Static: no goose, no network.

Pins that, for every runner that runs a recipe:

- the parameter keys it passes are exactly the keys the recipe declares;
- every `{{ placeholder }}` the recipe uses is a declared parameter;
- when it reads a result file, that is the one the recipe tells the agent to write;
- a runner that runs a recipe is registered below (a new one cannot skip the check);
- and that the check itself catches the mistake it exists for.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
OPERATE = ROOT / ".github/asdd/operate"
# runner script -> the recipe it runs
PAIRS = {
    "test.sh": "recipes/test-runner.yaml",
    "docsync.sh": "recipes/documentation.yaml",
}


def passed_params(script_text):
    return set(re.findall(r"--params?\s+([A-Za-z_][A-Za-z0-9_]*)=", script_text))


def declared_params(recipe):
    return {p["key"] for p in recipe.get("parameters") or []}


def used_placeholders(recipe):
    text = "\n".join(str(recipe.get(k, "")) for k in ("prompt", "instructions"))
    return set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", text))


def problems(script_text, recipe):
    out = []
    passed, declared = passed_params(script_text), declared_params(recipe)
    if passed - declared:
        out.append(
            f"the runner passes {sorted(passed - declared)} but the recipe does not declare them"
        )
    if declared - passed:
        out.append(
            f"the recipe declares {sorted(declared - passed)} but the runner never passes them"
        )
    if used_placeholders(recipe) - declared:
        out.append(
            f"the recipe uses {sorted(used_placeholders(recipe) - declared)} without declaring it"
        )
    return out


def _load(recipe_rel):
    return yaml.safe_load((ROOT / recipe_rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("runner", sorted(PAIRS))
def test_the_runner_passes_exactly_the_parameters_its_recipe_declares(runner):
    script = (OPERATE / runner).read_text(encoding="utf-8")
    assert problems(script, _load(PAIRS[runner])) == []


@pytest.mark.parametrize("runner", sorted(PAIRS))
def test_a_runner_that_reads_a_result_file_reads_the_one_the_recipe_writes(runner):
    script = (OPERATE / runner).read_text(encoding="utf-8")
    recipe_text = (ROOT / PAIRS[runner]).read_text(encoding="utf-8")
    read_by_runner = re.search(r'(?m)^RESULT="([^"]+)"', script)
    if read_by_runner:  # a runner that prints its report instead has no result file to agree on
        assert read_by_runner.group(1) in recipe_text


def test_every_runner_that_runs_a_recipe_is_registered_here():
    """A new operate runner must name its recipe above, so it gets the same check."""
    running = {
        p.name
        for p in OPERATE.glob("*.sh")
        if re.search(r"cli/operate-run\.py|goose run --recipe", p.read_text(encoding="utf-8"))
    }
    assert running == set(PAIRS), (
        f"register {sorted(running - set(PAIRS))} in PAIRS (or drop a stale entry)"
    )
    for recipe_rel in PAIRS.values():
        assert (ROOT / recipe_rel).is_file()


def test_the_check_catches_the_mistake_it_exists_for():
    """The kit's test runner passed `change_ref` to a recipe whose parameter is `pr`: Goose died on a missing
    parameter and `|| true` hid it. The same shape, as text, must be reported."""
    recipe = {"parameters": [{"key": "pr"}], "prompt": "Test {{ pr }}"}
    wrong = "python3 cli/operate-run.py --role test-runner --param change_ref=abc"
    found = problems(wrong, recipe)
    assert any("change_ref" in p for p in found)
    assert any("'pr'" in p for p in found)
    assert problems("python3 cli/operate-run.py --param pr=abc", recipe) == []


def test_an_undeclared_placeholder_in_the_prompt_is_reported():
    recipe = {"parameters": [{"key": "pr"}], "prompt": "Test {{ pr }} for {{ owner }}"}
    assert any("owner" in p for p in problems("--param pr=1", recipe))
