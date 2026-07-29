"""Bounded L4 ClientHello SNI routing for a Relay boundary.

This module does not terminate TLS or proxy application data. It consumes only
the first TLS ClientHello records, returns those exact bytes for forwarding, and
selects one pre-registered opaque route reference. It deliberately exposes no
route-listing or catch-all API.
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


MAX_CLIENT_HELLO_BYTES = 16 * 1024
MAX_CLIENT_HELLO_RECORDS = 8
MAX_ROUTE_COUNT = 4096
MAX_CONCURRENT_INSPECTIONS = 128
DEFAULT_CLIENT_HELLO_TIMEOUT_SECONDS = 2.0
MIN_CLIENT_HELLO_TIMEOUT_SECONDS = 0.05
MAX_CLIENT_HELLO_TIMEOUT_SECONDS = 10.0

_TLS_HANDSHAKE_CONTENT_TYPE = 22
_TLS_CLIENT_HELLO_TYPE = 1
_SERVER_NAME_EXTENSION = 0
_HOST_NAME_TYPE = 0

_ERROR_CODES = frozenset(
    {
        "client_hello_incomplete",
        "client_hello_read_failed",
        "client_hello_timeout",
        "client_hello_too_large",
        "invalid_server_name",
        "malformed_client_hello",
        "route_not_found",
        "router_busy",
        "sni_required",
    }
)


class SniRoutingError(RuntimeError):
    """A fail-closed routing failure with a bounded, non-enumerating code."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("unapproved_sni_routing_error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SniRouteSelection:
    """Ephemeral routing result; ``preface`` must be forwarded exactly once."""

    route_ref: str = field(repr=False)
    preface: bytes = field(repr=False)

    def __repr__(self) -> str:
        return "SniRouteSelection(<redacted>)"


