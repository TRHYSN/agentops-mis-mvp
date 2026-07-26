#!/usr/bin/env python3
"""Exercise the production Relay journal and daemon against real systemd."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    ENABLEMENT_LINK_PATH,
    UNIT_PATH,
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    build_activation_journal_identity,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    _open_locked_production_store,
    build_activation_revision,
)
from agentops_mis_cli.relay_activation_recovery_controller import (  # noqa: E402
    _run_confirmed_recovery_write_with,
)
from agentops_mis_cli.relay_activation_recovery_executor import (  # noqa: E402
    _run_confirmed_recovery_step_with,
)
from agentops_mis_cli.relay_activation_recovery_preview import (  # noqa: E402
    _preview_activation_recovery_with,
)
from agentops_mis_cli.relay_activation_scan import (  # noqa: E402
    _scan_activation_prerequisites_while_locked,
)
from agentops_mis_cli.relay_admin import (  # noqa: E402
    RelayAdminError,
    _plan_for_install,
    _publish_install,
    inspect_bundle,
    relay_status,
)
from agentops_mis_cli.relay_systemd_mutation import (  # noqa: E402
    _run_bound_systemd_mutation,
)
from agentops_mis_cli.relay_systemd_read import (  # noqa: E402
    read_systemd_show,
)
from scripts.relay_linux_production_install_acceptance import (  # noqa: E402
    ABSENT_PATHS,
    CONFIG_ROOT,
    INSTALL_PLAN_ERROR_IDS,
    RUNTIME_ROOT,
    STABLE_LAUNCHER,
    STATE_ROOT,
    UNIT_SOURCE,
    ParentModeSnapshot,
    _account_present,
    _bundle_input,
    _canonical_json,
    _cleanup,
    _create_service_account,
    _harden_install_parents,
    _mkdir_owned,
    _write_owned,
)
from scripts.relay_linux_systemd_recovery_acceptance import (  # noqa: E402
    _diagnose_rollback_stop,
    _systemctl_identity,
)


OPT_IN = "AGENTOPS_RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE"
BROWSER_PORT = 18443
CONNECTOR_PORT = 19443
MAX_STEP_COUNT = 16
MAX_TLS_BYTES = 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
OPENSSL_PATHS = (Path("/usr/bin/openssl"), Path("/bin/openssl"))
JOURNALCTL_PATHS = (Path("/usr/bin/journalctl"), Path("/bin/journalctl"))
RUNUSER_PATH = Path("/usr/sbin/runuser")
RELAY_SERVICE_USER = "agentops-relay"
RELAY_JOURNAL_ERROR_IDS = frozenset(
    {
        "browser_acceptor_start_failed",
        "connector_acceptor_start_failed",
        "file_parent_untrusted",
        "relay_acceptor_unavailable",
        "relay_instance_active",
        "relay_instance_lock_rejected",
        "relay_socket_failed",
        "state_lock_failed",
        "state_lock_rejected",
        "state_write_failed",
    }
)


class AcceptanceFailure(Exception):
    def __init__(self, stage: str = "unknown") -> None:
        self.stage = stage
        super().__init__("linux_production_systemd_acceptance_failed")


def _command_result(argv: tuple[str, ...]) -> int | None:
    try:
        result = subprocess.run(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return None
    return result.returncode


def _trusted_runuser_path() -> Path | None:
    try:
        metadata = os.lstat(RUNUSER_PATH)
    except OSError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & 0o111
    ):
        return None
    return RUNUSER_PATH


def _run_as_relay_service(
    argv: tuple[str, ...],
    *,
    capture_stdout: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes] | None:
    runuser = _trusted_runuser_path()
    if runuser is None:
        return None
    try:
        return subprocess.run(
            (
                str(runuser),
                "--user",
                RELAY_SERVICE_USER,
                "--",
                *argv,
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=timeout,
        )
    except Exception:
        return None


def _openssl_path() -> Path:
    for path in OPENSSL_PATHS:
        try:
            metadata = os.lstat(path)
        except OSError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == 0
            and not stat.S_IMODE(metadata.st_mode) & 0o022
            and stat.S_IMODE(metadata.st_mode) & 0o111
        ):
            return path
    raise AcceptanceFailure("preflight")


def _port_available(port: int) -> bool:
    listener: socket.socket | None = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        if listener is not None:
            listener.close()


def _preflight() -> Path:
    if (
        os.environ.get(OPT_IN) != "1"
        or not sys.platform.startswith("linux")
        or os.geteuid() != 0
        or not Path("/run/systemd/system").is_dir()
        or _account_present()
        or any(os.path.lexists(path) for path in ABSENT_PATHS)
        or not UNIT_SOURCE.is_file()
        or UNIT_SOURCE.is_symlink()
        or not _port_available(BROWSER_PORT)
        or not _port_available(CONNECTOR_PORT)
    ):
        raise AcceptanceFailure("preflight")
    return _openssl_path()


def _generate_tls_material(openssl: Path) -> tuple[bytes, bytes]:
    try:
        with tempfile.TemporaryDirectory(
            prefix="agentops-relay-production-tls-"
        ) as temporary:
            directory = Path(temporary)
            certificate = directory / "relay-cert.pem"
            private_key = directory / "relay-key.pem"
            result = subprocess.run(
                (
                    str(openssl),
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=acceptance.example.invalid",
                    "-addext",
                    "subjectAltName=DNS:acceptance.example.invalid",
                    "-keyout",
                    str(private_key),
                    "-out",
                    str(certificate),
                ),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                timeout=30,
            )
            certificate_bytes = certificate.read_bytes()
            private_key_bytes = private_key.read_bytes()
            if (
                result.returncode != 0
                or not certificate_bytes
                or len(certificate_bytes) > MAX_TLS_BYTES
                or not private_key_bytes
                or len(private_key_bytes) > MAX_TLS_BYTES
            ):
                raise AcceptanceFailure("runtime_provisioning")
            return certificate_bytes, private_key_bytes
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure("runtime_provisioning") from None


def _provision_runtime_material(
    uid: int,
    gid: int,
    openssl: Path,
) -> None:
    certificate, private_key = _generate_tls_material(openssl)
    _mkdir_owned(CONFIG_ROOT, 0o755, 0, 0)
    _mkdir_owned(CONFIG_ROOT / "tls", 0o755, 0, 0)
    _mkdir_owned(CONFIG_ROOT / "routes", 0o755, 0, 0)
    _mkdir_owned(STATE_ROOT, 0o700, uid, gid)
    _mkdir_owned(RUNTIME_ROOT, 0o700, uid, gid)

    config = {
        "browser_listen": {"host": "127.0.0.1", "port": BROWSER_PORT},
        "connector_listen": {
            "host": "127.0.0.1",
            "port": CONNECTOR_PORT,
        },
        "connector_tls": {
            "cert_file": "/etc/agentops-mis-relay/tls/relay-cert.pem",
            "key_file": "/etc/agentops-mis-relay/tls/relay-key.pem",
        },
        "routes": [
            {
                "hostname": "acceptance.example.invalid",
                "key_file": (
                    "/etc/agentops-mis-relay/routes/acceptance.key"
                ),
                "route": "rte_linux_production_systemd",
            }
        ],
        "schema_version": 1,
        "state_path": "/var/lib/agentops-mis-relay/epochs.json",
        "status_path": "/run/agentops-mis-relay/status.json",
    }
    route_key = hashlib.sha256(
        b"agentops relay linux production systemd acceptance"
    ).hexdigest().encode("ascii") + b"\n"
    _write_owned(
        CONFIG_ROOT / "config.json",
        _canonical_json(config),
        0o640,
        0,
        gid,
    )
    _write_owned(
        CONFIG_ROOT / "tls" / "relay-cert.pem",
        certificate,
        0o640,
        0,
        gid,
    )
    _write_owned(
        CONFIG_ROOT / "tls" / "relay-key.pem",
        private_key,
        0o600,
        uid,
        gid,
    )
    _write_owned(
        CONFIG_ROOT / "routes" / "acceptance.key",
        route_key,
        0o600,
        uid,
        gid,
    )


def _mark_unit_stale() -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            UNIT_PATH,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        expected = UNIT_SOURCE.read_bytes()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != 0o644
            or before.st_nlink != 1
            or before.st_size != len(expected)
        ):
            raise AcceptanceFailure("systemd_setup")
        payload = bytearray()
        while len(payload) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(payload))
            if not chunk:
                raise AcceptanceFailure("systemd_setup")
            payload.extend(chunk)
        if (
            bytes(payload) != expected
            or os.read(descriptor, 1)
        ):
            raise AcceptanceFailure("systemd_setup")
        os.utime(
            descriptor,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        after = os.fstat(descriptor)
        current = os.lstat(UNIT_PATH)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_mtime_ns == before.st_mtime_ns
            or current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
            or stat.S_IFMT(current.st_mode) != stat.S_IFMT(before.st_mode)
            or stat.S_IMODE(current.st_mode) != stat.S_IMODE(before.st_mode)
            or current.st_uid != before.st_uid
            or current.st_gid != before.st_gid
            or current.st_nlink != before.st_nlink
            or current.st_size != before.st_size
        ):
            raise AcceptanceFailure("systemd_setup")
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure("systemd_setup") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _relay_status_ready() -> bool:
    status_command = (
        str(STABLE_LAUNCHER),
        "status",
        "--config",
        str(CONFIG_ROOT / "config.json"),
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        result = _run_as_relay_service(status_command)
        if result is not None and result.returncode == 0:
            return True
        time.sleep(0.1)
    return False


def _relay_status_projection() -> tuple[int | None, dict[str, object] | None]:
    result = _run_as_relay_service(
        (
            str(STABLE_LAUNCHER),
            "status",
            "--config",
            str(CONFIG_ROOT / "config.json"),
        ),
        capture_stdout=True,
        timeout=5,
    )
    if result is None:
        return None, None
    try:
        if not result.stdout or len(result.stdout) > MAX_DIAGNOSTIC_BYTES:
            return result.returncode, None
        payload = json.loads(result.stdout.decode("utf-8"))
        if not isinstance(payload, dict):
            return result.returncode, None
        return result.returncode, payload
    except Exception:
        return None, None


def _relay_journal_error() -> str | None:
    journalctl: Path | None = None
    for path in JOURNALCTL_PATHS:
        try:
            metadata = os.lstat(path)
        except OSError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == 0
            and not stat.S_IMODE(metadata.st_mode) & 0o022
            and stat.S_IMODE(metadata.st_mode) & 0o111
        ):
            journalctl = path
            break
    if journalctl is None:
        return None
    try:
        result = subprocess.run(
            (
                str(journalctl),
                "--no-pager",
                "--output=cat",
                "--unit=agentops-mis-relay.service",
                "--lines=30",
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=5,
        )
        if (
            result.returncode != 0
            or not result.stdout
            or len(result.stdout) > MAX_DIAGNOSTIC_BYTES
        ):
            return None
        for line in reversed(result.stdout.decode("utf-8").splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            error_id = payload.get("error")
            if (
                payload.get("operation") == "relay_serve"
                and isinstance(error_id, str)
                and error_id in RELAY_JOURNAL_ERROR_IDS
            ):
                return error_id
    except Exception:
        return None
    return None


def _diagnose_relay_status(systemctl_path: str) -> str:
    status_code, projection = _relay_status_projection()
    if status_code == 0 and projection is not None:
        return "relay_status_diagnostic_ready_late"

    command = (systemctl_path, "--system")
    active = (
        _command_result(
            (*command, "is-active", "agentops-mis-relay.service")
        )
        == 0
    )
    failed = (
        _command_result(
            (*command, "is-failed", "agentops-mis-relay.service")
        )
        == 0
    )
    journal_error = _relay_journal_error()
    if journal_error is not None:
        return f"relay_status_diagnostic_journal_{journal_error}"
    state_id = "active" if active else ("failed" if failed else "inactive")
    if projection is None:
        return f"relay_status_diagnostic_{state_id}_projection_invalid"
    if projection.get("status_fresh") is False:
        return f"relay_status_diagnostic_{state_id}_stale"
    if projection.get("pid_alive") is False:
        return f"relay_status_diagnostic_{state_id}_pid_unavailable"
    if projection.get("ready") is False:
        return f"relay_status_diagnostic_{state_id}_not_ready"
    if _port_available(BROWSER_PORT):
        return f"relay_status_diagnostic_{state_id}_browser_unbound"
    if _port_available(CONNECTOR_PORT):
        return f"relay_status_diagnostic_{state_id}_connector_unbound"
    return f"relay_status_diagnostic_{state_id}_status_rejected"


def _diagnose_forward_start(systemctl_path: str) -> str:
    command = (systemctl_path, "--system")
    if (
        _command_result(
            (*command, "is-active", "agentops-mis-relay.service")
        )
        == 0
    ):
        return "forward_start_diagnostic_active_late_state"
    check_command = (
        str(STABLE_LAUNCHER),
        "check",
        "--config",
        str(CONFIG_ROOT / "config.json"),
    )
    if _command_result(check_command) != 0:
        return "forward_start_diagnostic_root_check"
    if _trusted_runuser_path() is None:
        return "forward_start_diagnostic_runuser"
    service_check = _run_as_relay_service(check_command)
    if service_check is None or service_check.returncode != 0:
        return "forward_start_diagnostic_service_check"
    if not _port_available(BROWSER_PORT):
        return "forward_start_diagnostic_browser_port"
    if not _port_available(CONNECTOR_PORT):
        return "forward_start_diagnostic_connector_port"
    if (
        _command_result(
            (*command, "is-failed", "agentops-mis-relay.service")
        )
        == 0
    ):
        return "forward_start_diagnostic_failed_state"
    return "forward_start_diagnostic_inactive_state"


def _unexpected_decision_stage(
    prefix: str,
    decision: dict[str, object],
) -> str:
    bounded = {
        "action": str(decision.get("action_id")),
        "operation": str(decision.get("operation_id")),
        "reason": str(decision.get("reason_id")),
        "step": str(decision.get("step_id")),
    }
    allowed = {
        "action": {
            "blocked",
            "complete",
            "inverse",
            "resume",
            "terminalize",
        },
        "operation": {
            "none",
            "publish_rollback_receipt",
            "publish_success_receipt",
            "publish_terminal_revision",
            "record_observation",
            "run_step",
        },
        "reason": {
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
        "step": {
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
    if (
        prefix not in {"forward", "rollback"}
        or any(bounded[key] not in allowed[key] for key in bounded)
    ):
        return "decision_diagnostic_invalid"
    return (
        f"{prefix}_decision_"
        + "_".join(
            bounded[key]
            for key in ("action", "operation", "reason", "step")
        )
    )


def _plan_binding_diagnostic(
    initial_prerequisites,
    initial_systemd,
    plan_sha256: str,
    store_open_count: list[int],
) -> str:
    """Return only the first bounded identity category that drifted."""

    store_open_count[0] += 1
    with _open_locked_production_store(Path("/")) as store:
        capability = store._activation_scan_capability()
        current = _scan_activation_prerequisites_while_locked(capability)

    categories = (
        (
            "install_flags",
            (
                initial_prerequisites.root,
                initial_prerequisites.installed_state,
                initial_prerequisites.service_account_ready,
                initial_prerequisites.config_ready,
                initial_prerequisites.tls_material_ready,
                initial_prerequisites.route_keys_ready,
                initial_prerequisites.recovery_required,
            ),
            (
                current.root,
                current.installed_state,
                current.service_account_ready,
                current.config_ready,
                current.tls_material_ready,
                current.route_keys_ready,
                current.recovery_required,
            ),
        ),
        (
            "release_tree",
            (
                initial_prerequisites.release_id,
                initial_prerequisites.version_id,
                initial_prerequisites.release_tree_sha256,
            ),
            (
                current.release_id,
                current.version_id,
                current.release_tree_sha256,
            ),
        ),
        ("unit", initial_prerequisites.unit, current.unit),
        ("config", initial_prerequisites.config, current.config),
        (
            "certificate",
            initial_prerequisites.certificate,
            current.certificate,
        ),
        (
            "private_key",
            initial_prerequisites.private_key,
            current.private_key,
        ),
        ("route_keys", initial_prerequisites.route_keys, current.route_keys),
        (
            "state_directory",
            initial_prerequisites.state_directory,
            current.state_directory,
        ),
        (
            "runtime_directory",
            initial_prerequisites.runtime_directory,
            current.runtime_directory,
        ),
        (
            "trusted_parent_chain",
            initial_prerequisites.trusted_parent_chain_sha256,
            current.trusted_parent_chain_sha256,
        ),
        (
            "service_identity",
            (
                initial_prerequisites.service_uid,
                initial_prerequisites.service_gid,
                initial_prerequisites.service_group_ids,
            ),
            (
                current.service_uid,
                current.service_gid,
                current.service_group_ids,
            ),
        ),
        ("systemctl", initial_prerequisites.systemctl, current.systemctl),
    )
    for category, initial_value, current_value in categories:
        if initial_value != current_value:
            return f"forward_plan_binding_{category}"

    reconstructed = compile_activation_plan(
        replace(
            current,
            enablement_links=initial_prerequisites.enablement_links,
        ),
        initial_systemd,
    )
    if (
        reconstructed.ok is not True
        or reconstructed.plan_sha256 != plan_sha256
    ):
        return "forward_plan_binding_systemd_reconstruction"
    return "forward_plan_binding_unclassified"


def _preview_once(
    plan_sha256: str,
    requested_outcome: str,
    store_open_count: list[int],
) -> dict[str, object]:
    store_open_count[0] += 1
    with _open_locked_production_store(Path("/")) as store:
        capability = store._activation_scan_capability()
        return _preview_activation_recovery_with(
            plan_sha256,
            requested_outcome,
            snapshot_loader=store._load_recovery_snapshot,
            scanner=lambda: _scan_activation_prerequisites_while_locked(
                capability
            ),
            systemd_reader=read_systemd_show,
        )


def _run_step_once(
    plan_sha256: str,
    requested_outcome: str,
    decision_sha256: str,
    store_open_count: list[int],
) -> dict[str, object]:
    store_open_count[0] += 1
    with _open_locked_production_store(Path("/")) as store:
        capability = store._activation_scan_capability()
        return _run_confirmed_recovery_step_with(
            plan_sha256,
            requested_outcome,
            decision_sha256,
            store=store,
            scanner=lambda: _scan_activation_prerequisites_while_locked(
                capability
            ),
            systemd_reader=read_systemd_show,
            mutation_runner=_run_bound_systemd_mutation,
        )


def _run_write_once(
    plan_sha256: str,
    requested_outcome: str,
    decision_sha256: str,
    store_open_count: list[int],
) -> dict[str, object]:
    store_open_count[0] += 1
    with _open_locked_production_store(Path("/")) as store:
        capability = store._activation_scan_capability()
        return _run_confirmed_recovery_write_with(
            plan_sha256,
            requested_outcome,
            decision_sha256,
            store=store,
            scanner=lambda: _scan_activation_prerequisites_while_locked(
                capability
            ),
            systemd_reader=read_systemd_show,
        )


def _owned_unit_and_link() -> bool:
    if not os.path.lexists(UNIT_PATH):
        return not os.path.lexists(ENABLEMENT_LINK_PATH)
    try:
        metadata = os.lstat(UNIT_PATH)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o644
            or metadata.st_nlink != 1
            or metadata.st_size != len(UNIT_SOURCE.read_bytes())
            or Path(UNIT_PATH).read_bytes() != UNIT_SOURCE.read_bytes()
        ):
            return False
        if os.path.lexists(ENABLEMENT_LINK_PATH):
            link = os.lstat(ENABLEMENT_LINK_PATH)
            if (
                not stat.S_ISLNK(link.st_mode)
                or Path(ENABLEMENT_LINK_PATH).resolve()
                != Path(UNIT_PATH)
            ):
                return False
    except OSError:
        return False
    return True


def _quiesce_systemd(systemctl_path: str | None) -> bool:
    if not _owned_unit_and_link():
        return False
    if not os.path.lexists(UNIT_PATH):
        return True
    command = systemctl_path or "/usr/bin/systemctl"
    _command_result(
        (command, "--system", "stop", "agentops-mis-relay.service")
    )
    _command_result(
        (command, "--system", "disable", "agentops-mis-relay.service")
    )
    active = _command_result(
        (command, "--system", "is-active", "agentops-mis-relay.service")
    )
    return active not in {0, None} and not os.path.lexists(
        ENABLEMENT_LINK_PATH
    )


def _reload_after_cleanup(systemctl_path: str | None) -> bool:
    command = systemctl_path or "/usr/bin/systemctl"
    reload_code = _command_result((command, "--system", "daemon-reload"))
    _command_result(
        (
            command,
            "--system",
            "reset-failed",
            "agentops-mis-relay.service",
        )
    )
    return reload_code == 0


def _run() -> dict[str, object]:
    openssl = _preflight()
    bundle_path, bundle_sha256 = _bundle_input()
    stage = "install_parent_hardening"
    account_created = False
    parent_modes: tuple[ParentModeSnapshot, ...] = ()
    systemctl_path: str | None = None
    forward_steps: list[str] = []
    rollback_steps: list[str] = []
    recovered_late_observation_steps: list[str] = []
    store_open_count = [0]
    final_state = ""
    relay_started = False
    initial_reload_required = False
    try:
        parent_modes = _harden_install_parents()

        stage = "account_provisioning"
        uid, gid = _create_service_account()
        account_created = True

        stage = "offline_bundle_inspect"
        bundle = inspect_bundle(bundle_path, bundle_sha256)
        stage = "offline_install_plan"
        try:
            install_plan = _plan_for_install(Path("/"), bundle)
        except RelayAdminError as exc:
            diagnostic = (
                exc.error_id
                if exc.error_id in INSTALL_PLAN_ERROR_IDS
                else "other"
            )
            raise AcceptanceFailure(
                f"offline_install_plan_{diagnostic}"
            ) from None
        if install_plan.no_op:
            raise AcceptanceFailure(stage)

        stage = "offline_install_publish"
        _publish_install(install_plan)

        stage = "runtime_provisioning"
        _provision_runtime_material(uid, gid, openssl)
        status, status_code = relay_status(Path("/"))
        if (
            status_code != 0
            or status.get("state_id") != "installed_valid"
            or status.get("installed") is not True
        ):
            raise AcceptanceFailure(stage)

        stage = "systemd_setup"
        systemctl = _systemctl_identity()
        systemctl_path = systemctl.canonical_path
        _run_bound_systemd_mutation(systemctl, "daemon_reload")
        _mark_unit_stale()

        stage = "prepared_publish"
        store_open_count[0] += 1
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            scanner = lambda: _scan_activation_prerequisites_while_locked(
                capability
            )
            initial_prerequisites = scanner()
            initial_systemd = read_systemd_show(initial_prerequisites)
            activation_plan = compile_activation_plan(
                initial_prerequisites,
                initial_systemd,
            )
            if (
                activation_plan.ok is not True
                or activation_plan.plan_sha256 is None
                or activation_plan.daemon_reload is not True
                or initial_systemd.active_state != "inactive"
                or initial_systemd.unit_file_state != "disabled"
            ):
                raise AcceptanceFailure(stage)
            initial_reload_required = initial_systemd.need_daemon_reload
            identity = build_activation_journal_identity(
                initial_prerequisites,
                initial_systemd,
                confirmed_plan_sha256=activation_plan.plan_sha256,
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
            store.publish_revision(prepared)
            plan_sha256 = activation_plan.plan_sha256

        stage = "forward_execution"
        for _index in range(MAX_STEP_COUNT):
            stage = "forward_preview"
            decision = _preview_once(
                plan_sha256,
                "resume",
                store_open_count,
            )
            operation = str(decision.get("operation_id"))
            if operation == "publish_success_receipt":
                break
            if operation != "run_step":
                if (
                    decision.get("action_id") == "blocked"
                    and decision.get("operation_id") == "none"
                    and decision.get("reason_id") == "plan_binding_unproven"
                    and decision.get("step_id") == "start"
                ):
                    raise AcceptanceFailure(
                        _plan_binding_diagnostic(
                            initial_prerequisites,
                            initial_systemd,
                            plan_sha256,
                            store_open_count,
                        )
                    )
                raise AcceptanceFailure(
                    _unexpected_decision_stage("forward", decision)
                )
            step_id = str(decision.get("step_id"))
            if step_id not in {
                "daemon_reload",
                "enable",
                "start",
                "verify",
            }:
                raise AcceptanceFailure(
                    _unexpected_decision_stage("forward", decision)
                )
            stage = f"forward_{step_id}"
            try:
                _run_step_once(
                    plan_sha256,
                    "resume",
                    str(decision["decision_sha256"]),
                    store_open_count,
                )
            except Exception:
                if step_id == "start":
                    raise AcceptanceFailure(
                        _diagnose_forward_start(systemctl_path)
                    ) from None
                raise
            forward_steps.append(step_id)
        else:
            raise AcceptanceFailure("forward_limit")
        if forward_steps != ["daemon_reload", "enable", "start", "verify"]:
            raise AcceptanceFailure("forward_sequence")

        stage = "relay_status"
        relay_started = _relay_status_ready()
        if not relay_started:
            raise AcceptanceFailure(
                _diagnose_relay_status(systemctl_path)
            )

        stage = "rollback_execution"
        for _index in range(MAX_STEP_COUNT):
            stage = "rollback_preview"
            decision = _preview_once(
                plan_sha256,
                "rollback",
                store_open_count,
            )
            operation = str(decision.get("operation_id"))
            if operation == "run_step":
                step_id = str(decision.get("step_id"))
                if step_id not in {
                    "rollback_stop",
                    "rollback_disable",
                    "verify",
                }:
                    raise AcceptanceFailure(
                        _unexpected_decision_stage("rollback", decision)
                    )
                stage = f"rollback_{step_id}"
                try:
                    _run_step_once(
                        plan_sha256,
                        "rollback",
                        str(decision["decision_sha256"]),
                        store_open_count,
                    )
                except Exception:
                    if step_id == "rollback_stop":
                        diagnostic = _diagnose_rollback_stop(systemctl)
                        if diagnostic == (
                            "rollback_rollback_stop_diagnostic_"
                            "valid_late_state"
                        ):
                            continue
                        raise AcceptanceFailure(diagnostic) from None
                    raise
                rollback_steps.append(step_id)
                continue

            expected_write = {
                (
                    "inverse",
                    "record_observation",
                ): "record_observation",
                (
                    "inverse",
                    "publish_rollback_receipt",
                ): "publish_rollback_receipt",
                (
                    "terminalize",
                    "publish_terminal_revision",
                ): "publish_terminal_revision",
                ("complete", "none"): "complete",
            }.get((str(decision.get("action_id")), operation))
            if expected_write is None:
                raise AcceptanceFailure(
                    _unexpected_decision_stage("rollback", decision)
                )
            stage = f"rollback_{expected_write}"
            result = _run_write_once(
                plan_sha256,
                "rollback",
                str(decision["decision_sha256"]),
                store_open_count,
            )
            if expected_write == "record_observation":
                recovered_step = str(decision.get("step_id"))
                if (
                    recovered_step not in {
                        "rollback_stop",
                        "rollback_disable",
                        "verify",
                    }
                    or recovered_step in rollback_steps
                ):
                    raise AcceptanceFailure(
                        _unexpected_decision_stage("rollback", decision)
                    )
                rollback_steps.append(recovered_step)
                recovered_late_observation_steps.append(recovered_step)
            if expected_write == "complete":
                final_state = str(result.get("state"))
                break
        else:
            raise AcceptanceFailure("rollback_limit")
        if rollback_steps != [
            "rollback_stop",
            "rollback_disable",
            "verify",
        ]:
            raise AcceptanceFailure("rollback_sequence")

        stage = "final_verification"
        store_open_count[0] += 1
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            final_prerequisites = (
                _scan_activation_prerequisites_while_locked(capability)
            )
            final_systemd = read_systemd_show(final_prerequisites)
            snapshot = store._load_recovery_snapshot(plan_sha256)
            store_state = store.inspect_store()
            if (
                final_state != "service_state_rolled_back"
                or snapshot.revisions[-1].phase != "terminal"
                or snapshot.revisions[-1].terminal_state
                != "service_state_rolled_back"
                or final_systemd.active_state != "inactive"
                or final_systemd.unit_file_state != "disabled"
                or final_prerequisites.enablement_links
                or store_state.get("ok") is not True
                or store_state.get("state") != "ready"
                or store_state.get("completed_transaction_count") != 1
                or _relay_status_ready()
            ):
                raise AcceptanceFailure(stage)

        stage = "complete"
        return {
            "account_provisioned": True,
            "cleanup_ok": True,
            "external_network_used": False,
            "final_state": final_state,
            "forward_steps": forward_steps,
            "initial_reload_required": initial_reload_required,
            "installed_tree": True,
            "loopback_only": True,
            "ok": True,
            "operation": "relay_linux_production_systemd_acceptance",
            "production_journal": True,
            "production_store_reopen_boundaries": (
                store_open_count[0] - 1
            ),
            "real_relay_process_started": relay_started,
            "real_systemd": True,
            "recovered_late_observation_steps": (
                recovered_late_observation_steps
            ),
            "rollback_steps": rollback_steps,
            "stage": stage,
        }
    except AcceptanceFailure as exc:
        raise AcceptanceFailure(
            stage if exc.stage == "unknown" else exc.stage
        ) from None
    except Exception:
        raise AcceptanceFailure(stage) from None
    finally:
        quiesced = _quiesce_systemd(systemctl_path)
        product_cleanup = _cleanup(
            account_created=account_created or _account_present(),
            parent_modes=parent_modes,
        )
        reloaded = (
            _reload_after_cleanup(systemctl_path)
            if product_cleanup
            else False
        )
        if not (quiesced and product_cleanup and reloaded):
            raise AcceptanceFailure("cleanup")


def main() -> int:
    try:
        result = _run()
    except AcceptanceFailure as exc:
        result = {
            "cleanup_ok": (
                exc.stage != "cleanup"
                and not _account_present()
                and not any(os.path.lexists(path) for path in ABSENT_PATHS)
            ),
            "external_network_used": False,
            "failure_id": "linux_production_systemd_acceptance_failed",
            "loopback_only": True,
            "ok": False,
            "operation": "relay_linux_production_systemd_acceptance",
            "stage": exc.stage,
        }
    except Exception:
        result = {
            "cleanup_ok": False,
            "external_network_used": False,
            "failure_id": "linux_production_systemd_acceptance_failed",
            "loopback_only": True,
            "ok": False,
            "operation": "relay_linux_production_systemd_acceptance",
            "stage": "unknown",
        }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
