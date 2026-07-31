#!/usr/bin/env python3
"""Exercise the read-only systemd adapter and activation preview on any OS."""
from __future__ import annotations

import builtins
import contextlib
import hashlib
import inspect
import io
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_activation_preview as preview_module  # noqa: E402
from agentops_mis_cli import relay_systemd_read as systemd_module  # noqa: E402
from agentops_mis_cli.relay_activation import (  # noqa: E402
    CONFIG_PATH,
    ENABLEMENT_LINK_PATH,
    MAX_SYSTEMD_SHOW_BYTES,
    RUNTIME_DIRECTORY,
    STATE_DIRECTORY,
    SYSTEMD_PROPERTIES,
    UNIT_NAME,
    UNIT_PATH,
    ActivationPrerequisiteSnapshot,
    DirectoryIdentity,
    FileIdentity,
    LinkIdentity,
    RootIdentity,
    SystemdSnapshot,
    parse_systemd_show_bytes,
)
from agentops_mis_cli.relay_activation_preview import (  # noqa: E402
    ACTIVATION_PREREQUISITE_CHANGED,
    RelayActivationPreviewError,
    _preview_activation_with,
)
from agentops_mis_cli.relay_admin import main as relay_admin_main  # noqa: E402
from agentops_mis_cli.relay_systemd_read import (  # noqa: E402
    SYSTEMD_SHOW_ERROR_ID,
    RelaySystemdShowError,
    _read_systemd_show_with,
    _run_systemd_show_process,
)


PRIVATE_CANARY = "relay-preview-private-canary"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def descriptor_is_closed(descriptor: int) -> bool:
    try:
        os.fstat(descriptor)
    except OSError:
        return True
    return False


def identity(
    path: str,
    digest: str,
    *,
    inode: int,
    owner: int = 0,
    group: int = 0,
    mode: int = 0o644,
    size: int = 128,
) -> FileIdentity:
    return FileIdentity(
        kind="regular",
        canonical_path=path,
        device_id=7,
        inode=inode,
        owner_id=owner,
        group_id=group,
        mode=mode,
        nlink=1,
        size=size,
        content_sha256=digest,
    )


def enablement_link() -> LinkIdentity:
    return LinkIdentity(
        kind="symlink",
        canonical_path=ENABLEMENT_LINK_PATH,
        target=UNIT_PATH,
        device_id=7,
        inode=16,
        owner_id=0,
        group_id=0,
        nlink=1,
    )


def prerequisites() -> ActivationPrerequisiteSnapshot:
    service_uid = 1701
    service_gid = 1701
    return ActivationPrerequisiteSnapshot(
        root=RootIdentity(
            kind="directory",
            canonical_path="/",
            device_id=1,
            inode=2,
            owner_id=0,
            group_id=0,
            mode=0o755,
        ),
        release_id="0.1.0-" + ("1" * 12),
        version_id="0.1.0",
        release_tree_sha256=HASH_A,
        unit=identity(UNIT_PATH, HASH_B, inode=10),
        config=identity(
            CONFIG_PATH,
            HASH_C,
            inode=11,
            group=service_gid,
            mode=0o640,
        ),
        certificate=identity(
            "/etc/agentops-mis-relay/tls/relay.crt",
            HASH_D,
            inode=12,
        ),
        private_key=identity(
            "/etc/agentops-mis-relay/tls/relay.key",
            HASH_E,
            inode=13,
            owner=service_uid,
            group=service_gid,
            mode=0o600,
        ),
        route_keys=(
            identity(
                "/etc/agentops-mis-relay/routes/route-a.key",
                HASH_F,
                inode=14,
                owner=service_uid,
                group=service_gid,
                mode=0o600,
            ),
        ),
        state_directory=DirectoryIdentity(
            kind="directory",
            canonical_path=STATE_DIRECTORY,
            device_id=7,
            inode=18,
            owner_id=service_uid,
            group_id=service_gid,
            mode=0o700,
            nlink=2,
        ),
        runtime_directory=DirectoryIdentity(
            kind="directory",
            canonical_path=RUNTIME_DIRECTORY,
            device_id=7,
            inode=19,
            owner_id=service_uid,
            group_id=service_gid,
            mode=0o700,
            nlink=2,
        ),
        trusted_parent_chain_sha256="0" * 64,
        service_uid=service_uid,
        service_gid=service_gid,
        service_group_ids=(service_gid,),
        systemctl=identity(
            "/usr/bin/systemctl",
            "9" * 64,
            inode=15,
            mode=0o755,
        ),
        enablement_links=(),
    )


