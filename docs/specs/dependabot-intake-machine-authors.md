# Spec: Machine authors at intake (Dependabot PRs can pass)

Status: implemented. Lane: `pillar:platform`.

## Problem

`intake` is a required check. It fails a PR that does not tick the authorship-disclosure box in the PR template
and does not carry exactly one lane label. Dependabot writes its own PR body and applies its own labels
(`dependencies` plus the ecosystem), so it can do neither, and every Dependabot PR fails intake with
`disclosed=false laned=false` (DCO passes: Dependabot signs off). In six attempts (#79, #81, #84, #161, #195,
#196) none has merged. The workaround is to close the PR and open the same change again as a bot-authored PR
with the `chore` label and a disclosure (#194 replaced #161), which costs a PR per update and delays security
fixes: #196 carries body-parser CVE-2026-111181 and four fast-uri URI vulnerabilities.

## Requirements

1. `.asdd.yml` SHALL have a `machine_authors` list of GitHub bot logins whose own identity is their authorship
   disclosure. It lists `dependabot[bot]`. Empty or absent means none. A login that does not end in `[bot]` is
   never a machine author even if listed, because no person has such a login.
2. The intake workflow SHALL decide whether the PR author is on that list from the BASE commit's `.asdd.yml`
   (`git show <base-sha>:.asdd.yml`), through `.github/asdd/machine-author.sh`, so a PR cannot add itself. The
   PR author is GitHub event metadata, not text the PR controls. The workflow also hands the gate the author of
   each non-merge commit and the PR author's login as data files.
3. WHEN the PR author is a machine author AND every non-merge commit in the PR is authored by that same login
   (case-insensitive), intake SHALL treat the disclosure as satisfied and a PR with no lane label as laned and
   a `chore` (so the spec gate is skipped). A PR with a lane label keeps the normal rules for that lane, and two
   lane labels still fail.
4. A human commit pushed onto the branch, a missing author file, or a PR author that is not on the list SHALL
   leave every requirement in force: the PR then needs a ticked disclosure like any other.
5. DCO sign-off, the anti-flood cap, and the existing owner override SHALL be unchanged for a machine author.
6. The intake result SHALL record `machine_author` (true or false) in its JSON so the waiver is on the record.
7. Behaviour is unchanged for every PR whose author is not on the list, and for a workflow that does not set
   `machine_author` (older callers and local runs).

## What this does not do

- It does not let anything merge itself. A machine author's PR still needs the required checks, a human
  approval and, on a protected path, a named reviewer. `asdd-automerge.yml` still acts only on the founder's own
  safe-lane PRs.
- It does not waive the review lenses, the tests, the supply-chain and security audits, or any other required
  check. The advisory ASDD review already comments on Dependabot PRs (it did on #79, #81, #84 and #161) and
  keeps doing so.
- It does not apply to `onehill-dev-agent[bot]`: that identity opens PRs that tick the disclosure and carry a
  lane label, and keeps its own rules (the `Agent:` trailer and so on).
- It does not trust the commit author name by itself: the name is only compared with the PR author, to notice a
  human commit on a machine's branch. The authority is the PR author on the trusted list.

## Known limits

- `.asdd.yml` is not in `protected_paths` or CODEOWNERS today, so a change to the list needs only the ordinary
  approval. The `[bot]` rule limits the damage (a person cannot be added), and the list is still read from the
  base commit. Protecting `.asdd.yml` (which also holds `review_override_owners`) is a separate decision.
- A PR that already failed intake does not re-run on its own after this merges. Re-trigger intake by pushing to
  the branch (`@dependabot rebase`), editing the PR, or adding and removing a label.
- Dependabot PRs that were cancelled by the label-event race show as cancelled, not failed; they re-run on the
  same trigger.

## Acceptance

- A PR authored by `dependabot[bot]`, all commits by `dependabot[bot]`, signed off, with no disclosure box and
  only `dependencies` and `rust` or `javascript` labels, passes intake with `machine_author: true`. The real
  PRs #195 and #196, replayed through the script with their real bodies, labels and commits, pass; the current
  script fails the same inputs with `disclosed=false laned=false`.
- The same PR from a login not on the list, a PR with a human commit added, a PR with an unsigned commit, a PR
  over the anti-flood cap, and a PR with two lane labels each still fail.
- The workflow reads the list from `.asdd-work/base-asdd.yml`, never from the PR's working tree.
- Tests: `tests/test_asdd_machine_author.py`; the existing intake, DCO, owner-override and contribution-intake
  tests pass unchanged.
