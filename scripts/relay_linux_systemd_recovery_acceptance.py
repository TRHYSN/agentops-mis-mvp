#!/usr/bin/env python3
"""Run guarded Relay recovery against real systemd on a disposable Linux VM."""
from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    CONTROLLED_STOP_EXEC_STATUS,
    ENABLEMENT_LINK_PATH,
    INVOCATION_ID_PATTERN,
    MAX_SYSTEMD_SHOW_BYTES,
    SHA256_PATTERN,
    SYSTEMCTL_PATHS,
    SYSTEMD_PROPERTIES,
    UNIT_PATH,
    UNIT_NAME,
    ActivationPrerequisiteSnapshot,
    FileIdentity,
    LinkIdentity,
    compile_activation_plan,
    parse_systemd_show_bytes,
)
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    build_activation_journal_identity,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    _open_fixture_store,
    build_activation_revision,
)
from agentops_mis_cli.relay_activation_recovery_controller import (  # noqa: E402
    _run_confirmed_recovery_write_with,
)
from agentops_mis_cli.relay_activation_recovery_executor import (  # noqa: E402
    _run_confirmed_recovery_step_with,
)
from agentops_mis_cli.relay_activation_recovery_preview import (  # noqa: E402
    RelayActivationRecoveryPreviewError,
    _preview_activation_recovery_with,
)
from agentops_mis_cli.relay_systemd_mutation import (  # noqa: E402
    _run_bound_systemd_mutation,
)
from agentops_mis_cli.relay_systemd_read import (  # noqa: E402
    read_systemd_show,
)
from scripts.relay_activation_recovery_decision_smoke import (  # noqa: E402
    prerequisites,
)


OPT_IN = "AGENTOPS_RELAY_LINUX_SYSTEMD_ACCEPTANCE"
UNIT_BYTES = b"""[Unit]
Description=AgentOps MIS disposable recovery acceptance

[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity
DynamicUser=yes
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"""
UNIT_CHANGED_BYTES = UNIT_BYTES + b"\n# recovery acceptance\n"
MAX_STEP_COUNT = 16
SYSTEMD_SHOW_TIMEOUT_SECONDS = 5
PROCESS_DEATH_CHILD_TIMEOUT_SECONDS = 30
PROCESS_DEATH_EXECUTE_MODE = "--process-death-execute"
PROCESS_DEATH_RECOVER_MODE = "--process-death-recover"
PROCESS_DEATH_PIPE_MARKER = b"daemon_reload_returned\n"
PROCESS_DEATH_MUTATION_RECORD = b"daemon_reload\n"
PROCESS_DEATH_MUTATION_FILE = "daemon-reload-mutations.log"
RECEIPT_PROCESS_DEATH_EXECUTE_MODE = "--receipt-process-death-execute"
RECEIPT_PROCESS_DEATH_RECOVER_MODE = "--receipt-process-death-recover"
RECEIPT_PROCESS_DEATH_PIPE_MARKER = b"rollback_receipt_published\n"
MAX_PROCESS_DEATH_RESULT_BYTES = 4096
MAX_PROCESS_DEATH_MUTATION_RECORDS = 4


class AcceptanceFailure(Exception):
    def __init__(self, stage: str = "unknown") -> None:
        self.stage = stage
        super().__init__("linux_systemd_acceptance_failed")


class _CountingRecoveryStore:
    def __init__(self, store: Any) -> None:
        self._store = store
        self.receipt_writes = 0
        self.revision_writes = 0

    def _load_recovery_snapshot(self, plan_sha256: str) -> Any:
        return self._store._load_recovery_snapshot(plan_sha256)

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        self.revision_writes += 1
        return self._store.publish_revision(raw)

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        self.receipt_writes += 1
        return self._store.publish_receipt(raw)


class _ReceiptCheckpointStore(_CountingRecoveryStore):
    def __init__(self, store: Any, pipe_descriptor: int) -> None:
        super().__init__(store)
        self._pipe_descriptor = pipe_descriptor

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        raise AcceptanceFailure("receipt_process_death_unexpected_revision")

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        if self.receipt_writes != 0:
            raise AcceptanceFailure(
                "receipt_process_death_receipt_reentered"
            )
        self.receipt_writes += 1
        result = self._store.publish_receipt(raw)
        if (
            result.get("ok") is not True
            or result.get("outcome") != "created"
        ):
            raise AcceptanceFailure(
                "receipt_process_death_receipt_publish"
            )
        if (
            os.write(
                self._pipe_descriptor,
                RECEIPT_PROCESS_DEATH_PIPE_MARKER,
            )
            != len(RECEIPT_PROCESS_DEATH_PIPE_MARKER)
        ):
            raise AcceptanceFailure(
                "receipt_process_death_pipe_marker_write"
            )
        os.close(self._pipe_descriptor)
        self._pipe_descriptor = -1
        while True:
            signal.pause()


def _fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
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


def _file_identity(path: str) -> FileIdentity:
    descriptor = -1
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_nlink != 1
        ):
            raise AcceptanceFailure
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if _fingerprint(before) != _fingerprint(opened):
            raise AcceptanceFailure
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise AcceptanceFailure
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AcceptanceFailure
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if not (
            _fingerprint(opened)
            == _fingerprint(after)
            == _fingerprint(current)
        ):
            raise AcceptanceFailure
        return FileIdentity(
            kind="regular",
            canonical_path=path,
            device_id=opened.st_dev,
            inode=opened.st_ino,
            owner_id=opened.st_uid,
            group_id=opened.st_gid,
            mode=stat.S_IMODE(opened.st_mode),
            nlink=opened.st_nlink,
            size=opened.st_size,
            content_sha256=digest.hexdigest(),
        )
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _systemctl_identity() -> FileIdentity:
    for path in ("/usr/bin/systemctl", "/bin/systemctl"):
        if path not in SYSTEMCTL_PATHS:
            continue
        try:
            return _file_identity(path)
        except AcceptanceFailure:
            continue
    raise AcceptanceFailure


