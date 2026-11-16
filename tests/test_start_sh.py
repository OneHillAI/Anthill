"""start.sh on Linux and macOS (#145, #147). The script used to look for Ollama only at ~/bin/ollama,
download the macOS app zip on any OS and blame the network when that failed, ignore a server already
running on :11434, open the browser with the macOS-only `open`, and rely on `lsof` to stop an old
dashboard. These tests run the real script in a temp project with a PATH holding only stubs (a fake
`uname`, `curl`, `ollama`, `open`, `xdg-open`, `lsof`, ...), so nothing is downloaded, installed or
started. The only process ever killed is a `sleep` the test itself spawned. Model-free."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_HINT = "https://ollama.com/download"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash script")

# A real binary here would be picked up as a fallback, so the "no Ollama" cases cannot run.
needs_no_system_ollama = pytest.mark.skipif(
    os.access("/usr/local/bin/ollama", os.X_OK),
    reason="/usr/local/bin/ollama exists and start.sh would use it",
)

# Real tools start.sh needs; everything else is a stub or absent.
REAL_TOOLS = (
    "bash",
    "dirname",
    "grep",
    "cut",
    "mktemp",
    "seq",
    "mkdir",
    "chmod",
    "rm",
    "nohup",
    "cat",
)

RECORD = 'echo "${0##*/} $*" >> "$CALLS"\n'

CURL_BODY = """case "$*" in
  *11434/api/tags*) [ -f "$SANDBOX/tags" ] && cat "$SANDBOX/tags"; exit 0 ;;
  *11434*) [ -f "$SANDBOX/server_up" ]; exit $? ;;
  *127.0.0.1:8000*) exit 0 ;;
  *) exit 22 ;;
esac"""

OLLAMA_BODY = """case "$1" in
  list) echo "qwen2.5:3b" ;;
