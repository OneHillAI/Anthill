# Spec: bring an earlier copy's account into a new install

Status: implemented. Lane: `pillar:platform`.

## Problem

A person who had used Anthill from a project folder (`bash start.sh`, `make alpha`) downloaded the desktop app and
could not sign in: "you can only set up and not sign in". The two copies keep their data in different places.

- A project copy keeps the database in `data/anthill.db`, per-user wikis in `data/wikis`, org wikis in
  `data/org-wiki` and `data/org-wikis`, files in `data/files`, skills in `data/skills`, the personal workspace in
  `workspace`, and its security keys (`ANTHILL_JWT_SECRET`, `ANTHILL_ENCRYPTION_KEY`, optional provider keys) in
  the project's `.env`.
- The desktop app keeps everything under its own data home (`~/Library/Application Support/Anthill`), with the keys in
  `secrets.env`. It starts empty.

On an empty database `/login` redirects to `/setup`, and the first-run page hides its "Sign in" link, so the only
choice was to create a second, empty account. The earlier account was never found.

## Requirements

1. The first-run page (no organisation yet) shows a secondary section, "Already used Anthill on this Mac? Bring in your
   account.", with one text field for the folder of the earlier copy: the project folder that holds `data/`, or that
   `data` folder. One sentence says how to find it. It also says, in muted text, which folder this app keeps its data in.
2. `POST /setup/bring-in` works only when all of these hold, otherwise it returns 403 and changes nothing: the install
   has no organisation; the server is not shared (`ANTHILL_LOCAL_ONLY=1` on a loopback bind, Remote access off); the
   request comes from the machine itself (loopback client, loopback `Host`, no `X-Forwarded-*`, `Forwarded`, `Via` or
   similar header, so a request through a proxy or tunnel is refused); and any `Origin` header names the same host (a
   page on another site cannot post the form). The section is shown only under the same conditions.
3. The folder is untrusted input. It must be an absolute path (or `~`), an existing folder, not the app's own data home
   or inside it, and hold `data/anthill.db` (or `anthill.db` when given the data folder). Only these names under the old
   folder are read: `data/anthill.db`, `data/wikis`, `data/org-wiki`, `data/org-wikis`, `data/files`, `data/skills`,
   `workspace`, `.env` and `data/secrets.env`. A link that resolves outside the old folder, anywhere in what is read,
   refuses the whole bring-in; a link that stays inside is skipped, not followed.
4. The old database is opened read-only (`immutable` when it has no `-wal` file, so nothing is written next to it;
   `mode=ro` when it has one, so unmerged writes are read). It must have `organizations` and `users` tables and at least
   one user, and the copy must pass `PRAGMA quick_check`. The copy is made with SQLite's backup API, so it is
   consistent whether or not the old copy is running.
5. Everything is copied, never moved; the old folder is left as it was. Copies are built in temporary folders beside their
   destinations and renamed into place, so a failure while copying leaves the new install unchanged. A file that already
   exists in the new install is never overwritten (the app's starter skill, for example, stays). The database is swapped
   in last.
6. From `.env` and `secrets.env` only a fixed list of names is read: `ANTHILL_JWT_SECRET`, `ANTHILL_ENCRYPTION_KEY`,
   `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `FIRECRAWL_API_KEY`, `JINA_API_KEY`, `MEILI_URL`, `MEILI_API_KEY`. They
   are merged into the app's `secrets.env` (mode 0600) and set in the running process, including the live encryption key,
   so encrypted fields from the old database read at once. An encryption key that is not 32 bytes of base64 counts as
   missing. Values are never logged or shown.
7. Without the old encryption key (no `.env`, or none in it) the person is told, on the same page, that the account and
   wikis will still come across but passwords and API keys stored in the earlier copy cannot be read and need entering
   again, and is offered "Continue without the saved keys". Nothing is copied until they do. The app keeps its own keys.
8. After the swap the engine and session factory are rebuilt on the new database with the normal start-up migrations,
   the owner and legacy-wiki start-up steps are re-run, and the person is redirected to `/login?brought_in=1` (or
   `brought_in=keys` when the keys did not come), which shows a one-line notice. `/login` on an empty install still
   redirects to `/setup`.

## Known limits

- It brings in one earlier copy, once, onto an install with no organisation. It does not merge two installs, and an
  install that already has an account cannot bring one in (refused with 403).
- A `data` folder that is itself a link to a folder outside the project is refused; point at the real folder instead.
- Folders are copied in one request, so a very large `files` folder makes the click slow. There is no progress bar.
- Without the old encryption key, encrypted fields in the copied database stay unreadable; the person must re-enter them.
- Paths stored inside the old database (for example a file location outside the data folder) are not rewritten.
- A background job that held the old engine mid-swap could fail once; it reconnects to the new file on its next run.
- Run the app behind a proxy or tunnel and the section is hidden and the endpoint refuses, by design.

## Acceptance

- On an empty install, `/setup` shows the bring-in section and the app's data folder; `/login` still redirects to `/setup`.
- Bringing in a project folder, or its `data` folder, lets the person sign in with the old email and password, the old wiki
  pages exist, `secrets.env` is 0600 and holds the old keys, and an encrypted field from the old database decrypts.
- Refused with the new install unchanged: not a folder, no `anthill.db`, a SQLite file that is not Anthill's or has no
  account, the app's own folder, a link that leaves the folder (in a tree, `.env` or the database), an install that already
  has an organisation, a non-loopback client or `Host`, a forwarding header, a cross-site `Origin`, a server not marked local.
- A missing or unusable `.env` asks first, then continues with the app's own keys; only allowlisted names are copied.
- Tests: `tests/test_first_run_bring_in.py` (model-free).
