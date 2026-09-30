# Changelog fragments

Add your PR's changelog entry as a **fragment** here instead of editing `CHANGELOG.md`. Because two PRs
never touch the same file, changelog merge conflicts become structurally impossible (that's the whole
point). At release time the fragments are assembled into a `## [X.Y.Z]` section and deleted.

## Add an entry

Create one file named `<id>.<category>.md`:

- `<id>` = your PR or issue number (e.g. `513`), or a short `+slug` if you don't have a number yet
  (e.g. `+escalation-consent`). Both are fine; numeric ids just sort first.
- `<category>` = one of `added`, `changed`, `deprecated`, `removed`, `fixed`, `security`
  (the Keep a Changelog headings).
- Content = the bullet body only, the same prose you'd write today. **No** leading `-` and **no**
  heading. Plain hyphens only (the em/en-dash slop gate scans this directory too).

A PR may add more than one (e.g. `513.fixed.md` and `513.security.md`).

```
changelog.d/513.security.md
------------------------------------
**Cloud escalation now requires per-use consent.** ...one paragraph, as usual...
```

## Preview / assemble

```
scripts/build_changelog.py --draft                # print the pending [Unreleased] section, changes nothing
scripts/build_changelog.py 0.11.5 2026-07-20      # cut the section into CHANGELOG.md and clear this dir
```

The release process runs the assembler; you normally only add fragments. `check-release.sh` fails a
release if any fragment is left unassembled.
