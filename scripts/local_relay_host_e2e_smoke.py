#!/usr/bin/env python3
"""Exercise the deployable Relay daemon with the real Host connector service."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import queue
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.relay_connector_service_smoke import (  # noqa: E402
    HOST_HOSTNAME,
    HTTP_REQUEST,
    HTTP_RESPONSE,
    RELAY_HOSTNAME,
    ROUTE,
    TIMEOUT,
    bind_listener,
    browser_http_round_trip,
    host_http_server,
    read_status,
    service_command,
    wait_status,
    write_private_json,
)
from scripts.relay_tls_authenticated_tunnel_smoke import (  # noqa: E402
    generate_certificate,
)


def reserve_loopback_listeners(count: int) -> list[socket.socket]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listeners.append(bind_listener())
        return listeners
    except Exception:
        for listener in listeners:
            listener.close()
        raise


def build_child_environment(home: Path, database_guard: Path) -> dict[str, str]:
    environment = {
        "AGENTOPS_DB_PATH": str(database_guard),
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(home),
    }
    for name in ("LANG", "LC_ALL", "LC_CTYPE"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def stop_process(process: subprocess.Popen[str] | None) -> tuple[str, str, int | None]:
    if process is None:
        return "", "", None
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    return stdout or "", stderr or "", process.returncode


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def wait_for_relay_refresh(
    path: Path,
    process: subprocess.Popen[str],
    *,
    previous_updated_at: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = read_status(path)
        updated_at = payload.get("updated_at_unix")
        if process.poll() is not None or (
            isinstance(updated_at, int) and updated_at > previous_updated_at
        ):
            return payload
        time.sleep(0.05)
    return payload


def run_locked() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"error": "openssl_unavailable", "ok": False}, sort_keys=True))
        return 1

    failures: list[str] = []
    relay_process: subprocess.Popen[str] | None = None
    connector_process: subprocess.Popen[str] | None = None
    relay_stdout = ""
    relay_stderr = ""
    connector_stdout = ""
    connector_stderr = ""
    relay_returncode: int | None = None
    connector_returncode: int | None = None
    relay_ready: dict[str, Any] = {}
    connector_ready: dict[str, Any] = {}
    relay_stopped: dict[str, Any] = {}
    connector_stopped: dict[str, Any] = {}
    connector_after_success: dict[str, Any] = {}
    connector_after_unknown_sni: dict[str, Any] = {}
    relay_after_unknown_sni: dict[str, Any] = {}
    relay_before_unknown_sni: dict[str, Any] = {}
    backend_receipt: dict[str, Any] = {}
    round_trip_ok = False
    wrong_sni_failed = False
    db_guard_created = False
    application_payload_retained = False

    with tempfile.TemporaryDirectory(prefix="agentops-local-relay-host-e2e-") as temporary:
        temporary_path = Path(temporary)
        temporary_path.chmod(0o700)
        private = temporary_path / "private"
        private.mkdir(mode=0o700)

        relay_certificate, relay_private_key = generate_certificate(
            openssl,
            private,
            prefix="relay",
            hostname=RELAY_HOSTNAME,
        )
        host_certificate, host_private_key = generate_certificate(
            openssl,
            private,
            prefix="host",
            hostname=HOST_HOSTNAME,
        )
        relay_private_key.chmod(0o600)
        host_private_key.chmod(0o600)

        backend_listener = bind_listener()
        host_http_port = int(backend_listener.getsockname()[1])
        (
            browser_reservation,
            connector_reservation,
            host_tls_reservation,
        ) = reserve_loopback_listeners(3)
        browser_port, connector_port, host_tls_port = (
            int(browser_reservation.getsockname()[1]),
            int(connector_reservation.getsockname()[1]),
            int(host_tls_reservation.getsockname()[1]),
        )

        tunnel_key = os.urandom(32)
        route_key_path = private / "route.key"
        route_key_path.write_text(tunnel_key.hex() + "\n", encoding="ascii")
        route_key_path.chmod(0o600)

        relay_config_path = private / "relay.json"
        relay_state_path = private / "relay-state.json"
        relay_status_path = private / "relay-status.json"
        write_private_json(
            relay_config_path,
            {
                "browser_listen": {"host": "127.0.0.1", "port": browser_port},
                "connector_listen": {"host": "127.0.0.1", "port": connector_port},
                "connector_tls": {
                    "cert_file": str(relay_certificate),
                    "key_file": str(relay_private_key),
                },
                "routes": [
                    {
                        "hostname": HOST_HOSTNAME,
                        "key_file": str(route_key_path),
                        "route": ROUTE,
                    }
                ],
                "schema_version": 1,
                "state_path": str(relay_state_path),
                "status_path": str(relay_status_path),
            },
        )

        connector_config_path = private / "connector.json"
        connector_secrets_path = private / "connector-secrets.json"
        connector_epoch_path = private / "connector-epoch.json"
        connector_status_path = private / "connector-status.json"
        write_private_json(
            connector_config_path,
            {
                "enabled": True,
                "host_certificate_path": str(host_certificate),
                "host_http_port": host_http_port,
                "host_private_key_path": str(host_private_key),
                "host_server_hostname": HOST_HOSTNAME,
                "host_tls_listen_port": host_tls_port,
                "relay_ca_path": str(relay_certificate),
                "relay_host": "127.0.0.1",
                "relay_port": connector_port,
                "relay_server_hostname": RELAY_HOSTNAME,
                "route": ROUTE,
                "schema_version": 1,
            },
        )
        write_private_json(
            connector_secrets_path,
            {"schema_version": 1, "tunnel_key_hex": tunnel_key.hex()},
        )

        database_guard = temporary_path / "mis-authority-must-not-open.db"
        child_environment = build_child_environment(private, database_guard)

        backend_results: queue.Queue[dict[str, Any]] = queue.Queue()
        backend_thread = threading.Thread(
            target=host_http_server,
            args=(backend_listener, backend_results, 1),
            daemon=True,
        )
        backend_thread.start()

        try:
            browser_reservation.close()
            connector_reservation.close()
            relay_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentops_mis_cli.relay_daemon",
                    "serve",
                    "--config",
                    str(relay_config_path),
                ],
                cwd=ROOT,
                env=child_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            relay_ready = wait_status(
                relay_status_path,
                lambda payload: payload.get("ready") is True,
            )
            require(relay_ready.get("ready") is True, "Relay daemon did not become ready", failures)

            host_tls_reservation.close()
            connector_process = subprocess.Popen(
                service_command(
                    connector_config_path,
                    connector_secrets_path,
                    connector_epoch_path,
                    connector_status_path,
                ),
                cwd=ROOT,
                env=child_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            connector_ready = wait_status(
                connector_status_path,
                lambda payload: payload.get("state") == "connected"
                and payload.get("successful_connections", 0) >= 1,
            )
            require(
                connector_ready.get("state") == "connected",
                "Host connector did not establish an authenticated route",
                failures,
            )
            require(
                wait_status(
                    relay_status_path,
                    lambda payload: payload.get("ready") is True
                    and payload.get("active_routes") == 1,
                ).get("active_routes")
                == 1,
                "Relay daemon did not publish the active Host route",
                failures,
            )

            browser_context = ssl.create_default_context(cafile=str(host_certificate))
            browser_context.minimum_version = ssl.TLSVersion.TLSv1_2
            response, tls_version = browser_http_round_trip(
                ("127.0.0.1", browser_port),
                browser_context,
            )
            round_trip_ok = response == HTTP_RESPONSE and tls_version in {
                "TLSv1.2",
                "TLSv1.3",
            }
            require(round_trip_ok, "browser-to-Host TLS/HTTP round trip mismatched", failures)
            connector_after_success = wait_status(
                connector_status_path,
                lambda payload: payload.get("host_tls_accepted_connections", 0) == 1,
            )
            require(
                connector_after_success.get("host_tls_accepted_connections") == 1
                and connector_after_success.get("host_tls_rejected_connections", 0) == 0,
                "Host TLS counters did not record exactly one accepted browser connection",
                failures,
            )
            relay_before_unknown_sni = read_status(relay_status_path)

            try:
                browser_http_round_trip(
                    ("127.0.0.1", browser_port),
                    browser_context,
                    server_hostname="unknown.agentops.test",
                )
            except (OSError, ssl.SSLError):
                wrong_sni_failed = True
            require(wrong_sni_failed, "unknown browser SNI did not fail closed", failures)
            time.sleep(0.3)
            connector_after_unknown_sni = read_status(connector_status_path)
            relay_after_unknown_sni = wait_for_relay_refresh(
                relay_status_path,
                relay_process,
                previous_updated_at=int(
                    relay_before_unknown_sni.get("updated_at_unix") or 0
                ),
            )
            require(
                connector_after_unknown_sni.get("host_tls_accepted_connections")
                == connector_after_success.get("host_tls_accepted_connections")
                and connector_after_unknown_sni.get("host_tls_rejected_connections")
                == connector_after_success.get("host_tls_rejected_connections"),
                "unknown SNI reached the Host TLS proxy",
                failures,
            )
            require(
                relay_process.poll() is None
                and relay_after_unknown_sni.get("ready") is True
                and relay_after_unknown_sni.get("active_routes") == 1,
                "Relay daemon lost readiness during unknown-SNI rejection",
                failures,
            )

            backend_thread.join(TIMEOUT)
            if not backend_thread.is_alive():
                backend_receipt = backend_results.get_nowait()
            require(
                not backend_thread.is_alive()
                and backend_receipt.get("matching_requests") == 1
                and not backend_receipt.get("error_type"),
                "Host HTTP backend did not receive exactly one accepted request",
                failures,
            )
        except Exception as exc:
            failures.append(f"end-to-end topology failed with {type(exc).__name__}")
        finally:
            connector_stdout, connector_stderr, connector_returncode = stop_process(
                connector_process
            )
            connector_process = None
            relay_stdout, relay_stderr, relay_returncode = stop_process(relay_process)
            relay_process = None
            connector_stopped = read_status(connector_status_path)
            relay_stopped = read_status(relay_status_path)
            for reservation in (
                browser_reservation,
                connector_reservation,
                host_tls_reservation,
            ):
                reservation.close()
            if backend_thread.is_alive():
                backend_listener.close()
                backend_thread.join(1.0)

        require(connector_returncode == 0, "Host connector did not stop cleanly", failures)
        require(relay_returncode == 0, "Relay daemon did not stop cleanly", failures)
        require(
            connector_stopped.get("state") == "stopped"
            and connector_stopped.get("host_tls_ready") is False,
            "Host connector retained a ready state after stop",
            failures,
        )
        require(
            connector_stopped.get("host_tls_accepted_connections")
            == connector_after_success.get("host_tls_accepted_connections")
            and connector_stopped.get("host_tls_rejected_connections")
            == connector_after_success.get("host_tls_rejected_connections"),
            "final Host TLS counters changed after unknown-SNI rejection",
            failures,
        )
        require(
            relay_stopped.get("stopped") is True
            and relay_stopped.get("ready") is False,
            "Relay daemon retained a ready state after stop",
            failures,
        )

        db_guard_created = database_guard.exists()
        require(not db_guard_created, "Relay topology opened the MIS authority database", failures)

        retained = json.dumps(
            {
                "connector_ready": connector_ready,
                "connector_after_success": connector_after_success,
                "connector_after_unknown_sni": connector_after_unknown_sni,
                "connector_stopped": connector_stopped,
                "relay_after_unknown_sni": relay_after_unknown_sni,
                "relay_before_unknown_sni": relay_before_unknown_sni,
                "relay_ready": relay_ready,
                "relay_stopped": relay_stopped,
            },
            sort_keys=True,
        ) + "".join(
            (relay_stdout, relay_stderr, connector_stdout, connector_stderr)
        )
        tunnel_key_hex = tunnel_key.hex()
        forbidden = (
            tunnel_key_hex,
            tunnel_key_hex[:24],
            tunnel_key_hex[24:48],
            tunnel_key_hex[48:],
            base64.b64encode(tunnel_key).decode("ascii"),
            HTTP_REQUEST.decode("ascii"),
            HTTP_RESPONSE.decode("ascii"),
            "/workspace",
            "AGENTOPS_HOST_OK",
            base64.b64encode(b"/workspace").decode("ascii"),
            base64.b64encode(b"AGENTOPS_HOST_OK").decode("ascii"),
        )
        application_payload_retained = any(value in retained for value in forbidden)
        require(
            not application_payload_retained,
            "Relay or connector status retained a route key or application payload",
            failures,
        )

    result = {
        "application_payload_retained": application_payload_retained,
        "browser_host_round_trip": round_trip_ok,
        "database_opened": db_guard_created,
        "deployed_public_relay_claimed": False,
        "dns_acme_claimed": False,
        "failures": failures,
        "host_tls_terminated_after_relay": round_trip_ok,
        "ok": not failures,
        "operation": "local_relay_host_e2e_smoke",
        "physical_second_device_claimed": False,
        "relay_is_mis_authority": False,
        "unknown_sni_failed_closed": wrong_sni_failed,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


def main() -> int:
    with Path(__file__).open("rb") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        return run_locked()


if __name__ == "__main__":
    raise SystemExit(main())
