"""Foreground lifecycle entrypoint for the disabled-by-default Relay connector."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import ssl
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

from agentops_mis_cli.relay_connector_supervisor import RelayConnectorSupervisor
from agentops_mis_cli.relay_epoch_store import PersistentRelayEpochStore
from agentops_mis_cli.relay_host_tls_proxy import HostTlsProxy, bind_loopback_listener


MAX_PRIVATE_JSON_BYTES = 16 * 1024
MAX_TLS_FILE_BYTES = 1024 * 1024
SERVICE_SCHEMA_VERSION = 1
_stop = threading.Event()
_SAFE_FAILURE_CODES = {
    "config_port_invalid",
    "config_schema_invalid",
    "config_value_invalid",
    "disabled_config_shape_invalid",
    "enabled_config_shape_invalid",
    "enabled_config_upgrade_required",
    "host_certificate_hostname_mismatch",
    "host_certificate_key_mismatch",
    "host_tls_proxy_start_failed",
    "host_tls_proxy_unavailable",
    "private_directory_invalid",
    "private_directory_permissions_invalid",
    "private_directory_unavailable",
    "private_json_invalid",
    "private_json_shape_invalid",
    "private_json_unreadable",
    "ready_signal_failed",
    "relay_ca_invalid",
    "secrets_shape_invalid",
    "service_stop_failed",
    "status_target_invalid",
    "status_target_unavailable",
    "supervisor_start_failed",
    "tls_file_invalid",
    "tls_file_unavailable",
    "tunnel_key_invalid",
}


class RelayConnectorServiceError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _private_directory(path: Path, *, create: bool = False) -> None:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise RelayConnectorServiceError("private_directory_unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise RelayConnectorServiceError("private_directory_invalid")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RelayConnectorServiceError("private_directory_permissions_invalid")


def _read_private_json(path: Path) -> dict[str, Any]:
    path = _absolute(path)
    _private_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_PRIVATE_JSON_BYTES
        ):
            raise RelayConnectorServiceError("private_json_invalid")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except RelayConnectorServiceError:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ValueError, TypeError) as exc:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        raise RelayConnectorServiceError("private_json_unreadable") from exc
    if not isinstance(payload, dict):
        raise RelayConnectorServiceError("private_json_shape_invalid")
    return payload


def _validate_tls_file(path_value: str, *, private_key: bool = False) -> Path:
    path = _absolute(Path(path_value))
    _private_directory(path.parent)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RelayConnectorServiceError("tls_file_unavailable") from exc
    allowed_modes = {0o600} if private_key else {0o600, 0o644}
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) not in allowed_modes
        or metadata.st_size <= 0
        or metadata.st_size > MAX_TLS_FILE_BYTES
    ):
        raise RelayConnectorServiceError("tls_file_invalid")
    return path


def _validate_certificate_hostname(certificate_path: Path, hostname: str) -> None:
    try:
        decoded = ssl._ssl._test_decode_cert(str(certificate_path))  # type: ignore[attr-defined]
        subject_alt_names = decoded.get("subjectAltName") or []
        dns_names = {
            str(value).rstrip(".").lower()
            for kind, value in subject_alt_names
            if kind == "DNS" and isinstance(value, str)
        }
    except (OSError, ValueError, TypeError) as exc:
        raise RelayConnectorServiceError("host_certificate_hostname_mismatch") from exc
    if hostname.rstrip(".").lower() not in dns_names:
        raise RelayConnectorServiceError("host_certificate_hostname_mismatch")


def _acquire_service_lock(status_path: Path) -> int:
    status_path = _absolute(status_path)
    _private_directory(status_path.parent, create=True)
    lock_path = status_path.with_name(f".{status_path.name}.lock")
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RelayConnectorServiceError("service_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RelayConnectorServiceError("service_already_running") from exc
        return descriptor
    except Exception:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _write_private_status(path: Path, payload: dict[str, Any]) -> None:
    path = _absolute(path)
    _private_directory(path.parent, create=True)
    try:
        target_metadata = path.lstat()
    except FileNotFoundError:
        target_metadata = None
    except OSError as exc:
        raise RelayConnectorServiceError("status_target_unavailable") from exc
    if target_metadata is not None and (
        path.is_symlink()
        or not stat.S_ISREG(target_metadata.st_mode)
        or target_metadata.st_uid != os.getuid()
        or stat.S_IMODE(target_metadata.st_mode) != 0o600
    ):
        raise RelayConnectorServiceError("status_target_invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _disabled_status(*, managed_by_host_stack: bool = False) -> dict[str, Any]:
    return {
        "connect_attempts": 0,
        "current_epoch": None,
        "enabled": False,
        "events": [],
        "failure_code": None,
        "limitations": {
            "certificate_lifecycle": False,
            "deployed_relay": False,
            "host_lifecycle_integrated": managed_by_host_stack,
            "sni_routing": False,
            "tailscale_changed": False,
        },
        "ok": True,
        "operation": "relay_connector_service",
        "host_tls_failure_code": None,
        "host_tls_ready": False,
        "host_tls_state": "disabled",
        "host_lifecycle_integrated": managed_by_host_stack,
        "relay_tls_enabled": False,
        "state": "disabled",
        "successful_connections": 0,
        "token_omitted": True,
    }


def _failed_status(code: str, *, managed_by_host_stack: bool = False) -> dict[str, Any]:
    payload = _disabled_status(managed_by_host_stack=managed_by_host_stack)
    payload.update({
        "enabled": True,
        "failure_code": code,
        "ok": False,
        "state": "failed",
    })
    return payload


def _service_status(
    supervisor: RelayConnectorSupervisor,
    host_tls_proxy: HostTlsProxy,
    *,
    managed_by_host_stack: bool = False,
) -> dict[str, Any]:
    status = supervisor.status()
    host_tls_status = host_tls_proxy.status()
    return {
        "connect_attempts": status["connect_attempts"],
        "current_epoch": status["current_epoch"],
        "enabled": status["enabled"],
        "events": status["events"],
        "failure_code": status["failure_code"],
        "limitations": {
            "certificate_lifecycle": False,
            "deployed_relay": False,
            "host_lifecycle_integrated": managed_by_host_stack,
            "sni_routing": False,
            "tailscale_changed": False,
        },
        "ok": status["state"] in {
            "connecting",
            "connected",
            "backoff",
            "stopped",
        },
        "operation": "relay_connector_service",
        "host_tls_accepted_connections": host_tls_status["accepted_connections"],
        "host_tls_active_connections": host_tls_status["active_connections"],
        "host_tls_failure_code": host_tls_status["failure_code"],
        "host_tls_failure_counts": host_tls_status["failure_counts"],
        "host_tls_handshake_failure_counts": host_tls_status[
            "handshake_failure_counts"
        ],
        "host_tls_ready": host_tls_status["ready"],
        "host_tls_rejected_connections": host_tls_status["rejected_connections"],
        "host_tls_state": host_tls_status["state"],
        "host_lifecycle_integrated": managed_by_host_stack,
        "relay_tls_enabled": status["relay_tls_enabled"],
        "state": status["state"],
        "successful_connections": status["successful_connections"],
        "token_omitted": True,
    }


def load_connector_config(config_path: Path) -> dict[str, Any]:
    config = _read_private_json(config_path)
    if config.get("schema_version") != SERVICE_SCHEMA_VERSION:
        raise RelayConnectorServiceError("config_schema_invalid")
    enabled = config.get("enabled")
    if enabled is False:
        if set(config) != {"enabled", "schema_version"}:
            raise RelayConnectorServiceError("disabled_config_shape_invalid")
        return config
    required = {
        "enabled",
        "host_certificate_path",
        "host_http_port",
        "host_private_key_path",
        "host_server_hostname",
        "host_tls_listen_port",
        "relay_ca_path",
        "relay_host",
        "relay_port",
        "relay_server_hostname",
        "route",
        "schema_version",
    }
    legacy_required = {
        "enabled",
        "host_tls_port",
        "relay_ca_path",
        "relay_host",
        "relay_port",
        "relay_server_hostname",
        "route",
        "schema_version",
    }
    if enabled is True and set(config) == legacy_required:
        raise RelayConnectorServiceError("enabled_config_upgrade_required")
    if enabled is not True or set(config) != required:
        raise RelayConnectorServiceError("enabled_config_shape_invalid")
    if (
        not isinstance(config["relay_port"], int)
        or isinstance(config["relay_port"], bool)
        or not (1 <= config["relay_port"] <= 65535)
        or not isinstance(config["host_http_port"], int)
        or isinstance(config["host_http_port"], bool)
        or not (1 <= config["host_http_port"] <= 65535)
        or not isinstance(config["host_tls_listen_port"], int)
        or isinstance(config["host_tls_listen_port"], bool)
        or not (1 <= config["host_tls_listen_port"] <= 65535)
        or config["host_http_port"] == config["host_tls_listen_port"]
    ):
        raise RelayConnectorServiceError("config_port_invalid")
    for key in (
        "host_certificate_path",
        "host_private_key_path",
        "host_server_hostname",
        "relay_ca_path",
        "relay_host",
        "relay_server_hostname",
        "route",
    ):
        if not isinstance(config[key], str) or not config[key]:
            raise RelayConnectorServiceError("config_value_invalid")
    return config


def _load_enabled_configuration(config_path: Path, secrets_path: Path) -> tuple[dict[str, Any], bytes]:
    config = load_connector_config(config_path)
    if config.get("enabled") is False:
        return config, b""

    secret_payload = _read_private_json(secrets_path)
    if set(secret_payload) != {"schema_version", "tunnel_key_hex"} or secret_payload.get(
        "schema_version"
    ) != SERVICE_SCHEMA_VERSION:
        raise RelayConnectorServiceError("secrets_shape_invalid")
    tunnel_key_hex = secret_payload.get("tunnel_key_hex")
    if not isinstance(tunnel_key_hex, str) or len(tunnel_key_hex) != 64:
        raise RelayConnectorServiceError("tunnel_key_invalid")
    try:
        tunnel_key = bytes.fromhex(tunnel_key_hex)
    except ValueError as exc:
        raise RelayConnectorServiceError("tunnel_key_invalid") from exc
    return config, tunnel_key


def validate_connector_material(
    config_path: Path,
    secrets_path: Path,
) -> tuple[dict[str, Any], bytes, ssl.SSLContext | None, ssl.SSLContext | None]:
    """Validate private Relay material without binding sockets or using the network."""
    config, tunnel_key = _load_enabled_configuration(config_path, secrets_path)
    if config.get("enabled") is False:
        return config, tunnel_key, None, None

    relay_ca_path = _validate_tls_file(config["relay_ca_path"])
    host_certificate_path = _validate_tls_file(config["host_certificate_path"])
    host_private_key_path = _validate_tls_file(
        config["host_private_key_path"],
        private_key=True,
    )
    try:
        relay_context = ssl.create_default_context(cafile=str(relay_ca_path))
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise RelayConnectorServiceError("relay_ca_invalid") from exc
    relay_context.minimum_version = ssl.TLSVersion.TLSv1_2
    _validate_certificate_hostname(
        host_certificate_path,
        config["host_server_hostname"],
    )
    host_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    host_context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        host_context.load_cert_chain(
            str(host_certificate_path),
            str(host_private_key_path),
        )
    except (OSError, ssl.SSLError) as exc:
        raise RelayConnectorServiceError("host_certificate_key_mismatch") from exc
    return config, tunnel_key, relay_context, host_context


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop.set()


def _close_ready_fd(ready_fd: int | None) -> None:
    if ready_fd is None:
        return
    try:
        os.close(ready_fd)
    except OSError:
        pass


def _signal_ready(ready_fd: int | None) -> None:
    if ready_fd is None:
        return
    try:
        if os.write(ready_fd, b"\x01") != 1:
            raise RelayConnectorServiceError("ready_signal_failed")
    except OSError as exc:
        raise RelayConnectorServiceError("ready_signal_failed") from exc
    finally:
        _close_ready_fd(ready_fd)


def _run_service_locked(
    *,
    config_path: Path,
    secrets_path: Path,
    epoch_state_path: Path,
    status_path: Path,
    managed_by_host_stack: bool = False,
    ready_fd: int | None = None,
) -> int:
    supervisor: RelayConnectorSupervisor | None = None
    host_tls_proxy: HostTlsProxy | None = None
    try:
        config, tunnel_key, relay_context, host_context = validate_connector_material(
            config_path,
            secrets_path,
        )
        if config.get("enabled") is False:
            status = _disabled_status(managed_by_host_stack=managed_by_host_stack)
            _write_private_status(status_path, status)
            _close_ready_fd(ready_fd)
            ready_fd = None
            print(json.dumps(status, sort_keys=True))
            return 0

        if relay_context is None or host_context is None:
            raise RelayConnectorServiceError("enabled_config_shape_invalid")
        host_tls_listener = bind_loopback_listener(config["host_tls_listen_port"])
        try:
            host_tls_proxy = HostTlsProxy(
                listener=host_tls_listener,
                backend_target=("127.0.0.1", config["host_http_port"]),
                tls_context=host_context,
                expected_server_hostname=config["host_server_hostname"],
            )
        except Exception:
            host_tls_listener.close()
            raise
        if not host_tls_proxy.start() or not host_tls_proxy.wait_until_ready():
            raise RelayConnectorServiceError("host_tls_proxy_start_failed")
        identity_payload = json.dumps(
            {
                "relay_host": config["relay_host"],
                "relay_port": config["relay_port"],
                "relay_server_hostname": config["relay_server_hostname"],
                "route": config["route"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        epoch_store = PersistentRelayEpochStore(
            _absolute(epoch_state_path),
            connector_identity=hashlib.sha256(identity_payload + tunnel_key).digest(),
        )
        supervisor = RelayConnectorSupervisor(
            relay_address=(config["relay_host"], config["relay_port"]),
            host_tls_target=("127.0.0.1", config["host_tls_listen_port"]),
            route=config["route"],
            key=tunnel_key,
            enabled=True,
            epoch_allocator=epoch_store,
            relay_ssl_context=relay_context,
            relay_server_hostname=config["relay_server_hostname"],
        )
        if not supervisor.start():
            raise RelayConnectorServiceError("supervisor_start_failed")
        if not supervisor.wait_until_started():
            raise RelayConnectorServiceError("supervisor_start_failed")

        status = _service_status(
            supervisor,
            host_tls_proxy,
            managed_by_host_stack=managed_by_host_stack,
        )
        last_status = json.dumps(status, sort_keys=True, separators=(",", ":"))
        _write_private_status(status_path, status)
        _signal_ready(ready_fd)
        ready_fd = None
        while not _stop.wait(0.1):
            status = _service_status(
                supervisor,
                host_tls_proxy,
                managed_by_host_stack=managed_by_host_stack,
            )
            rendered = json.dumps(status, sort_keys=True, separators=(",", ":"))
            if rendered != last_status:
                _write_private_status(status_path, status)
                last_status = rendered
            if status["state"] == "failed":
                supervisor_stopped = supervisor.stop()
                proxy_stopped = host_tls_proxy.stop()
                if not supervisor_stopped or not proxy_stopped:
                    raise RelayConnectorServiceError("service_stop_failed")
                status = _service_status(
                    supervisor,
                    host_tls_proxy,
                    managed_by_host_stack=managed_by_host_stack,
                )
                _write_private_status(status_path, status)
                print(json.dumps(status, sort_keys=True))
                return 1
            if not status["host_tls_ready"]:
                raise RelayConnectorServiceError("host_tls_proxy_unavailable")

        supervisor_stopped = supervisor.stop()
        proxy_stopped = host_tls_proxy.stop()
        if not supervisor_stopped or not proxy_stopped:
            raise RelayConnectorServiceError("service_stop_failed")
        status = _service_status(
            supervisor,
            host_tls_proxy,
            managed_by_host_stack=managed_by_host_stack,
        )
        _write_private_status(status_path, status)
        print(json.dumps(status, sort_keys=True))
        return 0
    except Exception as exc:
        _close_ready_fd(ready_fd)
        if supervisor is not None:
            supervisor.stop()
        if host_tls_proxy is not None:
            host_tls_proxy.stop()
        candidate_code = str(exc) if isinstance(exc, RelayConnectorServiceError) else ""
        failure_code = (
            candidate_code
            if candidate_code in _SAFE_FAILURE_CODES
            else "service_configuration_or_runtime_failed"
        )
        status = _failed_status(
            failure_code,
            managed_by_host_stack=managed_by_host_stack,
        )
        try:
            _write_private_status(status_path, status)
        except Exception:
            pass
        print(json.dumps(status, sort_keys=True))
        return 1


def run_service(
    *,
    config_path: Path,
    secrets_path: Path,
    epoch_state_path: Path,
    status_path: Path,
    managed_by_host_stack: bool = False,
    ready_fd: int | None = None,
) -> int:
    try:
        lock_descriptor = _acquire_service_lock(status_path)
    except RelayConnectorServiceError:
        _close_ready_fd(ready_fd)
        status = _failed_status(
            "service_instance_lock_failed",
            managed_by_host_stack=managed_by_host_stack,
        )
        print(json.dumps(status, sort_keys=True))
        return 1
    try:
        return _run_service_locked(
            config_path=config_path,
            secrets_path=secrets_path,
            epoch_state_path=epoch_state_path,
            status_path=status_path,
            managed_by_host_stack=managed_by_host_stack,
            ready_fd=ready_fd,
        )
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AgentOps MIS Relay connector service.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--secrets", type=Path, required=True)
    parser.add_argument("--epoch-state", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--managed-by-host-stack", action="store_true")
    parser.add_argument("--ready-fd", type=int)
    args = parser.parse_args()
    if args.ready_fd is not None and args.ready_fd < 3:
        parser.error("--ready-fd must be an inherited private descriptor")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    return run_service(
        config_path=args.config,
        secrets_path=args.secrets,
        epoch_state_path=args.epoch_state,
        status_path=args.status,
        managed_by_host_stack=args.managed_by_host_stack,
        ready_fd=args.ready_fd,
    )


if __name__ == "__main__":
    raise SystemExit(main())
