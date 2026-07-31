#!/usr/bin/env python3
"""Exercise the Relay connector as a bounded foreground service process."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import signal
import socket
import ssl
import stat
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

from agentops_mis_cli.relay_tunnel import LocalFakeRelay  # noqa: E402
from agentops_mis_cli.relay_epoch_store import PersistentRelayEpochStore  # noqa: E402
from scripts.relay_tls_authenticated_tunnel_smoke import (  # noqa: E402
    HOST_HOSTNAME,
    RELAY_HOSTNAME,
    ROUTE,
    TIMEOUT,
    TlsServerListener,
    bind_listener,
    generate_certificate,
)


HTTP_REQUEST = (
    b"GET /workspace HTTP/1.1\r\n"
    b"Host: host.agentops.test\r\n"
    b"Connection: close\r\n\r\n"
)
HTTP_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 16\r\n"
    b"Connection: close\r\n\r\n"
    b"AGENTOPS_HOST_OK"
)


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def wait_status(path: Path, predicate: Any, timeout: float = TIMEOUT) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = read_status(path)
        if payload and predicate(payload):
            return payload
        time.sleep(0.05)
    return read_status(path)


def receive_until_headers(stream: socket.socket, maximum: int = 16 * 1024) -> bytes:
    payload = bytearray()
    while b"\r\n\r\n" not in payload:
        chunk = stream.recv(min(4096, maximum - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) >= maximum:
            raise ValueError("bounded HTTP request exceeded")
    return bytes(payload)


def receive_to_eof(stream: socket.socket, maximum: int = 64 * 1024) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = stream.recv(min(4096, maximum - received))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        received += len(chunk)
        if received >= maximum:
            raise ValueError("bounded HTTP response exceeded")


def host_http_server(
    listener: socket.socket,
    result: queue.Queue[dict[str, Any]],
    expected_connections: int,
) -> None:
    receipt: dict[str, Any] = {"matching_requests": 0}
    # One backend listener spans the deliberate Relay stop/backoff/reconnect.
    listener.settimeout(TIMEOUT * 3)
    try:
        for _ in range(expected_connections):
            stream, _ = listener.accept()
            stream.settimeout(TIMEOUT)
            with stream:
                if receive_until_headers(stream) == HTTP_REQUEST:
                    receipt["matching_requests"] += 1
                stream.sendall(HTTP_RESPONSE)
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()
        result.put(receipt)


def browser_http_round_trip(
    address: tuple[str, int],
    context: ssl.SSLContext,
    *,
    server_hostname: str = HOST_HOSTNAME,
) -> tuple[bytes, str | None]:
    with socket.create_connection(address, timeout=TIMEOUT) as raw:
        raw.settimeout(TIMEOUT)
        with context.wrap_socket(raw, server_hostname=server_hostname) as tls:
            tls_version = tls.version()
            tls.sendall(HTTP_REQUEST)
            return receive_to_eof(tls), tls_version


def service_command(
    config_path: Path,
    secrets_path: Path,
    epoch_path: Path,
    status_path: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentops_mis_cli.relay_connector_service",
        "--config",
        str(config_path),
        "--secrets",
        str(secrets_path),
        "--epoch-state",
        str(epoch_path),
        "--status",
        str(status_path),
    ]


def start_relay(
    *,
    browser_port: int,
    connector_port: int,
    context: ssl.SSLContext,
    tunnel_key: bytes,
) -> tuple[LocalFakeRelay, TlsServerListener, tuple[str, int]]:
    browser_listener = bind_listener(browser_port)
    connector_listener = TlsServerListener(bind_listener(connector_port), context)
    relay = LocalFakeRelay(
        browser_listener=browser_listener,
        connector_listener=connector_listener,
        route=ROUTE,
        key=tunnel_key,
    )
    relay.start()
    return relay, connector_listener, browser_listener.getsockname()


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    relay: LocalFakeRelay | None = None
    process: subprocess.Popen[str] | None = None
    final_status: dict[str, Any] = {}
    host_receipt: dict[str, Any] = {}
    replacement_relay_events: list[dict[str, Any]] = []
    replacement_relay_tls_connections = 0

    with tempfile.TemporaryDirectory(prefix="agentops-relay-service-") as temporary:
        temporary_path = Path(temporary)
        service_home = temporary_path / "service"
        service_home.mkdir(mode=0o700)

        disabled_config = service_home / "disabled-config.json"
        disabled_status = service_home / "disabled-status.json"
        write_private_json(disabled_config, {"enabled": False, "schema_version": 1})
        disabled = subprocess.run(
            service_command(
                disabled_config,
                service_home / "absent-secrets.json",
                service_home / "disabled-epoch.json",
                disabled_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        disabled_payload = read_status(disabled_status)
        if (
            disabled.returncode != 0
            or disabled_payload.get("state") != "disabled"
            or disabled_payload.get("connect_attempts") != 0
            or disabled_payload.get("enabled") is not False
        ):
            failures.append("disabled service did not exit without network work")

        broad_status = service_home / "broad-status.json"
        broad_status.write_text("{}\n", encoding="utf-8")
        broad_status.chmod(0o644)
        broad = subprocess.run(
            service_command(
                disabled_config,
                service_home / "absent-secrets.json",
                service_home / "broad-epoch.json",
                broad_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if broad.returncode == 0 or stat.S_IMODE(broad_status.stat().st_mode) != 0o644:
            failures.append("broad status target was silently repaired")

        relay_certificate, relay_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="relay-service",
            hostname=RELAY_HOSTNAME,
        )
        host_certificate, host_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="host-service",
            hostname=HOST_HOSTNAME,
        )
        host_key.chmod(0o600)
        relay_server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        relay_server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        relay_server_context.load_cert_chain(str(relay_certificate), str(relay_key))
        browser_context = ssl.create_default_context(cafile=str(host_certificate))
        browser_context.minimum_version = ssl.TLSVersion.TLSv1_2

        browser_probe = bind_listener()
        connector_probe = bind_listener()
        host_http_probe = bind_listener()
        host_tls_probe = bind_listener()
        browser_port = browser_probe.getsockname()[1]
        connector_port = connector_probe.getsockname()[1]
        host_http_port = host_http_probe.getsockname()[1]
        host_tls_port = host_tls_probe.getsockname()[1]
        browser_probe.close()
        connector_probe.close()
        host_tls_probe.close()
        tunnel_key = os.urandom(32)

        enabled_config_payload = {
            "enabled": True,
            "host_certificate_path": str(host_certificate),
            "host_http_port": host_http_port,
            "host_private_key_path": str(host_key),
            "host_server_hostname": HOST_HOSTNAME,
            "host_tls_listen_port": host_tls_port,
            "relay_ca_path": str(relay_certificate),
            "relay_host": "127.0.0.1",
            "relay_port": connector_port,
            "relay_server_hostname": RELAY_HOSTNAME,
            "route": ROUTE,
            "schema_version": 1,
        }
        enabled_config = service_home / "enabled-config.json"
        write_private_json(enabled_config, enabled_config_payload)

        legacy_config = service_home / "legacy-config.json"
        legacy_status = service_home / "legacy-status.json"
        write_private_json(
            legacy_config,
            {
                "enabled": True,
                "host_tls_port": host_tls_port,
                "relay_ca_path": str(relay_certificate),
                "relay_host": "127.0.0.1",
                "relay_port": connector_port,
                "relay_server_hostname": RELAY_HOSTNAME,
                "route": ROUTE,
                "schema_version": 1,
            },
        )

        bad_secrets = service_home / "bad-secrets.json"
        bad_status = service_home / "bad-status.json"
        write_private_json(
            bad_secrets,
            {"schema_version": 1, "tunnel_key_hex": "not-a-valid-key"},
        )
        bad = subprocess.run(
            service_command(
                enabled_config,
                bad_secrets,
                service_home / "bad-epoch.json",
                bad_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        bad_payload = read_status(bad_status)
        if (
            bad.returncode == 0
            or bad_payload.get("state") != "failed"
            or bad_payload.get("failure_code") != "tunnel_key_invalid"
            or bad_payload.get("connect_attempts") != 0
            or "not-a-valid-key" in bad.stdout + bad.stderr + json.dumps(bad_payload)
        ):
            failures.append("invalid service secret did not fail closed before network")

        secrets_path = service_home / "secrets.json"
        epoch_path = service_home / "epoch.json"
        status_path = service_home / "status.json"
        write_private_json(
            secrets_path,
            {"schema_version": 1, "tunnel_key_hex": tunnel_key.hex()},
        )

        legacy = subprocess.run(
            service_command(
                legacy_config,
                secrets_path,
                service_home / "legacy-epoch.json",
                legacy_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        legacy_payload = read_status(legacy_status)
        if (
            legacy.returncode == 0
            or legacy_payload.get("state") != "failed"
            or legacy_payload.get("failure_code") != "enabled_config_upgrade_required"
            or legacy_payload.get("connect_attempts") != 0
        ):
            failures.append("legacy external-TLS config did not fail before network")

        wrong_certificate, wrong_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="wrong-host-service",
            hostname="wrong-host.agentops.test",
        )
        wrong_key.chmod(0o600)
        wrong_host_config = dict(enabled_config_payload)
        wrong_host_config.update(
            {
                "host_certificate_path": str(wrong_certificate),
                "host_private_key_path": str(wrong_key),
            }
        )
        wrong_host_config_path = service_home / "wrong-host-config.json"
        wrong_host_status = service_home / "wrong-host-status.json"
        write_private_json(wrong_host_config_path, wrong_host_config)
        wrong_host = subprocess.run(
            service_command(
                wrong_host_config_path,
                secrets_path,
                service_home / "wrong-host-epoch.json",
                wrong_host_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        wrong_host_payload = read_status(wrong_host_status)
        if (
            wrong_host.returncode == 0
            or wrong_host_payload.get("state") != "failed"
            or wrong_host_payload.get("failure_code")
            != "host_certificate_hostname_mismatch"
            or wrong_host_payload.get("connect_attempts") != 0
        ):
            failures.append("mismatched Host certificate hostname did not fail before network")

        mismatched_key_config = dict(enabled_config_payload)
        mismatched_key_config["host_private_key_path"] = str(wrong_key)
        mismatched_key_config_path = service_home / "mismatched-key-config.json"
        mismatched_key_status = service_home / "mismatched-key-status.json"
        write_private_json(mismatched_key_config_path, mismatched_key_config)
        mismatched_key = subprocess.run(
            service_command(
                mismatched_key_config_path,
                secrets_path,
                service_home / "mismatched-key-epoch.json",
                mismatched_key_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        mismatched_key_payload = read_status(mismatched_key_status)
        if (
            mismatched_key.returncode == 0
            or mismatched_key_payload.get("state") != "failed"
            or mismatched_key_payload.get("failure_code")
            != "host_certificate_key_mismatch"
            or mismatched_key_payload.get("connect_attempts") != 0
        ):
            failures.append("mismatched Host certificate key did not fail before network")

        invalid_ca = service_home / "invalid-ca.pem"
        invalid_ca.write_text("not a certificate\n", encoding="utf-8")
        invalid_ca.chmod(0o600)
        invalid_ca_config = dict(enabled_config_payload)
        invalid_ca_config["relay_ca_path"] = str(invalid_ca)
        invalid_ca_config_path = service_home / "invalid-ca-config.json"
        invalid_ca_status = service_home / "invalid-ca-status.json"
        write_private_json(invalid_ca_config_path, invalid_ca_config)
        invalid_ca_service = subprocess.run(
            service_command(
                invalid_ca_config_path,
                secrets_path,
                service_home / "invalid-ca-epoch.json",
                invalid_ca_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        invalid_ca_payload = read_status(invalid_ca_status)
        invalid_ca_rendered = (
            invalid_ca_service.stdout
            + invalid_ca_service.stderr
            + json.dumps(invalid_ca_payload, sort_keys=True)
        )
        if (
            invalid_ca_service.returncode == 0
            or invalid_ca_payload.get("failure_code") != "relay_ca_invalid"
            or str(invalid_ca) in invalid_ca_rendered
        ):
            failures.append("invalid Relay CA did not fail with a path-free bounded code")

        broad_tls_directory = temporary_path / "broad-tls"
        broad_tls_directory.mkdir(mode=0o755)
        broad_tls_directory.chmod(0o755)
        broad_certificate = broad_tls_directory / "host-cert.pem"
        broad_private_key = broad_tls_directory / "host-key.pem"
        shutil.copyfile(host_certificate, broad_certificate)
        shutil.copyfile(host_key, broad_private_key)
        broad_certificate.chmod(0o644)
        broad_private_key.chmod(0o600)
        broad_tls_config = dict(enabled_config_payload)
        broad_tls_config.update(
            {
                "host_certificate_path": str(broad_certificate),
                "host_private_key_path": str(broad_private_key),
            }
        )
        broad_tls_config_path = service_home / "broad-tls-config.json"
        broad_tls_status = service_home / "broad-tls-status.json"
        write_private_json(broad_tls_config_path, broad_tls_config)
        broad_tls = subprocess.run(
            service_command(
                broad_tls_config_path,
                secrets_path,
                service_home / "broad-tls-epoch.json",
                broad_tls_status,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        broad_tls_payload = read_status(broad_tls_status)
        if (
            broad_tls.returncode == 0
            or broad_tls_payload.get("state") != "failed"
            or broad_tls_payload.get("failure_code")
            != "private_directory_permissions_invalid"
            or broad_tls_payload.get("connect_attempts") != 0
        ):
            failures.append("broad TLS parent directory did not fail before network")

        identity_payload = json.dumps(
            {
                "relay_host": enabled_config_payload["relay_host"],
                "relay_port": enabled_config_payload["relay_port"],
                "relay_server_hostname": enabled_config_payload["relay_server_hostname"],
                "route": enabled_config_payload["route"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        preexisting_epoch = PersistentRelayEpochStore(
            epoch_path,
            connector_identity=hashlib.sha256(identity_payload + tunnel_key).digest(),
        ).next_epoch()

        host_result: queue.Queue[dict[str, Any]] = queue.Queue()
        host_thread = threading.Thread(
            target=host_http_server,
            args=(host_http_probe, host_result, 2),
            daemon=True,
        )
        host_thread.start()

        relay, tls_listener, browser_address = start_relay(
            browser_port=browser_port,
            connector_port=connector_port,
            context=relay_server_context,
            tunnel_key=tunnel_key,
        )
        process = subprocess.Popen(
            service_command(enabled_config, secrets_path, epoch_path, status_path),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        connected = wait_status(
            status_path,
            lambda payload: payload.get("state") == "connected"
            and payload.get("successful_connections", 0) >= 1,
        )
        if connected.get("state") != "connected":
            failures.append("service did not establish the first authenticated tunnel")
        if int(connected.get("current_epoch") or 0) <= preexisting_epoch:
            failures.append("service did not continue the pre-existing connector epoch")

        status_before_second_instance = status_path.read_bytes()
        second_instance = subprocess.run(
            service_command(enabled_config, secrets_path, epoch_path, status_path),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if second_instance.returncode == 0 or status_path.read_bytes() != status_before_second_instance:
            failures.append("second service instance changed the active owner's status")

        try:
            first_response, first_version = browser_http_round_trip(
                browser_address,
                browser_context,
            )
            if first_response != HTTP_RESPONSE:
                failures.append("first service browser round trip mismatched")
            if first_version not in {"TLSv1.2", "TLSv1.3"}:
                failures.append("first service browser TLS version mismatched")
        except Exception as exc:
            failures.append(f"first service browser round trip failed with {type(exc).__name__}")

        wrong_sni_failed = False
        try:
            browser_http_round_trip(
                browser_address,
                browser_context,
                server_hostname="wrong.agentops.test",
            )
        except (OSError, ssl.SSLError):
            wrong_sni_failed = True
        if not wrong_sni_failed:
            failures.append("service-owned Host TLS proxy accepted wrong SNI")

        relay.stop()
        relay = None
        disconnected = wait_status(
            status_path,
            lambda payload: payload.get("state") in {"backoff", "connecting"},
            timeout=4.0,
        )
        first_epoch = int(connected.get("current_epoch") or 0)
        if disconnected.get("state") not in {"backoff", "connecting"}:
            failures.append("service did not observe Relay disconnect")

        relay, replacement_tls_listener, replacement_browser_address = start_relay(
            browser_port=browser_port,
            connector_port=connector_port,
            context=relay_server_context,
            tunnel_key=tunnel_key,
        )
        reconnected = wait_status(
            status_path,
            lambda payload: payload.get("state") == "connected"
            and payload.get("successful_connections", 0) >= 2,
        )
        if (
            reconnected.get("state") != "connected"
            or int(reconnected.get("current_epoch") or 0) <= first_epoch
        ):
            failures.append("service reconnect did not use a higher persisted epoch")
        try:
            second_response, second_version = browser_http_round_trip(
                replacement_browser_address,
                browser_context,
            )
            if second_response != HTTP_RESPONSE:
                failures.append("second service browser round trip mismatched")
            if second_version not in {"TLSv1.2", "TLSv1.3"}:
                failures.append("second service browser TLS version mismatched")
        except Exception as exc:
            failures.append(f"second service browser round trip failed with {type(exc).__name__}")

        host_thread.join(TIMEOUT)
        host_receipt = host_result.get_nowait() if not host_thread.is_alive() else {}
        if (
            host_thread.is_alive()
            or host_receipt.get("error_type")
            or host_receipt.get("matching_requests") != 2
        ):
            failures.append("service-owned TLS proxy did not forward two Host HTTP requests")

        stalled_browser = socket.create_connection(
            replacement_browser_address,
            timeout=TIMEOUT,
        )
        stalled_browser.settimeout(TIMEOUT)
        stalled_status = wait_status(
            status_path,
            lambda payload: payload.get("host_tls_active_connections", 0) >= 1,
            timeout=4.0,
        )
        if stalled_status.get("host_tls_active_connections", 0) < 1:
            failures.append("service did not own the stalled browser TLS handshake")

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        try:
            if stalled_browser.recv(1) not in {b"", None}:
                failures.append("stalled service browser remained readable after SIGTERM")
        except OSError:
            pass
        finally:
            stalled_browser.close()
        final_status = read_status(status_path)
        if (
            process.returncode != 0
            or final_status.get("state") != "stopped"
            or final_status.get("ok") is not True
            or final_status.get("host_tls_state") != "stopped"
            or final_status.get("host_tls_ready") is not False
        ):
            failures.append("service SIGTERM did not produce a clean stopped state")

        rendered = json.dumps(
            {
                "connected": connected,
                "disabled": disabled_payload,
                "final": final_status,
                "reconnected": reconnected,
            },
            sort_keys=True,
        ) + stdout + stderr
        forbidden = (
            tunnel_key.hex(),
            ROUTE,
            RELAY_HOSTNAME,
            HOST_HOSTNAME,
            str(temporary_path),
            str(browser_port),
            str(connector_port),
            str(host_http_port),
            str(host_tls_port),
        )
        if any(value in rendered for value in forbidden):
            failures.append("service status or output exposed private configuration")
        if stat.S_IMODE(status_path.stat().st_mode) != 0o600:
            failures.append("service status file was not 0600")
        if stat.S_IMODE(epoch_path.stat().st_mode) != 0o600:
            failures.append("service epoch file was not 0600")
        failure_counts = final_status.get("host_tls_failure_counts") or {}
        if (
            failure_counts.get("backend_connect") != 0
            or failure_counts.get("forwarding") != 0
            or failure_counts.get("tls_handshake", 0) < 1
        ):
            failures.append("service Host TLS failure-stage counts were inconsistent")
        if len(tls_listener.accepted_versions()) < 2 or len(
            replacement_tls_listener.accepted_versions()
        ) < 2:
            failures.append("service control/data connections did not all use Relay TLS")
        replacement_relay_events = relay.metadata.snapshot()
        replacement_relay_tls_connections = len(
            replacement_tls_listener.accepted_versions()
        )

        relay.stop()
        relay = None
        process = None

    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    if relay is not None:
        relay.stop()

    result = {
        "crash_persistent_epoch": True,
        "deployed_relay": False,
        "disabled_by_default": True,
        "failures": failures,
        "host_http_matching_requests": int(host_receipt.get("matching_requests") or 0),
        "host_lifecycle_integrated": False,
        "host_tls_accepted_connections": int(
            final_status.get("host_tls_accepted_connections") or 0
        ),
        "host_tls_failure_counts": final_status.get("host_tls_failure_counts") or {},
        "host_tls_handshake_failure_counts": final_status.get(
            "host_tls_handshake_failure_counts"
        )
        or {},
        "host_tls_proxy_integrated": True,
        "host_tls_rejected_connections": int(
            final_status.get("host_tls_rejected_connections") or 0
        ),
        "nested_tls_after_reconnect": not any("round trip" in item for item in failures),
        "ok": not failures,
        "operation": "relay_connector_service_smoke",
        "private_status_and_state": not any("0600" in item for item in failures),
        "replacement_relay_events": replacement_relay_events,
        "replacement_relay_tls_connections": replacement_relay_tls_connections,
        "service_process_lifecycle": not any("SIGTERM" in item for item in failures),
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
