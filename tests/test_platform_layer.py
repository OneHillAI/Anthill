"""The operating-system layer: process liveness, the process-death lock, and the sidecar's orphan watchdog.

These run on every platform (the Windows build job runs them too), so they use real child processes and
no POSIX-only commands.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from anthill.platform_layer import pid_alive, try_lock_exclusive

SLEEPER = "import time; time.sleep(60)"


def _wait_until(condition, timeout: float = 15.0, step: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


@pytest.fixture
def children():
    started: list[subprocess.Popen] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def _spawn(children, *args, **kwargs) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, *args], **kwargs)
    children.append(proc)
    return proc


def test_pid_alive_for_this_process_and_nonsense_ids():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(0) is False
    assert pid_alive(-5) is False


def test_pid_alive_follows_a_child_without_disturbing_it(children):
    child = _spawn(children, "-c", SLEEPER)
    assert pid_alive(child.pid) is True
    assert (
        child.poll() is None
    )  # asking must never signal the process (signal 0 is Ctrl+C on Windows)
    child.kill()
    child.wait()
    assert pid_alive(child.pid) is False


def test_try_lock_exclusive_refuses_a_second_holder_then_frees_on_close(tmp_path):
    path = tmp_path / "x.lock"
    first = path.open("a+")
    second = path.open("a+")
    try:
        assert try_lock_exclusive(first) is True
        assert try_lock_exclusive(second) is False
        first.close()
        assert _wait_until(lambda: try_lock_exclusive(second), timeout=5)
    finally:
        first.close()
        second.close()


def test_lock_is_dropped_when_the_holding_process_dies(tmp_path, children):
    path = tmp_path / "held.lock"
    holder = _spawn(
        children,
        "-c",
        textwrap.dedent(
            f"""
            import time
            from anthill.platform_layer import try_lock_exclusive
            handle = open({str(path)!r}, "a+")
            assert try_lock_exclusive(handle)
            print("locked", flush=True)
            time.sleep(60)
            """
        ),
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"
    mine = path.open("a+")
    try:
        assert try_lock_exclusive(mine) is False  # another process holds it
        holder.kill()
        holder.wait()
        assert _wait_until(lambda: try_lock_exclusive(mine), timeout=10)  # the system dropped it
    finally:
        mine.close()


def test_scheduler_election_lets_only_one_process_run(tmp_path, monkeypatch, children):
    from anthill.web import scheduler

    engine = SimpleNamespace(url=SimpleNamespace(database=str(tmp_path / "anthill.db")))
    monkeypatch.setattr(scheduler, "_scheduler_process_lock", None)
    assert scheduler._acquire_scheduler_process_lock(engine) is True
    try:
        rival = textwrap.dedent(
            f"""
            import sys
            from types import SimpleNamespace
            from anthill.web import scheduler
            engine = SimpleNamespace(url=SimpleNamespace(database={str(tmp_path / "anthill.db")!r}))
            sys.exit(0 if scheduler._acquire_scheduler_process_lock(engine) else 7)
            """
        )
        assert _spawn(children, "-c", rival).wait(timeout=120) == 7  # a second process is refused
    finally:
        scheduler._scheduler_process_lock.close()
        monkeypatch.setattr(scheduler, "_scheduler_process_lock", None)
    assert _wait_until(lambda: _spawn(children, "-c", rival).wait(timeout=120) == 0, timeout=20)


WATCHED = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from anthill import desktop

    beat = Path(sys.argv[1])
    desktop._exit_when_orphaned()
    for _ in range(300):  # a stray test run cannot leave this behind for long
        beat.write_text(str(time.time()))
        time.sleep(0.1)
    """
)

MIDDLE = textwrap.dedent(
    """
    import subprocess, sys
    subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]]).wait()
    """
)


def _heartbeat_stopped(beat: Path, quiet_for: float = 2.5) -> bool:
    return beat.exists() and time.time() - beat.stat().st_mtime > quiet_for


def _sidecar_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHILL_SHELL_PID"}
    env["ANTHILL_NO_BROWSER"] = "1"
    env.update(extra)
    return env


def test_backend_exits_when_its_launcher_is_killed(tmp_path, children):
    """The bootloader dies (a quit that kills it, or a crash): the worker must stop serving, on every OS."""
    worker_py = tmp_path / "worker.py"
    worker_py.write_text(WATCHED)
    middle_py = tmp_path / "middle.py"
    middle_py.write_text(MIDDLE)
    beat = tmp_path / "beat.txt"
    middle = _spawn(children, str(middle_py), str(worker_py), str(beat), env=_sidecar_env())
    assert _wait_until(beat.exists, timeout=60)  # the worker is up and beating
    middle.kill()
    middle.wait()
    assert _wait_until(lambda: _heartbeat_stopped(beat), timeout=20), (
        "backend kept running after its launcher died"
    )


def test_backend_exits_when_the_desktop_shell_is_killed(tmp_path, children):
    """The shell crashes or is force-quit: the backend, still parented to a live process, must notice."""
    worker_py = tmp_path / "worker.py"
    worker_py.write_text(WATCHED)
    beat = tmp_path / "beat.txt"
    shell = _spawn(children, "-c", SLEEPER)
    _spawn(
        children,
        str(worker_py),
        str(beat),
        env=_sidecar_env(ANTHILL_SHELL_PID=str(shell.pid)),
    )
    assert _wait_until(beat.exists, timeout=60)
    shell.kill()
    shell.wait()
    assert _wait_until(lambda: _heartbeat_stopped(beat), timeout=20), (
        "backend kept running after the shell died"
    )
