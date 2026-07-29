"""Dependency-free transport primitives for the browser-only Relay tunnel.

The deployed Relay is deliberately not implemented here.  This module defines
the bounded envelope and replay rules used by the deterministic loopback
transport harness.  Application bytes remain opaque to the Relay and are never
included in event evidence.
"""
from __future__ import annotations

import json
import re
import math
import socket
import struct
import threading
from collections import deque
from dataclasses import dataclass


PROTOCOL_VERSION = 1
MAX_CONTROL_BYTES = 4096
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_EVENT_COUNT = 256
MAX_REQUEST_HISTORY = 4096
MAX_REPLAY_STREAMS = 256
_LENGTHS = struct.Struct("!II")
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
_DIRECTIONS = {"console_to_host", "host_to_console"}
_EVENT_STATUSES = {"failed", "forwarded", "rejected"}
_ERROR_CLASS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RelayProtocolError(ValueError):
    """A bounded, non-secret Relay protocol failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RelayFrame:
    route_ref: str
    epoch: int
    message_id: int
    request_id: str
    direction: str
    payload: bytes

    def validate(self) -> None:
        if not isinstance(self.route_ref, str) or not _REFERENCE.fullmatch(self.route_ref):
            raise RelayProtocolError("invalid_route_ref")
        if not isinstance(self.request_id, str) or not _REFERENCE.fullmatch(self.request_id):
            raise RelayProtocolError("invalid_request_id")
        if not isinstance(self.direction, str) or self.direction not in _DIRECTIONS:
            raise RelayProtocolError("invalid_direction")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 1:
            raise RelayProtocolError("invalid_epoch")
        if not isinstance(self.message_id, int) or isinstance(self.message_id, bool) or self.message_id < 1:
            raise RelayProtocolError("invalid_message_id")
        if not isinstance(self.payload, bytes):
            raise RelayProtocolError("invalid_payload")
        if len(self.payload) > MAX_PAYLOAD_BYTES:
            raise RelayProtocolError("payload_too_large")

    def control(self) -> dict[str, object]:
        return {
            "direction": self.direction,
            "epoch": self.epoch,
            "message_id": self.message_id,
            "request_id": self.request_id,
            "route_ref": self.route_ref,
            "version": PROTOCOL_VERSION,
        }


def _read_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = stream.recv(remaining)
        except OSError:
            raise RelayProtocolError("read_failed") from None
        if not chunk:
            raise RelayProtocolError("unexpected_eof")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(frame: RelayFrame) -> bytes:
    frame.validate()
    control = json.dumps(
        frame.control(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if len(control) > MAX_CONTROL_BYTES:
        raise RelayProtocolError("control_too_large")
    return _LENGTHS.pack(len(control), len(frame.payload)) + control + frame.payload


def receive_frame(stream: socket.socket) -> RelayFrame:
    control_size, payload_size = _LENGTHS.unpack(_read_exact(stream, _LENGTHS.size))
    if control_size < 2 or control_size > MAX_CONTROL_BYTES:
        raise RelayProtocolError("invalid_control_size")
    if payload_size > MAX_PAYLOAD_BYTES:
        raise RelayProtocolError("payload_too_large")
    try:
        control = json.loads(_read_exact(stream, control_size).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RelayProtocolError("invalid_control") from None
    if not isinstance(control, dict) or set(control) != {
        "direction",
        "epoch",
        "message_id",
        "request_id",
        "route_ref",
        "version",
    }:
        raise RelayProtocolError("invalid_control_shape")
    version = control.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise RelayProtocolError("unsupported_version")
    frame = RelayFrame(
        route_ref=control.get("route_ref"),
        epoch=control.get("epoch"),
        message_id=control.get("message_id"),
        request_id=control.get("request_id"),
        direction=control.get("direction"),
        payload=_read_exact(stream, payload_size),
    )
    frame.validate()
    return frame


class ReplayWindow:
    """Enforce one monotonically increasing stream per route and direction."""

    def __init__(
        self,
        max_request_history: int = MAX_REQUEST_HISTORY,
        max_streams: int = MAX_REPLAY_STREAMS,
    ) -> None:
        if max_request_history < 1 or max_request_history > MAX_REQUEST_HISTORY:
            raise RelayProtocolError("invalid_request_history_limit")
        if max_streams < 1 or max_streams > MAX_REPLAY_STREAMS:
            raise RelayProtocolError("invalid_stream_limit")
        self._state: dict[tuple[str, str], tuple[int, int]] = {}
        self._requests: set[tuple[str, str, str]] = set()
        self._request_order: deque[tuple[str, str, str]] = deque()
        self._pending: dict[tuple[str, str], tuple[int, int, tuple[str, str, str]]] = {}
        self._pending_requests: set[tuple[str, str, str]] = set()
        self._failed_epochs: dict[tuple[str, str], int] = {}
        self._max_request_history = max_request_history
        self._max_streams = max_streams
        self._lock = threading.Lock()

    def reserve(self, frame: RelayFrame) -> tuple[str, str, int, int, str]:
        frame.validate()
        key = (frame.route_ref, frame.direction)
        request_key = (frame.route_ref, frame.direction, frame.request_id)
        with self._lock:
            if key in self._pending:
                raise RelayProtocolError("stream_busy")
            previous = self._state.get(key)
            failed_epoch = self._failed_epochs.get(key)
            if failed_epoch is not None and frame.epoch <= failed_epoch:
                raise RelayProtocolError("stream_epoch_failed")
            if previous is None:
                if len(self._state) + len(self._pending) >= self._max_streams:
                    raise RelayProtocolError("stream_capacity_exceeded")
                if frame.message_id != 1:
                    raise RelayProtocolError("message_gap")
            else:
                previous_epoch, previous_message_id = previous
                if frame.epoch < previous_epoch:
                    raise RelayProtocolError("stale_epoch")
                if frame.epoch == previous_epoch:
                    if frame.message_id <= previous_message_id:
                        raise RelayProtocolError("replayed_message")
                    if frame.message_id != previous_message_id + 1:
                        raise RelayProtocolError("message_gap")
                elif frame.message_id != 1:
                    raise RelayProtocolError("new_epoch_must_restart_sequence")
            if request_key in self._requests or request_key in self._pending_requests:
                raise RelayProtocolError("replayed_request")
            self._pending[key] = (frame.epoch, frame.message_id, request_key)
            self._pending_requests.add(request_key)
        return (frame.route_ref, frame.direction, frame.epoch, frame.message_id, frame.request_id)

    def commit(self, reservation: tuple[str, str, int, int, str]) -> None:
        route_ref, direction, epoch, message_id, request_id = reservation
        key = (route_ref, direction)
        request_key = (route_ref, direction, request_id)
        with self._lock:
            if self._pending.get(key) != (epoch, message_id, request_key):
                raise RelayProtocolError("invalid_reservation")
            self._pending.pop(key)
            self._pending_requests.discard(request_key)
            self._state[key] = (epoch, message_id)
            if self._failed_epochs.get(key, 0) < epoch:
                self._failed_epochs.pop(key, None)
            if len(self._request_order) >= self._max_request_history:
                self._requests.discard(self._request_order.popleft())
            self._requests.add(request_key)
            self._request_order.append(request_key)

    def rollback(self, reservation: tuple[str, str, int, int, str]) -> None:
        route_ref, direction, epoch, message_id, request_id = reservation
        key = (route_ref, direction)
        request_key = (route_ref, direction, request_id)
        with self._lock:
            if self._pending.get(key) == (epoch, message_id, request_key):
                self._pending.pop(key)
                self._pending_requests.discard(request_key)

    def fail_epoch(self, reservation: tuple[str, str, int, int, str]) -> None:
        route_ref, direction, epoch, message_id, request_id = reservation
        key = (route_ref, direction)
        request_key = (route_ref, direction, request_id)
        with self._lock:
            if self._pending.get(key) != (epoch, message_id, request_key):
                raise RelayProtocolError("invalid_reservation")
            self._pending.pop(key)
            self._pending_requests.discard(request_key)
            self._state[key] = (epoch, message_id)
            self._failed_epochs[key] = epoch
            if len(self._request_order) >= self._max_request_history:
                self._requests.discard(self._request_order.popleft())
            self._requests.add(request_key)
            self._request_order.append(request_key)

    def accept(self, frame: RelayFrame) -> None:
        reservation = self.reserve(frame)
        self.commit(reservation)

    def release_route(self, route_ref: str) -> None:
        if not isinstance(route_ref, str) or not _REFERENCE.fullmatch(route_ref):
            raise RelayProtocolError("invalid_route_ref")
        with self._lock:
            if any(key[0] == route_ref for key in self._pending):
                raise RelayProtocolError("route_busy")
            keys = [key for key in self._state if key[0] == route_ref]
            for key in keys:
                self._state.pop(key, None)
                self._failed_epochs.pop(key, None)
            self._requests = {item for item in self._requests if item[0] != route_ref}
            self._request_order = deque(item for item in self._request_order if item[0] != route_ref)


class BoundedRelayEvents:
    """In-memory operational evidence containing no application bytes."""

    def __init__(self, max_events: int = MAX_EVENT_COUNT) -> None:
        if max_events < 1 or max_events > MAX_EVENT_COUNT:
            raise RelayProtocolError("invalid_event_limit")
        self._events: deque[dict[str, object]] = deque(maxlen=max_events)

    def record(self, frame: RelayFrame, *, status: str, error_class: str | None = None) -> None:
        if status not in _EVENT_STATUSES:
            raise RelayProtocolError("invalid_event_status")
        if error_class is not None and not _ERROR_CLASS.fullmatch(error_class):
            raise RelayProtocolError("invalid_error_class")
        event = {
            "byte_count": len(frame.payload),
            "direction": frame.direction,
            "epoch": frame.epoch,
            "message_id": frame.message_id,
            "status": status,
            "version": PROTOCOL_VERSION,
        }
        if error_class:
            event["error_class"] = error_class
        self._events.append(event)

    def snapshot(self) -> list[dict[str, object]]:
        return [dict(event) for event in self._events]


def relay_one_frame(
    source: socket.socket,
    destination: socket.socket,
    *,
    replay_window: ReplayWindow,
    events: BoundedRelayEvents,
    expected_route_ref: str,
    expected_direction: str,
    expected_epoch: int,
    timeout_seconds: float = 10.0,
) -> RelayFrame:
    """Validate and forward one frame under a trusted connection context."""

    if not isinstance(expected_route_ref, str) or not _REFERENCE.fullmatch(expected_route_ref):
        raise RelayProtocolError("invalid_expected_route")
    if not isinstance(expected_direction, str) or expected_direction not in _DIRECTIONS:
        raise RelayProtocolError("invalid_expected_direction")
    if not isinstance(expected_epoch, int) or isinstance(expected_epoch, bool) or expected_epoch < 1:
        raise RelayProtocolError("invalid_expected_epoch")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise RelayProtocolError("invalid_timeout")
    if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0 or timeout_seconds > 60:
        raise RelayProtocolError("invalid_timeout")
    try:
        source_timeout = source.gettimeout()
        destination_timeout = destination.gettimeout()
    except OSError:
        raise RelayProtocolError("socket_unavailable") from None
    try:
        source.settimeout(float(timeout_seconds))
        destination.settimeout(float(timeout_seconds))
    except OSError:
        try:
            source.settimeout(source_timeout)
        except OSError:
            pass
        raise RelayProtocolError("socket_unavailable") from None
    try:
        frame = receive_frame(source)
        if frame.route_ref != expected_route_ref:
            events.record(frame, status="rejected", error_class="route_mismatch")
            raise RelayProtocolError("route_mismatch")
        if frame.direction != expected_direction:
            events.record(frame, status="rejected", error_class="direction_mismatch")
            raise RelayProtocolError("direction_mismatch")
        if frame.epoch != expected_epoch:
            events.record(frame, status="rejected", error_class="epoch_mismatch")
            raise RelayProtocolError("epoch_mismatch")
        reservation = replay_window.reserve(frame)
    except RelayProtocolError as exc:
        if "frame" in locals() and exc.code not in {"route_mismatch", "direction_mismatch", "epoch_mismatch"}:
            events.record(frame, status="rejected", error_class=exc.code)
        raise
    else:
        try:
            destination.sendall(encode_frame(frame))
        except OSError:
            replay_window.fail_epoch(reservation)
            events.record(frame, status="failed", error_class="destination_unavailable")
            raise RelayProtocolError("destination_unavailable") from None
        replay_window.commit(reservation)
        events.record(frame, status="forwarded")
        return frame
    finally:
        try:
            source.settimeout(source_timeout)
        except OSError:
            pass
        try:
            destination.settimeout(destination_timeout)
        except OSError:
            pass
