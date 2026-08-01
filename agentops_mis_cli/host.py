"""Lifecycle commands for the private, loopback AgentOps MIS host."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import select
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentops_mis_cli.relay_connector_service import (
    RelayConnectorServiceError,
    validate_connector_material,
)
from agentops_mis_cli import host_log, relay_control, relay_restart, runtime_lock


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "scripts" / "run_local_stack.py"
BACKUP_UTILITY = ROOT / "scripts" / "agentops_local_backup.py"
DEFAULT_UI_DIST = ROOT / "ui" / "start-building-app" / "dist"
MACOS_TAILSCALE_BIN = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
HOST_STOP_GRACE_SECONDS = 20
HOST_STORAGE_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
HOST_STORAGE_MIN_FREE_ENV = "AGENTOPS_HOST_MIN_FREE_BYTES"
HOST_SERVICE_LABEL = "dev.agentops.mis.private-host"
HOST_SERVICE_STATE_CONVERGENCE_ATTEMPTS = 4
HOST_SERVICE_STATE_CONVERGENCE_DELAY_SECONDS = 0.1
HOST_SERVICE_STATE_CONVERGENCE_READ_TIMEOUT_SECONDS = 1
HOST_MANAGED_RESTART_REQUEST_MAX_BYTES = 1024
HOST_DATA_MARKER = {
    "schema_version": 1,
    "product": "AgentOps MIS Private Host Data",
    "managed": True,
}
HOST_WORKER_ADAPTERS = {"mock", "hermes", "openclaw"}
HOST_WORKER_PROCESS_PATTERNS = (
    r"-m[[:space:]]+agentops_mis_cli\.worker([[:space:]]|$).*--adapter([=[:space:]]){adapter}([[:space:]]|$)",
    r"(^|[ /])agentops-worker([[:space:]]|$).*--adapter([=[:space:]]){adapter}([[:space:]]|$)",
    r"agent_worker\.py([[:space:]]|$).*--adapter([=[:space:]]){adapter}([[:space:]]|$)",
)
RELAY_ENABLED_CONFIG_KEYS = {
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


class HostArgumentError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise HostArgumentError("invalid_arguments")


class NoLocalRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_urlopen(request, *, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoLocalRedirects(),
    )
    return opener.open(request, timeout=timeout)


def host_home() -> Path:
    return Path(os.environ.get("AGENTOPS_HOST_HOME") or (Path.home() / ".agentops" / "host")).expanduser().resolve()


def host_install_root() -> Path:
    configured = os.environ.get("AGENTOPS_INSTALL_ROOT")
    return Path(configured).expanduser().resolve() if configured else (
        ROOT.parent.parent if ROOT.parent.name == "versions" else ROOT
    )


def _existing_filesystem_path(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    if candidate.is_file():
        candidate = candidate.parent
    return candidate


def host_storage_preflight(
    *,
    target_path: Path | None = None,
    minimum_free_bytes: int | None = None,
    reserve_bytes: int = 0,
    disk_usage=None,
    environ: dict[str, str] | None = None,
) -> dict:
    environment = os.environ if environ is None else environ
    raw_minimum = minimum_free_bytes
    if raw_minimum is None:
        raw_minimum = environment.get(HOST_STORAGE_MIN_FREE_ENV)

    threshold_error = None
    if raw_minimum in (None, ""):
        effective_minimum = HOST_STORAGE_MIN_FREE_BYTES
    else:
        try:
            requested_minimum = int(str(raw_minimum).strip())
        except (TypeError, ValueError):
            requested_minimum = HOST_STORAGE_MIN_FREE_BYTES
            threshold_error = "invalid_threshold"
        if requested_minimum < HOST_STORAGE_MIN_FREE_BYTES:
            effective_minimum = HOST_STORAGE_MIN_FREE_BYTES
            threshold_error = "threshold_below_production_floor"
        else:
            effective_minimum = requested_minimum

    try:
        reserve = int(reserve_bytes)
    except (TypeError, ValueError):
        reserve = 0
        threshold_error = threshold_error or "invalid_reserve"
    if reserve < 0:
        reserve = 0
        threshold_error = threshold_error or "invalid_reserve"

    requested_path = (target_path or host_install_root()).expanduser().absolute()
    filesystem_path = _existing_filesystem_path(requested_path)
    required_bytes = effective_minimum + reserve
    free_bytes = None
    status = threshold_error
    try:
        usage = (disk_usage or shutil.disk_usage)(filesystem_path)
        free_bytes = int(usage.free)
    except (OSError, TypeError, ValueError):
        status = status or "storage_unavailable"
    if status is None:
        status = "ready" if free_bytes is not None and free_bytes >= required_bytes else "insufficient_free_space"

    return {
        "ok": status == "ready",
        "operation": "host_storage_preflight",
        "filesystem_path": str(filesystem_path),
        "free_bytes": free_bytes,
        "required_bytes": required_bytes,
        "minimum_free_bytes": effective_minimum,
        "reserve_bytes": reserve,
        "status": status,
        "read_only": True,
        "network_used": False,
        "database_content_read": False,
        "credentials_read": False,
        "token_omitted": True,
    }


def paths() -> dict[str, Path]:
    home = host_home()
    return {
        "home": home,
        "config": home / "config.json",
        "secrets": home / "secrets.json",
        "data": home / "data",
        "database": home / "data" / "agentops_mis.db",
        "backups": home / "backups",
        "logs": home / "logs",
        "log": home / "logs" / "host.log",
        "run": home / "run",
        "pid": home / "run" / "host.pid.json",
        "service_instance": home / "run" / "host.service-instance.json",
        "restart_socket": home / "run" / "host.restart.sock",
        "runtime_lock": home / "run" / "host.runtime.lock",
        "ownership": home / ".agentops-host-data.json",
        "lifecycle_lock": home.parent / ".agentops-mis-host-lifecycle.lock",
        "relay": home / "relay",
        "relay_config": home / "relay" / "config.json",
        "relay_prepared": home / "relay" / "prepared.json",
        "relay_transition": home / "relay" / "transition.json",
        "relay_restart_receipt": home / "relay" / "restart-receipt.json",
        "relay_restart_sequence": home / "relay" / "restart-sequence.json",
        "relay_restart_archive": home / "relay" / "restart-archive.json",
        "relay_restart_audit_outbox": home / "relay" / "restart-audit-outbox",
        "relay_secrets": home / "relay" / "secrets.json",
        "relay_epoch": home / "relay" / "epoch.json",
        "relay_status": home / "relay" / "status.json",
    }


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_private_bounded_json(path: Path, *, maximum_bytes: int = 16 * 1024) -> dict | None:
    descriptor = -1
    try:
        parent_metadata = path.parent.lstat()
        if (
            path.parent.is_symlink()
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _relay_enabled_config_shape_valid(config: dict) -> bool:
    if set(config) != RELAY_ENABLED_CONFIG_KEYS:
        return False
    ports = (config.get("host_http_port"), config.get("host_tls_listen_port"), config.get("relay_port"))
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (1 <= value <= 65535)
        for value in ports
    ):
        return False
    return all(
        isinstance(config.get(key), str) and bool(config[key])
        for key in RELAY_ENABLED_CONFIG_KEYS
        - {"enabled", "host_http_port", "host_tls_listen_port", "relay_port", "schema_version"}
    )


def relay_connector_projection(p: dict[str, Path] | None = None) -> dict:
    host_paths = p or paths()
    config_path = host_paths["relay_config"]
    base = {
        "configured": False,
        "config_valid": True,
        "deployed_relay": False,
        "enabled": False,
        "failure_code": None,
        "host_lifecycle_integrated": False,
        "ok": True,
        "ready": False,
        "state": "unconfigured",
        "tailscale_changed": False,
        "token_omitted": True,
    }
    if not config_path.exists() and not config_path.is_symlink():
        return base
    config = _read_private_bounded_json(config_path)
    if (
        config is None
        or config.get("schema_version") != 1
        or not isinstance(config.get("enabled"), bool)
        or (
            config.get("enabled") is False
            and set(config) != {"enabled", "schema_version"}
        )
        or (
            config.get("enabled") is True
            and not _relay_enabled_config_shape_valid(config)
        )
    ):
        return {
            **base,
            "configured": True,
            "config_valid": False,
            "failure_code": "relay_connector_config_invalid",
            "ok": False,
            "state": "invalid_config",
        }
    enabled = bool(config["enabled"])
    if enabled:
        pid_record = _read_private_bounded_json(host_paths["pid"]) or {}
        try:
            parent_pid = int(pid_record.get("pid") or 0)
        except (TypeError, ValueError):
            parent_pid = 0
        children = managed_relay_child_pids(parent_pid, host_paths["relay_status"])
        status = _read_private_bounded_json(host_paths["relay_status"])
        try:
            status_mtime = (
                host_paths["relay_status"].stat().st_mtime
                if status is not None and not host_paths["relay_status"].is_symlink()
                else 0
            )
            started_at = float(pid_record.get("started_at_epoch") or 0)
        except (OSError, TypeError, ValueError):
            status_mtime = 0
            started_at = 0
        runtime_state = str((status or {}).get("state") or "")
        runtime_integrated = bool(
            managed_process_record_matches(pid_record, parent_pid)
            and children is not None
            and len(children) == 1
            and status is not None
            and status_mtime >= started_at
            and status.get("enabled") is True
            and status.get("host_lifecycle_integrated") is True
            and status.get("host_tls_ready") is True
            and isinstance(status.get("current_epoch"), int)
            and status["current_epoch"] > 0
            and runtime_state in {"connecting", "connected", "backoff"}
        )
        if runtime_integrated:
            return {
                **base,
                "configured": True,
                "enabled": True,
                "state": runtime_state,
                "runtime_ready": True,
                "remote_ready": False,
                "host_tls_ready": True,
                "host_lifecycle_integrated": True,
                "ok": True,
                "ready": False,
            }
    return {
        **base,
        "configured": True,
        "enabled": enabled,
        "state": "enabled_unmanaged" if enabled else "disabled",
        "failure_code": "relay_connector_lifecycle_not_integrated" if enabled else None,
        "ok": not enabled,
    }


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def managed_process_identity(pid: int) -> dict | None:
    if not process_alive(pid):
        return None
    try:
        process = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        rendered = process.stdout.strip()
        process_group_id = os.getpgid(pid)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0 or not rendered or str(STACK) not in rendered:
        return None
    return {
        "process_group_id": process_group_id,
        "process_identity_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }


def write_managed_pid_record(path: Path, process: subprocess.Popen, *, foreground: bool = False) -> None:
    identity = None
    previous_identity = None
    for _attempt in range(40):
        candidate = managed_process_identity(process.pid)
        if candidate and candidate == previous_identity:
            identity = candidate
            break
        previous_identity = candidate
        if process.poll() is not None:
            break
        time.sleep(0.025)
    if not identity:
        try:
            if foreground:
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if foreground:
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        raise RuntimeError("Host process identity could not be established.")
    write_private_json(path, {
        "schema_version": 1,
        "pid": process.pid,
        "process_group_id": identity["process_group_id"],
        "process_identity_hash": identity["process_identity_hash"],
        "started_at_epoch": time.time(),
        **({"foreground": True} if foreground else {}),
    })


def managed_process_record_matches(record: dict, pid: int) -> bool:
    identity = managed_process_identity(pid)
    if not identity:
        return False
    try:
        recorded_group = int(record.get("process_group_id"))
    except (TypeError, ValueError):
        return False
    return bool(
        record.get("schema_version") == 1
        and recorded_group == identity["process_group_id"]
        and secrets.compare_digest(
            str(record.get("process_identity_hash") or ""),
            identity["process_identity_hash"],
        )
    )


def managed_relay_child_pids(parent_pid: int, status_path: Path) -> list[int] | None:
    if parent_pid <= 0:
        return []
    try:
        process = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    expected_status = str(status_path)
    children: list[int] = []
    for raw in process.stdout.splitlines():
        columns = raw.strip().split(None, 2)
        if len(columns) != 3:
            continue
        try:
            pid = int(columns[0])
            ppid = int(columns[1])
        except ValueError:
            continue
        command = columns[2]
        if (
            ppid == parent_pid
            and "-m agentops_mis_cli.relay_connector_service" in command
            and "--managed-by-host-stack" in command
            and expected_status in command
        ):
            children.append(pid)
    return children


def managed_host_running() -> bool:
    record = read_json(paths()["pid"])
    return process_alive(int(record.get("pid") or 0))


def _acquire_lifecycle_lock() -> int:
    lock_path = paths()["lifecycle_lock"]
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise RuntimeError("Host lifecycle lock is not a regular file.")
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _release_lifecycle_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


@contextlib.contextmanager
def lifecycle_lock():
    descriptor = _acquire_lifecycle_lock()
    try:
        yield
    finally:
        _release_lifecycle_lock(descriptor)


def ensure_host_data_marker(*, allow_legacy: bool = False) -> bool:
    marker_path = paths()["ownership"]
    if marker_path.exists():
        if marker_path.is_symlink() or read_json(marker_path) != HOST_DATA_MARKER:
            raise RuntimeError("Host data ownership marker is invalid.")
        marker_path.chmod(0o600)
        return False
    home = paths()["home"]
    existing_entries = list(home.iterdir()) if home.is_dir() else []
    if existing_entries:
        config = read_json(paths()["config"])
        secret_values = read_json(paths()["secrets"])
        try:
            database_path = Path(str(config.get("database_path") or "")).expanduser().resolve()
            database_is_managed = database_path == paths()["database"]
        except (OSError, RuntimeError, ValueError):
            database_is_managed = False
        api_key = secret_values.get("api_key")
        admin_key = secret_values.get("admin_key")
        owner_setup_code = secret_values.get("owner_setup_code")
        secret_shape_valid = bool(
            isinstance(api_key, str)
            and api_key.startswith("agthost_")
            and isinstance(admin_key, str)
            and admin_key.startswith("agtadmin_")
            and isinstance(owner_setup_code, str)
            and len(owner_setup_code) >= 16
        )
        legacy_owned = bool(
            allow_legacy
            and config.get("version") == 1
            and config.get("deployment_mode") == "private_host"
            and config.get("host") == "127.0.0.1"
            and database_is_managed
            and isinstance(config.get("port"), int)
            and config.get("workspace_id")
            and secret_shape_valid
            and paths()["data"].is_dir()
            and paths()["run"].is_dir()
            and (ROOT / "docs" / "LOCAL_HOST_REMOTE_CONSOLE_SPEC.md").is_file()
        )
        if not legacy_owned:
            raise RuntimeError("Host data root is non-empty without a valid ownership marker.")
    write_private_json(marker_path, HOST_DATA_MARKER)
    return True


def health(base_url: str) -> dict:
    try:
        with local_urlopen(base_url.rstrip("/") + "/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"reachable": response.status == 200, "status": payload.get("status", "unknown")}
    except (OSError, ValueError, urllib.error.URLError):
        return {"reachable": False, "status": "unavailable"}


def loopback_base_url(host: str, port: int) -> str | None:
    value = str(host or "").strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not address.is_loopback:
        return None
    rendered = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{rendered}:{int(port)}"


def local_json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    request_headers = {"Content-Type": "application/json", "Origin": base_url.rstrip("/")}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers=request_headers,
    )
    try:
        with local_urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {}
        except ValueError:
            return exc.code, {}
    except (OSError, urllib.error.URLError, ValueError):
        return 0, {"error": "host_unavailable"}


def human_access_state(base_url: str, *, running: bool, reachable: bool) -> dict:
    result = {
        "status": "host_stopped" if not running else "unavailable",
        "bootstrap_required": None,
        "login_ready": False,
        "token_omitted": True,
    }
    if not running or not reachable or not base_url:
        return result
    status, payload = local_json_request(base_url, "/api/human-auth/status")
    if status != 200 or payload.get("required") is not True:
        return result
    bootstrap_value = payload.get("bootstrap_required")
    if not isinstance(bootstrap_value, bool):
        return result
    bootstrap_required = bootstrap_value
    result.update({
        "status": "bootstrap_required" if bootstrap_required else "ready",
        "bootstrap_required": bootstrap_required,
        "login_ready": not bootstrap_required,
    })
    return result


def tailscale_binary() -> tuple[str | None, str]:
    override = os.environ.get("AGENTOPS_TAILSCALE_BIN", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return (str(candidate.resolve()), "configured") if candidate.is_file() and os.access(candidate, os.X_OK) else (None, "configured_invalid")
    path_binary = shutil.which("tailscale")
    if path_binary:
        return path_binary, "path"
    if MACOS_TAILSCALE_BIN.is_file() and os.access(MACOS_TAILSCALE_BIN, os.X_OK):
        return str(MACOS_TAILSCALE_BIN), "macos_app"
    return None, "unavailable"


def tailscale_state() -> dict:
    binary, source = tailscale_binary()
    result = {
        "installed": bool(binary),
        "installation_source": source,
        "backend_state": "unavailable",
        "dns_name": "",
        "token_omitted": True,
    }
    if not binary:
        return result
    try:
        process = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=5, check=False)
        payload = json.loads(process.stdout) if process.returncode == 0 else {}
        self_state = payload.get("Self") or {}
        result.update({
            "backend_state": payload.get("BackendState") or "unknown",
            "dns_name": str(self_state.get("DNSName") or "").rstrip("."),
        })
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return result


def tailscale_serve_state(binary: str | None, target: str, https_port: int = 443) -> dict:
    result = {
        "status_available": False,
        "configured": False,
        "target_matches": False,
        "conflict": False,
        "backend_count": 0,
        "handler_count": 0,
        "exclusive": False,
        "public_funnel_enabled": False,
        "unsupported_config": False,
        "https_port": https_port,
        "raw_config_omitted": True,
        "token_omitted": True,
    }
    if not binary:
        return result
    try:
        process = subprocess.run(
            [binary, "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if process.returncode != 0:
            return result
        payload = json.loads(process.stdout or "{}")

        def proxy_targets(value) -> list[str]:
            if isinstance(value, dict):
                targets = [str(value["Proxy"])] if value.get("Proxy") else []
                for child in value.values():
                    targets.extend(proxy_targets(child))
                return targets
            if isinstance(value, list):
                return [item for child in value for item in proxy_targets(child)]
            return []

        def port_matches(value) -> bool:
            text = str(value)
            return text == str(https_port) or text.rsplit(":", 1)[-1] == str(https_port)

        selected_web: list[tuple[object, bool]] = []
        selected_tcp: list[tuple[object, bool]] = []
        funnel_selected = False

        def collect_selected(value, *, service_wrapped: bool = False) -> None:
            nonlocal funnel_selected
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "Web" and isinstance(child, dict):
                        selected_web.extend((item, service_wrapped) for origin, item in child.items() if port_matches(origin))
                    elif key == "TCP" and isinstance(child, dict):
                        selected_tcp.extend((item, service_wrapped) for port, item in child.items() if port_matches(port))
                    elif key == "Services":
                        collect_selected(child, service_wrapped=True)
                    elif key == "AllowFunnel":
                        if isinstance(child, dict):
                            funnel_selected = funnel_selected or any(port_matches(port) and bool(flag) for port, flag in child.items())
                        else:
                            funnel_selected = funnel_selected or bool(child)
                    elif service_wrapped:
                        collect_selected(child, service_wrapped=True)
            elif isinstance(value, list):
                for child in value:
                    collect_selected(child, service_wrapped=service_wrapped)

        collect_selected(payload)

        def funnel_enabled(value) -> bool:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "AllowFunnel":
                        if isinstance(child, dict) and any(bool(flag) for flag in child.values()):
                            return True
                        if not isinstance(child, dict) and bool(child):
                            return True
                    if funnel_enabled(child):
                        return True
                return False
            if isinstance(value, list):
                return any(funnel_enabled(child) for child in value)
            return False

        selected_values = [value for value, _wrapped in selected_web + selected_tcp]
        targets = sorted(set(proxy_targets(selected_values)))
        configured = bool(selected_web or selected_tcp or targets)
        public_funnel_enabled = funnel_selected or any(funnel_enabled(value) for value in selected_values)
        handler_count = sum(
            len(value.get("Handlers") or {})
            for value, _wrapped in selected_web
            if isinstance(value, dict) and isinstance(value.get("Handlers"), dict)
        )

        def exclusive_web(value, wrapped: bool) -> bool:
            if wrapped or not isinstance(value, dict) or set(value) - {"Handlers", "AllowFunnel"}:
                return False
            handlers = value.get("Handlers")
            if not isinstance(handlers, dict) or set(handlers) != {"/"}:
                return False
            root_handler = handlers.get("/")
            return isinstance(root_handler, dict) and set(root_handler) == {"Proxy"} and root_handler.get("Proxy") == target

        def exclusive_tcp(value, wrapped: bool) -> bool:
            return not wrapped and isinstance(value, dict) and set(value) == {"HTTPS"} and value.get("HTTPS") is True

        exclusive = bool(
            len(selected_web) == 1
            and len(selected_tcp) == 1
            and exclusive_web(*selected_web[0])
            and exclusive_tcp(*selected_tcp[0])
            and not public_funnel_enabled
        )
        target_matches = target in targets
        result.update({
            "status_available": True,
            "configured": configured,
            "target_matches": target_matches,
            "conflict": configured and not exclusive,
            "backend_count": len(targets),
            "handler_count": handler_count,
            "exclusive": exclusive,
            "public_funnel_enabled": public_funnel_enabled,
            "unsupported_config": configured and not exclusive and not public_funnel_enabled,
        })
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return result


def require_initialized() -> tuple[dict, dict]:
    p = paths()
    config = read_json(p["config"])
    secret_values = read_json(p["secrets"])
    if not config or not secret_values:
        raise RuntimeError("Host is not initialized. Run: agentops host init")
    return config, secret_values


def _cmd_init_unlocked(args) -> int:
    p = paths()
    if p["config"].exists() or p["secrets"].exists():
        emit({
            "ok": False,
            "operation": "host_init",
            "error": "already_initialized",
            "message": "Host configuration already exists; initialization never overwrites secrets.",
            "home": str(p["home"]),
            "token_omitted": True,
        })
        return 2
    ensure_host_data_marker()
    if p["relay"].is_symlink() or (p["relay"].exists() and not p["relay"].is_dir()):
        raise RuntimeError("Relay directory must be a private managed directory.")
    if p["relay"].exists():
        relay_metadata = p["relay"].lstat()
        if (
            relay_metadata.st_uid != os.getuid()
            or stat.S_IMODE(relay_metadata.st_mode) != 0o700
        ):
            raise RuntimeError("Relay directory permissions are not private.")
    else:
        p["relay"].mkdir(parents=True, mode=0o700)
        p["relay"].chmod(0o700)
    if p["relay_config"].is_symlink():
        raise RuntimeError("Relay configuration must not be a symlink.")
    if p["relay_config"].exists():
        if relay_connector_projection(p)["state"] != "disabled":
            raise RuntimeError("Existing Relay configuration is not the safe default.")
    else:
        write_private_json(p["relay_config"], {"enabled": False, "schema_version": 1})
    for key in ("home", "data", "logs", "run"):
        p[key].mkdir(parents=True, exist_ok=True, mode=0o700)
        p[key].chmod(0o700)
    config = {
        "version": 1,
        "host": "127.0.0.1",
        "port": int(args.port),
        "workspace_id": args.workspace_id,
        "database_path": str(p["database"]),
        "ui_dist": str(Path(args.ui_dist).expanduser().resolve() if args.ui_dist else DEFAULT_UI_DIST),
        "deployment_mode": "private_host",
        "cookie_secure": False,
        "allowed_origins": [f"http://127.0.0.1:{int(args.port)}"],
        "network_publication": "disabled",
        "tailscale_https_port": 443,
    }
    owner_setup_code = secrets.token_urlsafe(18)
    secret_values = {
        "api_key": "agthost_" + secrets.token_urlsafe(32),
        "admin_key": "agtadmin_" + secrets.token_urlsafe(32),
        "owner_setup_code": owner_setup_code,
    }
    write_private_json(p["config"], config)
    write_private_json(p["secrets"], secret_values)
    ui_ready = (Path(config["ui_dist"]) / "index.html").is_file()
    local_console_url = f"http://127.0.0.1:{int(args.port)}/workspace"
    next_actions = (
        ["Run: agentops host start"]
        if ui_ready
        else [
            "Build or provide the production UI bundle.",
            "Run: agentops host start --build-ui",
        ]
    )
    next_actions.append(f"After Host start, open on this Mac: {local_console_url}")
    next_actions.append("CLI recovery only: agentops host bootstrap-owner --confirm")
    emit({
        "ok": True,
        "operation": "host_init",
        "home": str(p["home"]),
        "config_path": str(p["config"]),
        "secrets_path": str(p["secrets"]),
        "owner_setup_code": owner_setup_code,
        "owner_setup_code_visible_once": True,
        "next_actions": next_actions,
        "token_omitted": True,
    })
    return 0


def cmd_init(args) -> int:
    with lifecycle_lock():
        p = paths()
        home_preexisted = p["home"].exists()
        marker_preexisted = p["ownership"].exists()
        relay_preexisted = p["relay"].exists() or p["relay"].is_symlink()
        relay_config_preexisted = (
            p["relay_config"].exists() or p["relay_config"].is_symlink()
        )
        try:
            return _cmd_init_unlocked(args)
        except Exception:
            if not relay_config_preexisted:
                p["relay_config"].unlink(missing_ok=True)
            for key in ("secrets", "config"):
                p[key].unlink(missing_ok=True)
            for key in ("run", "logs", "data"):
                try:
                    p[key].rmdir()
                except OSError:
                    pass
            if not relay_preexisted:
                try:
                    p["relay"].rmdir()
                except OSError:
                    pass
            if not marker_preexisted:
                p["ownership"].unlink(missing_ok=True)
            if not home_preexisted:
                try:
                    p["home"].rmdir()
                except OSError:
                    pass
            raise


def cmd_bootstrap_owner(args) -> int:
    config, secret_values = require_initialized()
    if not args.confirm:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "preview_only": True,
            "error": "confirmation_required",
            "message": "Re-run with --confirm. The password is read securely from the terminal or one stdin line, never from argv.",
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2

    base_url = loopback_base_url(config.get("host"), int(config["port"]))
    if not base_url:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "unsafe_bootstrap_target",
            "message": "Owner bootstrap is restricted to a literal loopback Host target.",
            "target_omitted": True,
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2
    if not managed_host_running():
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "managed_host_not_running",
            "next_action": "agentops host start",
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2
    if not health(base_url)["reachable"]:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "host_unavailable",
            "next_action": "agentops host start",
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2
    auth_status, auth_payload = local_json_request(base_url, "/api/human-auth/status")
    if auth_status != 200 or auth_payload.get("required") is not True:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "human_auth_disabled" if auth_status == 200 else "owner_bootstrap_status_unavailable",
            "http_status": auth_status or None,
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2
    if auth_payload.get("bootstrap_required") is not True:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "owner_already_initialized",
            "http_status": 409,
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2

    username = str(args.username or "").strip()
    if not username:
        if not sys.stdin.isatty():
            emit({
                "ok": False,
                "operation": "host_bootstrap_owner",
                "error": "username_required",
                "setup_code_omitted": True,
                "password_omitted": True,
                "token_omitted": True,
            })
            return 2
        username = input("Owner username: ").strip()
    display_name = str(args.display_name or username).strip() or username

    if args.password_stdin:
        password = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
    else:
        if not sys.stdin.isatty():
            emit({
                "ok": False,
                "operation": "host_bootstrap_owner",
                "error": "interactive_terminal_required",
                "message": "Use an interactive terminal or --password-stdin. Password argv/env options do not exist.",
                "setup_code_omitted": True,
                "password_omitted": True,
                "token_omitted": True,
            })
            return 2
        password = getpass.getpass("Owner password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            emit({
                "ok": False,
                "operation": "host_bootstrap_owner",
                "error": "password_confirmation_mismatch",
                "setup_code_omitted": True,
                "password_omitted": True,
                "token_omitted": True,
            })
            return 2

    if not username or not password:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "username_and_password_required",
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2

    status, payload = local_json_request(
        base_url,
        "/api/human-auth/bootstrap",
        method="POST",
        body={
            "setup_code": secret_values["owner_setup_code"],
            "username": username,
            "display_name": display_name,
            "password": password,
        },
    )
    if status != 201 or (payload.get("user") or {}).get("role") != "owner":
        safe_errors = {
            "owner_already_initialized",
            "human_auth_disabled",
            "invalid_setup_code",
            "invalid_username",
            "weak_password",
            "host_unavailable",
        }
        safe_messages = {
            "weak_password": "Password must contain at least 12 characters.",
        }
        error = str(payload.get("error") or "owner_bootstrap_failed")
        safe_error = error if error in safe_errors else "owner_bootstrap_failed"
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": safe_error,
            "http_status": status or None,
            **({"message": safe_messages[safe_error]} if safe_error in safe_messages else {}),
            "setup_code_omitted": True,
            "password_omitted": True,
            "token_omitted": True,
        })
        return 2 if status in {0, 400, 401, 403, 409} else 1
    emit({
        "ok": True,
        "operation": "host_bootstrap_owner",
        "owner_created": True,
        "role": "owner",
        "next_action": "Sign in from the private Console with the Owner username and password you just chose.",
        "setup_code_omitted": True,
        "password_omitted": True,
        "session_cookie_omitted": True,
        "token_omitted": True,
    })
    return 0


def cmd_configure_cli(args) -> int:
    config, secret_values = require_initialized()
    if not args.confirm:
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "preview_only": True,
            "error": "confirmation_required",
            "message": "Re-run with --confirm to configure this Host's local machine CLI credential.",
            "browser_session_reused": False,
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2

    base_url = loopback_base_url(config.get("host"), int(config["port"]))
    if not base_url:
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "unsafe_cli_target",
            "message": "Local CLI configuration is restricted to a literal loopback Host target.",
            "target_omitted": True,
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2
    if not managed_host_running():
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "managed_host_not_running",
            "next_action": "agentops host start",
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2
    if not health(base_url)["reachable"]:
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "host_unavailable",
            "next_action": "agentops host start",
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2

    status, payload = local_json_request(
        base_url,
        "/api/agent-gateway/status",
        headers={"Authorization": f"Bearer {secret_values['api_key']}"},
    )
    if status != 200 or payload.get("provider") != "agent_gateway":
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "gateway_authentication_failed",
            "http_status": status or None,
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2 if status in {0, 401, 403} else 1

    from . import agentops as agentops_cli

    cli_config_path = agentops_cli.CONFIG_PATH.expanduser()
    safe_home = Path(os.environ.get("HOME") or Path.home()).expanduser().resolve()
    safe_config_root = (safe_home / ".agentops").resolve()
    try:
        cli_config_path.resolve().relative_to(safe_config_root)
    except ValueError:
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "unsafe_cli_config_path",
            "config_path_omitted": True,
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2
    if cli_config_path.is_symlink():
        emit({
            "ok": False,
            "operation": "host_configure_cli",
            "error": "unsafe_cli_config_path",
            "config_path_omitted": True,
            "credential_omitted": True,
            "token_omitted": True,
        })
        return 2
    cli_config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cli_config_path.parent.chmod(0o700)

    cli_config = agentops_cli.load_config()
    cli_config.update({
        "base_url": base_url,
        "workspace_id": config.get("workspace_id", "local-demo"),
        "agent_id": "agt_host_local_cli",
        "api_key": secret_values["api_key"],
        "api_key_base_url": base_url,
    })
    agentops_cli.save_config(cli_config)
    emit({
        "ok": True,
        "operation": "host_configure_cli",
        "base_url": base_url,
        "workspace_id": cli_config["workspace_id"],
        "agent_id": cli_config["agent_id"],
        "machine_credential_configured": True,
        "browser_session_reused": False,
        "config_private": True,
        "credential_omitted": True,
        "token_omitted": True,
    })
    return 0


def host_env(config: dict, secret_values: dict) -> dict:
    env = os.environ.copy()
    env.update({
        "AGENTOPS_DB_PATH": config["database_path"],
        "AGENTOPS_DEPLOYMENT_MODE": "private_host",
        "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
        "AGENTOPS_COOKIE_SECURE": "true" if config.get("cookie_secure", True) else "false",
        "AGENTOPS_API_KEY": secret_values["api_key"],
        "AGENTOPS_ADMIN_KEY": secret_values["admin_key"],
        "AGENTOPS_OWNER_SETUP_CODE": secret_values["owner_setup_code"],
        "AGENTOPS_ALLOWED_ORIGINS": ",".join(config.get("allowed_origins") or []),
        "AGENTOPS_WORKSPACE_ID": config.get("workspace_id", "local-demo"),
        "AGENTOPS_SKIP_SEED_EXPORTS": "1",
    })
    release = install_state()["current"]
    if release:
        env["AGENTOPS_HOST_VERSION"] = str(release.get("version") or "development")
        env["AGENTOPS_GIT_COMMIT"] = str(release.get("git_commit") or "unknown")
    return env


def effective_ui_dist(config: dict) -> tuple[Path, bool]:
    configured = Path(str(config["ui_dist"])).expanduser().resolve()
    state = install_state()
    current = state.get("current") or {}
    current_target = Path(str(current.get("target") or "")).resolve() if current.get("target") else None
    versions = (Path(state["install_root"]) / "versions").resolve()
    try:
        relative = configured.relative_to(versions)
    except ValueError:
        relative = None
    managed = bool(relative and len(relative.parts) == 4 and relative.parts[1:] == ("ui", "start-building-app", "dist"))
    if managed and current_target:
        return current_target / "ui" / "start-building-app" / "dist", True
    return configured, False


def requested_host_worker_adapters(args) -> list[str]:
    if args.no_workers:
        return []
    return list(dict.fromkeys(args.worker or ["mock"]))


def running_worker_adapter_pids(adapter: str) -> list[int] | None:
    if adapter not in HOST_WORKER_ADAPTERS:
        return []
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return None
    pids: set[int] = set()
    for template in HOST_WORKER_PROCESS_PATTERNS:
        try:
            process = subprocess.run(
                [pgrep, "-f", "--", template.format(adapter=adapter)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if process.returncode not in {0, 1}:
            return None
        for raw in process.stdout.splitlines():
            try:
                pid = int(raw.strip())
            except ValueError:
                continue
            if pid > 0 and pid != os.getpid():
                pids.add(pid)
    return sorted(pids)


def host_worker_ownership_preflight(args) -> dict:
    requested = requested_host_worker_adapters(args)
    conflicts = []
    unavailable = []
    for adapter in requested:
        pids = running_worker_adapter_pids(adapter)
        if pids is None:
            unavailable.append(adapter)
        elif pids:
            conflicts.append({
                "adapter": adapter,
                "pids": pids,
                "process_command_omitted": True,
            })
    return {
        "ok": not conflicts and not unavailable,
        "operation": "host_worker_ownership_preflight",
        "requested_workers": requested,
        "conflicts": conflicts,
        "check_unavailable_adapters": unavailable,
        "process_command_omitted": True,
        "token_omitted": True,
    }


def emit_host_worker_ownership_error(preflight: dict) -> int:
    check_unavailable = bool(preflight.get("check_unavailable_adapters"))
    emit({
        **preflight,
        "ok": False,
        "operation": "host_start",
        "error": "worker_ownership_check_unavailable" if check_unavailable else "worker_ownership_conflict",
        "message": (
            "Host could not prove exclusive Worker ownership on this machine."
            if check_unavailable
            else "A requested adapter already has a local Worker process. Choose one ownership model before starting Host workers."
        ),
        "remediation": {
            "existing_worker_mode": "agentops host start --no-workers",
            "host_owned_worker_mode": "Stop or unload the existing local Worker owner, then retry the requested Host start.",
            "automatic_process_termination": False,
        },
        "live_execution_performed": False,
    })
    return 2


def stack_command(config: dict, args, *, stack_ready_fd: int | None = None) -> list[str]:
    ui_dist, _managed = effective_ui_dist(config)
    host_paths = paths()
    if host_log.start_gate(host_paths["logs"]).get("ok") is not True:
        raise RuntimeError("Host log recovery is required before startup.")
    command = [
        sys.executable,
        str(STACK),
        "--backend-host",
        config["host"],
        "--backend-port",
        str(config["port"]),
        "--production-ui",
        "--ui-dist",
        str(ui_dist),
        "--relay-config",
        str(host_paths["relay_config"]),
        "--relay-secrets",
        str(host_paths["relay_secrets"]),
        "--relay-epoch-state",
        str(host_paths["relay_epoch"]),
        "--relay-status",
        str(host_paths["relay_status"]),
        "--runtime-lock",
        str(host_paths["runtime_lock"]),
    ]
    if args.build_ui:
        command.append("--build-ui")
        if args.install_ui:
            command.append("--install-ui")
    if args.no_workers:
        command.append("--no-workers")
    else:
        for adapter in args.worker or ["mock"]:
            command.extend(["--worker", adapter])
    if args.confirm_live_workers:
        command.append("--confirm-live-workers")
    if stack_ready_fd is not None:
        command.extend(["--stack-ready-fd", str(stack_ready_fd)])
    return command


def _host_log_start_preflight(p: dict[str, Path]) -> bool:
    gate = host_log.start_gate(p["logs"])
    if gate.get("ok") is True:
        return True
    recovery_ready = gate.get("error") == "host_log_recovery_required"
    emit({
        "ok": False,
        "operation": "host_start",
        "error": gate.get("error") or "host_log_recovery_metadata_unverifiable",
        "recovery_required": True,
        "next_action": (
            "agentops host log-rotate --confirm-rotate"
            if recovery_ready
            else "agentops host log-rotate"
        ),
        "content_omitted": True,
        "paths_omitted": True,
        "token_omitted": True,
    })
    return False


def _cmd_start_unlocked(args) -> int:
    p = paths()
    pid_record = read_json(p["pid"])
    pid = int(pid_record.get("pid") or 0)
    if process_alive(pid):
        emit({"ok": False, "operation": "host_start", "error": "already_running", "pid": pid, "token_omitted": True})
        return 2
    if not _host_log_start_preflight(p):
        return 2
    startup_recovery = _prepare_restart_receipt_recovery(p)
    config, secret_values = require_initialized()
    worker_preflight = host_worker_ownership_preflight(args)
    if not worker_preflight["ok"]:
        return emit_host_worker_ownership_error(worker_preflight)
    env = host_env(config, secret_values)
    if args.foreground:
        command = stack_command(config, args)
        process = subprocess.Popen(command, cwd=ROOT, env=env)
        write_managed_pid_record(p["pid"], process, foreground=True)
        try:
            return process.wait()
        finally:
            current_record = read_json(p["pid"])
            if int(current_record.get("pid") or 0) == process.pid:
                p["pid"].unlink(missing_ok=True)
    p["log"].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stack_ready_read_fd, stack_ready_write_fd = os.pipe()
    command = stack_command(config, args, stack_ready_fd=stack_ready_write_fd)
    with p["log"].open("a", encoding="utf-8") as log_file:
        p["log"].chmod(0o600)
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(stack_ready_write_fd,),
            )
        except Exception:
            os.close(stack_ready_read_fd)
            raise
        finally:
            os.close(stack_ready_write_fd)
    try:
        write_managed_pid_record(p["pid"], process)
    except Exception:
        os.close(stack_ready_read_fd)
        raise
    base_url = f"http://{config['host']}:{config['port']}"
    deadline = time.time() + 25
    readiness = {"reachable": False, "status": "unavailable"}
    stack_ready = False
    while time.time() < deadline and process.poll() is None:
        if not stack_ready:
            readable, _writable, _exceptional = select.select(
                [stack_ready_read_fd],
                [],
                [],
                0.05,
            )
            if readable:
                stack_ready = os.read(stack_ready_read_fd, 1) == b"\x01"
        readiness = health(base_url)
        if stack_ready and readiness["reachable"]:
            break
        time.sleep(0.25)
    os.close(stack_ready_read_fd)
    if not stack_ready or not readiness["reachable"]:
        terminated = _terminate_background_stack(process)
        if terminated:
            p["pid"].unlink(missing_ok=True)
            _fail_restart_receipt_recovery_start(p, startup_recovery)
        else:
            _mark_restart_recovery_rollback_failed(p)
        emit({
            "ok": False,
            "operation": "host_start",
            "error": "startup_failed",
            "log_path": str(p["log"]),
            "token_omitted": True,
        })
        return 1
    recovery_healthy, terminated = _finish_restart_recovery_with_cleanup(
        p,
        startup_recovery,
        process,
        terminate=_terminate_background_stack,
    )
    if not recovery_healthy:
        if terminated:
            p["pid"].unlink(missing_ok=True)
        emit({
            "ok": False,
            "operation": "host_start",
            "error": "restart_recovery_retry_required",
            "token_omitted": True,
        })
        return 1
    emit({
        "ok": True,
        "operation": "host_start",
        "pid": process.pid,
        "local_console_url": base_url + "/workspace",
        "health": readiness,
        "workers": args.worker or ([] if args.no_workers else ["mock"]),
        "live_workers_confirmed": bool(args.confirm_live_workers),
        "network_publication": config.get("network_publication", "disabled"),
        "log_path": str(p["log"]),
        "token_omitted": True,
    })
    return 0


def _launch_foreground_locked(args):
    p = paths()
    pid_record = read_json(p["pid"])
    pid = int(pid_record.get("pid") or 0)
    if process_alive(pid):
        emit({"ok": False, "operation": "host_start", "error": "already_running", "pid": pid, "token_omitted": True})
        return None, p, 2
    if not _host_log_start_preflight(p):
        return None, p, 2
    startup_recovery = _prepare_restart_receipt_recovery(p)
    config, secret_values = require_initialized()
    worker_preflight = host_worker_ownership_preflight(args)
    if not worker_preflight["ok"]:
        return None, p, emit_host_worker_ownership_error(worker_preflight)
    if startup_recovery is not None:
        process, _readiness = _launch_supervised_stack_locked(
            args,
            config,
            secret_values,
            p,
        )
        if process is None:
            _fail_restart_receipt_recovery_start(p, startup_recovery)
            return None, p, 1
        recovery_healthy, terminated = _finish_restart_recovery_with_cleanup(
            p,
            startup_recovery,
            process,
            terminate=_terminate_supervised_child,
        )
        if not recovery_healthy:
            current_record = read_json(p["pid"])
            if terminated and int(current_record.get("pid") or 0) == process.pid:
                p["pid"].unlink(missing_ok=True)
            return None, p, 1
        return process, p, 0
    process = subprocess.Popen(stack_command(config, args), cwd=ROOT, env=host_env(config, secret_values))
    write_managed_pid_record(p["pid"], process, foreground=True)
    return process, p, 0


def _wait_foreground(process, p: dict[str, Path], *, marker_created: bool = False) -> int:
    status = None
    try:
        status = process.wait()
        return status
    finally:
        with lifecycle_lock():
            current_record = read_json(p["pid"])
            if int(current_record.get("pid") or 0) == process.pid:
                p["pid"].unlink(missing_ok=True)
            if marker_created and status not in (None, 0):
                p["ownership"].unlink(missing_ok=True)


def _managed_launch_agent_gate(
    args,
    *,
    service_path: Path | None = None,
    parent_pid: int | None = None,
    launchd_state: dict | None = None,
) -> dict:
    definition = host_service_definition()
    checked = inspect_host_service(service_path or default_host_service_path())
    state = launchd_state if launchd_state is not None else checked["service_state"]
    exact_environment = all(
        os.environ.get(key) == str(value)
        for key, value in definition["EnvironmentVariables"].items()
    )
    try:
        exact_working_directory = Path.cwd().resolve() == Path(definition["WorkingDirectory"]).resolve()
    except OSError:
        exact_working_directory = False
    exact_arguments = bool(
        getattr(args, "command", "") == "start"
        and getattr(args, "foreground", False)
        and getattr(args, "managed_launch_agent", False)
        and getattr(args, "no_workers", False)
        and not getattr(args, "worker", None)
        and not getattr(args, "build_ui", False)
        and not getattr(args, "install_ui", False)
        and not getattr(args, "confirm_live_workers", False)
    )
    exact_service = bool(
        checked["ok"]
        and state.get("loaded") is True
        and state.get("label", HOST_SERVICE_LABEL) == HOST_SERVICE_LABEL
        and (os.getppid() if parent_pid is None else parent_pid) == 1
        and exact_arguments
        and exact_environment
        and exact_working_directory
    )
    return {
        "ok": exact_service,
        "error": None if exact_service else "managed_launch_agent_required",
        "label": HOST_SERVICE_LABEL,
        "template_hash": hashlib.sha256(host_service_template()).hexdigest(),
        "token_omitted": True,
        "paths_omitted": True,
    }


def _open_managed_restart_socket(path: Path) -> socket.socket:
    try:
        parent_metadata = path.parent.lstat()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, mode=0o700)
        parent_metadata = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise RuntimeError("Managed restart directory is unsafe.")
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("Managed restart endpoint is unsafe.")
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(4)
        listener.setblocking(False)
        return listener
    except Exception:
        listener.close()
        if path.exists() and not path.is_symlink() and stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink(missing_ok=True)
        raise


def _remove_managed_restart_socket(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        return
    if not path.is_symlink() and stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.getuid():
        path.unlink(missing_ok=True)


def _private_managed_restart_socket(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return bool(
        not path.is_symlink()
        and stat.S_ISSOCK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _terminate_supervised_child(process, *, timeout: float = HOST_STOP_GRACE_SECONDS) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.terminate()
    except OSError:
        return process.poll() is not None
    try:
        process.wait(timeout=max(0.05, timeout))
        return True
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            return process.poll() is not None
        try:
            process.wait(timeout=max(0.05, min(timeout, 5)))
        except subprocess.TimeoutExpired:
            return False
        return True


def _terminate_background_stack(process, *, timeout: float = HOST_STOP_GRACE_SECONDS) -> bool:
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        return process.poll() is not None
    try:
        process.wait(timeout=max(0.05, timeout))
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            return process.poll() is not None
        try:
            process.wait(timeout=max(0.05, min(timeout, 5)))
        except subprocess.TimeoutExpired:
            return False
        return True


def _launch_supervised_stack_locked(
    args,
    config: dict,
    secret_values: dict,
    p: dict[str, Path],
    *,
    startup_timeout: float = 25,
    popen_factory=None,
    health_check=None,
):
    stack_ready_read_fd, stack_ready_write_fd = os.pipe()
    process = None
    factory = popen_factory or subprocess.Popen
    check_health = health_check or health
    try:
        process = factory(
            stack_command(config, args, stack_ready_fd=stack_ready_write_fd),
            cwd=ROOT,
            env=host_env(config, secret_values),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(stack_ready_write_fd,),
        )
    except Exception:
        os.close(stack_ready_read_fd)
        raise
    finally:
        os.close(stack_ready_write_fd)
    try:
        write_managed_pid_record(p["pid"], process, foreground=True)
        base_url = f"http://{config['host']}:{config['port']}"
        deadline = time.monotonic() + startup_timeout
        stack_ready = False
        readiness = {"reachable": False, "status": "unavailable"}
        while time.monotonic() < deadline and process.poll() is None:
            if not stack_ready:
                readable, _writable, _exceptional = select.select([stack_ready_read_fd], [], [], 0.05)
                if readable:
                    stack_ready = os.read(stack_ready_read_fd, 1) == b"\x01"
            readiness = check_health(base_url)
            if stack_ready and readiness.get("reachable") is True:
                return process, readiness
            time.sleep(0.05)
        _terminate_supervised_child(process)
        current_record = read_json(p["pid"])
        if int(current_record.get("pid") or 0) == process.pid:
            p["pid"].unlink(missing_ok=True)
        return None, readiness
    finally:
        os.close(stack_ready_read_fd)


def _write_managed_service_instance(path: Path, *, child_pid: int, template_hash: str) -> None:
    payload = {
        "schema_version": 1,
        "supervisor_pid": os.getpid(),
        "stack_child_pid": child_pid,
        "label": HOST_SERVICE_LABEL,
        "template_hash": template_hash,
    }
    _atomic_write_service(
        path,
        (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii"),
    )


def _managed_restart_instance_valid(
    record: dict | None,
    p: dict[str, Path],
    *,
    service_path: Path | None = None,
    launchd_state: dict | None = None,
) -> bool:
    if record is None or set(record) != {
        "schema_version",
        "supervisor_pid",
        "stack_child_pid",
        "label",
        "template_hash",
    }:
        return False
    try:
        supervisor_pid = int(record["supervisor_pid"])
        child_pid = int(record["stack_child_pid"])
    except (TypeError, ValueError):
        return False
    expected_hash = hashlib.sha256(host_service_template()).hexdigest()
    checked = inspect_host_service(service_path or default_host_service_path())
    state = launchd_state if launchd_state is not None else checked["service_state"]
    pid_record = _read_private_bounded_json(p["pid"])
    return bool(
        record["schema_version"] == 1
        and record["label"] == HOST_SERVICE_LABEL
        and secrets.compare_digest(str(record["template_hash"]), expected_hash)
        and supervisor_pid > 0
        and child_pid > 0
        and process_alive(supervisor_pid)
        and process_alive(child_pid)
        and pid_record is not None
        and int(pid_record.get("pid") or 0) == child_pid
        and managed_process_record_matches(pid_record, child_pid)
        and _private_managed_restart_socket(p["restart_socket"])
        and checked["ok"]
        and state.get("loaded") is True
    )


def managed_host_restart_status(
    *,
    service_path: Path | None = None,
    launchd_state: dict | None = None,
    _caller_parent_pid_override: int | None = None,
) -> dict:
    p = paths()
    record = _read_private_bounded_json(p["service_instance"])
    caller_parent_pid = os.getppid() if _caller_parent_pid_override is None else _caller_parent_pid_override
    if not _managed_restart_instance_valid(
        record,
        p,
        service_path=service_path,
        launchd_state=launchd_state,
    ) or int((record or {}).get("stack_child_pid") or 0) != caller_parent_pid:
        return {
            "ok": False,
            "operation": "host_managed_restart_status",
            "error": "managed_launch_agent_required",
            "available": False,
            "paths_omitted": True,
            "token_omitted": True,
        }
    return {
        "ok": True,
        "operation": "host_managed_restart_status",
        "available": True,
        "paths_omitted": True,
        "token_omitted": True,
    }


def _managed_restart_request_bytes(
    *,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
) -> bytes:
    if action not in {"enable", "disable"}:
        raise ValueError("invalid_managed_restart_request")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", transition_ref or ""):
        raise ValueError("invalid_managed_restart_request")
    if (
        not isinstance(transaction_sequence, int)
        or isinstance(transaction_sequence, bool)
        or transaction_sequence < 1
        or not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
    ):
        raise ValueError("invalid_managed_restart_request")
    payload = (json.dumps({
        "action": action,
        "expected_revision": expected_revision,
        "transaction_sequence": transaction_sequence,
        "transition_ref": transition_ref,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(payload) > HOST_MANAGED_RESTART_REQUEST_MAX_BYTES:
        raise ValueError("invalid_managed_restart_request")
    return payload


def _receive_managed_restart_request(connection: socket.socket) -> dict | None:
    payload = bytearray()
    while len(payload) <= HOST_MANAGED_RESTART_REQUEST_MAX_BYTES:
        try:
            chunk = connection.recv(min(256, HOST_MANAGED_RESTART_REQUEST_MAX_BYTES + 1 - len(payload)))
        except OSError:
            return None
        if not chunk:
            break
        payload.extend(chunk)
        if b"\n" in chunk:
            break
    if not payload.endswith(b"\n") or len(payload) > HOST_MANAGED_RESTART_REQUEST_MAX_BYTES:
        return None
    try:
        value = json.loads(bytes(payload).decode("ascii"))
    except (UnicodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {
        "action",
        "expected_revision",
        "transaction_sequence",
        "transition_ref",
    }:
        return None
    try:
        expected = _managed_restart_request_bytes(
            action=value["action"],
            transition_ref=value["transition_ref"],
            transaction_sequence=value["transaction_sequence"],
            expected_revision=value["expected_revision"],
        )
    except (KeyError, ValueError):
        return None
    return value if secrets.compare_digest(bytes(payload), expected) else None


def _unix_peer_pid(connection: socket.socket) -> int | None:
    try:
        if hasattr(socket, "SO_PEERCRED"):
            raw = connection.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            pid, _uid, _gid = struct.unpack("3i", raw)
            return pid if pid > 0 else None
        if sys.platform == "darwin":
            raw = connection.getsockopt(0, 2, struct.calcsize("i"))
            pid = struct.unpack("i", raw)[0]
            return pid if pid > 0 else None
    except (OSError, struct.error, ValueError):
        return None
    return None


def _managed_backend_process(pid: int, expected_parent_pid: int) -> bool:
    if pid <= 0 or expected_parent_pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "ppid=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    columns = result.stdout.strip().split(None, 1)
    if result.returncode != 0 or len(columns) != 2:
        return False
    try:
        parent_pid = int(columns[0])
    except ValueError:
        return False
    command = columns[1]
    return bool(
        parent_pid == expected_parent_pid
        and re.search(r"(^|[ /])server\.py([ ]|$)", command)
        and "--host" in command
        and "--port" in command
    )


def _managed_restart_peer_authorized(
    connection: socket.socket,
    expected_parent_pid: int,
    *,
    peer_pid_reader=None,
    backend_checker=None,
) -> bool:
    peer_pid = (peer_pid_reader or _unix_peer_pid)(connection)
    check_backend = backend_checker or _managed_backend_process
    return peer_pid is not None and check_backend(peer_pid, expected_parent_pid)


def _advance_managed_restart_receipt(
    p: dict[str, Path],
    request: dict,
    state: str,
) -> dict:
    projection = relay_restart.transition_restart_receipt(
        receipt_path=p["relay_restart_receipt"],
        sequence_path=p["relay_restart_sequence"],
        action=request["action"],
        transition_ref=request["transition_ref"],
        transaction_sequence=request["transaction_sequence"],
        expected_revision=request["expected_revision"],
        state=state,
    )
    audit_event_pending = False
    if state in relay_restart.TERMINAL_STATES:
        audit_event_pending = not _try_write_managed_restart_audit_event(
            p,
            request,
            state=state,
            revision=int(projection["revision"]),
        )
    return {
        **request,
        "expected_revision": int(projection["revision"]),
        "audit_event_pending": audit_event_pending,
    }


def _try_write_managed_restart_audit_event(
    p: dict[str, Path],
    context: dict,
    *,
    state: str,
    revision: int,
) -> bool:
    audit_outbox = p.get("relay_restart_audit_outbox") or (
        p["relay_restart_receipt"].parent / "restart-audit-outbox"
    )
    try:
        relay_restart.write_restart_audit_event(
            outbox_dir=audit_outbox,
            action=context["action"],
            state=state,
            transaction_sequence=context["transaction_sequence"],
            revision=revision,
            transition_ref=context["transition_ref"],
            nonblocking=True,
        )
        return True
    except relay_restart.RelayRestartError:
        return False


def _try_finalize_managed_restart_receipt(
    p: dict[str, Path],
    context: dict,
) -> bool:
    """Finalize terminal evidence without killing a healthy Host on retryable I/O."""
    try:
        relay_restart.finalize_restart_receipt(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
            action=context["action"],
            transition_ref=context["transition_ref"],
            transaction_sequence=context["transaction_sequence"],
            expected_revision=context["expected_revision"],
        )
        return True
    except relay_restart.RelayRestartError as exc:
        if exc.code in {"audit_event_busy", "write_failed"}:
            return False
        raise


def _managed_relay_runtime_healthy(action: str, p: dict[str, Path]) -> bool:
    projection = relay_connector_projection(p)
    if action == "enable":
        return bool(
            projection.get("enabled") is True
            and projection.get("runtime_ready") is True
            and projection.get("host_tls_ready") is True
        )
    return bool(
        projection.get("enabled") is False
        and projection.get("state") == "disabled"
        and projection.get("ok") is True
    )


def _prepare_restart_receipt_recovery(p: dict[str, Path]) -> dict | None:
    try:
        context = relay_restart.restart_recovery_context(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
        )
    except relay_restart.RelayRestartError as exc:
        if exc.code == "receipt_not_found":
            return None
        raise RuntimeError("Private Host restart recovery is unavailable.") from exc
    context = {**context, "expected_revision": int(context["revision"])}
    state = str(context["state"])
    if state == "rollback_failed":
        _try_write_managed_restart_audit_event(
            p,
            context,
            state=state,
            revision=int(context["revision"]),
        )
        raise RuntimeError("Private Host restart recovery requires operator repair.")
    if state == "config_applied":
        context = _advance_managed_restart_receipt(p, context, "restoring_config")
        state = "restoring_config"
    if state in {"restoring_config", "rolled_back"}:
        projection = relay_restart.ensure_restart_recovery_configs(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
            action=context["action"],
            transition_ref=context["transition_ref"],
            transaction_sequence=context["transaction_sequence"],
            expected_revision=context["expected_revision"],
            use_target=False,
        )
        if state == "rolled_back":
            _try_write_managed_restart_audit_event(
                p,
                context,
                state=state,
                revision=int(projection["revision"]),
            )
            return None
        return {
            **context,
            "expected_revision": int(projection["revision"]),
            "recovery_mode": "original",
        }
    if state in {
        "response_flushed",
        "restart_requested",
        "validating_new_host",
        "manual_restart_required",
        "healthy",
    }:
        projection = relay_restart.ensure_restart_recovery_configs(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
            action=context["action"],
            transition_ref=context["transition_ref"],
            transaction_sequence=context["transaction_sequence"],
            expected_revision=context["expected_revision"],
            use_target=True,
        )
        return {
            **context,
            "expected_revision": int(projection["revision"]),
            "recovery_mode": "target",
        }
    raise RuntimeError("Private Host restart recovery state is invalid.")


def _finish_restart_receipt_recovery(
    p: dict[str, Path],
    context: dict | None,
    *,
    runtime_validator=None,
) -> bool:
    if context is None:
        return True
    validate_runtime = runtime_validator or _managed_relay_runtime_healthy
    if context["recovery_mode"] == "original":
        original_action = "disable" if context["action"] == "enable" else "enable"
        try:
            original_healthy = bool(validate_runtime(original_action, p))
        except Exception:
            original_healthy = False
        terminal_state = "rolled_back" if original_healthy else "rollback_failed"
        _advance_managed_restart_receipt(p, context, terminal_state)
        return terminal_state == "rolled_back"

    state = str(context["state"])
    if state == "response_flushed":
        context = _advance_managed_restart_receipt(p, context, "restart_requested")
        state = "restart_requested"
    if state in {"restart_requested", "manual_restart_required"}:
        context = _advance_managed_restart_receipt(p, context, "validating_new_host")
        state = "validating_new_host"
    try:
        target_healthy = bool(validate_runtime(context["action"], p))
    except Exception:
        target_healthy = False
    if target_healthy:
        if state != "healthy":
            context = _advance_managed_restart_receipt(p, context, "healthy")
        else:
            context = {
                **context,
                "audit_event_pending": not _try_write_managed_restart_audit_event(
                    p,
                    context,
                    state="healthy",
                    revision=int(context["expected_revision"]),
                ),
            }
        if context.get("audit_event_pending") is True:
            return True
        _try_finalize_managed_restart_receipt(p, context)
        return True

    if state != "restoring_config":
        context = _advance_managed_restart_receipt(p, context, "restoring_config")
    try:
        relay_restart.ensure_restart_recovery_configs(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
            action=context["action"],
            transition_ref=context["transition_ref"],
            transaction_sequence=context["transaction_sequence"],
            expected_revision=context["expected_revision"],
            use_target=False,
        )
    except relay_restart.RelayRestartError:
        try:
            _advance_managed_restart_receipt(p, context, "rollback_failed")
        except relay_restart.RelayRestartError:
            pass
        raise
    return False


def _fail_restart_receipt_recovery_start(
    p: dict[str, Path],
    context: dict | None,
) -> None:
    if context is None:
        return
    if context["recovery_mode"] == "original":
        try:
            _advance_managed_restart_receipt(p, context, "rollback_failed")
        except relay_restart.RelayRestartError:
            pass
        return
    try:
        context = _advance_managed_restart_receipt(p, context, "restoring_config")
        relay_restart.ensure_restart_recovery_configs(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
            action=context["action"],
            transition_ref=context["transition_ref"],
            transaction_sequence=context["transaction_sequence"],
            expected_revision=context["expected_revision"],
            use_target=False,
        )
    except relay_restart.RelayRestartError:
        try:
            _advance_managed_restart_receipt(p, context, "rollback_failed")
        except relay_restart.RelayRestartError:
            pass


def _mark_restart_recovery_rollback_failed(
    p: dict[str, Path],
) -> None:
    try:
        latest = relay_restart.restart_recovery_context(
            receipt_path=p["relay_restart_receipt"],
            sequence_path=p["relay_restart_sequence"],
        )
    except relay_restart.RelayRestartError:
        return
    context = {**latest, "expected_revision": int(latest["revision"])}
    if context["state"] == "rollback_failed":
        return
    try:
        if context.get("state") != "restoring_config":
            context = _advance_managed_restart_receipt(p, context, "restoring_config")
        _advance_managed_restart_receipt(p, context, "rollback_failed")
    except relay_restart.RelayRestartError:
        pass


def _finish_restart_recovery_with_cleanup(
    p: dict[str, Path],
    context: dict | None,
    process,
    *,
    terminate,
    runtime_validator=None,
) -> tuple[bool, bool]:
    def clear_pid_record_if_terminated(terminated: bool) -> None:
        if not terminated:
            return
        current_record = read_json(p["pid"])
        if int(current_record.get("pid") or 0) == process.pid:
            p["pid"].unlink(missing_ok=True)

    try:
        recovery_healthy = _finish_restart_receipt_recovery(
            p,
            context,
            runtime_validator=runtime_validator,
        )
    except Exception:
        terminated = bool(terminate(process))
        clear_pid_record_if_terminated(terminated)
        if not terminated:
            _mark_restart_recovery_rollback_failed(p)
        raise
    if recovery_healthy:
        return True, False
    terminated = bool(terminate(process))
    clear_pid_record_if_terminated(terminated)
    if not terminated:
        _mark_restart_recovery_rollback_failed(p)
    return False, terminated


def request_managed_host_restart(
    *,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
    service_path: Path | None = None,
    launchd_state: dict | None = None,
    timeout: float = 2,
    _caller_parent_pid_override: int | None = None,
) -> dict:
    status = managed_host_restart_status(
        service_path=service_path,
        launchd_state=launchd_state,
        _caller_parent_pid_override=_caller_parent_pid_override,
    )
    if status.get("available") is not True:
        return {
            **status,
            "operation": "host_managed_restart_request",
            "accepted": False,
        }
    try:
        request_bytes = _managed_restart_request_bytes(
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=transaction_sequence,
            expected_revision=expected_revision,
        )
    except ValueError:
        return {
            "ok": False,
            "operation": "host_managed_restart_request",
            "accepted": False,
            "error": "managed_restart_request_invalid",
            "paths_omitted": True,
            "token_omitted": True,
        }
    p = paths()
    requester = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    requester.settimeout(max(0.1, min(timeout, 5)))
    try:
        requester.connect(str(p["restart_socket"]))
        requester.sendall(request_bytes)
        accepted = requester.recv(1) == b"\x01"
    except OSError:
        accepted = False
    finally:
        requester.close()
    return {
        "ok": accepted,
        "operation": "host_managed_restart_request",
        "accepted": accepted,
        **({"error": "managed_restart_request_failed"} if not accepted else {}),
        "paths_omitted": True,
        "token_omitted": True,
    }


def _run_managed_foreground_supervisor(
    args,
    config: dict,
    secret_values: dict,
    p: dict[str, Path],
    listener: socket.socket,
    template_hash: str,
    *,
    marker_created: bool = False,
    startup_timeout: float = 25,
    stop_timeout: float = HOST_STOP_GRACE_SECONDS,
    popen_factory=None,
    health_check=None,
    config_loader=None,
    relay_runtime_validator=None,
    peer_authorizer=None,
    startup_recovery=None,
    startup_lock_descriptor: int | None = None,
    install_signal_handlers: bool = True,
) -> int:
    stopping = False
    previous_handlers: dict[int, object] = {}

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    try:
        if install_signal_handlers:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_stop)
    except Exception:
        if startup_lock_descriptor is not None:
            _release_lifecycle_lock(startup_lock_descriptor)
        raise

    process = None
    completed_status = None
    restart_lock_descriptor = (
        startup_lock_descriptor
        if startup_lock_descriptor is not None
        else _acquire_lifecycle_lock()
    )
    try:
        process, _readiness = _launch_supervised_stack_locked(
            args,
            config,
            secret_values,
            p,
            startup_timeout=startup_timeout,
            popen_factory=popen_factory,
            health_check=health_check,
        )
        if process is None:
            _fail_restart_receipt_recovery_start(p, startup_recovery)
            completed_status = 1
            return completed_status
        _write_managed_service_instance(
            p["service_instance"],
            child_pid=process.pid,
            template_hash=template_hash,
        )
        recovery_healthy, _terminated = _finish_restart_recovery_with_cleanup(
            p,
            startup_recovery,
            process,
            terminate=lambda child: _terminate_supervised_child(
                child,
                timeout=stop_timeout,
            ),
            runtime_validator=relay_runtime_validator,
        )
        if not recovery_healthy:
            completed_status = 1
            return completed_status
        _release_lifecycle_lock(restart_lock_descriptor)
        restart_lock_descriptor = -1
        while True:
            if stopping:
                completed_status = 0 if _terminate_supervised_child(process, timeout=stop_timeout) else 1
                return completed_status
            status = process.poll()
            if status is not None:
                completed_status = status
                return completed_status
            readable, _writable, _exceptional = select.select([listener], [], [], 0.1)
            if not readable:
                continue
            try:
                connection, _address = listener.accept()
            except BlockingIOError:
                continue
            restart_request = None
            with connection:
                connection.settimeout(1)
                authorize_peer = peer_authorizer or _managed_restart_peer_authorized
                peer_authorized = bool(authorize_peer(connection, process.pid))
                if peer_authorized:
                    restart_request = _receive_managed_restart_request(connection)
                accepted = False
                if restart_request is not None:
                    try:
                        restart_lock_descriptor = _acquire_lifecycle_lock()
                        restart_request = _advance_managed_restart_receipt(
                            p,
                            restart_request,
                            "restart_requested",
                        )
                        accepted = True
                    except relay_restart.RelayRestartError:
                        if restart_lock_descriptor >= 0:
                            _release_lifecycle_lock(restart_lock_descriptor)
                            restart_lock_descriptor = -1
                        restart_request = None
                try:
                    connection.sendall(b"\x01" if accepted else b"\x00")
                except OSError:
                    pass
            if not accepted or restart_request is None:
                continue
            if not _terminate_supervised_child(process, timeout=stop_timeout):
                completed_status = 1
                return completed_status
            current_record = read_json(p["pid"])
            if int(current_record.get("pid") or 0) == process.pid:
                p["pid"].unlink(missing_ok=True)
            restart_request = _advance_managed_restart_receipt(
                p,
                restart_request,
                "validating_new_host",
            )
            replacement_healthy = False
            try:
                replacement_config, replacement_secrets = (config_loader or require_initialized)()
                process, _readiness = _launch_supervised_stack_locked(
                    args,
                    replacement_config,
                    replacement_secrets,
                    p,
                    startup_timeout=startup_timeout,
                    popen_factory=popen_factory,
                    health_check=health_check,
                )
                if process is not None:
                    _write_managed_service_instance(
                        p["service_instance"],
                        child_pid=process.pid,
                        template_hash=template_hash,
                    )
                    validate_runtime = relay_runtime_validator or _managed_relay_runtime_healthy
                    replacement_healthy = bool(validate_runtime(restart_request["action"], p))
            except (OSError, RuntimeError, relay_restart.RelayRestartError):
                replacement_healthy = False
            if replacement_healthy:
                healthy = _advance_managed_restart_receipt(p, restart_request, "healthy")
                if healthy.get("audit_event_pending") is not True:
                    _try_finalize_managed_restart_receipt(p, healthy)
                _release_lifecycle_lock(restart_lock_descriptor)
                restart_lock_descriptor = -1
                continue
            if (
                process is not None
                and process.poll() is None
                and not _terminate_supervised_child(process, timeout=stop_timeout)
            ):
                try:
                    restart_request = _advance_managed_restart_receipt(
                        p,
                        restart_request,
                        "restoring_config",
                    )
                    _advance_managed_restart_receipt(
                        p,
                        restart_request,
                        "rollback_failed",
                    )
                except relay_restart.RelayRestartError:
                    pass
                completed_status = 1
                return completed_status
            current_record = read_json(p["pid"])
            if process is not None and int(current_record.get("pid") or 0) == process.pid:
                p["pid"].unlink(missing_ok=True)
            restart_request = _advance_managed_restart_receipt(
                p,
                restart_request,
                "restoring_config",
            )
            try:
                relay_restart.restore_original_configs(
                    receipt_path=p["relay_restart_receipt"],
                    sequence_path=p["relay_restart_sequence"],
                    action=restart_request["action"],
                    transition_ref=restart_request["transition_ref"],
                    transaction_sequence=restart_request["transaction_sequence"],
                    expected_revision=restart_request["expected_revision"],
                )
            except relay_restart.RelayRestartError:
                try:
                    _advance_managed_restart_receipt(
                        p,
                        restart_request,
                        "rollback_failed",
                    )
                except relay_restart.RelayRestartError:
                    pass
                completed_status = 1
                return completed_status
            rollback_healthy = False
            try:
                rollback_config, rollback_secrets = (config_loader or require_initialized)()
                process, _readiness = _launch_supervised_stack_locked(
                    args,
                    rollback_config,
                    rollback_secrets,
                    p,
                    startup_timeout=startup_timeout,
                    popen_factory=popen_factory,
                    health_check=health_check,
                )
                if process is not None:
                    _write_managed_service_instance(
                        p["service_instance"],
                        child_pid=process.pid,
                        template_hash=template_hash,
                    )
                    validate_runtime = relay_runtime_validator or _managed_relay_runtime_healthy
                    original_action = "disable" if restart_request["action"] == "enable" else "enable"
                    rollback_healthy = bool(validate_runtime(original_action, p))
            except (OSError, RuntimeError, relay_restart.RelayRestartError):
                rollback_healthy = False
            terminal_state = "rolled_back" if rollback_healthy else "rollback_failed"
            restart_request = _advance_managed_restart_receipt(
                p,
                restart_request,
                terminal_state,
            )
            if not rollback_healthy:
                completed_status = 1
                return completed_status
            _release_lifecycle_lock(restart_lock_descriptor)
            restart_lock_descriptor = -1
    finally:
        if restart_lock_descriptor >= 0:
            _release_lifecycle_lock(restart_lock_descriptor)
        if process is not None and process.poll() is None:
            _terminate_supervised_child(process, timeout=stop_timeout)
        listener.close()
        _remove_managed_restart_socket(p["restart_socket"])
        with lifecycle_lock():
            instance = _read_private_bounded_json(p["service_instance"])
            if instance and int(instance.get("supervisor_pid") or 0) == os.getpid():
                p["service_instance"].unlink(missing_ok=True)
            if process is not None:
                current_record = read_json(p["pid"])
                if int(current_record.get("pid") or 0) == process.pid:
                    p["pid"].unlink(missing_ok=True)
            if marker_created and completed_status != 0:
                p["ownership"].unlink(missing_ok=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def cmd_start(args) -> int:
    if not args.foreground:
        with lifecycle_lock():
            marker_created = ensure_host_data_marker(allow_legacy=True)
            try:
                status = _cmd_start_unlocked(args)
            except Exception:
                if marker_created:
                    paths()["ownership"].unlink(missing_ok=True)
                raise
            if marker_created and status != 0:
                paths()["ownership"].unlink(missing_ok=True)
            return status
    if getattr(args, "managed_launch_agent", False):
        startup_lock_descriptor = _acquire_lifecycle_lock()
        supervisor_owns_lock = False
        try:
            marker_created = ensure_host_data_marker(allow_legacy=True)
            p = paths()
            pid_record = read_json(p["pid"])
            if process_alive(int(pid_record.get("pid") or 0)):
                if marker_created:
                    p["ownership"].unlink(missing_ok=True)
                emit({"ok": False, "operation": "host_start", "error": "already_running", "token_omitted": True})
                return 2
            if not _host_log_start_preflight(p):
                if marker_created:
                    p["ownership"].unlink(missing_ok=True)
                return 2
            startup_recovery = _prepare_restart_receipt_recovery(p)
            config, secret_values = require_initialized()
            gate = _managed_launch_agent_gate(args)
            if not gate["ok"]:
                if marker_created:
                    p["ownership"].unlink(missing_ok=True)
                emit({
                    "ok": False,
                    "operation": "host_start",
                    "error": gate["error"],
                    "paths_omitted": True,
                    "token_omitted": True,
                })
                return 2
            worker_preflight = host_worker_ownership_preflight(args)
            if not worker_preflight["ok"]:
                if marker_created:
                    p["ownership"].unlink(missing_ok=True)
                return emit_host_worker_ownership_error(worker_preflight)
            try:
                listener = _open_managed_restart_socket(p["restart_socket"])
            except Exception:
                if marker_created:
                    p["ownership"].unlink(missing_ok=True)
                raise
            supervisor_owns_lock = True
            return _run_managed_foreground_supervisor(
                args,
                config,
                secret_values,
                p,
                listener,
                gate["template_hash"],
                marker_created=marker_created,
                startup_recovery=startup_recovery,
                startup_lock_descriptor=startup_lock_descriptor,
            )
        finally:
            if not supervisor_owns_lock:
                _release_lifecycle_lock(startup_lock_descriptor)
    with lifecycle_lock():
        marker_created = ensure_host_data_marker(allow_legacy=True)
        try:
            process, p, status = _launch_foreground_locked(args)
        except Exception:
            if marker_created:
                paths()["ownership"].unlink(missing_ok=True)
            raise
        if process is None and marker_created:
            p["ownership"].unlink(missing_ok=True)
    return status if process is None else _wait_foreground(process, p, marker_created=marker_created)


def cmd_status(_args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    pid_record = read_json(p["pid"])
    pid = int(pid_record.get("pid") or 0)
    running = process_alive(pid)
    base_url = f"http://{config['host']}:{config['port']}"
    readiness = health(base_url) if running else {"reachable": False, "status": "stopped"}
    ts = tailscale_state()
    target = f"http://{config['host']}:{config['port']}"
    https_port = int(config.get("tailscale_https_port") or 443)
    binary, _source = tailscale_binary()
    serve = tailscale_serve_state(binary, target, https_port)
    publication_enabled = config.get("network_publication") == "tailscale_serve"
    port_suffix = "" if https_port == 443 else f":{https_port}"
    private_origin = f"https://{ts['dns_name']}{port_suffix}" if publication_enabled and ts["dns_name"] else ""
    configured_origin = str(config.get("private_console_origin") or "").rstrip("/")
    origin_matches_config = bool(private_origin and configured_origin == private_origin and private_origin in (config.get("allowed_origins") or []))
    private_url = private_origin + "/workspace" if private_origin else ""
    private_url_ready = bool(
        private_url
        and running
        and readiness["reachable"]
        and origin_matches_config
        and ts["backend_state"] == "Running"
        and serve["status_available"]
        and serve["target_matches"]
        and not serve["conflict"]
    )
    ui_dist, ui_dist_managed = effective_ui_dist(config)
    human_access = human_access_state(
        loopback_base_url(config.get("host"), int(config["port"])) or "",
        running=running,
        reachable=bool(readiness["reachable"]),
    )
    relay_connector = relay_connector_projection(p)
    next_actions = []
    if human_access["status"] == "bootstrap_required":
        next_actions.append(f"Open {base_url}/workspace on this Host to create the first Owner.")
        next_actions.append("CLI recovery only: agentops host bootstrap-owner --confirm")
    elif human_access["status"] in {"host_stopped", "unavailable"}:
        next_actions.append("Run agentops host start, then check Owner readiness again.")
    emit({
        "ok": running and readiness["reachable"],
        "operation": "host_status",
        "running": running,
        "pid": pid if running else None,
        "health": readiness,
        "local_console_url": base_url + "/workspace",
        "private_console_url": private_url,
        "private_url_ready": private_url_ready,
        "network_publication": config.get("network_publication", "disabled"),
        "private_origin_matches_config": origin_matches_config,
        "serve": serve,
        "tailscale": ts,
        "database_path": config["database_path"],
        "ui_dist": str(ui_dist),
        "ui_dist_managed": ui_dist_managed,
        "human_access": human_access,
        "relay_connector": relay_connector,
        "next_actions": next_actions,
        "token_omitted": True,
    })
    return 0 if running and readiness["reachable"] else 1


def cmd_storage_preflight(args) -> int:
    payload = host_storage_preflight(minimum_free_bytes=args.minimum_free_bytes)
    emit(payload)
    return 0 if payload["ok"] else 1


def _cmd_stop_unlocked(_args) -> int:
    p = paths()
    record = read_json(p["pid"])
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        emit({"ok": False, "operation": "host_stop", "status": "invalid_pid_record", "token_omitted": True})
        return 2
    if not process_alive(pid):
        p["pid"].unlink(missing_ok=True)
        emit({"ok": True, "operation": "host_stop", "status": "already_stopped", "token_omitted": True})
        return 0
    if not managed_process_record_matches(record, pid):
        emit({
            "ok": False,
            "operation": "host_stop",
            "status": "process_identity_unverified",
            "pid": pid,
            "token_omitted": True,
        })
        return 2
    try:
        if int(record["process_group_id"]) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        emit({"ok": False, "operation": "host_stop", "status": "termination_failed", "pid": pid, "token_omitted": True})
        return 1
    deadline = time.time() + HOST_STOP_GRACE_SECONDS
    while time.time() < deadline and process_alive(pid):
        time.sleep(0.2)
    if process_alive(pid):
        emit({"ok": False, "operation": "host_stop", "status": "timeout", "pid": pid, "token_omitted": True})
        return 1
    p["pid"].unlink(missing_ok=True)
    emit({"ok": True, "operation": "host_stop", "status": "stopped", "token_omitted": True})
    return 0


def cmd_stop(args) -> int:
    with lifecycle_lock():
        return _cmd_stop_unlocked(args)


def cmd_restart(args) -> int:
    foreground_process = None
    foreground_paths = None
    marker_created = False
    with lifecycle_lock():
        stop_output = io.StringIO()
        with contextlib.redirect_stdout(stop_output):
            stop_code = _cmd_stop_unlocked(args)
        try:
            stop_result = json.loads(stop_output.getvalue())
        except ValueError:
            stop_result = {}
        if stop_code != 0:
            emit({
                "ok": False,
                "operation": "host_restart",
                "error": "stop_failed",
                "stop_status": stop_result.get("status", "unknown"),
                "token_omitted": True,
            })
            return stop_code
        marker_created = ensure_host_data_marker(allow_legacy=True)
        try:
            if getattr(args, "foreground", False):
                foreground_process, foreground_paths, start_code = _launch_foreground_locked(args)
                if foreground_process is None:
                    if marker_created:
                        paths()["ownership"].unlink(missing_ok=True)
                    return start_code
            else:
                start_output = io.StringIO()
                with contextlib.redirect_stdout(start_output):
                    start_code = _cmd_start_unlocked(args)
                try:
                    start_result = json.loads(start_output.getvalue())
                except ValueError:
                    start_result = {}
                if marker_created and start_code != 0:
                    paths()["ownership"].unlink(missing_ok=True)
        except Exception:
            if marker_created:
                paths()["ownership"].unlink(missing_ok=True)
            raise
    if foreground_process is not None:
        return _wait_foreground(foreground_process, foreground_paths, marker_created=marker_created)
    start_result.update({
        "operation": "host_restart",
        "stop_status": stop_result.get("status", "stopped"),
        "token_omitted": True,
    })
    emit(start_result)
    return start_code


def host_doctor_next_actions(
    *,
    gates: list[dict],
    tailscale: dict,
    human_access: dict,
    base_url: str,
    ui_dist_managed: bool,
) -> list[str]:
    gate_state = {str(gate.get("id") or ""): bool(gate.get("ok")) for gate in gates}
    actions: list[str] = []

    def add_action(action: str) -> None:
        if action not in actions:
            actions.append(action)

    if human_access.get("status") == "bootstrap_required":
        actions.extend([
            f"Open {base_url}/workspace on this Host to create the first Owner.",
            "CLI recovery only: agentops host bootstrap-owner --confirm",
        ])
    elif human_access.get("status") == "host_stopped":
        actions.append("Run agentops host start to verify human login readiness.")

    if not gate_state.get("production_ui", False):
        add_action(
            "Repair or reinstall the Private Host package before starting the Host."
            if ui_dist_managed
            else "Run agentops host start --build-ui after providing the production UI bundle."
        )
    if not gate_state.get("stack_entrypoint", False):
        add_action("Repair or reinstall the Private Host package before starting the Host.")

    if not tailscale.get("installed"):
        actions.append("Optional private Console: install and sign in to Tailscale on both devices.")
    elif tailscale.get("backend_state") != "Running":
        actions.append("Optional private Console: open Tailscale on this Host and sign in.")
    elif not tailscale.get("dns_name"):
        actions.append("Optional private Console: wait for this Host to receive its Tailscale DNS name.")
    return actions


def cmd_doctor(_args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    ui_dist, ui_dist_managed = effective_ui_dist(config)
    relay_connector = relay_connector_projection(p)
    gates = [
        {"id": "config_private", "ok": (p["config"].stat().st_mode & 0o077) == 0},
        {"id": "secrets_private", "ok": (p["secrets"].stat().st_mode & 0o077) == 0},
        {"id": "database_parent_private", "ok": (p["data"].stat().st_mode & 0o077) == 0},
        {"id": "production_ui", "ok": (ui_dist / "index.html").is_file()},
        {"id": "stack_entrypoint", "ok": STACK.is_file()},
        {
            "id": "relay_connector_safe_default",
            "ok": bool(
                relay_connector["config_valid"]
                and not relay_connector["enabled"]
            ),
        },
    ]
    ts = tailscale_state()
    pid = int(read_json(p["pid"]).get("pid") or 0)
    running = process_alive(pid)
    base_url = f"http://{config['host']}:{config['port']}"
    readiness = health(base_url) if running else {"reachable": False, "status": "stopped"}
    human_access = human_access_state(
        loopback_base_url(config.get("host"), int(config["port"])) or "",
        running=running,
        reachable=bool(readiness["reachable"]),
    )
    next_actions = host_doctor_next_actions(
        gates=gates,
        tailscale=ts,
        human_access=human_access,
        base_url=base_url,
        ui_dist_managed=ui_dist_managed,
    )
    emit({
        "ok": all(gate["ok"] for gate in gates),
        "operation": "host_doctor",
        "gates": gates,
        "ui_dist": str(ui_dist),
        "ui_dist_managed": ui_dist_managed,
        "tailscale": ts,
        "host_health": readiness,
        "human_access": human_access,
        "relay_connector": relay_connector,
        "next_actions": next_actions,
        "token_omitted": True,
    })
    return 0 if all(gate["ok"] for gate in gates) else 1


def cmd_logs(_args) -> int:
    p = paths()
    size = p["log"].stat().st_size if p["log"].exists() else 0
    emit({
        "ok": p["log"].exists(),
        "operation": "host_logs",
        "log_path": str(p["log"]),
        "size_bytes": size,
        "content_omitted": True,
        "token_omitted": True,
    })
    return 0 if p["log"].exists() else 1


def _cmd_log_rotate_unlocked(args) -> int:
    require_initialized()
    p = paths()
    maintenance_lock_fd = None
    if args.confirm_rotate:
        try:
            maintenance_lock_fd = runtime_lock.acquire_runtime_lock(p["runtime_lock"])
        except runtime_lock.RuntimeLockError as exc:
            running = exc.code == "held"
            emit({
                "ok": False,
                "operation": "host_log_rotate",
                "dry_run": False,
                "error": "host_running" if running else "host_runtime_state_unverifiable",
                "message": (
                    "Stop the Host before confirming log rotation."
                    if running
                    else "The Host runtime lock could not be verified safely."
                ),
                "next_action": "agentops host stop" if running else "agentops host doctor",
                "content_omitted": True,
                "paths_omitted": True,
                "token_omitted": True,
            })
            return 2
    try:
        p["pid"].lstat()
        pid_exists = True
    except FileNotFoundError:
        pid_exists = False
    except OSError:
        pid_exists = True
    pid_record = _read_private_bounded_json(p["pid"], maximum_bytes=4096) or {}
    pid_record_unsafe = pid_exists and not pid_record
    try:
        pid = int(pid_record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = -1
        pid_record_unsafe = True
    if args.confirm_rotate and pid_record_unsafe:
        runtime_lock.release_runtime_lock(maintenance_lock_fd)
        maintenance_lock_fd = None
        emit({
            "ok": False,
            "operation": "host_log_rotate",
            "dry_run": False,
            "error": "host_pid_record_unverifiable",
            "content_omitted": True,
            "paths_omitted": True,
            "token_omitted": True,
        })
        return 2
    running = process_alive(pid)
    if args.confirm_rotate and running:
        runtime_lock.release_runtime_lock(maintenance_lock_fd)
        maintenance_lock_fd = None
        emit({
            "ok": False,
            "operation": "host_log_rotate",
            "dry_run": False,
            "error": "host_running",
            "message": "Stop the Host before confirming log rotation.",
            "next_action": "agentops host stop",
            "content_omitted": True,
            "paths_omitted": True,
            "token_omitted": True,
        })
        return 2
    try:
        payload, status = host_log.rotate_logs(
            p["logs"],
            max_bytes=args.max_bytes,
            backups=args.backups,
            confirm_rotate=args.confirm_rotate,
            plan_hash=args.plan_hash,
        )
    finally:
        runtime_lock.release_runtime_lock(maintenance_lock_fd)
    payload["host_running"] = running
    payload["authority_ledger_unchanged"] = True
    payload["secret_store_unchanged"] = True
    payload["launchd_log_content_identity_preserved"] = True
    if (
        payload.get("error") == "host_log_inventory_permissions_unsafe"
        and payload.get("entry_label") == "launchd.log"
    ):
        service_log = inspect_host_service_log()
        repair_candidate = bool(
            service_log.get("ok") is True
            and service_log.get("preparation_required") is True
        )
        payload["service_log_repair_candidate"] = repair_candidate
        payload["service_check_required"] = repair_candidate
        payload["manual_recovery_required"] = not repair_candidate
        if repair_candidate:
            payload["next_action"] = "agentops host service-check"
    emit(payload)
    return status


def cmd_log_rotate(args) -> int:
    if not args.confirm_rotate:
        return _cmd_log_rotate_unlocked(args)
    with lifecycle_lock():
        return _cmd_log_rotate_unlocked(args)


def run_backup_utility(*arguments: str) -> tuple[dict, int]:
    if not BACKUP_UTILITY.is_file():
        return {"ok": False, "error": "backup_utility_missing"}, 1
    process = subprocess.run(
        [sys.executable, str(BACKUP_UTILITY), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        payload = {"ok": False, "error": "backup_utility_invalid_response"}
    payload["utility_output_omitted"] = True
    payload["token_omitted"] = True
    return payload, process.returncode


def cmd_backup(_args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    p["backups"].mkdir(parents=True, exist_ok=True, mode=0o700)
    p["backups"].chmod(0o700)
    payload, status = run_backup_utility(
        "create",
        "--db-path",
        config["database_path"],
        "--backup-dir",
        str(p["backups"]),
    )
    for key in ("backup_path", "manifest_path"):
        value = payload.get(key)
        if value and Path(str(value)).is_file():
            Path(str(value)).chmod(0o600)
    payload["operation"] = "host_backup"
    payload["secret_store_included"] = False
    payload["hashed_auth_state_included"] = True
    payload["host_may_remain_running"] = True
    emit(payload)
    return status


def cmd_backup_verify(args) -> int:
    require_initialized()
    p = paths()
    arguments = ["verify"]
    if args.backup:
        arguments.extend(["--backup", str(Path(args.backup).expanduser().resolve())])
    else:
        arguments.extend(["--backup-dir", str(p["backups"])])
    payload, status = run_backup_utility(*arguments)
    payload["operation"] = "host_backup_verify"
    payload["read_only"] = True
    emit(payload)
    return status


def _cmd_backup_prune_unlocked(args) -> int:
    require_initialized()
    p = paths()
    arguments = [
        "prune",
        "--backup-dir",
        str(p["backups"]),
        "--keep",
        str(args.keep),
    ]
    if args.confirm_prune:
        arguments.append("--confirm-prune")
    if args.plan_hash:
        arguments.extend(["--plan-hash", args.plan_hash])
    payload, status = run_backup_utility(*arguments)
    payload["operation"] = "host_backup_prune"
    payload["authority_ledger_unchanged"] = True
    payload["secret_store_included"] = False
    payload["backup_content_omitted"] = True
    emit(payload)
    return status


def cmd_backup_prune(args) -> int:
    if not args.confirm_prune:
        return _cmd_backup_prune_unlocked(args)
    with lifecycle_lock():
        return _cmd_backup_prune_unlocked(args)


def _cmd_restore_unlocked(args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    pid = int(read_json(p["pid"]).get("pid") or 0)
    if process_alive(pid):
        emit({
            "ok": False,
            "operation": "host_restore",
            "error": "host_running",
            "message": "Stop the Host before restoring its authority ledger.",
            "next_action": "agentops host stop",
            "token_omitted": True,
        })
        return 2
    if not args.confirm_restore:
        emit({
            "ok": False,
            "operation": "host_restore",
            "dry_run": True,
            "error": "confirm_restore_required",
            "message": "Re-run with --confirm-restore after verifying the selected backup.",
            "token_omitted": True,
        })
        return 2
    arguments = [
        "restore",
        "--backup",
        str(Path(args.backup).expanduser().resolve()),
        "--target",
        config["database_path"],
        "--confirm-restore",
    ]
    if Path(config["database_path"]).exists():
        arguments.append("--overwrite")
    payload, status = run_backup_utility(*arguments)
    for key in ("target_path", "pre_restore_copy"):
        value = payload.get(key)
        if value and Path(str(value)).is_file():
            Path(str(value)).chmod(0o600)
    payload["operation"] = "host_restore"
    payload["secret_store_restored"] = False
    payload["hashed_auth_records_restored"] = True
    payload["restored_auth_state_revoked"] = True
    payload["restart_required"] = status == 0
    payload["next_action"] = "agentops host start" if status == 0 else "Verify the backup and retry."
    emit(payload)
    return status


def cmd_restore(args) -> int:
    with lifecycle_lock():
        return _cmd_restore_unlocked(args)


def install_state() -> dict:
    install_root = host_install_root()
    current = install_root / "current"
    previous = install_root / "previous"

    def release(link: Path) -> dict:
        if not link.is_symlink():
            return {}
        target = link.resolve()
        versions = (install_root / "versions").resolve()
        if versions not in target.parents or not target.is_dir():
            return {}
        manifest = read_json(target / "release-manifest.json")
        if not manifest:
            return {}
        return {
            "version": manifest.get("version"),
            "git_commit": manifest.get("git_commit"),
            "target": str(target),
        }

    return {
        "install_root": install_root,
        "current_link": current,
        "previous_link": previous,
        "current": release(current),
        "previous": release(previous),
    }


def default_host_service_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{HOST_SERVICE_LABEL}.plist"


def host_service_path(value: str = "") -> Path:
    return Path(value).expanduser().absolute() if value else default_host_service_path().absolute()


def host_service_definition(*, managed_launch_agent: bool = True) -> dict:
    state = install_state()
    packaged = bool(state["current"])
    current_path = Path(state["current_link"]) if packaged else ROOT
    install_root = Path(state["install_root"]).resolve()
    p = paths()
    program_arguments = [
        str(Path(sys.executable).resolve()),
        "-m",
        "agentops_mis_cli",
        "host",
        "start",
        "--foreground",
    ]
    if managed_launch_agent:
        program_arguments.append("--managed-launch-agent")
    program_arguments.append("--no-workers")
    return {
        "Label": HOST_SERVICE_LABEL,
        "ProgramArguments": program_arguments,
        "EnvironmentVariables": {
            "AGENTOPS_HOST_HOME": str(p["home"]),
            "AGENTOPS_INSTALL_ROOT": str(install_root),
            "PYTHONPATH": str(current_path),
        },
        "WorkingDirectory": str(current_path),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(p["logs"] / "launchd.log"),
        "StandardErrorPath": str(p["logs"] / "launchd.log"),
    }


def host_service_template(*, managed_launch_agent: bool = True) -> bytes:
    service = host_service_definition(managed_launch_agent=managed_launch_agent)
    arguments = "\n".join(
        f"      <string>{html.escape(str(value))}</string>"
        for value in service["ProgramArguments"]
    )
    environment = "\n".join(
        "\n".join(
            (
                f"      <key>{html.escape(str(key))}</key>",
                f"      <string>{html.escape(str(value))}</string>",
            )
        )
        for key, value in sorted(service["EnvironmentVariables"].items())
    )
    rendered = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{html.escape(service['Label'])}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{environment}
  </dict>
  <key>WorkingDirectory</key>
  <string>{html.escape(service['WorkingDirectory'])}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>{service['ThrottleInterval']}</integer>
  <key>StandardOutPath</key>
  <string>{html.escape(service['StandardOutPath'])}</string>
  <key>StandardErrorPath</key>
  <string>{html.escape(service['StandardErrorPath'])}</string>
</dict>
</plist>
"""
    return rendered.encode("utf-8")


