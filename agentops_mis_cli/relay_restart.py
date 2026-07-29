"""Private, durable restart receipts for coordinated Relay config changes."""
from __future__ import annotations

import base64
import binascii
import fcntl
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
SEQUENCE_SCHEMA_VERSION = 1
AUDIT_EVENT_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 384 * 1024
MAX_SEQUENCE_BYTES = 4096
MAX_AUDIT_EVENT_BYTES = 4096
MAX_AUDIT_OUTBOX_ENTRIES = 1024
MAX_TRANSACTION_SEQUENCE = (1 << 63) - 1
MAX_REVISION = (1 << 31) - 1
MAX_TRANSITION_REF_BYTES = 128

STATES = frozenset(
    {
        "config_applied",
        "response_flushed",
        "restart_requested",
        "validating_new_host",
        "healthy",
        "restoring_config",
        "rolled_back",
        "rollback_failed",
        "manual_restart_required",
    }
)
TERMINAL_STATES = frozenset(
    {"healthy", "rolled_back", "rollback_failed"}
)

_TARGET_RECOVERY_STATES = frozenset(
    {
        "response_flushed",
        "restart_requested",
        "validating_new_host",
        "manual_restart_required",
        "healthy",
    }
)
_ORIGINAL_RECOVERY_STATES = frozenset({"restoring_config", "rolled_back"})

_ACTIONS = frozenset({"disable", "enable"})
_TRANSITION_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_TRANSITIONS = {
    "config_applied": frozenset({"response_flushed", "restoring_config"}),
    "response_flushed": frozenset(
        {"restart_requested", "restoring_config", "manual_restart_required"}
    ),
    "restart_requested": frozenset(
        {"validating_new_host", "restoring_config", "manual_restart_required"}
    ),
    "validating_new_host": frozenset(
        {"healthy", "restoring_config", "manual_restart_required"}
    ),
    "restoring_config": frozenset({"rolled_back", "rollback_failed"}),
    "healthy": frozenset({"restoring_config"}),
    "rolled_back": frozenset(),
    "rollback_failed": frozenset(),
    "manual_restart_required": frozenset(
        {"validating_new_host", "restoring_config"}
    ),
}
_RECEIPT_KEYS = frozenset(
    {
        "action",
        "active_config_path",
        "active_original_config_b64",
        "active_target_config_b64",
        "host_config_path",
        "host_original_config_b64",
        "host_target_config_b64",
        "revision",
        "schema_version",
        "sequence_path",
        "state",
        "transaction_sequence",
        "transition_ref",
    }
)
_SEQUENCE_KEYS = frozenset({"last_transaction_sequence", "schema_version"})
_AUDIT_EVENT_KEYS = frozenset(
    {
        "action",
        "revision",
        "schema_version",
        "state",
        "transaction_sequence",
        "transition_ref",
    }
)
_AUDIT_EVENT_NAME_PATTERN = re.compile(r"restart-(0|[1-9][0-9]{0,18})[.]json")
_ERROR_CODES = frozenset(
    {
        "audit_event_invalid",
        "audit_event_busy",
        "audit_event_not_found",
        "archive_exists",
        "config_pair_invalid",
        "config_pair_rollback_failed",
        "config_pair_write_failed",
        "config_paths_invalid",
        "config_too_large",
        "invalid_action",
        "invalid_config_bytes",
        "invalid_revision",
        "invalid_state",
        "invalid_state_transition",
        "invalid_transaction_sequence",
        "invalid_transition_ref",
        "receipt_active",
        "receipt_already_exists",
        "receipt_invalid",
        "receipt_not_found",
        "sequence_binding_mismatch",
        "sequence_exhausted",
        "sequence_invalid",
        "stale_revision",
        "stale_transaction_sequence",
        "target_invalid",
        "terminal_required",
        "transition_ref_mismatch",
        "write_failed",
    }
)


class RelayRestartError(RuntimeError):
    """Bounded failure that never includes config bytes, refs, or paths."""

    def __init__(self, code: str) -> None:
        safe_code = code if code in _ERROR_CODES else "receipt_invalid"
        super().__init__(safe_code)
        self.code = safe_code


def _absolute(path: Path) -> Path:
    try:
        return Path(os.path.abspath(Path(path).expanduser()))
    except (OSError, TypeError, ValueError) as exc:
        raise RelayRestartError("target_invalid") from exc


