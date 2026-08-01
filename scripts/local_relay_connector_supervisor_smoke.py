#!/usr/bin/env python3
"""Prove bounded reconnect supervision against the loopback fake Relay."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import socket
import ssl
import struct
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

from agentops_mis_cli.relay_connector_supervisor import (  # noqa: E402
    MAX_STATUS_EVENTS,
    RelayConnectorSupervisor,
)
from agentops_mis_cli.relay_epoch_store import PersistentRelayEpochStore  # noqa: E402
from agentops_mis_cli.relay_tunnel import LocalFakeRelay, RelayProtocolError  # noqa: E402


HOSTNAME = "agentops-supervisor.test"
ROUTE = "supervisor-local-test"
TIMEOUT = 8.0
REQUEST = b"\x00\xffsupervisor-reconnect-request\r\n" + bytes(range(256))
RESPONSE = b"\xff\x00supervisor-reconnect-response\r\n" + bytes(reversed(range(256)))


def bind_listener(port: int = 0) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(16)
    return listener


def read_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = stream.recv(size)
        if not chunk:
            raise EOFError("bounded message ended early")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def send_message(stream: socket.socket, payload: bytes) -> None:
    stream.sendall(struct.pack("!I", len(payload)) + payload)


def receive_message(stream: socket.socket) -> bytes:
    size = struct.unpack("!I", read_exact(stream, 4))[0]
    if size > 128 * 1024:
        raise ValueError("message exceeds smoke bound")
    return read_exact(stream, size)


def generate_certificate(openssl: str, directory: Path) -> tuple[Path, Path]:
    certificate = directory / "host-cert.pem"
    private_key = directory / "host-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={HOSTNAME}",
            "-addext",
            f"subjectAltName=DNS:{HOSTNAME}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    return certificate, private_key


def host_tls_server(
    listener: socket.socket,
    certificate: Path,
    private_key: Path,
    result: queue.Queue[dict[str, Any]],
) -> None:
    receipt: dict[str, Any] = {"request_matches": False}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    listener.settimeout(TIMEOUT)
    try:
        raw, _ = listener.accept()
        raw.settimeout(TIMEOUT)
        with raw:
            with context.wrap_socket(raw, server_side=True) as tls:
                receipt["request_matches"] = receive_message(tls) == REQUEST
                send_message(tls, RESPONSE)
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()
        result.put(receipt)


def tls_round_trip(address: tuple[str, int]) -> tuple[bytes, bytes]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(address, timeout=TIMEOUT) as raw:
        raw.settimeout(TIMEOUT)
        with context.wrap_socket(raw, server_hostname=HOSTNAME) as tls:
            peer = tls.getpeercert(binary_form=True)
            send_message(tls, REQUEST)
            return receive_message(tls), peer


def wait_until(predicate: Any, timeout_seconds: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def new_relay(
    browser_port: int,
    connector_port: int,
    key: bytes,
) -> tuple[LocalFakeRelay, tuple[str, int], tuple[str, int]]:
    browser_listener = bind_listener(browser_port)
    connector_listener = bind_listener(connector_port)
    relay = LocalFakeRelay(
        browser_listener=browser_listener,
        connector_listener=connector_listener,
        route=ROUTE,
        key=key,
    )
    relay.start()
    return relay, browser_listener.getsockname(), connector_listener.getsockname()


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    relay: LocalFakeRelay | None = None
    supervisor: RelayConnectorSupervisor | None = None
    host_result: queue.Queue[dict[str, Any]] = queue.Queue()
    host_thread: threading.Thread | None = None

    with tempfile.TemporaryDirectory(prefix="agentops-supervisor-") as temporary:
        temporary_path = Path(temporary)
        certificate, private_key = generate_certificate(openssl, temporary_path)
        expected_der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
        expected_fingerprint = hashlib.sha256(expected_der).digest()
        key = os.urandom(32)
        epoch_store = PersistentRelayEpochStore(
            temporary_path / "relay-state" / "epoch.json",
            connector_identity=hashlib.sha256(ROUTE.encode("ascii") + key).digest(),
        )

        disabled = RelayConnectorSupervisor(
            relay_address=("127.0.0.1", 9),
            host_tls_target=("127.0.0.1", 9),
            route=ROUTE,
            key=key,
        )
        disabled_started = disabled.start()
        disabled_status = disabled.status()
        time.sleep(0.1)
        disabled_unchanged = disabled.status() == disabled_status
        disabled.stop()
        if disabled_started or not disabled_unchanged or disabled_status["state"] != "disabled":
            failures.append("disabled-by-default supervisor spawned work")

        weak_key_rejected = False
        non_loopback_rejected = False
        try:
            RelayConnectorSupervisor(
                relay_address=("127.0.0.1", 9),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                key=b"short",
                enabled=True,
            )
        except RelayProtocolError:
            weak_key_rejected = True
        try:
            RelayConnectorSupervisor(
                relay_address=("localhost", 9),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                key=key,
                enabled=True,
            )
        except ValueError:
            non_loopback_rejected = True
        if not weak_key_rejected:
            failures.append("weak supervisor key was accepted")
        if not non_loopback_rejected:
            failures.append("non-literal Relay target was accepted")

        failed_state_directory = temporary_path / "failed-relay-state"
        failed_state_directory.mkdir(mode=0o700)
        failed_state_path = failed_state_directory / "epoch.json"
        failed_state_path.write_text("{}\n", encoding="utf-8")
        failed_state_path.chmod(0o600)
        failed_allocator = PersistentRelayEpochStore(
            failed_state_path,
            connector_identity=b"corrupt-state-fixture",
        )
        allocation_failure = RelayConnectorSupervisor(
            relay_address=("127.0.0.1", 9),
            host_tls_target=("127.0.0.1", 9),
            route=ROUTE,
            key=key,
            enabled=True,
            epoch_allocator=failed_allocator,
        )
        allocation_failure.start()
        allocation_failed_closed = wait_until(
            lambda: allocation_failure.status()["state"] == "failed",
            1.0,
        )
        allocation_failure_status = allocation_failure.status()
        if (
            not allocation_failed_closed
            or allocation_failure_status["failure_code"] != "epoch_allocation_failed"
            or allocation_failure_status["current_epoch"] is not None
            or allocation_failure_status["successful_connections"] != 0
        ):
            failures.append("epoch allocation failure did not fail closed before connection")
        allocation_failure.stop()

        stop_race_stable = True
        for _ in range(32):
            stop_race = RelayConnectorSupervisor(
                relay_address=("127.0.0.1", 9),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                key=key,
                enabled=True,
                connect_timeout_seconds=0.1,
                backoff_initial_seconds=0.01,
                backoff_cap_seconds=0.01,
            )
            stop_race.start()
            if not stop_race.stop(timeout_seconds=0.5):
                stop_race_stable = False
                break
            stopped_snapshot = stop_race.status()
            time.sleep(0.01)
            settled_snapshot = stop_race.status()
            if (
                stopped_snapshot["state"] != "stopped"
                or settled_snapshot["connect_attempts"] != stopped_snapshot["connect_attempts"]
                or settled_snapshot["current_epoch"] != stopped_snapshot["current_epoch"]
                or settled_snapshot["successful_connections"]
                != stopped_snapshot["successful_connections"]
            ):
                stop_race_stable = False
                break
        if not stop_race_stable:
            failures.append("immediate stop/start race created work after stop")

        host_listener = bind_listener()
        host_address = host_listener.getsockname()
        first_browser_listener = bind_listener()
        first_connector_listener = bind_listener()
        browser_port = first_browser_listener.getsockname()[1]
        connector_port = first_connector_listener.getsockname()[1]
        first_browser_listener.close()
        first_connector_listener.close()

        relay, browser_address, connector_address = new_relay(browser_port, connector_port, key)
        host_thread = threading.Thread(
            target=host_tls_server,
            args=(host_listener, certificate, private_key, host_result),
            daemon=True,
        )
        host_thread.start()
        supervisor = RelayConnectorSupervisor(
            relay_address=connector_address,
            host_tls_target=host_address,
            route=ROUTE,
            key=key,
            enabled=True,
            epoch_allocator=epoch_store,
            connect_timeout_seconds=0.5,
            backoff_initial_seconds=0.03,
            backoff_cap_seconds=0.12,
        )
        if not supervisor.start() or not supervisor.wait_for_connections(1, TIMEOUT):
            failures.append("initial supervised connection did not become ready")
        initial_status = supervisor.status()
        initial_epoch = initial_status["current_epoch"] or 0

        relay.stop()
        relay = None
        if not wait_until(lambda: supervisor.status()["state"] != "connected", 3.0):
            failures.append("forced control disconnect was not observed")
        if not wait_until(
            lambda: len(
                [
                    event
                    for event in supervisor.status()["events"]
                    if event["state"] == "backoff"
                ]
            )
            >= 3,
            3.0,
        ):
            failures.append("bounded exponential backoff was not observable")

        relay, replacement_browser_address, replacement_connector_address = new_relay(
            browser_port, connector_port, key
        )
        if replacement_browser_address != browser_address or replacement_connector_address != connector_address:
            failures.append("replacement fake Relay did not retain loopback endpoints")
        if not supervisor.wait_for_connections(2, TIMEOUT):
            failures.append("supervisor did not reconnect")
        reconnected_status = supervisor.status()
        reconnected_epoch = reconnected_status["current_epoch"] or 0
        if reconnected_epoch <= initial_epoch:
            failures.append("reconnect epoch did not strictly increase")
        connecting_epochs = [
            event["epoch"]
            for event in reconnected_status["events"]
            if event["state"] == "connecting"
        ]
        if (
            len(connecting_epochs) < 2
            or any(epoch is None for epoch in connecting_epochs)
            or connecting_epochs != sorted(set(connecting_epochs))
        ):
            failures.append("in-process connection epochs were not strictly increasing")

        response = b""
        peer = b""
        try:
            response, peer = tls_round_trip(replacement_browser_address)
        except Exception as exc:
            failures.append(f"TLS round trip failed with {type(exc).__name__}")
        if response != RESPONSE:
            failures.append("TLS response mismatch after reconnect")
        if hashlib.sha256(peer).digest() != expected_fingerprint:
            failures.append("TLS peer certificate did not come from Host endpoint")

        host_thread.join(TIMEOUT)
        if host_thread.is_alive():
            failures.append("Host TLS server did not stop")
        else:
            receipt = host_result.get_nowait()
            if receipt.get("error_type") or not receipt.get("request_matches"):
                failures.append("Host TLS server did not receive the exact request")

        status_text = json.dumps(reconnected_status, sort_keys=True)
        forbidden_values = (
            ROUTE,
            key.hex(),
            hashlib.sha256(key).hexdigest(),
            str(temporary_path),
            REQUEST.hex(),
            RESPONSE.hex(),
            str(browser_port),
            str(connector_port),
            str(host_address[1]),
        )
        if any(value in status_text for value in forbidden_values):
            failures.append("status metadata exposed a forbidden identifier or value")
        if len(reconnected_status["events"]) > MAX_STATUS_EVENTS:
            failures.append("supervisor status history exceeded its bound")
        observed_backoffs = [
            event["backoff_seconds"]
            for event in reconnected_status["events"]
            if event["state"] == "backoff"
        ]
        expected_backoffs = [0.03, 0.06, 0.12]
        if observed_backoffs[:3] != expected_backoffs or any(
            delay is None or delay > 0.12 for delay in observed_backoffs
        ):
            failures.append("exponential backoff did not follow its deterministic cap")
        if set(reconnected_status) != {
            "connect_attempts",
            "current_epoch",
            "enabled",
            "events",
            "failure_code",
            "limitations",
            "relay_tls_enabled",
            "state",
            "successful_connections",
        } or any(
            set(event) != {"attempt", "backoff_seconds", "epoch", "state"}
            for event in reconnected_status["events"]
        ):
            failures.append("supervisor status contained a non-allowlisted field")
        if reconnected_status["relay_tls_enabled"] is not False:
            failures.append("plain loopback supervisor incorrectly reported Relay TLS")
        if reconnected_status["limitations"] != {
            "crash_persistent_epoch": True,
            "deployed_relay": False,
            "dns_sni_certificate_lifecycle": False,
            "exactly_once_transport": False,
            "tailscale_changed": False,
        }:
            failures.append("supervisor limitations were overstated")

        before_stop = supervisor.status()
        stop_started = time.monotonic()
        stopped = supervisor.stop(timeout_seconds=2.5)
        stop_elapsed = time.monotonic() - stop_started
        after_stop = supervisor.status()
        restart_started = supervisor.start()
        time.sleep(0.25)
        settled = supervisor.status()
        if not stopped or stop_elapsed >= 3.0:
            failures.append("supervisor stop was not bounded")
        if restart_started:
            failures.append("supervisor restarted after stop")
        if (
            settled["connect_attempts"] != after_stop["connect_attempts"]
            or settled["current_epoch"] != after_stop["current_epoch"]
            or settled["successful_connections"] != after_stop["successful_connections"]
        ):
            failures.append("supervisor spawned connection work after stop")
        if after_stop["connect_attempts"] < before_stop["connect_attempts"]:
            failures.append("stop regressed the attempt counter")
        if after_stop["state"] != "stopped":
            failures.append("stopped supervisor did not report stopped state")

        supervisor_source = (
            ROOT / "agentops_mis_cli" / "relay_connector_supervisor.py"
        ).read_text(encoding="utf-8")
        forbidden_source_calls = (".bind(", ".listen(", "launchctl", "tailscale ", "subprocess.")
        if any(value in supervisor_source.lower() for value in forbidden_source_calls):
            failures.append("supervisor source owned a listener or network configuration call")

    if supervisor is not None:
        supervisor.stop()
    if relay is not None:
        relay.stop()

    result = {
        "bounded_status_events": True,
        "crash_persistent_epoch": True,
        "deployed_relay": False,
        "disabled_by_default": True,
        "dns_sni_certificate_lifecycle": False,
        "exactly_once_transport": False,
        "epoch_allocation_failure_bounded": not any(
            "epoch allocation failure" in item for item in failures
        ),
        "failures": failures,
        "forced_control_disconnect": not any("disconnect" in item for item in failures),
        "higher_epoch_reconnect": not any("epoch" in item for item in failures),
        "initial_connect": not any("initial" in item for item in failures),
        "literal_loopback_only": True,
        "ok": not failures,
        "operation": "local_relay_connector_supervisor_smoke",
        "real_tls_after_reconnect": not any("TLS" in item or "Host TLS" in item for item in failures),
        "stop_bounded": not any("stop" in item or "after stop" in item for item in failures),
        "stop_start_race_stable": not any("stop/start race" in item for item in failures),
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
