"""Read one bounded systemd snapshot through a scanner-bound executable."""
from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

from agentops_mis_cli.relay_activation import (
    MAX_SYSTEMD_SHOW_BYTES,
    SYSTEMCTL_PATHS,
    SYSTEMD_PROPERTIES,
    UNIT_NAME,
    ActivationPrerequisiteSnapshot,
    FileIdentity,
    RelayActivationError,
    SystemdSnapshot,
    parse_systemd_show_bytes,
)


SYSTEMD_SHOW_ERROR_ID = "systemd_show_failed"
SYSTEMD_SHOW_TIMEOUT_SECONDS = 5.0
MAX_SYSTEMCTL_EXECUTABLE_BYTES = 16 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SHOW_PROPERTY_ARGUMENT = "--property=" + ",".join(SYSTEMD_PROPERTIES)
_MINIMAL_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}


class RelaySystemdShowError(Exception):
    """One redacted failure for all production systemd read errors."""

    def __init__(self) -> None:
        self.error_id = SYSTEMD_SHOW_ERROR_ID
        super().__init__(SYSTEMD_SHOW_ERROR_ID)


class _SystemdReadInvalid(Exception):
    pass


def _metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
    )


def _identity_fingerprint(identity: FileIdentity) -> tuple[int, ...]:
    return (
        identity.device_id,
        identity.inode,
        identity.size,
        stat.S_IFREG,
        identity.mode,
        identity.owner_id,
        identity.group_id,
        identity.nlink,
    )


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
    )


def _validate_bound_identity(identity: FileIdentity) -> None:
    if (
        not isinstance(identity, FileIdentity)
        or identity.kind != "regular"
        or identity.canonical_path not in SYSTEMCTL_PATHS
        or type(identity.device_id) is not int
        or identity.device_id < 0
        or type(identity.inode) is not int
        or identity.inode < 0
        or type(identity.owner_id) is not int
        or identity.owner_id != 0
        or type(identity.group_id) is not int
        or identity.group_id != 0
        or type(identity.mode) is not int
        or identity.mode < 0
        or identity.mode > 0o7777
        or identity.mode & 0o022
        or not identity.mode & 0o111
        or type(identity.nlink) is not int
        or identity.nlink != 1
        or type(identity.size) is not int
        or identity.size <= 0
        or identity.size > MAX_SYSTEMCTL_EXECUTABLE_BYTES
        or not isinstance(identity.content_sha256, str)
        or not _SHA256_PATTERN.fullmatch(identity.content_sha256)
    ):
        raise _SystemdReadInvalid


def _validate_metadata(
    metadata: os.stat_result,
    identity: FileIdentity,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _metadata_identity(metadata) != _identity_fingerprint(identity)
    ):
        raise _SystemdReadInvalid


