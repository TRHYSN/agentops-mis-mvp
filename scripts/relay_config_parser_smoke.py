#!/usr/bin/env python3
"""Verify strict, credential-free Relay daemon config parsing."""
from __future__ import annotations

import builtins
import contextlib
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from agentops_mis_cli import relay_daemon  # noqa: E402


CANARY = "RELAY_CONFIG_PRIVATE_CANARY"
VALID_CONFIG = {
    "schema_version": 1,
    "browser_listen": {
        "host": "127.0.0.1",
        "port": 8443,
    },
    "connector_listen": {
        "host": "127.0.0.1",
        "port": 9443,
    },
    "connector_tls": {
        "cert_file": "/etc/agentops-mis-relay/tls/relay-cert.pem",
        "key_file": "/etc/agentops-mis-relay/tls/relay-key.pem",
    },
    "state_path": "/var/lib/agentops-mis-relay/epochs.json",
    "status_path": "/run/agentops-mis-relay/status.json",
    "routes": [
        {
            "hostname": "relay-config.example.test",
            "route": "relay_config_smoke",
            "key_file": "/etc/agentops-mis-relay/routes/relay-config.key",
        }
    ],
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def encoded(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def expect_error(data: object, expected: str | tuple[str, ...]) -> None:
    expected_codes = (expected,) if isinstance(expected, str) else expected
    try:
        relay_daemon.parse_config_bytes(data)  # type: ignore[arg-type]
    except relay_daemon.RelayDaemonError as exc:
        require(
            exc.code in expected_codes,
            f"expected {expected_codes}, received {exc.code}",
        )
        require(str(exc) == exc.code, "parser error disclosed rejected config material")
        require(CANARY not in repr(exc), "parser exception repr disclosed config material")
        require(exc.__cause__ is None, "parser exception retained a rejected-input cause")
        require(exc.__context__ is None, "parser exception retained rejected-input context")
    else:
        raise AssertionError(f"invalid config passed: {expected_codes}")


def with_path(field: str, value: str) -> dict[str, object]:
    payload = copy.deepcopy(VALID_CONFIG)
    if field == "connector_cert":
        payload["connector_tls"]["cert_file"] = value  # type: ignore[index]
    elif field == "connector_key":
        payload["connector_tls"]["key_file"] = value  # type: ignore[index]
    elif field == "state":
        payload["state_path"] = value
    elif field == "status":
        payload["status_path"] = value
    elif field == "route_key":
        payload["routes"][0]["key_file"] = value  # type: ignore[index]
    else:
        raise AssertionError("unknown synthetic path field")
    return payload


def _write_requested(mode: object) -> bool:
    if isinstance(mode, str):
        return any(marker in mode for marker in ("w", "a", "x", "+"))
    return False


def _write_flags(flags: int) -> bool:
    mask = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    if hasattr(os, "O_TMPFILE"):
        mask |= os.O_TMPFILE
    return bool(flags & mask)


def _inside_fixture(path: object, fixture: Path) -> bool:
    if isinstance(path, int):
        return True
    try:
        candidate = Path(os.fspath(path))
    except TypeError:
        return False
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        candidate.resolve(strict=False).relative_to(fixture)
    except (OSError, ValueError):
        return False
    return True


@contextlib.contextmanager
def guarded_effects(fixture: Path) -> Iterator[None]:
    fixture = fixture.resolve()
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_socket = socket.socket
    original_run = subprocess.run
    original_popen = subprocess.Popen
    original_call = subprocess.call
    original_check_call = subprocess.check_call
    original_check_output = subprocess.check_output

    def guarded_builtin_open(file: object, mode: str = "r", *args: object, **kwargs: object):
        if _write_requested(mode) and not _inside_fixture(file, fixture):
            raise AssertionError("write escaped parser smoke fixture")
        return original_builtin_open(file, mode, *args, **kwargs)

    def guarded_io_open(file: object, mode: str = "r", *args: object, **kwargs: object):
        if _write_requested(mode) and not _inside_fixture(file, fixture):
            raise AssertionError("write escaped parser smoke fixture")
        return original_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if _write_flags(flags) and not _inside_fixture(path, fixture):
            raise AssertionError("write escaped parser smoke fixture")
        return original_os_open(path, flags, *args, **kwargs)

    def blocked_external_effect(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("parser smoke attempted network or subprocess activity")

    builtins.open = guarded_builtin_open
    io.open = guarded_io_open
    os.open = guarded_os_open
    socket.socket = blocked_external_effect  # type: ignore[assignment]
    subprocess.run = blocked_external_effect  # type: ignore[assignment]
    subprocess.Popen = blocked_external_effect  # type: ignore[assignment,misc]
    subprocess.call = blocked_external_effect  # type: ignore[assignment]
    subprocess.check_call = blocked_external_effect  # type: ignore[assignment]
    subprocess.check_output = blocked_external_effect  # type: ignore[assignment]
    try:
        yield
    finally:
        builtins.open = original_builtin_open
        io.open = original_io_open
        os.open = original_os_open
        socket.socket = original_socket
        subprocess.run = original_run
        subprocess.Popen = original_popen
        subprocess.call = original_call
        subprocess.check_call = original_check_call
        subprocess.check_output = original_check_output


def duplicate_configs() -> list[bytes]:
    base = encoded(VALID_CONFIG).decode("utf-8")
    replacements = [
        (
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
        ),
        (
            '"browser_listen":{"host":"127.0.0.1","port":8443}',
            '"browser_listen":{"host":"127.0.0.1","host":"127.0.0.2","port":8443}',
        ),
        (
            '"connector_tls":{"cert_file":'
            '"/etc/agentops-mis-relay/tls/relay-cert.pem",',
            '"connector_tls":{"cert_file":'
            f'"{CANARY}","cert_file":'
            '"/etc/agentops-mis-relay/tls/relay-cert.pem",',
        ),
        (
            '"hostname":"relay-config.example.test",'
            '"key_file":"/etc/agentops-mis-relay/routes/relay-config.key",'
            '"route":"relay_config_smoke"',
            '"hostname":"relay-config.example.test",'
            '"key_file":"/etc/agentops-mis-relay/routes/relay-config.key",'
            '"route":"relay_config_smoke","route":"relay_config_other"',
        ),
    ]
    cases: list[bytes] = []
    for needle, replacement in replacements:
        require(base.count(needle) == 1, "duplicate-key fixture needle drifted")
        cases.append(base.replace(needle, replacement).encode("utf-8"))
    return cases


def main() -> int:
    parsed = relay_daemon.parse_config_bytes(encoded(VALID_CONFIG))
    require(parsed.browser_host == "127.0.0.1", "browser host compatibility drifted")
    require(parsed.browser_port == 8443, "browser port compatibility drifted")
    require(parsed.connector_host == "127.0.0.1", "connector host compatibility drifted")
    require(parsed.connector_port == 9443, "connector port compatibility drifted")
    require(
        parsed.connector_cert_file
        == Path("/etc/agentops-mis-relay/tls/relay-cert.pem"),
        "connector certificate path compatibility drifted",
    )
    require(
        parsed.connector_key_file
        == Path("/etc/agentops-mis-relay/tls/relay-key.pem"),
        "connector key path compatibility drifted",
    )
    require(
        parsed.state_path == Path("/var/lib/agentops-mis-relay/epochs.json"),
        "state path compatibility drifted",
    )
    require(
        parsed.status_path == Path("/run/agentops-mis-relay/status.json"),
        "status path compatibility drifted",
    )
    require(len(parsed.routes) == 1, "route compatibility drifted")
    require(
        parsed.routes[0].key_file
        == Path("/etc/agentops-mis-relay/routes/relay-config.key"),
        "route key path compatibility drifted",
    )

    duplicate_cases = duplicate_configs()
    for data in duplicate_cases:
        expect_error(data, "config_duplicate_key")

    invalid_schema = copy.deepcopy(VALID_CONFIG)
    invalid_schema["schema_version"] = True
    expect_error(encoded(invalid_schema), "config_schema_unsupported")

    invalid_listener = copy.deepcopy(VALID_CONFIG)
    invalid_listener["browser_listen"]["host"] = f"not-an-ip-{CANARY}"  # type: ignore[index]
    expect_error(encoded(invalid_listener), "browser_listener_invalid")

    invalid_hostname = copy.deepcopy(VALID_CONFIG)
    invalid_hostname["routes"][0]["hostname"] = f"bad host {CANARY}"  # type: ignore[index]
    expect_error(encoded(invalid_hostname), "route_hostname_invalid")

    invalid_route = copy.deepcopy(VALID_CONFIG)
    invalid_route["routes"][0]["route"] = {"private": CANARY}  # type: ignore[index]
    expect_error(encoded(invalid_route), "route_ref_invalid")

    original_json_loads = relay_daemon.json.loads

    def unexpected_json_parse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("out-of-contract bytes reached the JSON parser")

    relay_daemon.json.loads = unexpected_json_parse
    try:
        expect_error(b"x" * (relay_daemon.MAX_CONFIG_BYTES + 1), "config_invalid_json")
        expect_error("{}", "config_invalid_json")
    finally:
        relay_daemon.json.loads = original_json_loads

    base_config = encoded(VALID_CONFIG)
    schema_marker = b'"schema_version":1'
    require(base_config.count(schema_marker) == 1, "schema fixture marker drifted")
    oversized_integer_config = base_config.replace(
        schema_marker,
        b'"schema_version":' + (b"9" * 5000),
    )
    expect_error(oversized_integer_config, "config_invalid_json")
    expect_error(
        (b"[" * 2000) + b"0" + (b"]" * 2000),
        ("config_invalid_json", "config_shape_invalid"),
    )
    expect_error(
        base_config.replace(schema_marker, b'"schema_version":1e0'),
        "config_invalid_json",
    )
    expect_error(
        base_config.replace(schema_marker, b'"schema_version":NaN'),
        "config_invalid_json",
    )

    path_fields = {
        "connector_cert": "connector_cert_path_invalid",
        "connector_key": "connector_key_path_invalid",
        "state": "state_path_invalid",
        "status": "status_path_invalid",
        "route_key": "route_key_path_invalid",
    }
    invalid_paths = (
        "~/relay.file",
        "/etc/agentops-mis-relay/./relay.file",
        "/etc/agentops-mis-relay/../relay.file",
        "/etc//agentops-mis-relay/relay.file",
        "//etc/agentops-mis-relay/relay.file",
        "/etc/agentops-mis-relay/relay.file/",
        "etc/agentops-mis-relay/relay.file",
        "/etc/agentops-mis-relay/line\nbreak",
        "/etc/agentops-mis-relay/tab\tfile",
        "/etc/agentops-mis-relay/delete\x7ffile",
        "/etc/agentops-mis-relay/non-ascii-\u00e9",
        "/" + ("a" * relay_daemon.MAX_CANONICAL_PATH_CHARS),
    )
    path_cases = 0
    for field, expected in path_fields.items():
        for value in invalid_paths:
            expect_error(encoded(with_path(field, value)), expected)
            path_cases += 1

    with tempfile.TemporaryDirectory(prefix="relay-config-parser-") as temporary:
        fixture = Path(temporary).resolve()
        with guarded_effects(fixture):
            config_path = fixture / "config.json"
            config_path.write_bytes(encoded(VALID_CONFIG))
            parser_calls: list[bytes] = []
            original_parse_config_bytes = relay_daemon.parse_config_bytes

            def observed_parse_config_bytes(data: bytes):
                parser_calls.append(data)
                return original_parse_config_bytes(data)

            relay_daemon.parse_config_bytes = observed_parse_config_bytes
            try:
                loaded = relay_daemon.load_config(config_path)
            finally:
                relay_daemon.parse_config_bytes = original_parse_config_bytes
            require(loaded == parsed, "load_config did not use the shared bytes parser")
            require(
                parser_calls == [encoded(VALID_CONFIG)],
                "load_config bypassed or repeated the shared bytes parser",
            )

            rejected_path = fixture / "rejected.json"
            rejected_path.write_bytes(duplicate_cases[2])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                return_code = relay_daemon.main(
                    ["check", "--config", str(rejected_path)]
                )
            rendered = output.getvalue()
            result = json.loads(rendered)
            require(return_code == 1, "CLI duplicate-key check unexpectedly succeeded")
            require(
                result
                == {
                    "error": "config_duplicate_key",
                    "ok": False,
                    "operation": "relay_check",
                    "token_omitted": True,
                },
                "CLI config failure output exceeded its bounded schema",
            )
            require(CANARY not in rendered, "CLI output disclosed rejected config material")
            require(str(fixture) not in rendered, "CLI output disclosed local fixture path")

            oversized_integer_path = fixture / "oversized-integer.json"
            oversized_integer_path.write_bytes(oversized_integer_config)
            oversized_output = io.StringIO()
            with contextlib.redirect_stdout(oversized_output):
                oversized_return_code = relay_daemon.main(
                    ["check", "--config", str(oversized_integer_path)]
                )
            oversized_result = json.loads(oversized_output.getvalue())
            require(
                oversized_return_code == 1
                and oversized_result
                == {
                    "error": "config_invalid_json",
                    "ok": False,
                    "operation": "relay_check",
                    "token_omitted": True,
                },
                "CLI oversized integer escaped bounded failure output",
            )

    print(
        json.dumps(
            {
                "canonical_path_rejections": path_cases,
                "cli_failure_cases": 2,
                "config_byte_boundary_rejections": 6,
                "duplicate_object_rejections": len(duplicate_cases),
                "load_config_shared_parser": True,
                "network_subprocess_blocked": True,
                "ok": True,
                "operation": "relay_config_parser_smoke",
                "output_redacted": True,
                "schema_type_rejections": 1,
                "validation_chain_rejections": 3,
                "writes_confined_to_fixture": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
