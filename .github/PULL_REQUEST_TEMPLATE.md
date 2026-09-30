## Lane (required)
Label this PR with exactly one: `pillar:privacy` `pillar:knowledge` `pillar:model` `pillar:platform`
`pillar:feature` `chore`. Automated PRs must add the label before marking the PR ready for review;
the intake gate reads GitHub labels rather than this checklist.

## Spec (required for non-`chore`)
ASDD is spec-driven. Either link the spec this implements, or add one in this PR that it follows:
- Existing spec: `docs/specs/____.md` (or `Spec: docs/specs/____.md`)
- New spec included in this PR: `docs/specs/____.md` (problem, requirements, acceptance criteria)

## What & why
<!-- What does this change and why? Link any related issue. -->

## How
<!-- Key implementation notes for reviewers. -->

## Disclosure (required - ASDD)
<!-- Tick the one that applies. Presenting agent work as human is a conduct violation. Keep this
     section when an automated tool replaces or expands the PR description. -->
- [ ] Authored by a **human**.
- [ ] Authored or co-authored by an **AI agent under human direction** (named below; each agent commit
      carries the `Agent:` trailer).
- [ ] I have **read and understand this change** and take responsibility for it (whoever or whatever
      wrote it). Disclosure is attribution; this is accountability.

> Agent identity (if any): `____` - Instructed by (human handle): `____`

## Checklist
- [ ] Every commit in this PR is signed off (DCO): use `git commit -s`; intake checks the full PR range.
- [ ] `make test` is green.
- [ ] New behavior has tests (model-free where possible).
- [ ] Optional features degrade gracefully (a missing key/dep disables only itself).
- [ ] Docs updated if behavior or setup changed (a `docs/SYSTEM_IMPACT_LOG.md` line; a
      `changelog.d/<id>.<category>.md` fragment if user-facing - see `changelog.d/README.md`, do not
      hand-edit `CHANGELOG.md`).
- [ ] If this touches a protected path, a founding-contributor review is requested.

---
<sub>This project follows [the ASDD](https://github.com/OneHillAI/ASDD): agents disclose,
humans approve merges, security and quality are gates.</sub>
