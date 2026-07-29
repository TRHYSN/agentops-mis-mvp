#!/usr/bin/env python3
"""Verify the optional local-stack runtime lock without touching real Host data."""
from __future__ import annotations

import contextlib
import ast
import io
import json
import os
import select
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import run_local_stack as stack
from agentops_mis_cli import runtime_lock


ROOT = Path(__file__).resolve().parents[1]
HOLD_LOCK = r"""
import sys
import time
from pathlib import Path
from scripts.run_local_stack import acquire_runtime_lock

descriptor = acquire_runtime_lock(Path(sys.argv[1]))
print("LOCKED", flush=True)
time.sleep(60)
"""
TRY_LOCK = r"""
import json
import sys
from pathlib import Path
from scripts.run_local_stack import acquire_runtime_lock, release_runtime_lock

try:
    descriptor = acquire_runtime_lock(Path(sys.argv[1]))
except RuntimeError:
    print(json.dumps({"acquired": False, "error": "runtime_lock_unavailable"}))
    raise SystemExit(3)
release_runtime_lock(descriptor)
print(json.dumps({"acquired": True, "released": True}))
"""
INHERITED_LOCK_CHILD = r"""
import os
import sys
import time

os.fstat(int(sys.argv[1]))
print("CHILD_LOCK_READY", flush=True)
time.sleep(2.0)
"""
CRASHING_LOCK_PARENT = r"""
import json
import os
import subprocess
import sys
from pathlib import Path
from scripts.run_local_stack import acquire_runtime_lock

descriptor = acquire_runtime_lock(Path(sys.argv[1]))
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[2], str(descriptor)],
    pass_fds=(descriptor,),
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
)
ready = child.stdout.readline().strip() if child.stdout is not None else ""
print(json.dumps({"child_pid": child.pid, "ready": ready == "CHILD_LOCK_READY"}), flush=True)
os._exit(0)
"""


