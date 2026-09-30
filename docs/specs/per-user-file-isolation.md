# Spec: per-user file isolation within an org

Status: accepted
Lane: `pillar:privacy`
Relates to: the codebase security review (PR #432 acknowledged this as a residual).

## Thesis

Agent-created downloadable files were scoped to the **org** only (`data/files/<org>/`, with unguessable
token names). Within a single org, one member's agent could still `list_files`/`read_file` (or, with the
name, download) another member's file. For a privacy-first product this per-user boundary should hold too,
even though deployments are one-org-enforced (so this is defence-in-depth, not a cross-tenant hole).

## Requirement

- WHEN a file is created for a request that has a single end-user, the system MUST scope it to that
  **user within the org**, not the org alone.
- WHEN a member requests to list, read, or download files, the system MUST only surface files owned by
  that same user.
- WHERE a request has no single end-user (agent-to-agent / a2a), the system MAY fall back to org scope.

## Design

A single canonical owner key, `files_owner(org, user_id)`, returns `"<org>-u<user>"` (per-user) or
`"<org>"` (org-only fallback). It is **slash-free** so it resolves to the same directory whether it is
sanitised (`_files_dir`) or used as a raw path segment (`_files_target`). Both the agent file tools
(`make_tools(owner=...)` at the chat, scheduler, and agent-run sites) and the `/files` create + serve
routes (`_files_owner`) use it. a2a stays org-scoped.

## Acceptance

- Two members of the same org resolve to different owner dirs; member A cannot read/serve member B's file.
- A member can still create, list, read, and download their own files.
- No-end-user callers (a2a) keep the prior org-only behaviour.
- Regression tests in `tests/test_security_fixes.py`.

## Non-goals / notes

- Migration: files already under `data/files/<org>/` are not readable from the new per-user dirs. There is
  deliberately **no read fall-back to the org dir** (that would reintroduce the leak). Acceptable
  pre-launch.