def _hash_descriptor(descriptor: int, expected_size: int) -> str:
    if expected_size <= 0 or expected_size > MAX_SYSTEMCTL_EXECUTABLE_BYTES:
        raise _SystemdReadInvalid
    digest = hashlib.sha256()
    total = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            remaining = MAX_SYSTEMCTL_EXECUTABLE_BYTES - total
            chunk = os.read(descriptor, min(64 * 1024, remaining + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SYSTEMCTL_EXECUTABLE_BYTES:
                raise _SystemdReadInvalid
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        raise _SystemdReadInvalid from None
    if total != expected_size:
        raise _SystemdReadInvalid
    return digest.hexdigest()


@dataclass
class _BoundSystemctl:
    descriptor: int
    identity: FileIdentity
    opened_fingerprint: tuple[int, ...]
    closed: bool = False

    @property
    def executable_path(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"

    def revalidate(self) -> None:
        if self.closed:
            raise _SystemdReadInvalid
        try:
            held = os.fstat(self.descriptor)
            current = os.lstat(self.identity.canonical_path)
        except OSError:
            raise _SystemdReadInvalid from None
        if (
            _metadata_fingerprint(held) != self.opened_fingerprint
            or _metadata_fingerprint(current) != self.opened_fingerprint
        ):
            raise _SystemdReadInvalid
        _validate_metadata(held, self.identity)
        _validate_metadata(current, self.identity)
        if (
            _hash_descriptor(self.descriptor, self.identity.size)
            != self.identity.content_sha256
        ):
            raise _SystemdReadInvalid
        try:
            held_after = os.fstat(self.descriptor)
            current_after = os.lstat(self.identity.canonical_path)
        except OSError:
            raise _SystemdReadInvalid from None
        if (
            _metadata_fingerprint(held_after) != self.opened_fingerprint
            or _metadata_fingerprint(current_after) != self.opened_fingerprint
        ):
            raise _SystemdReadInvalid

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            os.close(self.descriptor)
        except OSError:
            pass


def _open_bound_systemctl(identity: FileIdentity) -> _BoundSystemctl:
    _validate_bound_identity(identity)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not cloexec:
        raise _SystemdReadInvalid
    descriptor = -1
    try:
        before = os.lstat(identity.canonical_path)
        _validate_metadata(before, identity)
        descriptor = os.open(
            identity.canonical_path,
            os.O_RDONLY | nofollow | cloexec,
        )
        opened = os.fstat(descriptor)
        after = os.lstat(identity.canonical_path)
        if not (
            _metadata_fingerprint(before)
            == _metadata_fingerprint(opened)
            == _metadata_fingerprint(after)
        ):
            raise _SystemdReadInvalid
        _validate_metadata(opened, identity)
        if _hash_descriptor(descriptor, identity.size) != identity.content_sha256:
            raise _SystemdReadInvalid
        opened_after_hash = os.fstat(descriptor)
        path_after_hash = os.lstat(identity.canonical_path)
        opened_fingerprint = _metadata_fingerprint(opened)
        if (
            _metadata_fingerprint(opened_after_hash) != opened_fingerprint
            or _metadata_fingerprint(path_after_hash) != opened_fingerprint
        ):
            raise _SystemdReadInvalid
        return _BoundSystemctl(
            descriptor=descriptor,
            identity=identity,
            opened_fingerprint=opened_fingerprint,
        )
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _show_command(systemctl_path: str) -> tuple[str, ...]:
    if systemctl_path not in SYSTEMCTL_PATHS:
        raise _SystemdReadInvalid
    return (
        systemctl_path,
        "--system",
        "show",
        UNIT_NAME,
        "--no-pager",
        _SHOW_PROPERTY_ARGUMENT,
    )


def _terminate_child(
    process: subprocess.Popen[bytes],
    *,
    failed: bool,
    group_kill: Callable[[int, int], None] = os.killpg,
) -> None:
    if type(failed) is not bool:
        raise _SystemdReadInvalid
    if not failed:
        try:
            return_code = process.wait(timeout=0.25)
        except Exception:
            raise _SystemdReadInvalid from None
        if return_code != 0 or process.poll() is None:
            raise _SystemdReadInvalid
        return

    try:
        group_exists = True
        try:
            group_kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            group_exists = False
        except Exception:
            raise _SystemdReadInvalid from None

        leader_reaped = False
        try:
            process.wait(timeout=0.25)
            leader_reaped = True
        except subprocess.TimeoutExpired:
            pass

        if group_exists:
            try:
                group_kill(process.pid, 0)
            except ProcessLookupError:
                group_exists = False
            except Exception:
                raise _SystemdReadInvalid from None

        if group_exists:
            try:
                group_kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                group_exists = False
            except Exception:
                raise _SystemdReadInvalid from None

            try:
                process.wait(timeout=0.25)
                leader_reaped = True
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.25)
                leader_reaped = True

            deadline = time.monotonic() + 0.25
            while group_exists and time.monotonic() < deadline:
                try:
                    group_kill(process.pid, 0)
                except ProcessLookupError:
                    group_exists = False
                    break
                except Exception:
                    raise _SystemdReadInvalid from None
                time.sleep(0.005)
        if group_exists:
            raise _SystemdReadInvalid
        if not leader_reaped:
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.25)
        else:
            process.wait(timeout=0.25)
        if process.poll() is None:
            raise _SystemdReadInvalid
    except Exception:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=0.25)
        except Exception:
            raise _SystemdReadInvalid from None
        raise