def systemd_bytes(**overrides: str) -> bytes:
    values = {
        "LoadState": "loaded",
        "UnitFileState": "disabled",
        "ActiveState": "inactive",
        "SubState": "dead",
        "Result": "success",
        "ExecMainStatus": "0",
        "FragmentPath": UNIT_PATH,
        "NeedDaemonReload": "no",
        "InvocationID": "",
        "MainPID": "0",
    }
    values.update(overrides)
    return "".join(
        f"{name}={values[name]}\n" for name in SYSTEMD_PROPERTIES
    ).encode("ascii")


class FakeBinding:
    def __init__(
        self,
        file_identity: FileIdentity,
        *,
        fail_revalidation_at: int | None = None,
    ):
        self.descriptor = 707
        self.identity = file_identity
        self.executable_path = "/proc/self/fd/707"
        self.fail_revalidation_at = fail_revalidation_at
        self.revalidation_count = 0
        self.closed = False

    def revalidate(self) -> None:
        self.revalidation_count += 1
        if self.revalidation_count == self.fail_revalidation_at:
            raise RuntimeError(f"{PRIVATE_CANARY}:identity-race")

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        payload: bytes,
        *,
        return_code: int = 0,
        hold_open: bool = False,
        running_after_output: bool = False,
        ignore_term: bool = False,
        leader_exited_with_helper: bool = False,
        cleanup_events: list[str] | None = None,
    ):
        self.pid = 919191
        self.terminated = False
        self.killed = False
        self.waited = False
        self.ignore_term = ignore_term
        self.group_alive = (
            hold_open or running_after_output or leader_exited_with_helper
        )
        self.cleanup_events = (
            cleanup_events if cleanup_events is not None else []
        )
        self._return_code: int | None = (
            None
            if hold_open or running_after_output
            else (0 if leader_exited_with_helper else return_code)
        )
        self._writer = -1
        if hold_open or leader_exited_with_helper:
            reader, writer = os.pipe()
            self.stdout = os.fdopen(reader, "rb", buffering=0)
            self._writer = writer
        else:
            temporary = tempfile.TemporaryFile(mode="w+b")
            temporary.write(payload)
            temporary.seek(0)
            self.stdout = temporary

    def poll(self) -> int | None:
        return self._return_code

    def terminate(self) -> None:
        self.terminated = True
        if self._writer >= 0:
            os.close(self._writer)
            self._writer = -1
        self._return_code = -15

    def kill(self) -> None:
        self.killed = True
        if self._writer >= 0:
            os.close(self._writer)
            self._writer = -1
        self._return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.cleanup_events.append("wait")
        self.waited = True
        if self._return_code is None:
            raise subprocess.TimeoutExpired("systemctl", 0)
        return self._return_code


class FakePopenFactory:
    def __init__(
        self,
        payload: bytes,
        *,
        return_code: int = 0,
        hold_open: bool = False,
        running_after_output: bool = False,
        ignore_term: bool = False,
        leader_exited_with_helper: bool = False,
    ):
        self.payload = payload
        self.return_code = return_code
        self.hold_open = hold_open
        self.running_after_output = running_after_output
        self.ignore_term = ignore_term
        self.leader_exited_with_helper = leader_exited_with_helper
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.processes: list[FakeProcess] = []
        self.group_signals: list[int] = []
        self.cleanup_events: list[str] = []

    def __call__(self, command, **kwargs):
        self.calls.append((tuple(command), dict(kwargs)))
        process = FakeProcess(
            self.payload,
            return_code=self.return_code,
            hold_open=self.hold_open,
            running_after_output=self.running_after_output,
            ignore_term=self.ignore_term,
            leader_exited_with_helper=self.leader_exited_with_helper,
            cleanup_events=self.cleanup_events,
        )
        self.processes.append(process)
        return process

    def group_kill(self, process_id: int, sent_signal: int) -> None:
        if not self.processes or process_id != self.processes[-1].pid:
            raise ProcessLookupError
        self.group_signals.append(sent_signal)
        process = self.processes[-1]
        if sent_signal == 0:
            self.group_signals.pop()
            self.cleanup_events.append("probe")
            if process.group_alive:
                return
            raise ProcessLookupError
        if sent_signal == signal.SIGTERM:
            self.cleanup_events.append("term")
            if process.ignore_term:
                return
            process.group_alive = False
            if process._return_code is None:
                process.terminate()
            elif process._writer >= 0:
                os.close(process._writer)
                process._writer = -1
        elif sent_signal == signal.SIGKILL:
            self.cleanup_events.append("kill")
            process.group_alive = False
            process.kill()
        else:
            raise ValueError("unexpected signal")


