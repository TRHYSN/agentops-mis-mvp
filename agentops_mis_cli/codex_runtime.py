"""Bounded Codex CLI execution for the AgentOps worker adapter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from .redaction import redact_text


DEFAULT_CODEX_APP_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
READ_ONLY_PROHIBITED_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
    "browser_action",
    "computer_use",
}
WORKSPACE_WRITE_PROHIBITED_ITEM_TYPES = {
    "mcp_tool_call",
    "web_search",
    "browser_action",
    "computer_use",
}
ALLOWED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "item.started",
    "item.updated",
    "item.completed",
    "turn.completed",
    "error",
}
ALLOWED_ITEM_TYPES = {"agent_message", "reasoning", "error"}
WORKSPACE_WRITE_ITEM_TYPES = ALLOWED_ITEM_TYPES | {"command_execution", "file_change", "todo_list"}
MAX_JSONL_BYTES = 2_000_000
MAX_JSONL_EVENTS = 2_000
MAX_STDERR_BYTES = 262_144
MAX_WORKSPACE_WRITE_FILES = 200
MAX_WORKSPACE_WRITE_BYTES = 10_000_000
MAX_GIT_PATH_OUTPUT_BYTES = 1_000_000
SECRET_DIFF_PATTERNS = (
    b"BEGIN PRIVATE KEY",
    b"github_pat_",
    b"ghp_",
    b"xoxb-",
    b"xoxp-",
    b"agtok_",
    b"agtsess_",
    b"sk-proj-",
)
DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "multi_agent",
    "plugins",
    "shell_tool",
    "unified_exec",
)
WORKSPACE_WRITE_DISABLED_FEATURES = tuple(
    feature for feature in DISABLED_FEATURES if feature not in {"shell_tool", "unified_exec"}
)


@dataclass
class CodexRuntimeResult:
    ok: bool
    output_summary: str
    raw_payload_hash: str
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    output_tokens: int = 0
    target_resource: str = "local://codex/read-only"
    retryable: bool = False
    observation: dict | None = None


@dataclass
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str


class CodexOutputLimitExceeded(RuntimeError):
    pass


class _WindowsKillJob:
    """Keep a Windows subprocess tree killable after its launcher exits."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimitInformation(ctypes.Structure):
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

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _ThreadEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._kernel32 = kernel32
        self._handle = handle
        self._thread_entry_type = _ThreadEntry32

    def assign(self, proc: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(proc._handle))  # type: ignore[attr-defined]
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def resume(self, proc: subprocess.Popen[bytes]) -> None:
        import ctypes

        snapshot = self._kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if int(snapshot) == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        resumed = False
        try:
            entry = self._thread_entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            available = self._kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if int(entry.th32OwnerProcessID) == proc.pid:
                    thread = self._kernel32.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if thread:
                        try:
                            if self._kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                                resumed = True
                        finally:
                            self._kernel32.CloseHandle(thread)
                available = self._kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            self._kernel32.CloseHandle(snapshot)
        if not resumed:
            raise OSError(ctypes.get_last_error(), "ResumeThread failed")

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _binary_is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return path.suffix.lower() == ".exe"
    return os.access(path, os.X_OK)


def _binary_command(binary: Path, arguments: list[str]) -> list[str]:
    return [str(binary), *arguments]


