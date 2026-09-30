# Agent Skills in Anthill (agentskills.io conformant + governed)

Anthill's skills are **conformant [agentskills.io](https://agentskills.io) skills** - the open Agent
Skills format - **extended** with Anthill's governance and routing fields. This is the same
adopt-and-extend move [OKGF](OKGF.md) makes over OKF: portable underneath, governed on top.

## What a skill is

A folder whose name is the skill's (lowercase-hyphenated) name, containing a `SKILL.md`:

```
skills/pdf-processing/
├── SKILL.md          # required: YAML frontmatter + Markdown instructions
├── scripts/          # optional: executable code
├── references/       # optional: extra docs loaded on demand
└── assets/           # optional: templates, data
```

Skills live in the built-in `skills/` dir (`ANTHILL_SKILLS_DIR`) and in each workspace's skills dir
(personal / team / org), so a skill is scoped like the rest of the knowledge ladder.

## The frontmatter

### Base fields (agentskills.io - portable to any skills-compatible agent)

| Field | Required | Rule |
|---|---|---|
| `name` | yes | 1-64 chars, lowercase letters/numbers + single hyphens (no leading/trailing/consecutive); **equals the folder name** |
| `description` | yes | 1-1024 chars, non-empty; what it does + when to use it (see below - `skill_md()` folds `when_to_use` in automatically when both are given) |
| `license` | no | license name or bundled-file reference |
| `compatibility` | no | <= 500 chars; environment requirements |
| `allowed-tools` | no | space-separated pre-approved tools (experimental) |

### Anthill extensions (the `x-anthill-*` namespace - base-standard tools ignore these)

| Field | Meaning |
|---|---|
| `x-anthill-title` | human display title (the base `name` is a slug, so the UI shows this) |
| `x-anthill-when-to-use` | one-line trigger, used by the cheap keyword matcher at run time |
| `x-anthill-tier` | provenance: `builtin` \| `personal` \| `team` \| `org` |
| `x-anthill-scopes` | permission scopes this skill is allowed to assume (governance) |

The current agentskills.io spec treats "when to use" as part of `description`, not a separate field -
a tool that only reads the base `description` (ignoring `x-anthill-when-to-use`) would otherwise never
learn when a skill applies. So `skill_md()` folds `when_to_use` into `description` ("`<description>` Use
when: `<when_to_use>`") whenever both are given, while still ALSO emitting `x-anthill-when-to-use`
unchanged (Anthill's own cheap keyword matcher and the authoring UI read that field directly).

Example:

```markdown
---
name: pdf-processing
description: Extract text and tables from PDFs and fill forms. Use when: when handling PDFs, forms, or document extraction
license: Apache-2.0
x-anthill-title: PDF Processing
x-anthill-when-to-use: when handling PDFs, forms, or document extraction
x-anthill-tier: org
x-anthill-scopes: files, docs
---

1. ...instructions...
```

## Conformance + interop

- **Written skills are valid agentskills.io skills** (`anthill/agent/skills.py::skill_md`): the base
  `name`/`description` conform, the folder matches the name, and governance is namespaced under
  `x-anthill-*`. A conformant skill from another tool drops straight into a skills dir and loads.
- **Reader is backward-compatible**: it still accepts Anthill's pre-standard frontmatter (a title-cased
  `name`, top-level `when_to_use`/`scopes`/`tier`) and a loose single-file `skills/<slug>.md`.
- **Validation**: `validate_skill_name()` enforces the spec name rules; `conform_name()` normalises any
  label to a conformant, folder-matching name.
- **Progressive disclosure**: only each skill's name + description are considered at match time (cheap
  keyword overlap, no model call); the full body is injected into the system prompt only when a skill is
  activated for the task.

## Governance (why the extensions exist)

An agent's skill is a *procedure it will reuse* - a supply-chain surface, not just a note. The
`x-anthill-*` fields let Anthill scope a skill (personal stays personal; an org/team skill goes through
the wiki review gate before it is shared), record its provenance (`tier`), and bound what it may do
(`scopes`). The base agentskills.io layer keeps skills portable; the governance layer keeps them safe.