def _diagnose_rollback_stop(identity: FileIdentity) -> str:
    """Return one bounded classifier without retaining systemd output."""

    try:
        result = subprocess.run(
            (
                identity.canonical_path,
                "--system",
                "show",
                UNIT_NAME,
                "--no-pager",
                "--property=" + ",".join(SYSTEMD_PROPERTIES),
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            timeout=SYSTEMD_SHOW_TIMEOUT_SECONDS,
        )
    except Exception:
        return "rollback_rollback_stop_diagnostic_command"
    raw = result.stdout
    if (
        result.returncode != 0
        or not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_SYSTEMD_SHOW_BYTES
        or b"\x00" in raw
        or b"\r" in raw
    ):
        return "rollback_rollback_stop_diagnostic_command"
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return "rollback_rollback_stop_diagnostic_shape"
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            return "rollback_rollback_stop_diagnostic_shape"
        name, value = line.split("=", 1)
        if (
            name not in SYSTEMD_PROPERTIES
            or name in values
            or len(value) > 4096
        ):
            return "rollback_rollback_stop_diagnostic_shape"
        values[name] = value
    if set(values) != set(SYSTEMD_PROPERTIES):
        return "rollback_rollback_stop_diagnostic_shape"
    expected = (
        ("LoadState", {"loaded"}, "load_state"),
        ("UnitFileState", {"enabled"}, "unit_file_state"),
        ("ActiveState", {"inactive"}, "active_state"),
        ("SubState", {"dead"}, "sub_state"),
        ("Result", {"", "success"}, "result"),
        ("FragmentPath", {UNIT_PATH}, "fragment_path"),
        ("NeedDaemonReload", {"no"}, "daemon_reload"),
        ("MainPID", {"0"}, "main_pid"),
    )
    for name, allowed, classifier in expected:
        if values[name] not in allowed:
            return f"rollback_rollback_stop_diagnostic_{classifier}"
    exec_status_value = values["ExecMainStatus"]
    if (
        not exec_status_value
        or not exec_status_value.isascii()
        or not exec_status_value.isdecimal()
        or (
            len(exec_status_value) > 1
            and exec_status_value.startswith("0")
        )
        or int(exec_status_value) > 255
        or (
            int(exec_status_value)
            not in {0, CONTROLLED_STOP_EXEC_STATUS}
        )
        or (
            int(exec_status_value) == CONTROLLED_STOP_EXEC_STATUS
            and values["Result"] != "success"
        )
    ):
        return "rollback_rollback_stop_diagnostic_exec_status"
    invocation_id = values["InvocationID"]
    if (
        invocation_id
        and not INVOCATION_ID_PATTERN.fullmatch(invocation_id)
    ):
        return "rollback_rollback_stop_diagnostic_invocation_id"
    try:
        snapshot = parse_systemd_show_bytes(raw)
    except Exception:
        return "rollback_rollback_stop_diagnostic_parser"
    if (
        snapshot.active_state != "inactive"
        or snapshot.unit_file_state != "enabled"
        or snapshot.need_daemon_reload
    ):
        return "rollback_rollback_stop_diagnostic_observation"
    return "rollback_rollback_stop_diagnostic_valid_late_state"


def _enablement_links() -> tuple[LinkIdentity, ...]:
    try:
        metadata = os.lstat(ENABLEMENT_LINK_PATH)
    except FileNotFoundError:
        return ()
    except OSError:
        raise AcceptanceFailure from None
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or Path(ENABLEMENT_LINK_PATH).resolve()
        != Path(UNIT_PATH)
    ):
        raise AcceptanceFailure
    return (
        LinkIdentity(
            kind="symlink",
            canonical_path=ENABLEMENT_LINK_PATH,
            target=UNIT_PATH,
            device_id=metadata.st_dev,
            inode=metadata.st_ino,
            owner_id=metadata.st_uid,
            group_id=metadata.st_gid,
            nlink=metadata.st_nlink,
        ),
    )


def _write_unit() -> None:
    descriptor = -1
    created: tuple[int, int] | None = None
    complete = False
    try:
        descriptor = os.open(
            UNIT_PATH,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o644,
        )
        metadata = os.fstat(descriptor)
        created = (metadata.st_dev, metadata.st_ino)
        view = memoryview(UNIT_BYTES)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AcceptanceFailure
            view = view[written:]
        os.fsync(descriptor)
        complete = True
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created is not None and not complete:
            try:
                current = os.lstat(UNIT_PATH)
                if (current.st_dev, current.st_ino) == created:
                    os.unlink(UNIT_PATH)
            except OSError:
                pass


