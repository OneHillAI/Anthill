"""Guards on the real-device kit (scripts/windows_device_check.ps1, docs/releasing.md).

PowerShell cannot run in this test, so these pin what must stay true of the file: it keeps the names the CI check uses, it covers the eight steps of the checklist, it stays plain ASCII (Windows PowerShell 5.1 reads a file without a byte-order mark as ANSI), and
CI really runs it. On a Windows runner CI also runs its self-test (the file parses and the machine can be read).
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "windows_device_check.ps1"


@pytest.fixture(scope="module")
def ps():
    return SCRIPT.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The script without comments and the help block, so a word in a comment cannot satisfy or break a check."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)#.*$", "", ln) for ln in text.splitlines())


def _installer_check():
    spec = importlib.util.spec_from_file_location(
        "windows_installer_check", ROOT / "scripts" / "windows_installer_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_file_is_plain_ascii_and_declares_its_switches(ps):
    ps.encode("ascii")  # raises if a character would be misread by Windows PowerShell 5.1
    param_block = re.search(r"param\((.*?)\n\)", ps, flags=re.S).group(1)
    for switch in ("Beta", "AfterUninstall", "NonInteractive", "SelfTest"):
        assert f"[switch]${switch}" in param_block
    assert "[string]$OutFile" in param_block


def test_it_uses_the_same_names_as_the_ci_check_and_the_app(ps):
    check = _installer_check()
    assert f'$SidecarExe = "{check.SIDECAR_EXE}"' in ps
    assert f'$UninstallerExe = "{check.UNINSTALLER_EXE}"' in ps
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text()
    shell = re.search(r'^name = "([^"]+)"', cargo, flags=re.M).group(1)
    assert f'$ShellExe = "{shell}.exe"' in ps  # the shell is named after the Rust package
    conf = (ROOT / "src-tauri" / "tauri.conf.json").read_text()
    product = re.search(r'"productName":\s*"([^"]+)"', conf).group(1)
    assert f'{{ "{product} Beta" }} else {{ "{product}" }}' in ps
    beta = (ROOT / ".github" / "workflows" / "desktop-beta-windows.yml").read_text()
    assert (
        f"ANTHILL_CHECK_DATA_FOLDER: {product} Beta" in beta
    )  # the data folder CI checks for the beta
    assert (
        "secrets.env" in ps
        and "secrets.env" in (ROOT / "scripts" / "windows_installer_check.py").read_text()
    )


def test_it_covers_the_eight_steps_of_the_checklist_in_the_release_guide(ps):
    guide = (ROOT / "docs" / "releasing.md").read_text()
    steps = re.findall(
        r"^\s+(\d)\. ",
        guide.split("Before the first public Windows build")[1].split("## The rule")[0],
        flags=re.M,
    )
    assert steps == [str(n) for n in range(1, 9)]
    headings = re.findall(r'Say "## (\d)\. ', ps)
    assert sorted(set(headings)) == [
        str(n) for n in range(1, 9)
    ]  # step 8 appears twice: after the uninstall and as the hand-over
    assert "windows_device_check.ps1" in guide and "-AfterUninstall" in guide and "-Beta" in guide


def test_it_exits_non_zero_when_something_failed_and_never_asks_in_ci_mode(ps):
    assert ps.count("exit 1") >= 2 and "if ($failed -gt 0) { exit 1 }" in ps
    assert "NOT ASKED" in ps and "if ($NonInteractive)" in ps
    assert "Set-StrictMode" in ps and '$ErrorActionPreference = "Stop"' in ps
    assert "IsInRole" in ps  # it records whether it was run as administrator
    # an unexpected error still writes what was recorded so far, and a process list is never counted on a bare function result
    assert re.search(r"trap \{[^}]*Write-Result[^}]*exit 1", ps, flags=re.S)
    assert not re.search(r"(?<!@)\(Get-ProcessesNamed [^)]*\)\.Count", _code(ps))


def test_ci_runs_it_on_the_runner_that_installed_and_removed_the_app():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "windows-ci.yml").read_text())
    steps = workflow["jobs"]["installer"]["steps"]
    names = [s.get("name", "") for s in steps]
    kit = next(s for s in steps if "real-device kit" in s.get("name", ""))
    assert (
        names.index(kit["name"])
        == next(i for i, n in enumerate(names) if n.startswith("Install it, run it")) + 1
    )
    assert "-SelfTest" in kit["run"] and "-AfterUninstall -NonInteractive" in kit["run"]
    assert "-ExecutionPolicy Bypass" in kit["run"]
    areas = importlib.util.spec_from_file_location("a", ROOT / "scripts" / "windows_ci_areas.py")
    module = importlib.util.module_from_spec(areas)
    areas.loader.exec_module(module)
    assert (
        "scripts/windows_device_check.ps1" in module.SHELL
    )  # a change to the kit runs the installer job


def test_nvidia_smi_gets_its_two_arguments_quoted(ps):
    """An unquoted comma list is an array in PowerShell and reaches the program as separate arguments."""
    text = ps
    assert '"--query-gpu=name,driver_version,memory.total" "--format=csv,noheader"' in text
    assert "--query-gpu=name,driver_version,memory.total --format=csv,noheader" not in text


def test_paths_in_the_result_do_not_carry_the_account_name(ps):
    """The result is pasted on the public release page: user-profile paths are shown as %LOCALAPPDATA%."""
    text = ps
    assert "function Hide-Home" in text and '"%LOCALAPPDATA%"' in text
    assert (
        "Hide-Home $left" in text and "(Hide-Home $DataDir)" in text and "Hide-Home $appDir" in text
    )
    assert "under $LocalAppData" not in text


def test_an_app_that_was_already_running_is_not_recorded_as_a_measured_pass(ps):
    text = ps
    assert '$windowStatus = if ($wasRunning) { "NOT MEASURED" }' in text
