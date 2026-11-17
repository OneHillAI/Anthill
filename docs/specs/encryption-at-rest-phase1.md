# Spec: encryption at rest, phase 1 (the key leaves the data folder)

Status: parts 1 and 2 implemented, for review together. Lane: `pillar:privacy`. Phase 2 (encrypting the whole data
folder) is a separate change. Code: `anthill/keystore.py`, `anthill/web/key_startup.py` (part 1: the key),
`anthill/backup.py`, `anthill/backup_crypto.py`, `anthill/stored_values.py` (part 2: backups).

## Goal, and what it protects

Everything encrypted today stays AES-256-GCM, with the same key value, in the same format
(`base64(nonce + ciphertext + tag)`). That is the saved credentials only: provider and connector keys, the mailbox
password, MCP headers, Slack secrets (the `*_enc` columns). Phase 1 changes only where the key lives.

On a Mac the data key and the session secret are one item in the login Keychain. The data folder keeps a small
non-secret marker, `key-location.json` (version, "keychain", the item's account name, a short check value that cannot
be used to find the key). So a copy of the data folder is not enough to read the saved passwords.

Not protected, and said so in the app: a program running as you (the Keychain item can be read by any program of the
same user while the Keychain is unlocked); everything that is plain files today (the wiki, chats, files, the cache,
memories, the database apart from the `*_enc` columns); backups and copies of the data folder made before the key
moved, which still open every saved password (phase 1 does not change the key). Phase 2 covers the plain files.
Disk encryption (FileVault) is the cover for a lost or stolen Mac; the backup page shows whether it is on.

## Start-up (one decision table, `keystore.ensure_ready()`)

It runs before serving: `anthill web`, the desktop sidecar and the container entry. Any other way of starting the app
stops in the FastAPI startup handler with the same message. A refusal is plain words, exit status 1, no traceback,
and never a new key (not even with `ANTHILL_ALLOW_EPHEMERAL_KEY=1`).

| Case | What happens |
|---|---|
| Marker exists | Read the item. Item missing, unreadable or its check value differs: refuse. Else use it, and remove key lines from `secrets.env` and `.env` that equal the item (idempotent; this also finishes a move that crashed after the marker). A different key line is left with a note. |
| No marker, Keychain, legacy key (environment, `secrets.env`, `.env`) | One step: validate (32 bytes), add the item (never `-U`: an existing item is an error), read back, prove the key opens at least one stored value (if none opens: do not move, warn), write the marker, read both again, and only then remove the key lines. Any failed step deletes only what it made and the app starts on the legacy key with one plain sentence. |
| No marker, Keychain, no legacy key | Stored values exist: refuse. None: fresh install, make the pair, add the item, read back, write the marker. A failure leaves nothing behind and refuses. |
| No Keychain (Linux, Windows, Docker) | The key stays where it is today. No key and stored values: refuse. No key and none: `_ensure_secrets` or `start.sh` make one, as now. The interface says the key is in a plain file and not protected. |
| `ANTHILL_ALLOW_EPHEMERAL_KEY=1` | A throwaway key, only with no stored values and no marker. |

"Stored values" means any non-empty `*_enc` column or council-member copy. A database that exists but cannot be read
(locked, damaged) is a plain error, never "no values". `start.sh` asks the same probe before it generates a key.

## Nothing weaker than main (the key)

Each protection that exists on `main` in the files this change touches, and where it is now. Test names are in
`tests/test_keystore.py` unless a file is named. Result: none is weaker or gone. Where the behaviour differs it is
stricter. The cost is availability: a start that used to carry on with a made-up key now stops (see "Not verified").

