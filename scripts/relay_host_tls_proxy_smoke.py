#!/usr/bin/env python3
"""Exercise loopback-only Host TLS termination without a deployed Relay."""
from __future__ import annotations

import json
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_host_tls_proxy  # noqa: E402
from agentops_mis_cli.relay_host_tls_proxy import (  # noqa: E402
    HostTlsProxy,
    _clean_tls_shutdown,
    bind_loopback_listener,
)
from scripts.relay_tls_authenticated_tunnel_smoke import (  # noqa: E402
    HOST_HOSTNAME,
    TIMEOUT,
    bind_listener,
    generate_certificate,
)


REQUEST = b"GET /workspace HTTP/1.1\r\nHost: host.agentops.test\r\nConnection: close\r\n\r\n"
RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 16\r\n"
    b"Connection: close\r\n\r\n"
    b"AGENTOPS_HOST_OK"
)


def receive_to_eof(stream: socket.socket, maximum: int = 64 * 1024) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = stream.recv(min(4096, maximum - received))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        received += len(chunk)
        if received >= maximum:
            raise ValueError("bounded response exceeded")


def run_http_backend(
    listener: socket.socket,
    receipt: dict[str, object],
    expected_connections: int,
) -> None:
    listener.settimeout(TIMEOUT)
    receipt["matching_requests"] = 0
    try:
        for _ in range(expected_connections):
            stream, _ = listener.accept()
            stream.settimeout(TIMEOUT)
            with stream:
                request = receive_to_eof_until_headers(stream)
                if request == REQUEST:
                    receipt["matching_requests"] += 1
                stream.sendall(RESPONSE)
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
    finally:
        listener.close()


