#!/usr/bin/env python3
"""Loopback-only acceptance for the bounded Relay SNI router slice."""
from __future__ import annotations

import json
import socket
import ssl
import struct
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_sni_router import (  # noqa: E402
    MAX_CLIENT_HELLO_BYTES,
    BoundedSniRouter,
    SniRoutingError,
)


ALPHA_HOST = "alpha.console.agentops.test"
BETA_HOST = "beta.console.agentops.test"
UNKNOWN_HOST = "unknown.console.agentops.test"
ALPHA_ROUTE = "rte_alpha_01"
BETA_ROUTE = "rte_beta_01"
OPAQUE_ALPHA = b"opaque-application-alpha"
OPAQUE_BETA = b"opaque-application-beta"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def loopback_tcp_pair() -> tuple[socket.socket, socket.socket]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=2)
    server, address = listener.accept()
    listener.close()
    if address[0] != "127.0.0.1":
        client.close()
        server.close()
        raise RuntimeError("non_loopback_peer")
    client.settimeout(2)
    server.settimeout(2)
    return client, server


def real_client_hello(server_hostname: str | None) -> bytes:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    tls = context.wrap_bio(
        incoming,
        outgoing,
        server_side=False,
        server_hostname=server_hostname,
    )
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    payload = outgoing.read()
    if not payload or payload[0] != 22:
        raise RuntimeError("real_client_hello_unavailable")
    return payload


def route_wire(
    router: BoundedSniRouter,
    wire: bytes,
    *,
    trailing: bytes = b"",
) -> tuple[str, bytes, bytes]:
    client, relay = loopback_tcp_pair()
    try:
        client.sendall(wire + trailing)
        selection = router.route_connection(relay)
        remainder = b""
        if trailing:
            remainder = relay.recv(len(trailing))
        return selection.route_ref, selection.preface, remainder
    finally:
        client.close()
        relay.close()


def rejected_code(router: BoundedSniRouter, wire: bytes) -> str | None:
    client, relay = loopback_tcp_pair()
    try:
        client.sendall(wire)
        try:
            router.route_connection(relay)
        except SniRoutingError as exc:
            return exc.code
        return None
    finally:
        client.close()
        relay.close()


class SignallingSocket:
    """Socket adapter used only to make the capacity fixture deterministic."""

    def __init__(self, stream: socket.socket, receive_started: threading.Event) -> None:
        self._stream = stream
        self._receive_started = receive_started

    def gettimeout(self) -> float | None:
        return self._stream.gettimeout()

    def settimeout(self, value: float | None) -> None:
        self._stream.settimeout(value)

    def recv(self, size: int) -> bytes:
        self._receive_started.set()
        return self._stream.recv(size)


