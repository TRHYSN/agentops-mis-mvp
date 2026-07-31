#!/usr/bin/env python3
"""Exercise the bounded browser-only Relay transport contract on loopback."""
from __future__ import annotations

import json
import socket
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_core.relay_transport import (  # noqa: E402
    MAX_PAYLOAD_BYTES,
    BoundedRelayEvents,
    RelayFrame,
    RelayProtocolError,
    ReplayWindow,
    encode_frame,
    receive_frame,
    relay_one_frame,
)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def loopback_tcp_pair() -> tuple[socket.socket, socket.socket]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=5)
    server, _address = listener.accept()
    listener.close()
    client.settimeout(5)
    server.settimeout(5)
    return client, server


def rejected_code(
    wire: bytes,
    sender: socket.socket,
    relay_input: socket.socket,
    relay_output: socket.socket,
    replay: ReplayWindow,
    events: BoundedRelayEvents,
    *,
    expected_route_ref: str,
    expected_direction: str,
    expected_epoch: int,
) -> str | None:
    sender.sendall(wire)
    try:
        relay_one_frame(
            relay_input,
            relay_output,
            replay_window=replay,
            events=events,
            expected_route_ref=expected_route_ref,
            expected_direction=expected_direction,
            expected_epoch=expected_epoch,
            timeout_seconds=5,
        )
    except RelayProtocolError as exc:
        return exc.code
    return None


