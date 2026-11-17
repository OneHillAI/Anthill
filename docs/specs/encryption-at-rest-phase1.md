# Spec: encryption at rest, phase 1 (the key leaves the data folder)

Status: part 1 implemented, for review. Lane: `pillar:privacy`. Part 2 (protected backups) and phase 2 (encrypting
the whole data folder) are separate changes. Code: `anthill/keystore.py`, `anthill/web/key_startup.py`.

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
