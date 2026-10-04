# Spec: backup and restore cover the wikis and the original uploads

Status: accepted
Lane: `pillar:privacy`
Relates to: `anthill/backup.py`, `anthill/desktop.py` (`_apply_paths`), `anthill/wiki/workspace.py` (`workspace_for`), the admin Backup page.

## 1. Problem

A person's knowledge is theirs to keep, and a backup is the only way to keep it through a disk failure, a
bad update or a move to a new machine. `create_backup` archived the database, the legacy `workspace/`
folder, `files/`, `skills/` and `secrets.env`, and nothing else.

The packaged app does not keep wikis in `workspace/`. `desktop._apply_paths` sets `ANTHILL_WIKI_ROOT` to
`<home>/wikis` and `ANTHILL_ORG_WIKI` to `<home>/org-wiki`, and `workspace_for` puts a personal wiki at
`wikis/user-<id>`, a project wiki at `wikis/team-<id>` and the org wiki at `org-wiki`. Each wiki holds its
pages (`wiki/`) and the original uploads (`raw/`). The cloud org backend (`anthill/server.py`) uses the same
layout. None of this was in the archive.

Verified on a throwaway home laid out by `desktop._apply_paths` (2026-10-04): 11 files on disk, 5 in the
archive. No wiki page, no original upload, no org wiki. The consequences:

- A backup restored on a new machine, or after a deleted data folder, brings back the database but none of
  the knowledge it points at.
- `restore_backup` has the same list, so a restore leaves the current wikis as they are while the database
  rolls back. The two end up out of step.
- The safety copy `restore_backup` takes first is built by `create_backup`, so it did not protect the wikis
  either. A mistaken restore could not be undone for them.
- The Backup page says the archive contains "the wiki". It did not.
- `tests/test_backup.py` only used the legacy `workspace/` folder, so nothing noticed.

The layout has been in the packaged app since 0.1.2, so every packaged install is affected.

## 2. Requirements

- R1. WHEN a backup is created, the system MUST include the per-user and per-project wikis
  (`ANTHILL_WIKI_ROOT`) and the org wiki (`ANTHILL_ORG_WIKI`), pages and original uploads both.
- R2. The two locations MUST resolve with the same variables and defaults the wiki code uses
  (`data/wikis`, `data/org-wiki`), so a dev run, the container and the packaged app all behave alike.
- R3. WHEN a backup is restored, the system MUST restore those wikis as a full replace, like the other
  trees, so the result is a true point in time that matches the restored database.
- R4. The pre-restore safety copy MUST include the wikis, so a mistaken restore is reversible for them too.
- R5. WHEN the archive being restored has no wiki folders (a backup made before this change), the system
  MUST leave the current wikis untouched. An older backup never deletes knowledge it does not contain.
- R6. Backup and restore MUST walk one shared list of trees, so a tree cannot be saved without being
  restored.

## 3. Design

`data_paths()` gains `wikis` and `org_wiki`. A module-level `_TREES` list names every archived tree and its
archive path (`data/workspace`, `data/wikis`, `data/org-wiki`, `data/files`, `data/skills`). `create_backup`
and `restore_backup` both loop over it. The manifest `includes` list gains `wikis` and `org_wiki` when they
are present.

The manifest format number stays at 1. The change only adds folders to the archive and no code reads the
number. An older build restoring a new archive ignores the folders it does not know, which is today's
behaviour and no worse. A bump would give old builds no protection because they never check it.

The archive grows by the size of the original uploads, and so does the safety copy. Each upload is capped
at 25 MB, so the growth is bounded by what the person has stored.

## 4. Acceptance criteria

- On a home laid out by `desktop._apply_paths`, the archive contains a personal wiki page, an original
  upload, a project wiki page, an org wiki page and an org original, and `includes` lists `wikis` and
  `org_wiki`.
- After deleting `wikis/` and `org-wiki/`, a restore brings every file back with its contents.
- A wiki page written after the backup is gone after the restore, and is present in the safety copy.
- Restoring an archive that has no wiki folders leaves existing wikis as they were.
- The existing round-trip, snapshot, model and admin-page tests still pass.

## 5. Not in this change

- Fine-tuned adapters under `<home>/adapters`. The fine-tuned model is embedded through Ollama instead.
- The semantic cache and the central index. Both are rebuilt from the database and the wikis.
- A scheduled automatic backup. Backups still run when an admin asks for one.
- File identity, honest deletion and a file library (see the file manager research).
