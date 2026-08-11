"""Shell-free, bounded subprocess execution with process-tree cleanup."""

from __future__ import annotations

import math
import locale
import os
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _JobObjectBasicLimitInformation(ctypes.Structure):
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

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _KERNEL32.SetInformationJobObject.restype = wintypes.BOOL
    _KERNEL32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _KERNEL32.AssignProcessToJobObject.restype = wintypes.BOOL
    _KERNEL32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _KERNEL32.TerminateJobObject.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


class _WindowsJob:
    """Own a kill-on-close Windows Job Object for one subprocess tree."""

    _EXTENDED_LIMIT_INFORMATION = 9
    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    def __init__(self, handle: object) -> None:
        self._handle = handle

    @classmethod
    def attach(cls, process: subprocess.Popen[bytes]) -> "_WindowsJob | None":
        if os.name != "nt":
            return None
        handle = _KERNEL32.CreateJobObjectW(None, None)
        if not handle:
            return None
        information = _JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = cls._LIMIT_KILL_ON_JOB_CLOSE
        configured = _KERNEL32.SetInformationJobObject(
            handle,
            cls._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and _KERNEL32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(int(process._handle)),
        )
        if not assigned:
            _KERNEL32.CloseHandle(handle)
            return None
        return cls(handle)

    def terminate(self) -> bool:
        if os.name != "nt" or self._handle is None:
            return False
        return bool(_KERNEL32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        if os.name == "nt" and self._handle is not None:
            _KERNEL32.CloseHandle(self._handle)
            self._handle = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def _validated_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("argv must be a non-empty sequence of strings")
    normalized = tuple(argv)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in normalized):
        raise ValueError("argv entries must be non-empty strings without NUL bytes")
    return normalized


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    windows_job: _WindowsJob | None,
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if windows_job is not None and windows_job.terminate():
            return
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        if completed.returncode != 0 and process.poll() is None:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()


def _decode_output(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return value.decode(locale.getpreferredencoding(False), errors="replace")


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout_seconds: float = 30,
) -> ProcessResult:
    """Run an argument vector without a shell and bound the whole process tree."""

    normalized = _validated_argv(argv)
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")

    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(
        list(normalized),
        cwd=None if cwd is None else Path(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **popen_options,
    )
    windows_job = _WindowsJob.attach(process)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process, windows_job)
        stdout, stderr = process.communicate()
    finally:
        if windows_job is not None:
            windows_job.close()
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    return ProcessResult(
        argv=normalized,
        returncode=process.returncode,
        stdout=_decode_output(stdout),
        stderr=_decode_output(stderr),
        timed_out=timed_out,
        duration_ms=duration_ms,
    )


__all__ = ["ProcessResult", "run_bounded"]
