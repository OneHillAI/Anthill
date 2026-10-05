# Desktop auto-update (Tauri)

Goal: cut one release and every installed `Anthill.app` self-installs it, with no manual reinstall.
This is the Tauri updater path. It complements `RELEASE_SIGNING.md` (Apple Developer ID, which makes
the download open without a Gatekeeper warning); the two signings are independent.

## How it works

1. The desktop app is the Tauri shell in `src-tauri/`. It bundles the backend as a sidecar binary
   (`binaries/anthill-server-<target-triple>`, a one-file PyInstaller build of `anthill/desktop.py`)
   and points a native window at the backend it spawns. See `src-tauri/README.md`.
2. On launch, `src-tauri/src/lib.rs` calls the updater: it fetches the manifest at the endpoint in
   `src-tauri/tauri.conf.json` (`plugins.updater.endpoints` ->
   `https://github.com/OneHillAI/Anthill/releases/latest/download/latest.json`). If a newer release
   exists, it downloads the signed bundle, verifies the signature against the bundled **public** key,
   installs it, and restarts into the new version. If the server is unreachable the check is skipped
   and the app runs offline on its current version.
3. The release CI (`.github/workflows/desktop-release.yml`) builds the sidecar, builds + signs the
   Tauri app, and uploads the update artifacts plus `latest.json` to the GitHub Release for the tag.

So updates flow through releases: you still cut `vX.Y.Z` (see [`releasing.md`](releasing.md) for the beta lane that
runs first); the difference is each installed app applies
it automatically instead of someone re-downloading the dmg.

## One-time owner setup (required before auto-update works)

The update artifacts are signed with a **minisign** keypair. The private key is a repository secret the
owner holds; it must never be committed or pasted into a session. Without it the desktop-release job
no-ops, so nothing here breaks the standard dmg release.

1. Install the Tauri CLI and generate the keypair (choose a password when prompted):
   ```bash
   cargo install tauri-cli --version '^2' --locked
   cargo tauri signer generate -w anthill-updater.key
   ```
   This writes `anthill-updater.key` (private) and prints the **public** key.
2. Paste the printed public key into `src-tauri/tauri.conf.json` -> `plugins.updater.pubkey`
   (replacing the key that is there now).
3. Add two repository secrets (Settings -> Secrets and variables -> Actions):
   - `TAURI_SIGNING_PRIVATE_KEY` = the full contents of `anthill-updater.key`
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` = the password you chose
4. Delete the local `anthill-updater.key` once the secret is set. The public key in the app and the
   private key in CI are a matched pair; rotating the key means re-issuing both and shipping a build
   with the new public key before old installs can verify new updates.

The key has been rotated once, in v0.12.12 (public key id `3DE93D4EAF18A15E`). An install built before
v0.12.12 carries the old key, cannot verify updates, and needs one manual download of the current
release. Every install from v0.12.12 on updates itself.

## Why the releases must be public

The updater fetches release assets from `github.com/OneHillAI/Anthill`. A shipped app cannot safely
carry a token, so those URLs must be reachable without authentication. The repo and its releases have
been public since the launch, so every install can reach `latest.json` and the signed artifacts. If
the repo ever goes private again, auto-update stops for outside installs. In that case host
`latest.json` and the signed artifacts on a public channel and point `plugins.updater.endpoints`
there instead.

## Windows

The Windows alpha updates through the same feed and the same updater key: the `windows-x86_64` entry in `latest.json`
points at the NSIS installer (`*_x64-setup.exe`) and its `.sig`. On launch the shell checks the feed before it starts
the backend, so no backend is running while the installer replaces the program files.

The `Windows update` check proves this on every pull request that touches the shell: it builds two installers with a
throwaway key (the real key and the real feed are never used), serves a local feed that offers the newer one, installs
the older one, and requires the app to ask the feed, download the new installer, be replaced, start and answer, be the
only copy running, and leave nothing behind when closed (`scripts/windows_update_check.py`). It passes: the Windows
updater quits the old app, the installer replaces the files and starts the new version by itself.

Signing order matters once Windows code signing exists. The updater signature (`.sig`) covers the exact bytes of the
installer, so the installer must be signed by Windows code signing first and the updater signature made after it;
changing the installer afterwards makes every update fail verification.

## Releasing

Cut a release as usual (`scripts/check-release.sh` still gates the version + changelog):
```bash
# bump pyproject.toml version, assemble the changelog from PR fragments, then tag:
python scripts/build_changelog.py X.Y.Z <date>   # turns changelog.d/ fragments into ## [X.Y.Z]
git tag vX.Y.Z && git push origin vX.Y.Z
```
Both workflows fire on the tag: `release.yml` builds the standalone dmg + appliance pkg, and
`desktop-release.yml` builds the signed Tauri app + `latest.json`. The desktop job keeps the Tauri
app version in lockstep with the tag so the updater compares versions correctly.

## Status

The Rust shell and this pipeline compile and run in CI, not on a machine without the Rust/Node/Tauri
toolchain. The Python sidecar (`scripts/build-sidecar.sh` + `Anthill-sidecar.spec`) is verified: it
builds a one-file binary that serves headless and prints `PORT=<n>`. The first signed desktop release
is the end-to-end validation of the Rust build + signing.