| Protection | On main | Now | Test |
|---|---|---|---|
| AES-256-GCM, random 96-bit nonce, `base64(nonce + ct + tag)`, `encrypt(plaintext)` / `decrypt(token)` | `web/crypto.py` | same code and format; only the key's source changed | `test_the_ciphertext_format_and_the_function_signatures_are_unchanged`, `test_ciphertext_made_before_the_move_still_opens_after_it` |
| Key must be 32 bytes | `ValueError` at import, lenient base64 | `KeyUnavailable`, strict base64, before serving | `test_a_legacy_key_that_is_not_32_bytes_is_refused` |
| No silent throwaway key | a missing key became a random one, so data was lost on restart | refused unless `ANTHILL_ALLOW_EPHEMERAL_KEY=1` and no stored values and no marker | `test_stored_values_and_no_key_refuse_and_make_nothing`, `test_the_dev_switch_gives_a_throwaway_key_only_with_no_values_and_no_marker` |
| Session tokens: HS256 only, clock leeway, TTL, renewal, revocation | `web/crypto.py` | unchanged; the secret comes from the key layer (random per process if none is set, as on main) | `tests/test_session_token_pyjwt.py`, `tests/test_jwt_live_revocation.py` |
| Slack OAuth `state` is signed and timed | JWT secret from the environment, else the fixed text `anthill-dev` | key derived from the JWT secret; no fixed fallback | `test_a_token_signed_with_the_old_fixed_or_raw_secret_is_refused`, `test_no_fixed_fallback_secret_is_left_in_the_code`, `tests/test_slack_bot.py::test_oauth_callback_rejects_bad_state` |
| Task-result review link is signed (CSRF) | the JWT secret directly | derived key, own purpose | `tests/test_task_result_review.py::test_review_is_csrf_protected`, `test_signing_keys_differ_by_purpose_and_come_from_the_jwt_secret` |
| `secrets.env` is 0600 | `desktop._ensure_secrets` | same where it is written; not written at all with a Keychain; files the key layer rewrites are 0600 | `tests/test_key_startup.py::test_without_a_keychain_a_fresh_install_gets_a_key_file_as_before`, `test_files_the_key_layer_rewrites_stay_private` |
| Existing key never overwritten by the desktop or `start.sh` | only a missing line was added | same, and no key is made while stored values exist | `tests/test_key_startup.py::test_without_a_keychain_a_missing_key_over_stored_values_is_refused`, `tests/test_start_sh.py::test_a_database_with_encrypted_values_and_no_key_stops_instead_of_making_one` |
| `.env` is 0600 | set when `start.sh` made it | set on every write | `tests/test_start_sh.py::test_a_fresh_project_gets_both_secrets_once_when_the_database_is_empty` |
| Bring-in: empty install, this machine only, same origin, folder guard, links skipped, env read capped at 64 KiB, staged database 0600, `secrets.env` 0600, undo on failure | `web/bring_in.py` | unchanged, plus the imported key must open every stored value, keys stay out of the environment and files with a Keychain, and the key swap is undone on failure | `tests/test_first_run_bring_in.py` (the `test_refuses_*` family, `test_a_link_that_stays_inside_the_folder_is_skipped_not_followed`, `test_bringing_in_a_project_folder_brings_the_account_wikis_and_keys`, `test_a_failure_after_the_new_item_is_made_restores_the_old_marker_and_removes_it`) |
| Audit events from bring-in or key handling | none on main | none added to the audit log (key steps go to the logger, no secret in them) | n/a |
| Database file 0600 | `web/db.py` `_secure_db_file` | not touched | n/a |

## Recovery cases

- **Fresh install:** item and marker made at first start; nothing written to `secrets.env`.
- **Update from a legacy install:** the one-step move above. Same values, nothing re-encrypted.
- **No Keychain:** unchanged. Honest limit: the key is in a plain file.
- **Missing item** (removed, or another login keychain): refuse. The data stays; restoring the item or a backup that
  holds the key reopens it.
- **Data folder copied to another Mac:** the marker points at an item that is not there: refuse. That is the point.
- **Restore of a backup:** see "Protected backups". A backup carries its own key, so a copy restored on another Mac, or
  after the Keychain item was lost, opens its saved passwords.
- **Bring-in of an earlier copy:** the imported key (base64, 32 bytes) is validated first, and must open every stored
  value in the staged database (an earlier copy with no stored values is fine; "continue without the key" is unchanged).
  `keystore.adopt` adds a new item, repoints the marker (atomic) and keeps the old item until the bring-in succeeded;
  a failure restores the old marker and deletes the new item.

Residual risk: if the Keychain item is lost after the plain copies are gone, the saved passwords cannot be read. Recovery
is a protected backup, which carries the key (below); without one, re-entering the credentials is the way back.

## Protected backups

