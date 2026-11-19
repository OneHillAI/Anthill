# Spec: the guide describes backup and restore, what stays out of answers, the speed line and Linux

Status: implemented. Lane: `pillar:privacy`. Issue: #288. Follows #239, #280 and #285. Related: #220, #221, #284, #222, #145 to #147, #189.

## Problem

The key now lives in the Keychain and a backup is one passphrase-protected file that carries it (#220, #221), but
`docs/setup.md` gave that in one sentence. A person who has to restore, or who loses the Mac's saved key, could not find
who may make a backup, where the page is, what is in the file, or how a restore behaves. Three other points were
missing or inconsistent:

1. An upload that is waiting for a decision, was rejected or has been removed is no longer used in answers (#284), and
   `docs/using-chat-and-knowledge.md` did not say so.
2. The chat page shows a small "⏱" line with the time to the first word and the speed of an answer, measured on the model
   server (#222; `chat.html`), which no page described.
3. `README.md` and the start script cover Linux (#145 to #147, #189), while `docs/platform-support.md` said "Windows on
   ARM, Linux, Intel Mac: Not supported" with no word on running from source. That page is in the repo and is not
   published on the site.

## Requirements

1. `docs/setup.md` SHALL describe backup and restore: the install owner only; where the page is (Manage organisation,
   Backup) and that a Solo account has no menu link and opens `/backup`; the commands `anthill backup` and `anthill
   restore`; one `.anthillbak` file encrypted as a whole with a passphrase of at least 12 characters that cannot be
   recovered; what it holds (database, wiki, files, skills, the key) and what it does not (model files; `secrets.env`
   is never written as a file); what a restore checks, the safety copy and the restart; older plain backups; the
   pre-migration snapshots (database only, no key, no passphrase on the same computer); and what a backup does not
   protect, with the advice to keep disk encryption on. Facts are those in `backup.html`, `anthill/backup.py` and
   `anthill/cli.py`.
2. `docs/using-chat-and-knowledge.md` SHALL say that a held, rejected or removed upload is not used in answers (chat,
   agents, scheduled tasks, cached answers), and that an older upload may still be read until the install owner runs
   `anthill knowledge inventory --apply`. It SHALL also describe the speed line.
3. `docs/platform-support.md` SHALL separate Linux from the unsupported row and say what Linux means: no app or
   installer, and the README quick start covers running from source.
4. No claim SHALL go beyond the code: nothing about key rotation, nothing about the wiki, chats or cache being encrypted.
5. Existing headings, page order, links between pages and the publish whitelist SHALL NOT change; `docs/setup.md` gains
   one new heading, "Back up and restore".

## Decisions

The two decisions in #288 were not answered separately; the maintainer said to build the PR, which takes the recommended
option of each: the Solo reader is told the page is at `/backup`, and the "not used in answers" rule is stated with the
one-sentence caveat for older uploads, naming the command.

## Known limits

- The missing Solo menu link to the Backup page is an app matter and is not fixed here.
- An Ollama on another machine being treated as remote (#247) is not described: the field that sets the address could
  not be placed in the redesigned Settings.

## Acceptance

- Each fact in requirement 1 matches `backup.html`, `anthill/backup.py` and `anthill/cli.py`; the speed line matches
  `chat.html`; the Linux sentence matches `README.md`.
- The doc tests pass and `node docs-site/scripts/collect-docs.js` copies all eight pages.
- After merge, the live setup and chat pages show the new text.