def main() -> int:
    failures: list[str] = []
    router = BoundedSniRouter(
        {
            "ALPHA.CONSOLE.AGENTOPS.TEST.": ALPHA_ROUTE,
            BETA_HOST: BETA_ROUTE,
        }
    )
    alpha_hello = real_client_hello(ALPHA_HOST)
    beta_hello = real_client_hello(BETA_HOST)

    alpha_route, alpha_preface, alpha_remainder = route_wire(
        router,
        alpha_hello,
        trailing=OPAQUE_ALPHA,
    )
    beta_route, beta_preface, beta_remainder = route_wire(
        router,
        beta_hello,
        trailing=OPAQUE_BETA,
    )
    require(alpha_route == ALPHA_ROUTE, "alpha SNI crossed into another Host route", failures)
    require(beta_route == BETA_ROUTE, "beta SNI crossed into another Host route", failures)
    require(alpha_route != beta_route, "two Host routes were not isolated", failures)
    require(alpha_preface == alpha_hello, "alpha ClientHello was not preserved exactly", failures)
    require(beta_preface == beta_hello, "beta ClientHello was not preserved exactly", failures)
    require(alpha_remainder == OPAQUE_ALPHA, "alpha application payload was consumed", failures)
    require(beta_remainder == OPAQUE_BETA, "beta application payload was consumed", failures)
    require(OPAQUE_ALPHA not in alpha_preface, "alpha application payload entered router preface", failures)
    require(OPAQUE_BETA not in beta_preface, "beta application payload entered router preface", failures)

    repeat_route, repeat_preface, _ = route_wire(router, alpha_hello)
    require(
        repeat_route == ALPHA_ROUTE and repeat_preface == alpha_hello,
        "stateless repeated inspection changed route or bytes",
        failures,
    )

    unknown_code = rejected_code(router, real_client_hello(UNKNOWN_HOST))
    malformed_code = rejected_code(
        router,
        b"\x16\x03\x01\x00\x05\x01\x00\x00\x01\x00",
    )
    no_sni_code = rejected_code(router, real_client_hello(None))
    oversized_header = struct.pack("!BHH", 22, 0x0301, MAX_CLIENT_HELLO_BYTES)
    oversized_code = rejected_code(router, oversized_header)
    require(unknown_code == "route_not_found", "unknown SNI did not fail closed", failures)
    require(malformed_code == "malformed_client_hello", "malformed ClientHello was accepted", failures)
    require(no_sni_code == "sni_required", "ClientHello without SNI was accepted", failures)
    require(oversized_code == "client_hello_too_large", "oversized ClientHello was accepted", failures)

    timeout_router = BoundedSniRouter({ALPHA_HOST: ALPHA_ROUTE}, timeout_seconds=0.1)
    timeout_client, timeout_relay = loopback_tcp_pair()
    timeout_started = time.monotonic()
    try:
        timeout_client.sendall(b"\x16")
        try:
            timeout_router.route_connection(timeout_relay)
            timeout_code = None
        except SniRoutingError as exc:
            timeout_code = exc.code
    finally:
        timeout_elapsed = time.monotonic() - timeout_started
        timeout_client.close()
        timeout_relay.close()
    require(timeout_code == "client_hello_timeout", "partial ClientHello did not time out", failures)
    require(timeout_elapsed < 0.75, "ClientHello deadline was not bounded", failures)

    capacity_router = BoundedSniRouter(
        {ALPHA_HOST: ALPHA_ROUTE},
        timeout_seconds=0.25,
        max_concurrent_inspections=1,
    )
    stalled_client, stalled_relay = loopback_tcp_pair()
    capacity_client, capacity_relay = loopback_tcp_pair()
    receive_started = threading.Event()
    stalled_result: list[str] = []

    def hold_inspection() -> None:
        try:
            capacity_stream = SignallingSocket(stalled_relay, receive_started)
            capacity_router.route_connection(capacity_stream)  # type: ignore[arg-type]
        except SniRoutingError as exc:
            stalled_result.append(exc.code)

    stalled_thread = threading.Thread(target=hold_inspection, daemon=True)
    stalled_thread.start()
    require(receive_started.wait(1.0), "capacity fixture did not enter inspection", failures)
    try:
        capacity_client.sendall(alpha_hello)
        busy_started = time.monotonic()
        try:
            capacity_router.route_connection(capacity_relay)
            busy_code = None
        except SniRoutingError as exc:
            busy_code = exc.code
        busy_elapsed = time.monotonic() - busy_started
    finally:
        capacity_client.close()
        capacity_relay.close()
        stalled_thread.join(1.0)
        stalled_client.close()
        stalled_relay.close()
    require(busy_code == "router_busy", "inspection capacity did not fail closed", failures)
    require(busy_elapsed < 0.1, "inspection backpressure was queued instead of rejected", failures)
    require(stalled_result == ["client_hello_timeout"], "stalled inspection was not bounded", failures)

    invalid_route_rejected = False
    wildcard_rejected = False
    duplicate_normalized_rejected = False
    try:
        BoundedSniRouter({ALPHA_HOST: "/private/authority.db"})
    except ValueError:
        invalid_route_rejected = True
    try:
        BoundedSniRouter({"*.agentops.test": ALPHA_ROUTE})
    except ValueError:
        wildcard_rejected = True
    try:
        BoundedSniRouter({ALPHA_HOST: ALPHA_ROUTE, ALPHA_HOST.upper(): BETA_ROUTE})
    except ValueError:
        duplicate_normalized_rejected = True
    require(invalid_route_rejected, "path-like route authority was accepted", failures)
    require(wildcard_rejected, "catch-all hostname route was accepted", failures)
    require(duplicate_normalized_rejected, "normalized hostname collision was accepted", failures)

    public_error_text = " ".join(
        code or "" for code in (unknown_code, malformed_code, no_sni_code, oversized_code)
    )
    forbidden_public_values = (
        ALPHA_HOST,
        BETA_HOST,
        UNKNOWN_HOST,
        OPAQUE_ALPHA.decode("ascii"),
        OPAQUE_BETA.decode("ascii"),
        "/private/authority.db",
        "credential",
    )
    require(
        not any(value in public_error_text for value in forbidden_public_values),
        "routing failures exposed routes, payload, authority, or credentials",
        failures,
    )
    require(
        OPAQUE_ALPHA not in repr(router).encode("utf-8")
        and OPAQUE_BETA not in repr(router).encode("utf-8"),
        "router representation retained application payload",
        failures,
    )

    receipt = {
        "application_payload_opaque": alpha_remainder == OPAQUE_ALPHA and beta_remainder == OPAQUE_BETA,
        "bounded_client_hello_bytes": MAX_CLIENT_HELLO_BYTES,
        "bounded_client_hello_timeout": timeout_code == "client_hello_timeout",
        "bounded_concurrency_backpressure": busy_code == "router_busy",
        "deployed_relay": False,
        "exact_normalized_sni": alpha_route == ALPHA_ROUTE,
        "fail_closed_cases": {
            "malformed": malformed_code,
            "no_sni": no_sni_code,
            "oversized": oversized_code,
            "unknown": unknown_code,
        },
        "host_route_isolation": alpha_route != beta_route,
        "loopback_only": True,
        "no_tls_termination": alpha_preface == alpha_hello and beta_preface == beta_hello,
        "ok": not failures,
        "route_enumeration": False,
    }
    if failures:
        receipt["failures"] = failures
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