One `.anthillbak` file (`anthill backup`, the Backup page). The whole archive is encrypted, so the file shows nothing
without the passphrase. Format: a header (magic, version, scrypt cost, salt, chunk size, nonce prefix), then AES-256-GCM
chunks of 1 MiB. The key is scrypt of the passphrase; chunk `i` has nonce `prefix + i` and associated data
`header + i + final flag`, so a chunk cannot move, repeat or be dropped, and cutting the file short fails. Both
directions stream. A header is untrusted: scrypt cost is capped per field and together (memory 128*n*r at most 256 MiB,
n*r*p at most 2**21). The passphrase for a NEW backup is at least 12 characters (restore does not enforce it), comes from
a file, the environment or a hidden prompt on the command line, and is never in a URL or argv.

Inside: the database, the wikis, files and skills, and `data/key-export.json` (the data key, the session secret, and
the other lines of `secrets.env`, such as optional search keys a bring-in copied there, which main backed up with that
file). Never inside: the `secrets.env` file, `key-location.json`, model files. The manifest says so. Older plain `.tar.gz` backups still
restore, with a plain warning that they were not protected, and are refused unless they carry a key.

Restore, in this order: read the whole archive into a staging folder beside the data and verify every chunk; check that
the archive's key opens EVERY stored encrypted value in the archive's database (a plain refusal otherwise, and above
5000 values); only then take a safety copy (a protected backup with the same passphrase) and put the new state in place,
moving the old database aside. A wrong passphrase, a damaged or cut-short file, or any failed step leaves the install
as it was (everything is put back, the safety copy is kept). If the backup's key differs from this install's, the key is
swapped: with the Keychain, `keystore.adopt` adds a new item and repoints the marker, the old item is removed only after
everything worked and put back if not; with no Keychain, the key lines go into `secrets.env` and the old file is kept
aside. A restart is needed afterwards: the running copy still holds the old database. Stale `.restore-staging-*` folders
(a crashed restore) are removed at the next start, only real folders of that name in the data home, never one in use.

Snapshots (pre-migration copies of the database) carry no key and need no passphrase; restoring one warns, but still
restores, if this install's key does not open every saved password in it. Key copies an older version left beside
snapshots are removed with the other plain copies when the key moves into the Keychain.

Not protected by a backup: the files on this computer while Anthill runs, and any program running as you; the wiki, chats
and cache are plain files there. Older backups and old copies of `secrets.env` still open every saved password.

### Nothing weaker than main (backups)

Test names are in `tests/test_backup_protected.py` unless a file is named. Result: none is weaker or gone. Two
behaviours changed on purpose: model files are no longer in a backup, and export is a POST (the passphrase must not be
in a URL).

