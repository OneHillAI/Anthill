# Format the nav tests to the pinned ruff standard

## Status

Accepted. Retroactive spec for #593 - a `chore` lane PR, which the mandatory-spec gate exempts
(`docs/specs/mandatory-spec-gate.md`). Recorded here so the one-off normalization is on file like every
other change.

## Problem

`ruff format` is pinned to 0.15.16 in the `dev` extra (`pyproject.toml`) so formatting is deterministic and
CI matches local runs. Two test files predated the current formatter output and were never reflowed:

- `tests/test_nav_roles.py`
- `tests/test_profile_nav.py`

`ruff format --check anthill tests` therefore reported "2 files would be reformatted", so the format gate
would fail on any PR whose CI runs it, even a PR that touches neither file. The drift is purely cosmetic -
both files parse and pass - but a red format gate is noise that masks real failures.

## Change

Run `ruff format tests/test_nav_roles.py tests/test_profile_nav.py`. The only edits are two over-long
boolean asserts reflowed from one line into ruff's parenthesized multi-line form. For example:

    assert "workspace-mode" in tasks and "nav-more" in tasks  # groups collapse into "More" in a workspace

becomes

    assert (
        "workspace-mode" in tasks and "nav-more" in tasks
    )  # groups collapse into "More" in a workspace

No assertion, string, or comment text changes - only line breaks and indentation.

## Acceptance criteria

- `ruff format --check anthill tests` reports the whole tree already formatted (no files would be
  reformatted).
- The diff is confined to `tests/test_nav_roles.py` and `tests/test_profile_nav.py` and is line-break /
  indentation only; no `assert` expression, string literal, or comment differs.
- The affected tests still pass unchanged.

## Non-goals

- Reformatting anything else, or changing the pinned ruff version.
- Any change to test logic or coverage.
