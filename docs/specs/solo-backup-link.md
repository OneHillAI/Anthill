# Spec: Settings links the Backup page, so a Solo account can find it

Status: implemented. Lane: `pillar:privacy`. Issue: #301. Related: #204 (Solo and Org stay separate), #220 and #221 (the key in the Keychain, protected backups), #241 (words only on the first-run page), #288 (the backup section of the guide).

## Problem

The Backup page (`/backup`) is open to a Solo account's owner: `docs/specs/solo-org-separation.md` leaves `/backup*` open
because it "is the only UI to back up a person's own install". But every link to it was on an organisation page
(`_org_tabs.html`, `org_settings_hub.html`, `settings_general.html`, `settings_org_wiki.html`). The Solo dashboard, the
Settings tabs and the sidebar had none, and the desktop app has no address bar, so a Solo Mac user could not open
`/backup`; the only way to back up was the `anthill backup` command in a terminal. Since the encryption key moved to the
Keychain, the encrypted backup that carries the key is the way back if the Mac's saved key is lost, so this mattered.

The Integrations tab had the same problem (founder report, 2026-10-01) and was fixed with its own Settings tab.

## Requirements

1. Settings, on the **This device** tab, SHALL show a **Backup** card to an admin, with a short description and a link
   to `/backup` labelled "Back up and restore". It SHALL NOT be shown to a user who is not an admin.
2. The card sits between Appearance and Version. No new route, no new tab, no change to the Solo dashboard.
3. `docs/setup.md`, "Back up and restore", SHALL name this route first (Settings, This device tab, Back up and restore)
   and keep Manage organisation, Backup for an organisation account.
4. `docs/specs/solo-org-separation.md` SHALL record that Settings, This device, now links the page.
5. The Backup page, its routes, who may make or restore a backup (`install_scope_allowed`) and the passphrase rules
   SHALL NOT change; the page still shows one line to someone who may not use it.

## Decision

Decided by the maintainer on 2026-11-19 ("Both, one after the other"): the recommended placement in #301, the This
device tab.

## Tests

`tests/test_settings_backup_link.py`: a Solo account created through first-run setup finds the link, the label and the
sentence in the This device panel (it fails without the template change, at the link assertion); a member who is not an
admin gets no link.

## Known limits

- The card is shown to an admin, as the Integrations tab is. On a shared server an admin who is not the install owner
  sees the link and then the page's one-line explanation.
- Not checked in the desktop app window; the tests render the page.

## Acceptance

- The new tests pass and the Solo one fails without the template change; the neighbouring Settings, first-run, Solo
  separation and backup tests pass; the full suite passes in CI.
- On a fresh Solo install, Settings > This device shows the Backup card and the link opens the page.
