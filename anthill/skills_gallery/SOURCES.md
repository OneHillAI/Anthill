# Gallery sources (for audit)

Every entry in this directory was verified against a real, published open-source license before being
vendored here - none were fabricated or assumed. This gallery is separate from `ANTHILL_SKILLS_DIR` (or
any workspace's `skills/` dir), so an entry here is inert until a user explicitly adopts it via
`POST /skills/gallery/{slug}/adopt` (`anthill/agent/skills.py::load_gallery()`/`GET /skills/gallery`).

## Origin

All four entries are vendored from Anthropic's public `anthropics/skills` repository:

- Source: <https://github.com/anthropics/skills>
- Commit fetched: `b29e7cf65e5cb78a5ac33d582270551bc74a14eb` (`main`, fetched 2026-08-02)
- The repository's `README.md` states: "Many skills in this repo are open source (Apache 2.0)," and
  explicitly calls out `skills/docx`, `skills/pdf`, `skills/pptx`, and `skills/xlsx` as
  **source-available, not open source** - those four (and any other skill without its own
  `LICENSE.txt`, e.g. `skills/doc-coauthoring`) are deliberately EXCLUDED from this gallery.
- Each entry below has its own per-skill `LICENSE.txt` in the upstream repo (not a single repo-wide
  LICENSE file) - verified individually, not assumed from the README's general statement:

| Gallery slug | Upstream path | Verified license |
|---|---|---|
| `brand-guidelines` | `skills/brand-guidelines/SKILL.md` | Apache License 2.0 (own `LICENSE.txt`) |
| `frontend-design` | `skills/frontend-design/SKILL.md` | Apache License 2.0 (own `LICENSE.txt`) |
| `internal-comms` | `skills/internal-comms/SKILL.md` | Apache License 2.0 (own `LICENSE.txt`) |
| `theme-factory` | `skills/theme-factory/SKILL.md` | Apache License 2.0 (own `LICENSE.txt`) |

## What was and wasn't vendored

Only each skill's own `SKILL.md` and its own `LICENSE.txt` are vendored here - not the upstream
folder's other bundled files (e.g. `internal-comms/examples/*.md`, `theme-factory/themes/*` +
`theme-showcase.pdf`). Some of these skills' instructions reference those additional files by path
(e.g. "load `examples/3p-updates.md`") - they are not present in this trimmed gallery entry, so an
agent following the skill literally may need to ask the user for that detail instead, or a maintainer
can fetch the missing files from the source path above and add them as bundled assets. This was a
deliberate trade-off to keep the gallery small and auditable rather than vendoring entire skill folders
(including scripts) wholesale.

## Modifications made

Per Apache-2.0 §4(b) ("You must cause any modified files to carry prominent notices stating that You
changed the files"), two mechanical changes were made, both disclosed here:

1. Every entry's `license:` frontmatter value was normalized from the upstream
   `Complete terms in LICENSE.txt` to the SPDX identifier `Apache-2.0`, so `load_gallery()`'s license
   filter (and `anthill/agent/skills.py::skill_md()`'s `license:` field, which this codebase already
   supports) can match on it programmatically.
2. `frontend-design/SKILL.md` had 5 em dash and en dash characters mechanically replaced with a plain
   hyphen, each in place, to satisfy this repo's own house-style lint (`.github/workflows/ci.yml`'s
   "Slop gates", `AGENTS.md` - no em/en dash in any tracked `.md` file). No
   words were added, removed, or reordered; only the punctuation mark changed.

Every other instructional body is otherwise byte-for-byte the same as the upstream `SKILL.md`. The
full, unmodified Apache License 2.0 text is included as each entry's own `LICENSE.txt` (Apache-2.0
§4(a)).

## Excluded candidates (checked, not vendored)

Also considered and rejected, to record that the exclusion was deliberate, not an oversight:
- `skills/docx`, `skills/pdf`, `skills/pptx`, `skills/xlsx` - explicitly source-available per the
  upstream README, not open source.
- `skills/doc-coauthoring` - no `LICENSE.txt` in its folder; license status unverified.
- `skills/skill-creator` - genuinely Apache-2.0, but deliberately not vendored here: this program's
  own spec (`docs/specs/knowledge-onboarding-and-guidance.md`, requirement 3) calls for Anthill's
  native guided wizard to reproduce `skill-creator`'s methodology WITHOUT embedding the tool itself
  ("too heavy for non-technical users") - vendoring it into the gallery would be exactly that.
- `skills/algorithmic-art`, `skills/mcp-builder`, `skills/webapp-testing`, `skills/canvas-design`,
  `skills/slack-gif-creator`, `skills/claude-api` - genuinely Apache-2.0, but each is built around
  bundled scripts/reference files that are load-bearing to actually using the skill; left out of this
  first, small gallery in favor of entries whose `SKILL.md` stands on its own (see above).
