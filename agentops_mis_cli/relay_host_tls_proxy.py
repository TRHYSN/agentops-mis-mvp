"""Loopback-only Host TLS termination for the outbound Relay tunnel."""
from __future__ import annotations

import select
import socket
import ssl
import threading
import time
from typing import Any

from agentops_mis_cli.relay_tunnel import (
    BoundedRelayMetadata,
    IO_TIMEOUT_SECONDS,
    _close_socket,
    _valid_dns_name,
)


MAX_ACTIVE_CONNECTIONS = 32
MAX_PROXY_BUFFER_BYTES = 256 * 1024
_FAILURE_STAGES = ("backend_connect", "forwarding", "tls_handshake")
_PROXY_FAILURE_CODES = {
    "accept_failed",
    "accept_thread_start_failed",
    "connection_thread_start_failed",
}
_HANDSHAKE_FAILURE_KINDS = ("os_error", "tls_protocol", "unexpected_eof")


def _clean_tls_shutdown(
    stream: ssl.SSLSocket,
) -> tuple[bool, socket.socket | None, bool]:
    deadline = time.monotonic() + min(IO_TIMEOUT_SECONDS, 2.0)
    close_notify_sent = False
    while time.monotonic() < deadline:
        try:
            return True, stream.unwrap(), False
        except ssl.SSLWantReadError:
            # OpenSSL has sent our close_notify. Give the peer time to read it
            # and acknowledge before the tunnel data socket is closed; closing
            # immediately can race the queued alert on slower Linux runners.
            close_notify_sent = True
            try:
                readable, _, _ = select.select(
                    [stream],
                    [],
                    [],
                    max(0.0, deadline - time.monotonic()),
                )
            except (OSError, ValueError):
                return False, None, True
            if not readable:
                return False, None, True
        except ssl.SSLWantWriteError:
            try:
                _, writable, _ = select.select(
                    [],
                    [stream],
                    [],
                    max(0.0, deadline - time.monotonic()),
                )
            except (OSError, ValueError):
                return False, None, close_notify_sent
            if not writable:
                return False, None, close_notify_sent
        except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
            # A peer is allowed to close its transport after consuming our
            # close_notify without sending a reciprocal alert. The strict
            # client smoke proves our alert was received; do not turn that
            # one-way clean shutdown into a forwarding failure.
            return close_notify_sent, None, close_notify_sent
        except (OSError, ssl.SSLError):
            return False, None, close_notify_sent
    # Receiving the peer's reciprocal close_notify is optional here. Once our
    # alert has been queued and given the bounded drain window, close without
    # SHUT_RDWR so Linux cannot discard it.
    return close_notify_sent, None, close_notify_sent


def _perform_tls_handshake(
    stream: ssl.SSLSocket,
    stop: threading.Event,
) -> None:
    deadline = time.monotonic() + IO_TIMEOUT_SECONDS
    stream.setblocking(False)
    while not stop.is_set():
        try:
            stream.do_handshake()
            return
        except ssl.SSLWantReadError:
            readable = [stream]
            writable: list[socket.socket] = []
        except ssl.SSLWantWriteError:
            readable = []
            writable = [stream]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("Host TLS handshake timed out")
        try:
            select.select(readable, writable, [], min(0.1, remaining))
        except (OSError, ValueError) as exc:
            raise OSError("Host TLS handshake socket unavailable") from exc
    raise OSError("Host TLS proxy stopping")


