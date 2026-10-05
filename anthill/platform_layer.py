"""The few things Anthill must do differently on each operating system, in one place.

The rest of the code asks this module ("is that process alive?", "can I take this lock?") and does not
test the operating system itself. See docs/specs/windows-support.md and docs/platform-support.md.

The module is not called ``platform.py``: PyInstaller puts ``anthill/`` on its search path when it
analyses ``anthill/desktop.py``, and a sibling called ``platform`` would then hide the standard library
module of that name.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from typing import IO, Any

IS_WINDOWS = sys.platform == "win32"

# Windows: the access right that lets us ask whether a process has finished, and the exit code a
# process reports while it is still running.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running and we could signal it.

    The desktop shell is not our child, so we cannot wait on it; we can only ask the system whether
    its id still belongs to a live process. Never sends a signal."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)  # signal 0 sends nothing; it only checks the process exists
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours


def _pid_alive_windows(pid: int) -> bool:
    # Not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, which would interrupt the process.
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means it exists but belongs to someone else; anything else means it is gone.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # type: ignore[attr-defined]
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return True  # cannot tell; treat as alive rather than kill a healthy backend
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def try_lock_exclusive(handle: IO[str]) -> bool:
    """Take a lock on the open file that the system drops when this process dies.

    Returns True if we hold it now, False if another process already does. Any other failure (a file
    system that cannot lock) raises ``OSError`` for the caller to decide on."""
    if IS_WINDOWS:
        import msvcrt

        handle.seek(0)  # the lock covers the byte at the current position
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def hidden_window_kwargs() -> dict[str, Any]:
    """Extra ``subprocess`` arguments that stop a child console program flashing a window on Windows.

    Empty everywhere else, so a call that spreads it behaves exactly as before on macOS and Linux."""
    if not IS_WINDOWS:
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]


def detached_process_kwargs() -> dict[str, Any]:
    """``subprocess`` arguments that let a child outlive the process that started it.

    macOS and Linux: a new session. Windows: no console window, no console shared with us, and its own
    process group, so closing Anthill neither closes it nor sends it our Ctrl+C."""
    if not IS_WINDOWS:
        return {"start_new_session": True}
    flags = (
        subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    )
    return {"creationflags": flags}


def memory_gb() -> tuple[float, float] | None:
    """``(total, available)`` physical memory in GB on Windows, or None elsewhere or if unreadable.

    The other platforms have their own probes in anthill/hosting/sizing.py."""
    if not IS_WINDOWS:
        return None
    import ctypes

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    gb = 1024**3
    return (status.ullTotalPhys / gb, status.ullAvailPhys / gb)