def _private_directory(path: Path, *, create: bool = False) -> None:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise RelayRestartError("target_invalid") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RelayRestartError("target_invalid")


def _open_private_regular(path: Path, flags: int, *, missing_code: str) -> int:
    descriptor = -1
    try:
        descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RelayRestartError("target_invalid")
        return descriptor
    except FileNotFoundError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise RelayRestartError(missing_code) from exc
    except RelayRestartError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise RelayRestartError("target_invalid") from exc


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RelayRestartError("target_invalid") from exc


def _read_private_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    missing_code: str,
    invalid_code: str,
    allow_empty: bool = False,
) -> bytes:
    path = _absolute(path)
    _private_directory(path.parent)
    descriptor = _open_private_regular(path, os.O_RDONLY, missing_code=missing_code)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size > maximum_bytes or (
            metadata.st_size == 0 and not allow_empty
        ):
            raise RelayRestartError(invalid_code)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum_bytes or (not payload and not allow_empty):
            raise RelayRestartError(invalid_code)
        return payload
    except RelayRestartError:
        raise
    except OSError as exc:
        raise RelayRestartError(invalid_code) from exc
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise RelayRestartError("write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_private_write(path: Path, payload: bytes, *, allow_create: bool) -> None:
    path = _absolute(path)
    _private_directory(path.parent, create=allow_create)
    exists = _path_present(path)
    if not exists and not allow_create:
        raise RelayRestartError("target_invalid")
    if exists:
        descriptor = _open_private_regular(
            path, os.O_RDONLY, missing_code="target_invalid"
        )
        os.close(descriptor)

    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except RelayRestartError:
        raise
    except OSError as exc:
        raise RelayRestartError("write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _unlink_private(path: Path) -> None:
    path = _absolute(path)
    descriptor = _open_private_regular(
        path, os.O_RDONLY, missing_code="receipt_not_found"
    )
    os.close(descriptor)
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise RelayRestartError("write_failed") from exc


def _acquire_lock(target_path: Path, *, nonblocking: bool = False) -> int:
    target_path = _absolute(target_path)
    _private_directory(target_path.parent, create=True)
    lock_path = target_path.with_name(f".{target_path.name}.lock")
    descriptor = _open_private_regular(
        lock_path,
        os.O_RDWR | os.O_CREAT,
        missing_code="target_invalid",
    )
    try:
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(descriptor, flags)
        return descriptor
    except BlockingIOError as exc:
        os.close(descriptor)
        raise RelayRestartError("audit_event_busy") from exc
    except OSError as exc:
        os.close(descriptor)
        raise RelayRestartError("target_invalid") from exc


def _release_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _config_bytes(value: bytes) -> bytes:
    if not isinstance(value, bytes):
        raise RelayRestartError("invalid_config_bytes")
    if len(value) > MAX_CONFIG_BYTES:
        raise RelayRestartError("config_too_large")
    return value


def _validate_action(action: str) -> str:
    if action not in _ACTIONS:
        raise RelayRestartError("invalid_action")
    return action


def _validate_transition_ref(transition_ref: str) -> str:
    if not isinstance(transition_ref, str):
        raise RelayRestartError("invalid_transition_ref")
    try:
        encoded = transition_ref.encode("ascii")
    except UnicodeError as exc:
        raise RelayRestartError("invalid_transition_ref") from exc
    if (
        not encoded
        or len(encoded) > MAX_TRANSITION_REF_BYTES
        or _TRANSITION_REF_PATTERN.fullmatch(transition_ref) is None
    ):
        raise RelayRestartError("invalid_transition_ref")
    return transition_ref


def _validate_transaction_sequence(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (1 <= value <= MAX_TRANSACTION_SEQUENCE)
    ):
        raise RelayRestartError("invalid_transaction_sequence")
    return value


def _validate_revision(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (1 <= value <= MAX_REVISION)
    ):
        raise RelayRestartError("invalid_revision")
    return value


def _restart_audit_event_payload(
    *,
    action: str,
    state: str,
    transaction_sequence: int,
    revision: int,
    transition_ref: str,
) -> dict[str, Any]:
    action = _validate_action(action)
    if state not in TERMINAL_STATES:
        raise RelayRestartError("terminal_required")
    return {
        "action": action,
        "revision": _validate_revision(revision),
        "schema_version": AUDIT_EVENT_SCHEMA_VERSION,
        "state": state,
        "transaction_sequence": _validate_transaction_sequence(
            transaction_sequence
        ),
        "transition_ref": _validate_transition_ref(transition_ref),
    }


def _encode_restart_audit_event(payload: dict[str, Any]) -> bytes:
    raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(raw) > MAX_AUDIT_EVENT_BYTES:
        raise RelayRestartError("audit_event_invalid")
    return raw


def _decode_restart_audit_event(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RelayRestartError("audit_event_invalid") from exc
    if (
        not isinstance(payload, dict)
        or frozenset(payload) != _AUDIT_EVENT_KEYS
        or payload.get("schema_version") != AUDIT_EVENT_SCHEMA_VERSION
    ):
        raise RelayRestartError("audit_event_invalid")
    try:
        return _restart_audit_event_payload(
            action=payload.get("action"),
            state=payload.get("state"),
            transaction_sequence=payload.get("transaction_sequence"),
            revision=payload.get("revision"),
            transition_ref=payload.get("transition_ref"),
        )
    except RelayRestartError as exc:
        raise RelayRestartError("audit_event_invalid") from exc


def _decode_config(value: Any) -> bytes:
    if not isinstance(value, str):
        raise RelayRestartError("receipt_invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error) as exc:
        raise RelayRestartError("receipt_invalid") from exc
    if len(decoded) > MAX_CONFIG_BYTES:
        raise RelayRestartError("receipt_invalid")
    return decoded


def _encode_receipt(payload: dict[str, Any]) -> bytes:
    raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise RelayRestartError("config_too_large")
    return raw


def _decode_receipt(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RelayRestartError("receipt_invalid") from exc
    if not isinstance(payload, dict) or frozenset(payload) != _RECEIPT_KEYS:
        raise RelayRestartError("receipt_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RelayRestartError("receipt_invalid")
    if payload.get("action") not in _ACTIONS or payload.get("state") not in STATES:
        raise RelayRestartError("receipt_invalid")
    try:
        _validate_transition_ref(payload.get("transition_ref"))
        _validate_transaction_sequence(payload.get("transaction_sequence"))
        _validate_revision(payload.get("revision"))
    except RelayRestartError as exc:
        raise RelayRestartError("receipt_invalid") from exc

    path_fields = ("active_config_path", "host_config_path", "sequence_path")
    if any(
        not isinstance(payload.get(field), str)
        or str(_absolute(Path(payload[field]))) != payload[field]
        for field in path_fields
    ):
        raise RelayRestartError("receipt_invalid")
    if len({payload[field] for field in path_fields}) != len(path_fields):
        raise RelayRestartError("receipt_invalid")

    decoded = dict(payload)
    for stored_field, decoded_field in (
        ("active_original_config_b64", "active_original_config"),
        ("active_target_config_b64", "active_target_config"),
        ("host_original_config_b64", "host_original_config"),
        ("host_target_config_b64", "host_target_config"),
    ):
        decoded[decoded_field] = _decode_config(payload.get(stored_field))
    return decoded


def _stored_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: receipt[key] for key in _RECEIPT_KEYS}


def _load_receipt_locked(receipt_path: Path) -> dict[str, Any]:
    raw = _read_private_bytes(
        receipt_path,
        maximum_bytes=MAX_RECEIPT_BYTES,
        missing_code="receipt_not_found",
        invalid_code="receipt_invalid",
    )
    return _decode_receipt(raw)


def _read_sequence_locked(sequence_path: Path, *, allow_missing: bool) -> int:
    if allow_missing and not _path_present(sequence_path):
        return 0
    raw = _read_private_bytes(
        sequence_path,
        maximum_bytes=MAX_SEQUENCE_BYTES,
        missing_code="sequence_invalid",
        invalid_code="sequence_invalid",
    )
    try:
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RelayRestartError("sequence_invalid") from exc
    if (
        not isinstance(payload, dict)
        or frozenset(payload) != _SEQUENCE_KEYS
        or payload.get("schema_version") != SEQUENCE_SCHEMA_VERSION
    ):
        raise RelayRestartError("sequence_invalid")
    value = payload.get("last_transaction_sequence")
    try:
        return _validate_transaction_sequence(value)
    except RelayRestartError as exc:
        raise RelayRestartError("sequence_invalid") from exc


def _write_sequence_locked(sequence_path: Path, value: int) -> None:
    raw = (
        json.dumps(
            {
                "last_transaction_sequence": value,
                "schema_version": SEQUENCE_SCHEMA_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    _atomic_private_write(sequence_path, raw, allow_create=True)


def _allocate_transaction_sequence(sequence_path: Path) -> int:
    sequence_path = _absolute(sequence_path)
    lock_descriptor = _acquire_lock(sequence_path)
    try:
        last_sequence = _read_sequence_locked(sequence_path, allow_missing=True)
        if last_sequence >= MAX_TRANSACTION_SEQUENCE:
            raise RelayRestartError("sequence_exhausted")
        next_sequence = last_sequence + 1
        _write_sequence_locked(sequence_path, next_sequence)
        return next_sequence
    finally:
        _release_lock(lock_descriptor)


def _verify_sequence(
    receipt: dict[str, Any],
    sequence_path: Path,
    *,
    nonblocking: bool = False,
) -> None:
    sequence_path = _absolute(sequence_path)
    if receipt["sequence_path"] != str(sequence_path):
        raise RelayRestartError("sequence_binding_mismatch")
    lock_descriptor = _acquire_lock(sequence_path, nonblocking=nonblocking)
    try:
        last_sequence = _read_sequence_locked(sequence_path, allow_missing=False)
    finally:
        _release_lock(lock_descriptor)
    if last_sequence < receipt["transaction_sequence"]:
        raise RelayRestartError("sequence_invalid")


def _validate_bound_paths(
    *,
    receipt_path: Path,
    sequence_path: Path,
    active_config_path: Path,
    host_config_path: Path,
) -> tuple[Path, Path, Path, Path]:
    paths = tuple(
        _absolute(path)
        for path in (
            receipt_path,
            sequence_path,
            active_config_path,
            host_config_path,
        )
    )
    if len(set(paths)) != len(paths):
        raise RelayRestartError("config_paths_invalid")
    for config_path in paths[2:]:
        _private_directory(config_path.parent, create=True)
        if _path_present(config_path):
            descriptor = _open_private_regular(
                config_path, os.O_RDONLY, missing_code="target_invalid"
            )
            os.close(descriptor)
    return paths


def _check_identity(
    receipt: dict[str, Any],
    *,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
) -> None:
    _validate_action(action)
    _validate_transition_ref(transition_ref)
    _validate_transaction_sequence(transaction_sequence)
    _validate_revision(expected_revision)
    _verify_sequence(receipt, sequence_path)
    if receipt["transaction_sequence"] != transaction_sequence:
        raise RelayRestartError("stale_transaction_sequence")
    if receipt["transition_ref"] != transition_ref:
        raise RelayRestartError("transition_ref_mismatch")
    if receipt["action"] != action:
        raise RelayRestartError("invalid_action")
    if receipt["revision"] != expected_revision:
        raise RelayRestartError("stale_revision")


def _public_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    state = receipt["state"]
    return {
        "action": receipt["action"],
        "state": state,
        "transaction_sequence": receipt["transaction_sequence"],
        "revision": receipt["revision"],
        "restart_required": state not in {"healthy", "rolled_back"},
        "restart_requested": state
        in {
            "restart_requested",
            "validating_new_host",
            "healthy",
            "restoring_config",
            "rolled_back",
            "rollback_failed",
        },
        "manual_restart_required": state == "manual_restart_required",
        "original_configs_omitted": True,
        "target_configs_omitted": True,
        "transition_ref_omitted": True,
        "private_paths_omitted": True,
        "digests_omitted": True,
    }


def _recovery_context(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": receipt["action"],
        "state": receipt["state"],
        "transaction_sequence": receipt["transaction_sequence"],
        "revision": receipt["revision"],
        "transition_ref": receipt["transition_ref"],
    }


def create_restart_receipt(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    active_config_path: Path,
    host_config_path: Path,
    active_original_config: bytes,
    active_target_config: bytes,
    host_original_config: bytes,
    host_target_config: bytes,
    replace_terminal: bool = False,
) -> dict[str, Any]:
    """Create a new transaction or replace an existing terminal receipt."""
    action = _validate_action(action)
    transition_ref = _validate_transition_ref(transition_ref)
    if not isinstance(replace_terminal, bool):
        raise RelayRestartError("receipt_invalid")
    configs = tuple(
        _config_bytes(value)
        for value in (
            active_original_config,
            active_target_config,
            host_original_config,
            host_target_config,
        )
    )
    (
        receipt_path,
        sequence_path,
        active_config_path,
        host_config_path,
    ) = _validate_bound_paths(
        receipt_path=receipt_path,
        sequence_path=sequence_path,
        active_config_path=active_config_path,
        host_config_path=host_config_path,
    )

    lock_descriptor = _acquire_lock(receipt_path)
    try:
        if _path_present(receipt_path):
            existing = _load_receipt_locked(receipt_path)
            _verify_sequence(existing, sequence_path)
            exact_initial_retry = bool(
                existing["state"] == "config_applied"
                and existing["action"] == action
                and existing["transition_ref"] == transition_ref
                and existing["active_config_path"] == str(active_config_path)
                and existing["host_config_path"] == str(host_config_path)
                and existing["active_original_config"] == configs[0]
                and existing["active_target_config"] == configs[1]
                and existing["host_original_config"] == configs[2]
                and existing["host_target_config"] == configs[3]
            )
            if exact_initial_retry:
                return _public_projection(existing)
            if existing["state"] not in TERMINAL_STATES:
                raise RelayRestartError("receipt_active")
            if not replace_terminal:
                raise RelayRestartError("receipt_already_exists")

        transaction_sequence = _allocate_transaction_sequence(sequence_path)
        receipt = {
            "action": action,
            "active_config_path": str(active_config_path),
            "active_original_config_b64": base64.b64encode(configs[0]).decode(
                "ascii"
            ),
            "active_target_config_b64": base64.b64encode(configs[1]).decode(
                "ascii"
            ),
            "host_config_path": str(host_config_path),
            "host_original_config_b64": base64.b64encode(configs[2]).decode(
                "ascii"
            ),
            "host_target_config_b64": base64.b64encode(configs[3]).decode(
                "ascii"
            ),
            "revision": 1,
            "schema_version": SCHEMA_VERSION,
            "sequence_path": str(sequence_path),
            "state": "config_applied",
            "transaction_sequence": transaction_sequence,
            "transition_ref": transition_ref,
        }
        _atomic_private_write(
            receipt_path,
            _encode_receipt(receipt),
            allow_create=True,
        )
        return _public_projection(receipt)
    finally:
        _release_lock(lock_descriptor)


def public_restart_receipt(
    *, receipt_path: Path, sequence_path: Path
) -> dict[str, Any]:
    """Read the bounded public projection without private receipt material."""
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _verify_sequence(receipt, sequence_path)
        return _public_projection(receipt)
    finally:
        _release_lock(lock_descriptor)


def restart_recovery_context(
    *,
    receipt_path: Path,
    sequence_path: Path,
    nonblocking: bool = False,
) -> dict[str, Any]:
    """Read only the identity and state required to resume one private receipt."""
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path, nonblocking=nonblocking)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _verify_sequence(receipt, sequence_path, nonblocking=nonblocking)
        return _recovery_context(receipt)
    finally:
        _release_lock(lock_descriptor)


def transition_restart_receipt(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
    state: str,
) -> dict[str, Any]:
    """Apply one ordered state revision with transaction replay protection."""
    if state not in STATES:
        raise RelayRestartError("invalid_state")
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _check_identity(
            receipt,
            sequence_path=sequence_path,
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=transaction_sequence,
            expected_revision=expected_revision,
        )
        if receipt["state"] == state:
            return _public_projection(receipt)
        if state not in _TRANSITIONS[receipt["state"]]:
            raise RelayRestartError("invalid_state_transition")
        if receipt["revision"] >= MAX_REVISION:
            raise RelayRestartError("invalid_revision")
        receipt["state"] = state
        receipt["revision"] += 1
        _atomic_private_write(
            receipt_path,
            _encode_receipt(_stored_receipt(receipt)),
            allow_create=False,
        )
        return _public_projection(receipt)
    finally:
        _release_lock(lock_descriptor)


def _write_config_pair(
    *,
    receipt: dict[str, Any],
    use_target: bool,
) -> None:
    active_path = Path(receipt["active_config_path"])
    host_path = Path(receipt["host_config_path"])
    active_source = receipt[
        "active_original_config" if use_target else "active_target_config"
    ]
    host_source = receipt[
        "host_original_config" if use_target else "host_target_config"
    ]
    active_destination = receipt[
        "active_target_config" if use_target else "active_original_config"
    ]
    host_destination = receipt[
        "host_target_config" if use_target else "host_original_config"
    ]
    active_current = _read_private_bytes(
        active_path,
        maximum_bytes=MAX_CONFIG_BYTES,
        missing_code="config_pair_invalid",
        invalid_code="config_pair_invalid",
        allow_empty=True,
    )
    host_current = _read_private_bytes(
        host_path,
        maximum_bytes=MAX_CONFIG_BYTES,
        missing_code="config_pair_invalid",
        invalid_code="config_pair_invalid",
        allow_empty=True,
    )
    if (
        active_current == active_destination
        and host_current == host_destination
    ):
        return
    if active_current not in {active_source, active_destination} or host_current not in {
        host_source,
        host_destination,
    }:
        raise RelayRestartError("config_pair_invalid")

    try:
        _atomic_private_write(active_path, active_destination, allow_create=False)
        _atomic_private_write(host_path, host_destination, allow_create=False)
    except RelayRestartError as write_error:
        try:
            _atomic_private_write(active_path, active_source, allow_create=False)
            _atomic_private_write(host_path, host_source, allow_create=False)
        except RelayRestartError as rollback_error:
            raise RelayRestartError("config_pair_rollback_failed") from rollback_error
        raise RelayRestartError("config_pair_write_failed") from write_error


def _change_config_pair(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
    use_target: bool,
) -> dict[str, Any]:
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _check_identity(
            receipt,
            sequence_path=sequence_path,
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=transaction_sequence,
            expected_revision=expected_revision,
        )
        allowed_state = "config_applied" if use_target else "restoring_config"
        if receipt["state"] != allowed_state:
            raise RelayRestartError("invalid_state_transition")
        _write_config_pair(receipt=receipt, use_target=use_target)
        return _public_projection(receipt)
    finally:
        _release_lock(lock_descriptor)


def apply_target_configs(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
) -> dict[str, Any]:
    """Apply both target configs, restoring both originals on any failure."""
    return _change_config_pair(
        receipt_path=receipt_path,
        sequence_path=sequence_path,
        action=action,
        transition_ref=transition_ref,
        transaction_sequence=transaction_sequence,
        expected_revision=expected_revision,
        use_target=True,
    )


def restore_original_configs(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
) -> dict[str, Any]:
    """Restore both original configs, reapplying both targets on failure."""
    return _change_config_pair(
        receipt_path=receipt_path,
        sequence_path=sequence_path,
        action=action,
        transition_ref=transition_ref,
        transaction_sequence=transaction_sequence,
        expected_revision=expected_revision,
        use_target=False,
    )


def ensure_restart_recovery_configs(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
    use_target: bool,
) -> dict[str, Any]:
    """Idempotently restore the config pair implied by one recoverable state."""
    if not isinstance(use_target, bool):
        raise RelayRestartError("receipt_invalid")
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _check_identity(
            receipt,
            sequence_path=sequence_path,
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=transaction_sequence,
            expected_revision=expected_revision,
        )
        state = receipt["state"]
        if use_target and state not in _TARGET_RECOVERY_STATES:
            raise RelayRestartError("invalid_state")
        if not use_target and state not in _ORIGINAL_RECOVERY_STATES:
            raise RelayRestartError("invalid_state")
        _write_config_pair(receipt=receipt, use_target=use_target)
        return _public_projection(receipt)
    finally:
        _release_lock(lock_descriptor)


def finalize_restart_receipt(
    *,
    receipt_path: Path,
    sequence_path: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    expected_revision: int,
    archive_path: Path | None = None,
) -> dict[str, Any]:
    """Durably archive optionally and remove one terminal receipt."""
    receipt_path = _absolute(receipt_path)
    lock_descriptor = _acquire_lock(receipt_path)
    try:
        receipt = _load_receipt_locked(receipt_path)
        _check_identity(
            receipt,
            sequence_path=sequence_path,
            action=action,
            transition_ref=transition_ref,
            transaction_sequence=transaction_sequence,
            expected_revision=expected_revision,
        )
        if receipt["state"] not in TERMINAL_STATES:
            raise RelayRestartError("terminal_required")
        if archive_path is not None:
            archive_path = _absolute(archive_path)
            if archive_path in {receipt_path, _absolute(sequence_path)}:
                raise RelayRestartError("config_paths_invalid")
            _private_directory(archive_path.parent, create=True)
            if _path_present(archive_path):
                raise RelayRestartError("archive_exists")
            _atomic_private_write(
                archive_path,
                _encode_receipt(_stored_receipt(receipt)),
                allow_create=True,
            )
        projection = _public_projection(receipt)
        _unlink_private(receipt_path)
        return projection
    finally:
        _release_lock(lock_descriptor)


def write_restart_audit_event(
    *,
    outbox_dir: Path,
    action: str,
    state: str,
    transaction_sequence: int,
    revision: int,
    transition_ref: str,
    nonblocking: bool = False,
) -> dict[str, Any]:
    """Durably enqueue one bounded terminal outcome for MIS audit ingestion."""
    payload = _restart_audit_event_payload(
        action=action,
        state=state,
        transaction_sequence=transaction_sequence,
        revision=revision,
        transition_ref=transition_ref,
    )
    outbox_dir = _absolute(outbox_dir)
    _private_directory(outbox_dir, create=True)
    event_path = outbox_dir / f"restart-{payload['transaction_sequence']}.json"
    lock_descriptor = _acquire_lock(
        outbox_dir / "events",
        nonblocking=nonblocking,
    )
    try:
        if _path_present(event_path):
            existing = _decode_restart_audit_event(
                _read_private_bytes(
                    event_path,
                    maximum_bytes=MAX_AUDIT_EVENT_BYTES,
                    missing_code="audit_event_not_found",
                    invalid_code="audit_event_invalid",
                )
            )
            if existing != payload:
                replaces_unfinalized_healthy = bool(
                    existing["transaction_sequence"]
                    == payload["transaction_sequence"]
                    and existing["transition_ref"] == payload["transition_ref"]
                    and existing["action"] == payload["action"]
                    and existing["state"] == "healthy"
                    and payload["state"] in {"rolled_back", "rollback_failed"}
                    and payload["revision"] > existing["revision"]
                )
                if not replaces_unfinalized_healthy:
                    raise RelayRestartError("audit_event_invalid")
                _atomic_private_write(
                    event_path,
                    _encode_restart_audit_event(payload),
                    allow_create=False,
                )
                return dict(payload)
            return dict(existing)
        if len(_restart_audit_event_candidates_locked(outbox_dir)) >= MAX_AUDIT_OUTBOX_ENTRIES:
            raise RelayRestartError("audit_event_invalid")
        _atomic_private_write(
            event_path,
            _encode_restart_audit_event(payload),
            allow_create=True,
        )
        return dict(payload)
    finally:
        _release_lock(lock_descriptor)


def _quarantine_restart_audit_event_locked(
    outbox_dir: Path,
    *,
    transaction_sequence: int,
    event_path: Path,
) -> Path:
    """Preserve one malformed event as owner-only repair evidence."""
    quarantine_dir = outbox_dir / "quarantine"
    _private_directory(quarantine_dir, create=True)
    for suffix in range(MAX_AUDIT_OUTBOX_ENTRIES + 1):
        marker = "" if suffix == 0 else f".{suffix}"
        quarantine_path = quarantine_dir / (
            f"restart-{transaction_sequence}{marker}.invalid"
        )
        if _path_present(quarantine_path):
            continue
        try:
            os.replace(event_path, quarantine_path)
            os.chmod(quarantine_path, 0o600, follow_symlinks=False)
            _fsync_directory(quarantine_dir)
            _fsync_directory(outbox_dir)
            return quarantine_path
        except OSError as exc:
            raise RelayRestartError("write_failed") from exc
    raise RelayRestartError("audit_event_invalid")


def _restart_audit_event_candidates_locked(
    outbox_dir: Path,
) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []
    try:
        with os.scandir(outbox_dir) as entries:
            for entry in entries:
                match = _AUDIT_EVENT_NAME_PATTERN.fullmatch(entry.name)
                if match is None:
                    continue
                sequence = _validate_transaction_sequence(int(match.group(1)))
                candidates.append((sequence, Path(entry.path)))
                if len(candidates) > MAX_AUDIT_OUTBOX_ENTRIES:
                    raise RelayRestartError("audit_event_invalid")
    except RelayRestartError:
        raise
    except OSError as exc:
        raise RelayRestartError("target_invalid") from exc
    return candidates


def read_restart_audit_event(
    *,
    outbox_dir: Path,
    transaction_sequence: int,
    nonblocking: bool = False,
) -> dict[str, Any]:
    """Read one exact event without scanning or exposing its private path."""
    transaction_sequence = _validate_transaction_sequence(transaction_sequence)
    outbox_dir = _absolute(outbox_dir)
    _private_directory(outbox_dir)
    event_path = outbox_dir / f"restart-{transaction_sequence}.json"
    lock_descriptor = _acquire_lock(
        outbox_dir / "events",
        nonblocking=nonblocking,
    )
    try:
        event = _decode_restart_audit_event(
            _read_private_bytes(
                event_path,
                maximum_bytes=MAX_AUDIT_EVENT_BYTES,
                missing_code="audit_event_not_found",
                invalid_code="audit_event_invalid",
            )
        )
        if event["transaction_sequence"] != transaction_sequence:
            raise RelayRestartError("audit_event_invalid")
        return event
    finally:
        _release_lock(lock_descriptor)


def pending_restart_audit_events(
    *,
    outbox_dir: Path,
    limit: int = 32,
    nonblocking: bool = False,
) -> list[dict[str, Any]]:
    """Read a bounded ordered batch without exposing private filesystem paths."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 64):
        raise RelayRestartError("audit_event_invalid")
    outbox_dir = _absolute(outbox_dir)
    if not _path_present(outbox_dir):
        return []
    _private_directory(outbox_dir)
    lock_descriptor = _acquire_lock(
        outbox_dir / "events",
        nonblocking=nonblocking,
    )
    try:
        candidates = _restart_audit_event_candidates_locked(outbox_dir)
        events: list[dict[str, Any]] = []
        for sequence, path in sorted(candidates):
            if len(events) >= limit:
                break
            try:
                raw = _read_private_bytes(
                    path,
                    maximum_bytes=MAX_AUDIT_EVENT_BYTES,
                    missing_code="audit_event_not_found",
                    invalid_code="audit_event_invalid",
                )
                event = _decode_restart_audit_event(raw)
            except RelayRestartError as exc:
                if exc.code != "audit_event_invalid":
                    raise
                _quarantine_restart_audit_event_locked(
                    outbox_dir,
                    transaction_sequence=sequence,
                    event_path=path,
                )
                continue
            if event["transaction_sequence"] != sequence:
                _quarantine_restart_audit_event_locked(
                    outbox_dir,
                    transaction_sequence=sequence,
                    event_path=path,
                )
                continue
            events.append(event)
        return events
    finally:
        _release_lock(lock_descriptor)


def acknowledge_restart_audit_event(
    *,
    outbox_dir: Path,
    transaction_sequence: int,
    revision: int,
    transition_ref: str,
    nonblocking: bool = False,
) -> None:
    """Delete only the exact event already committed to the MIS ledger."""
    transaction_sequence = _validate_transaction_sequence(transaction_sequence)
    revision = _validate_revision(revision)
    transition_ref = _validate_transition_ref(transition_ref)
    outbox_dir = _absolute(outbox_dir)
    _private_directory(outbox_dir)
    event_path = outbox_dir / f"restart-{transaction_sequence}.json"
    lock_descriptor = _acquire_lock(
        outbox_dir / "events",
        nonblocking=nonblocking,
    )
    try:
        event = _decode_restart_audit_event(
            _read_private_bytes(
                event_path,
                maximum_bytes=MAX_AUDIT_EVENT_BYTES,
                missing_code="audit_event_not_found",
                invalid_code="audit_event_invalid",
            )
        )
        if (
            event["transaction_sequence"] != transaction_sequence
            or event["revision"] != revision
            or event["transition_ref"] != transition_ref
        ):
            raise RelayRestartError("audit_event_invalid")
        _unlink_private(event_path)
    finally:
        _release_lock(lock_descriptor)
