"""The parsing helpers of scripts/windows_installer_check.py, run on every platform with captured Windows output."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "windows_installer_check.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("windows_installer_check", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASKLIST = (
    '"Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title"\r\n'
    '"Anthill.exe","4120","Console","1","58,212 K","Running","RUNNER\\runneradmin","0:00:02","Anthill"\r\n'
    '"Anthill.exe","4188","Console","1","12,000 K","Running","RUNNER\\runneradmin","0:00:00","N/A"\r\n'
)

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       904
  TCP    127.0.0.1:50321        0.0.0.0:0              LISTENING       5200
  TCP    127.0.0.1:50321        127.0.0.1:50400        ESTABLISHED     5200
  TCP    [::1]:50999            [::]:0                 LISTENING       5200
  TCP    192.168.1.5:8080       0.0.0.0:0              LISTENING       5200
  TCP    127.0.0.1:11434        0.0.0.0:0              LISTENING       7000
"""


def test_tasklist_rows_are_read_by_header(check):
    rows = check.parse_tasklist(TASKLIST)
    assert [r["PID"] for r in rows] == ["4120", "4188"]
    assert rows[0]["Window Title"] == "Anthill"


def test_tasklist_with_nothing_found_is_empty(check):
    assert (
        check.parse_tasklist("INFO: No tasks are running which match the specified criteria.") == []
    )
    assert check.parse_tasklist("") == []


def test_listening_ports_are_only_the_backends_loopback_ones(check):
    assert check.parse_listening_ports(NETSTAT, {5200}) == [50321, 50999]
    assert check.parse_listening_ports(NETSTAT, {7000}) == [11434]
    assert check.parse_listening_ports(NETSTAT, {1}) == []


def test_installed_app_is_the_folder_holding_the_backend(check, tmp_path):
    assert check.find_installed_app(tmp_path) is None
    (tmp_path / "Anthill").mkdir()
    (tmp_path / "Anthill" / "anthill-desktop.exe").write_bytes(
        b""
    )  # no backend yet: not the install
    assert check.find_installed_app(tmp_path) is None
    (tmp_path / "Anthill" / "anthill-server.exe").write_bytes(b"")
    assert check.find_installed_app(tmp_path) == tmp_path / "Anthill"
    (tmp_path / "Programs" / "Anthill").mkdir(parents=True)
    (tmp_path / "Programs" / "Anthill" / "anthill-server.exe").write_bytes(b"")
    assert check.find_installed_app(tmp_path) == tmp_path / "Programs" / "Anthill"  # Programs wins


def test_the_shell_is_the_executable_that_is_not_the_backend_or_the_uninstaller(check, tmp_path):
    assert check.main_exe_name(tmp_path) is None
    for name in ("anthill-server.exe", "uninstall.exe", "anthill-desktop.exe", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert check.main_exe_name(tmp_path) == "anthill-desktop.exe"