def private_directory(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def child_environment() -> dict[str, str]:
    return {
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
    }


def try_lock_in_child(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", TRY_LOCK, str(path)],
        cwd=ROOT,
        env=child_environment(),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )


def invoke_stack(arguments: list[str]) -> tuple[object, str, mock.Mock, mock.Mock]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    popen = mock.Mock(side_effect=AssertionError("subprocess started"))
    run = mock.Mock(side_effect=AssertionError("subprocess started"))
    result: object = None
    with (
        mock.patch.object(stack.sys, "argv", ["run_local_stack.py", *arguments]),
        mock.patch.object(stack.signal, "signal"),
        mock.patch.object(stack, "port_open", return_value=True),
        mock.patch.object(stack, "gateway_ready", return_value=True),
        mock.patch.object(stack.subprocess, "Popen", popen),
        mock.patch.object(stack.subprocess, "run", run),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        try:
            result = stack.main()
        except Exception as exc:  # The caller checks the bounded public error.
            result = exc
    return result, stdout.getvalue() + stderr.getvalue(), popen, run


def main() -> int:
    failures: list[str] = []
    check_count = 0

    def require(condition: bool, label: str) -> None:
        nonlocal check_count
        check_count += 1
        if not condition:
            failures.append(label)

    def expect_rejected(path: Path, label: str) -> None:
        descriptor: int | None = None
        try:
            descriptor = stack.acquire_runtime_lock(path)
        except RuntimeError:
            require(True, label)
        else:
            require(False, label)
        finally:
            stack.release_runtime_lock(descriptor)

    holder: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="agentops-runtime-lock-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)

        lifecycle_parent = private_directory(root / "lifecycle")
        lifecycle_lock = lifecycle_parent / "stack.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_LOCK, str(lifecycle_lock)],
            cwd=ROOT,
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready, _write, _errors = select.select([holder.stdout], [], [], 6)
            marker = holder.stdout.readline().strip() if ready and holder.stdout is not None else ""
            require(marker == "LOCKED" and holder.poll() is None, "holder process did not acquire the runtime lock")

            locked_attempt = try_lock_in_child(lifecycle_lock)
            require(locked_attempt.returncode == 3, "a second process acquired an already-held runtime lock")
            require("runtime_lock_unavailable" in locked_attempt.stdout, "lock contention did not fail with bounded metadata")

            metadata = lifecycle_lock.lstat()
            require(stat.S_ISREG(metadata.st_mode), "runtime lock is not a regular file")
            require(stat.S_IMODE(metadata.st_mode) == 0o600, "runtime lock mode is not 0600")
            require(metadata.st_uid == os.getuid(), "runtime lock owner is not the current UID")
            require(metadata.st_nlink == 1, "runtime lock has more than one link")
        finally:
            if holder.poll() is None:
                holder.terminate()
            try:
                holder.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate(timeout=3)

        released_attempt = try_lock_in_child(lifecycle_lock)
        require(released_attempt.returncode == 0, "runtime lock was not released when its owner exited")
        require('"released": true' in released_attempt.stdout, "released runtime lock could not be reacquired")

        direct_descriptor = stack.acquire_runtime_lock(lifecycle_lock)
        stack.release_runtime_lock(direct_descriptor)
        require(try_lock_in_child(lifecycle_lock).returncode == 0, "explicit release did not make the lock reusable")

        inherited_parent = private_directory(root / "inherited-lifecycle")
        inherited_lock = inherited_parent / "stack.lock"
        crashing_parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CRASHING_LOCK_PARENT,
                str(inherited_lock),
                INHERITED_LOCK_CHILD,
            ],
            cwd=ROOT,
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        inherited_line = crashing_parent.stdout.readline() if crashing_parent.stdout is not None else ""
        crashing_parent.wait(timeout=6)
        try:
            inherited_receipt = json.loads(inherited_line)
        except (TypeError, ValueError):
            inherited_receipt = {}
        inherited_attempt = try_lock_in_child(inherited_lock)
        require(
            crashing_parent.returncode == 0 and inherited_receipt.get("ready") is True,
            "crashing parent did not leave a verified lock-holding child",
        )
        require(
            inherited_attempt.returncode == 3,
            "runtime lock was released while a child survived its parent",
        )
        reacquired_after_child = inherited_attempt
        child_deadline = time.monotonic() + 6.0
        while time.monotonic() < child_deadline:
            reacquired_after_child = try_lock_in_child(inherited_lock)
            if reacquired_after_child.returncode == 0:
                break
            time.sleep(0.05)
        require(
            reacquired_after_child.returncode == 0,
            "runtime lock was not released after the last inheriting child exited",
        )

        stack_tree = ast.parse(Path(stack.__file__).read_text(encoding="utf-8"))
        popen_calls = [
            node
            for node in ast.walk(stack_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "Popen"
        ]
        pass_fds_values = [
            next((keyword.value for keyword in node.keywords if keyword.arg == "pass_fds"), None)
            for node in popen_calls
        ]
        require(
            len(popen_calls) == 4
            and all(
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "inherited_runtime_fds"
                for value in pass_fds_values
            ),
            "a long-lived stack child does not inherit the runtime lock descriptor",
        )
        require(
            stack.inherited_runtime_fds(None, 1, 2, True, -1) == ()
            and stack.inherited_runtime_fds(7, 9) == (7, 9),
            "runtime descriptor projection accepted an unsafe descriptor",
        )

        mode_parent = private_directory(root / "bad-parent-mode")
        mode_parent.chmod(0o755)
        expect_rejected(mode_parent / "stack.lock", "runtime lock accepted a non-0700 parent")

        real_parent = private_directory(root / "real-parent")
        parent_link = root / "parent-link"
        parent_link.symlink_to(real_parent, target_is_directory=True)
        expect_rejected(parent_link / "stack.lock", "runtime lock followed a symlinked parent")

        symlink_parent = private_directory(root / "symlink-file")
        symlink_target = symlink_parent / "target"
        symlink_target.write_text("fixture", encoding="utf-8")
        symlink_target.chmod(0o600)
        (symlink_parent / "stack.lock").symlink_to(symlink_target)
        expect_rejected(symlink_parent / "stack.lock", "runtime lock followed a symlinked file")

        file_mode_parent = private_directory(root / "bad-file-mode")
        file_mode_lock = file_mode_parent / "stack.lock"
        file_mode_lock.touch(mode=0o600)
        file_mode_lock.chmod(0o640)
        expect_rejected(file_mode_lock, "runtime lock accepted a non-0600 file")

        hardlink_parent = private_directory(root / "hardlink")
        hardlink_lock = hardlink_parent / "stack.lock"
        hardlink_lock.touch(mode=0o600)
        hardlink_lock.chmod(0o600)
        os.link(hardlink_lock, hardlink_parent / "second-link")
        expect_rejected(hardlink_lock, "runtime lock accepted a multi-link file")

        directory_parent = private_directory(root / "directory-file")
        private_directory(directory_parent / "stack.lock")
        expect_rejected(directory_parent / "stack.lock", "runtime lock accepted a directory as its lock file")

        owner_parent = private_directory(root / "owner-check")
        owner_lock = owner_parent / "stack.lock"
        owner_lock.touch(mode=0o600)
        owner_lock.chmod(0o600)
        real_uid = os.getuid()
        owner_parent_metadata = owner_parent.lstat()
        owner_lock_metadata = owner_lock.lstat()
        with mock.patch.object(runtime_lock.os, "getuid", return_value=real_uid + 1):
            require(not runtime_lock.private_runtime_parent(owner_parent_metadata), "parent owner mismatch was accepted")
            require(not runtime_lock.private_runtime_lock(owner_lock_metadata), "file owner mismatch was accepted")
            expect_rejected(owner_lock, "runtime lock accepted a different owner")

        content_parent = private_directory(root / "content-check")
        content_lock = content_parent / "stack.lock"
        secret_canary = "CREDENTIAL_CANARY_VALUE"
        content_lock.write_text(secret_canary, encoding="utf-8")
        content_lock.chmod(0o600)
        content_descriptor = stack.acquire_runtime_lock(content_lock)
        try:
            held_result, held_output, held_popen, held_run = invoke_stack(
                ["--runtime-lock", str(content_lock), "--no-ui", "--no-workers"]
            )
            require(isinstance(held_result, RuntimeError), "CLI did not reject an already-held runtime lock")
            require(not held_popen.called and not held_run.called, "CLI started a child before rejecting lock contention")
            public_failure = held_output + str(held_result)
            require(secret_canary not in public_failure, "runtime lock failure leaked lock-file contents")
            require(
                not any(marker in public_failure for marker in ("Bearer ", "sk-", "ntn_", "agtok_", "agtsess_")),
                "runtime lock failure leaked credential-like output",
            )
            require(content_lock.read_text(encoding="utf-8") == secret_canary, "runtime locking modified existing file contents")
        finally:
            stack.release_runtime_lock(content_descriptor)

        unsafe_result, _unsafe_output, unsafe_popen, unsafe_run = invoke_stack(
            ["--runtime-lock", str(mode_parent / "stack.lock"), "--no-ui", "--no-workers"]
        )
        require(isinstance(unsafe_result, RuntimeError), "CLI accepted an unsafe runtime-lock parent")
        require(not unsafe_popen.called and not unsafe_run.called, "CLI started a child before rejecting an unsafe parent")

        unsafe_file_result, _unsafe_file_output, unsafe_file_popen, unsafe_file_run = invoke_stack(
            ["--runtime-lock", str(file_mode_lock), "--no-ui", "--no-workers"]
        )
        require(isinstance(unsafe_file_result, RuntimeError), "CLI accepted an unsafe runtime-lock file")
        require(
            not unsafe_file_popen.called and not unsafe_file_run.called,
            "CLI started a child before rejecting an unsafe lock file",
        )

        integrated_parent = private_directory(root / "integrated")
        integrated_lock = integrated_parent / "stack.lock"
        integrated_result, integrated_output, integrated_popen, integrated_run = invoke_stack(
            ["--runtime-lock", str(integrated_lock), "--no-ui", "--no-workers"]
        )
        require(integrated_result == 0, "CLI runtime-lock path did not preserve a successful local-stack invocation")
        require(not integrated_popen.called and not integrated_run.called, "bounded integration fixture unexpectedly started a child")
        require(try_lock_in_child(integrated_lock).returncode == 0, "CLI did not release its runtime lock during final cleanup")
        require("AgentOps MIS local stack is running" in integrated_output, "runtime-lock mode changed normal status output")

        legacy_result, legacy_output, legacy_popen, legacy_run = invoke_stack(["--no-ui", "--no-workers"])
        require(legacy_result == 0, "omitting --runtime-lock changed existing development behavior")
        require(not legacy_popen.called and not legacy_run.called, "no-lock compatibility fixture unexpectedly started a child")
        require("AgentOps MIS local stack is running" in legacy_output, "no-lock mode lost existing status output")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_runtime_lock_smoke",
                "checks": check_count,
                "exclusive": not any("second process" in item for item in failures),
                "process_lifetime_held": not any("holder process" in item for item in failures),
                "process_tree_lifetime_held": not any(
                    "survived its parent" in item
                    or "last inheriting child" in item
                    or "does not inherit" in item
                    for item in failures
                ),
                "released_and_reacquired": not any("reacquir" in item or "release" in item for item in failures),
                "unsafe_paths_fail_closed": not any("accepted" in item for item in failures),
                "child_process_started_on_rejection": False,
                "real_host_data_read": False,
                "credential_content_omitted": not any("leaked" in item for item in failures),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
