#!/usr/bin/env python3
"""Decide which parts of the Windows CI a change needs, from the list of changed files.

    git diff --name-only BASE...HEAD | python scripts/windows_ci_areas.py    prints backend=true|false, shell=true|false
    python scripts/windows_ci_areas.py --all                                   prints both true (when the change is unknown)

The Windows CI builds the backend (the "sidecar") once and shares it with three jobs. The `backend` job (platform tests and
a real local model) is needed when the backend or the platform layer changed; the `installer` and `update` jobs build the
real app and are needed when the desktop shell, the sidecar build or the platform layer changed. A change to only the
Ollama code therefore does not pay for two Tauri builds, as before the three workflows were joined.

The rules live here, in plain data, so they are tested; the workflow's own `paths:` filter must list every one of them
(tests/test_windows_ci.py checks that). An entry ending in "/" means everything under that folder.
"""

from __future__ import annotations

import sys

# Folders end in "/". Everything else is one exact file.
SHARED = (
    "src-tauri/",
    "scripts/build-sidecar.sh",
    "Anthill-sidecar.spec",
    "anthill/desktop.py",
    "anthill/platform_layer.py",
    ".github/workflows/windows-ci.yml",
    "scripts/windows_ci_areas.py",
    "tests/test_windows_ci.py",
)
BACKEND = (
    *SHARED,
    "scripts/smoke_frozen_chat.py",
    "anthill/web/scheduler.py",
    "anthill/inference/ollama.py",
    "anthill/hosting/sizing.py",
    "requirements.lock",
    "pyproject.toml",
    "tests/test_platform_layer.py",
    "tests/test_ollama_windows.py",
)
SHELL = (
    *SHARED,
    "scripts/windows_installer_check.py",
    "scripts/windows_update_check.py",
    "tests/test_windows_installer_check.py",
    "tests/test_windows_update_check.py",
)


def _matches(path: str, rules: tuple[str, ...]) -> bool:
    return any(path.startswith(rule) if rule.endswith("/") else path == rule for rule in rules)


def areas(paths: list[str]) -> dict[str, bool]:
    paths = [p.strip() for p in paths if p.strip()]
    return {
        "backend": any(_matches(p, BACKEND) for p in paths),
        "shell": any(_matches(p, SHELL) for p in paths),
    }


def workflow_paths() -> list[str]:
    """The `paths:` list the workflow needs: every rule, with folders written as `folder/**`."""
    return sorted({rule + "**" if rule.endswith("/") else rule for rule in (*BACKEND, *SHELL)})


def main(argv: list[str]) -> int:
    result = {"backend": True, "shell": True} if argv == ["--all"] else areas(sys.stdin.read().splitlines())
    for name, needed in result.items():
        print(f"{name}={'true' if needed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