def resolve_codex_binary(configured: str = "") -> Path:
    explicit = configured.strip() or os.environ.get("CODEX_BIN", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    candidates = [
        DEFAULT_CODEX_APP_BIN,
        shutil.which("codex") or "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if _binary_is_executable(path):
            return path
    return Path(candidates[0] or "codex").expanduser()


def _safe_proxy_value(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        return None
    return value


def codex_subprocess_env() -> dict[str, str]:
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "PATH",
        "PATHEXT",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "TEMP",
        "TMPDIR",
        "TMP",
        "USERPROFILE",
        "WINDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "NO_COLOR",
        "CODEX_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed and value}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        value = os.environ.get(key)
        if not value:
            continue
        safe_value = _safe_proxy_value(value) if key.lower() != "no_proxy" else value
        if safe_value:
            env[key] = safe_value
    env["NO_COLOR"] = "1"
    return env


def codex_binary_attestation(binary_path: str, *, timeout: int = 10) -> dict:
    binary = resolve_codex_binary(binary_path)
    default_binary = Path(DEFAULT_CODEX_APP_BIN)
    executable = _binary_is_executable(binary)
    official_bundle = False
    if executable and default_binary.is_file():
        official_bundle = binary.resolve() == default_binary.resolve()
    version_ok = False
    version_summary = ""
    binary_sha256 = None
    if executable:
        try:
            proc = subprocess.run(
                _binary_command(binary, ["--version"]),
                cwd=Path.cwd(),
                env=codex_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=min(max(int(timeout or 10), 1), 20),
                check=False,
            )
            version_ok = proc.returncode == 0
            version_summary = redact_text(proc.stdout or proc.stderr or f"exit={proc.returncode}", 160)
            digest = hashlib.sha256()
            with binary.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            binary_sha256 = digest.hexdigest()
        except Exception as exc:
            version_summary = redact_text(str(exc), 160)
    return {
        "attested": bool(executable and version_ok and official_bundle and binary_sha256),
        "official_chatgpt_bundle": official_bundle,
        "binary_executable": executable,
        "binary_sha256": binary_sha256,
        "version_ok": version_ok,
        "version_summary": version_summary,
        "binary_path_hash": hashlib.sha256(str(binary.resolve() if executable else binary).encode("utf-8")).hexdigest(),
        "raw_binary_path_omitted": True,
        "token_omitted": True,
    }


def codex_command(binary: Path, cwd: Path, *, sandbox: str = "read-only") -> list[str]:
    disabled_features = DISABLED_FEATURES if sandbox == "read-only" else WORKSPACE_WRITE_DISABLED_FEATURES
    arguments = [
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--strict-config",
    ]
    if sandbox == "read-only":
        arguments.append("--skip-git-repo-check")
    arguments.extend([
        "--config",
        'web_search="disabled"',
        "--sandbox",
        sandbox,
        "--color",
        "never",
        "-C",
        str(cwd),
        "-",
    ])
    for feature in disabled_features:
        arguments[1:1] = ["--disable", feature]
    return _binary_command(binary, arguments)


def _terminate_process_tree(proc: subprocess.Popen[bytes], windows_job: _WindowsKillJob | None = None) -> None:
    if os.name == "nt":
        if windows_job is not None:
            windows_job.terminate()
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if proc.poll() is None:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        if proc.poll() is None:
            proc.kill()


def _capture_stream(
    stream,
    target: bytearray,
    limit: int,
    output_limit_exceeded: threading.Event,
) -> None:
    try:
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                return
            remaining = max((limit + 1) - len(target), 0)
            if remaining:
                target.extend(chunk[:remaining])
            if len(chunk) > remaining or len(target) > limit:
                output_limit_exceeded.set()
                return
    finally:
        stream.close()


def _write_stdin(
    stream,
    prompt: bytes,
    errors: list[Exception],
    completed: threading.Event,
) -> None:
    try:
        stream.write(prompt)
        stream.flush()
    except Exception as exc:
        errors.append(exc)
    finally:
        try:
            stream.close()
        finally:
            completed.set()


def _run_codex_bounded(*, command: list[str], cwd: Path, prompt: str, timeout: int) -> BoundedProcessResult:
    process_options: dict[str, object] = {}
    windows_job: _WindowsKillJob | None = None
    if os.name == "nt":
        process_options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        )
        windows_job = _WindowsKillJob()
    else:
        process_options["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=codex_subprocess_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_options,
        )
        if windows_job is not None:
            windows_job.assign(proc)
            windows_job.resume(proc)
    except Exception:
        if "proc" in locals():
            _terminate_process_tree(proc, windows_job)
        if windows_job is not None:
            windows_job.close()
        raise
    stdout = bytearray()
    stderr = bytearray()
    started = time.monotonic()
    deadline = started + max(float(timeout), 0.0)
    output_limit_exceeded = threading.Event()
    stdin_completed = threading.Event()
    stdin_errors: list[Exception] = []
    readers: list[threading.Thread] = []
    writer: threading.Thread | None = None

    try:
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            raise RuntimeError("Codex subprocess pipes were not created")
        readers = [
            threading.Thread(
                target=_capture_stream,
                args=(proc.stdout, stdout, MAX_JSONL_BYTES, output_limit_exceeded),
                daemon=True,
            ),
            threading.Thread(
                target=_capture_stream,
                args=(proc.stderr, stderr, MAX_STDERR_BYTES, output_limit_exceeded),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        writer = threading.Thread(
            target=_write_stdin,
            args=(proc.stdin, prompt.encode("utf-8"), stdin_errors, stdin_completed),
            daemon=True,
        )
        writer.start()
        while True:
            if output_limit_exceeded.is_set():
                _terminate_process_tree(proc, windows_job)
                raise CodexOutputLimitExceeded("Codex subprocess output exceeded its bounded capture limit")
            if stdin_errors:
                _terminate_process_tree(proc, windows_job)
                raise RuntimeError("Codex subprocess closed stdin before receiving the bounded prompt")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(proc, windows_job)
                raise subprocess.TimeoutExpired(command, timeout)
            if proc.poll() is not None:
                break
            time.sleep(min(0.05, remaining))
        returncode = proc.wait(timeout=5)
        if writer is not None:
            writer.join(timeout=max(deadline - time.monotonic(), 0.0))
            if writer.is_alive() or not stdin_completed.is_set():
                _terminate_process_tree(proc, windows_job)
                raise subprocess.TimeoutExpired(command, timeout)
        if stdin_errors:
            raise RuntimeError("Codex subprocess closed stdin before receiving the bounded prompt")
        for reader in readers:
            reader.join(timeout=max(deadline - time.monotonic(), 0.0))
        if any(reader.is_alive() for reader in readers):
            _terminate_process_tree(proc, windows_job)
            raise subprocess.TimeoutExpired(command, timeout)
        if output_limit_exceeded.is_set():
            raise CodexOutputLimitExceeded("Codex subprocess output exceeded its bounded capture limit")
        return BoundedProcessResult(
            returncode=returncode,
            stdout=bytes(stdout).decode("utf-8", errors="replace"),
            stderr=bytes(stderr).decode("utf-8", errors="replace"),
        )
    except Exception:
        _terminate_process_tree(proc, windows_job)
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if writer is not None:
            writer.join(timeout=1)
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=1)
        raise
    finally:
        if windows_job is not None:
            windows_job.close()


def _parse_jsonl(stdout: str, *, sandbox: str = "read-only") -> tuple[str, int, dict, list[str], list[str]]:
    final_message = ""
    output_tokens = 0
    event_counts: dict[str, int] = {}
    item_counts: dict[str, int] = {}
    parse_errors = 0
    prohibited: list[str] = []
    protocol_errors: list[str] = []
    write_mode = sandbox == "workspace-write"
    prohibited_item_types = WORKSPACE_WRITE_PROHIBITED_ITEM_TYPES if write_mode else READ_ONLY_PROHIBITED_ITEM_TYPES
    allowed_item_types = WORKSPACE_WRITE_ITEM_TYPES if write_mode else ALLOWED_ITEM_TYPES
    lines = stdout.splitlines()
    if len(stdout.encode("utf-8")) > MAX_JSONL_BYTES:
        protocol_errors.append("jsonl_size_limit_exceeded")
    if len(lines) > MAX_JSONL_EVENTS:
        protocol_errors.append("jsonl_event_limit_exceeded")
    for line in lines[:MAX_JSONL_EVENTS]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        event_type = str(event.get("type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type not in ALLOWED_EVENT_TYPES:
            protocol_errors.append(f"unknown_event_type:{event_type}")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if item_type:
            item_counts[item_type] = item_counts.get(item_type, 0) + 1
        if item_type in prohibited_item_types:
            prohibited.append(item_type)
        elif item_type and item_type not in allowed_item_types:
            protocol_errors.append(f"unknown_item_type:{item_type}")
        if event_type == "item.completed" and item_type == "agent_message":
            final_message = str(item.get("text") or "").strip()
        if event_type == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            output_tokens = int(usage.get("output_tokens") or 0)
    if parse_errors:
        protocol_errors.append("jsonl_parse_error")
    for required_type in ("thread.started", "turn.started", "turn.completed"):
        if event_counts.get(required_type) != 1:
            protocol_errors.append(f"invalid_{required_type}_count")
    agent_message_count = item_counts.get("agent_message", 0)
    if (write_mode and agent_message_count < 1) or (not write_mode and agent_message_count != 1):
        protocol_errors.append("invalid_agent_message_count")
    runtime_error_event_count = event_counts.get("error", 0)
    protocol_completed = (
        event_counts.get("thread.started") == 1
        and event_counts.get("turn.started") == 1
        and event_counts.get("turn.completed") == 1
        and ((write_mode and agent_message_count >= 1) or (not write_mode and agent_message_count == 1))
    )
    if runtime_error_event_count and not protocol_completed:
        protocol_errors.append("runtime_error_event_without_completion")
    observation = {
        "protocol": "codex_exec_jsonl_v1",
        "sandbox": sandbox,
        "ephemeral": True,
        "ignore_user_config": True,
        "strict_config": True,
        "git_repo_check": "skipped_for_packaged_read_only_runtime" if not write_mode else "required_managed_worktree",
        "web_search": "disabled",
        "disabled_features": list(DISABLED_FEATURES if not write_mode else WORKSPACE_WRITE_DISABLED_FEATURES),
        "prompt_transport": "stdin",
        "event_counts": event_counts,
        "item_type_counts": item_counts,
        "parse_errors": parse_errors,
        "runtime_error_event_count": runtime_error_event_count,
        "runtime_error_recovered": bool(runtime_error_event_count and protocol_completed),
        "prohibited_item_types": sorted(set(prohibited)),
        "prohibited_event_count": len(prohibited),
        "protocol_errors": sorted(set(protocol_errors)),
        "protocol_valid": not protocol_errors,
        "raw_events_omitted": True,
        "raw_prompt_omitted": True,
        "raw_response_omitted": True,
        "token_omitted": True,
    }
    return final_message, output_tokens, observation, prohibited, protocol_errors


def execute_codex_read_only(*, binary_path: str, prompt: str, cwd: Path, timeout: int) -> CodexRuntimeResult:
    binary = resolve_codex_binary(binary_path)
    started = time.time()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return CodexRuntimeResult(
            ok=False,
            output_summary="Codex CLI is not available.",
            raw_payload_hash=hashlib.sha256(b"codex-binary-unavailable").hexdigest(),
            error_type="CodexBinaryUnavailable",
            error_message="Install Codex CLI or set CODEX_BIN to an executable path.",
            target_resource="local://codex/unavailable",
            retryable=False,
        )
    try:
        proc = _run_codex_bounded(
            command=codex_command(binary, cwd),
            cwd=cwd,
            prompt=prompt,
            timeout=max(int(timeout or 300), 1),
        )
        final_message, output_tokens, observation, prohibited, protocol_errors = _parse_jsonl(proc.stdout or "", sandbox="read-only")
        raw_hash = hashlib.sha256((proc.stdout or "").encode("utf-8")).hexdigest()
        duration_ms = int((time.time() - started) * 1000)
        if prohibited:
            return CodexRuntimeResult(
                ok=False,
                output_summary="Codex read-only adapter blocked a run that emitted prohibited tool events.",
                raw_payload_hash=raw_hash,
                error_type="CodexProhibitedToolEvent",
                error_message=f"Prohibited Codex event types: {', '.join(sorted(set(prohibited)))}.",
                duration_ms=duration_ms,
                output_tokens=output_tokens,
                retryable=False,
                observation=observation,
            )
        if protocol_errors:
            return CodexRuntimeResult(
                ok=False,
                output_summary="Codex read-only adapter rejected an invalid runtime event stream.",
                raw_payload_hash=raw_hash,
                error_type="CodexProtocolViolation",
                error_message=f"Codex JSONL protocol errors: {', '.join(sorted(set(protocol_errors)))}.",
                duration_ms=duration_ms,
                output_tokens=output_tokens,
                retryable=False,
                observation=observation,
            )
        ok = proc.returncode == 0 and bool(final_message)
        return CodexRuntimeResult(
            ok=ok,
            output_summary=redact_text(final_message, 200) if ok else "Codex read-only adapter execution failed.",
            raw_payload_hash=raw_hash,
            error_type=None if ok else "CodexExecutionFailed",
            error_message=None if ok else redact_text(proc.stderr or f"exit={proc.returncode}", 200),
            duration_ms=duration_ms,
            output_tokens=output_tokens,
            retryable=proc.returncode != 0,
            observation=observation,
        )
    except subprocess.TimeoutExpired:
        return CodexRuntimeResult(
            ok=False,
            output_summary="Codex read-only adapter timed out.",
            raw_payload_hash=hashlib.sha256(b"codex-timeout").hexdigest(),
            error_type="CodexTimeout",
            error_message=f"Codex execution exceeded {max(int(timeout or 300), 1)} seconds.",
            duration_ms=int((time.time() - started) * 1000),
            retryable=True,
        )
    except Exception as exc:
        return CodexRuntimeResult(
            ok=False,
            output_summary="Codex read-only adapter execution failed.",
            raw_payload_hash=hashlib.sha256(type(exc).__name__.encode("utf-8")).hexdigest(),
            error_type="CodexExecutionFailed",
            error_message=redact_text(str(exc), 200),
            duration_ms=int((time.time() - started) * 1000),
            retryable=True,
        )


def normalize_allowed_paths(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip().replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        if (
            not value
            or value in {".", ".git"}
            or path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] == ".git"
            or "\x00" in value
        ):
            raise ValueError(f"invalid Codex allowed path: {raw_value!r}")
        item = path.as_posix().rstrip("/")
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        raise ValueError("Codex workspace-write requires at least one bounded allowed path.")
    return sorted(normalized)


def _run_git(cwd: Path, args: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=codex_subprocess_env(),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = redact_text(proc.stderr.decode("utf-8", errors="replace") or f"exit={proc.returncode}", 220)
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail}")
    return proc


def _run_git_bounded(
    cwd: Path,
    args: list[str],
    *,
    stdout_limit: int,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Capture potentially large Git output without allowing unbounded memory use."""
    command = ["git", *args]
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=codex_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout = bytearray()
    stderr = bytearray()
    started = time.monotonic()

    def terminate_group() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("Git subprocess pipes were not created")
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, (stdout, stdout_limit))
        selector.register(proc.stderr, selectors.EVENT_READ, (stderr, MAX_STDERR_BYTES))
        while selector.get_map():
            if time.monotonic() - started > timeout:
                terminate_group()
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in selector.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target, limit = key.data
                target.extend(chunk)
                if len(target) > limit:
                    terminate_group()
                    raise RuntimeError("git output exceeded the workspace-write capture limit")
        returncode = proc.wait(timeout=5)
        result = subprocess.CompletedProcess(command, returncode, bytes(stdout), bytes(stderr))
        if returncode != 0:
            detail = redact_text(result.stderr.decode("utf-8", errors="replace") or f"exit={returncode}", 220)
            raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail}")
        return result
    except Exception:
        if proc.poll() is None:
            terminate_group()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise


def _ensure_allowed_roots_do_not_escape(repo_root: Path, allowed_paths: list[str]) -> None:
    for allowed in allowed_paths:
        cursor = repo_root
        for part in PurePosixPath(allowed).parts:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise ValueError(f"Codex allowed path traverses a symlink: {allowed}")
        existing = cursor if cursor.exists() else cursor.parent
        resolved = existing.resolve()
        if resolved != repo_root and repo_root not in resolved.parents:
            raise ValueError(f"Codex allowed path escapes repository root: {allowed}")


def codex_repository_preflight(*, source_repo: Path, allowed_paths: list[str] | tuple[str, ...]) -> dict:
    requested = source_repo.expanduser().resolve()
    allowed = normalize_allowed_paths(allowed_paths)
    if not requested.is_dir():
        raise ValueError("Codex source repository does not exist or is not a directory.")
    root_proc = _run_git(requested, ["rev-parse", "--show-toplevel"])
    repo_root = Path(root_proc.stdout.decode("utf-8", errors="replace").strip()).resolve()
    if repo_root != requested:
        raise ValueError("Codex source repository must be the exact Git worktree root.")
    _ensure_allowed_roots_do_not_escape(repo_root, allowed)
    status = _run_git_bounded(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        stdout_limit=MAX_GIT_PATH_OUTPUT_BYTES,
    ).stdout
    head = _run_git(repo_root, ["rev-parse", "HEAD"]).stdout.decode("ascii", errors="replace").strip()
    branch = _run_git(repo_root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    branch_name = branch.stdout.decode("utf-8", errors="replace").strip() or None
    return {
        "ok": not bool(status),
        "clean": not bool(status),
        "source_repo": str(repo_root),
        "source_repo_hash": hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest(),
        "baseline_head": head,
        "branch": branch_name,
        "allowed_paths": allowed,
        "status_hash": hashlib.sha256(status).hexdigest(),
        "dirty_entry_count": len([line for line in status.splitlines() if line]),
        "raw_status_omitted": True,
    }


def default_codex_worktree_root() -> Path:
    return Path(os.environ.get("AGENTOPS_HOME", "~/.agentops")).expanduser() / "codex-worktrees"


def managed_codex_worktree_path(action_id: str, worktree_root: Path | None = None) -> Path:
    safe_action_id = "".join(ch for ch in str(action_id or "") if ch.isalnum() or ch in {"-", "_"})[:96]
    if not safe_action_id:
        raise ValueError("prepared action id is required for a managed Codex worktree")
    root = (worktree_root or default_codex_worktree_root()).expanduser().resolve()
    return root / safe_action_id


def create_managed_codex_worktree(*, source_repo: Path, action_id: str, baseline_head: str, worktree_root: Path | None = None) -> Path:
    target = managed_codex_worktree_path(action_id, worktree_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError("managed Codex worktree already exists; inspect or roll it back before retrying")
    proc = _run_git(source_repo, ["worktree", "add", "--detach", str(target), baseline_head], timeout=60, check=False)
    if proc.returncode != 0:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        detail = redact_text(proc.stderr.decode("utf-8", errors="replace") or f"exit={proc.returncode}", 220)
        raise RuntimeError(f"failed to create managed Codex worktree: {detail}")
    return target.resolve()


def remove_managed_codex_worktree(*, source_repo: Path, worktree: Path) -> bool:
    proc = _run_git(source_repo, ["worktree", "remove", "--force", str(worktree)], timeout=60, check=False)
    if proc.returncode != 0 and worktree.exists():
        return False
    _run_git(source_repo, ["worktree", "prune"], timeout=30, check=False)
    return not worktree.exists()


def _changed_paths(worktree: Path) -> tuple[list[str], list[str], list[str]]:
    tracked_raw = _run_git_bounded(
        worktree,
        ["diff", "--name-only", "-z", "--no-renames", "HEAD", "--"],
        stdout_limit=MAX_GIT_PATH_OUTPUT_BYTES,
    ).stdout
    untracked_raw = _run_git_bounded(
        worktree,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        stdout_limit=MAX_GIT_PATH_OUTPUT_BYTES,
    ).stdout
    tracked = sorted({item.decode("utf-8", errors="surrogateescape") for item in tracked_raw.split(b"\0") if item})
    untracked = sorted({item.decode("utf-8", errors="surrogateescape") for item in untracked_raw.split(b"\0") if item})
    return tracked, untracked, sorted(set(tracked + untracked))


def _path_is_allowed(path: str, allowed_paths: list[str]) -> bool:
    return any(path == allowed or path.startswith(allowed + "/") for allowed in allowed_paths)


def _hash_untracked_files(worktree: Path, paths: list[str]) -> tuple[str, int, list[str]]:
    digest = hashlib.sha256()
    total_bytes = 0
    secret_hits: set[str] = set()
    overlap_size = max(len(pattern) for pattern in SECRET_DIFF_PATTERNS) - 1
    for relative in paths:
        target = worktree / relative
        if target.is_symlink() or not target.is_file():
            raise RuntimeError(f"workspace-write produced unsupported untracked path type: {relative}")
        size = target.stat().st_size
        total_bytes += size
        if total_bytes > MAX_WORKSPACE_WRITE_BYTES:
            raise RuntimeError("workspace-write untracked content exceeds the byte limit")
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        overlap = b""
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
                scan_window = overlap + chunk
                secret_hits.update(
                    pattern.decode("ascii")
                    for pattern in SECRET_DIFF_PATTERNS
                    if pattern in scan_window
                )
                overlap = scan_window[-overlap_size:] if overlap_size else b""
    return digest.hexdigest(), total_bytes, sorted(secret_hits)


def collect_codex_diff_evidence(*, worktree: Path, baseline_head: str, allowed_paths: list[str]) -> dict:
    current_head = _run_git(worktree, ["rev-parse", "HEAD"]).stdout.decode("ascii", errors="replace").strip()
    tracked, untracked, changed = _changed_paths(worktree)
    if current_head != baseline_head:
        raise RuntimeError("Codex changed Git HEAD inside the managed worktree")
    if not changed:
        raise RuntimeError("Codex workspace-write completed without a file change")
    if len(changed) > MAX_WORKSPACE_WRITE_FILES:
        raise RuntimeError("Codex workspace-write exceeded the changed-file limit")
    outside = [path for path in changed if not _path_is_allowed(path, allowed_paths)]
    if outside:
        raise RuntimeError(f"Codex changed paths outside the approved scope: {', '.join(outside[:8])}")
    for relative in changed:
        current = worktree / relative
        baseline_mode = _run_git(worktree, ["ls-tree", baseline_head, "--", relative], check=False).stdout
        current_mode = _run_git(worktree, ["ls-files", "-s", "--", relative], check=False).stdout
        if current.is_symlink() or baseline_mode.startswith(b"120000 "):
            raise RuntimeError(f"Codex workspace-write may not add or modify symlinks: {relative}")
        if baseline_mode.startswith(b"160000 ") or current_mode.startswith(b"160000 "):
            raise RuntimeError(f"Codex workspace-write may not add or modify submodules: {relative}")
    diff_check = _run_git(worktree, ["diff", "--check", baseline_head, "--"], check=False)
    if diff_check.returncode != 0:
        raise RuntimeError("Codex workspace-write failed git diff --check")
    diff = _run_git_bounded(
        worktree,
        ["diff", "--binary", "--no-ext-diff", "--no-renames", baseline_head, "--"],
        stdout_limit=MAX_WORKSPACE_WRITE_BYTES,
    ).stdout
    added_lines = b"\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith(b"+") and not line.startswith(b"+++")
    )
    secret_pattern_hits = [pattern.decode("ascii") for pattern in SECRET_DIFF_PATTERNS if pattern in added_lines]
    if secret_pattern_hits:
        raise RuntimeError("Codex workspace-write diff contains token or private-key markers")
    untracked_hash, untracked_bytes, untracked_secret_hits = _hash_untracked_files(worktree, untracked)
    secret_pattern_hits = sorted(set(secret_pattern_hits + untracked_secret_hits))
    if secret_pattern_hits:
        raise RuntimeError("Codex workspace-write content contains token or private-key markers")
    evidence_hash = hashlib.sha256(diff + b"\0" + untracked_hash.encode("ascii")).hexdigest()
    return {
        "baseline_head": baseline_head,
        "current_head": current_head,
        "head_unchanged": True,
        "changed_paths": changed,
        "changed_path_count": len(changed),
        "tracked_path_count": len(tracked),
        "untracked_path_count": len(untracked),
        "untracked_bytes": untracked_bytes,
        "diff_bytes": len(diff),
        "diff_hash": hashlib.sha256(diff).hexdigest(),
        "untracked_hash": untracked_hash,
        "evidence_hash": evidence_hash,
        "allowed_paths": list(allowed_paths),
        "git_diff_check_pass": True,
        "submodule_change_count": 0,
        "symlink_change_count": 0,
        "secret_scan_pass": True,
        "secret_pattern_hit_count": 0,
        "raw_diff_omitted": True,
        "raw_content_omitted": True,
    }


def execute_codex_workspace_write(
    *,
    binary_path: str,
    prompt: str,
    source_repo: Path,
    action_id: str,
    baseline_head: str,
    allowed_paths: list[str] | tuple[str, ...],
    timeout: int,
    worktree_root: Path | None = None,
    allow_test_fixture: bool = False,
) -> CodexRuntimeResult:
    binary = resolve_codex_binary(binary_path)
    started = time.time()
    allowed = normalize_allowed_paths(allowed_paths)
    source = source_repo.expanduser().resolve()
    worktree: Path | None = None
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return CodexRuntimeResult(
            ok=False,
            output_summary="Codex CLI is not available.",
            raw_payload_hash=hashlib.sha256(b"codex-binary-unavailable").hexdigest(),
            error_type="CodexBinaryUnavailable",
            error_message="Install Codex CLI or set CODEX_BIN to an executable path.",
            target_resource="local://codex/workspace-write/unavailable",
            retryable=False,
        )
    attestation = codex_binary_attestation(binary_path, timeout=min(timeout, 20))
    if not attestation.get("attested") and not allow_test_fixture:
        return CodexRuntimeResult(
            ok=False,
            output_summary="Codex workspace-write requires the attested ChatGPT-bundled Codex runtime.",
            raw_payload_hash=hashlib.sha256(b"codex-workspace-write-unattested-binary").hexdigest(),
            error_type="CodexWorkspaceWriteRuntimeUnattested",
            error_message="Arbitrary or unverified Codex binaries cannot receive workspace-write authority.",
            target_resource="local://codex/workspace-write/unattested",
            retryable=False,
            observation={"runtime_attestation": attestation, "product_readiness_proof": False, "token_omitted": True},
        )
    try:
        preflight = codex_repository_preflight(source_repo=source, allowed_paths=allowed)
        if not preflight["clean"]:
            raise RuntimeError("Codex source repository must be clean before workspace-write execution")
        if preflight["baseline_head"] != baseline_head:
            raise RuntimeError("Codex source repository HEAD no longer matches the approved prepared action")
        worktree = create_managed_codex_worktree(
            source_repo=source,
            action_id=action_id,
            baseline_head=baseline_head,
            worktree_root=worktree_root,
        )
        bounded_prompt = (
            f"{prompt}\n\n"
            "Workspace-write authorization:\n"
            f"- Modify only these repository-relative paths: {', '.join(allowed)}\n"
            "- Do not run git commit, git checkout, git reset, git clean, git worktree, or change Git metadata.\n"
            "- Do not access the network, browsers, MCP, other repositories, or paths outside this worktree.\n"
            "- Make the smallest complete change, run only relevant local verification, then summarize the result.\n"
        )
        proc = _run_codex_bounded(
            command=codex_command(binary, worktree, sandbox="workspace-write"),
            cwd=worktree,
            prompt=bounded_prompt,
            timeout=max(int(timeout or 600), 1),
        )
        final_message, output_tokens, observation, prohibited, protocol_errors = _parse_jsonl(
            proc.stdout or "", sandbox="workspace-write"
        )
        raw_hash = hashlib.sha256((proc.stdout or "").encode("utf-8")).hexdigest()
        if prohibited:
            raise RuntimeError(f"Codex emitted prohibited workspace-write events: {', '.join(sorted(set(prohibited)))}")
        if protocol_errors:
            raise RuntimeError(f"Codex JSONL protocol errors: {', '.join(sorted(set(protocol_errors)))}")
        if proc.returncode != 0 or not final_message:
            raise RuntimeError(redact_text(proc.stderr or f"Codex exited with status {proc.returncode}", 220))
        diff_evidence = collect_codex_diff_evidence(
            worktree=worktree,
            baseline_head=baseline_head,
            allowed_paths=allowed,
        )
        observation.update({
            "workspace_isolation": "managed_detached_git_worktree",
            "source_repo_hash": preflight["source_repo_hash"],
            "source_repo_clean": True,
            "managed_worktree_path_hash": hashlib.sha256(str(worktree).encode("utf-8")).hexdigest(),
            "rollback_required": False,
            "rollback_performed": False,
            "diff_evidence": diff_evidence,
            "runtime_attestation": {
                **attestation,
                "test_fixture_override": bool(allow_test_fixture),
            },
            "product_readiness_proof": bool(attestation.get("attested") and not allow_test_fixture),
        })
        return CodexRuntimeResult(
            ok=True,
            output_summary=redact_text(final_message, 200),
            raw_payload_hash=raw_hash,
            duration_ms=int((time.time() - started) * 1000),
            output_tokens=output_tokens,
            target_resource=f"local://codex/workspace-write/{action_id}",
            retryable=False,
            observation=observation,
        )
    except subprocess.TimeoutExpired:
        error_type = "CodexTimeout"
        error_message = f"Codex workspace-write exceeded {max(int(timeout or 600), 1)} seconds."
    except Exception as exc:
        error_type = "CodexWorkspaceWriteRejected"
        error_message = redact_text(str(exc), 240)
    rollback_performed = bool(worktree and remove_managed_codex_worktree(source_repo=source, worktree=worktree))
    return CodexRuntimeResult(
        ok=False,
        output_summary="Codex workspace-write failed closed and its managed worktree was rolled back." if rollback_performed else "Codex workspace-write failed closed; managed-worktree rollback needs operator review.",
        raw_payload_hash=hashlib.sha256((error_type + ":" + error_message).encode("utf-8")).hexdigest(),
        error_type=error_type,
        error_message=error_message,
        duration_ms=int((time.time() - started) * 1000),
        target_resource=f"local://codex/workspace-write/{action_id}",
        retryable=rollback_performed,
        observation={
            "protocol": "codex_exec_jsonl_v1",
            "sandbox": "workspace-write",
            "workspace_isolation": "managed_detached_git_worktree",
            "rollback_required": True,
            "rollback_performed": rollback_performed,
            "raw_diff_omitted": True,
            "raw_content_omitted": True,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
            "runtime_attestation": {
                **attestation,
                "test_fixture_override": bool(allow_test_fixture),
            },
            "product_readiness_proof": False,
        },
    )


def codex_preflight(*, binary_path: str, cwd: Path, timeout: int) -> dict:
    binary = resolve_codex_binary(binary_path)
    exists = binary.is_file()
    executable = _binary_is_executable(binary)
    version_ok = False
    version_summary = ""
    if executable:
        try:
            proc = subprocess.run(
                _binary_command(binary, ["--version"]),
                cwd=cwd,
                env=codex_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=min(max(int(timeout or 5), 1), 20),
                check=False,
            )
            version_ok = proc.returncode == 0
            version_summary = redact_text(proc.stdout or proc.stderr or f"exit={proc.returncode}", 200)
        except Exception as exc:
            version_summary = redact_text(str(exc), 200)
    return {
        "ok": bool(executable and version_ok),
        "adapter": "codex",
        "target_resource": "local://codex/read-only",
        "binary_path": str(binary),
        "binary_exists": exists,
        "binary_executable": executable,
        "windows_batch_shim_unsupported": bool(
            os.name == "nt" and exists and binary.suffix.lower() in {".bat", ".cmd"}
        ),
        "version_ok": version_ok,
        "version_summary": version_summary,
        "execution_contract": {
            "sandbox": "read-only",
            "ephemeral": True,
            "ignore_user_config": True,
            "strict_config": True,
            "git_repo_check": "skipped_for_packaged_read_only_runtime",
            "web_search": "disabled",
            "disabled_features": list(DISABLED_FEATURES),
            "prompt_transport": "stdin",
            "prohibited_tool_events_fail_run": True,
            "agentops_token_inherited": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        },
        "live_execution_performed": False,
        "token_omitted": True,
    }
