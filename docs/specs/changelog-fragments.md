# Changelog fragments (kill the merge-conflict treadmill)

## Status

Accepted. Replaces hand-editing `CHANGELOG.md`'s `## [Unreleased]` block with per-PR fragment files
under `changelog.d/`. Closes #513.

## Problem

Every open PR edited the same `## [Unreleased]` block, so each merge mechanically re-conflicted every
other open PR's changelog lines. The conflicts were never semantic (the same few lines), but they cost
real reviewer/agent time - in one afternoon (2026-07-13) four PRs (#499, #501, #502, #503) all needed
conflict surgery on that one file, and cutting the v0.11.3 release mid-stream re-conflicted everything
still open.

## Requirements (EARS)

- A PR SHALL record its changelog entry as a fragment file, not by editing a shared file, so two PRs
  never touch the same path.
- The release cut SHALL assemble all fragments into a `## [X.Y.Z] - DATE` section and remove the
  consumed fragments.
- A release SHALL NOT be taggable while unassembled fragments remain.
- The em/en-dash slop gate SHALL cover `changelog.d/`.

## Design

### Fragment files
- Location: `changelog.d/`.
- Name: `<id>.<category>.md`, where `<id>` is the PR/issue number (e.g. `513`) or a `+slug`
  (e.g. `+escalation-consent`) when no number exists yet, and `<category>` is one of
  `added | changed | deprecated | removed | fixed | security` (the Keep a Changelog headings already in
  use). A PR may add several (`513.fixed.md`, `513.security.md`).
- Content: the bullet body only - the same prose written today - with no leading `-` and no heading.
  Plain hyphens only.
- `changelog.d/.gitkeep` keeps the directory present when empty; `changelog.d/README.md` is the
  contributor how-to. Both are ignored by the assembler.

### Assembler - `scripts/build_changelog.py`
- `build_changelog.py <version> <YYYY-MM-DD>`: reads `changelog.d/*.md`, groups by category in Keep a
  Changelog order, writes a `## [X.Y.Z] - DATE` block between `## [Unreleased]` and the previous
  release, and deletes the consumed fragments. Deterministic order: numeric ids sort numerically, then
  `+slug` ids lexically.
- `build_changelog.py --draft`: prints the pending section without changing anything (for review and
  release prep). Since `[Unreleased]` is no longer hand-edited, this is how you see "what's unreleased".
- Runs as part of the release cut; the change is committed in the release PR.

### Release gate - `scripts/check-release.sh`
- Unchanged guarantees: a tag needs a matching `## [X.Y.Z]` section AND `pyproject.toml` version == tag.
- Added: fails if any `changelog.d/<id>.<category>.md` remains (fragments were not assembled).

### Slop gate
- No change needed: the CI slop gate already scans every tracked `*.md` (`git ls-files -z '*.md'`), so
  `changelog.d/*.md` is covered automatically.

### PR intake (advisory)
- `.github/workflows/changelog-fragment.yml`: a **non-blocking** check that warns when a PR changes
  `anthill/` but adds no fragment (mirrors the spec-gate philosophy; advisory to start, so it never
  fails a build). `chore`/docs PRs need no fragment.
- `.github/PULL_REQUEST_TEMPLATE.md`: the "Docs updated" line points to adding a `changelog.d/` fragment.

## Migration

Introduced right after the v0.11.4 cut, when `[Unreleased]` was empty - so there were no existing
entries to convert. The introducing PR only replaces the hand-edited `[Unreleased]` block with a pointer
note. Any PR still open against the old block does a one-time rebase: drop its `CHANGELOG.md` edit, add a
`changelog.d/` fragment. From then on, changelog conflicts cannot occur.

## Acceptance criteria

- Two PRs each adding a changelog entry never conflict on any shared file. (They add different files
  under `changelog.d/`.)
- `scripts/build_changelog.py 0.11.5 <date>` turns `changelog.d/*.md` into a correct `## [0.11.5]`
  section, grouped and ordered, and clears the directory.
- `scripts/check-release.sh vX.Y.Z` still fails a release with no changelog content, and now also fails
  if fragments remain unassembled.
- The slop gate covers `changelog.d/` (via the existing all-`*.md` scan).

## Notes / lane

Release tooling. Touches protected paths (`.github/workflows`, `scripts`, the PR template), so it needs
a founding-contributor review, and the release-cut process changes (run the assembler instead of moving
`[Unreleased]` entries) - coordinate with the release-tooling owner. Prior art: Towncrier and the
`changelog.d/` convention (pip, attrs, pytest, and others).
