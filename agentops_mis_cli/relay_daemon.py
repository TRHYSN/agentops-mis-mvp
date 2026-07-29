"""Deployable non-authority Relay daemon for browser-to-Host L4 transport."""
from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import os
import signal
import socket
import ssl
import stat
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agentops_mis_cli.relay_sni_router import (
    BoundedSniRouter,
    MAX_ROUTE_COUNT,
    SniRoutingError,
    normalize_dns_hostname,
)
from agentops_mis_cli.relay_tunnel import (
    HEX_ID_LENGTH,
    IO_TIMEOUT_SECONDS,
    MAX_CONNECTOR_HANDSHAKES,
    MAX_PENDING_STREAMS,
    MAX_REPLAY_EPOCHS,
    PAIR_TIMEOUT_SECONDS,
    BoundedRelayMetadata,
    RelayFrame,
    RelayProtocolError,
    _PendingBrowser,
    _close_socket,
    _forward_bidirectional,
    _validate_frame,
    receive_routed_frame,
    send_frame,
)


MAX_CONFIG_BYTES = 64 * 1024
MAX_KEY_FILE_BYTES = 512
MAX_CANONICAL_PATH_CHARS = 4096
MAX_JSON_INTEGER_DIGITS = 20
MAX_DAEMON_ROUTES = 256
MAX_BROWSER_CONNECTIONS = 256
STATE_SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 1


