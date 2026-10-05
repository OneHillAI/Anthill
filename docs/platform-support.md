# Platform support

Anthill is one codebase with one version number and one release for every platform (spec:
`docs/specs/windows-support.md`). A feature is the same on every platform unless this table says otherwise. Anything
that depends on the operating system lives in `anthill/platform_layer.py`, so the rest of the code asks "can this
platform do X?" and does not check the operating system itself.

## Where each platform stands

| Platform | Status |
| --- | --- |
| macOS, Apple Silicon | Beta. Signed, notarised, updates itself. |
| Windows 10 and 11, x86_64 | Alpha, in progress. The backend builds, starts and answers a chat with a real local model in CI, and the installer is built and install-tested in CI; there is no signing or update feed for it yet. |
| Windows on ARM, Linux, Intel Mac | Not supported. |

## Capability table

"Same" means the feature behaves the same on both platforms. This table is completed by an audit of every feature before
the first public Windows build; the rows below are what is known today.

| Capability | macOS | Windows | Notes |
| --- | --- | --- | --- |
| Chat, wiki, knowledge, connectors, web UI | Same | Same, not yet checked on a real machine | Shared Python code and web UI. |
| Hardware reading for the model picker | Memory, Apple chip or NVIDIA card | Memory and NVIDIA card | Windows reads memory from the system; a machine with no NVIDIA card is treated as CPU only. |
| Per-user data folder | `~/Library/Application Support/Anthill` | `%LOCALAPPDATA%\Anthill` | Chosen by `platformdirs`. |
| Backend stops when the app quits or crashes | Yes | Yes, checked by the Windows build job and, with the real app, by the installer job | Windows has no re-parenting, so the backend asks whether its launcher and the app are still alive. |
| One scheduler per database | Yes | Yes | An operating-system file lock that is dropped when the process dies. |
| PDF reading memory limit | Yes | Yes | A job object on Windows. |
| Local model (Ollama) install and start | Yes | Yes, on the CPU and NVIDIA cards. Checked on a CPU-only runner | A 1.46 GB first-run download on Windows (143 MB on macOS) because it carries the NVIDIA runtime. AMD and Intel cards run on the CPU for now. The NVIDIA path needs the real-device check. |
| On-device fine-tuning | Yes (Apple MLX) | No | Hidden in the interface where the platform cannot do it. |
| Installer | `.dmg` | NSIS installer for the current user, no administrator rights. Built, installed, run and uninstalled in CI; unsigned | Phase C. |
| Signing and notarisation | Yes | Not yet | Needs a decision on how to sign. |
| Automatic updates | Yes | Not yet | Same feed, a `windows-x86_64` key, in Phase C. |
| Always-on organisation backend appliance | Yes | No | Out of scope. |

## Rules for changes

- A change to the platform layer, the desktop shell or the sidecar build must pass the Windows build job.
- A feature may ship on one platform first only behind a capability flag, with the reason recorded here.
- Before a platform's first public release, a person installs the build on a real machine and ticks the checklist in
  `docs/releasing.md`.
