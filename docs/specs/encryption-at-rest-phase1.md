# Spec: encryption at rest, phase 1 (the key leaves the data folder)

Status: part 1 implemented, for review. Lane: `pillar:privacy`. Part 2 (protected backups) and phase 2 (encrypting
the whole data folder) are separate changes. Code: `anthill/keystore.py`, `anthill/web/key_startup.py`.

## Problem

The key that protects the saved credentials (`ANTHILL_ENCRYPTION_KEY`) and the session secret
(`ANTHILL_JWT_SECRET`) sit in a plain file beside the data (`secrets.env`, or `.env` on a source install). A copy of
the data folder therefore opens every saved password, token and API key. A missing key is also replaced silently by a
random one, which makes stored values unreadable with no warning.

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
- **Restore of a backup:** `secrets.env` comes back as the archive had it. A pre-move archive has the key lines, which
  equal the item and are removed at the next start. A post-move archive has no key: saved passwords need this Mac's
  item. Restoring an archive made under a different key is not handled in part 1 (the item wins).
- **Bring-in of an earlier copy:** the imported key (base64, 32 bytes) is validated first, and must open every stored
  value in the staged database (an earlier copy with no stored values is fine; "continue without the key" is unchanged).
  `keystore.adopt` adds a new item, repoints the marker (atomic) and keeps the old item until the bring-in succeeded;
  a failure restores the old marker and deletes the new item.

Residual risk: if the Keychain item is lost after the plain copies are gone, the saved passwords cannot be read. Recovery
is a protected backup that carries the key (part 2). Until then, re-entering the credentials is the way back.

## Acceptance

- On a Mac with the Keychain available, `secrets.env` and `.env` no longer hold the data key or session secret after
  the move, and every stored value still decrypts (`tests/test_keystore.py`, `tests/test_key_startup.py`).
- A missing Keychain item, or one that does not match the marker, stops the start with a plain message and never makes
  a new key, not even with the development switch when stored values exist (`tests/test_key_startup.py`).
- A failed migration step leaves the old copy in place and deletes only what it created (`tests/test_keystore.py`).
- With no Keychain the key stays where it is today and the interface says so (`tests/test_keystore.py`,
  `tests/test_start_sh.py`).
- `crypto.encrypt` / `decrypt` keep their signatures and format; the Slack and review-link secrets come from the key
  layer with no fixed fallback.
- The tables above name the test that covers each protection that exists on main.

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
   earlier large branch (`privacy/encryption-at-rest-phase1`) is the model; none of it is built in part 1.
4. **A lost key.** Phase 2 raises the cost of losing the key from "re-enter the saved passwords" to "the whole data
   folder is unreadable". So it needs a recovery route before it starts: a protected backup that carries the key
   (part 2), checked by a restore test, and a refusal to encrypt the plain files until a backup has been verified.

Phase 2 also needs the key to be rotatable (the prefix above), a per-process cache of the unwrapped key, and a decision
about whether a passphrase-protected key file is offered where there is no Keychain.
