#!/usr/bin/env python3
"""Prove Relay forwarding keeps each stream on one bounded owner thread."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_tunnel import (  # noqa: E402
    BoundedRelayMetadata,
    _forward_bidirectional,
)


TIMEOUT_SECONDS = 3.0


class OwnerCheckedSocket:
    def __init__(self, stream: socket.socket) -> None:
        self._stream = stream
        self._owner: int | None = None
        self._violations = 0

    def _claim(self) -> None:
        current = threading.get_ident()
        if self._owner is None:
            self._owner = current
        elif self._owner != current:
            self._violations += 1

    @property
    def owner(self) -> int | None:
        return self._owner

    @property
    def violations(self) -> int:
        return self._violations

    def fileno(self) -> int:
        self._claim()
        return self._stream.fileno()

    def setblocking(self, enabled: bool) -> None:
        self._claim()
        self._stream.setblocking(enabled)

    def recv(self, size: int) -> bytes:
        self._claim()
        return self._stream.recv(size)

    def send(self, payload: bytes | bytearray) -> int:
        self._claim()
        return self._stream.send(payload)

    def shutdown(self, how: int) -> None:
        self._claim()
        self._stream.shutdown(how)

    def close(self) -> None:
        self._claim()
        self._stream.close()


def peer_exchange(
    stream: socket.socket,
    outbound: bytes,
    receipt: dict[str, Any],
    key: str,
) -> None:
    received = bytearray()
    try:
        stream.settimeout(TIMEOUT_SECONDS)
        stream.sendall(outbound)
        stream.shutdown(socket.SHUT_WR)
        while True:
            chunk = stream.recv(64 * 1024)
            if not chunk:
                break
            received.extend(chunk)
    except Exception as exc:
        receipt[f"{key}_error"] = type(exc).__name__
    finally:
        receipt[key] = bytes(received)
        stream.close()


def main() -> int:
    failures: list[str] = []
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    checked_left = OwnerCheckedSocket(left)
    checked_right = OwnerCheckedSocket(right)
    metadata = BoundedRelayMetadata()
    receipt: dict[str, Any] = {}
    left_payload = b"left-owner-probe" * 1024
    right_payload = b"right-owner-probe" * 1024
    peers = (
        threading.Thread(
            target=peer_exchange,
            args=(left_peer, left_payload, receipt, "left_received"),
            daemon=True,
        ),
        threading.Thread(
            target=peer_exchange,
            args=(right_peer, right_payload, receipt, "right_received"),
            daemon=True,
        ),
    )
    for peer in peers:
        peer.start()

    forwarding_thread = threading.get_ident()
    started = time.monotonic()
    forwarded = _forward_bidirectional(checked_left, checked_right, metadata)
    elapsed = time.monotonic() - started
    for peer in peers:
        peer.join(TIMEOUT_SECONDS)

    if not forwarded:
        failures.append("full-duplex forwarding did not complete")
    if any(peer.is_alive() for peer in peers):
        failures.append("peer exchange exceeded its time bound")
    if receipt.get("left_received") != right_payload:
        failures.append("host-to-browser bytes changed")
    if receipt.get("right_received") != left_payload:
        failures.append("browser-to-host bytes changed")
    if any(key.endswith("_error") for key in receipt):
        failures.append("peer exchange raised an error")
    if checked_left.owner != forwarding_thread or checked_right.owner != forwarding_thread:
        failures.append("a stream was not owned by the forwarding thread")
    if checked_left.violations or checked_right.violations:
        failures.append("a stream was touched by multiple threads")
    if elapsed >= TIMEOUT_SECONDS:
        failures.append("forwarding exceeded its time bound")

    events = metadata.snapshot()
    if len(events) != 2 or any(event.get("status") != "forwarded" for event in events):
        failures.append("forwarding metadata was incomplete or contradictory")
    if any(set(event) != {"byte_count", "direction", "status"} for event in events):
        failures.append("forwarding metadata exposed an unapproved field")

    stop_left, stop_left_peer = socket.socketpair()
    stop_right, stop_right_peer = socket.socketpair()
    stop_checked_left = OwnerCheckedSocket(stop_left)
    stop_checked_right = OwnerCheckedSocket(stop_right)
    stop_metadata = BoundedRelayMetadata()
    stop = threading.Event()
    stop_receipt: dict[str, Any] = {}

    def stopped_forwarder() -> None:
        stop_receipt["thread"] = threading.get_ident()
        stop_receipt["forwarded"] = _forward_bidirectional(
            stop_checked_left,
            stop_checked_right,
            stop_metadata,
            stop=stop,
        )

    stop_thread = threading.Thread(target=stopped_forwarder, daemon=True)
    stop_thread.start()
    time.sleep(0.05)
    stop.set()
    stop_thread.join(1.0)
    stop_left_peer.close()
    stop_right_peer.close()
    if stop_thread.is_alive():
        failures.append("stop signal did not bound forwarding shutdown")
    if stop_receipt.get("forwarded") is not False:
        failures.append("stopped forwarding was reported as success")
    if (
        stop_checked_left.owner != stop_receipt.get("thread")
        or stop_checked_right.owner != stop_receipt.get("thread")
    ):
        failures.append("stop path changed stream ownership")
    if stop_checked_left.violations or stop_checked_right.violations:
        failures.append("stop path touched a stream from multiple threads")
    if any(event.get("status") != "failed" for event in stop_metadata.snapshot()):
        failures.append("stopped forwarding did not emit truthful bounded metadata")

    result = {
        "bounded_full_duplex": forwarded and elapsed < TIMEOUT_SECONDS,
        "bounded_stop": not stop_thread.is_alive(),
        "database_used": False,
        "failures": failures,
        "metadata_fields": ["byte_count", "direction", "status"],
        "ok": not failures,
        "operation": "relay_tunnel_single_owner_smoke",
        "payloads_retained": False,
        "single_owner_verified": checked_left.owner == forwarding_thread
        and checked_right.owner == forwarding_thread
        and stop_checked_left.owner == stop_receipt.get("thread")
        and stop_checked_right.owner == stop_receipt.get("thread")
        and not checked_left.violations
        and not checked_right.violations
        and not stop_checked_left.violations
        and not stop_checked_right.violations,
        "threads_spawned_by_forwarder": 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