| Protection | On main | Now | Test |
|---|---|---|---|
| Admin check, and the install owner (or the local desktop admin) for export and restore; others get 403 | `_require_admin`, `_require_install_scope` in `web/app.py` | same dependencies on the POST export and the restore | `tests/test_backup_install_owner.py` (export and restore, owner, other admin, member, local and remote) |
| The page shows the controls only to someone who may use them | `backup_page` | same | `tests/test_backup_install_owner.py::test_the_backup_page_shows_the_controls_only_to_someone_who_can_use_them` |
| Audit events `backup.export`, `backup.restore`, `backup.restore_failed` | `web/app.py` | same three names; "protected", "unprotected" or "failed closed" is in the detail. `backup.export_failed` is new (main logged no failed export) | `test_exporting_through_the_page_audits_and_streams_a_protected_file`, `test_restoring_through_the_page_fails_closed_on_a_wrong_passphrase_and_audits`, `test_an_old_plain_backup_through_the_page_is_audited_as_unprotected` |
| Archive paths: no absolute or `..` names | `_safe_members` | same skip, plus a resolved-path check, and only regular files and folders are read (links, devices skipped), a file listed twice is refused | `test_a_hostile_archive_cannot_write_outside_the_data_folder_or_plant_links`, `test_a_file_listed_twice_or_too_much_data_is_refused_before_anything_changes` |
| Size limits | none on the restore upload | the unpacked size is capped to a share of free disk space and the entry count is capped | `test_a_file_listed_twice_or_too_much_data_is_refused_before_anything_changes` |
| A non-backup is refused without touching data | `read_manifest` first | the kind and the whole archive are checked first | `tests/test_backup.py::test_restore_rejects_a_non_backup`, `test_wrong_passphrase_and_damage_fail_closed_and_touch_nothing` |
| A safety copy of the current state before a restore | plain `.tar.gz` with the key | a protected backup, kept on failure | `tests/test_backup.py::test_restore_is_point_in_time_and_the_safety_copy_keeps_the_newer_wiki_page`, `test_restore_on_another_mac_swaps_the_key_item_and_keeps_the_old_one_until_it_worked` |
| A restore is point-in-time and the newer org wikis are set aside, not deleted | `restore_backup` | same, with everything put back on a failed step | `tests/test_org_wiki_per_org.py::test_restoring_an_archive_from_before_org_wikis_moves_the_current_org_wikis_aside`, `test_a_failure_while_putting_data_in_place_puts_everything_back_and_keeps_the_safety_copy` |
| A consistent copy of a live database | SQLite backup API | same | `tests/test_backup.py::test_backup_and_restore_roundtrip` |
| File modes: `secrets.env` 0600 after a restore | `os.chmod` | 0600 when written | `test_a_restore_without_a_keychain_writes_secrets_env_privately` |
| File modes: backups, restored database, snapshots | default umask | backup, safety copy and restored database 0600; snapshot folder 0700, files 0600 | `test_backup_restore_and_snapshot_files_are_private` |
| The key in the archive | in the clear (`secrets.env`) | encrypted with the whole archive | `test_the_archive_shows_nothing_without_the_passphrase` |
| Optional keys kept in `secrets.env` are backed up and restored | the file was archived whole | carried inside the encrypted export and written back | `test_restore_on_another_mac_swaps_the_key_item_and_keeps_the_old_one_until_it_worked`, `test_an_old_plain_backup_with_a_key_restores_with_a_plain_warning` |
| Snapshots carry no key | `secrets.env` was copied beside each | nothing copied; old copies are removed | `test_snapshots_carry_no_key_and_the_old_key_copies_beside_them_are_cleaned` |
| Old plain backups still restore | yes | yes, with a warning, refused without a key | `test_an_old_plain_backup_with_a_key_restores_with_a_plain_warning`, `test_an_old_plain_backup_without_a_key_is_refused_unless_allowed` |

Unchanged and still open: the restore upload is written to the system temp folder under a name taken from the file name
(as on main), and plain backups made by earlier versions stay in `<data>/backups` with their key until deleted.

## What was not verified

The real Keychain (`security -i` is tested against a stubbed subprocess only), the packaged app, an app update over an
existing install, a locked Keychain at login, and `fdesetup` output on a real Mac. Concurrent starts are not locked:
two Anthills starting at once on one data folder could each add an item; the marker that wins is consistent and the
other item is left unused.

## How phase 2 builds on this

Design only; nothing here is built. `keystore.data_key()` stays the single place that hands out the data key, so
phase 2 changes who asks it, not where it lives.

1. **One read and write layer for files.** The wiki, uploads, chats, memories and the cache are read and written in many
   places. Phase 2 first routes them through one small layer (`read_bytes`, `write_bytes`, atomic) that is a plain
   pass-through today, then makes it encrypt. Nothing is encrypted until every writer uses it.
2. **Search and the semantic cache.** An encrypted page cannot be searched on disk. Two choices: keep a search index and
   embeddings in memory only, rebuilt at start (slower start, nothing plain on disk), or keep an index on disk and
   encrypt it too (faster, same layer). Embeddings can be inverted, so they count as the text they came from. The
   semantic cache is rebuildable, so it can be dropped when the key changes.
3. **A versioned ciphertext prefix, added later.** New values get a prefix such as `ah1.<key id>.<base64url>`, with the
   `table.column` as associated data so a value cannot be moved to another field. Values with no prefix keep reading
   with the current rule, so nothing needs re-encrypting at once. The key id is what makes key rotation possible. The
   earlier large branch (`privacy/encryption-at-rest-phase1`) is the model; none of it is built.
4. **A lost key.** Phase 2 raises the cost of losing the key from "re-enter the saved passwords" to "the whole data
   folder is unreadable". So it needs a recovery route before it starts: the protected backup above, checked by a restore
   test, and a refusal to encrypt the plain files until a backup has been verified.

Phase 2 also needs the key to be rotatable (the prefix above), a per-process cache of the unwrapped key, and a decision
about whether a passphrase-protected key file is offered where there is no Keychain.
