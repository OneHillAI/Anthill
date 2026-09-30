# Sidecar binaries (built in CI, gitignored)

Tauri's `externalBin` looks for the backend binary here, named with the Rust **target triple**
suffix so one bundle config works on every OS:

```
binaries/anthill-server-aarch64-apple-darwin
binaries/anthill-server-x86_64-apple-darwin
binaries/anthill-server-x86_64-pc-windows-msvc.exe
binaries/anthill-server-x86_64-unknown-linux-gnu
```

Each is the backend (`anthill/desktop.py`) built as a ONE-FILE binary by
`scripts/build-sidecar.sh` (PyInstaller via `Anthill-sidecar.spec`), then copied here and renamed to
the triple. Build it with:

```
bash scripts/build-sidecar.sh          # host triple from rustc, or set TAURI_TARGET_TRIPLE
```

`.github/workflows/desktop-release.yml` runs this per OS before `tauri build`. The binaries are
intentionally **not** committed (gitignored) - only this README and a `.gitkeep` are tracked.

Find your host triple with `rustc -Vv | grep host`.

The sidecar must be launched with `ANTHILL_NO_BROWSER=1`; in that mode `desktop.py` serves headless
on a free port and prints `PORT=<n>` on stdout, which the shell (`src/lib.rs`) reads.
