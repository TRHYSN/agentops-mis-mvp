#!/usr/bin/env python3
"""Exercise the private systemd mutation adapter without mutating systemd."""
from __future__ import annotations

import ast
import inspect
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    UNIT_NAME,
    FileIdentity,
)
from agentops_mis_cli import relay_systemd_mutation as mutation  # noqa: E402
from agentops_mis_cli.relay_systemd_mutation import (  # noqa: E402
    RelaySystemdMutationError,
    _mutation_command,
    _run_systemd_mutation_process,
    _run_systemd_mutation_with,
)


PRIVATE_CANARY = "MUTATION_PRIVATE_CANARY"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def identity() -> FileIdentity:
    return FileIdentity(
        kind="regular",
        canonical_path="/usr/bin/systemctl",
        device_id=1,
        inode=2,
        owner_id=0,
        group_id=0,
        mode=0o755,
        nlink=1,
        size=4096,
        content_sha256="a" * 64,
    )


class FakeBinding:
    def __init__(self, value: FileIdentity) -> None:
        self.identity = value
        self.descriptor = 91
        self.executable_path = "/proc/self/fd/91"
        self.revalidations = 0
        self.closed = False
        self.fail_on_revalidation = 0

    def revalidate(self) -> None:
        self.revalidations += 1
        if self.revalidations == self.fail_on_revalidation:
            raise RuntimeError(f"{PRIVATE_CANARY}:binding-replaced")

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        *,
        return_code: int = 0,
        timeout: bool = False,
    ) -> None:
        self.pid = 4242
        self.return_code = return_code
        self.timeout = timeout
        self.killed = False
        self.wait_timeouts: list[float] = []

    def wait(self, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(
                f"{PRIVATE_CANARY}:systemctl",
                timeout,
            )
        return self.return_code

    def poll(self) -> int | None:
        if self.timeout and not self.killed:
            return None
        return self.return_code

    def kill(self) -> None:
        self.killed = True
        self.timeout = False
        self.return_code = -signal.SIGKILL


def run_error(
    operation: object,
    *,
    binding_factory: Any,
    process_runner: Any,
) -> str:
    try:
        _run_systemd_mutation_with(
            identity(),
            operation,  # type: ignore[arg-type]
            binding_factory=binding_factory,
            process_runner=process_runner,
        )
    except RelaySystemdMutationError as exc:
        return str(exc)
    return ""


def main() -> int:
    failures: list[str] = []
    expected_commands = {
        "daemon_reload": (
            "/usr/bin/systemctl",
            "--system",
            "daemon-reload",
        ),
        "enable": (
            "/usr/bin/systemctl",
            "--system",
            "enable",
            UNIT_NAME,
        ),
        "start": (
            "/usr/bin/systemctl",
            "--system",
            "start",
            UNIT_NAME,
        ),
        "stop": (
            "/usr/bin/systemctl",
            "--system",
            "stop",
            UNIT_NAME,
        ),
        "disable": (
            "/usr/bin/systemctl",
            "--system",
            "disable",
            UNIT_NAME,
        ),
    }
    require(
        {
            name: _mutation_command("/usr/bin/systemctl", name)
            for name in expected_commands
        }
        == expected_commands,
        "mutation command allowlist changed",
        failures,
    )

    original_platform = mutation.sys.platform
    exact_invocations = 0
    try:
        mutation.sys.platform = "linux"
        for operation, expected_command in expected_commands.items():
            binding = FakeBinding(identity())
            process = FakeProcess()
            observed: dict[str, object] = {}

            def popen_factory(
                command: tuple[str, ...],
                **kwargs: object,
            ) -> FakeProcess:
                observed["command"] = command
                observed["kwargs"] = kwargs
                return process

            _run_systemd_mutation_with(
                identity(),
                operation,
                binding_factory=lambda _value, item=binding: item,
                process_runner=lambda item, name, factory=popen_factory: (
                    _run_systemd_mutation_process(
                        item,
                        name,
                        popen_factory=factory,
                    )
                ),
            )
            kwargs = observed.get("kwargs")
            exact = (
                observed.get("command") == expected_command
                and isinstance(kwargs, dict)
                and kwargs
                == {
                    "executable": "/proc/self/fd/91",
                    "shell": False,
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "cwd": "/",
                    "env": {
                        "LANG": "C",
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin",
                    },
                    "close_fds": True,
                    "pass_fds": (91,),
                    "start_new_session": True,
                }
                and process.wait_timeouts
                == [mutation.SYSTEMD_MUTATION_TIMEOUT_SECONDS, 0.25]
                and binding.revalidations == 2
                and binding.closed
            )
            if exact:
                exact_invocations += 1
    finally:
        mutation.sys.platform = original_platform

    binding_calls = 0

    def forbidden_binding(_value: FileIdentity) -> FakeBinding:
        nonlocal binding_calls
        binding_calls += 1
        raise AssertionError("invalid operation reached binding")

    invalid_operations = (
        "",
        "restart",
        "enable --now",
        "daemon-reload",
        None,
        True,
    )
    invalid_rejected = all(
        run_error(
            operation,
            binding_factory=forbidden_binding,
            process_runner=lambda _binding, _operation: None,
        )
        == mutation.SYSTEMD_MUTATION_ERROR_ID
        for operation in invalid_operations
    )
    require(
        invalid_rejected and binding_calls == 0,
        "invalid mutation reached the executable binding",
        failures,
    )

    substituted_identity = identity()
    object.__setattr__(
        substituted_identity,
        "canonical_path",
        "/bin/systemctl",
    )
    substituted_binding = FakeBinding(substituted_identity)
    substitution_error = run_error(
        "enable",
        binding_factory=lambda _value: substituted_binding,
        process_runner=lambda _binding, _operation: None,
    )
    require(
        substitution_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        and substituted_binding.revalidations == 0
        and substituted_binding.closed,
        "binding factory substituted a different systemctl identity",
        failures,
    )

    replacement_binding = FakeBinding(identity())
    replacement_binding.fail_on_revalidation = 2
    replacement_error = run_error(
        "enable",
        binding_factory=lambda _value: replacement_binding,
        process_runner=lambda _binding, _operation: None,
    )
    require(
        replacement_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        and PRIVATE_CANARY not in replacement_error
        and replacement_binding.closed,
        "post-mutation binding replacement was not redacted",
        failures,
    )

    timeout_binding = FakeBinding(identity())
    timeout_process = FakeProcess(timeout=True)
    signals: list[int] = []

    def group_kill(_pid: int, sent_signal: int) -> None:
        signals.append(sent_signal)
        if sent_signal == signal.SIGTERM:
            timeout_process.timeout = False
            timeout_process.return_code = -signal.SIGTERM
            return
        if sent_signal == 0:
            raise ProcessLookupError

    original_platform = mutation.sys.platform
    try:
        mutation.sys.platform = "linux"
        timeout_error = run_error(
            "start",
            binding_factory=lambda _value: timeout_binding,
            process_runner=lambda item, name: (
                _run_systemd_mutation_process(
                    item,
                    name,
                    popen_factory=lambda *_args, **_kwargs: timeout_process,
                    timeout_seconds=0.01,
                    group_kill=group_kill,
                )
            ),
        )
    finally:
        mutation.sys.platform = original_platform
    require(
        timeout_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        and PRIVATE_CANARY not in timeout_error
        and signals == [signal.SIGTERM, 0]
        and timeout_binding.closed,
        "timed-out mutation was not terminated and redacted",
        failures,
    )

    nonzero_binding = FakeBinding(identity())
    nonzero_process = FakeProcess(return_code=7)
    nonzero_signals: list[int] = []

    def exited_group_kill(_pid: int, sent_signal: int) -> None:
        nonzero_signals.append(sent_signal)
        raise ProcessLookupError

    original_platform = mutation.sys.platform
    try:
        mutation.sys.platform = "linux"
        nonzero_error = run_error(
            "disable",
            binding_factory=lambda _value: nonzero_binding,
            process_runner=lambda item, name: (
                _run_systemd_mutation_process(
                    item,
                    name,
                    popen_factory=lambda *_args, **_kwargs: nonzero_process,
                    group_kill=exited_group_kill,
                )
            ),
        )
    finally:
        mutation.sys.platform = original_platform
    require(
        nonzero_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        and PRIVATE_CANARY not in nonzero_error
        and nonzero_signals == [signal.SIGTERM]
        and nonzero_binding.closed,
        "nonzero mutation exit was not reaped and redacted",
        failures,
    )

    non_linux_binding = FakeBinding(identity())
    popen_calls = 0

    def forbidden_popen(*_args: object, **_kwargs: object) -> FakeProcess:
        nonlocal popen_calls
        popen_calls += 1
        raise AssertionError("non-Linux mutation reached subprocess")

    original_platform = mutation.sys.platform
    try:
        mutation.sys.platform = "darwin"
        non_linux_error = run_error(
            "stop",
            binding_factory=lambda _value: non_linux_binding,
            process_runner=lambda item, name: _run_systemd_mutation_process(
                item,
                name,
                popen_factory=forbidden_popen,
            ),
        )
    finally:
        mutation.sys.platform = original_platform
    require(
        non_linux_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        and popen_calls == 0
        and non_linux_binding.closed,
        "non-Linux mutation reached subprocess",
        failures,
    )

    admin_source = (
        ROOT / "agentops_mis_cli" / "relay_admin.py"
    ).read_text(encoding="utf-8")
    admin_tree = ast.parse(admin_source)
    mutation_imported = any(
        (
            isinstance(node, ast.Import)
            and any(
                alias.name
                == "agentops_mis_cli.relay_systemd_mutation"
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module
            == "agentops_mis_cli.relay_systemd_mutation"
        )
        for node in ast.walk(admin_tree)
    )
    mutation_called = any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "_run_bound_systemd_mutation"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_run_bound_systemd_mutation"
            )
        )
        for node in ast.walk(admin_tree)
    )
    private_surface = (
        not mutation_imported
        and not mutation_called
        and all(
            name.startswith("_")
            for name, value in vars(mutation).items()
            if inspect.isfunction(value)
            and value.__module__ == mutation.__name__
        )
    )
    require(
        private_surface,
        "mutation adapter is exposed through a production command surface",
        failures,
    )

    result = {
        "cli_mutation_exposed": False,
        "exact_mutation_commands": exact_invocations,
        "failures": failures,
        "invalid_operations_rejected": invalid_rejected,
        "network_used": False,
        "nonzero_exit_redacted": (
            nonzero_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        ),
        "non_linux_subprocess_blocked": popen_calls == 0,
        "ok": not failures,
        "operation": "relay_systemd_mutation_smoke",
        "post_mutation_binding_revalidated": (
            replacement_binding.revalidations == 2
        ),
        "private_canary_omitted": (
            PRIVATE_CANARY not in replacement_error
            and PRIVATE_CANARY not in timeout_error
        ),
        "substituted_binding_rejected": (
            substitution_error == mutation.SYSTEMD_MUTATION_ERROR_ID
        ),
        "timeout_process_group_terminated": (
            signals == [signal.SIGTERM, 0]
        ),
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