def _forward_tls_to_tcp(
    tls_stream: ssl.SSLSocket,
    backend: socket.socket,
    metadata: BoundedRelayMetadata,
) -> bool:
    to_backend = bytearray()
    to_browser = bytearray()
    browser_read_open = True
    backend_read_open = True
    backend_write_open = True
    browser_to_host_bytes = 0
    host_to_browser_bytes = 0
    failed = False
    raw_stream: socket.socket | None = None
    tls_close_only = False
    deadline = time.monotonic() + IO_TIMEOUT_SECONDS
    tls_stream.setblocking(False)
    backend.setblocking(False)

    try:
        while not failed:
            if not backend_read_open and not to_browser:
                if to_backend:
                    failed = True
                break
            if not browser_read_open and not to_backend and backend_write_open:
                try:
                    backend.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                backend_write_open = False

            readable: list[socket.socket] = []
            writable: list[socket.socket] = []
            if browser_read_open and len(to_backend) < MAX_PROXY_BUFFER_BYTES:
                readable.append(tls_stream)
            if backend_read_open and len(to_browser) < MAX_PROXY_BUFFER_BYTES:
                readable.append(backend)
            if to_browser:
                writable.append(tls_stream)
            if to_backend and backend_write_open:
                writable.append(backend)
            if not readable and not writable:
                failed = backend_read_open or bool(to_backend or to_browser)
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failed = True
                break
            ready_read, ready_write, _ = select.select(
                readable,
                writable,
                [],
                remaining,
            )
            if not ready_read and not ready_write:
                failed = True
                break

            activity = False
            if tls_stream in ready_read:
                try:
                    chunk = tls_stream.recv(
                        min(64 * 1024, MAX_PROXY_BUFFER_BYTES - len(to_backend))
                    )
                except ssl.SSLWantReadError:
                    chunk = None
                except (ssl.SSLWantWriteError, ssl.SSLEOFError, OSError):
                    failed = True
                    chunk = None
                if chunk:
                    to_backend.extend(chunk)
                    activity = True
                elif chunk == b"":
                    browser_read_open = False

            if backend in ready_read:
                try:
                    chunk = backend.recv(
                        min(64 * 1024, MAX_PROXY_BUFFER_BYTES - len(to_browser))
                    )
                except BlockingIOError:
                    chunk = None
                except OSError:
                    failed = True
                    chunk = None
                if chunk:
                    to_browser.extend(chunk)
                    activity = True
                elif chunk == b"":
                    backend_read_open = False

            if backend in ready_write and to_backend:
                try:
                    sent = backend.send(to_backend)
                except BlockingIOError:
                    sent = 0
                except OSError:
                    failed = True
                    sent = 0
                if sent:
                    del to_backend[:sent]
                    browser_to_host_bytes += sent
                    activity = True

            if tls_stream in ready_write and to_browser:
                try:
                    sent = tls_stream.send(to_browser)
                except ssl.SSLWantWriteError:
                    sent = 0
                except (ssl.SSLWantReadError, ssl.SSLError, OSError):
                    failed = True
                    sent = 0
                if sent:
                    del to_browser[:sent]
                    host_to_browser_bytes += sent
                    activity = True

            if activity:
                deadline = time.monotonic() + IO_TIMEOUT_SECONDS

        success = bool(
            not failed
            and not backend_read_open
            and not to_backend
            and not to_browser
        )
        if success:
            success, raw_stream, tls_close_only = _clean_tls_shutdown(tls_stream)
        metadata.record(
            "forwarded" if success else "failed",
            "browser_to_host",
            browser_to_host_bytes,
        )
        metadata.record(
            "forwarded" if success else "failed",
            "host_to_browser",
            host_to_browser_bytes,
        )
        return success
    finally:
        if raw_stream is not None:
            _close_socket(raw_stream)
        elif tls_close_only:
            try:
                tls_stream.close()
            except OSError:
                pass
        else:
            _close_socket(tls_stream)
        _close_socket(backend)