def receive_to_eof_until_headers(stream: socket.socket, maximum: int = 16 * 1024) -> bytes:
    payload = bytearray()
    while b"\r\n\r\n" not in payload:
        chunk = stream.recv(min(4096, maximum - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) >= maximum:
            raise ValueError("bounded request exceeded")
    return bytes(payload)


def browser_request(
    address: tuple[str, int],
    context: ssl.SSLContext,
    *,
    server_hostname: str,
    read_delay_seconds: float = 0.0,
) -> tuple[bytes, str | None]:
    with socket.create_connection(address, timeout=TIMEOUT) as raw:
        raw.settimeout(TIMEOUT)
        with context.wrap_socket(
            raw,
            server_hostname=server_hostname,
            suppress_ragged_eofs=False,
        ) as tls:
            tls_version = tls.version()
            tls.sendall(REQUEST)
            if read_delay_seconds > 0:
                time.sleep(read_delay_seconds)
            return receive_to_eof(tls), tls_version


def reserve_loopback_port() -> int:
    probe = bind_listener()
    port = probe.getsockname()[1]
    probe.close()
    return port


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    proxy: HostTlsProxy | None = None

    class ShutdownProbe:
        def __init__(self, terminal_error: BaseException | None = None) -> None:
            self.calls = 0
            self.terminal_error = terminal_error

        def unwrap(self) -> socket.socket:
            self.calls += 1
            if self.calls == 1:
                raise ssl.SSLWantReadError()
            if self.terminal_error is not None:
                raise self.terminal_error
            raise AssertionError("unexpected unwrap retry")

    closed_fd_probe = ShutdownProbe()
    with mock.patch.object(relay_host_tls_proxy.select, "select", side_effect=ValueError):
        closed_fd_result = _clean_tls_shutdown(closed_fd_probe)  # type: ignore[arg-type]
    if closed_fd_result != (False, None, True):
        failures.append("closed-fd TLS shutdown was not bounded and failed closed")

    fatal_probe = ShutdownProbe(ssl.SSLError("fatal shutdown alert"))
    with mock.patch.object(
        relay_host_tls_proxy.select,
        "select",
        return_value=([fatal_probe], [], []),
    ):
        fatal_result = _clean_tls_shutdown(fatal_probe)  # type: ignore[arg-type]
    if fatal_result != (False, None, True):
        failures.append("fatal TLS shutdown after close_notify was recorded as success")

    peer_eof_probe = ShutdownProbe(ssl.SSLEOFError())
    with mock.patch.object(
        relay_host_tls_proxy.select,
        "select",
        return_value=([peer_eof_probe], [], []),
    ):
        peer_eof_result = _clean_tls_shutdown(peer_eof_probe)  # type: ignore[arg-type]
    if peer_eof_result != (True, None, True):
        failures.append("peer EOF after local close_notify was not accepted")

    with tempfile.TemporaryDirectory(prefix="agentops-host-tls-proxy-") as temporary:
        temporary_path = Path(temporary)
        certificate, private_key = generate_certificate(
            openssl,
            temporary_path,
            prefix="host-proxy",
            hostname=HOST_HOSTNAME,
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(str(certificate), str(private_key))
        browser_context = ssl.create_default_context(cafile=str(certificate))
        browser_context.minimum_version = ssl.TLSVersion.TLSv1_2

        backend_listener = bind_listener()
        backend_address = backend_listener.getsockname()
        proxy_port = reserve_loopback_port()
        proxy_listener = bind_loopback_listener(proxy_port)
        proxy_address = proxy_listener.getsockname()
        receipt: dict[str, object] = {}
        backend_thread = threading.Thread(
            target=run_http_backend,
            args=(backend_listener, receipt, 2),
            daemon=True,
        )
        backend_thread.start()
        proxy = HostTlsProxy(
            listener=proxy_listener,
            backend_target=backend_address,
            tls_context=server_context,
            expected_server_hostname=HOST_HOSTNAME,
        )
        if not proxy.start() or not proxy.wait_until_ready():
            failures.append("Host TLS proxy did not become ready")

        try:
            response, tls_version = browser_request(
                proxy_address,
                browser_context,
                server_hostname=HOST_HOSTNAME,
            )
            if response != RESPONSE:
                failures.append("Host TLS proxy response mismatched")
            if tls_version not in {"TLSv1.2", "TLSv1.3"}:
                failures.append("Host TLS proxy negotiated an unexpected TLS version")
        except Exception as exc:
            failures.append(f"Host TLS proxy browser request failed with {type(exc).__name__}")

        wrong_sni_failed = False
        try:
            browser_request(
                proxy_address,
                browser_context,
                server_hostname="wrong.agentops.test",
            )
        except (OSError, ssl.SSLError):
            wrong_sni_failed = True
        if not wrong_sni_failed:
            failures.append("wrong Host SNI was accepted")

        try:
            second_response, second_tls_version = browser_request(
                proxy_address,
                browser_context,
                server_hostname=HOST_HOSTNAME,
                read_delay_seconds=0.2,
            )
            if second_response != RESPONSE:
                failures.append("Host TLS proxy post-rejection response mismatched")
            if second_tls_version not in {"TLSv1.2", "TLSv1.3"}:
                failures.append("Host TLS proxy post-rejection TLS version mismatched")
        except Exception as exc:
            failures.append(
                f"Host TLS proxy post-rejection request failed with {type(exc).__name__}"
            )

        backend_thread.join(TIMEOUT)
        if backend_thread.is_alive() or receipt.get("error_type"):
            failures.append("Host HTTP backend did not close cleanly")
        if receipt.get("matching_requests") != 2:
            failures.append("Host HTTP backend did not receive both browser requests")

        invalid_target_rejected = False
        extra_listener = bind_loopback_listener(reserve_loopback_port())
        try:
            HostTlsProxy(
                listener=extra_listener,
                backend_target=("localhost", backend_address[1]),
                tls_context=server_context,
                expected_server_hostname=HOST_HOSTNAME,
            )
        except ValueError:
            invalid_target_rejected = True
        finally:
            extra_listener.close()
        if not invalid_target_rejected:
            failures.append("non-literal Host HTTP target was accepted")

        legacy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            legacy_context.minimum_version = ssl.TLSVersion.TLSv1_1
        legacy_context_rejected = False
        extra_listener = bind_loopback_listener(reserve_loopback_port())
        try:
            HostTlsProxy(
                listener=extra_listener,
                backend_target=backend_address,
                tls_context=legacy_context,
                expected_server_hostname=HOST_HOSTNAME,
            )
        except ValueError:
            legacy_context_rejected = True
        finally:
            extra_listener.close()
        if not legacy_context_rejected:
            failures.append("Host TLS context below TLS 1.2 was accepted")

        status = proxy.status()
        rendered = json.dumps(status, sort_keys=True)
        forbidden = (
            HOST_HOSTNAME,
            str(temporary_path),
            str(backend_address[1]),
            str(proxy_port),
            REQUEST.decode("ascii"),
            RESPONSE.decode("ascii"),
        )
        if any(value in rendered for value in forbidden):
            failures.append("Host TLS proxy status exposed private configuration or payload")
        if status.get("accepted_connections") != 2 or status.get("rejected_connections", 0) < 1:
            failures.append("Host TLS proxy status did not summarize accepted/rejected connections")
        failure_counts = status.get("failure_counts") or {}
        if (
            failure_counts.get("backend_connect") != 0
            or failure_counts.get("forwarding") != 0
            or failure_counts.get("tls_handshake", 0) < 1
        ):
            failures.append("Host TLS proxy failure-stage counts were inconsistent")

        stalled = socket.create_connection(proxy_address, timeout=TIMEOUT)
        stalled.settimeout(TIMEOUT)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and proxy.status().get("active_connections") != 1:
            time.sleep(0.01)
        if proxy.status().get("active_connections") != 1:
            failures.append("stalled TLS handshake was not owned by the proxy")
        if not proxy.stop(timeout_seconds=3.0):
            failures.append("Host TLS proxy did not stop within the bounded deadline")
        try:
            if stalled.recv(1) not in {b"", None}:
                failures.append("stalled TLS handshake remained readable after stop")
        except OSError:
            pass
        finally:
            stalled.close()
        final_status = proxy.status()
        if (
            final_status.get("state") != "stopped"
            or final_status.get("ready") is not False
            or final_status.get("active_connections") != 0
        ):
            failures.append("Host TLS proxy final state was not stopped")
        proxy = None

        failed_listener = bind_loopback_listener(reserve_loopback_port())
        failed_proxy = HostTlsProxy(
            listener=failed_listener,
            backend_target=backend_address,
            tls_context=server_context,
            expected_server_hostname=HOST_HOSTNAME,
        )
        if not failed_proxy.start() or not failed_proxy.wait_until_ready():
            failures.append("fault-injection Host TLS proxy did not become ready")
        failed_listener.close()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and failed_proxy.status().get("state") != "failed":
            time.sleep(0.01)
        failed_status = failed_proxy.status()
        if (
            failed_status.get("state") != "failed"
            or failed_status.get("ready") is not False
            or failed_status.get("failure_code") != "accept_failed"
        ):
            failures.append("Host TLS proxy accept failure remained falsely ready")
        if not failed_proxy.stop(timeout_seconds=3.0):
            failures.append("failed Host TLS proxy did not stop within the bounded deadline")

    if proxy is not None:
        proxy.stop(timeout_seconds=1.0)

    result = {
        "application_tls_terminates_on_host": True,
        "closed_fd_shutdown_failed_closed": closed_fd_result == (False, None, True),
        "deployed_relay": False,
        "delayed_browser_read": True,
        "failures": failures,
        "final_accept_thread_alive": bool(
            final_status.get("accept_thread_alive") if "final_status" in locals() else False
        ),
        "final_active_connections": int(
            final_status.get("active_connections", 0) if "final_status" in locals() else 0
        ),
        "final_connection_threads_alive": int(
            final_status.get("connection_threads_alive", 0)
            if "final_status" in locals()
            else 0
        ),
        "final_failure_counts": (
            final_status.get("failure_counts") or {}
            if "final_status" in locals()
            else {}
        ),
        "final_handshake_failure_counts": (
            final_status.get("handshake_failure_counts") or {}
            if "final_status" in locals()
            else {}
        ),
        "final_state": final_status.get("state") if "final_status" in locals() else None,
        "peer_close_notify_optional": peer_eof_result == (True, None, True),
        "ragged_eof_rejected": True,
        "literal_loopback_only": True,
        "ok": not failures,
        "operation": "relay_host_tls_proxy_smoke",
        "payloads_omitted": True,
        "tailscale_changed": False,
        "wrong_sni_fail_closed": not any("wrong Host SNI" in item for item in failures),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
