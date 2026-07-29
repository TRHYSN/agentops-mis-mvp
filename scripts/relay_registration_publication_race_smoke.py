#!/usr/bin/env python3
"""Prove Relay control readiness cannot precede route publication."""
from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_tunnel  # noqa: E402
from scripts.relay_tls_authenticated_tunnel_smoke import bind_listener  # noqa: E402


ROUTE = "registration-publication-race"
REQUEST = b"relay-registration-barrier-request"
RESPONSE = b"relay-registration-barrier-response"
TIMEOUT = 5.0


def receive_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def host_endpoint(listener: socket.socket, result: queue.Queue[dict]) -> None:
    receipt = {"request_matched": False}
    try:
        listener.settimeout(TIMEOUT)
        stream, _ = listener.accept()
        stream.settimeout(TIMEOUT)
        with stream:
            receipt["request_matched"] = receive_exact(stream, len(REQUEST)) == REQUEST
            stream.sendall(RESPONSE)
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()
        result.put(receipt)


def main() -> int:
    failures: list[str] = []
    key = os.urandom(32)
    browser_listener = bind_listener()
    connector_listener = bind_listener()
    host_listener = bind_listener()
    host_result: queue.Queue[dict] = queue.Queue()
    host_thread = threading.Thread(
        target=host_endpoint,
        args=(host_listener, host_result),
        daemon=True,
    )
    host_thread.start()
    relay = relay_tunnel.LocalFakeRelay(
        browser_listener=browser_listener,
        connector_listener=connector_listener,
        route=ROUTE,
        key=key,
    )
    connector = relay_tunnel.HostTunnelConnector(
        relay_address=connector_listener.getsockname(),
        host_tls_target=host_listener.getsockname(),
        route=ROUTE,
        epoch=1,
        key=key,
    )
    ack_sent = threading.Event()
    release_ack_sender = threading.Event()
    original_send_frame = relay_tunnel.send_frame

    def gated_send_frame(stream, frame, frame_key):
        original_send_frame(stream, frame, frame_key)
        if frame.kind == "registered":
            ack_sent.set()
            if not release_ack_sender.wait(TIMEOUT):
                raise relay_tunnel.RelayProtocolError("registration_gate_timeout")

    relay_tunnel.send_frame = gated_send_frame
    browser: socket.socket | None = None
    lock_was_held = False
    response = b""
    try:
        relay.start()
        connector.start()
        if not ack_sent.wait(TIMEOUT) or not connector.wait_until_ready(TIMEOUT):
            failures.append("connector did not observe the gated registration ACK")

        acquired = relay._lock.acquire(blocking=False)
        lock_was_held = not acquired
        if acquired:
            relay._lock.release()
            failures.append("Relay exposed an ACK/publication scheduling window")

        browser = socket.create_connection(browser_listener.getsockname(), timeout=TIMEOUT)
        browser.settimeout(TIMEOUT)
        browser.sendall(REQUEST)
        release_ack_sender.set()
        response = receive_exact(browser, len(RESPONSE))
        if response != RESPONSE:
            failures.append("browser route failed after registration publication")
    except Exception as exc:
        failures.append(f"registration barrier failed with {type(exc).__name__}")
    finally:
        release_ack_sender.set()
        relay_tunnel.send_frame = original_send_frame
        if browser is not None:
            browser.close()
        connector.stop()
        relay.stop()

    host_thread.join(TIMEOUT)
    host_receipt = host_result.get_nowait() if not host_thread.is_alive() else {}
    if host_receipt.get("request_matched") is not True or host_receipt.get("error_type"):
        failures.append("Host endpoint did not receive the post-publication request")

    result = {
        "ack_and_route_publication_atomic": lock_was_held,
        "failures": failures,
        "ok": not failures,
        "operation": "relay_registration_publication_race_smoke",
        "payloads_omitted": True,
        "round_trip_completed": response == RESPONSE,
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
