"""Run one private, scanner-bound systemd mutation without exposing a CLI."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

from agentops_mis_cli.relay_activation import (
    SYSTEMCTL_PATHS,
    UNIT_NAME,
    FileIdentity,
)
from agentops_mis_cli.relay_systemd_read import (
    _BoundSystemctl,
    _MINIMAL_ENVIRONMENT,
    _open_bound_systemctl,
    _terminate_child,
    _validate_bound_identity,
)


SYSTEMD_MUTATION_ERROR_ID = "systemd_mutation_failed"
SYSTEMD_MUTATION_TIMEOUT_SECONDS = 15.0
SYSTEMD_MUTATIONS = (
    "daemon_reload",
    "enable",
    "start",
    "stop",
    "disable",
)


class RelaySystemdMutationError(Exception):
    """One redacted failure for every private systemd mutation error."""

    def __init__(self) -> None:
        self.error_id = SYSTEMD_MUTATION_ERROR_ID
        super().__init__(SYSTEMD_MUTATION_ERROR_ID)


class _SystemdMutationInvalid(Exception):
    pass


def _validate_mutation(operation: str) -> None:
    if type(operation) is not str or operation not in SYSTEMD_MUTATIONS:
        raise _SystemdMutationInvalid


def _mutation_command(
    systemctl_path: str,
    operation: str,
) -> tuple[str, ...]:
    if systemctl_path not in SYSTEMCTL_PATHS:
        raise _SystemdMutationInvalid
    _validate_mutation(operation)
    if operation == "daemon_reload":
        return (
            systemctl_path,
            "--system",
            "daemon-reload",
        )
    return (
        systemctl_path,
        "--system",
        operation,
        UNIT_NAME,
    )


def _run_systemd_mutation_process(
    binding: _BoundSystemctl,
    operation: str,
    *,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    timeout_seconds: float = SYSTEMD_MUTATION_TIMEOUT_SECONDS,
    group_kill: Callable[[int, int], None] = os.killpg,
) -> None:
    _validate_mutation(operation)
    if (
        type(timeout_seconds) is not float
        or timeout_seconds <= 0
        or timeout_seconds > SYSTEMD_MUTATION_TIMEOUT_SECONDS
        or not sys.platform.startswith("linux")
        or not binding.executable_path.startswith("/proc/self/fd/")
    ):
        raise _SystemdMutationInvalid
    process: subprocess.Popen[bytes] | None = None
    completed_successfully = False
    try:
        process = popen_factory(
            _mutation_command(
                binding.identity.canonical_path,
                operation,
            ),
            executable=binding.executable_path,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=dict(_MINIMAL_ENVIRONMENT),
            close_fds=True,
            pass_fds=(binding.descriptor,),
            start_new_session=True,
        )
        return_code = process.wait(timeout=timeout_seconds)
        if return_code != 0 or process.poll() is None:
            raise _SystemdMutationInvalid
        completed_successfully = True
    finally:
        if process is not None:
            _terminate_child(
                process,
                failed=not completed_successfully,
                group_kill=group_kill,
            )


BindingFactory = Callable[[FileIdentity], _BoundSystemctl]
MutationRunner = Callable[[_BoundSystemctl, str], None]


def _run_systemd_mutation_with(
    identity: FileIdentity,
    operation: str,
    *,
    binding_factory: BindingFactory,
    process_runner: MutationRunner,
) -> None:
    binding: _BoundSystemctl | None = None
    try:
        _validate_bound_identity(identity)
        _validate_mutation(operation)
        binding = binding_factory(identity)
        if binding.identity != identity:
            raise _SystemdMutationInvalid
        binding.revalidate()
        process_runner(binding, operation)
        binding.revalidate()
        if binding.identity != identity:
            raise _SystemdMutationInvalid
    except RelaySystemdMutationError:
        raise
    except Exception:
        raise RelaySystemdMutationError() from None
    finally:
        if binding is not None:
            binding.close()


def _run_bound_systemd_mutation(
    identity: FileIdentity,
    operation: str,
) -> None:
    """Private production boundary reserved for the confirmed controller."""

    _run_systemd_mutation_with(
        identity,
        operation,
        binding_factory=_open_bound_systemctl,
        process_runner=_run_systemd_mutation_process,
    )
