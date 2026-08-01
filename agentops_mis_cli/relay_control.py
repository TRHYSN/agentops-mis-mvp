"""Bounded, standalone two-phase control for private Relay transitions."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from agentops_mis_cli.relay_connector_service import (
    RelayConnectorServiceError,
    load_connector_config,
    validate_connector_material,
)
from agentops_mis_cli import relay_restart


SCHEMA_VERSION = 1
MAX_TRANSITION_TTL_SECONDS = 300
MAX_PRIVATE_JSON_BYTES = 64 * 1024
MAX_TLS_FILE_BYTES = 1024 * 1024
DISABLED_RELAY_CONFIG = {"enabled": False, "schema_version": 1}
_ACTIONS = {"enable", "disable"}
_HOST_DIGEST_FIELDS = (
    "allowed_origins",
    "cookie_secure",
    "host",
    "network_publication",
    "port",
    "private_console_origin",
    "tailscale_https_port",
)
_PUBLIC_ERROR_CODES = {
    "active_relay_state_invalid",
    "confirmation_already_recorded",
    "confirmation_action_mismatch",
    "confirmation_ref_mismatch",
    "confirmation_required",
    "host_config_invalid",
    "invalid_action",
    "invalid_ttl",
    "relay_material_invalid",
    "relay_origin_invalid",
    "rollback_incomplete",
    "rollback_pending",
    "transition_expired",
    "transition_invalid",
    "transition_material_changed",
    "transition_not_found",
    "transition_store_invalid",
    "transition_write_failed",
}


class RelayControlError(RuntimeError):
    """Bounded internal/public integration error without sensitive context."""

    def __init__(self, code: str) -> None:
        if code not in _PUBLIC_ERROR_CODES:
            code = "transition_invalid"
        super().__init__(code)
        self.code = code


_ControlFailure = RelayControlError


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _private_directory(path: Path, *, create: bool = False) -> None:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise _ControlFailure("transition_store_invalid") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise _ControlFailure("transition_store_invalid")


def _read_bounded_file(
    path: Path,
    *,
    maximum_bytes: int,
    allowed_modes: set[int],
    missing_code: str,
    invalid_code: str,
) -> bytes:
    path = _absolute(path)
    descriptor = -1
    try:
        _private_directory(path.parent)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) not in allowed_modes
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            raise _ControlFailure(invalid_code)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if not payload or len(payload) > maximum_bytes:
            raise _ControlFailure(invalid_code)
        return payload
    except FileNotFoundError as exc:
        raise _ControlFailure(missing_code) from exc
    except _ControlFailure:
        raise
    except OSError as exc:
        raise _ControlFailure(invalid_code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_json(
    path: Path,
    *,
    missing_code: str,
    invalid_code: str,
) -> tuple[bytes, dict[str, Any]]:
    raw = _read_bounded_file(
        path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code=missing_code,
        invalid_code=invalid_code,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise _ControlFailure(invalid_code) from exc
    if not isinstance(payload, dict):
        raise _ControlFailure(invalid_code)
    return raw, payload


def _write_private_bytes(path: Path, payload: bytes, *, allow_create: bool) -> None:
    path = _absolute(path)
    _private_directory(path.parent, create=allow_create)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise _ControlFailure("transition_write_failed") from exc
    if metadata is None and not allow_create:
        raise _ControlFailure("transition_write_failed")
    if metadata is not None and (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise _ControlFailure("transition_write_failed")
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = None
        path.chmod(0o600)
        _fsync_directory(path.parent)
    except _ControlFailure:
        raise
    except OSError as exc:
        raise _ControlFailure("transition_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    _write_private_bytes(path, payload, allow_create=False)


def _write_private_json(path: Path, payload: dict[str, Any], *, allow_create: bool) -> None:
    raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    _write_private_bytes(path, raw, allow_create=allow_create)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_private(path: Path) -> None:
    path = _absolute(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _ControlFailure("transition_write_failed") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise _ControlFailure("transition_write_failed")
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise _ControlFailure("transition_write_failed") from exc


def _acquire_lock(directory: Path) -> int:
    _private_directory(directory, create=True)
    lock_path = directory / ".relay-control.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise _ControlFailure("transition_store_invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except _ControlFailure:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise _ControlFailure("transition_store_invalid") from exc


def _release_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _safe_now(now: int | None) -> int:
    if now is None:
        return int(time.time())
    if not isinstance(now, int) or isinstance(now, bool) or now < 0:
        raise _ControlFailure("transition_invalid")
    return now


def _validate_action(action: str) -> str:
    if action not in _ACTIONS:
        raise _ControlFailure("invalid_action")
    return action


def _host_digest_projection(host_config: dict[str, Any]) -> bytes:
    origins = host_config.get("allowed_origins")
    if (
        not isinstance(origins, list)
        or any(not isinstance(origin, str) or not origin for origin in origins)
        or not isinstance(host_config.get("cookie_secure"), bool)
        or not isinstance(host_config.get("network_publication"), str)
        or not isinstance(host_config.get("port"), int)
        or isinstance(host_config.get("port"), bool)
    ):
        raise _ControlFailure("host_config_invalid")
    projection = {field: host_config.get(field) for field in _HOST_DIGEST_FIELDS}
    return json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _relay_origin(config: dict[str, Any]) -> str:
    hostname = config.get("host_server_hostname")
    if (
        not isinstance(hostname, str)
        or not hostname
        or hostname != hostname.strip()
        or hostname.startswith(".")
        or hostname.endswith(".")
        or any(character in hostname for character in "/:@?#\\")
    ):
        raise _ControlFailure("relay_origin_invalid")
    return f"https://{hostname.lower()}"


def _digest_parts(parts: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"agentops-relay-transition-v1\x00")
    for label, payload in parts:
        label_bytes = label.encode("ascii")
        digest.update(len(label_bytes).to_bytes(4, "big"))
        digest.update(label_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _collect_material(
    *,
    action: str,
    active_config_path: Path,
    prepared_config_path: Path,
    secrets_path: Path,
    host_config_path: Path,
    validate: bool,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    active_raw, active = _read_private_json(
        active_config_path,
        missing_code="active_relay_state_invalid",
        invalid_code="active_relay_state_invalid",
    )
    host_raw, host_config = _read_private_json(
        host_config_path,
        missing_code="host_config_invalid",
        invalid_code="host_config_invalid",
    )
    del host_raw
    host_projection = _host_digest_projection(host_config)

    prepared_raw = b""
    if action == "enable":
        if active != DISABLED_RELAY_CONFIG:
            raise _ControlFailure("active_relay_state_invalid")
        if host_config.get("network_publication") != "disabled":
            raise _ControlFailure("active_relay_state_invalid")
        prepared_raw, target = _read_private_json(
            prepared_config_path,
            missing_code="relay_material_invalid",
            invalid_code="relay_material_invalid",
        )
        material_config_path = prepared_config_path
    else:
        if active.get("enabled") is not True:
            raise _ControlFailure("active_relay_state_invalid")
        if host_config.get("network_publication") != "agentops_relay":
            raise _ControlFailure("active_relay_state_invalid")
        target = active
        material_config_path = active_config_path

    if target.get("enabled") is not True:
        raise _ControlFailure("relay_material_invalid")
    if target.get("host_http_port") != host_config.get("port"):
        raise _ControlFailure("relay_material_invalid")
    origin = _relay_origin(target)

    secrets_raw = _read_bounded_file(
        secrets_path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code="relay_material_invalid",
        invalid_code="relay_material_invalid",
    )
    referenced: list[tuple[str, bytes]] = []
    for label, key, modes in (
        ("relay-ca", "relay_ca_path", {0o600, 0o644}),
        ("host-certificate", "host_certificate_path", {0o600, 0o644}),
        ("host-private-key", "host_private_key_path", {0o600}),
    ):
        value = target.get(key)
        if not isinstance(value, str) or not value:
            raise _ControlFailure("relay_material_invalid")
        referenced.append(
            (
                label,
                _read_bounded_file(
                    Path(value),
                    maximum_bytes=MAX_TLS_FILE_BYTES,
                    allowed_modes=modes,
                    missing_code="relay_material_invalid",
                    invalid_code="relay_material_invalid",
                ),
            )
        )

    if validate:
        try:
            validated, _key, _relay_context, _host_context = validate_connector_material(
                material_config_path,
                secrets_path,
            )
        except RelayConnectorServiceError as exc:
            raise _ControlFailure("relay_material_invalid") from exc
        if validated != target:
            raise _ControlFailure("relay_material_invalid")

    digest = _digest_parts(
        [
            ("action", action.encode("ascii")),
            ("active-config", active_raw),
            ("prepared-config", prepared_raw),
            ("secrets", secrets_raw),
            *referenced,
            ("host-fields", host_projection),
        ]
    )
    return digest, origin, active, host_config


def _public_result(
    *,
    ok: bool,
    operation: str,
    action: str | None,
    transition_ref: str | None,
    expires_at: int | None,
    confirmation_required: bool,
    restart_required: bool,
    **values: Any,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "operation": operation,
        "action": action,
        "transition_ref": transition_ref,
        "expires_at": expires_at,
        "confirmation_required": confirmation_required,
        "restart_required": restart_required,
        "network_used": False,
        "sensitive_values_omitted": True,
        **values,
    }


def _failure(operation: str, code: str) -> dict[str, Any]:
    if code not in _PUBLIC_ERROR_CODES:
        code = "transition_invalid"
    return _public_result(
        ok=False,
        operation=operation,
        action=None,
        transition_ref=None,
        expires_at=None,
        confirmation_required=False,
        restart_required=False,
        error=code,
    )


def prepare_relay_transition(
    *,
    action: str,
    transition_path: Path,
    active_config_path: Path,
    prepared_config_path: Path,
    secrets_path: Path,
    host_config_path: Path,
    ttl_seconds: int = MAX_TRANSITION_TTL_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    """Prepare one explicit Relay transition without changing active state."""
    operation = "relay_transition_prepare"
    lock_descriptor = -1
    try:
        action = _validate_action(action)
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not (1 <= ttl_seconds <= MAX_TRANSITION_TTL_SECONDS)
        ):
            raise _ControlFailure("invalid_ttl")
        current_time = _safe_now(now)
        transition_path = _absolute(transition_path)
        lock_descriptor = _acquire_lock(transition_path.parent)
        material_digest, _origin, _active, _host = _collect_material(
            action=action,
            active_config_path=active_config_path,
            prepared_config_path=prepared_config_path,
            secrets_path=secrets_path,
            host_config_path=host_config_path,
            validate=True,
        )
        transition_ref = f"relay_{secrets.token_urlsafe(24)}"
        _write_private_json(
            transition_path,
            {
                "action": action,
                "created_at": current_time,
                "expires_at": current_time + ttl_seconds,
                "material_digest": material_digest,
                "schema_version": SCHEMA_VERSION,
                "state": "prepared",
                "transition_ref": transition_ref,
            },
            allow_create=True,
        )
        return _public_result(
            ok=True,
            operation=operation,
            action=action,
            transition_ref=transition_ref,
            expires_at=current_time + ttl_seconds,
            confirmation_required=True,
            restart_required=False,
            state="prepared",
        )
    except _ControlFailure as exc:
        return _failure(operation, exc.code)
    except Exception:
        return _failure(operation, "transition_invalid")
    finally:
        if lock_descriptor >= 0:
            _release_lock(lock_descriptor)


def _transition_payload(path: Path) -> dict[str, Any]:
    _raw, payload = _read_private_json(
        path,
        missing_code="transition_not_found",
        invalid_code="transition_invalid",
    )
    if set(payload) != {
        "action",
        "created_at",
        "expires_at",
        "material_digest",
        "schema_version",
        "state",
        "transition_ref",
    }:
        raise _ControlFailure("transition_invalid")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("action") not in _ACTIONS
        or payload.get("state") not in {"prepared", "confirmed"}
        or not isinstance(payload.get("created_at"), int)
        or isinstance(payload.get("created_at"), bool)
        or not isinstance(payload.get("expires_at"), int)
        or isinstance(payload.get("expires_at"), bool)
        or payload["expires_at"] <= payload["created_at"]
        or payload["expires_at"] - payload["created_at"] > MAX_TRANSITION_TTL_SECONDS
        or not isinstance(payload.get("transition_ref"), str)
        or not (20 <= len(payload["transition_ref"]) <= 128)
        or not isinstance(payload.get("material_digest"), str)
        or len(payload["material_digest"]) != 64
    ):
        raise _ControlFailure("transition_invalid")
    try:
        bytes.fromhex(payload["material_digest"])
    except ValueError as exc:
        raise _ControlFailure("transition_invalid") from exc
    return payload


def _target_host_config(
    *,
    action: str,
    host_config: dict[str, Any],
    relay_origin: str,
) -> dict[str, Any]:
    target = dict(host_config)
    origins = list(host_config["allowed_origins"])
    if action == "enable":
        if relay_origin not in origins:
            origins.append(relay_origin)
        target["network_publication"] = "agentops_relay"
        target["cookie_secure"] = True
        target["private_console_origin"] = relay_origin
    else:
        origins = [origin for origin in origins if origin != relay_origin]
        target["network_publication"] = "disabled"
        target["cookie_secure"] = False
        if target.get("private_console_origin") == relay_origin:
            target["private_console_origin"] = ""
    target["allowed_origins"] = origins
    return target


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _execute_with_rollback(
    *,
    action: str,
    transition_ref: str,
    transition_path: Path,
    active_config_path: Path,
    prepared_config_path: Path,
    host_config_path: Path,
    active: dict[str, Any],
    host_config: dict[str, Any],
    relay_origin: str,
) -> None:
    active_original = _read_bounded_file(
        active_config_path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code="active_relay_state_invalid",
        invalid_code="active_relay_state_invalid",
    )
    host_original = _read_bounded_file(
        host_config_path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code="host_config_invalid",
        invalid_code="host_config_invalid",
    )
    if action == "enable":
        prepared_raw = _read_bounded_file(
            prepared_config_path,
            maximum_bytes=MAX_PRIVATE_JSON_BYTES,
            allowed_modes={0o600},
            missing_code="relay_material_invalid",
            invalid_code="relay_material_invalid",
        )
        active_target = prepared_raw
    else:
        active_target = _json_bytes(DISABLED_RELAY_CONFIG)
    host_target = _json_bytes(
        _target_host_config(
            action=action,
            host_config=host_config,
            relay_origin=relay_origin,
        )
    )
    del active

    journal_path = _absolute(transition_path).with_name(".relay-transition-rollback.json")
    if journal_path.exists() or journal_path.is_symlink():
        raise _ControlFailure("rollback_pending")
    _write_private_json(
        journal_path,
        {
            "active_original_b64": base64.b64encode(active_original).decode("ascii"),
            "action": action,
            "host_original_b64": base64.b64encode(host_original).decode("ascii"),
            "schema_version": SCHEMA_VERSION,
            "transition_ref": transition_ref,
        },
        allow_create=True,
    )
    try:
        _atomic_write_bytes(active_config_path, active_target)
        _atomic_write_bytes(host_config_path, host_target)
        _unlink_private(transition_path)
        _unlink_private(journal_path)
    except Exception as write_error:
        rollback_ok = True
        try:
            _atomic_write_bytes(active_config_path, active_original)
        except Exception:
            rollback_ok = False
        try:
            _atomic_write_bytes(host_config_path, host_original)
        except Exception:
            rollback_ok = False
        if rollback_ok:
            try:
                _unlink_private(journal_path)
            except _ControlFailure:
                rollback_ok = False
        if not rollback_ok:
            raise _ControlFailure("rollback_incomplete") from write_error
        raise _ControlFailure("transition_write_failed") from write_error


def _execute_with_restart_receipt(
    *,
    action: str,
    transition_ref: str,
    transition_path: Path,
    active_config_path: Path,
    prepared_config_path: Path,
    host_config_path: Path,
    restart_receipt_path: Path,
    restart_sequence_path: Path,
    active: dict[str, Any],
    host_config: dict[str, Any],
    relay_origin: str,
) -> dict[str, Any]:
    active_original = _read_bounded_file(
        active_config_path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code="active_relay_state_invalid",
        invalid_code="active_relay_state_invalid",
    )
    host_original = _read_bounded_file(
        host_config_path,
        maximum_bytes=MAX_PRIVATE_JSON_BYTES,
        allowed_modes={0o600},
        missing_code="host_config_invalid",
        invalid_code="host_config_invalid",
    )
    if action == "enable":
        active_target = _read_bounded_file(
            prepared_config_path,
            maximum_bytes=MAX_PRIVATE_JSON_BYTES,
            allowed_modes={0o600},
            missing_code="relay_material_invalid",
            invalid_code="relay_material_invalid",
        )
    else:
        active_target = _json_bytes(DISABLED_RELAY_CONFIG)
    host_target = _json_bytes(
        _target_host_config(
            action=action,
            host_config=host_config,
            relay_origin=relay_origin,
        )
    )
    del active
    receipt: dict[str, Any] | None = None
    try:
        replace_terminal = False
        try:
            existing_receipt = relay_restart.restart_recovery_context(
                receipt_path=restart_receipt_path,
                sequence_path=restart_sequence_path,
            )
        except relay_restart.RelayRestartError as existing_error:
            if existing_error.code != "receipt_not_found":
                raise
        else:
            if existing_receipt.get("state") not in {"healthy", "rolled_back"}:
                raise _ControlFailure("rollback_pending")
            audit_outbox_dir = restart_receipt_path.parent / "restart-audit-outbox"
            try:
                exact_event = relay_restart.write_restart_audit_event(
                    outbox_dir=audit_outbox_dir,
                    action=existing_receipt["action"],
                    state=existing_receipt["state"],
                    transaction_sequence=existing_receipt["transaction_sequence"],
                    revision=existing_receipt["revision"],
                    transition_ref=existing_receipt["transition_ref"],
                    nonblocking=True,
                )
            except relay_restart.RelayRestartError as audit_error:
                if audit_error.code == "audit_event_busy":
                    raise _ControlFailure("rollback_pending") from audit_error
                raise
            exact_terminal_event = bool(
                exact_event["action"] == existing_receipt["action"]
                and exact_event["state"] == existing_receipt["state"]
                and exact_event["transaction_sequence"]
                == existing_receipt["transaction_sequence"]
                and exact_event["revision"] == existing_receipt["revision"]
                and exact_event["transition_ref"] == existing_receipt["transition_ref"]
            )
            if not exact_terminal_event:
                raise _ControlFailure("rollback_pending")
            replace_terminal = True
        receipt = relay_restart.create_restart_receipt(
            receipt_path=restart_receipt_path,
            sequence_path=restart_sequence_path,
            action=action,
            transition_ref=transition_ref,
            active_config_path=active_config_path,
            host_config_path=host_config_path,
            active_original_config=active_original,
            active_target_config=active_target,
            host_original_config=host_original,
            host_target_config=host_target,
            replace_terminal=replace_terminal,
        )
        relay_restart.apply_target_configs(
            receipt_path=restart_receipt_path,
            sequence_path=restart_sequence_path,
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=int(receipt["transaction_sequence"]),
            expected_revision=int(receipt["revision"]),
        )
        _unlink_private(transition_path)
        return receipt
    except (relay_restart.RelayRestartError, _ControlFailure) as exc:
        if receipt is None:
            if isinstance(exc, _ControlFailure):
                raise
            raise _ControlFailure("transition_invalid") from exc
        try:
            restoring = relay_restart.transition_restart_receipt(
                receipt_path=restart_receipt_path,
                sequence_path=restart_sequence_path,
                action=action,
                transition_ref=transition_ref,
                transaction_sequence=int(receipt["transaction_sequence"]),
                expected_revision=int(receipt["revision"]),
                state="restoring_config",
            )
            relay_restart.restore_original_configs(
                receipt_path=restart_receipt_path,
                sequence_path=restart_sequence_path,
                action=action,
                transition_ref=transition_ref,
                transaction_sequence=int(restoring["transaction_sequence"]),
                expected_revision=int(restoring["revision"]),
            )
            relay_restart.transition_restart_receipt(
                receipt_path=restart_receipt_path,
                sequence_path=restart_sequence_path,
                action=action,
                transition_ref=transition_ref,
                transaction_sequence=int(restoring["transaction_sequence"]),
                expected_revision=int(restoring["revision"]),
                state="rolled_back",
            )
        except relay_restart.RelayRestartError as rollback_error:
            raise _ControlFailure("rollback_incomplete") from rollback_error
        raise _ControlFailure("transition_write_failed") from exc


def _validated_transition_material(
    *,
    action: str,
    transition_ref: str,
    transition: dict[str, Any],
    current_time: int,
    active_config_path: Path,
    prepared_config_path: Path,
    secrets_path: Path,
    host_config_path: Path,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if transition["action"] != action:
        raise _ControlFailure("confirmation_action_mismatch")
    if not hmac.compare_digest(transition["transition_ref"], transition_ref):
        raise _ControlFailure("confirmation_ref_mismatch")
    if current_time >= transition["expires_at"]:
        raise _ControlFailure("transition_expired")
    try:
        material_digest, relay_origin, active, host_config = _collect_material(
            action=action,
            active_config_path=active_config_path,
            prepared_config_path=prepared_config_path,
            secrets_path=secrets_path,
            host_config_path=host_config_path,
            validate=False,
        )
    except _ControlFailure as exc:
        if exc.code in {"transition_store_invalid", "host_config_invalid"}:
            raise
        raise _ControlFailure("transition_material_changed") from exc
    if not hmac.compare_digest(transition["material_digest"], material_digest):
        raise _ControlFailure("transition_material_changed")
    material_path = prepared_config_path if action == "enable" else active_config_path
    try:
        validate_connector_material(material_path, secrets_path)
    except RelayConnectorServiceError as exc:
        raise _ControlFailure("transition_material_changed") from exc
    return relay_origin, active, host_config


def public_relay_status(
    *,
    transition_path: Path,
    active_config_path: Path,
    host_config_path: Path,
    now: int | None = None,
) -> dict[str, Any]:
    """Return bounded Relay control state without material, paths, or digests."""
    operation = "relay_transition_status"
    lock_descriptor = -1
    try:
        current_time = _safe_now(now)
        transition_path = _absolute(transition_path)
        lock_descriptor = _acquire_lock(transition_path.parent)
        try:
            active = load_connector_config(active_config_path)
        except RelayConnectorServiceError as exc:
            raise _ControlFailure("active_relay_state_invalid") from exc
        _raw, host_config = _read_private_json(
            host_config_path,
            missing_code="host_config_invalid",
            invalid_code="host_config_invalid",
        )
        _host_digest_projection(host_config)
        try:
            transition = _transition_payload(transition_path)
        except _ControlFailure as exc:
            if exc.code != "transition_not_found":
                raise
            transition = None
        if transition is None:
            return _public_result(
                ok=True,
                operation=operation,
                action=None,
                transition_ref=None,
                expires_at=None,
                confirmation_required=False,
                restart_required=False,
                state="idle",
                relay_enabled=active.get("enabled") is True,
                network_publication=host_config["network_publication"],
            )
        expired = current_time >= transition["expires_at"]
        return _public_result(
            ok=True,
            operation=operation,
            action=transition["action"],
            transition_ref=transition["transition_ref"],
            expires_at=transition["expires_at"],
            confirmation_required=transition["state"] == "prepared" and not expired,
            restart_required=False,
            state="expired" if expired else transition["state"],
            relay_enabled=active.get("enabled") is True,
            network_publication=host_config["network_publication"],
        )
    except _ControlFailure as exc:
        return _failure(operation, exc.code)
    except Exception:
        return _failure(operation, "transition_invalid")
    finally:
        if lock_descriptor >= 0:
            _release_lock(lock_descriptor)


def confirm_relay_transition(
    *,
    action: str,
    transition_ref: str,
    transition_path: Path,
    active_config_path: Path,
    prepared_config_path: Path,
    secrets_path: Path,
    host_config_path: Path,
    now: int | None = None,
) -> dict[str, Any]:
    """Record confirmation only after exact material has been revalidated."""
    operation = "relay_transition_confirm"
    lock_descriptor = -1
    try:
        action = _validate_action(action)
        if not isinstance(transition_ref, str) or not transition_ref:
            raise _ControlFailure("confirmation_ref_mismatch")
        current_time = _safe_now(now)
        transition_path = _absolute(transition_path)
        lock_descriptor = _acquire_lock(transition_path.parent)
        transition = _transition_payload(transition_path)
        if transition["state"] == "confirmed":
            raise _ControlFailure("confirmation_already_recorded")
        _validated_transition_material(
            action=action,
            transition_ref=transition_ref,
            transition=transition,
            current_time=current_time,
            active_config_path=active_config_path,
            prepared_config_path=prepared_config_path,
            secrets_path=secrets_path,
            host_config_path=host_config_path,
        )
        confirmed = dict(transition)
        confirmed["state"] = "confirmed"
        _write_private_json(transition_path, confirmed, allow_create=False)
        return _public_result(
            ok=True,
            operation=operation,
            action=action,
            transition_ref=transition_ref,
            expires_at=transition["expires_at"],
            confirmation_required=False,
            restart_required=False,
            state="confirmed",
        )
    except _ControlFailure as exc:
        return _failure(operation, exc.code)
    except Exception:
        return _failure(operation, "transition_invalid")
    finally:
        if lock_descriptor >= 0:
            _release_lock(lock_descriptor)


def execute_confirmed_relay_transition(
    *,
    action: str,
    transition_ref: str,
    transition_path: Path,
    active_config_path: Path,
    prepared_config_path: Path,
    secrets_path: Path,
    host_config_path: Path,
    restart_receipt_path: Path | None = None,
    restart_sequence_path: Path | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Execute one confirmed transition transactionally, then consume it."""
    operation = "relay_transition_execute"
    lock_descriptor = -1
    try:
        action = _validate_action(action)
        if not isinstance(transition_ref, str) or not transition_ref:
            raise _ControlFailure("confirmation_ref_mismatch")
        current_time = _safe_now(now)
        transition_path = _absolute(transition_path)
        lock_descriptor = _acquire_lock(transition_path.parent)
        transition = _transition_payload(transition_path)
        if transition["state"] != "confirmed":
            raise _ControlFailure("confirmation_required")
        relay_origin, active, host_config = _validated_transition_material(
            action=action,
            transition_ref=transition_ref,
            transition=transition,
            current_time=current_time,
            active_config_path=active_config_path,
            prepared_config_path=prepared_config_path,
            secrets_path=secrets_path,
            host_config_path=host_config_path,
        )
        restart_receipt = None
        if restart_receipt_path is not None and restart_sequence_path is not None:
            restart_receipt = _execute_with_restart_receipt(
                action=action,
                transition_ref=transition_ref,
                transition_path=transition_path,
                active_config_path=active_config_path,
                prepared_config_path=prepared_config_path,
                host_config_path=host_config_path,
                restart_receipt_path=restart_receipt_path,
                restart_sequence_path=restart_sequence_path,
                active=active,
                host_config=host_config,
                relay_origin=relay_origin,
            )
        else:
            _execute_with_rollback(
                action=action,
                transition_ref=transition_ref,
                transition_path=transition_path,
                active_config_path=active_config_path,
                prepared_config_path=prepared_config_path,
                host_config_path=host_config_path,
                active=active,
                host_config=host_config,
                relay_origin=relay_origin,
            )
        return _public_result(
            ok=True,
            operation=operation,
            action=action,
            transition_ref=transition_ref,
            expires_at=transition["expires_at"],
            confirmation_required=False,
            restart_required=True,
            network_publication="agentops_relay" if action == "enable" else "disabled",
            state="executed",
            **(
                {
                    "transaction_sequence": restart_receipt["transaction_sequence"],
                    "revision": restart_receipt["revision"],
                    "rollback_armed": True,
                }
                if restart_receipt is not None
                else {}
            ),
        )
    except _ControlFailure as exc:
        return _failure(operation, exc.code)
    except Exception:
        return _failure(operation, "transition_invalid")
    finally:
        if lock_descriptor >= 0:
            _release_lock(lock_descriptor)
