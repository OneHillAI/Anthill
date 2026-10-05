# Spec: Anthill for Windows

Status: Phases A (build and start) and B (local model) merged. Phase C in progress, in three pull requests: C1 the installer and the shell, tested in CI and unsigned; C2 the release flow; C3 signing and the self-update proof. Windows is an alpha; macOS is a beta.
Lane: `pillar:platform`
Relates to: `src-tauri/tauri.conf.json`, `src-tauri/src/lib.rs`, `scripts/build-sidecar.sh`, `anthill/desktop.py`,
`anthill/inference/ollama.py`, `.github/workflows/desktop-release.yml`, `.github/workflows/desktop-beta.yml`,
`docs/releasing.md`, `docs/AUTOUPDATE.md`

## 1. Problem

Anthill ships only for macOS on Apple Silicon. Most organisations also run Windows machines, and those people cannot
install it. The Tauri shell, the web UI and most of the Python backend are portable. What stands in the way is the
backend build, the local model install, process clean-up, code signing and CI.

## 2. What exists and what is missing

Already in place: the Tauri 2 shell starts the PyInstaller backend (`anthill-server`) as a sidecar and points a window at
it on `127.0.0.1`; the bundle targets already list the Windows installer (`nsis`); `anthill/desktop.py` already picks
`%LOCALAPPDATA%` for data on Windows; the updater feed is one `latest.json` with a key per platform.

Missing:

- The backend has never been built or run on Windows. Native libraries (lancedb, pyarrow, onnxruntime, cryptography)
  need checking, and the frozen-build checks added for macOS (the Arrow allocator crash) need Windows equivalents.
- The local model installer (Ollama) only exists for macOS. Windows needs its own locate, install and start logic.
- The backend's self-exit when the shell dies is POSIX-only. Windows needs an equivalent so no backend is orphaned.
- A text search flags a few modules for POSIX-only calls (`anthill/web/scheduler.py`, `anthill/web/provision_run.py`,
  `anthill/training/remote.py`, `anthill/multimodal/pdf_worker.py`). Each must be read; the search is a pointer, not a verdict.
- No Windows code signing, no Windows runner in CI, no Windows tests, and the release and beta workflows are macOS only.

## 3. Requirements

R1. THE project SHALL keep one codebase, one version number and one release for all platforms. A Windows fix ships as the
next version for everyone; no separate release train.

R2. The first target SHALL be Windows 10 and 11 on x86_64. Windows on ARM, Linux and Intel Macs are out of scope here.

R3. Features SHALL be the same on every platform by default. Where a platform cannot do something (for example on-device
fine-tuning, which uses Apple's MLX), the difference SHALL be recorded in the capability table (section 5) and detected in code, so
the interface hides what the platform cannot do instead of failing.

R4. THE Windows build SHALL be an NSIS installer that installs for the current user without administrator rights and
installs the WebView2 runtime when it is missing.

R5. THE local model SHALL be Ollama on Windows, located or installed the way macOS does it today, and an Anthill that
finds no suitable local model SHALL offer the cloud or inference-provider path as it does now.

R6. Quitting or crashing the shell SHALL NOT leave the backend running. On Windows this needs a job object or an
equivalent, tested by killing the shell.

R7. Installers and update bundles SHALL be signed. Until signing exists, a build may be published only as a clearly
marked "Windows alpha" pre-release, never as the latest release. Windows is an alpha as a whole until the maintainer says
otherwise.

R8. Windows SHALL update itself through the same feed (platform key `windows-x86_64`) and the same updater signing key.

R9. CI SHALL build the Windows backend and run a smoke test on every PR that touches the shell, the sidecar build or the
platform layer. The job is triggered by the PR itself, so no one has to start it by hand. The smoke test starts the
backend, waits for it to serve, and sends one chat turn to a fake model, as the macOS frozen smoke test does.

R10. One tag SHALL build every platform. A release SHALL become the latest release only when every platform's files are
present, and the release check SHALL gain a Windows section. Cut Beta SHALL build Windows too.

R11. THE docs SHALL carry the capability table, release notes SHALL name platform-specific changes, and the issue template
SHALL ask for the operating system.

## 4. Plan

Phase A, build and start. A Windows runner builds the backend with PyInstaller (unsigned) and passes the smoke test. The
POSIX-only spots are fixed or guarded. Exit: the PR-triggered job is green.

Phase B, local model. Ollama on Windows: locate or install, start, stop, CPU and GPU. Exit: the smoke test runs a real small
model on a Windows runner (CPU).

Phase C, ship. The NSIS installer, signing, the updater, the beta channel and the release flow, with the clean-shutdown job
object. Exit: a CI-built installer installs on a real Windows machine, chats, updates itself to the next version, and
leaves no process behind on quit.

Phase D, parity. The full test suite runs on Windows as a required check, and the capability table is complete.

Each phase is its own PR or small series, each with tests, and none changes how macOS builds.

## 5. Maintaining more than one platform

- **One version, one release (R1).** The Python backend and the web UI are shared. Only a thin platform layer differs.
- **A single platform layer.** A new module (`anthill/platform_layer.py`) holds everything that depends on the operating system:
  data paths, process lifecycle, which model installer to use, and capability flags. The rest of the code asks "can this
  platform do X?" and never checks the operating system itself. It is not named `platform.py`, because a sibling of
  that name would hide the standard library module when PyInstaller analyses `anthill/desktop.py`.
- **A capability table**, `docs/platform-support.md`, created in Phase A and completed by an audit of every feature. Default
  is "same everywhere". A feature may ship on one platform first only behind a capability flag, with the reason recorded.
- **Tests per platform.** CI runs the smoke test and, from Phase D, the full suite on Windows and macOS. A change to the
  platform layer or the shell needs the Windows job green.
- **A real-device check.** Before a platform's first public release, and for any release that touches the platform layer, a
  person installs the build on a real machine and ticks a short checklist in `docs/releasing.md`.
- **Release gating.** All platforms' files must be on the release before it is made latest; the pre-release default already
  gives us that gate.
- **Triage.** Issues and PRs carry an `os:windows` label next to the lane label, and the morning digest shows each
  platform's build status.

## 6. Decisions

1. **Signing (open).** The preferred route is SignPath Foundation, which is free for open source. Its terms
   exclude a project with "commercial dual-licensing for all components", and `NOTICE` and the README offer a
   commercial licence next to the AGPL, so SignPath has to be asked whether that disqualifies Anthill. The fallback is
   Azure Artifact Signing (about $9.99 a month, organisations in the USA, Canada, the EU and the UK), which the
   maintainer would rather avoid. A certificate from a certificate authority is the third choice. Until signing exists,
   Windows builds are alpha pre-releases for testers.
2. **A real Windows machine (decided, 2026-10-05).** One is available for the real-device check.
3. **Audience of the first Windows build (decided, 2026-10-05).** Windows is an alpha, for testers only.

## 7. Out of scope

Linux builds (the targets exist in the config but are unsupported), Windows on ARM, a Windows service for the always-on
organisation backend (the macOS appliance), Microsoft sign-in, and the Chrome and iOS clients (separate specs).

## 8. Proof on a real change

Not "set up" until a CI-built installer has been installed on a real Windows machine, started a chat, updated itself to the
next version, and quit without leaving a backend process behind. Phase A alone proves only that the backend builds and starts.