def expect_show_error(
    action,
    label: str,
    failures: list[str],
) -> None:
    try:
        action()
    except RelaySystemdShowError as exc:
        require(
            exc.error_id == SYSTEMD_SHOW_ERROR_ID,
            f"{label}: error id drifted",
            failures,
        )
        require(
            str(exc) == SYSTEMD_SHOW_ERROR_ID,
            f"{label}: exception leaked detail",
            failures,
        )
        return
    except Exception as exc:
        failures.append(f"{label}: wrong exception type {type(exc).__name__}")
        return
    failures.append(f"{label}: unsafe adapter behavior was accepted")


def adapter_action(
    base: ActivationPrerequisiteSnapshot,
    factory: FakePopenFactory,
    *,
    timeout_seconds: float = 1.0,
    binding: FakeBinding | None = None,
):
    chosen = binding or FakeBinding(base.systemctl)
    result = _read_systemd_show_with(
        base.systemctl,
        binding_factory=lambda _identity: chosen,
        process_runner=lambda value: _run_systemd_show_process(
            value,
            popen_factory=factory,
            timeout_seconds=timeout_seconds,
            group_kill=factory.group_kill,
        ),
    )
    return result, chosen


def main() -> int:
    failures: list[str] = []
    base = prerequisites()
    inactive_bytes = systemd_bytes()
    expected_snapshot = parse_systemd_show_bytes(inactive_bytes)

    original_platform = systemd_module.sys.platform
    real_systemctl_binding_checked = False
    if original_platform.startswith("linux"):
        real_systemctl_path = None
        for value in ("/usr/bin/systemctl", "/bin/systemctl"):
            candidate = Path(value)
            try:
                candidate_metadata = os.lstat(candidate)
            except OSError:
                continue
            candidate_mode = stat.S_IMODE(candidate_metadata.st_mode)
            if (
                stat.S_ISREG(candidate_metadata.st_mode)
                and candidate_metadata.st_uid == 0
                and candidate_metadata.st_gid == 0
                and candidate_metadata.st_nlink == 1
                and candidate_mode & 0o111
                and not candidate_mode & 0o022
                and 0 < candidate_metadata.st_size
                <= systemd_module.MAX_SYSTEMCTL_EXECUTABLE_BYTES
            ):
                real_systemctl_path = candidate
                break
        require(
            real_systemctl_path is not None,
            "Linux has no production-valid allowlisted systemctl",
            failures,
        )
        if real_systemctl_path is not None:
            metadata = os.lstat(real_systemctl_path)
            data = real_systemctl_path.read_bytes()
            real_identity = identity(
                real_systemctl_path.as_posix(),
                hashlib.sha256(data).hexdigest(),
                inode=metadata.st_ino,
                owner=metadata.st_uid,
                group=metadata.st_gid,
                mode=metadata.st_mode & 0o7777,
                size=len(data),
            )
            real_identity = replace(
                real_identity,
                device_id=metadata.st_dev,
                nlink=metadata.st_nlink,
            )
            real_binding = systemd_module._open_bound_systemctl(
                real_identity
            )
            real_descriptor = real_binding.descriptor
            real_binding.revalidate()
            real_binding.close()
            real_systemctl_binding_checked = True
            require(
                descriptor_is_closed(real_descriptor),
                "real systemctl binding leaked its descriptor",
                failures,
            )

            opened_descriptors: list[int] = []
            original_os_open = systemd_module.os.open

            def recording_open(path, flags):
                descriptor = original_os_open(path, flags)
                opened_descriptors.append(descriptor)
                return descriptor

            systemd_module.os.open = recording_open
            try:
                try:
                    systemd_module._open_bound_systemctl(
                        replace(
                            real_identity,
                            content_sha256="8" * 64,
                        )
                    )
                    failures.append(
                        "real systemctl binding accepted wrong content hash"
                    )
                except Exception:
                    pass
            finally:
                systemd_module.os.open = original_os_open
            require(
                bool(opened_descriptors)
                and all(
                    descriptor_is_closed(value)
                    for value in opened_descriptors
                ),
                "wrong-hash real binding leaked a descriptor",
                failures,
            )
    systemd_module.sys.platform = "linux"
    try:
        happy_factory = FakePopenFactory(inactive_bytes)
        observed, happy_binding = adapter_action(base, happy_factory)
        require(
            observed == expected_snapshot,
            "happy adapter snapshot changed",
            failures,
        )
        require(
            happy_binding.revalidation_count == 2,
            "adapter did not revalidate identity before and after child",
            failures,
        )
        require(happy_binding.closed, "adapter did not close bound FD", failures)
        require(
            len(happy_factory.calls) == 1,
            "adapter command count changed",
            failures,
        )
        if happy_factory.calls:
            command, options = happy_factory.calls[0]
            require(
                command
                == (
                    "/usr/bin/systemctl",
                    "--system",
                    "show",
                    UNIT_NAME,
                    "--no-pager",
                    "--property=" + ",".join(SYSTEMD_PROPERTIES),
                ),
                "systemctl command allowlist changed",
                failures,
            )
            require(
                options.get("executable") == "/proc/self/fd/707",
                "adapter did not execute the opened FD",
                failures,
            )
            require(options.get("shell") is False, "shell was enabled", failures)
            require(
                options.get("stdin") == subprocess.DEVNULL,
                "stdin was not closed",
                failures,
            )
            require(
                options.get("stderr") == subprocess.DEVNULL,
                "stderr was not discarded",
                failures,
            )
            require(
                options.get("stdout") == subprocess.PIPE,
                "stdout was not bounded through a pipe",
                failures,
            )
            require(options.get("cwd") == "/", "cwd was not fixed", failures)
            require(
                options.get("env")
                == {
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                },
                "environment allowlist changed",
                failures,
            )
            require(
                options.get("pass_fds") == (707,),
                "opened executable FD was not preserved",
                failures,
            )
            require(
                options.get("close_fds") is True,
                "unrelated descriptors were not closed",
                failures,
            )

        overflow_factory = FakePopenFactory(
            b"x" * (MAX_SYSTEMD_SHOW_BYTES + 1),
            running_after_output=True,
        )
        expect_show_error(
            lambda: adapter_action(base, overflow_factory),
            "stdout overflow",
            failures,
        )
        require(
            bool(overflow_factory.processes)
            and overflow_factory.processes[0].stdout.closed,
            "overflow child stdout was not closed",
            failures,
        )
        require(
            overflow_factory.group_signals == [signal.SIGTERM]
            and overflow_factory.processes[0].waited,
            "overflow did not terminate and wait for the child group",
            failures,
        )
        require(
            overflow_factory.cleanup_events
            == ["term", "wait", "probe", "wait"],
            "overflow cleanup did not reap leader before probing group",
            failures,
        )

        nonzero_factory = FakePopenFactory(
            (PRIVATE_CANARY + "\n").encode("ascii"),
            return_code=7,
        )
        expect_show_error(
            lambda: adapter_action(base, nonzero_factory),
            "nonzero exit",
            failures,
        )
        require(
            bool(nonzero_factory.processes)
            and nonzero_factory.processes[0].stdout.closed,
            "nonzero child stdout was not closed",
            failures,
        )

        malformed_factory = FakePopenFactory(
            (PRIVATE_CANARY + "\n").encode("ascii")
        )
        expect_show_error(
            lambda: adapter_action(base, malformed_factory),
            "malformed systemd output",
            failures,
        )

        timeout_factory = FakePopenFactory(
            b"",
            hold_open=True,
            ignore_term=True,
        )
        expect_show_error(
            lambda: adapter_action(
                base,
                timeout_factory,
                timeout_seconds=0.02,
            ),
            "timeout",
            failures,
        )
        require(
            bool(timeout_factory.processes)
            and timeout_factory.processes[0].killed
            and timeout_factory.processes[0].waited
            and timeout_factory.processes[0].stdout.closed,
            "timed out child was not terminated, waited, and closed",
            failures,
        )
        require(
            timeout_factory.group_signals
            == [signal.SIGTERM, signal.SIGKILL]
            and timeout_factory.processes[0].killed,
            "timeout did not escalate child group cleanup to SIGKILL",
            failures,
        )
        require(
            timeout_factory.cleanup_events
            == [
                "term",
                "wait",
                "probe",
                "kill",
                "wait",
                "probe",
                "wait",
            ],
            "timeout cleanup order changed",
            failures,
        )

        orphan_helper_factory = FakePopenFactory(
            b"",
            leader_exited_with_helper=True,
        )
        expect_show_error(
            lambda: adapter_action(
                base,
                orphan_helper_factory,
                timeout_seconds=0.02,
            ),
            "exited leader with live helper",
            failures,
        )
        require(
            orphan_helper_factory.group_signals == [signal.SIGTERM]
            and not orphan_helper_factory.processes[0].group_alive
            and orphan_helper_factory.processes[0].waited
            and orphan_helper_factory.processes[0].stdout.closed,
            "exited leader left its stdout-holding helper group alive",
            failures,
        )
        require(
            orphan_helper_factory.cleanup_events
            == ["term", "wait", "probe", "wait"],
            "exited-leader cleanup did not reap before probing helper group",
            failures,
        )

        before_race_binding = FakeBinding(
            base.systemctl,
            fail_revalidation_at=1,
        )
        before_race_factory = FakePopenFactory(inactive_bytes)
        expect_show_error(
            lambda: adapter_action(
                base,
                before_race_factory,
                binding=before_race_binding,
            ),
            "pre-child identity mismatch",
            failures,
        )
        require(
            not before_race_factory.calls and before_race_binding.closed,
            "pre-child identity mismatch reached subprocess",
            failures,
        )

        after_race_binding = FakeBinding(
            base.systemctl,
            fail_revalidation_at=2,
        )
        after_race_factory = FakePopenFactory(inactive_bytes)
        expect_show_error(
            lambda: adapter_action(
                base,
                after_race_factory,
                binding=after_race_binding,
            ),
            "post-child identity race",
            failures,
        )
        require(
            len(after_race_factory.calls) == 1 and after_race_binding.closed,
            "post-child identity race did not close exact binding",
            failures,
        )

        for label, unsafe_identity in (
            (
                "path override",
                replace(base.systemctl, canonical_path="/tmp/systemctl"),
            ),
            (
                "owner mismatch",
                replace(base.systemctl, owner_id=1701),
            ),
            (
                "content identity mismatch",
                replace(base.systemctl, content_sha256="z" * 64),
            ),
            (
                "forged bool owner",
                replace(base.systemctl, owner_id=False),
            ),
            (
                "forged bool group",
                replace(base.systemctl, group_id=False),
            ),
            (
                "forged bool link count",
                replace(base.systemctl, nlink=True),
            ),
        ):
            called = []
            expect_show_error(
                lambda candidate=unsafe_identity: _read_systemd_show_with(
                    candidate,
                    binding_factory=lambda _identity: called.append("open"),
                    process_runner=lambda _binding: inactive_bytes,
                ),
                label,
                failures,
            )
            require(
                not called,
                f"{label}: invalid scanner identity reached open",
                failures,
            )
    finally:
        systemd_module.sys.platform = original_platform

    sequence: list[str] = []
    scan_values = iter((base, base))

    def fake_scanner() -> ActivationPrerequisiteSnapshot:
        sequence.append("scan")
        return next(scan_values)

    def fake_reader(
        snapshot: ActivationPrerequisiteSnapshot,
    ) -> SystemdSnapshot:
        sequence.append("show")
        require(snapshot == base, "reader received wrong snapshot", failures)
        return expected_snapshot

    original_open = builtins.open
    original_os_open = os.open
    original_socket = socket.socket
    original_connect = socket.create_connection
    original_popen = subprocess.Popen
    external_calls: list[str] = []

    def blocked(*_args, **_kwargs):
        external_calls.append("blocked")
        raise AssertionError("preview attempted external behavior")

    builtins.open = blocked
    os.open = blocked
    socket.socket = blocked
    socket.create_connection = blocked
    subprocess.Popen = blocked
    try:
        projection = _preview_activation_with(
            scanner=fake_scanner,
            systemd_reader=fake_reader,
        )
    finally:
        builtins.open = original_open
        os.open = original_os_open
        socket.socket = original_socket
        socket.create_connection = original_connect
        subprocess.Popen = original_popen

    require(
        sequence == ["scan", "show", "scan"],
        "preview sequence is not scanner -> show -> scanner",
        failures,
    )
    require(not external_calls, "preview performed writes or network", failures)
    require(projection.get("ok") is True, "preview was not ready", failures)
    require(
        projection.get("state") == "plan_ready",
        "preview state changed",
        failures,
    )
    allowed_projection_keys = {
        "ok",
        "operation_id",
        "plan_sha256",
        "prerequisites",
        "release_id",
        "requested",
        "schema_id",
        "state",
        "systemd",
        "unit_id",
        "version_id",
    }
    require(
        set(projection) == allowed_projection_keys,
        "preview projection exposed extra fields",
        failures,
    )

    drift_sequence: list[str] = []
    drifted = replace(base, trusted_parent_chain_sha256="8" * 64)
    drift_values = iter((base, drifted))
    try:
        _preview_activation_with(
            scanner=lambda: (
                drift_sequence.append("scan") or next(drift_values)
            ),
            systemd_reader=lambda _snapshot: (
                drift_sequence.append("show") or expected_snapshot
            ),
        )
        failures.append("exact prerequisite drift was accepted")
    except RelayActivationPreviewError as exc:
        require(
            exc.error_id == ACTIVATION_PREREQUISITE_CHANGED,
            "rescan drift error id changed",
            failures,
        )
    require(
        drift_sequence == ["scan", "show", "scan"],
        "drift path skipped required sequence",
        failures,
    )

    enabled = replace(base, enablement_links=(enablement_link(),))
    active_systemd = parse_systemd_show_bytes(
        systemd_bytes(
            UnitFileState="enabled",
            ActiveState="active",
            SubState="running",
            InvocationID="1" * 32,
            MainPID="42",
        )
    )
    active_values = iter((enabled, enabled))
    already_active = _preview_activation_with(
        scanner=lambda: next(active_values),
        systemd_reader=lambda _snapshot: active_systemd,
    )
    require(
        already_active.get("state") == "already_active"
        and "plan_sha256" not in already_active,
        "already_active projection contract changed",
        failures,
    )

    try:
        _preview_activation_with(
            scanner=lambda: base,
            systemd_reader=lambda _snapshot: (_ for _ in ()).throw(
                RuntimeError(
                    f"{PRIVATE_CANARY}:/usr/bin/systemctl:UID=1701:HOST=secret"
                )
            ),
        )
        failures.append("unexpected reader failure was accepted")
    except RelayActivationPreviewError as exc:
        require(
            PRIVATE_CANARY not in str(exc)
            and "/usr/bin/systemctl" not in str(exc)
            and "1701" not in str(exc),
            "preview exception leaked private detail",
            failures,
        )

    encoded_projection = json.dumps(projection, sort_keys=True)
    for private_value in (
        PRIVATE_CANARY,
        "/usr/bin/systemctl",
        "/etc/agentops-mis-relay",
        "1701",
        "LANG",
        "LC_ALL",
        "secret",
    ):
        require(
            private_value not in encoded_projection,
            f"projection leaked {private_value}",
            failures,
        )

    require(
        list(inspect.signature(systemd_module.read_systemd_show).parameters)
        == ["prerequisites"],
        "public systemd reader exposes test/path override",
        failures,
    )
    require(
        not inspect.signature(preview_module.preview_activation).parameters,
        "public preview exposes scanner/runner override",
        failures,
    )
    open_source = inspect.getsource(systemd_module._open_bound_systemctl)
    binding_source = inspect.getsource(systemd_module._BoundSystemctl.revalidate)
    process_source = inspect.getsource(
        systemd_module._run_systemd_show_process
    )
    require(
        "os.lstat" in open_source
        and "os.fstat" in open_source
        and "_hash_descriptor" in open_source
        and "os.lstat" in binding_source
        and "os.fstat" in binding_source
        and "_hash_descriptor" in binding_source,
        "FD/path/full-hash identity checks drifted",
        failures,
    )
    require(
        "communicate(" not in process_source,
        "adapter uses unbounded communicate",
        failures,
    )
    for forbidden in (
        "daemon-reload",
        " enable ",
        " start ",
        " stop ",
        " disable ",
        "sudo",
        "pkexec",
        "journalctl",
    ):
        require(
            forbidden not in process_source,
            f"read-only adapter contains forbidden operation {forbidden}",
            failures,
        )

    preview_calls: list[str] = []
    subprocess_calls: list[str] = []
    original_preview = preview_module.preview_activation
    original_systemd_popen = systemd_module.subprocess.Popen

    def forbidden_preview():
        preview_calls.append("preview")
        raise AssertionError("unsafe CLI reached preview")

    def forbidden_popen(*_args, **_kwargs):
        subprocess_calls.append("popen")
        raise AssertionError("unsafe CLI reached subprocess")

    preview_module.preview_activation = forbidden_preview
    systemd_module.subprocess.Popen = forbidden_popen
    cli_results: list[tuple[list[str], int, str, str]] = []
    try:
        for arguments in (
            ["--root", "/tmp/fixture", "activate"],
            ["--root", "//", "activate"],
            ["--root", "/.", "activate"],
            ["--root", "/./", "activate"],
            ["--root=/.", "activate"],
            ["--root", "/", "--root=/", "activate"],
            ["--root", "/", "activate", "--confirm-activate"],
            [
                "--root",
                "/",
                "activate",
                "--plan-sha256",
                "a" * 64,
            ],
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                status = relay_admin_main(arguments)
            cli_results.append(
                (arguments, status, stdout.getvalue(), stderr.getvalue())
            )
    finally:
        preview_module.preview_activation = original_preview
        systemd_module.subprocess.Popen = original_systemd_popen

    require(
        not preview_calls and not subprocess_calls,
        "unsafe CLI arguments reached scanner/subprocess",
        failures,
    )
    for arguments, status, stdout, stderr in cli_results:
        expected_error = (
            "host_root_required"
            if (
                "/tmp/fixture" in arguments
                or "//" in arguments
                or "/." in arguments
                or "/./" in arguments
                or "--root=/." in arguments
                or arguments.count("--root") + sum(
                    value.startswith("--root=") for value in arguments
                )
                > 1
            )
            else "activation_mutation_unavailable"
        )
        try:
            error = json.loads(stderr)
        except Exception:
            error = {}
        require(status == 1, "unsafe CLI arguments returned success", failures)
        require(not stdout, "unsafe CLI arguments wrote stdout", failures)
        require(
            error
            == {
                "error_id": expected_error,
                "ok": False,
                "operation_id": "activate",
            },
            "unsafe CLI error projection changed",
            failures,
        )
        require(
            "/tmp/fixture" not in stderr
            and PRIVATE_CANARY not in stderr
            and "a" * 64 not in stderr,
            "CLI error leaked argument detail",
            failures,
        )

    collision_stdout = io.StringIO()
    collision_stderr = io.StringIO()
    with contextlib.redirect_stdout(
        collision_stdout
    ), contextlib.redirect_stderr(collision_stderr):
        collision_status = relay_admin_main(
            ["--root", "activate", "status"]
        )
    try:
        collision_output = json.loads(collision_stdout.getvalue())
    except Exception:
        collision_output = {}
    require(
        collision_status == 1
        and collision_output.get("operation_id") == "status"
        and collision_output.get("state_id") == "invalid"
        and "host_root_required" not in collision_stderr.getvalue(),
        "status root value colliding with activate changed command semantics",
        failures,
    )

    result = {
        "adapter_command_exact": not failures,
        "already_active_without_plan_hash": (
            already_active.get("state") == "already_active"
            and "plan_sha256" not in already_active
        ),
        "cli_unsafe_inputs_blocked_before_subprocess": (
            not preview_calls and not subprocess_calls
        ),
        "failures": failures,
        "identity_revalidated_after_child": (
            happy_binding.revalidation_count == 2
        ),
        "network_used": False,
        "preview_sequence": sequence,
        "public_overrides_exposed": False,
        "real_systemctl_binding_checked_on_linux": (
            real_systemctl_binding_checked
        ),
        "systemd_mutation_performed": False,
        "write_performed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
