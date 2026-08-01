#!/usr/bin/env python3
"""Loopback acceptance for the deployable multi-route Relay daemon."""
from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import ssl
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

from agentops_mis_cli.relay_daemon import (  # noqa: E402
    MultiRouteRelay,
    PersistentEpochStore,
    bind_listener,
)
from agentops_mis_cli.relay_tunnel import (  # noqa: E402
    HostTunnelConnector,
    RelayFrame,
    RelayProtocolError,
    encode_frame,
    receive_routed_frame,
)


RELAY_HOSTNAME = "relay.agentops.test"
ALPHA_HOSTNAME = "alpha.console.agentops.test"
BETA_HOSTNAME = "beta.console.agentops.test"
UNKNOWN_HOSTNAME = "unknown.console.agentops.test"
ALPHA_ROUTE = "rte_daemon_alpha"
BETA_ROUTE = "rte_daemon_beta"
ALPHA_MARKER = b"alpha-host-route-ok"
BETA_MARKER = b"beta-host-route-ok"
FOLLOWUP = b"clienthello-followup-sentinel"
TIMEOUT = 8.0


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def generate_certificate(
    openssl: str,
    directory: Path,
    *,
    hostname: str,
) -> tuple[Path, Path]:
    certificate = directory / "relay-cert.pem"
    private_key = directory / "relay-key.pem"
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
    private_key.chmod(0o600)
    certificate.chmod(0o644)
    return certificate, private_key


def real_client_hello(server_hostname: str) -> bytes:
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
        raise RuntimeError("client_hello_unavailable")
    return payload


