# Spec: The post-merge test run is deterministic

Status: implemented. Lane: `pillar:platform`. Issue: #174. Supersedes the post-merge runner requirements of
[`asdd-test-agent.md`](asdd-test-agent.md) (R1 to R6, R8, R9).

## Problem

After every merge to `main`, `asdd-test.yml` installed the test dependencies and the Goose CLI, then asked a
model (`gpt-oss-120b`) to run `pytest`, count the passes and write a JSON result. Running pytest and reading its
counts is not a judgement, so the model added only ways to fail: the Goose download, the model endpoint, or a
malformed reply each recorded `asdd/test=error` on a green suite. Promote Beta reads that status, so a flaky
agent blocked a release while `ci.yml` was green on the same commit. It also cost inference time and money per
merge and ran the merged code in a step that held a model key.

## Requirements

1. After every push to `main`, `asdd-test.yml` SHALL run `pytest -n 2 --dist loadscope -q --junitxml=...` on the
   merged commit, the same command and worker count `ci.yml` uses.
2. The verdict SHALL be pytest's exit code, with the JUnit XML supplying counts and failing case names:
   exit 0 with tests run and no failure in the XML is a pass; exit 1 is a fail (a collection error under xdist is
   an errored test, so a suite that cannot be collected is a fail); any other exit code, an exit 0 with a missing
   or unreadable XML, an exit 0 with no tests, and an exit 0 whose XML lists failures are "no verdict". Nothing
   but a clean run is a pass.
3. The result SHALL keep the structured shape the report and the status already read (`verdict`, `payload` with
   `passed`, `failed`, `failing`), plus `runner: pytest`. The report keeps its `## Test agent - result for` header
   and says it is deterministic. Failing cases are capped and clipped as before.
4. The workflow SHALL keep recording the commit status `asdd/test` (success, failure, error for no verdict) and
   posting the report on the merged PR, and it SHALL keep refusing a commit that is not on `main` and never run on
   a pull request. Promote Beta is unchanged.
5. No model, Goose, endpoint, key or secret SHALL be in the workflow or reach the step that runs the merged
   code; that step has no `env:`.
6. Every run SHALL leave one audit record (role `test-runner`, action `test.run`) through `cli/audit.py`.
7. `test.sh` keeps its arguments and exits 0 once it has run, whatever the verdict, so the report and status are
   always produced.

## Known limits

- The `test_runner` role in `.asdd.yml`, `recipes/test-runner.yaml` and the model-independence rules that name
  `test_runner` (`scripts/check-models.sh`, `cli/connect-check.py`, `cli/dev-council.py`) are not removed here.
  The recipe stays available to run by hand with Goose; nothing runs it automatically any more. Retiring the role
  is a roster decision for a separate change.
- The run is not independent of the code under test the way a different model's review is; it is the same suite
  CI already runs. What it adds is a verdict on the exact merged commit that Promote Beta can read.
- Not run against GitHub here: the workflow edit is checked by tests that read the file, and the run itself is
  proven by running **ASDD test** by hand on a commit on `main` after merge.

## Acceptance

- A green suite reports PASS with counts and records success; a red one reports FAIL naming the failing cases;
  a suite with no tests, or one pytest was killed on, reports NO VERDICT and records error.
- The exit code and the XML disagreeing is never a pass.
- The workflow text contains no Goose, model variable or secret, the test step has no `env:`, and the workflow
  still triggers only on `push` to `main` and `workflow_dispatch`.
- `tests/test_asdd_test_agent.py`, `tests/test_beta_promote.py` and `tests/test_operate_runner_contract.py` pass.
- After merge: run **ASDD test** by hand on a recent commit on `main` and confirm the comment and the
  `asdd/test` status appear.