def main() -> int:
    failures: list[str] = []
    replay = ReplayWindow()
    events = BoundedRelayEvents(max_events=8)
    console, relay_from_console = loopback_tcp_pair()
    relay_to_host, host = loopback_tcp_pair()
    host_to_relay, relay_from_host = loopback_tcp_pair()
    relay_to_console, console_response = loopback_tcp_pair()
    sockets = (
        console,
        relay_from_console,
        relay_to_host,
        host,
        host_to_relay,
        relay_from_host,
        relay_to_console,
        console_response,
    )
    try:
        route_ref = "rte_loopback_01"
        request_marker = "req_loopback_01"
        opaque_request = b"\x16\x03\x03opaque-tls-ciphertext-request-marker"
        request_frame = RelayFrame(
            route_ref=route_ref,
            epoch=1,
            message_id=1,
            request_id=request_marker,
            direction="console_to_host",
            payload=opaque_request,
        )
        console.sendall(encode_frame(request_frame))
        relay_one_frame(
            relay_from_console,
            relay_to_host,
            replay_window=replay,
            events=events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=1,
            timeout_seconds=5,
        )
        received_request = receive_frame(host)
        require(received_request.payload == opaque_request, "Host did not receive exact opaque bytes", failures)

        opaque_response = b"\x16\x03\x03opaque-tls-ciphertext-response-marker"
        response_frame = RelayFrame(
            route_ref=route_ref,
            epoch=1,
            message_id=1,
            request_id="req_loopback_02",
            direction="host_to_console",
            payload=opaque_response,
        )
        host_to_relay.sendall(encode_frame(response_frame))
        relay_one_frame(
            relay_from_host,
            relay_to_console,
            replay_window=replay,
            events=events,
            expected_route_ref=route_ref,
            expected_direction="host_to_console",
            expected_epoch=1,
            timeout_seconds=5,
        )
        received_response = receive_frame(console_response)
        require(received_response.payload == opaque_response, "Console did not receive exact opaque bytes", failures)

        duplicate_code = rejected_code(
            encode_frame(request_frame),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=1,
        )
        require(duplicate_code == "replayed_message", "duplicate message did not fail closed", failures)

        stale = RelayFrame(
            route_ref=route_ref,
            epoch=0,
            message_id=1,
            request_id="req_loopback_03",
            direction="console_to_host",
            payload=b"stale",
        )
        try:
            encode_frame(stale)
            stale_code = None
        except RelayProtocolError as exc:
            stale_code = exc.code
        require(stale_code == "invalid_epoch", "invalid epoch was accepted", failures)

        reconnected = RelayFrame(
            route_ref=route_ref,
            epoch=2,
            message_id=1,
            request_id="req_loopback_04",
            direction="console_to_host",
            payload=b"\x16\x03\x03reconnected-tls-ciphertext",
        )
        console.sendall(encode_frame(reconnected))
        relay_one_frame(
            relay_from_console,
            relay_to_host,
            replay_window=replay,
            events=events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
            timeout_seconds=5,
        )
        require(receive_frame(host).epoch == 2, "new connection epoch did not resume", failures)

        stale_after_reconnect = RelayFrame(
            route_ref=route_ref,
            epoch=1,
            message_id=2,
            request_id="req_loopback_05",
            direction="console_to_host",
            payload=b"stale-after-reconnect",
        )
        stale_code = rejected_code(
            encode_frame(stale_after_reconnect),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=1,
        )
        require(stale_code == "stale_epoch", "stale connection epoch did not fail closed", failures)

        duplicate_request = RelayFrame(
            route_ref=route_ref,
            epoch=2,
            message_id=2,
            request_id="req_loopback_04",
            direction="console_to_host",
            payload=b"request-replay",
        )
        request_code = rejected_code(
            encode_frame(duplicate_request),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
        )
        require(request_code == "replayed_request", "duplicate request ID did not fail closed", failures)

        sequence_gap = RelayFrame(
            route_ref=route_ref,
            epoch=2,
            message_id=3,
            request_id="req_loopback_06",
            direction="console_to_host",
            payload=b"sequence-gap",
        )
        gap_code = rejected_code(
            encode_frame(sequence_gap),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
        )
        require(gap_code == "message_gap", "message sequence gap did not fail closed", failures)

        wrong_direction = RelayFrame(
            route_ref=route_ref,
            epoch=2,
            message_id=1,
            request_id="req_loopback_08",
            direction="host_to_console",
            payload=b"direction-spoof",
        )
        direction_code = rejected_code(
            encode_frame(wrong_direction),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
        )
        require(direction_code == "direction_mismatch", "connection direction was not enforced", failures)

        epoch_spoof = RelayFrame(
            route_ref=route_ref,
            epoch=999,
            message_id=1,
            request_id="req_loopback_09",
            direction="console_to_host",
            payload=b"epoch-spoof",
        )
        epoch_code = rejected_code(
            encode_frame(epoch_spoof),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
        )
        require(epoch_code == "epoch_mismatch", "connection epoch was not enforced", failures)

        sensitive_route_marker = "agt" + "ok_tokenlike_canary_12345"
        sensitive_request_marker = "sk" + "_tokenlike_canary_123456"
        metadata_spoof = RelayFrame(
            route_ref=sensitive_route_marker,
            epoch=2,
            message_id=1,
            request_id=sensitive_request_marker,
            direction="console_to_host",
            payload=b"metadata-spoof",
        )
        route_code = rejected_code(
            encode_frame(metadata_spoof),
            console,
            relay_from_console,
            relay_to_host,
            replay,
            events,
            expected_route_ref=route_ref,
            expected_direction="console_to_host",
            expected_epoch=2,
        )
        require(route_code == "route_mismatch", "connection route was not enforced", failures)

        failure_sender, failure_input = loopback_tcp_pair()
        failed_destination, failed_peer = loopback_tcp_pair()
        failed_destination.shutdown(socket.SHUT_WR)
        try:
            destination_failure = RelayFrame(
                route_ref="rte_loopback_02",
                epoch=1,
                message_id=1,
                request_id="req_loopback_07",
                direction="console_to_host",
                payload=b"destination-failure",
            )
            failure_sender.sendall(encode_frame(destination_failure))
            try:
                relay_one_frame(
                    failure_input,
                    failed_destination,
                    replay_window=replay,
                    events=events,
                    expected_route_ref="rte_loopback_02",
                    expected_direction="console_to_host",
                    expected_epoch=1,
                    timeout_seconds=5,
                )
                destination_code = None
            except RelayProtocolError as exc:
                destination_code = exc.code
            require(
                destination_code == "destination_unavailable",
                "destination failure was not reduced to a bounded Relay error",
                failures,
            )
            retry_destination, retry_receiver = loopback_tcp_pair()
            try:
                failure_sender.sendall(encode_frame(destination_failure))
                try:
                    relay_one_frame(
                        failure_input,
                        retry_destination,
                        replay_window=replay,
                        events=events,
                        expected_route_ref="rte_loopback_02",
                        expected_direction="console_to_host",
                        expected_epoch=1,
                        timeout_seconds=5,
                    )
                    failed_epoch_code = None
                except RelayProtocolError as exc:
                    failed_epoch_code = exc.code
                require(
                    failed_epoch_code == "stream_epoch_failed",
                    "partial-write risk did not invalidate the failed stream epoch",
                    failures,
                )
                recovered_frame = RelayFrame(
                    route_ref="rte_loopback_02",
                    epoch=2,
                    message_id=1,
                    request_id="req_loopback_10",
                    direction="console_to_host",
                    payload=b"new-epoch-recovery",
                )
                failure_sender.sendall(encode_frame(recovered_frame))
                relay_one_frame(
                    failure_input,
                    retry_destination,
                    replay_window=replay,
                    events=events,
                    expected_route_ref="rte_loopback_02",
                    expected_direction="console_to_host",
                    expected_epoch=2,
                    timeout_seconds=5,
                )
                require(
                    receive_frame(retry_receiver).request_id == recovered_frame.request_id,
                    "new stream epoch did not recover after a failed write",
                    failures,
                )
            finally:
                retry_destination.close()
                retry_receiver.close()
        finally:
            failure_sender.close()
            failure_input.close()
            failed_destination.close()
            failed_peer.close()

        oversized_header = struct.pack("!II", 2, MAX_PAYLOAD_BYTES + 1) + b"{}"
        console.sendall(oversized_header)
        try:
            receive_frame(relay_from_console)
            oversized_code = None
        except RelayProtocolError as exc:
            oversized_code = exc.code
        require(oversized_code == "payload_too_large", "oversized frame was not rejected before payload read", failures)

        event_snapshot = events.snapshot()
        event_text = json.dumps(event_snapshot, ensure_ascii=True, sort_keys=True)
        prohibited_markers = (
            "opaque-tls-ciphertext-request-marker",
            "opaque-tls-ciphertext-response-marker",
            "reconnected-tls-ciphertext",
            "request-replay",
            "sequence-gap",
            "stale-after-reconnect",
            "destination-failure",
            "direction-spoof",
            "epoch-spoof",
            "metadata-spoof",
            "new-epoch-recovery",
            sensitive_route_marker,
            sensitive_request_marker,
        )
        require(len(event_snapshot) <= 8, "Relay event evidence exceeded its retention bound", failures)
        require(
            all(marker not in event_text for marker in prohibited_markers),
            "Relay event evidence retained application bytes",
            failures,
        )
        allowed_fields = {
            "byte_count",
            "direction",
            "epoch",
            "error_class",
            "message_id",
            "status",
            "version",
        }
        require(
            all(set(event).issubset(allowed_fields) for event in event_snapshot),
            "Relay event evidence exposed an unapproved field",
            failures,
        )

        eviction_events = BoundedRelayEvents(max_events=2)
        for message_id in (1, 2, 3):
            eviction_events.record(
                RelayFrame(
                    route_ref="rte_eviction_01",
                    epoch=1,
                    message_id=message_id,
                    request_id=f"req_eviction_{message_id:02d}",
                    direction="console_to_host",
                    payload=b"x",
                ),
                status="forwarded",
            )
        evicted_snapshot = eviction_events.snapshot()
        require(
            len(evicted_snapshot) == 2 and evicted_snapshot[0].get("message_id") == 2,
            "bounded event retention did not evict the oldest record",
            failures,
        )

        limited_replay = ReplayWindow(max_request_history=2, max_streams=1)
        first_route = RelayFrame(
            route_ref="rte_capacity_01",
            epoch=1,
            message_id=1,
            request_id="req_capacity_01",
            direction="console_to_host",
            payload=b"x",
        )
        second_route = RelayFrame(
            route_ref="rte_capacity_02",
            epoch=1,
            message_id=1,
            request_id="req_capacity_02",
            direction="console_to_host",
            payload=b"x",
        )
        limited_replay.accept(first_route)
        try:
            limited_replay.accept(second_route)
            capacity_code = None
        except RelayProtocolError as exc:
            capacity_code = exc.code
        pending_replay = ReplayWindow(max_streams=1)
        pending_reservation = pending_replay.reserve(first_route)
        try:
            pending_replay.release_route(first_route.route_ref)
            busy_release_code = None
        except RelayProtocolError as exc:
            busy_release_code = exc.code
        pending_replay.rollback(pending_reservation)
        pending_replay.release_route(first_route.route_ref)
        limited_replay.release_route(first_route.route_ref)
        try:
            limited_replay.accept(second_route)
            release_recovered = True
        except RelayProtocolError:
            release_recovered = False
        require(capacity_code == "stream_capacity_exceeded", "replay state cap was not enforced", failures)
        require(busy_release_code == "route_busy", "active route release did not fail closed", failures)
        require(release_recovered, "route release did not recover replay capacity", failures)
    finally:
        for stream in sockets:
            stream.close()

    result = {
        "operation": "local_l4_relay_transport_smoke",
        "ok": not failures,
        "binding": "temporary_127.0.0.1_tcp_only",
        "authority_database_present": False,
        "opaque_round_trip_verified": not failures,
        "replay_and_epoch_guard_verified": not failures,
        "connection_context_binding_verified": not failures,
        "bounded_frame_io": "256_kib_frame_ceiling_and_5_second_socket_deadline",
        "tls_handshake_tested": False,
        "tls_termination_boundary_proven": False,
        "tailscale_configuration_changed": False,
        "deployed_relay_claimed": False,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
