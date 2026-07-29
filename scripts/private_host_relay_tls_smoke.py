#!/usr/bin/env python3
"""Prove opaque loopback Relay forwarding to a Host-owned TLS endpoint."""
from __future__ import annotations

import hashlib
import json
import queue
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any


HOSTNAME = "agentops-host.test"
MAX_APPLICATION_BYTES = 128 * 1024
SOCKET_TIMEOUT_SECONDS = 10
REQUEST = (
    b"\x00\xff\x80private-host-relay-request\r\n"
    + bytes(range(256))
    + b"\x00binary-tail\xfe\xfd"
)
RESPONSE = (
    b"\xff\x00private-host-relay-response\r\n"
    + bytes(reversed(range(256)))
    + b"\x00binary-tail\x81\x80"
)


class RelayEvidence:
    """Keep only bounded transport status and byte counts."""

    _ALLOWED_DIRECTIONS = {"browser_to_host", "host_to_browser"}
    _ALLOWED_STATUSES = {"accepted", "failed", "forwarded", "half_closed", "closed"}

    def __init__(self, max_events: int = 12) -> None:
        self._max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, *, status: str, direction: str, byte_count: int = 0) -> None:
        if status not in self._ALLOWED_STATUSES:
            raise ValueError("unapproved Relay status")
        if direction not in self._ALLOWED_DIRECTIONS:
            raise ValueError("unapproved Relay direction")
        bounded_count = min(max(int(byte_count), 0), MAX_APPLICATION_BYTES * 32)
        event = {
            "byte_count": bounded_count,
            "direction": direction,
            "status": status,
        }
        with self._lock:
            if len(self._events) < self._max_events:
                self._events.append(event)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events]


def bind_loopback_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(SOCKET_TIMEOUT_SECONDS)
    return listener


