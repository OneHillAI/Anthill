"""The runtime check feeds every developer-council member from one council pair unless it has its own.

The scripts read the council per member (`ASDD_MODEL_URL__COUNCIL_<i>`, `ASDD_RUNTIME_TOKEN__COUNCIL_<i>`).
In CI the workflow fills each of those from the member's own repo setting, else from the single
`ASDD_MODEL_URL__COUNCIL` / `ASDD_RUNTIME_TOKEN__COUNCIL`, so the council's provider and key are set once.
"""

from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/asdd-runtime-check.yml"


@pytest.mark.parametrize("i", [1, 2, 3])
def test_each_member_falls_back_to_the_single_council_pair(i):
    text = WORKFLOW.read_text()
    assert (
        f"ASDD_MODEL_URL__COUNCIL_{i}: ${{{{ vars.ASDD_MODEL_URL__COUNCIL_{i} || vars.ASDD_MODEL_URL__COUNCIL }}}}"
        in text
    )
    assert (
        f"ASDD_RUNTIME_TOKEN__COUNCIL_{i}: ${{{{ secrets.ASDD_RUNTIME_TOKEN__COUNCIL_{i} "
        "|| secrets.ASDD_RUNTIME_TOKEN__COUNCIL }}"
    ) in text


def test_the_shared_roster_pair_is_not_used_for_the_council():
    """The council is on Runware, the roster on Infercom: a member must never silently fall back to the
    shared Infercom pair in CI (that sent the council to the wrong provider and 404'd)."""
    text = WORKFLOW.read_text()
    for line in text.splitlines():
        if "__COUNCIL_" in line and ": ${{" in line:
            assert (
                "vars.ASDD_MODEL_URL }}" not in line and "secrets.ASDD_RUNTIME_TOKEN }}" not in line
            )