class HostTlsProxy:
    """Terminate browser TLS at a loopback listener and proxy to Host HTTP."""

    def __init__(
        self,
        *,
        listener: socket.socket,
        backend_target: tuple[str, int],
        tls_context: ssl.SSLContext,
        expected_server_hostname: str,
    ) -> None:
        try:
            listener_address = listener.getsockname()
        except OSError as exc:
            raise ValueError("Host TLS listener must already be bound") from exc
        if (
            listener.family != socket.AF_INET
            or listener_address[0] != "127.0.0.1"
            or not (1 <= int(listener_address[1]) <= 65535)
        ):
            raise ValueError("Host TLS listener must use literal 127.0.0.1")
        if (
            not isinstance(backend_target, tuple)
            or len(backend_target) != 2
            or backend_target[0] != "127.0.0.1"
            or not isinstance(backend_target[1], int)
            or isinstance(backend_target[1], bool)
            or not (1 <= backend_target[1] <= 65535)
        ):
            raise ValueError("Host HTTP target must use literal 127.0.0.1")
        if not isinstance(tls_context, ssl.SSLContext):
            raise TypeError("Host TLS context must be an SSLContext")
        if tls_context.minimum_version < ssl.TLSVersion.TLSv1_2:
            raise ValueError("Host TLS minimum version must be TLS 1.2 or newer")
        if not _valid_dns_name(expected_server_hostname):
            raise ValueError("invalid Host TLS server hostname")

        self._listener = listener
        self._backend_target = backend_target
        self._tls_context = tls_context
        self._expected_server_hostname = expected_server_hostname.rstrip(".").lower()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._connection_threads: set[threading.Thread] = set()
        self._active_sockets: set[socket.socket] = set()
        self._slots = threading.BoundedSemaphore(MAX_ACTIVE_CONNECTIONS)
        self._accepted_connections = 0
        self._rejected_connections = 0
        self._failure_counts = {stage: 0 for stage in _FAILURE_STAGES}
        self._handshake_failure_counts = {
            kind: 0 for kind in _HANDSHAKE_FAILURE_KINDS
        }
        self._failure_code: str | None = None
        self._stop_complete = False
        self.metadata = BoundedRelayMetadata()

        def validate_sni(_stream: ssl.SSLSocket, server_name: str | None, _context: ssl.SSLContext):
            normalized = (server_name or "").rstrip(".").lower()
            if normalized != self._expected_server_hostname:
                return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
            return None

        self._tls_context.set_servername_callback(validate_sni)

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return False
            thread = threading.Thread(target=self._run, daemon=True)
            self._thread = thread
            try:
                thread.start()
            except RuntimeError:
                self._thread = None
                self._failure_code = "accept_thread_start_failed"
                self._ready.clear()
                return False
            return True

    def _fail(self, code: str) -> None:
        if code not in _PROXY_FAILURE_CODES:
            raise ValueError("unapproved Host TLS proxy failure code")
        with self._lock:
            self._failure_code = code
            self._ready.clear()
        _close_socket(self._listener)
    def _run(self) -> None:
        self._listener.settimeout(0.2)
        self._ready.set()
        while not self._stop.is_set():
            try:
                browser, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self._fail("accept_failed")
                break
            browser.settimeout(IO_TIMEOUT_SECONDS)
            if not self._slots.acquire(blocking=False):
                with self._lock:
                    self._rejected_connections += 1
                self.metadata.record("rejected", "data")
                _close_socket(browser)
                continue

            def handle(stream: socket.socket = browser) -> None:
                try:
                    self._handle_connection(stream)
                finally:
                    self._slots.release()
                    with self._lock:
                        self._connection_threads.discard(threading.current_thread())

            connection_thread = threading.Thread(target=handle, daemon=True)
            with self._lock:
                if self._stop.is_set():
                    self._slots.release()
                    _close_socket(browser)
                    break
                self._connection_threads.add(connection_thread)
                self._active_sockets.add(browser)
            try:
                connection_thread.start()
            except RuntimeError:
                with self._lock:
                    self._connection_threads.discard(connection_thread)
                    self._active_sockets.discard(browser)
                    self._rejected_connections += 1
                self._slots.release()
                _close_socket(browser)
                self._fail("connection_thread_start_failed")
                break

    def _handle_connection(self, browser: socket.socket) -> None:
        tls_stream: ssl.SSLSocket | None = None
        backend: socket.socket | None = None
        failure_stage = "tls_handshake"
        try:
            tls_stream = self._tls_context.wrap_socket(
                browser,
                server_side=True,
                do_handshake_on_connect=False,
            )
            close_after_swap = False
            with self._lock:
                self._active_sockets.discard(browser)
                if self._stop.is_set():
                    close_after_swap = True
                else:
                    self._active_sockets.add(tls_stream)
            if close_after_swap:
                _close_socket(tls_stream)
                raise OSError("proxy stopping")
            _perform_tls_handshake(tls_stream, self._stop)
            failure_stage = "backend_connect"
            backend = socket.create_connection(self._backend_target, timeout=IO_TIMEOUT_SECONDS)
            with self._lock:
                if self._stop.is_set():
                    raise OSError("proxy stopping")
                self._active_sockets.add(backend)
                self._accepted_connections += 1
            failure_stage = "forwarding"
            self.metadata.record("authenticated", "data")
            forwarded = _forward_tls_to_tcp(tls_stream, backend, self.metadata)
            self.metadata.record("closed" if forwarded else "failed", "data")
            if not forwarded:
                with self._lock:
                    self._failure_counts["forwarding"] += 1
        except (OSError, ssl.SSLError) as exc:
            with self._lock:
                self._rejected_connections += 1
                self._failure_counts[failure_stage] += 1
                if failure_stage == "tls_handshake":
                    if isinstance(exc, ssl.SSLEOFError):
                        handshake_failure_kind = "unexpected_eof"
                    elif isinstance(exc, ssl.SSLError):
                        handshake_failure_kind = "tls_protocol"
                    else:
                        handshake_failure_kind = "os_error"
                    self._handshake_failure_counts[handshake_failure_kind] += 1
            self.metadata.record("rejected", "data")
            _close_socket(tls_stream or browser)
            _close_socket(backend)
        finally:
            with self._lock:
                self._active_sockets.discard(browser)
                if tls_stream is not None:
                    self._active_sockets.discard(tls_stream)
                if backend is not None:
                    self._active_sockets.discard(backend)

    def wait_until_ready(self, timeout_seconds: float = 5.0) -> bool:
        ready = self._ready.wait(max(0.0, min(float(timeout_seconds), 30.0)))
        with self._lock:
            return bool(
                ready
                and self._failure_code is None
                and not self._stop.is_set()
                and self._thread is not None
                and self._thread.is_alive()
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._failure_code is not None:
                state = "failed"
            elif self._stop.is_set():
                state = "stopped" if self._stop_complete else "stopping"
            elif self._ready.is_set() and self._thread is not None and self._thread.is_alive():
                state = "ready"
            else:
                state = "starting"
            return {
                "accepted_connections": self._accepted_connections,
                "accept_thread_alive": bool(
                    self._thread is not None and self._thread.is_alive()
                ),
                "active_connections": len(self._connection_threads),
                "connection_threads_alive": sum(
                    1 for item in self._connection_threads if item.is_alive()
                ),
                "events": self.metadata.snapshot(),
                "failure_code": self._failure_code,
                "failure_counts": dict(self._failure_counts),
                "handshake_failure_counts": dict(self._handshake_failure_counts),
                "limitations": {
                    "certificate_lifecycle": False,
                    "deployed_relay": False,
                    "literal_loopback_only": True,
                    "tailscale_changed": False,
                },
                "ready": state == "ready",
                "rejected_connections": self._rejected_connections,
                "state": state,
            }

    def stop(self, timeout_seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, min(float(timeout_seconds), 10.0))
        self._stop.set()
        _close_socket(self._listener)
        with self._lock:
            active_sockets = list(self._active_sockets)
            connection_threads = list(self._connection_threads)
            thread = self._thread
        for stream in active_sockets:
            _close_socket(stream)
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        for connection_thread in connection_threads:
            connection_thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            stopped = bool(
                (thread is None or not thread.is_alive())
                and not any(item.is_alive() for item in self._connection_threads)
            )
            self._stop_complete = stopped
            return stopped


def bind_loopback_listener(port: int) -> socket.socket:
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ValueError("Host TLS listener port is invalid")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(MAX_ACTIVE_CONNECTIONS)
        return listener
    except Exception:
        _close_socket(listener)
        raise