def _read_process_stdout(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> bytes:
    if (
        type(timeout_seconds) is not float
        or timeout_seconds <= 0
        or timeout_seconds > SYSTEMD_SHOW_TIMEOUT_SECONDS
        or process.stdout is None
    ):
        raise _SystemdReadInvalid
    try:
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
    except (AttributeError, OSError, ValueError):
        raise _SystemdReadInvalid from None

    output = bytearray()
    deadline = monotonic() + timeout_seconds
    eof = False
    while True:
        if monotonic() >= deadline:
            raise _SystemdReadInvalid
        if not eof:
            try:
                chunk = os.read(
                    descriptor,
                    min(4096, MAX_SYSTEMD_SHOW_BYTES - len(output) + 1),
                )
            except BlockingIOError:
                chunk = None
            except OSError:
                raise _SystemdReadInvalid from None
            if chunk == b"":
                eof = True
            elif chunk:
                output.extend(chunk)
                if len(output) > MAX_SYSTEMD_SHOW_BYTES:
                    raise _SystemdReadInvalid
        return_code = process.poll()
        if eof and return_code is not None:
            if return_code != 0:
                raise _SystemdReadInvalid
            return bytes(output)
        time.sleep(0.005)


def _run_systemd_show_process(
    binding: _BoundSystemctl,
    *,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    timeout_seconds: float = SYSTEMD_SHOW_TIMEOUT_SECONDS,
    group_kill: Callable[[int, int], None] = os.killpg,
) -> bytes:
    if (
        not sys.platform.startswith("linux")
        or not binding.executable_path.startswith("/proc/self/fd/")
    ):
        raise _SystemdReadInvalid
    process: subprocess.Popen[bytes] | None = None
    completed_successfully = False
    try:
        process = popen_factory(
            _show_command(binding.identity.canonical_path),
            executable=binding.executable_path,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=dict(_MINIMAL_ENVIRONMENT),
            close_fds=True,
            pass_fds=(binding.descriptor,),
            start_new_session=True,
        )
        output = _read_process_stdout(
            process,
            timeout_seconds=timeout_seconds,
        )
        completed_successfully = True
        return output
    finally:
        if process is not None:
            try:
                _terminate_child(
                    process,
                    failed=not completed_successfully,
                    group_kill=group_kill,
                )
            finally:
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except Exception:
                        pass


BindingFactory = Callable[[FileIdentity], _BoundSystemctl]
ProcessRunner = Callable[[_BoundSystemctl], bytes]


def _read_systemd_show_with(
    identity: FileIdentity,
    *,
    binding_factory: BindingFactory,
    process_runner: ProcessRunner,
) -> SystemdSnapshot:
    binding: _BoundSystemctl | None = None
    try:
        _validate_bound_identity(identity)
        binding = binding_factory(identity)
        binding.revalidate()
        raw = process_runner(binding)
        if not isinstance(raw, bytes) or len(raw) > MAX_SYSTEMD_SHOW_BYTES:
            raise _SystemdReadInvalid
        snapshot = parse_systemd_show_bytes(raw)
        binding.revalidate()
        return snapshot
    except RelaySystemdShowError:
        raise
    except Exception:
        raise RelaySystemdShowError() from None
    finally:
        if binding is not None:
            binding.close()


def read_systemd_show(
    prerequisites: ActivationPrerequisiteSnapshot,
) -> SystemdSnapshot:
    """Read the exact scanner-bound production unit state without overrides."""

    if not isinstance(prerequisites, ActivationPrerequisiteSnapshot):
        raise RelaySystemdShowError()
    return _read_systemd_show_with(
        prerequisites.systemctl,
        binding_factory=_open_bound_systemctl,
        process_runner=_run_systemd_show_process,
    )
