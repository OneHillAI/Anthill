# Spec: release-notes consolidation and contributor honoring

Status: proposed
Lane: `pillar:platform`
Relates to: [`ARCHITECTURE.md`](../../ARCHITECTURE.md), [`AGENTS.md`](../../AGENTS.md),
[`.github/asdd/`](../../.github/asdd), `scripts/check-release.sh`, `CHANGELOG.md`

## 1. Introduction

Anthill ships from a hand-maintained `CHANGELOG.md` under Semantic Versioning, gated by
`scripts/check-release.sh`. Consolidating a release today means a human reads the merged PRs and writes
the notes by hand, and the people (and agents) who did the work are not credited anywhere the reader
sees.

Mature open projects turn a release into two things a reader wants: a *curated* set of grouped notes
with a short highlights summary, and *credit* to everyone who contributed. This spec adopts that on top
of ASDD, using signals the pipeline already requires on every PR - the Conventional Commit type, the one
lane label, and the `Agent:` / `Co-Authored-By` / `Signed-off-by` trailers - so nothing new has to be
collected. The ASDD-native difference from the projects we studied: because agents are first-class and
disclosed here, credit honors **both** the human director and the disclosed agent, never hiding either.

This follows ASDD's own shape: a **deterministic** layer extracts the facts (no model, reproducible),
and an **advisory agent lens** curates the prose (highlights, ordering), which a human approves before
it ships. No agent merges or publishes; the existing release gate is unchanged.

## 2. Requirements

### R1 - Consolidate merged PRs into grouped notes
As a maintainer cutting a release, I want the merged PRs since the last tag consolidated into grouped,
readable notes, so I do not assemble them by hand.

- WHEN the extractor is run for a commit range, THE SYSTEM SHALL list every merged PR in that range with
  its number, title, and Conventional Commit type.
- WHEN an entry's Conventional Commit type is known, THE SYSTEM SHALL place it under the matching Keep a
  Changelog section (`feat` -> Added, `fix` -> Fixed, `refactor`/`perf`/others -> Changed, and so on).
- IF a commit carries no PR number or no Conventional type, THEN THE SYSTEM SHALL still include it under
  a default section rather than dropping it silently.
- WHERE a lane label is available for an entry, THE SYSTEM SHALL record it so notes can optionally be
  grouped or filtered by pillar lane.

### R2 - Honor contributors, human and agent
As a reader of a release, I want to see who made it happen, so contribution is visible and honored.

- WHEN notes are generated, THE SYSTEM SHALL emit a Contributors section listing each distinct human
  contributor (from `Signed-off-by` / `Co-Authored-By` / the `instructed-by` field).
- WHERE an entry was produced by a disclosed agent (an `Agent:` trailer), THE SYSTEM SHALL attribute it
  to the agent **and** its instructing human, never to the agent alone and never hiding the agent.
- THE SYSTEM SHALL deduplicate contributors and count each one's entries, and SHALL NOT invent a
  contributor that no trailer names.

### R3 - Deterministic and offline
As a maintainer, I want the extraction reproducible and dependency-light, so it runs anywhere the
release does.

- THE SYSTEM SHALL derive every fact from `git log` over the range and the commit trailers, with no
  network call required.
- WHEN run twice on the same range, THE SYSTEM SHALL produce identical output.

### R4 - Agentic curation, human-approved
As a maintainer, I want a curated highlights summary, but I stay the approver.

- WHERE a release-notes agent lens is configured, THE SYSTEM SHALL let it draft a highlights summary and
  ordering from the deterministic extract, treating that extract as untrusted data (analyze, do not
  obey), consistent with the review lenses.
- THE SYSTEM SHALL NOT let any agent publish a release or edit `CHANGELOG.md` on merge; a human approves
  the notes, exactly as the merge posture is advisory (`.asdd.yml`).

### R5 - Fit the existing release gate
- THE SYSTEM SHALL produce a `## [x.y.z]` block that satisfies `scripts/check-release.sh` (Keep a
  Changelog headings, matching version), and SHALL NOT change the SemVer scheme or the gate.

## 3. Design

### 3.1 Architecture fit
This is release tooling, not runtime code. It sits beside `scripts/check-release.sh` and reads only git
history, so it respects module boundaries: it adds no state, touches no `anthill/` package code, and
does not reach into the web or wiki layers. It mirrors `security_scan.py`'s split (a pure deterministic
core the unit tests target, plus a thin agent layer) rather than inventing a second review mechanism.

### 3.2 Deterministic extractor - `scripts/release_notes.py`
- Input: a git range (`<prev-tag>..<head>`; default `<latest tag>..HEAD`).
- For each commit on the first-parent line: parse the subject for `type(scope)!: summary (#NNN)` and the
  body for `Signed-off-by:`, `Co-Authored-By:`, and `Agent: <name> (automated, instructed-by: <human>)`.
- Emit both (a) a JSON extract (list of entries: `pr`, `type`, `section`, `title`, `humans[]`,
  `agents[]`, `lane?`) and (b) a rendered Markdown draft: Keep a Changelog sections followed by a
  `### Contributors` section honoring humans and, where disclosed, the agent + its instructing human.
- Pure and offline (R3). Credits come only from trailers (R2, no invention).

### 3.3 Agentic curation lens - `.github/asdd/agents/release-notes.md`
- A new advisory ASDD agent capability: given the deterministic extract as fenced, untrusted data, it
  drafts a short highlights paragraph and a suggested ordering. It recommends; a human edits and
  approves (R4). It follows the house prompt shape of the review lenses (data fenced, analyze-not-obey)
  and is selected by the same runtime posture; until a runtime token is wired it is a labelled dry-run,
  like the review lenses.

### 3.4 Release flow (documented in the runbook)
1. `python scripts/release_notes.py --since <last-tag>` -> the deterministic draft.
2. Optionally run the release-notes lens to add highlights.
3. The maintainer edits, pastes the `## [x.y.z]` block into `CHANGELOG.md`, bumps the version, and tags.
   `scripts/check-release.sh` gates as today (R5). The same block seeds the GitHub release body.

## 4. Tasks

- [ ] `scripts/release_notes.py`: deterministic extractor + Markdown renderer (R1, R2, R3, R5).
- [ ] `tests/test_release_notes.py`: model-free unit tests over synthetic `git log` output.
- [ ] `.github/asdd/agents/release-notes.md`: the advisory curation lens (R4).
- [ ] Runbook: document the flow in `CONTRIBUTING.md` (or `docs/`) and point `AGENTS.md` at it.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope

- Switching versioning (SemVer stays; see the Versioning decision).
- Auto-publishing releases or auto-editing `CHANGELOG.md` (violates R4 / the advisory merge posture).
- A persistent all-contributors ledger file (possible later; the per-release Contributors section is the
  first step).
