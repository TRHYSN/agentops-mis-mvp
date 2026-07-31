#!/usr/bin/env python3
"""Prove authenticated Relay TLS around Host-terminated application TLS."""
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
import warnings
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_connector_supervisor import RelayConnectorSupervisor  # noqa: E402
from agentops_mis_cli.relay_epoch_store import PersistentRelayEpochStore  # noqa: E402
from agentops_mis_cli.relay_tunnel import (  # noqa: E402
    HostTunnelConnector,
    LocalFakeRelay,
    RelayProtocolError,
    receive_frame,
)


RELAY_HOSTNAME = "relay.agentops.test"
HOST_HOSTNAME = "host.agentops.test"
ROUTE = "tls-authenticated-tunnel"
TIMEOUT = 8.0
REQUEST = b"\x00relay-outer-tls-host-inner-tls-request\xff"
RESPONSE = b"\xffrelay-outer-tls-host-inner-tls-response\x00"


def bind_listener(port: int = 0) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(16)
    return listener


class TlsServerListener:
    """Wrap accepted connector sockets without changing fake Relay logic."""

    def __init__(self, listener: socket.socket, context: ssl.SSLContext) -> None:
        self._listener = listener
        self._context = context
        self._accepted_versions: list[str] = []
        self._lock = threading.Lock()

    @property
    def family(self) -> socket.AddressFamily:
        return self._listener.family

    def getsockname(self) -> tuple[str, int]:
        return self._listener.getsockname()

    def settimeout(self, value: float) -> None:
        self._listener.settimeout(value)

    def accept(self) -> tuple[socket.socket, tuple[str, int]]:
        while True:
            raw, address = self._listener.accept()
            try:
                raw.settimeout(TIMEOUT)
                tls = self._context.wrap_socket(raw, server_side=True)
            except ssl.SSLError:
                raw.close()
                continue
            with self._lock:
                self._accepted_versions.append(tls.version() or "unknown")
            return tls, address

    def shutdown(self, how: int) -> None:
        self._listener.shutdown(how)

    def close(self) -> None:
        self._listener.close()

    def accepted_versions(self) -> list[str]:
        with self._lock:
            return list(self._accepted_versions)


def generate_certificate(
    openssl: str,
    directory: Path,
    *,
    prefix: str,
    hostname: str,
) -> tuple[Path, Path]:
    certificate = directory / f"{prefix}-cert.pem"
    private_key = directory / f"{prefix}-key.pem"
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
            f"/CN={hostname}",
            "-addext",
            f"subjectAltName=DNS:{hostname}",
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


def host_tls_server(
    listener: socket.socket,
    context: ssl.SSLContext,
    result: queue.Queue[dict[str, Any]],
) -> None:
    receipt: dict[str, Any] = {"request_matches": False}
    listener.settimeout(TIMEOUT)
    try:
        raw, _ = listener.accept()
        raw.settimeout(TIMEOUT)
        with raw:
            with context.wrap_socket(raw, server_side=True) as tls:
                receipt["tls_version"] = tls.version()
                receipt["request_matches"] = receive_message(tls) == REQUEST
                send_message(tls, RESPONSE)
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()
        result.put(receipt)


