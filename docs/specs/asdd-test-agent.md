# Spec: the ASDD test agents (test runner after merge, test author on demand)

Status: implemented, not yet proven live (see section 4)
Lane: `pillar:platform`
Relates to: [`asdd-model-roster.md`](asdd-model-roster.md) (roles `test_author`, `test_runner`),
`recipes/`, `cli/operate-run.py`, `.github/asdd/operate/`, the ASDD standard OP.2, OP.3 and OP.5
(`OneHillAI/ASDD`, `standards/spec-driven.md`)

## 1. Problem

The roster in `.asdd.yml` names two test roles, `test_author` and `test_runner`, both on `gpt-oss-120b`,
distinct from the developer. Nothing ran them. The only recipe was an older single `tester.yaml` that no
workflow called, and the kit's post-merge test workflow had never been installed. The ASDD standard (OP.5)
says wiring is not enough: each provisioned agent must be shown doing its job on a real artifact. On
2026-10-02 the founder found that most of the roster had been reviewed and discussed as if it ran while only
the reviewer (and, after that day's settings, the docs agent) actually did.

## 2. Requirements

R1. THE test runner SHALL run automatically after every push to `main`, on the merged (trusted) commit,
with the roster's `test_runner` model, and post a pass or fail report on the merged PR.

R2. THE test runner SHALL NOT run on an open pull request. It has a shell and the model key, and running a
stranger's code with the key in reach needs a sandbox this repo does not have. `cli/operate-guard.py`
enforces this, and a hand-started run SHALL refuse any commit that is not on `main`.

R3. THE runner's model, endpoint and key SHALL come from the roster and the per-role resolver
(`cli/resolve-model.sh`: `models.test_runner`, `ASDD_MODEL_URL__TEST_RUNNER`, `ASDD_RUNTIME_TOKEN__TEST_RUNNER`,
else the shared pair), and either spelling of the endpoint URL (base `/v1`, trailing slash, or the full
`/chat/completions`) SHALL work.

R4. THE report SHALL come from the agent's structured result file (verdict, counts, failing cases), be
labelled agent-reported, and name the model. A verdict other than pass or fail SHALL read "NO VERDICT",
never a pass.

R5. WHEN the agent does not run (not wired, Goose absent, the guard refuses), THE report SHALL say "NO
TEST AGENT RAN" and is not a result. WHEN it starts but leaves no result, THE report SHALL say it did not
run to completion, with the exit code and the last lines of output with the model key redacted.

R6. EVERY run, including one that never starts the agent, SHALL leave exactly one audit record
(`cli/operate-run.py` for a real run, the runner itself for the others).

R7. THE test author (`recipes/test-author.yaml`) SHALL be runnable on demand while a change is built, with
the roster's `test_author` model, through `cli/operate-run.py`. It is not an automatic CI job.

R8. THE kit's template for the post-merge test runner passed the recipe `change_ref` where the recipe's
parameter is `pr`, and looked for a "## Test result" heading the recipe never prints. This runner passes
`pr` and reads the result file; the same fixes belong upstream in `OneHillAI/ASDD`.

## 3. Acceptance criteria

- `tests/test_asdd_test_agent.py` passes (16 tests). 9 of the runner tests fail against the kit's template as shipped; 3 more test the report renderer on its own.
- A PASS, a FAIL (with failing cases), an unusable verdict, a run with no result (key redacted), an unwired
  runner and a missing Goose each produce the report described above.
- Goose receives the roster's model, `--params pr=<ref>`, and host plus `v1/chat/completions` for all three
  URL spellings; a per-role endpoint and key win over the shared pair.
- The workflow triggers only on `push` to `main` and `workflow_dispatch`, never `pull_request`, and refuses
  a commit that is not an ancestor of `main`.

## 4. Proof on a real change (OP.5)

This spec is implemented but the agent is not "set up" until it has reported a real pass or fail on a real
merged change. After merge: run `ASDD test` by hand (Actions, Run workflow) on a recent merge commit on
`main`, read the comment it posts on that PR, and record the result in the PR. If it fails, fix and re-run.

## 5. Out of scope

- Running the tester or any tool-using agent on an open PR (needs an egress-free sandbox).
- The interaction agent, the merge-reviewer and the impact reviewer (separate changes).
- Moving the docs agent to the per-role runner (separate change).
- Making the test agent a required check. It is advisory and post-merge; CI's own tests remain the gate.