esac"""


class Sandbox:
    def __init__(self, tmp_path: Path, uname: str):
        self.base = tmp_path / uname.lower()
        self.root = self.base / "project"
        self.bin = self.base / "bin"
        self.home = self.base / "home"
        self.calls = self.base / "calls"
        for d in (self.root / ".venv" / "bin", self.bin, self.home):
            d.mkdir(parents=True)
        self.calls.touch()
        shutil.copy(ROOT / "start.sh", self.root / "start.sh")
        (self.root / ".env").write_text("ANTHILL_JWT_SECRET=x\nANTHILL_ENCRYPTION_KEY=y\n")
        fake_dashboard = self.root / ".venv" / "bin" / "anthill"
        fake_dashboard.write_text("#!/bin/bash\nexit 0\n")
        fake_dashboard.chmod(0o755)
        for tool in REAL_TOOLS:
            (self.bin / tool).symlink_to(shutil.which(tool) or pytest.skip(f"no {tool}"))
        self.stub("uname", f'echo "{uname}"', record=False)
        self.stub("sleep", "")
        self.stub("curl", CURL_BODY.replace("$SANDBOX", str(self.base)))

    def stub(self, name: str, body: str = "", *, record: bool = True) -> Path:
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + (RECORD if record else "") + body + "\n")
        path.chmod(0o755)
        return path

    def calls_made(self) -> list[str]:
        return self.calls.read_text().splitlines()

    def popen(self, *args: str) -> subprocess.Popen[str]:
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "CALLS": str(self.calls),
            "ANTHILL_LOG_DIR": str(self.base),
        }
        # A runner started in the background (`&`, nohup, some CI wrappers) has SIGINT ignored, and a shell
        # cannot trap a signal that was ignored when it started, so put the default back before bash runs.
        reset_sigint = "import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execv(sys.argv[1], sys.argv[1:])"
        return subprocess.Popen(
            [sys.executable, "-c", reset_sigint, str(self.bin / "bash"), "start.sh", *args],
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        proc = self.popen(*args)
        out, _ = proc.communicate(timeout=30)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out)


@pytest.fixture
def linux(tmp_path):
    return Sandbox(tmp_path, "Linux")


@pytest.fixture
def darwin(tmp_path):
    return Sandbox(tmp_path, "Darwin")


def mark_server_up(box: Sandbox, tags: str | None = None) -> None:
    (box.base / "server_up").touch()
    if tags is not None:
        (box.base / "tags").write_text(tags)


# ── Ollama (#145) ───────────────────────────────────────────────────────────────


@needs_no_system_ollama
def test_linux_without_ollama_prints_the_install_command_and_does_not_blame_the_network(linux):
    r = linux.run("--setup-only")
    assert r.returncode != 0
    assert INSTALL_HINT in r.stdout
    assert "| sh" not in r.stdout  # never suggests piping a download into a shell
    assert "no internet" not in r.stdout
    assert not any("Ollama-darwin" in c for c in linux.calls_made())


@needs_no_system_ollama
def test_darwin_without_ollama_still_tries_the_app_download(darwin):
    r = darwin.run("--setup-only")
    assert r.returncode != 0
    assert "no internet" in r.stdout
    assert any("Ollama-darwin.zip" in c for c in darwin.calls_made())


def test_ollama_on_path_is_used_on_linux(linux):
    linux.stub("ollama", OLLAMA_BODY)
    r = linux.run("--setup-only")
    assert r.returncode == 0, r.stdout
    calls = linux.calls_made()
    assert "ollama list" in calls
    assert not any("pull" in c or "Ollama-darwin" in c for c in calls)


def test_ollama_in_home_bin_is_still_found(linux):
    (linux.home / "bin").mkdir()
    linux.stub("ollama", OLLAMA_BODY).rename(linux.home / "bin" / "ollama")
    r = linux.run("--setup-only")
    assert r.returncode == 0, r.stdout
    assert "ollama list" in linux.calls_made()


def test_missing_model_is_pulled_through_the_ollama_found(linux):
    linux.stub("ollama", 'case "$1" in list) echo "bge-m3" ;; esac')
    r = linux.run("--setup-only")
    assert r.returncode == 0, r.stdout
    assert "ollama pull qwen2.5:3b" in linux.calls_made()


@needs_no_system_ollama
def test_a_running_server_is_enough_without_the_binary(linux):
    mark_server_up(linux, tags='{"models":[{"name":"qwen2.5:3b"}]}')
    r = linux.run("--setup-only")
    assert r.returncode == 0, r.stdout
    assert "qwen2.5:3b ready" in r.stdout
    assert "install.sh" not in r.stdout
    assert not any(c.startswith("ollama") for c in linux.calls_made())


@needs_no_system_ollama
def test_a_running_server_without_the_model_says_how_to_pull_it(linux):
    mark_server_up(linux, tags='{"models":[]}')
    r = linux.run("--setup-only")
    assert r.returncode == 0, r.stdout
    assert "ollama pull qwen2.5:3b" in r.stdout
    assert "qwen2.5:3b ready" not in r.stdout


def test_serve_is_started_only_when_no_server_answers(linux):
    linux.stub("ollama", OLLAMA_BODY)
    linux.run("--setup-only")
    assert "ollama serve" in linux.calls_made()
    linux.calls.write_text("")
    mark_server_up(linux)
    linux.run("--setup-only")
    assert "ollama serve" not in linux.calls_made()


# ── platform-neutral launch (#147) ──────────────────────────────────────────────


def launchable(box: Sandbox) -> None:
    box.stub("ollama", OLLAMA_BODY)
    mark_server_up(box)


def test_linux_opens_the_browser_with_xdg_open(linux):
    launchable(linux)
    linux.stub("xdg-open")
    linux.stub("open")
    r = linux.run()
    assert r.returncode == 0, r.stdout
    calls = linux.calls_made()
    assert "xdg-open http://localhost:8000" in calls
    assert not any(c.startswith("open ") for c in calls)
    assert "http://localhost:8000" in r.stdout
    assert "Chrome" not in r.stdout
    assert "anthill.command" not in r.stdout


def test_linux_without_xdg_open_still_prints_the_url(linux):
    launchable(linux)
    r = linux.run()
    assert r.returncode == 0, r.stdout
    assert "http://localhost:8000" in r.stdout


def test_darwin_opens_the_browser_with_open(darwin):
    launchable(darwin)
    darwin.stub("open")
    darwin.stub("xdg-open")
    r = darwin.run()
    assert r.returncode == 0, r.stdout
    calls = darwin.calls_made()
    assert "open http://localhost:8000" in calls
    assert not any(c.startswith("xdg-open") for c in calls)


def test_no_wait_detaches_without_opening_a_browser_or_asking_to_hold_the_terminal(linux):
    launchable(linux)
    linux.stub("xdg-open")
    r = linux.run("--no-wait")
    assert r.returncode == 0, r.stdout
    assert not any(c.startswith("xdg-open") for c in linux.calls_made())
    assert "Ctrl-C" not in r.stdout


def test_the_terminal_hint_is_printed_when_the_script_holds_the_terminal(linux):
    launchable(linux)
    r = linux.run()
    assert "Ctrl-C stops Anthill" in r.stdout
    assert "--no-wait" in r.stdout


def test_setup_only_hint_names_the_mac_launcher_only_on_darwin(linux, darwin):
    for box in (linux, darwin):
        launchable(box)
    assert "anthill.command" not in linux.run("--setup-only").stdout
    assert "bash start.sh" in linux.run("--setup-only").stdout
    assert "anthill.command" in darwin.run("--setup-only").stdout


def test_ctrl_c_stops_the_dashboard_it_started(linux):
    launchable(linux)
    real_sleep = shutil.which("sleep") or pytest.skip("no sleep")
    dashboard = linux.root / ".venv" / "bin" / "anthill"
    dashboard.write_text(
        f'#!/bin/bash\necho $$ > "{linux.base}/dashboard.pid"\nexec {real_sleep} 60\n'
    )
    linux.stub("xdg-open", f': > "{linux.base}/opened"')
    script = linux.popen()
    try:
        for _ in range(100):
            if (linux.base / "opened").exists():
                break
            time.sleep(0.1)
        assert (linux.base / "opened").exists(), "start.sh never reached its wait"
        pid = int((linux.base / "dashboard.pid").read_text())
        script.send_signal(signal.SIGINT)
        script.wait(timeout=10)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            pytest.fail("the dashboard outlived Ctrl-C")
    finally:
        script.kill()
        script.wait()
        script.stdout.close()


# ── stopping an old dashboard without lsof (#147) ───────────────────────────────

LISTENER_TOOLS = {
    "lsof": 'cat "$SANDBOX/pid"',
    "fuser": 'echo "8000/tcp:" >&2; echo " $(cat "$SANDBOX/pid")"',
    "ss": (
        'echo "LISTEN 0 128 127.0.0.1:8000 0.0.0.0:* '
        'users:((\\"python\\",pid=$(cat "$SANDBOX/pid"),fd=5))"'
    ),
}


@pytest.fixture
def old_dashboard(linux):
    proc = subprocess.Popen(["sleep", "60"])
    (linux.base / "pid").write_text(str(proc.pid))
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


@pytest.mark.parametrize("tool", sorted(LISTENER_TOOLS))
def test_an_old_dashboard_is_stopped_with_whichever_tool_exists(linux, old_dashboard, tool):
    launchable(linux)
    linux.stub(tool, LISTENER_TOOLS[tool].replace("$SANDBOX", str(linux.base)))
    r = linux.run("--no-wait")
    assert r.returncode == 0, r.stdout
    assert old_dashboard.wait(timeout=10) != 0
    assert "Can't check" not in r.stdout


def test_without_any_tool_to_find_it_the_script_warns_and_carries_on(linux, old_dashboard):
    launchable(linux)
    r = linux.run("--no-wait")
    assert r.returncode == 0, r.stdout
    assert "Can't check for an old dashboard on :8000" in r.stdout
    assert old_dashboard.poll() is None
