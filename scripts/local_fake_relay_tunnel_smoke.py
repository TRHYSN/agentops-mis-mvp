#!/usr/bin/env python3
"""Compose a real TLS Host endpoint through the local fake outbound Relay."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import select
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

from agentops_mis_cli.relay_tunnel import (  # noqa: E402
    BoundedRelayMetadata,
    HostTunnelConnector,
    LocalFakeRelay,
    MAX_CONNECTOR_HANDSHAKES,
    RelayFrame,
    _forward_bidirectional,
    encode_frame,
    receive_frame,
)


HOSTNAME = "agentops-relay.test"
ROUTE = "agentops-local-test"
TIMEOUT = 8.0
REQUESTS = (
    b"\x00\xff\x80first-request\r\n" + bytes(range(256)) + b"\xfe\x00",
    b"\x81\x00second-request\r\n" + bytes(reversed(range(256))) + b"\xfd\x00",
)
RESPONSES = (
    b"\xff\x00first-response\r\n" + bytes(reversed(range(256))) + b"\x80",
    b"\x00\x81second-response\r\n" + bytes(range(256)) + b"\xfe",
)


def bind_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    return listener


def read_exact(stream: socket.socket, size: int) -> bytes:
    parts: list[bytes] = []
    while size:
        part = stream.recv(size)
        if not part:
            raise EOFError("bounded message ended early")
        parts.append(part)
        size -= len(part)
    return b"".join(parts)


def receive_message(stream: socket.socket) -> bytes:
    size = struct.unpack("!I", read_exact(stream, 4))[0]
    if size > 128 * 1024:
        raise ValueError("message exceeds smoke bound")
    return read_exact(stream, size)


def send_message(stream: socket.socket, payload: bytes) -> None:
    if len(payload) > 128 * 1024:
        raise ValueError("message exceeds smoke bound")
    stream.sendall(struct.pack("!I", len(payload)) + payload)


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
    receipt: dict[str, Any] = {"requests": [], "connections": 0}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    listener.settimeout(TIMEOUT)
    try:
        for response in RESPONSES:
            raw, _ = listener.accept()
            receipt["connections"] += 1
            raw.settimeout(TIMEOUT)
            with raw:
                with context.wrap_socket(raw, server_side=True) as tls:
                    request = receive_message(tls)
                    receipt["requests"].append(request)
                    send_message(tls, response)
    except Exception as exc:  # surfaced through bounded type/code only
        receipt["error"] = type(exc).__name__
    finally:
        listener.close()
        result.put(receipt)


def expect_closed(stream: socket.socket) -> bool:
    stream.settimeout(2.0)
    try:
        return stream.recv(1) == b""
    except (ConnectionResetError, BrokenPipeError):
        return True
    except socket.timeout:
        return False


def registration_probe(
    address: tuple[str, int],
    wire: bytes,
    *,
    key: bytes | None = None,
) -> tuple[bool, bytes]:
    with socket.create_connection(address, timeout=TIMEOUT) as stream:
        stream.sendall(wire)
        if key is None:
            return expect_closed(stream), b""
        try:
            registered = receive_frame(stream, key)
            return registered.kind == "registered", wire
        except Exception:
            return False, b""


def register_control(
    address: tuple[str, int],
    key: bytes,
    epoch: int,
) -> socket.socket:
    control = socket.create_connection(address, timeout=TIMEOUT)
    control.settimeout(TIMEOUT)
    send = encode_frame(RelayFrame("register", ROUTE, epoch, 1), key)
    control.sendall(send)
    registered = receive_frame(control, key)
    if registered != RelayFrame("registered", ROUTE, epoch, 1):
        control.close()
        raise RuntimeError("control registration failed")
    return control


def forwarding_failure_probe() -> bool:
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    metadata = BoundedRelayMetadata()
    right.close()
    right_peer.close()
    try:
        left_peer.sendall(b"partial-send-failure-probe")
        forwarded = _forward_bidirectional(left, right, metadata)
    finally:
        left_peer.close()
    events = metadata.snapshot()
    return not forwarded and any(event["status"] == "failed" for event in events) and not any(
        event["status"] == "forwarded" for event in events
    )


def handshake_bound_and_stop_probe(key: bytes) -> tuple[bool, bool]:
    browser_listener = bind_listener()
    connector_listener = bind_listener()
    relay = LocalFakeRelay(
        browser_listener=browser_listener,
        connector_listener=connector_listener,
        route=ROUTE,
        key=key,
    )
    relay.start()
    clients = [
        socket.create_connection(connector_listener.getsockname(), timeout=TIMEOUT)
        for _ in range(MAX_CONNECTOR_HANDSHAKES)
    ]
    time.sleep(0.25)
    overflow = socket.create_connection(connector_listener.getsockname(), timeout=TIMEOUT)
    overflow_failed_closed = expect_closed(overflow)
    overflow.close()
    started = time.monotonic()
    relay.stop()
    stop_bounded = time.monotonic() - started < 3.0
    readable, _, _ = select.select(clients, [], [], 2.0)
    all_handshakes_closed = len(readable) == len(clients)
    for client in clients:
        if client in readable:
            try:
                all_handshakes_closed = all_handshakes_closed and client.recv(1) == b""
            except (ConnectionResetError, OSError):
                pass
        client.close()
    return overflow_failed_closed, stop_bounded and all_handshakes_closed


def active_stream_stop_probe(key: bytes) -> bool:
    browser_listener = bind_listener()
    connector_listener = bind_listener()
    relay = LocalFakeRelay(
        browser_listener=browser_listener,
        connector_listener=connector_listener,
        route=ROUTE,
        key=key,
    )
    relay.start()
    control = register_control(connector_listener.getsockname(), key, 1)
    browser = socket.create_connection(browser_listener.getsockname(), timeout=TIMEOUT)
    opened = receive_frame(control, key)
    data = socket.create_connection(connector_listener.getsockname(), timeout=TIMEOUT)
    data.sendall(
        encode_frame(
            RelayFrame(
                "data",
                ROUTE,
                1,
                1,
                opened.connection_id,
                opened.nonce,
            ),
            key,
        )
    )
    acknowledged = receive_frame(data, key) == RelayFrame(
        "data_ready",
        ROUTE,
        1,
        1,
        opened.connection_id,
        opened.nonce,
    )
    browser.sendall(b"active")
    forwarded = read_exact(data, 6) == b"active"
    started = time.monotonic()
    relay.stop()
    stop_bounded = time.monotonic() - started < 3.0
    streams = [browser, data, control]
    readable, _, _ = select.select(streams, [], [], 2.0)
    all_closed = len(readable) == len(streams)
    for stream in streams:
        if stream in readable:
            try:
                all_closed = all_closed and stream.recv(1) == b""
            except (ConnectionResetError, OSError):
                pass
        stream.close()
    return acknowledged and forwarded and stop_bounded and all_closed


def tls_round_trip(
    relay_address: tuple[str, int],
    request: bytes,
) -> tuple[bytes, bytes]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(relay_address, timeout=TIMEOUT) as raw:
        raw.settimeout(TIMEOUT)
        with context.wrap_socket(raw, server_hostname=HOSTNAME) as tls:
            peer = tls.getpeercert(binary_form=True)
            send_message(tls, request)
            response = receive_message(tls)
            return response, peer


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    host_result: queue.Queue[dict[str, Any]] = queue.Queue()
    connectors: list[HostTunnelConnector] = []
    relay: LocalFakeRelay | None = None

    with tempfile.TemporaryDirectory(prefix="agentops-local-relay-") as temporary:
        temporary_path = Path(temporary)
        key_path = temporary_path / "tunnel.key"
        key_path.write_bytes(os.urandom(32))
        key_path.chmod(0o600)
        key = key_path.read_bytes()
        certificate, private_key = generate_certificate(openssl, temporary_path)

        unbound_browser = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unbound_connector = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener_constructor_failed_closed = False
        try:
            LocalFakeRelay(
                browser_listener=unbound_browser,
                connector_listener=unbound_connector,
                route=ROUTE,
                key=key,
            )
        except ValueError:
            listener_constructor_failed_closed = True
        finally:
            unbound_browser.close()
            unbound_connector.close()
        if not listener_constructor_failed_closed:
            failures.append("LocalFakeRelay accepted a non-loopback/unbound listener")

        forwarding_failure_truthful = forwarding_failure_probe()
        if not forwarding_failure_truthful:
            failures.append("forwarding failure was recorded as successful forwarding")
        handshake_bound_enforced, handshake_stop_bounded = handshake_bound_and_stop_probe(key)
        if not handshake_bound_enforced:
            failures.append("unauthenticated connector handshake bound was not enforced")
        if not handshake_stop_bounded:
            failures.append("Relay stop left unauthenticated connector handshakes alive")
        active_stream_stop_bounded = active_stream_stop_probe(key)
        if not active_stream_stop_bounded:
            failures.append("Relay stop left an active forwarding stream alive")

        host_listener = bind_listener()
        browser_listener = bind_listener()
        connector_listener = bind_listener()
        listener_addresses = (
            host_listener.getsockname(),
            browser_listener.getsockname(),
            connector_listener.getsockname(),
        )
        if any(address[0] != "127.0.0.1" for address in listener_addresses):
            failures.append("a listener was not literal IPv4 loopback")

        expected_der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
        expected_fingerprint = hashlib.sha256(expected_der).digest()
        host_thread = threading.Thread(
            target=host_tls_server,
            args=(host_listener, certificate, private_key, host_result),
            daemon=True,
        )
        host_thread.start()

        relay = LocalFakeRelay(
            browser_listener=browser_listener,
            connector_listener=connector_listener,
            route=ROUTE,
            key=key,
        )
        relay.start()
        connector_address = connector_listener.getsockname()
        browser_address = browser_listener.getsockname()

        unknown = RelayFrame("register", "unknown-route", 1, 1)
        unknown_route_closed, _ = registration_probe(
            connector_address, encode_frame(unknown, key)
        )
        bad_mac_wire = encode_frame(RelayFrame("register", ROUTE, 1, 1), os.urandom(32))
        bad_mac_closed, _ = registration_probe(connector_address, bad_mac_wire)

        epoch_one_wire = encode_frame(RelayFrame("register", ROUTE, 1, 1), key)
        first_registration_ok, replay_wire = registration_probe(
            connector_address, epoch_one_wire, key=key
        )
        deadline = time.monotonic() + TIMEOUT
        while relay.has_control() and time.monotonic() < deadline:
            time.sleep(0.01)
        replay_closed, _ = registration_probe(connector_address, replay_wire)

        observed_stream_refs: list[bytes] = []
        stale_control = register_control(connector_address, key, 2)
        stale_browser = socket.create_connection(browser_address, timeout=TIMEOUT)
        stale_open = receive_frame(stale_control, key)
        observed_stream_refs.extend(
            [stale_open.connection_id.encode("ascii"), stale_open.nonce.encode("ascii")]
        )
        replacement_control = register_control(connector_address, key, 3)
        stale_browser_closed = expect_closed(stale_browser)
        stale_claim = socket.create_connection(connector_address, timeout=TIMEOUT)
        stale_claim.sendall(
            encode_frame(
                RelayFrame(
                    "data",
                    ROUTE,
                    3,
                    1,
                    stale_open.connection_id,
                    stale_open.nonce,
                ),
                key,
            )
        )
        stale_claim_closed = expect_closed(stale_claim)
        stale_browser.close()
        stale_claim.close()
        replacement_control.close()
        stale_control.close()
        if not relay.wait_for_no_control(TIMEOUT):
            failures.append("replacement control connection did not disconnect")
        stale_pending_epoch_failed_closed = stale_browser_closed and stale_claim_closed
        if not stale_pending_epoch_failed_closed:
            failures.append("new epoch data connection claimed an old pending browser")

        replay_control = register_control(connector_address, key, 4)
        replay_browser = socket.create_connection(browser_address, timeout=TIMEOUT)
        replay_open = receive_frame(replay_control, key)
        observed_stream_refs.extend(
            [replay_open.connection_id.encode("ascii"), replay_open.nonce.encode("ascii")]
        )
        data_wire = encode_frame(
            RelayFrame(
                "data",
                ROUTE,
                4,
                1,
                replay_open.connection_id,
                replay_open.nonce,
            ),
            key,
        )
        first_data = socket.create_connection(connector_address, timeout=TIMEOUT)
        first_data.sendall(data_wire)
        first_data_ready = receive_frame(first_data, key)
        first_data_acknowledged = first_data_ready == RelayFrame(
            "data_ready",
            ROUTE,
            4,
            1,
            replay_open.connection_id,
            replay_open.nonce,
        )
        replay_browser.sendall(b"b")
        first_data.settimeout(TIMEOUT)
        first_data_forwarded = first_data.recv(1) == b"b"
        replayed_data = socket.create_connection(connector_address, timeout=TIMEOUT)
        replayed_data.sendall(data_wire)
        replayed_data_closed = expect_closed(replayed_data)
        replayed_data.close()
        replay_browser.close()
        first_data.close()
        replay_control.close()
        if not relay.wait_for_no_control(TIMEOUT):
            failures.append("data replay control connection did not disconnect")
        data_replay_failed_closed = (
            first_data_acknowledged and first_data_forwarded and replayed_data_closed
        )
        if not data_replay_failed_closed:
            failures.append("authenticated data connection replay did not fail closed")

        connector_one = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=host_listener.getsockname(),
            route=ROUTE,
            epoch=5,
            key=key,
        )
        connectors.append(connector_one)
        connector_one.start()
        if not connector_one.wait_until_ready(TIMEOUT) or not relay.wait_for_control(TIMEOUT):
            failures.append("first Host control connection did not register")
        time.sleep(0.75)
        if not relay.has_control() or connector_one.error:
            failures.append("idle Host control connection did not remain available")
        response_one, peer_one = tls_round_trip(browser_address, REQUESTS[0])

        connector_one.stop()
        connector_one_stopped_not_ready = not connector_one.wait_until_ready(0.1)
        if not relay.wait_for_no_control(TIMEOUT):
            failures.append("first Host control connection did not disconnect")

        connector_two = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=host_listener.getsockname(),
            route=ROUTE,
            epoch=6,
            key=key,
        )
        connectors.append(connector_two)
        connector_two.start()
        if not connector_two.wait_until_ready(TIMEOUT) or not relay.wait_for_control(TIMEOUT):
            failures.append("second Host control connection did not register")
        response_two, peer_two = tls_round_trip(browser_address, REQUESTS[1])

        connector_two.stop()
        connector_two_stopped_not_ready = not connector_two.wait_until_ready(0.1)
        relay.stop()
        host_thread.join(TIMEOUT)
        host_receipt = host_result.get(timeout=TIMEOUT)

        fingerprint_ok = all(
            hmac_digest == expected_fingerprint
            for hmac_digest in (hashlib.sha256(peer_one).digest(), hashlib.sha256(peer_two).digest())
        )
        if response_one != RESPONSES[0] or response_two != RESPONSES[1]:
            failures.append("TLS client did not receive exact binary responses")
        if host_receipt.get("requests") != list(REQUESTS):
            failures.append("Host did not receive exact binary requests")
        if host_receipt.get("connections") != 2:
            failures.append("Host TLS endpoint did not receive exactly two accepted browser sessions")
        if host_receipt.get("error"):
            failures.append("Host TLS endpoint reported an error")
        if not fingerprint_ok:
            failures.append("client certificate fingerprint did not match Host certificate")
        if not (connector_one_stopped_not_ready and connector_two_stopped_not_ready):
            failures.append("stopped Host connector still reported ready")
        if not unknown_route_closed:
            failures.append("unknown route did not fail closed")
        if not bad_mac_closed:
            failures.append("bad MAC did not fail closed")
        if not first_registration_ok or not replay_closed:
            failures.append("registration replay did not fail closed")

        relay_metadata = relay.metadata.snapshot()
        connector_metadata = [event for item in connectors for event in item.metadata.snapshot()]
        connector_controls_registered = all(
            any(
                event["status"] == "registered" and event["direction"] == "control"
                for event in item.metadata.snapshot()
            )
            for item in connectors
        )
        connector_data_authenticated = sum(
            1
            for event in connector_metadata
            if event["status"] == "authenticated" and event["direction"] == "data"
        ) == len(RESPONSES)
        relay_state = vars(relay)
        tls_context_or_key_path_present = any(
            isinstance(value, ssl.SSLContext)
            or (
                isinstance(value, (str, Path))
                and str(value) in {str(certificate), str(private_key)}
            )
            for value in relay_state.values()
        )
        serialized_metadata = json.dumps(
            {"connector": connector_metadata, "relay": relay_metadata},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        forbidden = [
            *REQUESTS,
            *RESPONSES,
            key,
            str(temporary_path).encode("utf-8"),
            str(certificate).encode("utf-8"),
            str(private_key).encode("utf-8"),
            *observed_stream_refs,
        ]
        metadata_bounded = len(serialized_metadata) < 16 * 1024
        metadata_clean = metadata_bounded and all(value not in serialized_metadata for value in forbidden)
        key_file_private = key_path.stat().st_mode & 0o777 == 0o600
        if not metadata_clean:
            failures.append("Relay metadata retained forbidden or unbounded material")
        if not key_file_private:
            failures.append("temporary tunnel key file was not mode 0600")

        result = {
            "bad_mac_failed_closed": bad_mac_closed,
            "active_stream_stop_bounded": active_stream_stop_bounded,
            "client_certificate_fingerprint_verified": fingerprint_ok,
            "control_connections_host_initiated": connector_controls_registered,
            "connector_shutdown_not_ready": (
                connector_one_stopped_not_ready and connector_two_stopped_not_ready
            ),
            "data_connection_per_browser_host_initiated": connector_data_authenticated,
            "database_used": False,
            "disconnect_new_epoch_verified": host_receipt.get("connections") == 2,
            "data_connection_replay_failed_closed": data_replay_failed_closed,
            "exact_binary_round_trip_verified": (
                host_receipt.get("requests") == list(REQUESTS)
                and response_one == RESPONSES[0]
                and response_two == RESPONSES[1]
            ),
            "exactly_once_transport_claimed": False,
            "half_close_verified": False,
            "handshake_concurrency_bound_enforced": handshake_bound_enforced,
            "handshake_stop_bounded": handshake_stop_bounded,
            "host_endpoint_process_reused": host_receipt.get("connections") == 2,
            "idle_control_connection_verified": not connector_one.error,
            "listeners_literal_loopback_ephemeral": all(
                address[0] == "127.0.0.1" and address[1] > 0 for address in listener_addresses
            ),
            "listener_constructor_enforces_loopback": listener_constructor_failed_closed,
            "ok": not failures,
            "operation": "local_fake_relay_tunnel_smoke",
            "relay_metadata_bounded_and_payload_free": metadata_clean,
            "relay_instance_tls_context_or_key_path_observed": tls_context_or_key_path_present,
            "tls_handshake_endpoint_verified_as_host": fingerprint_ok,
            "forwarding_failure_not_recorded_as_success": forwarding_failure_truthful,
            "registration_replay_failed_closed": first_registration_ok and replay_closed,
            "stale_pending_epoch_failed_closed": stale_pending_epoch_failed_closed,
            "temporary_hmac_key_file_mode_0600": key_file_private,
            "unknown_route_failed_closed": unknown_route_closed,
        }
        if failures:
            result["failures"] = failures
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