class RelayDaemonError(RuntimeError):
    """A bounded operational failure safe for local service output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RelayRouteConfig:
    hostname: str
    route: str
    key_file: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class RelayDaemonConfig:
    browser_host: str
    browser_port: int
    connector_host: str
    connector_port: int
    connector_cert_file: Path
    connector_key_file: Path = field(repr=False)
    state_path: Path
    status_path: Path
    routes: tuple[RelayRouteConfig, ...]


def _safe_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _emit_json(payload: Mapping[str, Any]) -> bool:
    try:
        print(_safe_json(payload), flush=True)
        return True
    except (BrokenPipeError, OSError):
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass
        return False


def _require_trusted_parent(path: Path) -> None:
    try:
        parent_stat = path.parent.lstat()
    except OSError as exc:
        raise RelayDaemonError("file_parent_untrusted") from exc
    if (
        stat.S_ISLNK(parent_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid not in {0, os.geteuid()}
        or parent_stat.st_mode & 0o022
    ):
        raise RelayDaemonError("file_parent_untrusted")


def _read_bounded_file(
    path: Path,
    *,
    max_bytes: int,
    private: bool,
) -> bytes:
    if not path.is_absolute():
        raise RelayDaemonError("path_must_be_absolute")
    _require_trusted_parent(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RelayDaemonError("file_not_regular")
        if file_stat.st_uid not in {0, os.geteuid()}:
            raise RelayDaemonError("file_owner_rejected")
        if file_stat.st_mode & (0o077 if private else 0o022):
            raise RelayDaemonError("file_permissions_rejected")
        if file_stat.st_size < 1 or file_stat.st_size > max_bytes:
            raise RelayDaemonError("file_size_rejected")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise RelayDaemonError("file_size_rejected")
        return payload
    except FileNotFoundError as exc:
        raise RelayDaemonError("file_not_found") from exc
    except OSError as exc:
        raise RelayDaemonError("file_read_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_object(value: object, *, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RelayDaemonError(code)
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise RelayDaemonError("config_duplicate_key")
        payload[key] = value
    return payload


def _strict_json_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise ValueError("json integer too large")
    return int(value)


def _reject_json_number(_value: str) -> object:
    raise ValueError("non-integer JSON number rejected")


def _parse_listener(value: object, *, code: str) -> tuple[str, int]:
    payload = _require_object(value, keys={"host", "port"}, code=code)
    host = payload.get("host")
    port = payload.get("port")
    if not isinstance(host, str):
        raise RelayDaemonError(code)
    host_invalid = False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host_invalid = True
    if host_invalid:
        raise RelayDaemonError(code)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RelayDaemonError(code)
    return host, port


def _absolute_path(value: object, *, code: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RelayDaemonError(code)
    if (
        len(value) > MAX_CANONICAL_PATH_CHARS
        or not value.isascii()
        or "~" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RelayDaemonError(code)
    path = Path(value)
    if not path.is_absolute() or value.startswith("//"):
        raise RelayDaemonError(code)
    if value != path.as_posix():
        raise RelayDaemonError(code)
    if value != "/" and any(
        part in {"", ".", ".."} for part in value.split("/")[1:]
    ):
        raise RelayDaemonError(code)
    return path


def parse_config_bytes(data: bytes) -> RelayDaemonConfig:
    if type(data) is not bytes or not data or len(data) > MAX_CONFIG_BYTES:
        raise RelayDaemonError("config_invalid_json")
    payload: object = None
    parse_failed = False
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_number,
            parse_float=_reject_json_number,
            parse_int=_strict_json_integer,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        parse_failed = True
    if parse_failed:
        raise RelayDaemonError("config_invalid_json")
    root = _require_object(
        payload,
        keys={
            "browser_listen",
            "connector_listen",
            "connector_tls",
            "routes",
            "schema_version",
            "state_path",
            "status_path",
        },
        code="config_shape_invalid",
    )
    schema_version = root.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise RelayDaemonError("config_schema_unsupported")
    browser_host, browser_port = _parse_listener(
        root.get("browser_listen"),
        code="browser_listener_invalid",
    )
    connector_host, connector_port = _parse_listener(
        root.get("connector_listen"),
        code="connector_listener_invalid",
    )
    connector_tls = _require_object(
        root.get("connector_tls"),
        keys={"cert_file", "key_file"},
        code="connector_tls_invalid",
    )
    cert_file = _absolute_path(
        connector_tls.get("cert_file"),
        code="connector_cert_path_invalid",
    )
    connector_key_file = _absolute_path(
        connector_tls.get("key_file"),
        code="connector_key_path_invalid",
    )
    state_path = _absolute_path(root.get("state_path"), code="state_path_invalid")
    status_path = _absolute_path(root.get("status_path"), code="status_path_invalid")

    route_payloads = root.get("routes")
    if not isinstance(route_payloads, list) or not 1 <= len(route_payloads) <= min(
        MAX_ROUTE_COUNT,
        MAX_DAEMON_ROUTES,
    ):
        raise RelayDaemonError("routes_invalid")
    routes: list[RelayRouteConfig] = []
    hostnames: set[str] = set()
    route_refs: set[str] = set()
    for value in route_payloads:
        route_payload = _require_object(
            value,
            keys={"hostname", "key_file", "route"},
            code="route_shape_invalid",
        )
        hostname_invalid = False
        try:
            hostname = normalize_dns_hostname(route_payload.get("hostname"))
        except (TypeError, ValueError):
            hostname_invalid = True
            hostname = ""
        if hostname_invalid:
            raise RelayDaemonError("route_hostname_invalid")
        route_ref = route_payload.get("route")
        route_ref_invalid = False
        try:
            _validate_frame(RelayFrame("register", route_ref, 1, 1))
        except (TypeError, RelayProtocolError):
            route_ref_invalid = True
        if route_ref_invalid:
            raise RelayDaemonError("route_ref_invalid")
        if hostname in hostnames or route_ref in route_refs:
            raise RelayDaemonError("route_duplicate")
        hostnames.add(hostname)
        route_refs.add(route_ref)
        routes.append(
            RelayRouteConfig(
                hostname=hostname,
                route=route_ref,
                key_file=_absolute_path(
                    route_payload.get("key_file"),
                    code="route_key_path_invalid",
                ),
            )
        )
    return RelayDaemonConfig(
        browser_host=browser_host,
        browser_port=browser_port,
        connector_host=connector_host,
        connector_port=connector_port,
        connector_cert_file=cert_file,
        connector_key_file=connector_key_file,
        state_path=state_path,
        status_path=status_path,
        routes=tuple(routes),
    )


def load_config(path: Path) -> RelayDaemonConfig:
    return parse_config_bytes(
        _read_bounded_file(path, max_bytes=MAX_CONFIG_BYTES, private=False)
    )


def load_route_keys(config: RelayDaemonConfig) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    unique_keys: set[bytes] = set()
    for route in config.routes:
        try:
            encoded = _read_bounded_file(
                route.key_file,
                max_bytes=MAX_KEY_FILE_BYTES,
                private=True,
            ).decode("ascii").strip()
            key = bytes.fromhex(encoded)
        except (UnicodeDecodeError, ValueError) as exc:
            raise RelayDaemonError("route_key_invalid") from exc
        if len(key) != 32:
            raise RelayDaemonError("route_key_invalid")
        if key in unique_keys:
            raise RelayDaemonError("route_key_reused")
        unique_keys.add(key)
        keys[route.route] = key
    return keys


def build_connector_tls_context(config: RelayDaemonConfig) -> ssl.SSLContext:
    _read_bounded_file(
        config.connector_cert_file,
        max_bytes=MAX_CONFIG_BYTES,
        private=False,
    )
    _read_bounded_file(
        config.connector_key_file,
        max_bytes=MAX_CONFIG_BYTES,
        private=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(
            certfile=str(config.connector_cert_file),
            keyfile=str(config.connector_key_file),
        )
    except (OSError, ssl.SSLError) as exc:
        raise RelayDaemonError("connector_tls_load_failed") from exc
    return context


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_trusted_parent(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            mode,
        )
        body = (_safe_json(payload) + "\n").encode("utf-8")
        os.write(descriptor, body)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise RelayDaemonError("state_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class PersistentEpochStore:
    """Persist only route epochs so stale controls remain rejected after restart."""

    def __init__(self, path: Path, routes: set[str]) -> None:
        self.path = path
        self.lock_path = path.with_name(f"{path.name}.lock")
        self._routes = set(routes)
        self._lock = threading.Lock()
        self._epochs = self._load_all()

    def _load_all(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(
                _read_bounded_file(
                    self.path,
                    max_bytes=MAX_CONFIG_BYTES,
                    private=True,
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RelayDaemonError("state_invalid") from exc
        root = _require_object(
            payload,
            keys={"routes", "schema_version"},
            code="state_invalid",
        )
        if root.get("schema_version") != STATE_SCHEMA_VERSION:
            raise RelayDaemonError("state_schema_unsupported")
        stored = root.get("routes")
        if not isinstance(stored, dict) or len(stored) > MAX_ROUTE_COUNT:
            raise RelayDaemonError("state_invalid")
        epochs: dict[str, int] = {}
        for route, value in stored.items():
            try:
                _validate_frame(RelayFrame("register", route, 1, 1))
            except (TypeError, RelayProtocolError) as exc:
                raise RelayDaemonError("state_invalid") from exc
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RelayDaemonError("state_invalid")
            epochs[route] = value
        return epochs

    @contextmanager
    def _file_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        _require_trusted_parent(self.lock_path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
            lock_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_uid not in {0, os.geteuid()}
                or lock_stat.st_mode & 0o077
            ):
                raise RelayDaemonError("state_lock_rejected")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise RelayDaemonError("state_lock_failed") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

    def current(self, route: str) -> int:
        with self._lock:
            return int(self._epochs.get(route) or 0)

    def commit(self, route: str, epoch: int) -> None:
        with self._lock:
            with self._file_lock():
                persisted = self._load_all()
                current = int(persisted.get(route) or 0)
                if route not in self._routes or epoch <= current:
                    raise RelayDaemonError("stale_epoch")
                if route not in persisted and len(persisted) >= MAX_ROUTE_COUNT:
                    raise RelayDaemonError("state_route_capacity_exceeded")
                persisted[route] = epoch
                _atomic_write_json(
                    self.path,
                    {
                        "routes": persisted,
                        "schema_version": STATE_SCHEMA_VERSION,
                    },
                )
                self._epochs = persisted


class RelayInstanceLock:
    """Prevent two daemon processes from serving one persisted route namespace."""

    def __init__(self, state_path: Path) -> None:
        self.path = state_path.with_name(f"{state_path.name}.instance.lock")
        self._descriptor = -1

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _require_trusted_parent(self.path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
            lock_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_uid not in {0, os.geteuid()}
                or lock_stat.st_mode & 0o077
            ):
                raise RelayDaemonError("relay_instance_lock_rejected")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RelayDaemonError("relay_instance_active") from exc
        except Exception:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor < 0:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._descriptor)
        self._descriptor = -1


@dataclass
class _RouteState:
    route: str
    key: bytes = field(repr=False)
    last_epoch: int = 0
    control: socket.socket | None = field(default=None, repr=False)
    control_epoch: int = 0
    control_seq: int = 1
    pending: dict[str, _PendingBrowser] = field(default_factory=dict, repr=False)
    reserved_data: set[tuple[int, str, str]] = field(default_factory=set, repr=False)
    consumed: deque[tuple[int, str, str]] = field(
        default_factory=lambda: deque(maxlen=MAX_REPLAY_EPOCHS),
        repr=False,
    )
    registration_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class MultiRouteRelay:
    """Route raw browser TLS to current authenticated Host connector sessions."""

    def __init__(
        self,
        *,
        browser_listener: socket.socket,
        connector_listener: socket.socket,
        hostnames: Mapping[str, str],
        route_keys: Mapping[str, bytes],
        connector_tls_context: ssl.SSLContext,
        epoch_store: PersistentEpochStore,
        max_browser_connections: int = MAX_BROWSER_CONNECTIONS,
    ) -> None:
        if set(hostnames.values()) != set(route_keys):
            raise ValueError("hostname and route key tables differ")
        if connector_tls_context.minimum_version < ssl.TLSVersion.TLSv1_2:
            raise ValueError("connector TLS minimum version rejected")
        self._browser_listener = browser_listener
        self._connector_listener = connector_listener
        self._router = BoundedSniRouter(hostnames)
        self._route_keys = {route: bytes(key) for route, key in route_keys.items()}
        self._connector_tls_context = connector_tls_context
        self._epoch_store = epoch_store
        self._states = {
            route: _RouteState(
                route=route,
                key=key,
                last_epoch=epoch_store.current(route),
            )
            for route, key in self._route_keys.items()
        }
        for route, key in self._route_keys.items():
            _validate_frame(RelayFrame("register", route, 1, 1))
            if len(key) != 32:
                raise RelayProtocolError("invalid_tunnel_key")
        if len(set(self._route_keys.values())) != len(self._route_keys):
            raise RelayProtocolError("reused_tunnel_key")
        self.metadata = BoundedRelayMetadata()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: set[threading.Thread] = set()
        self._acceptor_threads: dict[str, threading.Thread] = {}
        self._acceptor_failures: set[str] = set()
        self._active_sockets: set[socket.socket] = set()
        self._handshake_sockets: set[socket.socket] = set()
        self._handshake_slots = threading.BoundedSemaphore(MAX_CONNECTOR_HANDSHAKES)
        if not isinstance(max_browser_connections, int) or isinstance(max_browser_connections, bool):
            raise ValueError("browser connection capacity invalid")
        if not 1 <= max_browser_connections <= MAX_BROWSER_CONNECTIONS:
            raise ValueError("browser connection capacity invalid")
        self._browser_capacity = max_browser_connections
        self._browser_connections = 0
        self._browser_slots = threading.BoundedSemaphore(max_browser_connections)
        self._started = False

    def _spawn(self, target: Any, *args: Any) -> bool:
        def run() -> None:
            try:
                target(*args)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, daemon=True)
        with self._lock:
            if self._stop.is_set():
                return False
            self._threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._threads.discard(thread)
            return False
        return True

    def _spawn_acceptor(self, name: str, target: Any) -> bool:
        def run() -> None:
            try:
                target()
            finally:
                with self._lock:
                    current = threading.current_thread()
                    self._threads.discard(current)
                    if self._acceptor_threads.get(name) is current:
                        self._acceptor_threads.pop(name, None)
                    if not self._stop.is_set():
                        self._acceptor_failures.add(name)

        thread = threading.Thread(target=run, daemon=True, name=f"agentops-relay-{name}")
        with self._lock:
            if self._stop.is_set() or name in self._acceptor_threads:
                return False
            self._threads.add(thread)
            self._acceptor_threads[name] = thread
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._threads.discard(thread)
                self._acceptor_threads.pop(name, None)
                self._acceptor_failures.add(name)
            return False
        return True

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("relay_already_started")
            self._started = True
        for listener in (self._browser_listener, self._connector_listener):
            listener.settimeout(0.2)
        if not self._spawn_acceptor("browser", self._accept_browsers):
            raise RelayDaemonError("browser_acceptor_start_failed")
        if not self._spawn_acceptor("connector", self._accept_connectors):
            self._stop.set()
            _close_socket(self._browser_listener)
            raise RelayDaemonError("connector_acceptor_start_failed")

    def _accept_browsers(self) -> None:
        while not self._stop.is_set():
            try:
                browser, _ = self._browser_listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            browser.settimeout(IO_TIMEOUT_SECONDS)
            if not self._browser_slots.acquire(blocking=False):
                self.metadata.record("rejected", "data")
                _close_socket(browser)
                continue
            with self._lock:
                self._browser_connections += 1
            if not self._spawn(self._handle_browser_with_slot, browser):
                with self._lock:
                    self._browser_connections -= 1
                self._browser_slots.release()
                _close_socket(browser)

    def _handle_browser_with_slot(self, browser: socket.socket) -> None:
        try:
            self._handle_browser(browser)
        finally:
            with self._lock:
                self._browser_connections -= 1
            self._browser_slots.release()

    def _accept_connectors(self) -> None:
        while not self._stop.is_set():
            try:
                connector, _ = self._connector_listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connector.settimeout(IO_TIMEOUT_SECONDS)
            if not self._handshake_slots.acquire(blocking=False):
                self.metadata.record("rejected", "control")
                _close_socket(connector)
                continue
            with self._lock:
                self._handshake_sockets.add(connector)
            if not self._spawn(self._handle_connector, connector):
                with self._lock:
                    self._handshake_sockets.discard(connector)
                self._handshake_slots.release()
                _close_socket(connector)

    def _handle_connector(self, raw_connector: socket.socket) -> None:
        connector: socket.socket | None = None
        direction = "control"
        try:
            try:
                connector = self._connector_tls_context.wrap_socket(
                    raw_connector,
                    server_side=True,
                )
                with self._lock:
                    self._handshake_sockets.discard(raw_connector)
                    self._handshake_sockets.add(connector)
                first = receive_routed_frame(connector, self._route_keys)
            finally:
                self._handshake_slots.release()
            direction = "data" if first.kind == "data" else "control"
            state = self._states[first.route]
            if first.kind == "register":
                self._register_control(state, connector, first)
                with self._lock:
                    self._handshake_sockets.discard(connector)
                connector = None
                return
            if first.kind == "data":
                self._register_data(state, connector, first)
                with self._lock:
                    self._handshake_sockets.discard(connector)
                connector = None
                return
            raise RelayProtocolError("unexpected_connector_kind")
        except (RelayProtocolError, ssl.SSLError, OSError, RelayDaemonError):
            self.metadata.record("rejected", direction)
        finally:
            with self._lock:
                self._handshake_sockets.discard(raw_connector)
                if connector is not None:
                    self._handshake_sockets.discard(connector)
            _close_socket(connector)
            if connector is None:
                _close_socket(raw_connector)

    def _register_control(
        self,
        state: _RouteState,
        connector: socket.socket,
        frame: RelayFrame,
    ) -> None:
        if frame.seq != 1:
            raise RelayProtocolError("replayed_sequence")
        with state.registration_lock:
            with self._lock:
                if frame.epoch <= state.last_epoch:
                    raise RelayProtocolError("replayed_epoch")
            try:
                self._epoch_store.commit(state.route, frame.epoch)
            except RelayDaemonError as exc:
                raise RelayProtocolError("epoch_persist_failed") from exc
            with self._lock:
                state.last_epoch = frame.epoch
                old_control = state.control
                stale_pending = list(state.pending.values())
                state.control = None
                state.control_epoch = 0
                state.pending.clear()
            _close_socket(old_control)
            for pending in stale_pending:
                _close_socket(pending.browser)
                _close_socket(pending.data)
                pending.ready.set()
            send_frame(
                connector,
                RelayFrame("registered", state.route, frame.epoch, 1),
                state.key,
            )
            with self._lock:
                if self._stop.is_set() or frame.epoch != state.last_epoch:
                    raise RelayProtocolError("registration_superseded")
                state.control_epoch = frame.epoch
                state.control_seq = 1
                state.control = connector
                self._handshake_sockets.discard(connector)
        self.metadata.record("registered", "control")
        try:
            connector.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    unexpected = connector.recv(1)
                except socket.timeout:
                    continue
                if not unexpected:
                    break
                raise RelayProtocolError("unexpected_control_bytes")
        except (OSError, RelayProtocolError):
            pass
        finally:
            with self._lock:
                if state.control is connector:
                    state.control = None
            _close_socket(connector)

    def _register_data(
        self,
        state: _RouteState,
        connector: socket.socket,
        frame: RelayFrame,
    ) -> None:
        token = (frame.epoch, frame.connection_id, frame.nonce)
        with self._lock:
            if frame.epoch != state.control_epoch or state.control is None:
                raise RelayProtocolError("stale_epoch")
            if frame.seq != 1 or token in state.consumed or token in state.reserved_data:
                raise RelayProtocolError("replayed_data_connection")
            pending = state.pending.get(frame.connection_id)
            if (
                pending is None
                or pending.epoch != frame.epoch
                or pending.nonce != frame.nonce
                or pending.data is not None
            ):
                raise RelayProtocolError("unknown_stream")
            state.consumed.append(token)
            state.reserved_data.add(token)
        try:
            send_frame(
                connector,
                RelayFrame(
                    "data_ready",
                    state.route,
                    frame.epoch,
                    1,
                    frame.connection_id,
                    frame.nonce,
                ),
                state.key,
            )
            with self._lock:
                current = state.pending.get(frame.connection_id)
                if (
                    self._stop.is_set()
                    or frame.epoch != state.control_epoch
                    or state.control is None
                    or current is not pending
                    or pending.data is not None
                ):
                    raise RelayProtocolError("data_registration_superseded")
                pending.data = connector
                pending.ready.set()
        finally:
            with self._lock:
                state.reserved_data.discard(token)
        self.metadata.record("authenticated", "data")

    def _handle_browser(self, browser: socket.socket) -> None:
        pending: _PendingBrowser | None = None
        data: socket.socket | None = None
        state: _RouteState | None = None
        connection_id = os.urandom(HEX_ID_LENGTH // 2).hex()
        nonce = os.urandom(HEX_ID_LENGTH // 2).hex()
        try:
            selection = self._router.route_connection(browser)
            state = self._states[selection.route_ref]
            with self._lock:
                if state.control is None or len(state.pending) >= MAX_PENDING_STREAMS:
                    raise RelayProtocolError("route_unavailable")
                pending = _PendingBrowser(
                    browser=browser,
                    nonce=nonce,
                    epoch=state.control_epoch,
                    ready=threading.Event(),
                )
                state.pending[connection_id] = pending
                state.control_seq += 1
                control = state.control
                open_frame = RelayFrame(
                    "open",
                    state.route,
                    state.control_epoch,
                    state.control_seq,
                    connection_id,
                    nonce,
                )
            send_frame(control, open_frame, state.key)
            self.metadata.record("accepted", "data")
            pending.ready.wait(PAIR_TIMEOUT_SECONDS)
            with self._lock:
                if state.pending.get(connection_id) is pending:
                    state.pending.pop(connection_id, None)
                data = pending.data
                if data is not None:
                    self._active_sockets.update((browser, data))
            if data is None:
                raise RelayProtocolError("data_connection_timeout")
            data.settimeout(IO_TIMEOUT_SECONDS)
            data.sendall(selection.preface)
            self.metadata.record("forwarded", "browser_to_host", len(selection.preface))
            forwarded = _forward_bidirectional(
                browser,
                data,
                self.metadata,
                stop=self._stop,
            )
            self.metadata.record("closed" if forwarded else "failed", "data")
        except (RelayProtocolError, SniRoutingError, OSError):
            with self._lock:
                if state is not None and state.pending.get(connection_id) is pending:
                    state.pending.pop(connection_id, None)
                if data is None and pending is not None:
                    data = pending.data
            self.metadata.record("rejected", "data")
            _close_socket(browser)
            _close_socket(data)
        finally:
            with self._lock:
                self._active_sockets.discard(browser)
                if data is not None:
                    self._active_sockets.discard(data)

    def status(self) -> dict[str, Any]:
        with self._lock:
            active_routes = sum(1 for state in self._states.values() if state.control is not None)
            pending_streams = sum(len(state.pending) for state in self._states.values())
            acceptors_healthy = (
                not self._acceptor_failures
                and set(self._acceptor_threads) == {"browser", "connector"}
                and all(thread.is_alive() for thread in self._acceptor_threads.values())
            )
            return {
                "acceptor_failure_count": len(self._acceptor_failures),
                "acceptors_healthy": acceptors_healthy,
                "active_routes": active_routes,
                "active_streams": len(self._active_sockets) // 2,
                "browser_connection_capacity": self._browser_capacity,
                "browser_connections": self._browser_connections,
                "handshakes_in_progress": len(self._handshake_sockets),
                "pending_streams": pending_streams,
                "ready": self._started and not self._stop.is_set() and acceptors_healthy,
                "route_count": len(self._states),
                "schema_version": STATUS_SCHEMA_VERSION,
                "stopping": self._stop.is_set(),
                "transport_authority": False,
            }

    def wait_for_active_routes(self, count: int, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        while time.monotonic() < deadline:
            if self.status()["active_routes"] == count:
                return True
            time.sleep(0.01)
        return False

    def stop(self) -> None:
        self._stop.set()
        _close_socket(self._browser_listener)
        _close_socket(self._connector_listener)
        with self._lock:
            controls = [state.control for state in self._states.values()]
            pending = [
                item
                for state in self._states.values()
                for item in state.pending.values()
            ]
            handshakes = list(self._handshake_sockets)
            active = list(self._active_sockets)
            threads = list(self._threads)
        for stream in controls + handshakes + active:
            _close_socket(stream)
        for item in pending:
            _close_socket(item.browser)
            _close_socket(item.data)
            item.ready.set()
        deadline = time.monotonic() + 3.0
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))


def bind_listener(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host, port))
        listener.listen(128)
    except OSError:
        listener.close()
        raise
    return listener


def _write_status(config: RelayDaemonConfig, payload: Mapping[str, Any]) -> None:
    _atomic_write_json(
        config.status_path,
        {
            **payload,
            "pid": os.getpid(),
            "raw_payload_omitted": True,
            "token_omitted": True,
            "updated_at_unix": int(time.time()),
        },
    )


def run_check(config: RelayDaemonConfig) -> int:
    route_keys = load_route_keys(config)
    build_connector_tls_context(config)
    PersistentEpochStore(config.state_path, set(route_keys))
    _emit_json(
        {
            "ok": True,
            "operation": "relay_check",
            "route_count": len(route_keys),
            "transport_authority": False,
            "token_omitted": True,
        }
    )
    return 0


def run_status(config: RelayDaemonConfig) -> int:
    try:
        payload = json.loads(
            _read_bounded_file(
                config.status_path,
                max_bytes=MAX_CONFIG_BYTES,
                private=True,
            ).decode("utf-8")
        )
    except (RelayDaemonError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {
            "ok": False,
            "operation": "relay_status",
            "reason": "status_unavailable",
            "token_omitted": True,
        }
        _emit_json(payload)
        return 1
    pid = payload.get("pid")
    updated_at = payload.get("updated_at_unix")
    pid_alive = False
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1:
        try:
            os.kill(pid, 0)
        except OSError:
            pass
        else:
            pid_alive = True
    status_fresh = bool(
        isinstance(updated_at, int)
        and not isinstance(updated_at, bool)
        and 0 <= int(time.time()) - updated_at <= 5
    )
    ready = bool(payload.get("ready") and pid_alive and status_fresh)
    projected = {
        **payload,
        "daemon_operation": payload.get("operation"),
        "ok": ready,
        "operation": "relay_status",
        "pid_alive": pid_alive,
        "ready": ready,
        "status_fresh": status_fresh,
        "token_omitted": True,
    }
    if payload.get("ready") and not ready:
        projected["reason"] = "status_stale_or_process_unavailable"
    _emit_json(projected)
    return 0 if ready else 1


def run_serve(config: RelayDaemonConfig) -> int:
    route_keys = load_route_keys(config)
    context = build_connector_tls_context(config)
    epoch_store = PersistentEpochStore(config.state_path, set(route_keys))
    browser_listener: socket.socket | None = None
    connector_listener: socket.socket | None = None
    relay: MultiRouteRelay | None = None
    instance_lock = RelayInstanceLock(config.state_path)
    instance_acquired = False
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    try:
        instance_lock.acquire()
        instance_acquired = True
        browser_listener = bind_listener(config.browser_host, config.browser_port)
        connector_listener = bind_listener(config.connector_host, config.connector_port)
        relay = MultiRouteRelay(
            browser_listener=browser_listener,
            connector_listener=connector_listener,
            hostnames={route.hostname: route.route for route in config.routes},
            route_keys=route_keys,
            connector_tls_context=context,
            epoch_store=epoch_store,
        )
        relay.start()
        initial_status = relay.status()
        if initial_status.get("ready") is not True:
            raise RelayDaemonError("relay_acceptor_unavailable")
        _write_status(
            config,
            {
                **initial_status,
                "browser_port": config.browser_port,
                "connector_port": config.connector_port,
                "ok": True,
                "operation": "relay_serve",
            },
        )
        _emit_json(
            {
                "ok": True,
                "operation": "relay_serve",
                "ready": True,
                "route_count": len(route_keys),
                "transport_authority": False,
                "token_omitted": True,
            }
        )
        exit_code = 0
        while not stop.wait(1.0):
            current_status = relay.status()
            if current_status.get("ready") is not True:
                _write_status(
                    config,
                    {
                        **current_status,
                        "browser_port": config.browser_port,
                        "connector_port": config.connector_port,
                        "ok": False,
                        "operation": "relay_serve",
                        "reason": "relay_acceptor_unavailable",
                    },
                )
                exit_code = 1
                break
            _write_status(
                config,
                {
                    **current_status,
                    "browser_port": config.browser_port,
                    "connector_port": config.connector_port,
                    "ok": True,
                    "operation": "relay_serve",
                },
            )
        return exit_code
    finally:
        if relay is not None:
            relay.stop()
        else:
            _close_socket(browser_listener)
            _close_socket(connector_listener)
        if instance_acquired:
            try:
                _write_status(
                    config,
                    {
                        "active_routes": 0,
                        "ok": True,
                        "operation": "relay_serve",
                        "pending_streams": 0,
                        "ready": False,
                        "route_count": len(config.routes),
                        "schema_version": STATUS_SCHEMA_VERSION,
                        "stopped": True,
                        "stopping": False,
                        "transport_authority": False,
                    },
                )
            except RelayDaemonError:
                pass
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        if instance_acquired:
            instance_lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentOps MIS non-authority L4 Relay")
    parser.add_argument("command", choices=("check", "serve", "status"))
    parser.add_argument("--config", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(Path(args.config).expanduser())
        if args.command == "check":
            return run_check(config)
        if args.command == "status":
            return run_status(config)
        return run_serve(config)
    except RelayDaemonError as exc:
        _emit_json(
            {
                "error": exc.code,
                "ok": False,
                "operation": f"relay_{args.command}",
                "token_omitted": True,
            }
        )
        return 1
    except OSError:
        _emit_json(
            {
                "error": "relay_socket_failed",
                "ok": False,
                "operation": f"relay_{args.command}",
                "token_omitted": True,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
