"""Disabled-by-default in-process supervisor for an outbound Relay connector.

This test-only slice owns no listener, network configuration, persistent state, or
credentials. A caller may supply a separate crash-safe epoch allocator.
"""
from __future__ import annotations

import ssl
import threading
import time
from collections import deque
from typing import Any, Protocol

from agentops_mis_cli.relay_tunnel import (
    HostTunnelConnector,
    RelayProtocolError,
    _valid_dns_name,
    _valid_network_host,
)


MAX_STATUS_EVENTS = 32
MIN_BACKOFF_SECONDS = 0.01
MAX_BACKOFF_SECONDS = 5.0
MONITOR_INTERVAL_SECONDS = 0.05
_STATES = {"disabled", "starting", "connecting", "connected", "backoff", "failed", "stopped"}


class RelayEpochAllocator(Protocol):
    def next_epoch(self) -> int: ...


class RelayConnectorSupervisor:
    """Reconnect ``HostTunnelConnector`` without persisting secrets or status."""

    def __init__(
        self,
        *,
        relay_address: tuple[str, int],
        host_tls_target: tuple[str, int],
        route: str,
        key: bytes,
        enabled: bool = False,
        initial_epoch: int = 0,
        epoch_allocator: RelayEpochAllocator | None = None,
        relay_ssl_context: ssl.SSLContext | None = None,
        relay_server_hostname: str | None = None,
        connect_timeout_seconds: float = 1.0,
        backoff_initial_seconds: float = 0.05,
        backoff_cap_seconds: float = 1.0,
    ) -> None:
        self._validate_address(
            relay_address,
            "Relay",
            require_loopback=relay_ssl_context is None,
        )
        self._validate_address(host_tls_target, "Host TLS")
        if relay_ssl_context is None and relay_server_hostname is not None:
            raise ValueError("plain Relay transport cannot use a TLS hostname")
        if relay_ssl_context is not None:
            if not isinstance(relay_ssl_context, ssl.SSLContext):
                raise TypeError("Relay TLS context must be an SSLContext")
            if relay_ssl_context.verify_mode != ssl.CERT_REQUIRED or not relay_ssl_context.check_hostname:
                raise ValueError("Relay TLS must verify certificate and hostname")
            if relay_ssl_context.minimum_version < ssl.TLSVersion.TLSv1_2:
                raise ValueError("Relay TLS minimum version must be TLS 1.2 or newer")
            if not isinstance(relay_server_hostname, str) or not _valid_dns_name(relay_server_hostname):
                raise ValueError("Relay TLS server hostname is required")
        if not isinstance(key, (bytes, bytearray)) or len(key) < 32:
            raise RelayProtocolError("weak_tunnel_key")
        if not isinstance(route, str) or not (1 <= len(route) <= 96) or not all(
            char.isascii() and (char.isalnum() or char in "-_.") for char in route
        ):
            raise ValueError("invalid Relay route")
        if not isinstance(initial_epoch, int) or isinstance(initial_epoch, bool) or initial_epoch < 0:
            raise ValueError("initial epoch must be a non-negative integer")
        if epoch_allocator is not None and initial_epoch:
            raise ValueError("persistent epoch allocation cannot use an initial epoch")
        if not (0.1 <= float(connect_timeout_seconds) <= 30.0):
            raise ValueError("connect timeout must be between 0.1 and 30 seconds")
        if not (MIN_BACKOFF_SECONDS <= float(backoff_initial_seconds) <= MAX_BACKOFF_SECONDS):
            raise ValueError("initial backoff is outside the bounded range")
        if not (
            float(backoff_initial_seconds)
            <= float(backoff_cap_seconds)
            <= MAX_BACKOFF_SECONDS
        ):
            raise ValueError("backoff cap is outside the bounded range")

        self._relay_address = relay_address
        self._host_tls_target = host_tls_target
        self._route = route
        self._key = bytes(key)
        self._enabled = bool(enabled)
        self._epoch = initial_epoch
        self._epoch_allocator = epoch_allocator
        self._relay_ssl_context = relay_ssl_context
        self._relay_server_hostname = relay_server_hostname
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        self._backoff_initial_seconds = float(backoff_initial_seconds)
        self._backoff_cap_seconds = float(backoff_cap_seconds)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._startup_complete = threading.Event()
        self._thread: threading.Thread | None = None
        self._connector: HostTunnelConnector | None = None
        self._state = "disabled"
        self._attempts = 0
        self._successful_connections = 0
        self._failure_code: str | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_STATUS_EVENTS)
        self._record_locked("disabled")

    @staticmethod
    def _validate_address(
        address: tuple[str, int],
        label: str,
        *,
        require_loopback: bool = True,
    ) -> None:
        if (
            not isinstance(address, tuple)
            or len(address) != 2
            or not isinstance(address[0], str)
            or not _valid_network_host(address[0])
            or (require_loopback and address[0] != "127.0.0.1")
            or not isinstance(address[1], int)
            or isinstance(address[1], bool)
            or not (1 <= address[1] <= 65535)
        ):
            raise ValueError(f"{label} target must use literal 127.0.0.1 and a valid port")

    def _record_locked(self, state: str, *, backoff_seconds: float | None = None) -> None:
        if state not in _STATES:
            raise ValueError("unapproved supervisor state")
        self._state = state
        self._events.append(
            {
                "attempt": self._attempts,
                "backoff_seconds": backoff_seconds,
                "epoch": self._epoch if self._epoch else None,
                "state": state,
            }
        )

    def start(self) -> bool:
        """Start once when explicitly enabled; never restart after ``stop``."""
        with self._lock:
            if not self._enabled or self._stop.is_set() or self._thread is not None:
                return False
            self._record_locked("starting")
            thread = threading.Thread(target=self._run, daemon=True)
            self._thread = thread
            thread.start()
            return True

    def _run(self) -> None:
        backoff = self._backoff_initial_seconds
        while not self._stop.is_set():
            with self._lock:
                if self._stop.is_set():
                    break
                self._attempts += 1
            try:
                epoch = (
                    self._epoch_allocator.next_epoch()
                    if self._epoch_allocator is not None
                    else self._epoch + 1
                )
            except Exception:
                with self._lock:
                    self._failure_code = "epoch_allocation_failed"
                    self._record_locked("failed")
                    self._startup_complete.set()
                return
            if (
                not isinstance(epoch, int)
                or isinstance(epoch, bool)
                or epoch <= self._epoch
            ):
                with self._lock:
                    self._failure_code = "invalid_allocated_epoch"
                    self._record_locked("failed")
                    self._startup_complete.set()
                return
            with self._lock:
                if self._stop.is_set():
                    break
                self._epoch = epoch
                self._record_locked("connecting")

            try:
                connector = HostTunnelConnector(
                    relay_address=self._relay_address,
                    host_tls_target=self._host_tls_target,
                    route=self._route,
                    epoch=epoch,
                    key=self._key,
                    relay_ssl_context=self._relay_ssl_context,
                    relay_server_hostname=self._relay_server_hostname,
                )
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._connector = connector
                    # Publish and start atomically with respect to stop(), so a
                    # stop cannot land between ownership publication and startup.
                    connector.start()
                    self._startup_complete.set()
            except Exception:
                with self._lock:
                    self._connector = None
                    self._failure_code = "connector_start_failed"
                    self._record_locked("failed")
                    self._startup_complete.set()
                return
            connected = connector.wait_until_ready(self._connect_timeout_seconds)

            if connected and not self._stop.is_set():
                with self._lock:
                    self._successful_connections += 1
                    self._record_locked("connected")
                backoff = self._backoff_initial_seconds
                while not self._stop.wait(MONITOR_INTERVAL_SECONDS):
                    if not connector.wait_until_ready(0.0):
                        break

            connector.stop(timeout_seconds=1.0)
            with self._lock:
                if self._connector is connector:
                    self._connector = None
            if self._stop.is_set():
                break

            with self._lock:
                self._record_locked("backoff", backoff_seconds=backoff)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2.0, self._backoff_cap_seconds)

        with self._lock:
            self._connector = None
            self._record_locked("stopped")
            self._startup_complete.set()

    def wait_until_started(self, timeout_seconds: float = 3.0) -> bool:
        """Wait until the first durable epoch exists or startup fails closed."""
        timeout = max(0.0, min(float(timeout_seconds), 10.0))
        if not self._startup_complete.wait(timeout):
            return False
        with self._lock:
            return self._epoch > 0 and self._state in {
                "connecting",
                "connected",
                "backoff",
            }

    def wait_for_connections(self, minimum: int, timeout_seconds: float = 5.0) -> bool:
        """Wait for a bounded count without exposing connector identities."""
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            raise ValueError("minimum connections must be a positive integer")
        deadline = threading.Event()
        remaining = max(0.0, min(float(timeout_seconds), 30.0))
        step = min(MONITOR_INTERVAL_SECONDS, remaining) if remaining else 0.0
        while remaining > 0.0:
            with self._lock:
                if self._successful_connections >= minimum:
                    return True
                if self._stop.is_set():
                    return False
            deadline.wait(step)
            remaining -= step
            step = min(MONITOR_INTERVAL_SECONDS, remaining)
        with self._lock:
            return self._successful_connections >= minimum

    def status(self) -> dict[str, Any]:
        """Return only allowlisted, bounded, non-sensitive process metadata."""
        with self._lock:
            return {
                "connect_attempts": self._attempts,
                "current_epoch": self._epoch if self._epoch else None,
                "enabled": self._enabled,
                "events": [dict(event) for event in self._events],
                "failure_code": self._failure_code,
                "relay_tls_enabled": self._relay_ssl_context is not None,
                "limitations": {
                    "crash_persistent_epoch": self._epoch_allocator is not None,
                    "deployed_relay": False,
                    "dns_sni_certificate_lifecycle": False,
                    "exactly_once_transport": False,
                    "tailscale_changed": False,
                },
                "state": self._state,
                "successful_connections": self._successful_connections,
            }

    def stop(self, timeout_seconds: float = 3.0) -> bool:
        """Permanently stop this instance and bound shutdown latency."""
        deadline = time.monotonic() + max(0.0, min(float(timeout_seconds), 10.0))
        self._stop.set()
        self._startup_complete.set()
        with self._lock:
            connector = self._connector
            thread = self._thread
            if thread is None:
                self._record_locked("stopped")
        if connector is not None:
            connector.stop(timeout_seconds=min(1.0, max(0.0, deadline - time.monotonic())))
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            stopped = self._thread is None or not self._thread.is_alive()
            if stopped and self._state != "stopped":
                self._record_locked("stopped")
            return stopped