def connector_client_context(certificate: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH,
        cafile=str(certificate),
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def connector_server_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    return context


def host_receipt_server(
    listener: socket.socket,
    *,
    expected_preface: bytes,
    marker: bytes,
    expected_followup: bytes,
    receipts: queue.Queue[dict[str, Any]],
) -> None:
    receipt: dict[str, Any] = {
        "marker": marker.decode("ascii"),
        "followup_matches": False,
        "preface_matches": False,
    }
    listener.settimeout(TIMEOUT)
    try:
        stream, address = listener.accept()
        stream.settimeout(TIMEOUT)
        received = bytearray()
        while len(received) < len(expected_preface):
            chunk = stream.recv(len(expected_preface) - len(received))
            if not chunk:
                break
            received.extend(chunk)
        receipt["loopback_peer"] = address[0] == "127.0.0.1"
        receipt["preface_matches"] = bytes(received) == expected_preface
        followup = bytearray()
        while len(followup) < len(expected_followup):
            chunk = stream.recv(len(expected_followup) - len(followup))
            if not chunk:
                break
            followup.extend(chunk)
        receipt["followup_matches"] = bytes(followup) == expected_followup
        stream.sendall(marker)
        stream.shutdown(socket.SHUT_WR)
        stream.close()
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()
        receipts.put(receipt)


def start_host_target(
    expected_preface: bytes,
    marker: bytes,
) -> tuple[tuple[str, int], threading.Thread, queue.Queue[dict[str, Any]]]:
    listener = bind_listener("127.0.0.1", 0)
    receipts: queue.Queue[dict[str, Any]] = queue.Queue()
    thread = threading.Thread(
        target=host_receipt_server,
        kwargs={
            "listener": listener,
            "expected_preface": expected_preface,
            "marker": marker,
            "expected_followup": FOLLOWUP,
            "receipts": receipts,
        },
        daemon=True,
    )
    thread.start()
    return listener.getsockname(), thread, receipts


def held_host_server(
    listener: socket.socket,
    *,
    expected_preface: bytes,
    active: threading.Event,
    closed: threading.Event,
) -> None:
    listener.settimeout(TIMEOUT)
    stream: socket.socket | None = None
    try:
        stream, _ = listener.accept()
        stream.settimeout(TIMEOUT)
        expected = expected_preface + FOLLOWUP
        received = bytearray()
        while len(received) < len(expected):
            chunk = stream.recv(len(expected) - len(received))
            if not chunk:
                break
            received.extend(chunk)
        if bytes(received) == expected:
            active.set()
        while stream.recv(4096):
            pass
    except (OSError, socket.timeout):
        pass
    finally:
        if stream is not None:
            stream.close()
        listener.close()
        closed.set()


def start_held_host_target(
    expected_preface: bytes,
) -> tuple[tuple[str, int], threading.Thread, threading.Event, threading.Event]:
    listener = bind_listener("127.0.0.1", 0)
    active = threading.Event()
    closed = threading.Event()
    thread = threading.Thread(
        target=held_host_server,
        kwargs={
            "listener": listener,
            "expected_preface": expected_preface,
            "active": active,
            "closed": closed,
        },
        daemon=True,
    )
    thread.start()
    return listener.getsockname(), thread, active, closed


def browser_route(
    address: tuple[str, int],
    hello: bytes,
    *,
    expected_bytes: int,
) -> bytes:
    with socket.create_connection(address, timeout=TIMEOUT) as browser:
        browser.settimeout(TIMEOUT)
        browser.sendall(hello + FOLLOWUP)
        chunks: list[bytes] = []
        while sum(len(chunk) for chunk in chunks) < expected_bytes:
            chunk = browser.recv(expected_bytes - sum(len(item) for item in chunks))
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


def wait_for_file(path: Path, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            else:
                if payload.get("ready") is True:
                    return True
        time.sleep(0.05)
    return False


def wait_for_browser_connections(
    relay: MultiRouteRelay,
    count: int,
    timeout_seconds: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if relay.status().get("browser_connections") == count:
            return True
        time.sleep(0.01)
    return False


def wait_for_not_ready(
    relay: MultiRouteRelay,
    timeout_seconds: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if relay.status().get("ready") is False:
            return True
        time.sleep(0.01)
    return False


def wait_for_active_streams(
    relay: MultiRouteRelay,
    count: int,
    timeout_seconds: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if relay.status().get("active_streams") == count:
            return True
        time.sleep(0.01)
    return False


def reserve_port() -> int:
    listener = bind_listener("127.0.0.1", 0)
    port = int(listener.getsockname()[1])
    listener.close()
    return port


def main() -> int:
    failures: list[str] = []
    openssl = shutil.which("openssl")
    if not openssl:
        print(
            json.dumps(
                {
                    "ok": False,
                    "operation": "relay_daemon_smoke",
                    "reason": "openssl_unavailable",
                    "token_omitted": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    alpha_key = bytes.fromhex("11" * 32)
    beta_key = bytes.fromhex("22" * 32)
    wrong_key = bytes.fromhex("33" * 32)
    alpha_hello = real_client_hello(ALPHA_HOSTNAME)
    beta_hello = real_client_hello(BETA_HOSTNAME)
    transcripts: list[str] = []
    routed_sender, routed_receiver = socket.socketpair()
    try:
        routed_sender.sendall(
            encode_frame(
                RelayFrame("register", "rte_daemon_unknown", 1, 1),
                alpha_key,
            )
        )
        try:
            receive_routed_frame(
                routed_receiver,
                {
                    ALPHA_ROUTE: alpha_key,
                    BETA_ROUTE: beta_key,
                },
            )
        except RelayProtocolError as exc:
            unknown_connector_route_code = exc.code
        else:
            unknown_connector_route_code = ""
    finally:
        routed_sender.close()
        routed_receiver.close()
    require(
        unknown_connector_route_code == "unknown_route",
        "unknown connector route reached a configured key",
        failures,
    )

    with tempfile.TemporaryDirectory(prefix="agentops-relay-daemon-") as temp_dir:
        temp = Path(temp_dir)
        certificate, private_key = generate_certificate(
            openssl,
            temp,
            hostname=RELAY_HOSTNAME,
        )
        client_context = connector_client_context(certificate)
        server_context = connector_server_context(certificate, private_key)
        tombstone_path = temp / "tombstone-state.json"
        stale_store = PersistentEpochStore(
            tombstone_path,
            {ALPHA_ROUTE, BETA_ROUTE},
        )
        concurrent_store = PersistentEpochStore(
            tombstone_path,
            {ALPHA_ROUTE, BETA_ROUTE},
        )
        stale_store.commit(ALPHA_ROUTE, 7)
        concurrent_store.commit(BETA_ROUTE, 3)
        removed_route_store = PersistentEpochStore(
            tombstone_path,
            {BETA_ROUTE},
        )
        removed_route_store.commit(BETA_ROUTE, 4)
        readded_route_store = PersistentEpochStore(
            tombstone_path,
            {ALPHA_ROUTE, BETA_ROUTE},
        )
        require(
            readded_route_store.current(ALPHA_ROUTE) == 7,
            "removed route lost its persisted epoch tombstone",
            failures,
        )
        try:
            readded_route_store.commit(ALPHA_ROUTE, 7)
        except Exception as exc:
            stale_tombstone_rejected = getattr(exc, "code", "") == "stale_epoch"
        else:
            stale_tombstone_rejected = False
        require(stale_tombstone_rejected, "re-added route accepted its old epoch", failures)
        persisted_tombstones = json.loads(tombstone_path.read_text(encoding="utf-8"))
        require(
            persisted_tombstones.get("routes", {}).get(ALPHA_ROUTE) == 7
            and persisted_tombstones.get("routes", {}).get(BETA_ROUTE) == 4,
            "cross-instance epoch commit overwrote another route",
            failures,
        )

        state_path = temp / "relay-state.json"
        epoch_store = PersistentEpochStore(
            state_path,
            {ALPHA_ROUTE, BETA_ROUTE},
        )

        browser_listener = bind_listener("127.0.0.1", 0)
        connector_listener = bind_listener("127.0.0.1", 0)
        browser_address = browser_listener.getsockname()
        connector_address = connector_listener.getsockname()
        relay = MultiRouteRelay(
            browser_listener=browser_listener,
            connector_listener=connector_listener,
            hostnames={
                ALPHA_HOSTNAME: ALPHA_ROUTE,
                BETA_HOSTNAME: BETA_ROUTE,
            },
            route_keys={
                ALPHA_ROUTE: alpha_key,
                BETA_ROUTE: beta_key,
            },
            connector_tls_context=server_context,
            epoch_store=epoch_store,
            max_browser_connections=2,
        )
        relay.start()

        alpha_target, alpha_thread, alpha_receipts = start_host_target(
            alpha_hello,
            ALPHA_MARKER,
        )
        beta_target, beta_thread, beta_receipts = start_host_target(
            beta_hello,
            BETA_MARKER,
        )
        alpha_connector = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=alpha_target,
            route=ALPHA_ROUTE,
            epoch=1,
            key=alpha_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        beta_connector = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=beta_target,
            route=BETA_ROUTE,
            epoch=1,
            key=beta_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        alpha_connector.start()
        beta_connector.start()
        require(alpha_connector.wait_until_ready(TIMEOUT), "alpha control did not register", failures)
        require(beta_connector.wait_until_ready(TIMEOUT), "beta control did not register", failures)
        require(relay.wait_for_active_routes(2, TIMEOUT), "two routes were not simultaneously active", failures)
        require(relay.status().get("handshakes_in_progress") == 0, "completed control handshakes retained socket ownership", failures)

        stalled_browsers = [
            socket.create_connection(browser_address, timeout=TIMEOUT)
            for _ in range(2)
        ]
        require(
            wait_for_browser_connections(relay, 2),
            "browser capacity fixture did not occupy two slots",
            failures,
        )
        excess_browser = socket.create_connection(browser_address, timeout=TIMEOUT)
        excess_browser.settimeout(2.0)
        try:
            excess_rejected = excess_browser.recv(1) == b""
        except (ConnectionResetError, OSError):
            excess_rejected = True
        excess_browser.close()
        require(excess_rejected, "browser connection above capacity was queued", failures)
        for stalled_browser in stalled_browsers:
            stalled_browser.close()
        require(
            wait_for_browser_connections(relay, 0),
            "browser capacity was not released after stalled clients closed",
            failures,
        )

        alpha_response = browser_route(
            browser_address,
            alpha_hello,
            expected_bytes=len(ALPHA_MARKER),
        )
        beta_response = browser_route(
            browser_address,
            beta_hello,
            expected_bytes=len(BETA_MARKER),
        )
        alpha_thread.join(TIMEOUT)
        beta_thread.join(TIMEOUT)
        alpha_receipt = alpha_receipts.get(timeout=TIMEOUT)
        beta_receipt = beta_receipts.get(timeout=TIMEOUT)
        require(relay.status().get("handshakes_in_progress") == 0, "completed data handshakes retained socket ownership", failures)
        require(alpha_response == ALPHA_MARKER, "alpha SNI reached the wrong Host", failures)
        require(beta_response == BETA_MARKER, "beta SNI reached the wrong Host", failures)
        require(alpha_receipt.get("preface_matches") is True, "alpha ClientHello was not forwarded exactly", failures)
        require(beta_receipt.get("preface_matches") is True, "beta ClientHello was not forwarded exactly", failures)
        require(alpha_receipt.get("followup_matches") is True, "alpha ClientHello was forwarded more than once", failures)
        require(beta_receipt.get("followup_matches") is True, "beta ClientHello was forwarded more than once", failures)
        require(alpha_receipt.get("loopback_peer") is True, "alpha target was not Host loopback", failures)
        require(beta_receipt.get("loopback_peer") is True, "beta target was not Host loopback", failures)

        unknown_response = browser_route(
            browser_address,
            real_client_hello(UNKNOWN_HOSTNAME),
            expected_bytes=1,
        )
        require(unknown_response == b"", "unknown SNI received routed application data", failures)

        wrong_connector = HostTunnelConnector(
            relay_address=connector_address,
            host_tls_target=("127.0.0.1", 1),
            route=ALPHA_ROUTE,
            epoch=2,
            key=wrong_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        wrong_connector.start()
        require(not wrong_connector.wait_until_ready(2.0), "wrong route key authenticated", failures)
        require(relay.status().get("active_routes") == 2, "wrong key displaced a valid control", failures)
        wrong_connector.stop()

        first_metadata = relay.metadata.snapshot()
        alpha_connector.stop()
        beta_connector.stop()
        relay.stop()
        require(epoch_store.current(ALPHA_ROUTE) == 1, "alpha epoch was not persisted", failures)
        require(epoch_store.current(BETA_ROUTE) == 1, "beta epoch was not persisted", failures)

        health_browser_listener = bind_listener("127.0.0.1", 0)
        health_connector_listener = bind_listener("127.0.0.1", 0)
        health_relay = MultiRouteRelay(
            browser_listener=health_browser_listener,
            connector_listener=health_connector_listener,
            hostnames={
                ALPHA_HOSTNAME: ALPHA_ROUTE,
                BETA_HOSTNAME: BETA_ROUTE,
            },
            route_keys={
                ALPHA_ROUTE: alpha_key,
                BETA_ROUTE: beta_key,
            },
            connector_tls_context=server_context,
            epoch_store=PersistentEpochStore(
                state_path,
                {ALPHA_ROUTE, BETA_ROUTE},
            ),
        )
        health_relay.start()
        require(health_relay.status().get("ready") is True, "healthy acceptors were not ready", failures)
        health_browser_listener.close()
        require(
            wait_for_not_ready(health_relay),
            "failed browser acceptor retained ready=true",
            failures,
        )
        health_relay.stop()

        active_browser_listener = bind_listener("127.0.0.1", 0)
        active_connector_listener = bind_listener("127.0.0.1", 0)
        active_relay = MultiRouteRelay(
            browser_listener=active_browser_listener,
            connector_listener=active_connector_listener,
            hostnames={ALPHA_HOSTNAME: ALPHA_ROUTE},
            route_keys={ALPHA_ROUTE: alpha_key},
            connector_tls_context=server_context,
            epoch_store=PersistentEpochStore(
                temp / "active-stop-state.json",
                {ALPHA_ROUTE},
            ),
        )
        active_relay.start()
        held_target, held_thread, held_active, held_closed = start_held_host_target(
            alpha_hello,
        )
        held_connector = HostTunnelConnector(
            relay_address=active_connector_listener.getsockname(),
            host_tls_target=held_target,
            route=ALPHA_ROUTE,
            epoch=1,
            key=alpha_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        held_connector.start()
        require(held_connector.wait_until_ready(TIMEOUT), "active-stop control did not register", failures)
        held_browser = socket.create_connection(
            active_browser_listener.getsockname(),
            timeout=TIMEOUT,
        )
        held_browser.settimeout(TIMEOUT)
        held_browser.sendall(alpha_hello + FOLLOWUP)
        require(held_active.wait(TIMEOUT), "active-stop Host stream did not receive browser bytes", failures)
        require(
            wait_for_active_streams(active_relay, 1),
            "active-stop Relay did not report its live stream",
            failures,
        )
        active_stop_started = time.monotonic()
        active_relay.stop()
        active_stop_elapsed = time.monotonic() - active_stop_started
        try:
            held_browser_closed = held_browser.recv(1) == b""
        except (ConnectionResetError, OSError):
            held_browser_closed = True
        held_browser.close()
        held_connector.stop()
        held_thread.join(TIMEOUT)
        require(active_stop_elapsed < 4.0, "active forwarding stop exceeded bound", failures)
        require(held_browser_closed, "active browser stream survived Relay stop", failures)
        require(held_closed.is_set(), "Host target stream survived Relay stop", failures)

        restarted_browser_listener = bind_listener("127.0.0.1", 0)
        restarted_connector_listener = bind_listener("127.0.0.1", 0)
        restarted_connector_address = restarted_connector_listener.getsockname()
        restarted = MultiRouteRelay(
            browser_listener=restarted_browser_listener,
            connector_listener=restarted_connector_listener,
            hostnames={
                ALPHA_HOSTNAME: ALPHA_ROUTE,
                BETA_HOSTNAME: BETA_ROUTE,
            },
            route_keys={
                ALPHA_ROUTE: alpha_key,
                BETA_ROUTE: beta_key,
            },
            connector_tls_context=server_context,
            epoch_store=PersistentEpochStore(
                state_path,
                {ALPHA_ROUTE, BETA_ROUTE},
            ),
        )
        restarted.start()
        stale_connector = HostTunnelConnector(
            relay_address=restarted_connector_address,
            host_tls_target=("127.0.0.1", 1),
            route=ALPHA_ROUTE,
            epoch=1,
            key=alpha_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        stale_connector.start()
        require(not stale_connector.wait_until_ready(2.0), "stale epoch registered after restart", failures)
        require(restarted.status().get("active_routes") == 0, "stale epoch became current route owner", failures)
        stale_connector.stop()

        fresh_hello = real_client_hello(ALPHA_HOSTNAME)
        fresh_target, fresh_thread, fresh_receipts = start_host_target(
            fresh_hello,
            ALPHA_MARKER,
        )
        fresh_connector = HostTunnelConnector(
            relay_address=restarted_connector_address,
            host_tls_target=fresh_target,
            route=ALPHA_ROUTE,
            epoch=2,
            key=alpha_key,
            relay_ssl_context=client_context,
            relay_server_hostname=RELAY_HOSTNAME,
        )
        fresh_connector.start()
        require(fresh_connector.wait_until_ready(TIMEOUT), "fresh epoch did not register after restart", failures)
        fresh_response = browser_route(
            restarted_browser_listener.getsockname(),
            fresh_hello,
            expected_bytes=len(ALPHA_MARKER),
        )
        fresh_thread.join(TIMEOUT)
        fresh_receipt = fresh_receipts.get(timeout=TIMEOUT)
        require(fresh_response == ALPHA_MARKER, "fresh route did not forward after restart", failures)
        require(fresh_receipt.get("preface_matches") is True, "fresh ClientHello changed after restart", failures)
        require(fresh_receipt.get("followup_matches") is True, "fresh ClientHello was duplicated after restart", failures)
        fresh_connector.stop()
        restarted.stop()

        alpha_key_file = temp / "alpha.key"
        beta_key_file = temp / "beta.key"
        alpha_key_file.write_text(alpha_key.hex() + "\n", encoding="ascii")
        beta_key_file.write_text(beta_key.hex() + "\n", encoding="ascii")
        alpha_key_file.chmod(0o600)
        beta_key_file.chmod(0o600)
        cli_state_path = temp / "cli-state.json"
        cli_status_path = temp / "cli-status.json"
        cli_config = temp / "relay.json"
        cli_config_payload = {
            "schema_version": 1,
            "browser_listen": {
                "host": "127.0.0.1",
                "port": reserve_port(),
            },
            "connector_listen": {
                "host": "127.0.0.1",
                "port": reserve_port(),
            },
            "connector_tls": {
                "cert_file": str(certificate),
                "key_file": str(private_key),
            },
            "state_path": str(cli_state_path),
            "status_path": str(cli_status_path),
            "routes": [
                {
                    "hostname": ALPHA_HOSTNAME,
                    "route": ALPHA_ROUTE,
                    "key_file": str(alpha_key_file),
                },
                {
                    "hostname": BETA_HOSTNAME,
                    "route": BETA_ROUTE,
                    "key_file": str(beta_key_file),
                },
            ],
        }
        cli_config.write_text(
            json.dumps(cli_config_payload, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        cli_config.chmod(0o600)
        check = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "check",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((check.stdout, check.stderr))
        require(check.returncode == 0, f"relay check failed: {check.stderr or check.stdout}", failures)

        permissive_key_file = temp / "permissive.key"
        permissive_key_file.write_text(alpha_key.hex() + "\n", encoding="ascii")
        permissive_key_file.chmod(0o644)
        rejected_config_payload = json.loads(json.dumps(cli_config_payload))
        rejected_config_payload["routes"][0]["key_file"] = str(permissive_key_file)
        rejected_config = temp / "relay-permissive-key.json"
        rejected_config.write_text(
            json.dumps(rejected_config_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rejected_config.chmod(0o600)
        rejected_check = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "check",
                "--config",
                str(rejected_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((rejected_check.stdout, rejected_check.stderr))
        require(rejected_check.returncode != 0, "0644 route key passed Relay preflight", failures)
        require(
            "file_permissions_rejected" in rejected_check.stdout,
            "0644 route key did not return bounded permission failure",
            failures,
        )
        reused_key_payload = json.loads(json.dumps(cli_config_payload))
        reused_key_payload["routes"][1]["key_file"] = str(alpha_key_file)
        reused_key_config = temp / "relay-reused-key.json"
        reused_key_config.write_text(
            json.dumps(reused_key_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reused_key_config.chmod(0o600)
        reused_key_check = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "check",
                "--config",
                str(reused_key_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((reused_key_check.stdout, reused_key_check.stderr))
        require(
            reused_key_check.returncode != 0
            and "route_key_reused" in reused_key_check.stdout,
            "duplicate route key passed Relay preflight",
            failures,
        )

        daemon = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "serve",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(wait_for_file(cli_status_path, TIMEOUT), "relay daemon readiness status was not written", failures)
        live_status = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "status",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((live_status.stdout, live_status.stderr))
        require(live_status.returncode == 0, "running Relay status was not ready", failures)
        duplicate_daemon = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "serve",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((duplicate_daemon.stdout, duplicate_daemon.stderr))
        require(
            duplicate_daemon.returncode != 0
            and "relay_instance_active" in duplicate_daemon.stdout,
            "second Relay daemon acquired the same state namespace",
            failures,
        )
        daemon.terminate()
        try:
            daemon_stdout, daemon_stderr = daemon.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon_stdout, daemon_stderr = daemon.communicate(timeout=TIMEOUT)
            failures.append("relay daemon did not stop after SIGTERM")
        transcripts.extend((daemon_stdout or "", daemon_stderr or ""))
        require(daemon.returncode == 0, f"relay daemon SIGTERM exit={daemon.returncode}", failures)
        stopped_status = json.loads(cli_status_path.read_text(encoding="utf-8"))
        require(stopped_status.get("ready") is False, "stopped daemon retained ready=true", failures)
        require(stopped_status.get("stopped") is True, "stopped daemon status missing", failures)
        stopped_status_cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "status",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((stopped_status_cli.stdout, stopped_status_cli.stderr))
        require(stopped_status_cli.returncode != 0, "stopped Relay status remained ready", failures)
        forged_status = dict(stopped_status)
        forged_status.update({
            "pid": 999_999_999,
            "ready": True,
            "stopped": False,
            "updated_at_unix": int(time.time()),
        })
        cli_status_path.write_text(
            json.dumps(forged_status, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        forged_status_cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentops_mis_cli.relay_daemon",
                "status",
                "--config",
                str(cli_config),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=TIMEOUT,
        )
        transcripts.extend((forged_status_cli.stdout, forged_status_cli.stderr))
        require(
            forged_status_cli.returncode != 0
            and "status_stale_or_process_unavailable" in forged_status_cli.stdout,
            "orphaned ready status file was trusted",
            failures,
        )

        transcript = "\n".join(transcripts)
        forbidden = (
            alpha_key.hex(),
            beta_key.hex(),
            ALPHA_MARKER.decode("ascii"),
            BETA_MARKER.decode("ascii"),
        )
        require(not any(value in transcript for value in forbidden), "Relay CLI output leaked key or application marker", failures)
        metadata_text = json.dumps(first_metadata, sort_keys=True)
        require(ALPHA_MARKER.decode("ascii") not in metadata_text, "Relay metadata retained application payload", failures)
        require(BETA_MARKER.decode("ascii") not in metadata_text, "Relay metadata retained application payload", failures)

    result = {
        "ok": not failures,
        "operation": "relay_daemon_smoke",
        "two_host_exact_sni_isolation": alpha_response == ALPHA_MARKER and beta_response == BETA_MARKER,
        "unknown_sni_failed_closed": unknown_response == b"",
        "unknown_connector_route_failed_closed": unknown_connector_route_code
        == "unknown_route",
        "wrong_route_key_failed_closed": not wrong_connector.wait_until_ready(0.01),
        "permissive_key_file_failed_closed": rejected_check.returncode != 0,
        "duplicate_route_key_failed_closed": reused_key_check.returncode != 0,
        "single_daemon_instance_enforced": duplicate_daemon.returncode != 0,
        "epoch_tombstones_preserved": stale_tombstone_rejected,
        "acceptor_failure_clears_ready": health_relay.status().get("ready") is False,
        "active_forwarding_stop_bounded": active_stop_elapsed < 4.0
        and held_browser_closed
        and held_closed.is_set(),
        "restart_stale_epoch_failed_closed": not stale_connector.wait_until_ready(0.01),
        "fresh_epoch_after_restart": fresh_response == ALPHA_MARKER,
        "sigterm_bounded": daemon.returncode == 0,
        "status_liveness_checked": live_status.returncode == 0
        and stopped_status_cli.returncode != 0
        and forged_status_cli.returncode != 0,
        "relay_metadata_payload_free": not any(value in metadata_text for value in ("alpha-host", "beta-host")),
        "browser_connection_capacity_enforced": excess_rejected,
        "database_used": False,
        "deployed_relay_claimed": False,
        "dns_acme_claimed": False,
        "failures": failures,
        "token_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