def _mark_unit_changed() -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            UNIT_PATH,
            os.O_WRONLY
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        if os.write(descriptor, b"\n# recovery acceptance\n") != 23:
            raise AcceptanceFailure
        os.fsync(descriptor)
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _owned_unit() -> bool:
    descriptor = -1
    try:
        descriptor = os.open(
            UNIT_PATH,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > len(UNIT_CHANGED_BYTES)
        ):
            return False
        payload = bytearray()
        while len(payload) < metadata.st_size:
            chunk = os.read(
                descriptor,
                metadata.st_size - len(payload),
            )
            if not chunk:
                return False
            payload.extend(chunk)
        return bytes(payload) in {UNIT_BYTES, UNIT_CHANGED_BYTES}
    except FileNotFoundError:
        return True
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _cleanup(systemctl: FileIdentity | None) -> bool:
    if not _owned_unit():
        return False
    command = (
        systemctl.canonical_path
        if systemctl is not None
        else "/usr/bin/systemctl"
    )
    for operation in ("stop", "disable"):
        try:
            subprocess.run(
                (
                    command,
                    "--system",
                    operation,
                    "agentops-mis-relay.service",
                ),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except Exception:
            pass
    for path in (ENABLEMENT_LINK_PATH, UNIT_PATH):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
    try:
        subprocess.run(
            (command, "--system", "daemon-reload"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        subprocess.run(
            (
                command,
                "--system",
                "reset-failed",
                "agentops-mis-relay.service",
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        return False
    return not os.path.lexists(UNIT_PATH) and not os.path.lexists(
        ENABLEMENT_LINK_PATH
    )


def _process_death_environment() -> dict[str, str]:
    return {
        OPT_IN: "1",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _process_death_paths(
    journal_argument: str,
    marker_argument: str,
) -> tuple[Path, Path]:
    journal_root = Path(journal_argument)
    marker_path = Path(marker_argument)
    if (
        not journal_root.is_absolute()
        or not marker_path.is_absolute()
        or marker_path != journal_root / PROCESS_DEATH_MUTATION_FILE
        or not journal_root.is_dir()
        or not os.path.lexists(UNIT_PATH)
        or not _owned_unit()
        or _enablement_links()
    ):
        raise AcceptanceFailure("process_death_child_preflight")
    return journal_root, marker_path


def _process_death_scanner() -> ActivationPrerequisiteSnapshot:
    return replace(
        prerequisites(),
        unit=_file_identity(UNIT_PATH),
        systemctl=_systemctl_identity(),
        enablement_links=_enablement_links(),
    )


def _marker_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
    )


def _append_process_death_mutation(marker_path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            marker_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or before.st_nlink != 1
            or before.st_size % len(PROCESS_DEATH_MUTATION_RECORD)
            or before.st_size
            >= len(PROCESS_DEATH_MUTATION_RECORD)
            * MAX_PROCESS_DEATH_MUTATION_RECORDS
        ):
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
        if (
            os.write(descriptor, PROCESS_DEATH_MUTATION_RECORD)
            != len(PROCESS_DEATH_MUTATION_RECORD)
        ):
            raise AcceptanceFailure(
                "process_death_mutation_marker_write"
            )
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        current = os.lstat(marker_path)
        if (
            _marker_identity(before)
            != _marker_identity(after)
            or _marker_identity(after) != _marker_identity(current)
            or after.st_size
            != before.st_size + len(PROCESS_DEATH_MUTATION_RECORD)
            or current.st_size != after.st_size
        ):
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure(
            "process_death_mutation_marker_write"
        ) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _process_death_mutation_count(
    marker_path: Path,
    *,
    allow_missing: bool = False,
) -> int:
    descriptor = -1
    try:
        descriptor = os.open(
            marker_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        maximum = (
            len(PROCESS_DEATH_MUTATION_RECORD)
            * MAX_PROCESS_DEATH_MUTATION_RECORDS
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_uid != os.geteuid()
            or opened.st_gid != os.getegid()
            or opened.st_nlink != 1
            or opened.st_size <= 0
            or opened.st_size > maximum
            or opened.st_size % len(PROCESS_DEATH_MUTATION_RECORD)
        ):
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
        payload = bytearray()
        while len(payload) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(payload))
            if not chunk:
                raise AcceptanceFailure(
                    "process_death_mutation_marker_invalid"
                )
            payload.extend(chunk)
        if os.read(descriptor, 1):
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
        after = os.fstat(descriptor)
        current = os.lstat(marker_path)
        if not (
            _fingerprint(opened)
            == _fingerprint(after)
            == _fingerprint(current)
        ):
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
        count = opened.st_size // len(PROCESS_DEATH_MUTATION_RECORD)
        if bytes(payload) != PROCESS_DEATH_MUTATION_RECORD * count:
            raise AcceptanceFailure(
                "process_death_mutation_marker_invalid"
            )
        return count
    except FileNotFoundError:
        if allow_missing:
            return 0
        raise AcceptanceFailure(
            "process_death_mutation_marker_missing"
        ) from None
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure(
            "process_death_mutation_marker_invalid"
        ) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _receipt_count(store: Any, snapshot: Any) -> int:
    receipt = snapshot.receipt
    try:
        names = tuple(sorted(os.listdir(store.receipts_fd)))
    except Exception:
        raise AcceptanceFailure(
            "receipt_process_death_receipt_count"
        ) from None
    if (
        receipt is None
        or names != (f"{receipt.receipt_sha256}.json",)
    ):
        raise AcceptanceFailure("receipt_process_death_receipt_count")
    return 1


def _process_death_execute_child(arguments: list[str]) -> int:
    pipe_descriptor = -1
    try:
        if (
            len(arguments) != 5
            or os.environ.get(OPT_IN) != "1"
            or not sys.platform.startswith("linux")
            or os.geteuid() != 0
            or not Path("/run/systemd/system").is_dir()
            or SHA256_PATTERN.fullmatch(arguments[2]) is None
            or SHA256_PATTERN.fullmatch(arguments[3]) is None
        ):
            return 1
        journal_root, marker_path = _process_death_paths(
            arguments[0],
            arguments[1],
        )
        pipe_descriptor = int(arguments[4])
        pipe_metadata = os.fstat(pipe_descriptor)
        if pipe_descriptor < 3 or not stat.S_ISFIFO(pipe_metadata.st_mode):
            return 1
        mutation_returned = False
        checkpoint_published = False

        def mutation_runner(
            systemctl: FileIdentity,
            operation: str,
        ) -> None:
            nonlocal mutation_returned
            if operation != "daemon_reload" or mutation_returned:
                raise AcceptanceFailure(
                    "process_death_unexpected_mutation"
                )
            _run_bound_systemd_mutation(systemctl, operation)
            _append_process_death_mutation(marker_path)
            mutation_returned = True

        def checkpoint_scanner() -> ActivationPrerequisiteSnapshot:
            nonlocal checkpoint_published, pipe_descriptor
            if mutation_returned:
                if checkpoint_published:
                    raise AcceptanceFailure(
                        "process_death_checkpoint_reentered"
                    )
                if (
                    os.write(
                        pipe_descriptor,
                        PROCESS_DEATH_PIPE_MARKER,
                    )
                    != len(PROCESS_DEATH_PIPE_MARKER)
                ):
                    raise AcceptanceFailure(
                        "process_death_pipe_marker_write"
                    )
                os.close(pipe_descriptor)
                pipe_descriptor = -1
                checkpoint_published = True
                while True:
                    signal.pause()
            return _process_death_scanner()

        with _open_fixture_store(journal_root) as store:
            _run_confirmed_recovery_step_with(
                arguments[2],
                "resume",
                arguments[3],
                store=store,
                scanner=checkpoint_scanner,
                systemd_reader=read_systemd_show,
                mutation_runner=mutation_runner,
            )
        return 1
    except Exception:
        return 1
    finally:
        if pipe_descriptor >= 0:
            try:
                os.close(pipe_descriptor)
            except OSError:
                pass


def _process_death_recover_child(arguments: list[str]) -> int:
    stage = "recover_child_preflight"
    try:
        if (
            len(arguments) != 3
            or os.environ.get(OPT_IN) != "1"
            or not sys.platform.startswith("linux")
            or os.geteuid() != 0
            or not Path("/run/systemd/system").is_dir()
            or SHA256_PATTERN.fullmatch(arguments[2]) is None
        ):
            raise AcceptanceFailure(stage)
        journal_root, marker_path = _process_death_paths(
            arguments[0],
            arguments[1],
        )
        plan_sha256 = arguments[2]
        if _process_death_mutation_count(marker_path) != 1:
            raise AcceptanceFailure(stage)

        stage = "recover_child_reopen"
        with _open_fixture_store(journal_root) as store:
            before = store._load_recovery_snapshot(plan_sha256)
            if (
                len(before.revisions) != 2
                or before.receipt is not None
                or before.revisions[-1].phase != "intent"
                or before.revisions[-1].step_id != "daemon_reload"
                or before.revisions[-1].intent_id
                != "daemon_reload_requested"
            ):
                raise AcceptanceFailure(stage)

            stage = "recover_child_preview_observation"
            decision = _preview_activation_recovery_with(
                plan_sha256,
                "resume",
                snapshot_loader=store._load_recovery_snapshot,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            if (
                decision.get("action_id") != "resume"
                or decision.get("operation_id") != "record_observation"
                or decision.get("reason_id") != "resume_ready"
                or decision.get("step_id") != "daemon_reload"
            ):
                raise AcceptanceFailure(stage)

            stage = "recover_child_record_observation"
            result = _run_confirmed_recovery_write_with(
                plan_sha256,
                "resume",
                str(decision["decision_sha256"]),
                store=store,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            after = store._load_recovery_snapshot(plan_sha256)
            if (
                result.get("write_id") != "observed_revision"
                or len(after.revisions) != 3
                or after.receipt is not None
                or after.revisions[-1].phase != "observed"
                or after.revisions[-1].step_id != "daemon_reload"
            ):
                raise AcceptanceFailure(stage)

            stage = "recover_child_next_decision"
            next_decision = _preview_activation_recovery_with(
                plan_sha256,
                "resume",
                snapshot_loader=store._load_recovery_snapshot,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            if (
                next_decision.get("action_id") != "resume"
                or next_decision.get("operation_id") != "run_step"
                or next_decision.get("reason_id") != "resume_ready"
                or next_decision.get("step_id") != "enable"
            ):
                raise AcceptanceFailure(stage)

        if _process_death_mutation_count(marker_path) != 1:
            raise AcceptanceFailure(
                "recover_child_mutation_replayed"
            )
        print(
            json.dumps(
                {
                    "journal_reopened": True,
                    "latest_revision": 3,
                    "mutation_count": 1,
                    "mutation_replayed": False,
                    "next_step": "enable",
                    "observation_operation": "record_observation",
                    "ok": True,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "failure_id": "process_death_recovery_failed",
                    "ok": False,
                    "stage": stage,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1


def _run_process_death_gate(
    journal_root: Path,
    plan_sha256: str,
    confirmed_decision_sha256: str,
) -> dict[str, object]:
    marker_path = journal_root / PROCESS_DEATH_MUTATION_FILE
    if _process_death_mutation_count(
        marker_path,
        allow_missing=True,
    ) != 0:
        raise AcceptanceFailure("process_death_marker_preexisting")

    read_descriptor = -1
    write_descriptor = -1
    child: subprocess.Popen[bytes] | None = None
    try:
        read_descriptor, write_descriptor = os.pipe()
        child = subprocess.Popen(
            (
                sys.executable,
                str(Path(__file__).resolve()),
                PROCESS_DEATH_EXECUTE_MODE,
                str(journal_root),
                str(marker_path),
                plan_sha256,
                confirmed_decision_sha256,
                str(write_descriptor),
            ),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=_process_death_environment(),
            close_fds=True,
            pass_fds=(write_descriptor,),
            start_new_session=True,
        )
        os.close(write_descriptor)
        write_descriptor = -1
        ready, _writable, _exceptional = select.select(
            (read_descriptor,),
            (),
            (),
            PROCESS_DEATH_CHILD_TIMEOUT_SECONDS,
        )
        if not ready:
            raise AcceptanceFailure("process_death_marker_timeout")
        marker = os.read(
            read_descriptor,
            len(PROCESS_DEATH_PIPE_MARKER) + 1,
        )
        if (
            marker != PROCESS_DEATH_PIPE_MARKER
            or child.poll() is not None
        ):
            raise AcceptanceFailure("process_death_marker_invalid")
        child.kill()
        return_code = child.wait(
            timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS
        )
        if return_code != -signal.SIGKILL:
            raise AcceptanceFailure("process_death_sigkill_unproven")
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure("process_death_execution_failed") from None
    finally:
        for descriptor in (write_descriptor, read_descriptor):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if child is not None and child.poll() is None:
            try:
                child.kill()
                child.wait(
                    timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS
                )
            except Exception:
                pass

    if _process_death_mutation_count(marker_path) != 1:
        raise AcceptanceFailure("process_death_mutation_count")
    with _open_fixture_store(journal_root) as checkpoint_store:
        checkpoint = checkpoint_store._load_recovery_snapshot(
            plan_sha256
        )
        if (
            len(checkpoint.revisions) != 2
            or checkpoint.receipt is not None
            or checkpoint.revisions[-1].phase != "intent"
            or checkpoint.revisions[-1].step_id != "daemon_reload"
            or checkpoint.revisions[-1].intent_id
            != "daemon_reload_requested"
        ):
            raise AcceptanceFailure("process_death_checkpoint_invalid")

    try:
        recovered = subprocess.run(
            (
                sys.executable,
                str(Path(__file__).resolve()),
                PROCESS_DEATH_RECOVER_MODE,
                str(journal_root),
                str(marker_path),
                plan_sha256,
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=_process_death_environment(),
            timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS,
        )
    except Exception:
        raise AcceptanceFailure(
            "process_death_recovery_child_failed"
        ) from None
    if (
        recovered.returncode != 0
        or not recovered.stdout
        or len(recovered.stdout) > MAX_PROCESS_DEATH_RESULT_BYTES
        or b"\x00" in recovered.stdout
        or b"\r" in recovered.stdout
    ):
        raise AcceptanceFailure("process_death_recovery_child_failed")
    try:
        recovery_result = json.loads(recovered.stdout.decode("ascii"))
    except Exception:
        raise AcceptanceFailure(
            "process_death_recovery_result_invalid"
        ) from None
    expected = {
        "journal_reopened": True,
        "latest_revision": 3,
        "mutation_count": 1,
        "mutation_replayed": False,
        "next_step": "enable",
        "observation_operation": "record_observation",
        "ok": True,
    }
    if recovery_result != expected:
        raise AcceptanceFailure("process_death_recovery_result_invalid")
    if _process_death_mutation_count(marker_path) != 1:
        raise AcceptanceFailure("process_death_mutation_replayed")
    return {
        "checkpoint": "after_daemon_reload_before_observation",
        "child_exit_signal": "SIGKILL",
        **expected,
    }


def _receipt_process_death_execute_child(arguments: list[str]) -> int:
    pipe_descriptor = -1
    try:
        if (
            len(arguments) != 5
            or os.environ.get(OPT_IN) != "1"
            or not sys.platform.startswith("linux")
            or os.geteuid() != 0
            or not Path("/run/systemd/system").is_dir()
            or SHA256_PATTERN.fullmatch(arguments[2]) is None
            or SHA256_PATTERN.fullmatch(arguments[3]) is None
        ):
            return 1
        journal_root, marker_path = _process_death_paths(
            arguments[0],
            arguments[1],
        )
        if _process_death_mutation_count(marker_path) != 1:
            return 1
        pipe_descriptor = int(arguments[4])
        pipe_metadata = os.fstat(pipe_descriptor)
        if pipe_descriptor < 3 or not stat.S_ISFIFO(pipe_metadata.st_mode):
            return 1

        with _open_fixture_store(journal_root) as store:
            before = store._load_recovery_snapshot(arguments[2])
            last = before.revisions[-1]
            if (
                before.receipt is not None
                or last.phase != "observed"
                or last.step_id != "verify"
                or last.intent_id != "rollback_verify_requested"
                or last.observation_id != "rollback_verified"
                or last.owns_enable
                or last.owns_start
            ):
                return 1
            checkpoint_store = _ReceiptCheckpointStore(
                store,
                pipe_descriptor,
            )
            _run_confirmed_recovery_write_with(
                arguments[2],
                "rollback",
                arguments[3],
                store=checkpoint_store,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
        return 1
    except Exception:
        return 1
    finally:
        if pipe_descriptor >= 0:
            try:
                os.close(pipe_descriptor)
            except OSError:
                pass


def _receipt_process_death_recover_child(arguments: list[str]) -> int:
    stage = "receipt_recover_child_preflight"
    try:
        if (
            len(arguments) != 4
            or os.environ.get(OPT_IN) != "1"
            or not sys.platform.startswith("linux")
            or os.geteuid() != 0
            or not Path("/run/systemd/system").is_dir()
            or SHA256_PATTERN.fullmatch(arguments[2]) is None
            or SHA256_PATTERN.fullmatch(arguments[3]) is None
        ):
            raise AcceptanceFailure(stage)
        journal_root, marker_path = _process_death_paths(
            arguments[0],
            arguments[1],
        )
        plan_sha256 = arguments[2]
        receipt_decision_sha256 = arguments[3]
        if _process_death_mutation_count(marker_path) != 1:
            raise AcceptanceFailure(stage)

        stage = "receipt_recover_child_reopen"
        with _open_fixture_store(journal_root) as store:
            before = store._load_recovery_snapshot(plan_sha256)
            last = before.revisions[-1]
            receipt = before.receipt
            if (
                receipt is None
                or last.phase != "observed"
                or last.step_id != "verify"
                or last.intent_id != "rollback_verify_requested"
                or last.observation_id != "rollback_verified"
                or last.owns_enable
                or last.owns_start
                or receipt.identity.plan_sha256 != plan_sha256
                or receipt.terminal_revision != last.revision + 1
                or receipt.previous_revision_sha256 != last.record_sha256
                or receipt.terminal_state
                != "service_state_rolled_back"
                or receipt.owns_enable
                or receipt.owns_start
                or _receipt_count(store, before) != 1
            ):
                raise AcceptanceFailure(stage)
            receipt_sha256 = receipt.receipt_sha256
            revision_count = len(before.revisions)

            stage = "receipt_recover_child_preview_terminal"
            terminal_decision = _preview_activation_recovery_with(
                plan_sha256,
                "rollback",
                snapshot_loader=store._load_recovery_snapshot,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            terminal_decision_sha256 = str(
                terminal_decision.get("decision_sha256")
            )
            if (
                terminal_decision.get("action_id") != "terminalize"
                or terminal_decision.get("operation_id")
                != "publish_terminal_revision"
                or terminal_decision.get("reason_id") != "receipt_ready"
                or terminal_decision.get("step_id") != "terminal"
                or SHA256_PATTERN.fullmatch(
                    terminal_decision_sha256
                )
                is None
                or terminal_decision_sha256
                == receipt_decision_sha256
            ):
                raise AcceptanceFailure(stage)

            stage = "receipt_recover_child_publish_terminal"
            terminal_store = _CountingRecoveryStore(store)
            terminal_result = _run_confirmed_recovery_write_with(
                plan_sha256,
                "rollback",
                terminal_decision_sha256,
                store=terminal_store,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            terminal_snapshot = store._load_recovery_snapshot(
                plan_sha256
            )
            if (
                terminal_store.revision_writes != 1
                or terminal_store.receipt_writes != 0
                or terminal_result.get("write_id")
                != "terminal_revision"
                or terminal_result.get("state")
                != "service_state_rolled_back"
                or terminal_result.get("recovery_required") is not False
                or len(terminal_snapshot.revisions)
                != revision_count + 1
                or terminal_snapshot.revisions[-1].phase != "terminal"
                or terminal_snapshot.revisions[-1].revision
                != receipt.terminal_revision
                or terminal_snapshot.revisions[-1].receipt_sha256
                != receipt_sha256
                or terminal_snapshot.receipt is None
                or terminal_snapshot.receipt.receipt_sha256
                != receipt_sha256
                or _receipt_count(store, terminal_snapshot) != 1
            ):
                raise AcceptanceFailure(stage)

        stage = "receipt_recover_child_reopen_complete"
        with _open_fixture_store(journal_root) as store:
            complete_before = store._load_recovery_snapshot(plan_sha256)
            if (
                complete_before.receipt is None
                or complete_before.receipt.receipt_sha256
                != receipt_sha256
                or complete_before.revisions[-1].phase != "terminal"
                or complete_before.revisions[-1].terminal_state
                != "service_state_rolled_back"
                or _receipt_count(store, complete_before) != 1
            ):
                raise AcceptanceFailure(stage)
            complete_decision = _preview_activation_recovery_with(
                plan_sha256,
                "rollback",
                snapshot_loader=store._load_recovery_snapshot,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            if (
                complete_decision.get("action_id") != "complete"
                or complete_decision.get("operation_id") != "none"
                or complete_decision.get("reason_id")
                != "journal_complete"
                or complete_decision.get("step_id") != "terminal"
            ):
                raise AcceptanceFailure(stage)

            stage = "receipt_recover_child_complete_zero_write"
            complete_store = _CountingRecoveryStore(store)
            complete_result = _run_confirmed_recovery_write_with(
                plan_sha256,
                "rollback",
                str(complete_decision["decision_sha256"]),
                store=complete_store,
                scanner=_process_death_scanner,
                systemd_reader=read_systemd_show,
            )
            complete_after = store._load_recovery_snapshot(plan_sha256)
            if (
                complete_store.revision_writes != 0
                or complete_store.receipt_writes != 0
                or complete_result.get("write_id") != "none"
                or complete_result.get("state")
                != "service_state_rolled_back"
                or complete_after != complete_before
                or complete_after.receipt is None
                or complete_after.receipt.receipt_sha256
                != receipt_sha256
                or _receipt_count(store, complete_after) != 1
            ):
                raise AcceptanceFailure(stage)

        stage = "receipt_recover_child_final_state"
        final_systemd = read_systemd_show(_process_death_scanner())
        if (
            _process_death_mutation_count(marker_path) != 1
            or final_systemd.active_state != "inactive"
            or final_systemd.unit_file_state != "disabled"
            or _enablement_links()
        ):
            raise AcceptanceFailure(stage)

        print(
            json.dumps(
                {
                    "completion_write_count": 0,
                    "decision_recomputed": True,
                    "final_state": "service_state_rolled_back",
                    "journal_reopened": True,
                    "ok": True,
                    "receipt_count": 1,
                    "receipt_rewritten": False,
                    "receipt_sha256_unchanged": True,
                    "systemd_mutation_performed": False,
                    "terminal_revision_appended": True,
                    "terminal_write_count": 1,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "failure_id": (
                        "receipt_process_death_recovery_failed"
                    ),
                    "ok": False,
                    "stage": stage,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1


def _run_receipt_process_death_gate(
    journal_root: Path,
    plan_sha256: str,
    confirmed_decision_sha256: str,
) -> dict[str, object]:
    marker_path = journal_root / PROCESS_DEATH_MUTATION_FILE
    if _process_death_mutation_count(marker_path) != 1:
        raise AcceptanceFailure(
            "receipt_process_death_mutation_precondition"
        )

    read_descriptor = -1
    write_descriptor = -1
    child: subprocess.Popen[bytes] | None = None
    try:
        read_descriptor, write_descriptor = os.pipe()
        child = subprocess.Popen(
            (
                sys.executable,
                str(Path(__file__).resolve()),
                RECEIPT_PROCESS_DEATH_EXECUTE_MODE,
                str(journal_root),
                str(marker_path),
                plan_sha256,
                confirmed_decision_sha256,
                str(write_descriptor),
            ),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=_process_death_environment(),
            close_fds=True,
            pass_fds=(write_descriptor,),
            start_new_session=True,
        )
        os.close(write_descriptor)
        write_descriptor = -1
        ready, _writable, _exceptional = select.select(
            (read_descriptor,),
            (),
            (),
            PROCESS_DEATH_CHILD_TIMEOUT_SECONDS,
        )
        if not ready:
            raise AcceptanceFailure(
                "receipt_process_death_marker_timeout"
            )
        marker = os.read(
            read_descriptor,
            len(RECEIPT_PROCESS_DEATH_PIPE_MARKER) + 1,
        )
        if (
            marker != RECEIPT_PROCESS_DEATH_PIPE_MARKER
            or child.poll() is not None
        ):
            raise AcceptanceFailure(
                "receipt_process_death_marker_invalid"
            )
        child.kill()
        return_code = child.wait(
            timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS
        )
        if return_code != -signal.SIGKILL:
            raise AcceptanceFailure(
                "receipt_process_death_sigkill_unproven"
            )
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure(
            "receipt_process_death_execution_failed"
        ) from None
    finally:
        for descriptor in (write_descriptor, read_descriptor):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if child is not None and child.poll() is None:
            try:
                child.kill()
                child.wait(
                    timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS
                )
            except Exception:
                pass

    if _process_death_mutation_count(marker_path) != 1:
        raise AcceptanceFailure(
            "receipt_process_death_unexpected_mutation"
        )
    with _open_fixture_store(journal_root) as checkpoint_store:
        checkpoint = checkpoint_store._load_recovery_snapshot(
            plan_sha256
        )
        last = checkpoint.revisions[-1]
        receipt = checkpoint.receipt
        if (
            receipt is None
            or last.phase != "observed"
            or last.step_id != "verify"
            or last.intent_id != "rollback_verify_requested"
            or last.observation_id != "rollback_verified"
            or last.owns_enable
            or last.owns_start
            or receipt.identity.plan_sha256 != plan_sha256
            or receipt.terminal_revision != last.revision + 1
            or receipt.previous_revision_sha256 != last.record_sha256
            or receipt.terminal_state != "service_state_rolled_back"
            or receipt.owns_enable
            or receipt.owns_start
            or _receipt_count(checkpoint_store, checkpoint) != 1
        ):
            raise AcceptanceFailure(
                "receipt_process_death_checkpoint_invalid"
            )
        checkpoint_revision_count = len(checkpoint.revisions)
        checkpoint_receipt_sha256 = receipt.receipt_sha256
        checkpoint_terminal_revision = receipt.terminal_revision

    try:
        recovered = subprocess.run(
            (
                sys.executable,
                str(Path(__file__).resolve()),
                RECEIPT_PROCESS_DEATH_RECOVER_MODE,
                str(journal_root),
                str(marker_path),
                plan_sha256,
                confirmed_decision_sha256,
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=_process_death_environment(),
            timeout=PROCESS_DEATH_CHILD_TIMEOUT_SECONDS,
        )
    except Exception:
        raise AcceptanceFailure(
            "receipt_process_death_recovery_child_failed"
        ) from None
    if (
        recovered.returncode != 0
        or not recovered.stdout
        or len(recovered.stdout) > MAX_PROCESS_DEATH_RESULT_BYTES
        or b"\x00" in recovered.stdout
        or b"\r" in recovered.stdout
    ):
        raise AcceptanceFailure(
            "receipt_process_death_recovery_child_failed"
        )
    try:
        recovery_result = json.loads(recovered.stdout.decode("ascii"))
    except Exception:
        raise AcceptanceFailure(
            "receipt_process_death_recovery_result_invalid"
        ) from None
    expected = {
        "completion_write_count": 0,
        "decision_recomputed": True,
        "final_state": "service_state_rolled_back",
        "journal_reopened": True,
        "ok": True,
        "receipt_count": 1,
        "receipt_rewritten": False,
        "receipt_sha256_unchanged": True,
        "systemd_mutation_performed": False,
        "terminal_revision_appended": True,
        "terminal_write_count": 1,
    }
    if recovery_result != expected:
        raise AcceptanceFailure(
            "receipt_process_death_recovery_result_invalid"
        )

    with _open_fixture_store(journal_root) as final_store:
        final_snapshot = final_store._load_recovery_snapshot(plan_sha256)
        if (
            final_snapshot.receipt is None
            or final_snapshot.receipt.receipt_sha256
            != checkpoint_receipt_sha256
            or len(final_snapshot.revisions)
            != checkpoint_revision_count + 1
            or final_snapshot.revisions[-1].phase != "terminal"
            or final_snapshot.revisions[-1].revision
            != checkpoint_terminal_revision
            or final_snapshot.revisions[-1].receipt_sha256
            != checkpoint_receipt_sha256
            or final_snapshot.revisions[-1].terminal_state
            != "service_state_rolled_back"
            or _receipt_count(final_store, final_snapshot) != 1
        ):
            raise AcceptanceFailure(
                "receipt_process_death_final_snapshot_invalid"
            )
    if _process_death_mutation_count(marker_path) != 1:
        raise AcceptanceFailure(
            "receipt_process_death_unexpected_mutation"
        )
    return {
        "checkpoint": "after_rollback_receipt_before_terminal",
        "child_exit_signal": "SIGKILL",
        **expected,
    }


def _run() -> dict[str, object]:
    if (
        os.environ.get(OPT_IN) != "1"
        or not sys.platform.startswith("linux")
        or os.geteuid() != 0
        or not Path("/run/systemd/system").is_dir()
        or os.path.lexists(UNIT_PATH)
        or os.path.lexists(ENABLEMENT_LINK_PATH)
    ):
        raise AcceptanceFailure("preflight")

    stage = "setup"
    systemctl: FileIdentity | None = None
    cleanup_ok = False
    forward_steps: list[str] = []
    rollback_steps: list[str] = []
    final_state = ""
    process_death_result: dict[str, object] = {}
    receipt_process_death_result: dict[str, object] = {}
    try:
        systemctl = _systemctl_identity()
        _write_unit()
        _run_bound_systemd_mutation(systemctl, "daemon_reload")
        _mark_unit_changed()
        unit = _file_identity(UNIT_PATH)
        base = replace(
            prerequisites(),
            unit=unit,
            systemctl=systemctl,
        )

        def scanner() -> ActivationPrerequisiteSnapshot:
            return replace(
                base,
                enablement_links=_enablement_links(),
            )

        stage = "initial_observation"
        initial_prerequisites = scanner()
        initial_systemd = read_systemd_show(initial_prerequisites)
        plan = compile_activation_plan(
            initial_prerequisites,
            initial_systemd,
        )
        if plan.ok is not True or plan.plan_sha256 is None:
            raise AcceptanceFailure
        identity = build_activation_journal_identity(
            initial_prerequisites,
            initial_systemd,
            confirmed_plan_sha256=plan.plan_sha256,
        )
        prepared = build_activation_revision(
            identity,
            revision=1,
            previous_revision_sha256=GENESIS_REVISION_SHA256,
            phase="prepared",
            step_id="transaction_open",
            owns_enable=False,
            owns_start=False,
        )

        with tempfile.TemporaryDirectory(
            prefix="relay-linux-systemd-recovery-"
        ) as temporary:
            journal_root = Path(temporary)
            journal_root.chmod(0o700)
            with ExitStack() as stores:
                store = stores.enter_context(
                    _open_fixture_store(journal_root)
                )
                store.publish_revision(prepared)

                stage = "process_death_preview"
                process_death_decision = (
                    _preview_activation_recovery_with(
                        plan.plan_sha256,
                        "resume",
                        snapshot_loader=store._load_recovery_snapshot,
                        scanner=scanner,
                        systemd_reader=read_systemd_show,
                    )
                )
                if (
                    process_death_decision.get("action_id") != "resume"
                    or process_death_decision.get("operation_id")
                    != "run_step"
                    or process_death_decision.get("reason_id")
                    != "resume_ready"
                    or process_death_decision.get("step_id")
                    != "daemon_reload"
                ):
                    raise AcceptanceFailure
                store.close()
                stage = "process_death_daemon_reload"
                process_death_result = _run_process_death_gate(
                    journal_root,
                    plan.plan_sha256,
                    str(
                        process_death_decision["decision_sha256"]
                    ),
                )
                forward_steps.append("daemon_reload")
                store = stores.enter_context(
                    _open_fixture_store(journal_root)
                )

                stage = "forward_execution"
                for _index in range(MAX_STEP_COUNT):
                    stage = "forward_preview"
                    decision = _preview_activation_recovery_with(
                        plan.plan_sha256,
                        "resume",
                        snapshot_loader=store._load_recovery_snapshot,
                        scanner=scanner,
                        systemd_reader=read_systemd_show,
                    )
                    operation = decision.get("operation_id")
                    if operation == "publish_success_receipt":
                        break
                    if operation != "run_step":
                        raise AcceptanceFailure
                    step_id = str(decision.get("step_id"))
                    if step_id not in {
                        "daemon_reload",
                        "enable",
                        "start",
                        "verify",
                    }:
                        raise AcceptanceFailure
                    stage = f"forward_{step_id}"
                    forward_steps.append(step_id)
                    _run_confirmed_recovery_step_with(
                        plan.plan_sha256,
                        "resume",
                        str(decision["decision_sha256"]),
                        store=store,
                        scanner=scanner,
                        systemd_reader=read_systemd_show,
                        mutation_runner=_run_bound_systemd_mutation,
                    )
                else:
                    raise AcceptanceFailure

                stage = "rollback_execution"
                for _index in range(MAX_STEP_COUNT):
                    stage = "rollback_preview"
                    try:
                        decision = _preview_activation_recovery_with(
                            plan.plan_sha256,
                            "rollback",
                            snapshot_loader=store._load_recovery_snapshot,
                            scanner=scanner,
                            systemd_reader=read_systemd_show,
                        )
                    except RelayActivationRecoveryPreviewError as exc:
                        raise AcceptanceFailure(
                            "rollback_preview_error_"
                            + exc.error_id
                        ) from None
                    operation = decision.get("operation_id")
                    if operation == "run_step":
                        step_id = str(decision.get("step_id"))
                        if step_id not in {
                            "rollback_stop",
                            "rollback_disable",
                            "verify",
                        }:
                            raise AcceptanceFailure
                        stage = f"rollback_{step_id}"
                        rollback_steps.append(step_id)
                        try:
                            _run_confirmed_recovery_step_with(
                                plan.plan_sha256,
                                "rollback",
                                str(decision["decision_sha256"]),
                                store=store,
                                scanner=scanner,
                                systemd_reader=read_systemd_show,
                                mutation_runner=_run_bound_systemd_mutation,
                            )
                        except Exception:
                            if step_id == "rollback_stop":
                                raise AcceptanceFailure(
                                    _diagnose_rollback_stop(systemctl)
                                ) from None
                            raise
                    else:
                        action_id = str(decision.get("action_id"))
                        expected_write = {
                            (
                                "inverse",
                                "publish_rollback_receipt",
                            ): "publish_rollback_receipt",
                            (
                                "terminalize",
                                "publish_terminal_revision",
                            ): "publish_terminal_revision",
                            ("complete", "none"): "complete",
                        }.get((action_id, str(operation)))
                        if expected_write is None:
                            reason_id = str(
                                decision.get("reason_id")
                            )
                            step_id = str(
                                decision.get("step_id")
                            )
                            bounded = {
                                "action_id": action_id,
                                "operation_id": str(operation),
                                "reason_id": reason_id,
                                "step_id": step_id,
                            }
                            allowed = {
                                "action_id": {
                                    "blocked",
                                    "complete",
                                    "inverse",
                                    "resume",
                                    "terminalize",
                                },
                                "operation_id": {
                                    "none",
                                    "publish_rollback_receipt",
                                    "publish_success_receipt",
                                    "publish_terminal_revision",
                                    "record_observation",
                                    "run_step",
                                },
                                "reason_id": {
                                    "journal_complete",
                                    "no_owned_change",
                                    "ownership_ambiguous",
                                    "ownership_unproven",
                                    "plan_binding_unproven",
                                    "receipt_ready",
                                    "resume_ready",
                                    "rollback_contract_incomplete",
                                    "state_drift",
                                },
                                "step_id": {
                                    "None",
                                    "daemon_reload",
                                    "enable",
                                    "rollback_disable",
                                    "rollback_stop",
                                    "start",
                                    "terminal",
                                    "verify",
                                },
                            }
                            if any(
                                bounded[key] not in allowed[key]
                                for key in bounded
                            ):
                                raise AcceptanceFailure(
                                    "rollback_preview_unexpected"
                                )
                            raise AcceptanceFailure(
                                "rollback_preview_"
                                + "_".join(
                                    bounded[key]
                                    for key in (
                                        "action_id",
                                        "operation_id",
                                        "reason_id",
                                        "step_id",
                                    )
                                )
                            )
                        stage = f"rollback_{expected_write}"
                        if expected_write == "publish_rollback_receipt":
                            store.close()
                            stage = "rollback_receipt_process_death"
                            receipt_process_death_result = (
                                _run_receipt_process_death_gate(
                                    journal_root,
                                    plan.plan_sha256,
                                    str(decision["decision_sha256"]),
                                )
                            )
                            store = stores.enter_context(
                                _open_fixture_store(journal_root)
                            )
                            final_state = str(
                                receipt_process_death_result.get(
                                    "final_state"
                                )
                            )
                            break
                        result = _run_confirmed_recovery_write_with(
                            plan.plan_sha256,
                            "rollback",
                            str(decision["decision_sha256"]),
                            store=store,
                            scanner=scanner,
                            systemd_reader=read_systemd_show,
                        )
                        if expected_write == "complete":
                            final_state = str(result.get("state"))
                            break
                else:
                    raise AcceptanceFailure

                final_systemd = read_systemd_show(scanner())
                snapshot = store._load_recovery_snapshot(
                    plan.plan_sha256
                )
                if (
                    final_state != "service_state_rolled_back"
                    or snapshot.revisions[-1].phase != "terminal"
                    or snapshot.revisions[-1].terminal_state
                    != "service_state_rolled_back"
                    or final_systemd.active_state != "inactive"
                    or final_systemd.unit_file_state != "disabled"
                    or _enablement_links()
                ):
                    raise AcceptanceFailure
        stage = "complete"
        return {
            "final_state": final_state,
            "forward_steps": forward_steps,
            "initial_reload_required": (
                initial_systemd.need_daemon_reload
            ),
            "journal_scope": "temporary_fixture",
            "linux_systemd": True,
            "network_used": False,
            "ok": True,
            "operation": "relay_linux_systemd_recovery_acceptance",
            "process_death": process_death_result,
            "receipt_process_death": receipt_process_death_result,
            "rollback_steps": rollback_steps,
            "stage": stage,
            "systemctl_bound": True,
        }
    except AcceptanceFailure as exc:
        raise AcceptanceFailure(
            stage if exc.stage == "unknown" else exc.stage
        ) from None
    except Exception:
        raise AcceptanceFailure(stage) from None
    finally:
        cleanup_ok = _cleanup(systemctl)
        if not cleanup_ok:
            raise AcceptanceFailure("cleanup")


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] == PROCESS_DEATH_EXECUTE_MODE:
            return _process_death_execute_child(sys.argv[2:])
        if sys.argv[1] == PROCESS_DEATH_RECOVER_MODE:
            return _process_death_recover_child(sys.argv[2:])
        if sys.argv[1] == RECEIPT_PROCESS_DEATH_EXECUTE_MODE:
            return _receipt_process_death_execute_child(sys.argv[2:])
        if sys.argv[1] == RECEIPT_PROCESS_DEATH_RECOVER_MODE:
            return _receipt_process_death_recover_child(sys.argv[2:])
        return 1

    result: dict[str, object]
    try:
        result = _run()
        result["cleanup_ok"] = True
    except AcceptanceFailure as exc:
        result = {
            "cleanup_ok": (
                not os.path.lexists(UNIT_PATH)
                and not os.path.lexists(ENABLEMENT_LINK_PATH)
            ),
            "failure_id": "linux_systemd_acceptance_failed",
            "linux_systemd": sys.platform.startswith("linux"),
            "network_used": False,
            "ok": False,
            "operation": "relay_linux_systemd_recovery_acceptance",
            "stage": exc.stage,
        }
    except Exception:
        result = {
            "cleanup_ok": False,
            "failure_id": "linux_systemd_acceptance_failed",
            "linux_systemd": sys.platform.startswith("linux"),
            "network_used": False,
            "ok": False,
            "operation": "relay_linux_systemd_recovery_acceptance",
            "stage": "unknown",
        }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
