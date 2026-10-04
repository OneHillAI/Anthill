# Spec: the documentation agent proposes paste-ready docs and checks them

Status: implemented, not yet proven on a merge (see section 4)
Lane: `pillar:platform`
Relates to: [`asdd-model-roster.md`](asdd-model-roster.md) (role `documentation`),
[`asdd-test-agent.md`](asdd-test-agent.md), `recipes/documentation.yaml`, `.github/asdd/operate/`, the ASDD
standard OP.2 and OP.5 (`OneHillAI/ASDD`)

## 1. Problem

The documentation agent runs after every merge to `main` and posts a proposal on the merged PR. Its first
four proposals (on #53, #56, #57, #58) were live but not correct:

1. It wrote a free-form diff, so the impact-log entry landed at line 1 of the file, above the title and in
   the middle of an older entry.
2. It invented details: "4 tests updated, 2 new browser tests", none of which the PR says.
3. It used labels the log does not allow ("Footprint: reductive"; the log allows additive, refactor,
   migration, plan-only), and a changelog fragment with a heading and a name the assembler does not use.
4. It ran on the shared model setting rather than the roster's `documentation` model, left no audit
   record, and, because its output was a diff, the founder had to hand-copy fragments out of it.

The ASDD standard (OP.5) says the documentation agent is not set up until it has produced a correct update
for an actual change.

## 2. Requirements

R1. THE agent SHALL return a structured proposal (an impact entry, an optional changelog line, optional doc
edits). It SHALL NOT write a heading, a diff, a PR number or a date, and SHALL NOT edit any file.

R2. THE runner SHALL build the impact-log heading from facts GitHub and git supply: the real PR number, title
and merge date (the workflow reads them from the GitHub API and passes them as files).

R3. THE renderer SHALL check the entry against the project's rules and print a "Check before pasting" list
for anything that fails: the four template fields present, `user_visible` starting yes or no, `footprint`
starting with additive, refactor, migration or plan-only, and every number in the text appearing somewhere in
the PR (title, description, diff stat, changed paths).

R4. A changelog fragment SHALL only be proposed when product code (`anthill/`) changed, named
`changelog.d/<PR>.<category>.md` with an allowed category, with no heading and no leading dash. A release cut (a PR titled "chore: cut release ...", or one that changes nothing but the changelog, the version files and `changelog.d/`) SHALL never get a fragment: it assembles the fragments itself, and one for it would repeat the notes in the next release (found on PR 67, v1.0.1).

R5. A proposed doc edit SHALL be kept only if its file exists inside the repository and its "now untrue"
sentence is verbatim in that file; otherwise it is dropped with a note.

R6. En and em dashes (banned by the style gate) SHALL become plain hyphens, and every field SHALL be length
capped. The report SHALL say where the entry goes: directly under the newest `## YYYY-MM` heading.

R7. THE runner SHALL take its model, endpoint and key from the roster and per-role resolver
(`models.documentation`, `ASDD_MODEL_URL__DOCUMENTATION`, `ASDD_RUNTIME_TOKEN__DOCUMENTATION`, else the shared
pair), accept any spelling of the endpoint URL, run only on merged code (`cli/operate-guard.py`, and a
hand-started run must name a commit on `main`), and leave exactly one audit record per run.

R8. WHEN the agent does not run, THE report SHALL say "NO DOCUMENTATION AGENT RAN" and is not a proposal. WHEN
it starts but leaves no usable result, THE report SHALL say it did not run to completion, with the exit code
and the last lines of output with the key redacted.

R9. THE agent SHALL stay advisory: no PR is opened by a bot without the founder's yes (standing rule), so the
report is for a human to apply by hand.

## 3. Acceptance criteria

- `tests/test_asdd_docsync.py` passes (20 tests: the renderer's rules, and the real runner in an isolated git
  repo with only `goose` stubbed).
- A good proposal renders `### PR #<n> - <title> - merged <date>` from the supplied facts and names the
  newest month heading; an invented number, a bad footprint label, a missing sentence, a heading in the
  changelog text and a path outside the repo are each caught as described.
- Goose receives the roster model, `--params change_ref=<sha>`, and host plus `v1/chat/completions` for all
  three URL spellings; a per-role endpoint and key win over the shared pair.
- The workflow triggers only on `push` to `main` and `workflow_dispatch`, never `pull_request`, and refuses
  a commit that is not an ancestor of `main`.

## 4. Proof on a real change (OP.5)

Not "set up" until it has produced a correct update for a real merge: after merge, read the proposal it posts
on the next merged PR and confirm the entry is placed and formatted per R2 to R6 with nothing flagged that is
actually wrong. A run can also be started by hand (Actions, `ASDD docsync`, a commit on `main`). If it fails,
fix and re-run.

## 5. Out of scope

- The agent opening PRs or editing files (R9).
- The merge-reviewer, the impact reviewer and the interaction agent.
- Aligning the wider ASDD process docs (a separate docs change).