def browser_round_trip(
    address: tuple[str, int],
    context: ssl.SSLContext,
) -> tuple[bytes, bytes, str | None]:
    with socket.create_connection(address, timeout=TIMEOUT) as raw:
        raw.settimeout(TIMEOUT)
        with context.wrap_socket(raw, server_hostname=HOST_HOSTNAME) as tls:
            peer = tls.getpeercert(binary_form=True)
            send_message(tls, REQUEST)
            return receive_message(tls), peer, tls.version()


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    relay: LocalFakeRelay | None = None
    supervisor: RelayConnectorSupervisor | None = None
    host_thread: threading.Thread | None = None

    with tempfile.TemporaryDirectory(prefix="agentops-relay-auth-tls-") as temporary:
        temporary_path = Path(temporary)
        relay_certificate, relay_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="relay",
            hostname=RELAY_HOSTNAME,
        )
        host_certificate, host_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="host",
            hostname=HOST_HOSTNAME,
        )

        relay_server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        relay_server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        relay_server_context.load_cert_chain(str(relay_certificate), str(relay_key))
        relay_client_context = ssl.create_default_context(cafile=str(relay_certificate))
        relay_client_context.minimum_version = ssl.TLSVersion.TLSv1_2

        host_server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        host_server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        host_server_context.load_cert_chain(str(host_certificate), str(host_key))
        browser_context = ssl.create_default_context(cafile=str(host_certificate))
        browser_context.minimum_version = ssl.TLSVersion.TLSv1_2

        insecure_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure_context.check_hostname = False
        insecure_context.verify_mode = ssl.CERT_NONE
        insecure_rejected = False
        try:
            HostTunnelConnector(
                relay_address=("relay.invalid", 443),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                epoch=1,
                key=os.urandom(32),
                relay_ssl_context=insecure_context,
                relay_server_hostname=RELAY_HOSTNAME,
            )
        except ValueError:
            insecure_rejected = True
        if not insecure_rejected:
            failures.append("non-verifying Relay TLS context was accepted")

        legacy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            legacy_context.minimum_version = ssl.TLSVersion.TLSv1_1
        legacy_context_rejected = False
        try:
            HostTunnelConnector(
                relay_address=("relay.invalid", 443),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                epoch=1,
                key=os.urandom(32),
                relay_ssl_context=legacy_context,
                relay_server_hostname=RELAY_HOSTNAME,
            )
        except ValueError:
            legacy_context_rejected = True
        if not legacy_context_rejected:
            failures.append("Relay TLS context below TLS 1.2 was accepted")

        invalid_hostname_rejected = False
        try:
            HostTunnelConnector(
                relay_address=("relay.invalid", 443),
                host_tls_target=("127.0.0.1", 9),
                route=ROUTE,
                epoch=1,
                key=os.urandom(32),
                relay_ssl_context=relay_client_context,
                relay_server_hostname="invalid/hostname",
            )
        except ValueError:
            invalid_hostname_rejected = True
        if not invalid_hostname_rejected:
            failures.append("invalid Relay TLS hostname was accepted")

        browser_listener = bind_listener()
        connector_socket = bind_listener()
        connector_listener = TlsServerListener(connector_socket, relay_server_context)
        host_listener = bind_listener()
        browser_address = browser_listener.getsockname()
        connector_address = connector_listener.getsockname()
        host_address = host_listener.getsockname()
        tunnel_key = os.urandom(32)

        partial_sender, partial_receiver = socket.socketpair()
        try:
            partial_sender.sendall(b"\x00\x00")
            partial_timeout_rejected = False
            try:
                receive_frame(partial_receiver, tunnel_key, timeout_seconds=0.1)
            except RelayProtocolError as exc:
                partial_timeout_rejected = exc.code == "partial_read_timeout"
            if not partial_timeout_rejected:
                failures.append("partial frame timeout was treated as idle")
        finally:
            partial_sender.close()
            partial_receiver.close()

        idle_sender, idle_receiver = socket.socketpair()
        try:
            idle_timeout_detected = False
            try:
                receive_frame(idle_receiver, tunnel_key, timeout_seconds=0.1)
            except RelayProtocolError as exc:
                idle_timeout_detected = exc.code == "read_timeout"
            if not idle_timeout_detected:
                failures.append("idle frame timeout was not distinguishable")
        finally:
            idle_sender.close()
            idle_receiver.close()

        relay = LocalFakeRelay(
            browser_listener=browser_listener,
            connector_listener=connector_listener,
            route=ROUTE,
            key=tunnel_key,
        )
        relay.start()

        wrong_trust_context = ssl.create_default_context()
        wrong_trust = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=host_address,
            route=ROUTE,
            epoch=1,
            key=tunnel_key,
            relay_ssl_context=wrong_trust_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        wrong_trust.start()
        wrong_trust_ready = wrong_trust.wait_until_ready(2.0)
        wrong_trust_error = wrong_trust.error
        wrong_trust.stop()
        if wrong_trust_ready or wrong_trust_error != "relay_tls_failed":
            failures.append("untrusted Relay certificate did not fail before registration")

        wrong_key = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=host_address,
            route=ROUTE,
            epoch=1,
            key=os.urandom(32),
            relay_ssl_context=relay_client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        wrong_key.start()
        wrong_key_ready = wrong_key.wait_until_ready(2.0)
        wrong_key.stop()
        if wrong_key_ready:
            failures.append("Relay TLS bypassed Host HMAC authentication")

        host_result: queue.Queue[dict[str, Any]] = queue.Queue()
        host_thread = threading.Thread(
            target=host_tls_server,
            args=(host_listener, host_server_context, host_result),
            daemon=True,
        )
        host_thread.start()

        epoch_store = PersistentRelayEpochStore(
            temporary_path / "state" / "epoch.json",
            connector_identity=hashlib.sha256(ROUTE.encode("ascii") + tunnel_key).digest(),
        )
        supervisor = RelayConnectorSupervisor(
            relay_address=connector_address,
            host_tls_target=host_address,
            route=ROUTE,
            key=tunnel_key,
            enabled=True,
            epoch_allocator=epoch_store,
            relay_ssl_context=relay_client_context,
            relay_server_hostname=RELAY_HOSTNAME,
            connect_timeout_seconds=1.0,
        )
        if not supervisor.start() or not supervisor.wait_for_connections(1, TIMEOUT):
            failures.append("authenticated Relay TLS supervisor did not connect")

        response = b""
        host_peer = b""
        browser_tls_version: str | None = None
        try:
            response, host_peer, browser_tls_version = browser_round_trip(
                browser_address,
                browser_context,
            )
        except Exception as exc:
            failures.append(f"nested TLS round trip failed with {type(exc).__name__}")
        if response != RESPONSE:
            failures.append("nested TLS response mismatch")

        expected_host_der = ssl.PEM_cert_to_DER_cert(host_certificate.read_text(encoding="ascii"))
        if hashlib.sha256(host_peer).digest() != hashlib.sha256(expected_host_der).digest():
            failures.append("browser did not verify the Host application certificate")

        host_thread.join(TIMEOUT)
        if host_thread.is_alive():
            failures.append("Host application TLS server did not stop")
        else:
            receipt = host_result.get_nowait()
            if receipt.get("error_type") or not receipt.get("request_matches"):
                failures.append("Host application TLS server missed the exact request")

        status = supervisor.status()
        if status.get("relay_tls_enabled") is not True:
            failures.append("supervisor did not report authenticated Relay TLS")
        if status.get("limitations", {}).get("deployed_relay") is not False:
            failures.append("loopback TLS fixture overstated deployed Relay readiness")
        if len(connector_listener.accepted_versions()) < 3:
            failures.append("control/data Relay TLS handshakes were not all observed")
        if any(version not in {"TLSv1.2", "TLSv1.3"} for version in connector_listener.accepted_versions()):
            failures.append("Relay accepted an unsupported TLS version")
        if browser_tls_version not in {"TLSv1.2", "TLSv1.3"}:
            failures.append("browser-to-Host application TLS version was unsupported")

        relay_events = relay.metadata.snapshot()
        if not any(
            event.get("status") == "rejected" and event.get("direction") == "control"
            for event in relay_events
        ):
            failures.append("wrong Host HMAC did not produce bounded Relay rejection")
        rendered = json.dumps(
            {"relay": relay_events, "supervisor": status},
            sort_keys=True,
        )
        forbidden = (
            RELAY_HOSTNAME,
            HOST_HOSTNAME,
            tunnel_key.hex(),
            hashlib.sha256(tunnel_key).hexdigest(),
            str(temporary_path),
            REQUEST.hex(),
            RESPONSE.hex(),
            str(connector_address[1]),
            str(host_address[1]),
        )
        if any(value in rendered for value in forbidden):
            failures.append("Relay status exposed identity, payload, key, path, or port")

        supervisor.stop()
        supervisor = None
        relay.stop()
        relay = None

    if supervisor is not None:
        supervisor.stop()
    if relay is not None:
        relay.stop()

    result = {
        "application_tls_terminated_at_host": True,
        "browser_verified_host_certificate": not any("Host application certificate" in item for item in failures),
        "deployed_relay": False,
        "failures": failures,
        "host_hmac_authenticated": not any("Host HMAC" in item for item in failures),
        "nested_tls_round_trip": not any("nested TLS" in item for item in failures),
        "ok": not failures,
        "operation": "relay_tls_authenticated_tunnel_smoke",
        "relay_certificate_verified": not any("Relay certificate" in item for item in failures),
        "relay_plaintext_retained": False,
        "relay_tls_enabled": True,
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
