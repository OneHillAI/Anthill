from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .reader import (
    PDF_HARD_WORKER_MEMORY_BYTES,
    PDF_HARD_WORKER_OUTPUT_BYTES,
    PdfSafetyLimitExceeded,
    _file_content_to_payload,
    _read_pdf,
)

_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_WINDOWS_JOB_HANDLE: int | None = None


class _WindowsIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _WindowsJobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _WindowsJobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _WindowsJobBasicLimitInformation),
        ("IoInfo", _WindowsIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _current_virtual_memory_bytes() -> int:
    if sys.platform.startswith("linux"):
        pages = int(Path("/proc/self/statm").read_text().split()[0])
        return pages * os.sysconf("SC_PAGE_SIZE")
    output = subprocess.check_output(["ps", "-o", "vsz=", "-p", str(os.getpid())], text=True)
    return int(output.strip()) * 1024


def _windows_error(message: str) -> OSError:
    get_last_error = getattr(ctypes, "get_last_error", None)
    error_code = get_last_error() if get_last_error is not None else 0
    return OSError(error_code, message)


def _apply_windows_memory_limit(kernel32=None, psapi=None) -> None:
    global _WINDOWS_JOB_HANDLE
    if _WINDOWS_JOB_HANDLE is not None:
        return
    if kernel32 is None or psapi is None:
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise _windows_error("Windows Job Object APIs are unavailable")
        kernel32 = loader("kernel32", use_last_error=True)
        psapi = loader("psapi", use_last_error=True)

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    process = kernel32.GetCurrentProcess()
    counters = _WindowsProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        raise _windows_error("could not read worker memory usage")

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _windows_error("could not create worker memory job")
    try:
        limits = _WindowsJobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.ProcessMemoryLimit = counters.PrivateUsage + PDF_HARD_WORKER_MEMORY_BYTES
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _windows_error("could not configure worker memory job")
        if not kernel32.AssignProcessToJobObject(job, process):
            raise _windows_error("could not assign worker memory job")
    except OSError:
        kernel32.CloseHandle(job)
        raise
    _WINDOWS_JOB_HANDLE = job


def _apply_memory_limit(current_virtual_memory_bytes=_current_virtual_memory_bytes) -> None:
    if sys.platform == "win32":
        try:
            _apply_windows_memory_limit()
        except OSError as exc:
            raise PdfSafetyLimitExceeded(
                "PDF parsing cannot establish the worker memory safety limit.", transient=True
            ) from exc
        return
    try:
        import resource
    except ImportError as exc:
        raise PdfSafetyLimitExceeded(
            "PDF parsing cannot establish the worker memory safety limit.", transient=True
        ) from exc

    limit_kind = getattr(resource, "RLIMIT_AS", None)
    if limit_kind is None:
        raise PdfSafetyLimitExceeded(
            "PDF parsing cannot establish the worker memory safety limit.", transient=True
        )
    try:
        _, current_hard = resource.getrlimit(limit_kind)
        hard_limit = current_virtual_memory_bytes() + PDF_HARD_WORKER_MEMORY_BYTES
        if current_hard != resource.RLIM_INFINITY and current_hard >= 0:
            hard_limit = min(current_hard, hard_limit)
        resource.setrlimit(limit_kind, (hard_limit, hard_limit))
    except (OSError, ValueError) as exc:
        raise PdfSafetyLimitExceeded(
            "PDF parsing cannot establish the worker memory safety limit.", transient=True
        ) from exc


def _reserve_ipc_fd() -> int:
    """Move the parent-bound payload channel off fd 1, then point fd 1 at stderr.

    Native parsers underneath markitdown write to fd 1 directly, which
    `contextlib.redirect_stdout` cannot intercept; anything they emit would land in
    the middle of the JSON stream the parent reads.
    """
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    ipc_fd = os.dup(1)
    # Literal fd 2: a frozen windowed build can have `sys.stderr` set to None, but the
    # parent always spawns us with a stderr pipe on fd 2.
    os.dup2(2, 1)
    return ipc_fd


def main(arguments: list[str] | None = None) -> int:
    arguments = arguments if arguments is not None else sys.argv[1:]
    if len(arguments) != 1:
        return 2
    ipc_fd = _reserve_ipc_fd()
    status: str
    payload: dict[str, object] | str
    try:
        _apply_memory_limit()
        result = _read_pdf(Path(arguments[0]))
        status, payload = "ok", _file_content_to_payload(result)
    except MemoryError:
        status, payload = "safety-transient", "PDF parsing exceeded the worker memory safety limit."
    except PdfSafetyLimitExceeded as exc:
        status, payload = ("safety-transient" if exc.transient else "safety"), str(exc)
    except ImportError as exc:
        status, payload = "import", str(exc)
    except Exception as exc:
        status, payload = "error", str(exc)
    output = json.dumps(
        {"version": 1, "status": status, "payload": payload},
        separators=(",", ":"),
    ).encode()
    if len(output) > PDF_HARD_WORKER_OUTPUT_BYTES:
        output = json.dumps(
            {
                "version": 1,
                "status": "safety",
                "payload": "PDF parsing exceeded the worker output safety limit.",
            },
            separators=(",", ":"),
        ).encode()
    with os.fdopen(ipc_fd, "wb", closefd=True) as channel:
        channel.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