def receive_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise EOFError("stream closed before the bounded message completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_message(stream: socket.socket) -> bytes:
    size = struct.unpack("!I", receive_exact(stream, 4))[0]
    if size > MAX_APPLICATION_BYTES:
        raise ValueError("application message exceeded the smoke-test bound")
    return receive_exact(stream, size)


def send_message(stream: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_APPLICATION_BYTES:
        raise ValueError("application message exceeded the smoke-test bound")
    stream.sendall(struct.pack("!I", len(payload)) + payload)


def generate_certificate(openssl: str, directory: Path) -> tuple[Path, Path]:
    certificate_path = directory / "host-certificate.pem"
    private_key_path = directory / "host-private-key.pem"
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
            str(private_key_path),
            "-out",
            str(certificate_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    return certificate_path, private_key_path


def host_tls_endpoint(
    listener: socket.socket,
    certificate_path: Path,
    private_key_path: Path,
    expected_fingerprint: queue.Queue[str],
    result: queue.Queue[dict[str, Any]],
) -> None:
    receipt: dict[str, Any] = {
        "request": b"",
        "request_complete": False,
        "response_sent": False,
    }
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            certfile=str(certificate_path),
            keyfile=str(private_key_path),
        )
        certificate_der = ssl.PEM_cert_to_DER_cert(certificate_path.read_text(encoding="ascii"))
        expected_fingerprint.put(hashlib.sha256(certificate_der).hexdigest())

        raw_stream, _address = listener.accept()
        raw_stream.settimeout(SOCKET_TIMEOUT_SECONDS)
        with raw_stream:
            with context.wrap_socket(raw_stream, server_side=True) as tls_stream:
                receipt["request"] = receive_message(tls_stream)
                receipt["request_complete"] = True
                send_message(tls_stream, RESPONSE)
                receipt["response_sent"] = True
    except Exception as exc:  # pragma: no cover - surfaced through the receipt
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        if expected_fingerprint.empty():
            expected_fingerprint.put("")
    finally:
        listener.close()
        result.put(receipt)


def forward_opaque_bytes(
    source: socket.socket,
    destination: socket.socket,
    *,
    direction: str,
    evidence: RelayEvidence,
    errors: queue.Queue[str],
) -> None:
    forwarded = 0
    completed = False
    try:
        while True:
            chunk = source.recv(16 * 1024)
            if not chunk:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                evidence.record(status="half_closed", direction=direction, byte_count=forwarded)
                completed = True
                return
            destination.sendall(chunk)
            forwarded += len(chunk)
            if forwarded > MAX_APPLICATION_BYTES * 32:
                raise ValueError("Relay byte-count bound exceeded")
    except Exception as exc:  # pragma: no cover - surfaced through the queue
        errors.put(f"{direction}:{type(exc).__name__}")
        evidence.record(status="failed", direction=direction, byte_count=forwarded)
    finally:
        if completed:
            evidence.record(status="forwarded", direction=direction, byte_count=forwarded)


def opaque_relay(
    listener: socket.socket,
    host_address: tuple[str, int],
    evidence: RelayEvidence,
    result: queue.Queue[dict[str, Any]],
) -> None:
    receipt: dict[str, Any] = {}
    errors: queue.Queue[str] = queue.Queue()
    try:
        browser_stream, _address = listener.accept()
        browser_stream.settimeout(SOCKET_TIMEOUT_SECONDS)
        host_stream = socket.create_connection(host_address, timeout=SOCKET_TIMEOUT_SECONDS)
        host_stream.settimeout(SOCKET_TIMEOUT_SECONDS)
        evidence.record(status="accepted", direction="browser_to_host")
        evidence.record(status="accepted", direction="host_to_browser")
        with browser_stream, host_stream:
            threads = (
                threading.Thread(
                    target=forward_opaque_bytes,
                    args=(browser_stream, host_stream),
                    kwargs={
                        "direction": "browser_to_host",
                        "evidence": evidence,
                        "errors": errors,
                    },
                    daemon=True,
                ),
                threading.Thread(
                    target=forward_opaque_bytes,
                    args=(host_stream, browser_stream),
                    kwargs={
                        "direction": "host_to_browser",
                        "evidence": evidence,
                        "errors": errors,
                    },
                    daemon=True,
                ),
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(SOCKET_TIMEOUT_SECONDS)
            receipt["threads_completed"] = all(not thread.is_alive() for thread in threads)
            receipt["errors"] = list(errors.queue)
            evidence.record(status="closed", direction="browser_to_host")
            evidence.record(status="closed", direction="host_to_browser")
    except Exception as exc:  # pragma: no cover - surfaced through the receipt
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        listener.close()
        result.put(receipt)


def main() -> int:
    failures: list[str] = []
    openssl = shutil.which("openssl")
    if not openssl:
        print(
            json.dumps(
                {
                    "operation": "private_host_relay_tls_smoke",
                    "ok": False,
                    "error": "openssl_unavailable",
                    "deployed_relay_claimed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    evidence = RelayEvidence()
    host_result: queue.Queue[dict[str, Any]] = queue.Queue()
    relay_result: queue.Queue[dict[str, Any]] = queue.Queue()
    expected_fingerprint: queue.Queue[str] = queue.Queue()
    client_response = b""
    client_fingerprint = ""
    half_close_attempted = False

    with tempfile.TemporaryDirectory(prefix="agentops-relay-tls-") as temporary:
        temporary_path = Path(temporary)
        certificate_path, private_key_path = generate_certificate(openssl, temporary_path)
        host_listener = bind_loopback_listener()
        relay_listener = bind_loopback_listener()
        host_address = host_listener.getsockname()
        relay_address = relay_listener.getsockname()

        host_thread = threading.Thread(
            target=host_tls_endpoint,
            args=(
                host_listener,
                certificate_path,
                private_key_path,
                expected_fingerprint,
                host_result,
            ),
            daemon=True,
        )
        relay_thread = threading.Thread(
            target=opaque_relay,
            args=(relay_listener, host_address, evidence, relay_result),
            daemon=True,
        )
        host_thread.start()
        relay_thread.start()

        expected = expected_fingerprint.get(timeout=SOCKET_TIMEOUT_SECONDS)
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(relay_address, timeout=SOCKET_TIMEOUT_SECONDS) as raw_client:
            raw_client.settimeout(SOCKET_TIMEOUT_SECONDS)
            with client_context.wrap_socket(raw_client, server_hostname=HOSTNAME) as tls_client:
                peer_certificate = tls_client.getpeercert(binary_form=True)
                client_fingerprint = hashlib.sha256(peer_certificate).hexdigest()
                send_message(tls_client, REQUEST)
                client_response = receive_message(tls_client)

        host_thread.join(SOCKET_TIMEOUT_SECONDS)
        relay_thread.join(SOCKET_TIMEOUT_SECONDS)
        host_receipt = host_result.get(timeout=SOCKET_TIMEOUT_SECONDS)
        relay_receipt = relay_result.get(timeout=SOCKET_TIMEOUT_SECONDS)

        if host_thread.is_alive():
            failures.append("Host TLS endpoint did not stop")
        if relay_thread.is_alive():
            failures.append("Relay did not stop")
        if expected == "" or client_fingerprint != expected:
            failures.append("client certificate fingerprint did not match the Host certificate")
        if host_receipt.get("request") != REQUEST:
            failures.append("Host did not receive the exact binary request")
        if client_response != RESPONSE:
            failures.append("client did not receive the exact binary response")
        if not host_receipt.get("request_complete"):
            failures.append("Host did not complete the framed request")
        if not host_receipt.get("response_sent"):
            failures.append("Host did not send the response")
        if host_receipt.get("error"):
            failures.append(f"Host endpoint failed: {host_receipt['error']}")
        if relay_receipt.get("error"):
            failures.append(f"Relay failed: {relay_receipt['error']}")
        if relay_receipt.get("errors"):
            failures.append("Relay forwarding reported an error")
        if not relay_receipt.get("threads_completed"):
            failures.append("Relay forwarding threads did not complete")

        relay_evidence = evidence.snapshot()
        serialized_evidence = json.dumps(relay_evidence, ensure_ascii=True, sort_keys=True)
        prohibited = (
            REQUEST.decode("latin-1"),
            RESPONSE.decode("latin-1"),
            "private-host-relay-request",
            "private-host-relay-response",
            expected,
            client_fingerprint,
            str(certificate_path),
            str(private_key_path),
        )
        if any(marker and marker in serialized_evidence for marker in prohibited):
            failures.append("Relay evidence retained plaintext, a hash, key material, or a path")
        allowed_fields = {"byte_count", "direction", "status"}
        if len(relay_evidence) > 12:
            failures.append("Relay evidence exceeded its event bound")
        if any(set(event) != allowed_fields for event in relay_evidence):
            failures.append("Relay evidence exposed an unapproved field")
        if host_address[0] != "127.0.0.1" or relay_address[0] != "127.0.0.1":
            failures.append("a listener was not bound to literal IPv4 loopback")

        failure_evidence = RelayEvidence(max_events=4)
        failure_errors: queue.Queue[str] = queue.Queue()
        failure_source, idle_peer = socket.socketpair()
        failure_destination, destination_peer = socket.socketpair()
        try:
            failure_source.settimeout(0.01)
            forward_opaque_bytes(
                failure_source,
                failure_destination,
                direction="browser_to_host",
                evidence=failure_evidence,
                errors=failure_errors,
            )
        finally:
            failure_source.close()
            idle_peer.close()
            failure_destination.close()
            destination_peer.close()
        failure_statuses = [event.get("status") for event in failure_evidence.snapshot()]
        if failure_statuses != ["failed"] or failure_errors.empty():
            failures.append("failed forwarding produced contradictory Relay evidence")

    result = {
        "operation": "private_host_relay_tls_smoke",
        "ok": not failures,
        "binding": "temporary_127.0.0.1_listeners_only",
        "real_tls_handshake_verified": not failures,
        "certificate_fingerprint_verified_at_client": not failures,
        "exact_binary_request_verified": not failures,
        "exact_binary_response_verified": not failures,
        "half_close_verified": half_close_attempted and not failures,
        "half_close_status": "not_exercised_sslsocket_unilateral_shutdown_discards_tls_state",
        "relay_evidence": evidence.snapshot(),
        "relay_evidence_fields": ["byte_count", "direction", "status"],
        "relay_plaintext_or_hash_retained": False,
        "failed_forwarding_evidence_verified": not failures,
        "tls_material_location": "temporary_directory_only",
        "tls_terminated_at_relay": False,
        "database_used": False,
        "deployed_relay_claimed": False,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
