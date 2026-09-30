# Spec: bundle identifier rebrand to `org.onehill.anthill`

Status: accepted (pre-public-flip). Lane: chore.

## Context

The app's machine identifiers were minted under the retired `Colonies` brand:

- Tauri app identifier: `ai.colonies.anthill` (`src-tauri/tauri.conf.json`).
- PyInstaller sidecar bundle identifier: `dev.colonies.anthill` (`Anthill.spec`).
- Appliance / LaunchAgent label: `ai.colonies.anthill` (`anthill/hosting/appliance.py`).
- Appliance installer package ids: `ai.colonies` / `ai.colonies.anthill.appliance` (`scripts/appliance-pkg/distribution.xml`, `scripts/build-appliance-pkg.sh`).

The project rebranded to the Onehill Foundation (`onehill.org`). These identifiers are the last `colonies` namespace in the app's identity and must move before the public flip.

## Decision

Adopt the reverse-DNS of the Foundation domain, `org.onehill`, as the identifier root:

| Old | New |
| --- | --- |
| `ai.colonies.anthill` (Tauri app) | `org.onehill.anthill` |
| `dev.colonies.anthill` (PyInstaller sidecar) | `org.onehill.anthill` |
| `ai.colonies.anthill` (LaunchAgent label) | `org.onehill.anthill` |
| `ai.colonies` (installer organization) | `org.onehill` |
| `ai.colonies.anthill.appliance` (pkg id) | `org.onehill.anthill.appliance` |

## Migration and orphan handling

A macOS bundle identifier is the app's identity. Changing it means the OS treats a build with the new id as a **different application** from any install of the old id. Consequences and the plan:

- **Desktop app.** An installed `ai.colonies.anthill` build will not be updated in place by an `org.onehill.anthill` build; the two can coexist as two apps. This is why the change lands **before** the public flip, so the first public signed build already carries `org.onehill.anthill` and the mass of installs is never on the old id. Anyone on a pre-launch internal build reinstalls once.
- **Appliance / LaunchAgent.** The LaunchAgent plist filename changes (`ai.colonies.anthill.plist` to `org.onehill.anthill.plist`). A machine that already loaded the old agent needs the old plist unloaded and removed on upgrade. There are **no production appliance installs** yet (pre-launch), so no automated migration is shipped here. If appliances ship before every operator has reinstalled, add a one-time uninstall of the old label to the appliance installer as a follow-up.
- **Signing / notarization / updater.** Unaffected. The Developer ID certificate signs any identifier, and the Tauri updater endpoint (`releases/latest/download/latest.json`) does not change.

Automated in-app migration is intentionally **not** shipped: with no production installs, the reinstall-once path is simpler and lower-risk than migration code that would only ever run against pre-launch builds. Doing the rename before the flip is the migration.

## Acceptance criteria

- No `colonies` identifier remains in `tauri.conf.json`, `Anthill.spec`, `appliance.py`, the appliance pkg scripts, or `test_appliance.py`.
- `tauri.conf.json` remains valid JSON.
- `tests/test_appliance.py` asserts the `org.onehill.anthill` LaunchAgent label and passes.
- The change merges before the public flip (tracked as a pre-flip gate on the Anthill launch issue).
