"""Guard the Tauri v2 desktop capability manifest (docs/specs/desktop-shell-allow-open.md).

The desktop shell loads the backend over http://127.0.0.1:<port>/<path>, which Tauri v2 treats as a
remote origin. Every IPC command invoked from those pages - including tauri-plugin-shell's
`plugin:shell|open` click handler for `<a target="_blank">` links - must be explicitly listed in
`src-tauri/capabilities/default.json` and scoped only to the loopback origins.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPABILITY_PATH = ROOT / "src-tauri" / "capabilities" / "default.json"


def test_default_capability_grants_shell_open_only_to_loopback_origins() -> None:
    capability = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))

    assert capability["windows"] == ["main"]
    assert capability["remote"] == {
        "urls": ["http://127.0.0.1:*/**", "http://localhost:*/**"],
    }
    permissions = capability["permissions"]
    assert "shell:allow-open" in permissions
    assert "allow-open-profile" in permissions