def normalize_dns_hostname(value: str) -> str:
    """Return one exact lower-case ASCII DNS hostname or reject it.

    Wildcards, IP literals, empty labels, non-ASCII names, and DNS names outside
    the wire-format limits are not accepted. A single terminal dot is removed.
    """
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError("invalid_dns_hostname")
    candidate = value[:-1] if value.endswith(".") else value
    if not candidate or len(candidate) > 253 or "*" in candidate:
        raise ValueError("invalid_dns_hostname")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise ValueError("invalid_dns_hostname")
    labels = candidate.split(".")
    if not labels or any(
        len(label) < 1
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ValueError("invalid_dns_hostname")
    return candidate.lower()


def _valid_route_ref(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value.isascii()
        and value[0].isalnum()
        and value[-1].isalnum()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def _read_u16(payload: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(payload):
        raise SniRoutingError("malformed_client_hello")
    return int.from_bytes(payload[offset : offset + 2], "big"), offset + 2


def _extract_normalized_sni(client_hello: bytes) -> str:
    offset = 0
    if len(client_hello) < 38:
        raise SniRoutingError("malformed_client_hello")
    if client_hello[0] != 3:
        raise SniRoutingError("malformed_client_hello")
    offset = 2 + 32

    session_id_length = client_hello[offset]
    offset += 1
    if session_id_length > 32 or offset + session_id_length > len(client_hello):
        raise SniRoutingError("malformed_client_hello")
    offset += session_id_length

    cipher_suites_length, offset = _read_u16(client_hello, offset)
    if cipher_suites_length < 2 or cipher_suites_length % 2:
        raise SniRoutingError("malformed_client_hello")
    if offset + cipher_suites_length > len(client_hello):
        raise SniRoutingError("malformed_client_hello")
    offset += cipher_suites_length

    if offset >= len(client_hello):
        raise SniRoutingError("malformed_client_hello")
    compression_methods_length = client_hello[offset]
    offset += 1
    if compression_methods_length < 1 or offset + compression_methods_length > len(client_hello):
        raise SniRoutingError("malformed_client_hello")
    offset += compression_methods_length

    if offset == len(client_hello):
        raise SniRoutingError("sni_required")
    extensions_length, offset = _read_u16(client_hello, offset)
    extensions_end = offset + extensions_length
    if extensions_end != len(client_hello):
        raise SniRoutingError("malformed_client_hello")

    server_name_extension: bytes | None = None
    while offset < extensions_end:
        extension_type, offset = _read_u16(client_hello, offset)
        extension_length, offset = _read_u16(client_hello, offset)
        extension_end = offset + extension_length
        if extension_end > extensions_end:
            raise SniRoutingError("malformed_client_hello")
        if extension_type == _SERVER_NAME_EXTENSION:
            if server_name_extension is not None:
                raise SniRoutingError("malformed_client_hello")
            server_name_extension = client_hello[offset:extension_end]
        offset = extension_end

    if server_name_extension is None:
        raise SniRoutingError("sni_required")
    names_length, names_offset = _read_u16(server_name_extension, 0)
    if names_length < 3 or names_offset + names_length != len(server_name_extension):
        raise SniRoutingError("malformed_client_hello")

    host_name: bytes | None = None
    names_end = names_offset + names_length
    while names_offset < names_end:
        if names_offset + 3 > names_end:
            raise SniRoutingError("malformed_client_hello")
        name_type = server_name_extension[names_offset]
        name_length = int.from_bytes(
            server_name_extension[names_offset + 1 : names_offset + 3], "big"
        )
        names_offset += 3
        name_end = names_offset + name_length
        if name_length < 1 or name_end > names_end:
            raise SniRoutingError("malformed_client_hello")
        if name_type == _HOST_NAME_TYPE:
            if host_name is not None:
                raise SniRoutingError("malformed_client_hello")
            host_name = server_name_extension[names_offset:name_end]
        names_offset = name_end

    if host_name is None:
        raise SniRoutingError("sni_required")
    try:
        decoded = host_name.decode("ascii")
        return normalize_dns_hostname(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SniRoutingError("invalid_server_name") from exc


def _read_exact(
    stream: socket.socket,
    size: int,
    *,
    deadline: float,
    consumed: int,
) -> bytes:
    if size < 0 or consumed + size > MAX_CLIENT_HELLO_BYTES:
        raise SniRoutingError("client_hello_too_large")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise SniRoutingError("client_hello_timeout")
        try:
            stream.settimeout(timeout)
            chunk = stream.recv(remaining)
        except (socket.timeout, TimeoutError) as exc:
            raise SniRoutingError("client_hello_timeout") from exc
        except OSError as exc:
            raise SniRoutingError("client_hello_read_failed") from exc
        if not chunk:
            raise SniRoutingError("client_hello_incomplete")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_client_hello(
    stream: socket.socket,
    *,
    deadline: float,
) -> tuple[bytes, bytes]:
    preface = bytearray()
    handshake = bytearray()
    expected_handshake_bytes: int | None = None

    for _record_number in range(MAX_CLIENT_HELLO_RECORDS):
        header = _read_exact(stream, 5, deadline=deadline, consumed=len(preface))
        preface.extend(header)
        content_type, version, record_length = struct.unpack("!BHH", header)
        if (
            content_type != _TLS_HANDSHAKE_CONTENT_TYPE
            or version >> 8 != 3
            or version & 0xFF > 4
            or record_length < 1
        ):
            raise SniRoutingError("malformed_client_hello")
        record = _read_exact(
            stream,
            record_length,
            deadline=deadline,
            consumed=len(preface),
        )
        preface.extend(record)
        handshake.extend(record)

        if expected_handshake_bytes is None and len(handshake) >= 4:
            if handshake[0] != _TLS_CLIENT_HELLO_TYPE:
                raise SniRoutingError("malformed_client_hello")
            body_length = int.from_bytes(handshake[1:4], "big")
            expected_handshake_bytes = 4 + body_length
            if expected_handshake_bytes > MAX_CLIENT_HELLO_BYTES:
                raise SniRoutingError("client_hello_too_large")
        if expected_handshake_bytes is not None and len(handshake) >= expected_handshake_bytes:
            body = bytes(handshake[4:expected_handshake_bytes])
            return bytes(preface), body

    raise SniRoutingError("client_hello_too_large")


class BoundedSniRouter:
    """Select exact opaque route refs from bounded TLS ClientHello input."""

    __slots__ = ("_inspection_slots", "_routes", "_timeout_seconds")

    def __init__(
        self,
        routes: Mapping[str, str],
        *,
        timeout_seconds: float = DEFAULT_CLIENT_HELLO_TIMEOUT_SECONDS,
        max_concurrent_inspections: int = MAX_CONCURRENT_INSPECTIONS,
    ) -> None:
        if not isinstance(routes, Mapping) or len(routes) > MAX_ROUTE_COUNT:
            raise ValueError("invalid_route_table")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not MIN_CLIENT_HELLO_TIMEOUT_SECONDS
            <= float(timeout_seconds)
            <= MAX_CLIENT_HELLO_TIMEOUT_SECONDS
        ):
            raise ValueError("invalid_client_hello_timeout")
        if (
            not isinstance(max_concurrent_inspections, int)
            or isinstance(max_concurrent_inspections, bool)
            or not 1 <= max_concurrent_inspections <= MAX_CONCURRENT_INSPECTIONS
        ):
            raise ValueError("invalid_inspection_capacity")

        normalized: dict[str, str] = {}
        route_refs: set[str] = set()
        for hostname, route_ref in routes.items():
            normalized_hostname = normalize_dns_hostname(hostname)
            if normalized_hostname in normalized:
                raise ValueError("duplicate_normalized_hostname")
            if not _valid_route_ref(route_ref) or route_ref in route_refs:
                raise ValueError("invalid_route_ref")
            normalized[normalized_hostname] = route_ref
            route_refs.add(route_ref)
        self._routes = MappingProxyType(normalized)
        self._timeout_seconds = float(timeout_seconds)
        self._inspection_slots = threading.BoundedSemaphore(max_concurrent_inspections)

    def __repr__(self) -> str:
        return "BoundedSniRouter(<redacted>)"

    def route_connection(self, stream: socket.socket) -> SniRouteSelection:
        """Inspect one connection and return its route plus exact TLS preface.

        Capacity is never queued: excess inspections fail immediately. The
        router retains neither the preface nor later bytes after this call.
        """
        if not self._inspection_slots.acquire(blocking=False):
            raise SniRoutingError("router_busy")
        previous_timeout: float | None = None
        try:
            try:
                previous_timeout = stream.gettimeout()
            except OSError as exc:
                raise SniRoutingError("client_hello_read_failed") from exc
            deadline = time.monotonic() + self._timeout_seconds
            preface, client_hello = _receive_client_hello(stream, deadline=deadline)
            server_name = _extract_normalized_sni(client_hello)
            route_ref = self._routes.get(server_name)
            if route_ref is None:
                raise SniRoutingError("route_not_found")
            return SniRouteSelection(route_ref=route_ref, preface=preface)
        finally:
            try:
                stream.settimeout(previous_timeout)
            except OSError:
                pass
            self._inspection_slots.release()
