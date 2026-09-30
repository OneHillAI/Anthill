"""Cross-platform portability invariants for the per-user data directory.

`desktop.py::data_dir()` resolves the data home with platformdirs so the app runs on macOS,
Windows, and Linux. The single most important invariant: the macOS path must stay byte-identical
to the original hardcoded ``~/Library/Application Support/Anthill`` - a regression there would
strand every existing install's database and at-rest encryption keys. The macOS and Linux paths
are pure string construction (assertable on any runner); the Windows path needs the Windows API
to resolve %LOCALAPPDATA%, so that guard is Windows-gated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from platformdirs.macos import MacOS
from platformdirs.unix import Unix

from anthill.desktop import data_dir

_APP = "Anthill"


def test_macos_path_is_unchanged() -> None:
    """macOS must resolve to exactly ~/Library/Application Support/Anthill (no data migration)."""
    legacy = Path.home() / "Library" / "Application Support" / _APP
    assert Path(MacOS(_APP, appauthor=False).user_data_dir) == legacy


def test_linux_path_ends_in_app_name() -> None:
    """Linux resolves under the XDG data home, ending in the app name."""
    assert Unix(_APP, appauthor=False).user_data_dir.endswith(_APP)


def test_data_dir_creates_app_named_dir_on_this_os() -> None:
    """The real function returns an existing directory named after the app on the running OS."""
    d = data_dir()
    assert d.is_dir()
    assert d.name == _APP


@pytest.mark.skipif(sys.platform != "win32", reason="resolving %LOCALAPPDATA% requires Windows")
def test_windows_data_dir_not_doubled() -> None:
    """appauthor=False keeps Windows at %LOCALAPPDATA%/Anthill, not .../Anthill/Anthill."""
    d = data_dir()
    assert d.name == _APP
    assert d.parent.name != _APP