def launchctl_binary() -> str | None:
    override = os.environ.get("AGENTOPS_LAUNCHCTL_BIN", "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    candidate = Path("/bin/launchctl")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else shutil.which("launchctl")


def host_service_launchd_state(*, timeout: int = 5) -> dict:
    binary = launchctl_binary()
    result = {
        "checked": True,
        "available": bool(binary),
        "loaded": False,
        "label": HOST_SERVICE_LABEL,
        "domain": f"gui/{os.getuid()}",
        "command_output_omitted": True,
    }
    if not binary:
        return result
    try:
        process = subprocess.run(
            [binary, "print", f"gui/{os.getuid()}/{HOST_SERVICE_LABEL}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1, min(int(timeout), 20)),
            check=False,
        )
        result.update({"loaded": process.returncode == 0, "returncode": process.returncode})
    except (OSError, subprocess.TimeoutExpired):
        result["check_failed"] = True
    return result


def host_service_launchd_state_after_mutation(*, expected_loaded: bool) -> dict:
    state = {}
    converged = False
    for attempt in range(1, HOST_SERVICE_STATE_CONVERGENCE_ATTEMPTS + 1):
        state = host_service_launchd_state(timeout=HOST_SERVICE_STATE_CONVERGENCE_READ_TIMEOUT_SECONDS)
        converged = bool(
            state.get("available") is True
            and state.get("check_failed") is not True
            and bool(state.get("loaded")) == expected_loaded
        )
        if converged:
            break
        if attempt < HOST_SERVICE_STATE_CONVERGENCE_ATTEMPTS:
            time.sleep(HOST_SERVICE_STATE_CONVERGENCE_DELAY_SECONDS)
    return {
        **state,
        "converged": converged,
        "expected_loaded": expected_loaded,
        "verification_attempts": attempt,
    }


def inspect_host_service(path: Path, *, timeout: int = 5) -> dict:
    exists = path.exists()
    safe_regular_file = bool(exists and not path.is_symlink() and path.is_file())
    file_owned_by_user = False
    mode_exact_private = False
    parent_directory_exists = False
    parent_directory_safe = False
    try:
        if safe_regular_file:
            metadata = path.lstat()
            file_owned_by_user = metadata.st_uid == os.getuid()
            mode_exact_private = stat.S_IMODE(metadata.st_mode) == 0o600
        parent_metadata = path.parent.lstat()
        parent_directory_exists = True
        parent_directory_safe = bool(
            not path.parent.is_symlink()
            and stat.S_ISDIR(parent_metadata.st_mode)
            and parent_metadata.st_uid == os.getuid()
            and stat.S_IMODE(parent_metadata.st_mode) & 0o022 == 0
        )
    except OSError:
        pass
    content = b""
    if safe_regular_file:
        try:
            content = path.read_bytes()
        except OSError:
            pass
    parse_ok = bool(content.startswith(b'<?xml version="1.0"') and content.rstrip().endswith(b"</plist>"))
    exact_definition = bool(content and secrets.compare_digest(content, host_service_template()))
    legacy_definition = bool(
        content
        and secrets.compare_digest(
            content,
            host_service_template(managed_launch_agent=False),
        )
    )
    text = content.decode("utf-8", errors="replace")
    token_like_detected = any(prefix in text for prefix in ("agthost_", "agtadmin_", "agtok_", "agtsess_", "ntn_", "sk-"))
    mode_private = bool(safe_regular_file and (path.stat().st_mode & 0o077) == 0)
    service_state = host_service_launchd_state(timeout=timeout)
    launchctl_absence_verified = bool(
        service_state.get("available") is True
        and service_state.get("check_failed") is not True
        and service_state.get("loaded") is False
        and service_state.get("returncode") in {1, 113}
    )
    legacy_owned_definition = bool(
        safe_regular_file
        and parse_ok
        and legacy_definition
        and file_owned_by_user
        and mode_exact_private
        and parent_directory_safe
        and not token_like_detected
    )
    return {
        "ok": bool(
            safe_regular_file
            and parse_ok
            and exact_definition
            and file_owned_by_user
            and mode_exact_private
            and parent_directory_safe
            and not token_like_detected
        ),
        "operation": "host_service_check",
        "manager": "launchd",
        "label": HOST_SERVICE_LABEL,
        "service_path": str(path),
        "service_file": {
            "exists": exists,
            "safe_regular_file": safe_regular_file,
            "parse_ok": parse_ok,
            "exact_managed_definition": exact_definition,
            "legacy_managed_definition": legacy_definition,
            "legacy_owned_definition": legacy_owned_definition,
            "file_owned_by_user": file_owned_by_user,
            "mode_private": mode_private,
            "mode_exact_private": mode_exact_private,
            "parent_directory_exists": parent_directory_exists,
            "parent_directory_safe": parent_directory_safe,
            "token_like_detected": token_like_detected,
            "raw_content_omitted": True,
        },
        "service_state": service_state,
        "legacy_migration_ready": bool(legacy_owned_definition and launchctl_absence_verified),
        "host_only": True,
        "workers": [],
        "live_workers_started": False,
        "credential_material_in_service": token_like_detected,
        "token_omitted": True,
    }


def host_service_log_repair_available(service_check: dict, service_log: dict) -> bool:
    service_file = (
        service_check.get("service_file")
        if isinstance(service_check.get("service_file"), dict)
        else {}
    )
    service_definition_compatible = bool(
        service_check.get("ok") is True
        or service_check.get("legacy_migration_ready") is True
        or (
            service_file.get("exists") is False
            and (
                service_file.get("parent_directory_exists") is False
                or service_file.get("parent_directory_safe") is True
            )
        )
    )
    return bool(
        service_definition_compatible
        and service_log.get("ok") is True
        and service_log.get("preparation_required") is True
    )


def cmd_service_check(args) -> int:
    require_initialized()
    payload = inspect_host_service(host_service_path(args.service_path), timeout=args.timeout)
    service_log = inspect_host_service_log()
    log_rotation_ready = bool(
        service_log.get("ok") is True
        and service_log.get("mode_private") is True
    )
    repair_available = host_service_log_repair_available(payload, service_log)
    payload["service_log"] = service_log
    payload["log_rotation_ready"] = log_rotation_ready
    payload["service_log_repair_available"] = repair_available
    payload["manual_recovery_required"] = bool(
        not log_rotation_ready and not repair_available
    )
    payload["maintenance_status"] = "ready" if log_rotation_ready else "attention"
    payload["maintenance_next_action"] = (
        "agentops host service-install --overwrite"
        if repair_available
        else None
    )
    emit(payload)
    return 0 if payload["ok"] else 1


def _atomic_write_service(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError("Host service directory is unsafe.")
    path.parent.chmod(path.parent.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_host_service_log() -> dict:
    log_path = paths()["logs"] / "launchd.log"
    result = {
        "ok": False,
        "operation": "host_service_log_preflight",
        "present": False,
        "mode_private": False,
        "single_link_regular_file": False,
        "preparation_required": False,
        "planned_action": None,
        "content_omitted": True,
        "path_omitted": True,
        "token_omitted": True,
    }
    try:
        parent = log_path.parent.lstat()
    except FileNotFoundError:
        return {**result, "error": "host_service_log_directory_missing"}
    except OSError:
        return {**result, "error": "host_service_log_directory_unreadable"}
    if (
        log_path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        return {**result, "error": "host_service_log_directory_unsafe"}
    try:
        metadata = log_path.lstat()
    except FileNotFoundError:
        return {
            **result,
            "ok": True,
            "preparation_required": True,
            "planned_action": "create_private_log",
        }
    except OSError:
        return {**result, "error": "host_service_log_unreadable"}
    safe_file = bool(
        not log_path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
    )
    if not safe_file:
        return {
            **result,
            "present": True,
            "error": "host_service_log_unsafe",
        }
    mode_private = stat.S_IMODE(metadata.st_mode) == 0o600
    return {
        **result,
        "ok": True,
        "present": True,
        "mode_private": mode_private,
        "single_link_regular_file": True,
        "preparation_required": not mode_private,
        "planned_action": None if mode_private else "tighten_permissions",
    }


def prepare_host_service_log() -> dict:
    before = inspect_host_service_log()
    if before.get("ok") is not True:
        raise RuntimeError(str(before.get("error") or "host_service_log_preflight_failed"))
    log_path = paths()["logs"] / "launchd.log"
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(log_path.parent, parent_flags)
    descriptor = -1
    created = False
    try:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise RuntimeError("host_service_log_directory_changed")
        try:
            descriptor = os.open(
                log_path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            descriptor = os.open(
                log_path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            created = True
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
        ):
            raise RuntimeError("host_service_log_changed")
        os.fchmod(descriptor, 0o600)
        secured = os.fstat(descriptor)
        named = os.stat(log_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            secured.st_dev != named.st_dev
            or secured.st_ino != named.st_ino
            or not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.getuid()
            or named.st_nlink != 1
            or stat.S_IMODE(named.st_mode) != 0o600
        ):
            raise RuntimeError("host_service_log_identity_changed")
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    after = inspect_host_service_log()
    if after.get("ok") is not True or after.get("mode_private") is not True:
        raise RuntimeError("host_service_log_prepare_verification_failed")
    return {
        **after,
        "prepared": True,
        "created": created,
        "permissions_tightened": bool(before.get("present") and not before.get("mode_private")),
    }


def cmd_service_install(args) -> int:
    require_initialized()
    path = host_service_path(args.service_path)
    template = host_service_template()
    before = inspect_host_service(path, timeout=args.timeout)
    exists_before = bool(before["service_file"]["exists"])
    legacy_owned = bool(before["service_file"].get("legacy_owned_definition"))
    legacy_migration_ready = bool(before.get("legacy_migration_ready"))
    service_loaded = bool(before["service_state"].get("loaded"))
    service_log = inspect_host_service_log()
    owned_for_overwrite = bool(before["ok"] or legacy_owned)
    overwrite_allowed = bool(
        not exists_before
        or (
            args.overwrite
            and (
                before["ok"]
                or legacy_migration_ready
            )
        )
    )
    blockers = []
    if path.is_symlink():
        blockers.append("service_path_is_symlink")
    if (
        before["service_file"].get("parent_directory_exists") is True
        and before["service_file"].get("parent_directory_safe") is not True
    ):
        blockers.append("service_parent_directory_unsafe")
    if exists_before and not args.overwrite:
        blockers.append("service_file_exists")
    if exists_before and args.overwrite and not owned_for_overwrite:
        blockers.append("existing_service_not_owned")
    if exists_before and args.overwrite and legacy_owned and service_loaded:
        blockers.append("legacy_service_still_loaded")
    if exists_before and args.overwrite and legacy_owned and not service_loaded and not legacy_migration_ready:
        blockers.append("launchctl_state_unverified")
    if any(prefix.encode("ascii") in template for prefix in ("agthost_", "agtadmin_", "agtok_", "agtsess_", "ntn_", "sk-")):
        blockers.append("token_like_content_detected")
    if service_log.get("ok") is not True:
        blockers.append(str(service_log.get("error") or "host_service_log_preflight_failed"))
    wrote = False
    if args.confirm_install and overwrite_allowed and not blockers:
        try:
            service_log = prepare_host_service_log()
        except (OSError, RuntimeError):
            blockers.append("host_service_log_prepare_failed")
        if not blockers:
            _atomic_write_service(path, template)
            wrote = True
    after = inspect_host_service(path, timeout=args.timeout) if wrote else before
    dry_run = not args.confirm_install
    ok = bool((dry_run and not blockers) or (wrote and after["ok"]))
    if wrote:
        next_action = "agentops host service-control --action load"
    elif not blockers:
        next_action = "Re-run with --confirm-install after reviewing this preview."
    elif blockers == ["service_file_exists"] and (before["ok"] or legacy_migration_ready):
        next_action = "agentops host service-install --overwrite"
    elif "legacy_service_still_loaded" in blockers:
        next_action = "agentops host service-control --action unload"
    else:
        next_action = None
    emit({
        "ok": ok,
        "operation": "host_service_install",
        "manager": "launchd",
        "dry_run": dry_run,
        "confirmed_install": bool(args.confirm_install),
        "wrote": wrote,
        "overwrite": bool(args.overwrite),
        "exists_before": exists_before,
        "legacy_migration": legacy_owned,
        "legacy_migration_ready": legacy_migration_ready,
        "service_path": str(path),
        "service_file_mode": "0600" if wrote else None,
        "service_log": service_log,
        "template_hash": hashlib.sha256(template).hexdigest(),
        "template_bytes": len(template),
        "service_check": after,
        "blockers": blockers,
        "next_action": next_action,
        "manual_recovery_required": bool(blockers and not next_action),
        "service_loaded": service_loaded,
        "host_only": True,
        "workers": [],
        "live_workers_started": False,
        "raw_content_omitted": True,
        "token_omitted": True,
    })
    return 0 if ok else 1


def host_service_control_commands(path: Path, action: str, *, loaded: bool) -> list[list[str]]:
    binary = launchctl_binary() or "/bin/launchctl"
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{HOST_SERVICE_LABEL}"
    if action == "load":
        return [] if loaded else [[binary, "bootstrap", domain, str(path)]]
    if action == "unload":
        return [[binary, "bootout", target]] if loaded else []
    return [[binary, "kickstart", "-k", target]] if loaded else []


def cmd_service_control(args) -> int:
    require_initialized()
    path = host_service_path(args.service_path)
    checked = inspect_host_service(path, timeout=args.timeout)
    state = checked["service_state"]
    loaded = bool(state["loaded"])
    legacy_owned = bool(checked["service_file"].get("legacy_owned_definition"))
    legacy_unload = bool(args.action == "unload" and legacy_owned and loaded)
    service_log = inspect_host_service_log()
    commands = host_service_control_commands(path, args.action, loaded=loaded)
    blockers = []
    if not checked["ok"] and not legacy_unload:
        blockers.append("managed_service_check_failed")
    if not state["available"]:
        blockers.append("launchctl_unavailable")
    if state.get("check_failed"):
        blockers.append("launchctl_state_unverified")
    if args.action == "restart" and not loaded:
        blockers.append("service_not_loaded")
    if args.action == "load" and not loaded and managed_host_running():
        blockers.append("unmanaged_host_already_running")
    if args.action in {"load", "restart"} and (
        service_log.get("ok") is not True
        or service_log.get("mode_private") is not True
    ):
        blockers.append("host_service_log_not_private")
    dry_run = not args.confirm_control
    results = []
    if args.confirm_control and not blockers:
        for command in commands:
            try:
                process = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(1, min(int(args.timeout), 30)),
                    check=False,
                )
                results.append({"returncode": process.returncode, "ok": process.returncode == 0, "command_output_omitted": True})
                if process.returncode != 0:
                    blockers.append("launchctl_command_failed")
                    break
            except (OSError, subprocess.TimeoutExpired):
                results.append({"ok": False, "command_failed": True, "command_output_omitted": True})
                blockers.append("launchctl_command_failed")
                break
    successful_mutation = bool(args.confirm_control and commands and not blockers)
    state_after = (
        host_service_launchd_state_after_mutation(
            expected_loaded=args.action in {"load", "restart"},
        )
        if successful_mutation
        else state
    )
    expected_loaded = args.action in {"load", "restart"}
    if args.confirm_control and not blockers and (
        state_after.get("available") is not True
        or state_after.get("check_failed") is True
        or bool(state_after.get("loaded")) != expected_loaded
    ):
        blockers.append("launchctl_state_verification_failed")
    no_op = not commands and not blockers
    ok = not blockers
    emit({
        "ok": ok,
        "operation": "host_service_control",
        "manager": "launchd",
        "action": args.action,
        "dry_run": dry_run,
        "confirmed_control": bool(args.confirm_control),
        "service_path": str(path),
        "service_log": service_log,
        "legacy_unload": legacy_unload,
        "service_loaded_before": loaded,
        "service_state_after": state_after,
        "service_control_skipped": no_op,
        "service_mutated": bool(args.confirm_control and commands and not blockers),
        "planned_commands": commands,
        "command_results": results,
        "blockers": blockers,
        "host_only": True,
        "workers": [],
        "live_workers_started": False,
        "command_output_omitted": True,
        "token_omitted": True,
    })
    return 0 if ok else 1


def cmd_service_remove(args) -> int:
    require_initialized()
    path = host_service_path(args.service_path)
    checked = inspect_host_service(path, timeout=args.timeout)
    loaded = bool(checked["service_state"]["loaded"])
    blockers = []
    if not checked["ok"]:
        blockers.append("managed_service_check_failed")
    if not checked["service_state"]["available"] or checked["service_state"].get("check_failed"):
        blockers.append("launchctl_state_unverified")
    if loaded:
        blockers.append("service_still_loaded")
    removed = False
    if args.confirm_remove and not blockers:
        path.unlink()
        removed = True
    dry_run = not args.confirm_remove
    ok = not blockers
    emit({
        "ok": ok,
        "operation": "host_service_remove",
        "manager": "launchd",
        "dry_run": dry_run,
        "confirmed_remove": bool(args.confirm_remove),
        "removed": removed,
        "service_path": str(path),
        "blockers": blockers,
        "next_action": (
            "agentops host service-control --action unload --confirm-control"
            if loaded
            else "Re-run with --confirm-remove after reviewing this preview."
        ),
        "host_only": True,
        "workers": [],
        "live_workers_started": False,
        "raw_content_omitted": True,
        "token_omitted": True,
    })
    return 0 if ok else 1


def cmd_version(_args) -> int:
    state = install_state()
    current = state["current"]
    emit({
        "ok": bool(current),
        "operation": "host_version",
        "packaged_install": bool(current),
        "version": current.get("version") or "development",
        "git_commit": current.get("git_commit"),
        "previous_version": state["previous"].get("version"),
        "token_omitted": True,
    })
    return 0


def cmd_update(args) -> int:
    state = install_state()
    current = state["current"]
    if not args.check:
        emit({
            "ok": False,
            "operation": "host_update",
            "error": "check_required",
            "message": "Use --check for the side-effect-free update status command.",
            "token_omitted": True,
        })
        return 2
    emit({
        "ok": bool(current),
        "operation": "host_update_check",
        "check_only": True,
        "network_used": False,
        "current_version": current.get("version") or "development",
        "current_git_commit": current.get("git_commit"),
        "previous_version": state["previous"].get("version"),
        "update_available": "unknown",
        "next_action": "Install a newer verified release bundle while the Host is stopped.",
        "token_omitted": True,
    })
    return 0 if current else 1


def _cmd_rollback_unlocked(args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    pid = int(read_json(p["pid"]).get("pid") or 0)
    if process_alive(pid):
        emit({
            "ok": False,
            "operation": "host_rollback",
            "error": "host_running",
            "next_action": "agentops host stop",
            "token_omitted": True,
        })
        return 2
    state = install_state()
    current = state["current"]
    previous = state["previous"]
    if not current or not previous:
        emit({
            "ok": False,
            "operation": "host_rollback",
            "error": "previous_version_unavailable",
            "token_omitted": True,
        })
        return 2
    if not args.confirm_rollback:
        emit({
            "ok": False,
            "operation": "host_rollback",
            "dry_run": True,
            "error": "confirm_rollback_required",
            "from_version": current.get("version"),
            "to_version": previous.get("version"),
            "token_omitted": True,
        })
        return 2
    p["backups"].mkdir(parents=True, exist_ok=True, mode=0o700)
    backup, backup_status = run_backup_utility(
        "create",
        "--db-path",
        config["database_path"],
        "--backup-dir",
        str(p["backups"]),
    )
    if backup_status != 0 or not backup.get("ok"):
        emit({
            "ok": False,
            "operation": "host_rollback",
            "error": "pre_rollback_backup_failed",
            "token_omitted": True,
        })
        return 1
    for key in ("backup_path", "manifest_path"):
        value = backup.get(key)
        if value and Path(str(value)).is_file():
            Path(str(value)).chmod(0o600)

    current_link = state["current_link"]
    previous_link = state["previous_link"]
    next_current = current_link.with_name("current.next")
    next_previous = previous_link.with_name("previous.next")
    for temporary in (next_current, next_previous):
        temporary.unlink(missing_ok=True)
    next_current.symlink_to(previous["target"])
    next_previous.symlink_to(current["target"])
    os.replace(next_current, current_link)
    try:
        os.replace(next_previous, previous_link)
    except OSError:
        recovery = current_link.with_name("current.recovery")
        recovery.unlink(missing_ok=True)
        recovery.symlink_to(current["target"])
        os.replace(recovery, current_link)
        next_previous.unlink(missing_ok=True)
        emit({
            "ok": False,
            "operation": "host_rollback",
            "error": "binary_pointer_swap_failed",
            "current_version_preserved": True,
            "token_omitted": True,
        })
        return 1
    emit({
        "ok": True,
        "operation": "host_rollback",
        "from_version": current.get("version"),
        "to_version": previous.get("version"),
        "pre_rollback_backup_path": backup.get("backup_path"),
        "data_path_unchanged": True,
        "restart_required": True,
        "next_action": "agentops host start",
        "token_omitted": True,
    })
    return 0


def cmd_rollback(args) -> int:
    with lifecycle_lock():
        return _cmd_rollback_unlocked(args)


def cmd_console_url(_args) -> int:
    config, _secret_values = require_initialized()
    ts = tailscale_state()
    target = f"http://{config['host']}:{config['port']}"
    https_port = int(config.get("tailscale_https_port") or 443)
    binary, _source = tailscale_binary()
    serve = tailscale_serve_state(binary, target, https_port)
    local_url = f"http://{config['host']}:{config['port']}/workspace"
    publication_enabled = config.get("network_publication") == "tailscale_serve"
    private_origin = f"https://{ts['dns_name']}{'' if https_port == 443 else f':{https_port}'}" if publication_enabled and ts["dns_name"] else ""
    configured_origin = str(config.get("private_console_origin") or "").rstrip("/")
    origin_matches_config = bool(private_origin and configured_origin == private_origin and private_origin in (config.get("allowed_origins") or []))
    pid = int(read_json(paths()["pid"]).get("pid") or 0)
    running = process_alive(pid)
    readiness = health(target) if running else {"reachable": False, "status": "stopped"}
    private_url = private_origin + "/workspace" if private_origin else ""
    emit({
        "ok": True,
        "operation": "host_console_url",
        "local_console_url": local_url,
        "private_console_url": private_url,
        "private_url_ready": bool(
            private_url
            and running
            and readiness["reachable"]
            and origin_matches_config
            and ts["backend_state"] == "Running"
            and serve["status_available"]
            and serve["target_matches"]
            and not serve["conflict"]
        ),
        "private_origin_matches_config": origin_matches_config,
        "host_health": readiness,
        "serve": serve,
        "token_omitted": True,
    })
    return 0


def cmd_open_console(_args) -> int:
    config, secret_values = require_initialized()
    base_url = loopback_base_url(config.get("host"), int(config["port"]))
    if not base_url:
        emit({"ok": False, "operation": "host_open_console", "error": "unsafe_console_target", "target_omitted": True, "token_omitted": True})
        return 2
    if not managed_host_running() or not health(base_url)["reachable"]:
        emit({"ok": False, "operation": "host_open_console", "error": "managed_host_not_running", "next_action": "agentops host start", "token_omitted": True})
        return 2

    local_url = base_url + "/workspace"
    auth_status, auth_payload = local_json_request(base_url, "/api/human-auth/status")
    bootstrap_handoff = auth_status == 200 and auth_payload.get("bootstrap_required") is True
    local_authority_handoff = auth_status == 200 and auth_payload.get("required") is True
    target_url = local_url
    setup_code = ""
    if local_authority_handoff:
        setup_code = str(secret_values.get("owner_setup_code") or "").strip()
        if not setup_code and bootstrap_handoff:
            emit({"ok": False, "operation": "host_open_console", "error": "owner_setup_code_unavailable", "setup_code_omitted": True, "token_omitted": True})
            return 2
        if setup_code:
            target_url += "#agentops-owner-setup=" + urllib.parse.quote(setup_code, safe="")

    preview_only = os.environ.get("AGENTOPS_OPEN_CONSOLE_TEST_MODE") == "1"
    opened = False
    if not preview_only:
        if sys.platform != "darwin" or not Path("/usr/bin/osascript").is_file():
            emit({"ok": False, "operation": "host_open_console", "error": "graphical_console_unavailable", "local_console_url": local_url, "bootstrap_handoff_omitted": True, "local_authority_handoff_omitted": True, "setup_code_omitted": True, "token_omitted": True})
            return 2
        script = 'open location "' + target_url.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
        process = subprocess.run(
            ["/usr/bin/osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        opened = process.returncode == 0
        if not opened:
            emit({"ok": False, "operation": "host_open_console", "error": "console_open_failed", "command_output_omitted": True, "bootstrap_handoff_omitted": True, "local_authority_handoff_omitted": True, "setup_code_omitted": True, "token_omitted": True})
            return 1

    emit({
        "ok": True,
        "operation": "host_open_console",
        "opened": opened,
        "preview_only": preview_only,
        "local_console_url": local_url,
        "bootstrap_handoff_prepared": bootstrap_handoff,
        "local_authority_handoff_prepared": bool(setup_code),
        "bootstrap_handoff_omitted": True,
        "local_authority_handoff_omitted": True,
        "setup_code_omitted": True,
        "token_omitted": True,
    })
    return 0


def cmd_tailscale_preview(args) -> int:
    config, _secret_values = require_initialized()
    target = f"http://{config['host']}:{config['port']}"
    ts = tailscale_state()
    https_port = int(args.https_port)
    binary, _source = tailscale_binary()
    serve = tailscale_serve_state(binary, target, https_port)
    emit({
        "ok": True,
        "operation": "host_tailscale_preview",
        "preview_only": True,
        "command": f"tailscale serve --https={https_port} --bg {target}",
        "revoke_command": f"tailscale serve --https={https_port} off",
        "tailscale": ts,
        "serve": serve,
        "apply_blocked_by_conflict": serve["conflict"],
        "public_funnel_enabled": False,
        "automatic_execution": False,
        "token_omitted": True,
    })
    return 0


def cmd_relay_preflight(_args) -> int:
    config, _secret_values = require_initialized()
    p = paths()
    active_relay = relay_connector_projection(p)
    base = {
        "operation": "host_relay_preflight",
        "prepared_config_present": p["relay_prepared"].is_file(),
        "active_relay_enabled": active_relay["enabled"],
        "confirmation_required": True,
        "deployed_relay": False,
        "certificate_lifecycle": False,
        "network_used": False,
        "tailscale_changed": False,
        "config_omitted": True,
        "paths_omitted": True,
        "certificate_material_omitted": True,
        "machine_credential_omitted": True,
        "token_omitted": True,
    }
    if not p["relay_prepared"].is_file():
        emit({
            **base,
            "ok": False,
            "error": "prepared_relay_material_unavailable",
        })
        return 2
    if config.get("network_publication") != "disabled":
        emit({
            **base,
            "ok": False,
            "error": "network_publication_profile_active",
        })
        return 1
    if active_relay.get("state") != "disabled":
        emit({
            **base,
            "ok": False,
            "error": "active_relay_not_disabled",
        })
        return 1
    try:
        prepared, _tunnel_key, _relay_context, _host_context = validate_connector_material(
            p["relay_prepared"],
            p["relay_secrets"],
        )
    except RelayConnectorServiceError as exc:
        emit({
            **base,
            "ok": False,
            "error": "prepared_relay_material_invalid",
            "failure_code": str(exc),
        })
        return 1
    if prepared.get("enabled") is not True:
        emit({
            **base,
            "ok": False,
            "error": "prepared_relay_material_not_enabled",
        })
        return 1
    if prepared.get("host_http_port") != int(config.get("port") or 0):
        emit({
            **base,
            "ok": False,
            "error": "prepared_relay_backend_port_mismatch",
        })
        return 1
    emit({
        **base,
        "ok": True,
        "state": "prepared",
        "exact_material_validated": True,
        "next_action": "Owner confirmation controls are not implemented in this slice.",
    })
    return 0


def cmd_relay_transition(args) -> int:
    require_initialized()
    p = paths()
    with lifecycle_lock():
        common = {
            "action": args.action,
            "transition_path": p["relay_transition"],
            "active_config_path": p["relay_config"],
            "prepared_config_path": p["relay_prepared"],
            "secrets_path": p["relay_secrets"],
            "host_config_path": p["config"],
        }
        if args.confirm_ref:
            confirmed = relay_control.confirm_relay_transition(
                **common,
                transition_ref=args.confirm_ref,
            )
            payload = confirmed
            if confirmed.get("ok") is True:
                payload = relay_control.execute_confirmed_relay_transition(
                    **common,
                    transition_ref=args.confirm_ref,
                    restart_receipt_path=p["relay_restart_receipt"],
                    restart_sequence_path=p["relay_restart_sequence"],
                )
                if payload.get("ok") is True and payload.get("transaction_sequence"):
                    flushed = relay_restart.transition_restart_receipt(
                        receipt_path=p["relay_restart_receipt"],
                        sequence_path=p["relay_restart_sequence"],
                        action=args.action,
                        transition_ref=args.confirm_ref,
                        transaction_sequence=int(payload["transaction_sequence"]),
                        expected_revision=int(payload["revision"]),
                        state="response_flushed",
                    )
                    manual = relay_restart.transition_restart_receipt(
                        receipt_path=p["relay_restart_receipt"],
                        sequence_path=p["relay_restart_sequence"],
                        action=args.action,
                        transition_ref=args.confirm_ref,
                        transaction_sequence=int(flushed["transaction_sequence"]),
                        expected_revision=int(flushed["revision"]),
                        state="manual_restart_required",
                    )
                    payload.update({
                        "state": "manual_restart_required",
                        "restart_mode": "manual",
                        "restart_required": True,
                        "revision": manual["revision"],
                        "rollback_armed": True,
                    })
        else:
            payload = relay_control.prepare_relay_transition(**common)
    emit(payload)
    return 0 if payload.get("ok") is True else 1


def cmd_tailscale_apply(args) -> int:
    config, _secret_values = require_initialized()
    target = f"http://{config['host']}:{config['port']}"
    ts = tailscale_state()
    https_port = int(args.https_port)
    binary, _source = tailscale_binary()
    serve = tailscale_serve_state(binary, target, https_port)
    preview = {
        "ok": False,
        "operation": "host_tailscale_apply",
        "preview_only": not args.confirm,
        "command": f"tailscale serve --https={https_port} --bg {target}",
        "tailscale": ts,
        "serve": serve,
        "public_funnel_enabled": False,
        "token_omitted": True,
    }
    if not args.confirm:
        preview.update({"error": "confirmation_required", "message": "Re-run with --confirm after reviewing the Serve command."})
        emit(preview)
        return 2
    if not binary or ts["backend_state"] != "Running" or not ts["dns_name"]:
        preview.update({"error": "tailscale_not_ready", "message": "Tailscale must be installed, Running, and have a DNS name."})
        emit(preview)
        return 2
    if not serve["status_available"]:
        preview.update({"error": "tailscale_serve_status_unavailable", "message": "Existing Tailscale Serve state could not be verified; no changes were made."})
        emit(preview)
        return 2
    if serve["public_funnel_enabled"]:
        preview.update({
            "error": "tailscale_funnel_conflict",
            "message": "The selected HTTPS port has Funnel enabled. Private Host will not replace or reuse a public route.",
        })
        emit(preview)
        return 2
    if serve["conflict"] and not args.replace_existing_serve:
        preview.update({
            "error": "tailscale_serve_conflict",
            "message": "An existing Tailscale Serve configuration targets another local service. Re-run only after review with --replace-existing-serve.",
            "replacement_confirmation_required": True,
        })
        emit(preview)
        return 2
    process = subprocess.run([binary, "serve", f"--https={https_port}", "--bg", target], capture_output=True, text=True, timeout=30, check=False)
    if process.returncode != 0:
        preview.update({"error": "tailscale_serve_failed", "message": "Tailscale Serve failed; command output was omitted.", "exit_code": process.returncode})
        emit(preview)
        return 1
    origin = f"https://{ts['dns_name']}{'' if https_port == 443 else f':{https_port}'}"
    origins = sorted(set(config.get("allowed_origins") or []) | {origin})
    config.update({
        "allowed_origins": origins,
        "network_publication": "tailscale_serve",
        "private_console_origin": origin,
        "tailscale_https_port": https_port,
        "cookie_secure": True,
    })
    write_private_json(paths()["config"], config)
    emit({
        "ok": True,
        "operation": "host_tailscale_apply",
        "private_console_url": origin + "/workspace",
        "network_publication": "tailscale_serve",
        "public_funnel_enabled": False,
        "restart_required": True,
        "next_action": "agentops host restart",
        "command_output_omitted": True,
        "token_omitted": True,
    })
    return 0


def cmd_tailscale_revoke(args) -> int:
    config, _secret_values = require_initialized()
    https_port = int(config.get("tailscale_https_port") or 443)
    if not args.confirm:
        emit({
            "ok": False,
            "operation": "host_tailscale_revoke",
            "preview_only": True,
            "error": "confirmation_required",
            "command": f"tailscale serve --https={https_port} off",
            "token_omitted": True,
        })
        return 2
    binary, _source = tailscale_binary()
    if not binary:
        emit({"ok": False, "operation": "host_tailscale_revoke", "error": "tailscale_not_installed", "token_omitted": True})
        return 2
    target = f"http://{config['host']}:{config['port']}"
    serve = tailscale_serve_state(binary, target, https_port)
    owns_publication = config.get("network_publication") == "tailscale_serve"
    if not serve["status_available"]:
        emit({"ok": False, "operation": "host_tailscale_revoke", "error": "tailscale_serve_status_unavailable", "message": "Existing Tailscale Serve state could not be verified; no changes were made.", "token_omitted": True})
        return 2
    if not owns_publication or serve["conflict"] or (serve["configured"] and not serve["target_matches"]):
        emit({
            "ok": False,
            "operation": "host_tailscale_revoke",
            "error": "tailscale_serve_not_exclusively_owned",
            "message": "The configured HTTPS port is not exclusively owned by this Host; no changes were made.",
            "serve": serve,
            "token_omitted": True,
        })
        return 2
    process = subprocess.run([binary, "serve", f"--https={https_port}", "off"], capture_output=True, text=True, timeout=30, check=False)
    if process.returncode != 0:
        emit({
            "ok": False,
            "operation": "host_tailscale_revoke",
            "error": "tailscale_serve_disable_failed",
            "exit_code": process.returncode,
            "command_output_omitted": True,
            "token_omitted": True,
        })
        return 1
    local_origin = f"http://{config['host']}:{config['port']}"
    config.update({
        "allowed_origins": [local_origin],
        "network_publication": "disabled",
        "private_console_origin": "",
        "cookie_secure": False,
    })
    write_private_json(paths()["config"], config)
    emit({
        "ok": True,
        "operation": "host_tailscale_revoke",
        "network_publication": "disabled",
        "restart_required": True,
        "next_action": "agentops host restart",
        "command_output_omitted": True,
        "token_omitted": True,
    })
    return 0


def add_start_options(parser) -> None:
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--managed-launch-agent", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--build-ui", action="store_true")
    parser.add_argument("--install-ui", action="store_true")
    parser.add_argument("--worker", action="append", choices=["mock", "hermes", "openclaw"])
    parser.add_argument("--no-workers", action="store_true")
    parser.add_argument("--confirm-live-workers", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="agentops host", description="Manage the private local AgentOps MIS host.", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    init = sub.add_parser("init", help="Create private host config and one-time Owner setup code.")
    init.add_argument("--port", type=int, default=8787)
    init.add_argument("--workspace-id", default="local-demo")
    init.add_argument("--ui-dist")
    init.set_defaults(handler=cmd_init)
    bootstrap_owner = sub.add_parser("bootstrap-owner", help="Create the first Owner without exposing the setup code or password in argv.", allow_abbrev=False)
    bootstrap_owner.add_argument("--username")
    bootstrap_owner.add_argument("--display-name")
    bootstrap_owner.add_argument("--password-stdin", action="store_true", help="Read exactly one password line from stdin; no password argv/env option exists.")
    bootstrap_owner.add_argument("--confirm", action="store_true")
    bootstrap_owner.set_defaults(handler=cmd_bootstrap_owner)
    configure_cli = sub.add_parser("configure-cli", help="Configure the local machine CLI for this loopback Host without printing its token.", allow_abbrev=False)
    configure_cli.add_argument("--confirm", action="store_true")
    configure_cli.set_defaults(handler=cmd_configure_cli)
    start = sub.add_parser("start", help="Start the private host in the background by default.")
    add_start_options(start)
    start.set_defaults(handler=cmd_start)
    stop = sub.add_parser("stop", help="Stop the managed private host process group.")
    stop.set_defaults(handler=cmd_stop)
    restart = sub.add_parser("restart", help="Restart the managed private host.")
    add_start_options(restart)
    restart.set_defaults(handler=cmd_restart)
    status = sub.add_parser("status", help="Show redacted host, health and private-network status.")
    status.set_defaults(handler=cmd_status)
    storage_preflight = sub.add_parser(
        "storage-preflight",
        help="Check Host install-volume free space without reading the ledger, credentials, or network.",
    )
    storage_preflight.add_argument(
        "--minimum-free-bytes",
        type=int,
        help="Raise the minimum free-space requirement; values below the production floor fail closed.",
    )
    storage_preflight.set_defaults(handler=cmd_storage_preflight)
    doctor = sub.add_parser("doctor", help="Check private files, UI bundle and Tailscale readiness.")
    doctor.set_defaults(handler=cmd_doctor)
    logs = sub.add_parser("logs", help="Show log metadata without printing raw host output.")
    logs.set_defaults(handler=cmd_logs)
    log_rotate = sub.add_parser("log-rotate", help="Preview or explicitly confirm bounded stopped-Host log rotation.")
    log_rotate.add_argument("--max-bytes", type=int, default=host_log.HOST_LOG_ROTATE_DEFAULT_MAX_BYTES)
    log_rotate.add_argument("--backups", type=int, default=host_log.HOST_LOG_ROTATE_DEFAULT_BACKUPS)
    log_rotate.add_argument("--confirm-rotate", action="store_true")
    log_rotate.add_argument("--plan-hash", default="")
    log_rotate.set_defaults(handler=cmd_log_rotate)
    backup = sub.add_parser("backup", help="Create a verified SQLite authority-ledger backup while the Host may remain running.")
    backup.set_defaults(handler=cmd_backup)
    backup_verify = sub.add_parser("backup-verify", help="Verify the latest or selected Host backup without printing ledger rows.")
    backup_verify.add_argument("--backup")
    backup_verify.set_defaults(handler=cmd_backup_verify)
    backup_prune = sub.add_parser("backup-prune", help="Preview or explicitly confirm verified Host backup retention.")
    backup_prune.add_argument("--keep", type=int, default=5)
    backup_prune.add_argument("--confirm-prune", action="store_true")
    backup_prune.add_argument("--plan-hash", default="")
    backup_prune.set_defaults(handler=cmd_backup_prune)
    restore = sub.add_parser("restore", help="Restore a verified ledger backup while the Host is stopped.")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--confirm-restore", action="store_true")
    restore.set_defaults(handler=cmd_restore)
    version = sub.add_parser("version", help="Show packaged release provenance without contacting the network.")
    version.set_defaults(handler=cmd_version)
    update = sub.add_parser("update", help="Check local packaged update state without installing anything.")
    update.add_argument("--check", action="store_true")
    update.set_defaults(handler=cmd_update)
    rollback = sub.add_parser("rollback", help="Switch to the previous verified binary after a local ledger backup.")
    rollback.add_argument("--confirm-rollback", action="store_true")
    rollback.set_defaults(handler=cmd_rollback)
    service_install = sub.add_parser("service-install", help="Preview or install a host-only macOS LaunchAgent without starting workers.")
    service_install.add_argument("--service-path", default="")
    service_install.add_argument("--confirm-install", action="store_true")
    service_install.add_argument("--overwrite", action="store_true")
    service_install.add_argument("--timeout", type=int, default=5)
    service_install.set_defaults(handler=cmd_service_install)
    service_check = sub.add_parser("service-check", help="Read-only check for the managed host-only macOS LaunchAgent.")
    service_check.add_argument("--service-path", default="")
    service_check.add_argument("--timeout", type=int, default=5)
    service_check.set_defaults(handler=cmd_service_check)
    service_control = sub.add_parser("service-control", help="Preview or explicitly load, unload, or restart the managed Host LaunchAgent.")
    service_control.add_argument("--action", choices=["load", "unload", "restart"], required=True)
    service_control.add_argument("--service-path", default="")
    service_control.add_argument("--timeout", type=int, default=10)
    service_control.add_argument("--confirm-control", action="store_true")
    service_control.set_defaults(handler=cmd_service_control)
    service_remove = sub.add_parser("service-remove", help="Preview or remove an unloaded managed Host LaunchAgent file.")
    service_remove.add_argument("--service-path", default="")
    service_remove.add_argument("--timeout", type=int, default=5)
    service_remove.add_argument("--confirm-remove", action="store_true")
    service_remove.set_defaults(handler=cmd_service_remove)
    console_url = sub.add_parser("console-url", help="Show local and private console URLs.")
    console_url.set_defaults(handler=cmd_console_url)
    open_console = sub.add_parser("open-console", help="Open the local Console with a protected browser handoff without printing local authority.")
    open_console.set_defaults(handler=cmd_open_console)
    relay_preflight = sub.add_parser("relay-preflight", help="Validate pre-provisioned private Relay material without enabling it or using the network.")
    relay_preflight.set_defaults(handler=cmd_relay_preflight)
    relay_transition = sub.add_parser("relay-transition", help="Prepare or explicitly confirm one bounded private Relay config transition.", allow_abbrev=False)
    relay_transition.add_argument("--action", choices=["enable", "disable"], required=True)
    relay_transition.add_argument("--confirm-ref", default="", help="Confirm the exact prepared transition ref; the ref is non-secret and single-use.")
    relay_transition.set_defaults(handler=cmd_relay_transition)
    preview = sub.add_parser("tailscale-preview", help="Preview Tailscale Serve and revoke commands without executing them.")
    preview.add_argument("--https-port", type=int, choices=range(1, 65536), default=443, metavar="PORT")
    preview.set_defaults(handler=cmd_tailscale_preview)
    apply = sub.add_parser("tailscale-apply", help="Apply Tailscale Serve only after explicit confirmation.")
    apply.add_argument("--confirm", action="store_true")
    apply.add_argument("--https-port", type=int, choices=range(1, 65536), default=443, metavar="PORT")
    apply.add_argument("--replace-existing-serve", action="store_true")
    apply.set_defaults(handler=cmd_tailscale_apply)
    revoke = sub.add_parser("tailscale-revoke", help="Disable only the Host-owned Tailscale Serve HTTPS port after confirmation.")
    revoke.add_argument("--confirm", action="store_true")
    revoke.set_defaults(handler=cmd_tailscale_revoke)
    return parser


def main(argv=None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    bootstrap_args = raw_args
    password_argv_forbidden = any(
        value == "--password"
        or value.startswith("--password=")
        or value.startswith("--password-stdin=")
        or (value.startswith("--p") and "--password-stdin".startswith(value) and value != "--password-stdin")
        or (value == "--password-stdin" and index + 1 < len(bootstrap_args) and not bootstrap_args[index + 1].startswith("-"))
        for index, value in enumerate(bootstrap_args)
    )
    if password_argv_forbidden:
        emit({
            "ok": False,
            "operation": "host_bootstrap_owner",
            "error": "password_argv_forbidden",
            "message": "Password argv options do not exist. Use an interactive terminal or --password-stdin.",
            "password_omitted": True,
            "setup_code_omitted": True,
            "token_omitted": True,
        })
        return 2
    parser = build_parser()
    try:
        args = parser.parse_args(raw_args)
    except HostArgumentError:
        emit({
            "ok": False,
            "operation": "host_arguments",
            "error": "invalid_arguments",
            "argument_values_omitted": True,
            "token_omitted": True,
        })
        return 2
    try:
        return int(args.handler(args))
    except RuntimeError as exc:
        emit({"ok": False, "operation": f"host_{args.command}", "error": "host_not_ready", "message": str(exc), "token_omitted": True})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
