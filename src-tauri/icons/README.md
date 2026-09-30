# App icons (generated, not committed here)

`tauri.conf.json` references `icons/32x32.png`, `icons/128x128.png`, `icons/128x128@2x.png`,
`icons/icon.icns`, and `icons/icon.ico`. Generate the full set from the existing brand mark in one
command (run from `src-tauri/`):

```
tauri icon ../../assets/anthill-mark.png
```

That writes every required size/format into this directory. The repo already has a Pillow-based
generator (`scripts/gen_icons.py`) that emits the tile/PWA/favicon/.icns set for the web app; the
Tauri `tauri icon` command is the equivalent for the desktop bundle and is the simpler path here.

The generated binaries are gitignored; only this README is tracked.
