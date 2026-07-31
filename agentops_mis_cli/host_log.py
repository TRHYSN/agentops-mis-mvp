"""Fail-closed planning and stopped-Host rotation for private Host logs."""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path


HOST_LOG_ROTATE_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
HOST_LOG_ROTATE_MIN_MAX_BYTES = 1024 * 1024
HOST_LOG_ROTATE_DEFAULT_BACKUPS = 5
HOST_LOG_ROTATE_MIN_BACKUPS = 2
HOST_LOG_ROTATE_MAX_BACKUPS = 20
HOST_LOG_ROTATE_OUTPUT_LIMIT = 100
HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT = 256
HOST_LOG_ROTATE_JOURNAL_MAX_BYTES = 128 * 1024
_BACKUP_PATTERN = re.compile(r"^host\.log\.([1-9][0-9]*)$")
_JOURNAL_NAME = ".agentops-log-rotate-journal.json"
_JOURNAL_TEMP_PATTERN = re.compile(r"^\.agentops-log-rotate-journal\.json\.tmp-[0-9a-f]{16}$")
_STAGE_PREFIX = ".agentops-log-rotate-stage-"
_STAGE_PATTERN = re.compile(r"^\.agentops-log-rotate-stage-[0-9a-f]{32}$")


def _error(error: str, **extra) -> tuple[dict, int]:
    return {
        "ok": False,
        "operation": "host_log_rotate",
        "dry_run": True,
        "error": error,
        "content_omitted": True,
        "paths_omitted": True,
        "token_omitted": True,
        **extra,
    }, 1


def _metadata(path: Path, *, label: str) -> tuple[dict | None, dict | None]:
    try:
        value = path.lstat()
    except OSError:
        return None, {"error": "host_log_inventory_unreadable", "entry_label": label}
    if path.is_symlink():
        return None, {"error": "host_log_inventory_symlink", "entry_label": label}
    if not stat.S_ISREG(value.st_mode):
        return None, {"error": "host_log_inventory_not_regular", "entry_label": label}
    if value.st_uid != os.getuid():
        return None, {"error": "host_log_inventory_wrong_owner", "entry_label": label}
    if stat.S_IMODE(value.st_mode) != 0o600:
        return None, {"error": "host_log_inventory_permissions_unsafe", "entry_label": label}
    if value.st_nlink != 1:
        return None, {"error": "host_log_inventory_hardlink", "entry_label": label}
    return {
        "label": label,
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "links": int(value.st_nlink),
        "size_bytes": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
    }, None


def _entry_fingerprint(path: Path) -> dict:
    value = path.lstat()
    return {
        "name_sha256": hashlib.sha256(path.name.encode("utf-8")).hexdigest(),
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": int(value.st_mode),
        "uid": int(value.st_uid),
        "links": int(value.st_nlink),
        "size_bytes": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
    }


def _inventory(log_dir: Path) -> tuple[dict, dict | None]:
    if log_dir.is_symlink():
        return {}, {"error": "host_log_directory_symlink"}
    try:
        directory = log_dir.lstat()
    except FileNotFoundError:
        return {
            "directory": None,
            "directory_entries": [],
            "active": None,
            "backups": [],
            "preserved": [],
        }, None
    except OSError:
        return {}, {"error": "host_log_directory_unreadable"}
    if not stat.S_ISDIR(directory.st_mode):
        return {}, {"error": "host_log_directory_not_directory"}
    if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
        return {}, {"error": "host_log_directory_permissions_unsafe"}
    try:
        entries = sorted(log_dir.iterdir(), key=lambda item: item.name)
    except OSError:
        return {}, {"error": "host_log_directory_unreadable"}
    if len(entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT:
        return {}, {
            "error": "host_log_directory_entry_limit_exceeded",
            "entry_count": len(entries),
            "entry_limit": HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT,
        }

    active = None
    backups: list[tuple[int, dict]] = []
    preserved: list[dict] = []
    directory_entries = []
    for entry in entries:
        try:
            directory_entries.append(_entry_fingerprint(entry))
        except OSError:
            return {}, {"error": "host_log_inventory_unreadable"}
        if entry.name == "host.log":
            active, error = _metadata(entry, label="host.log")
            if error:
                return {}, error
            continue
        if entry.name == "launchd.log":
            metadata, error = _metadata(entry, label="launchd.log")
            if error:
                return {}, error
            preserved.append(metadata)
            continue
        if not entry.name.startswith("host.log."):
            return {}, {"error": "host_log_inventory_unknown_entry"}
        match = _BACKUP_PATTERN.fullmatch(entry.name)
        if not match:
            return {}, {"error": "host_log_inventory_unknown_entry"}
        suffix = int(match.group(1))
        if suffix > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT:
            return {}, {"error": "host_log_inventory_suffix_too_large"}
        metadata, error = _metadata(entry, label=f"host.log.{suffix}")
        if error:
            return {}, error
        backups.append((suffix, metadata))

    backups.sort(key=lambda item: item[0])
    suffixes = [item[0] for item in backups]
    if suffixes and suffixes != list(range(1, suffixes[-1] + 1)):
        return {}, {"error": "host_log_inventory_gap"}
    return {
        "directory": {
            "device": int(directory.st_dev),
            "inode": int(directory.st_ino),
            "mode": stat.S_IMODE(directory.st_mode),
            "uid": int(directory.st_uid),
            "links": int(directory.st_nlink),
        },
        "directory_entries": directory_entries,
        "active": active,
        "backups": [metadata for _suffix, metadata in backups],
        "preserved": preserved,
    }, None


def build_rotation_plan(log_dir: Path, *, max_bytes: int, backups: int) -> tuple[dict, dict | None]:
    inventory, error = _inventory(log_dir)
    if error:
        return {}, error
    active = inventory["active"]
    rotation_required = bool(active and int(active["size_bytes"]) > max_bytes)
    discard = []
    if rotation_required:
        discard = [
            item["label"]
            for item in inventory["backups"]
            if int(item["label"].rsplit(".", 1)[1]) >= backups
        ]
    canonical = {
        "plan_schema_version": 1,
        "operation": "host_log_rotate",
        "log_root_sha256": hashlib.sha256(str(log_dir.absolute()).encode("utf-8")).hexdigest(),
        "max_bytes": max_bytes,
        "backups": backups,
        "inventory": inventory,
        "rotation_required": rotation_required,
        "discard": discard,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    labels = ([active["label"]] if active else []) + [item["label"] for item in inventory["backups"]]
    public = {
        "ok": True,
        "operation": "host_log_rotate",
        "dry_run": True,
        "directory_present": inventory["directory"] is not None,
        "host_log_present": active is not None,
        "active_size_bytes": int(active["size_bytes"]) if active else 0,
        "max_bytes": max_bytes,
        "minimum_max_bytes": HOST_LOG_ROTATE_MIN_MAX_BYTES,
        "backups": backups,
        "minimum_backups": HOST_LOG_ROTATE_MIN_BACKUPS,
        "maximum_backups": HOST_LOG_ROTATE_MAX_BACKUPS,
        "rotation_required": rotation_required,
        "inventory_count": len(labels),
        "inventory_labels": labels[:HOST_LOG_ROTATE_OUTPUT_LIMIT],
        "inventory_labels_truncated": len(labels) > HOST_LOG_ROTATE_OUTPUT_LIMIT,
        "discard_count": len(discard),
        "discard_labels": discard[:HOST_LOG_ROTATE_OUTPUT_LIMIT],
        "discard_labels_truncated": len(discard) > HOST_LOG_ROTATE_OUTPUT_LIMIT,
        "plan_hash": hashlib.sha256(encoded).hexdigest(),
        "content_omitted": True,
        "paths_omitted": True,
        "token_omitted": True,
    }
    return {"canonical": canonical, "public": public}, None


def _directory_metadata(value: os.stat_result) -> dict:
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "links": int(value.st_nlink),
    }


def _metadata_at(directory_fd: int, name: str, *, allowed_links: tuple[int, ...] = (1,)) -> dict:
    value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink not in allowed_links
    ):
        raise OSError("unsafe log entry")
    return {
        "label": name,
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "links": int(value.st_nlink),
        "size_bytes": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
    }


def _stable_entry_matches(actual: dict, expected: dict) -> bool:
    keys = ("device", "inode", "mode", "uid", "size_bytes", "mtime_ns")
    return all(actual.get(key) == expected.get(key) for key in keys)


def _snapshot_entry_matches(name: str, actual: dict, expected: dict) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    keys = ("device", "inode", "mode", "uid")
    if not all(actual.get(key) == expected.get(key) for key in keys):
        return False
    if name == "launchd.log":
        actual_size = actual.get("size_bytes")
        expected_size = expected.get("size_bytes")
        return bool(
            isinstance(actual_size, int)
            and isinstance(expected_size, int)
            and actual_size >= expected_size
        )
    return all(actual.get(key) == expected.get(key) for key in ("size_bytes", "mtime_ns"))


def _cleanup_entry_matches(name: str, actual: dict, expected: dict) -> bool:
    return bool(
        _snapshot_entry_matches(name, actual, expected)
        and actual.get("links") == expected.get("links")
    )


def _snapshot_from_inventory(inventory: dict) -> dict:
    entries = {}
    for item in (
        ([inventory["active"]] if inventory.get("active") else [])
        + list(inventory.get("backups") or [])
        + list(inventory.get("preserved") or [])
    ):
        entries[item["label"]] = dict(item)
    return {"directory": dict(inventory["directory"]), "entries": entries}


def _snapshot_directory_fd(directory_fd: int, *, allow_partial: bool = False) -> dict:
    directory = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.getuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise OSError("unsafe log directory")
    names = sorted(os.listdir(directory_fd))
    if len(names) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT:
        raise OSError("log directory entry limit exceeded")
    entries = {}
    for name in names:
        if name not in {"host.log", "launchd.log"} and _BACKUP_PATTERN.fullmatch(name) is None:
            raise OSError("unknown log entry")
        entries[name] = _metadata_at(directory_fd, name, allowed_links=(1, 2))
    if not allow_partial:
        suffixes = sorted(
            int(name.rsplit(".", 1)[1])
            for name in entries
            if _BACKUP_PATTERN.fullmatch(name)
        )
        if suffixes and suffixes != list(range(1, suffixes[-1] + 1)):
            raise OSError("log generation gap")
    return {"directory": _directory_metadata(directory), "entries": entries}


def _snapshot_matches(actual: dict, expected: dict) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    actual_directory = actual.get("directory") or {}
    expected_directory = expected.get("directory") or {}
    if not isinstance(actual_directory, dict) or not isinstance(expected_directory, dict):
        return False
    if any(actual_directory.get(key) != expected_directory.get(key) for key in ("device", "inode", "mode", "uid")):
        return False
    actual_entries = actual.get("entries") or {}
    expected_entries = expected.get("entries") or {}
    if (
        not isinstance(actual_entries, dict)
        or not isinstance(expected_entries, dict)
        or len(actual_entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT
        or len(expected_entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT
    ):
        return False
    return bool(
        set(actual_entries) == set(expected_entries)
        and all(_snapshot_entry_matches(name, actual_entries[name], expected_entries[name]) for name in actual_entries)
    )


def _snapshot_subset_matches(actual: dict, expected: dict) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    actual_directory = actual.get("directory") or {}
    expected_directory = expected.get("directory") or {}
    if not isinstance(actual_directory, dict) or not isinstance(expected_directory, dict):
        return False
    if any(actual_directory.get(key) != expected_directory.get(key) for key in ("device", "inode", "mode", "uid")):
        return False
    actual_entries = actual.get("entries") or {}
    expected_entries = expected.get("entries") or {}
    if (
        not isinstance(actual_entries, dict)
        or not isinstance(expected_entries, dict)
        or len(actual_entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT
        or len(expected_entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT
    ):
        return False
    return bool(
        set(actual_entries).issubset(expected_entries)
        and all(_snapshot_entry_matches(name, actual_entries[name], expected_entries[name]) for name in actual_entries)
    )


def _open_private_parent(path: Path) -> int:
    if path.is_symlink():
        raise OSError("unsafe log parent")
    value = path.lstat()
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o700
    ):
        raise OSError("unsafe log parent")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    opened = os.fstat(descriptor)
    if opened.st_dev != value.st_dev or opened.st_ino != value.st_ino:
        os.close(descriptor)
        raise OSError("log parent changed")
    return descriptor


def _open_directory_at(parent_fd: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    value = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise OSError("unsafe anchored log directory")
    return descriptor


def _exists_at(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _directory_name_matches_fd(parent_fd: int, name: str, directory_fd: int) -> bool:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(directory_fd)
    except OSError:
        return False
    return bool(
        stat.S_ISDIR(named.st_mode)
        and stat.S_ISDIR(opened.st_mode)
        and named.st_uid == os.getuid()
        and stat.S_IMODE(named.st_mode) == 0o700
        and (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)
    )


def _regular_identity(value: os.stat_result) -> dict:
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "links": int(value.st_nlink),
        "size_bytes": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
    }


def _bound_regular_name_matches(parent_fd: int, name: str, expected: dict) -> bool:
    if not isinstance(expected, dict):
        return False
    try:
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
    ):
        return False
    actual = _regular_identity(value)
    return all(
        actual.get(key) == expected.get(key)
        for key in ("device", "inode", "mode", "uid", "links", "size_bytes", "mtime_ns")
    )


def _regular_name_matches_fd(
    parent_fd: int,
    name: str,
    descriptor: int,
    *,
    allowed_links: tuple[int, ...] = (1,),
) -> bool:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError:
        return False
    if (
        not stat.S_ISREG(named.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or named.st_uid != os.getuid()
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(named.st_mode) != 0o600
        or stat.S_IMODE(opened.st_mode) != 0o600
        or named.st_nlink not in allowed_links
        or opened.st_nlink not in allowed_links
    ):
        return False
    return _regular_identity(named) == _regular_identity(opened)


def _unlink_bound_regular(parent_fd: int, name: str, expected: dict) -> None:
    descriptor = -1
    try:
        expected_links = expected.get("links") if isinstance(expected, dict) else None
        if expected_links not in {1, 2}:
            raise OSError("private metadata identity is invalid")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = _regular_identity(os.fstat(descriptor))
        if opened != expected or not _regular_name_matches_fd(
            parent_fd,
            name,
            descriptor,
            allowed_links=(expected_links,),
        ):
            raise OSError("private metadata name changed before cleanup")
        os.unlink(name, dir_fd=parent_fd)
        remaining = os.fstat(descriptor)
        if (
            (remaining.st_dev, remaining.st_ino) != (expected["device"], expected["inode"])
            or remaining.st_nlink != expected_links - 1
        ):
            raise OSError("private metadata cleanup result is unverifiable")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise OSError("short private metadata write")
        offset += written


def _write_journal(
    parent_fd: int,
    payload: dict,
    *,
    expected_previous: dict | None = None,
) -> dict:
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > HOST_LOG_ROTATE_JOURNAL_MAX_BYTES:
        raise OSError("log rotation journal too large")
    temporary = f"{_JOURNAL_NAME}.tmp-{secrets.token_hex(8)}"
    descriptor = -1
    previous_fd = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        temporary_identity = _regular_identity(os.fstat(descriptor))
        if not _regular_name_matches_fd(parent_fd, temporary, descriptor):
            raise OSError("log rotation journal temporary changed before publication")

        if expected_previous is None:
            _rename_noreplace(parent_fd, temporary, _JOURNAL_NAME)
            if _exists_at(parent_fd, temporary) or not _regular_name_matches_fd(
                parent_fd,
                _JOURNAL_NAME,
                descriptor,
            ):
                raise OSError("initial log rotation journal publication is unverifiable")
        else:
            previous_fd = os.open(
                _JOURNAL_NAME,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            previous_identity = _regular_identity(os.fstat(previous_fd))
            if previous_identity != expected_previous or not _regular_name_matches_fd(
                parent_fd,
                _JOURNAL_NAME,
                previous_fd,
            ):
                raise OSError("prior log rotation journal changed before update")
            _atomic_exchange_regular(
                parent_fd,
                temporary,
                _JOURNAL_NAME,
                left_fd=descriptor,
                right_fd=previous_fd,
            )
            _unlink_bound_regular(parent_fd, temporary, previous_identity)

        _fsync_fd(parent_fd)
        journal_identity = _regular_identity(os.fstat(descriptor))
        if journal_identity.get("links") != 1 or not _regular_name_matches_fd(
            parent_fd,
            _JOURNAL_NAME,
            descriptor,
        ):
            raise OSError("log rotation journal identity changed during publication")
        return journal_identity
    finally:
        if descriptor >= 0 and _regular_name_matches_fd(
            parent_fd,
            temporary,
            descriptor,
            allowed_links=(1, 2),
        ):
            try:
                _unlink_bound_regular(parent_fd, temporary, _regular_identity(os.fstat(descriptor)))
            except FileNotFoundError:
                pass
        if previous_fd >= 0:
            os.close(previous_fd)
        if descriptor >= 0:
            os.close(descriptor)


def _read_journal(parent_fd: int) -> dict | None:
    try:
        descriptor = os.open(
            _JOURNAL_NAME,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    try:
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_nlink != 1
            or value.st_size <= 0
            or value.st_size > HOST_LOG_ROTATE_JOURNAL_MAX_BYTES
        ):
            raise OSError("unsafe log rotation journal")
        identity = _regular_identity(value)
        data = bytearray()
        while len(data) <= HOST_LOG_ROTATE_JOURNAL_MAX_BYTES:
            chunk = os.read(descriptor, min(16 * 1024, HOST_LOG_ROTATE_JOURNAL_MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        payload = json.loads(bytes(data).decode("utf-8"))
        if _regular_identity(os.fstat(descriptor)) != identity or not _regular_name_matches_fd(
            parent_fd,
            _JOURNAL_NAME,
            descriptor,
        ):
            raise OSError("log rotation journal changed while reading")
    finally:
        os.close(descriptor)
    if (
        not isinstance(payload, dict)
        or payload.get("journal_schema_version") != 1
        or payload.get("operation") != "host_log_rotate"
        or payload.get("phase") not in {"building", "prepared"}
        or _STAGE_PATTERN.fullmatch(str(payload.get("stage_label") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("plan_hash") or "")) is None
        or not isinstance(payload.get("old_snapshot"), dict)
    ):
        raise OSError("invalid log rotation journal")
    if payload["phase"] == "prepared" and not isinstance(payload.get("new_snapshot"), dict):
        raise OSError("incomplete prepared log rotation journal")
    payload["_journal_identity"] = identity
    return payload


def _stage_labels(parent_fd: int) -> list[str]:
    labels = []
    for name in os.listdir(parent_fd):
        if not name.startswith(_STAGE_PREFIX):
            continue
        if _STAGE_PATTERN.fullmatch(name) is None:
            raise OSError("invalid log rotation stage")
        labels.append(name)
    return sorted(labels)


def _journal_temp_snapshot(parent_fd: int) -> dict[str, dict]:
    prefix = f"{_JOURNAL_NAME}.tmp-"
    entries = {}
    for name in os.listdir(parent_fd):
        if not name.startswith(prefix):
            continue
        if _JOURNAL_TEMP_PATTERN.fullmatch(name) is None:
            raise OSError("invalid log rotation journal temporary")
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_nlink != 1
            or value.st_size > HOST_LOG_ROTATE_JOURNAL_MAX_BYTES
        ):
            raise OSError("unsafe log rotation journal temporary")
        entries[name] = _regular_identity(value)
    if len(entries) > HOST_LOG_ROTATE_DIRECTORY_ENTRY_LIMIT:
        raise OSError("log rotation journal temporary limit exceeded")
    return {name: entries[name] for name in sorted(entries)}


def _journal_temp_labels(parent_fd: int) -> list[str]:
    return list(_journal_temp_snapshot(parent_fd))


def _cleanup_journal_temps(parent_fd: int, expected: dict[str, dict]) -> None:
    if _journal_temp_snapshot(parent_fd) != expected:
        raise OSError("log rotation journal temporary set changed")
    for label, identity in expected.items():
        _unlink_bound_regular(parent_fd, label, identity)
    if expected:
        _fsync_fd(parent_fd)


def _cleanup_stage(
    parent_fd: int,
    stage_label: str,
    *,
    stage_fd: int,
    expected_snapshot: dict | None,
) -> None:
    if not _directory_name_matches_fd(parent_fd, stage_label, stage_fd):
        raise OSError("log rotation stage name changed before cleanup")
    snapshot = _snapshot_directory_fd(stage_fd, allow_partial=True)
    if expected_snapshot is not None and not _snapshot_subset_matches(
        snapshot,
        expected_snapshot,
    ):
        raise OSError("log rotation cleanup snapshot changed")
    for name in sorted(snapshot["entries"]):
        actual = _metadata_at(stage_fd, name, allowed_links=(1, 2))
        if not _cleanup_entry_matches(name, actual, snapshot["entries"][name]):
            raise OSError("log rotation stage member changed before cleanup")
        os.unlink(name, dir_fd=stage_fd)
    _fsync_fd(stage_fd)
    if os.listdir(stage_fd):
        raise OSError("log rotation stage changed during cleanup")
    if not _directory_name_matches_fd(parent_fd, stage_label, stage_fd):
        raise OSError("log rotation stage name changed during cleanup")
    os.rmdir(stage_label, dir_fd=parent_fd)
    if _exists_at(parent_fd, stage_label):
        raise OSError("log rotation stage still exists after cleanup")
    _fsync_fd(parent_fd)


def _inspect_pending_rotation(log_dir: Path) -> tuple[dict | None, dict | None]:
    try:
        parent_fd = _open_private_parent(log_dir.parent)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, {"error": "host_log_parent_unverifiable"}
    try:
        try:
            journal = _read_journal(parent_fd)
            stages = _stage_labels(parent_fd)
            journal_temp_snapshot = _journal_temp_snapshot(parent_fd)
        except (OSError, UnicodeError, ValueError):
            return None, {"error": "host_log_recovery_metadata_unverifiable"}
        if journal is None:
            if stages:
                return None, {"error": "host_log_orphan_stage"}
            if journal_temp_snapshot:
                return {
                    "recovery_kind": "journal_temps",
                    "journal_temp_snapshot": journal_temp_snapshot,
                }, None
            return None, None
        if any(stage != journal["stage_label"] for stage in stages):
            return None, {"error": "host_log_recovery_stage_conflict"}
        journal["journal_temp_snapshot"] = journal_temp_snapshot
        return journal, None
    finally:
        os.close(parent_fd)


def start_gate(log_dir: Path) -> dict:
    """Block Host startup while log-rotation recovery is pending or unverifiable."""
    pending, pending_error = _inspect_pending_rotation(log_dir)
    if pending_error:
        return {
            "ok": False,
            "error": pending_error.get("error") or "host_log_recovery_metadata_unverifiable",
            "recovery_required": True,
            "content_omitted": True,
            "paths_omitted": True,
            "token_omitted": True,
        }
    if pending is not None:
        return {
            "ok": False,
            "error": "host_log_recovery_required",
            "recovery_required": True,
            "content_omitted": True,
            "paths_omitted": True,
            "token_omitted": True,
        }
    return {
        "ok": True,
        "recovery_required": False,
        "content_omitted": True,
        "paths_omitted": True,
        "token_omitted": True,
    }


def _recover_pending_rotation(log_dir: Path, journal: dict) -> tuple[dict, int]:
    parent_fd = -1
    current_fd = -1
    stage_fd = -1
    try:
        parent_fd = _open_private_parent(log_dir.parent)
        journal_temp_snapshot = journal.get("journal_temp_snapshot") or {}
        if not isinstance(journal_temp_snapshot, dict):
            raise OSError("invalid log rotation journal temporary snapshot")
        if journal.get("recovery_kind") != "journal_temps" and not _bound_regular_name_matches(
            parent_fd,
            _JOURNAL_NAME,
            journal.get("_journal_identity") or {},
        ):
            raise OSError("log rotation journal name changed before recovery")
        _cleanup_journal_temps(parent_fd, journal_temp_snapshot)
        if journal.get("recovery_kind") == "journal_temps":
            return {
                "ok": False,
                "operation": "host_log_rotate",
                "dry_run": False,
                "error": "host_log_recovery_completed_replan_required",
                "recovery_completed": True,
                "recovery_state": "journal_temporary_removed",
                "confirmation_applied": True,
                "content_omitted": True,
                "paths_omitted": True,
                "token_omitted": True,
            }, 2
        current_fd = _open_directory_at(parent_fd, log_dir.name)
        current = _snapshot_directory_fd(current_fd)
        old_matches = _snapshot_matches(current, journal["old_snapshot"])
        new_matches = bool(
            journal.get("phase") == "prepared"
            and _snapshot_matches(current, journal.get("new_snapshot") or {})
        )
        if not old_matches and not new_matches:
            return _error(
                "host_log_recovery_state_unverifiable",
                dry_run=False,
                confirmation_applied=True,
                recovery_completed=False,
            )[0], 1
        expected_current = journal.get("new_snapshot") if new_matches else journal["old_snapshot"]
        stage_expected = None
        if _exists_at(parent_fd, journal["stage_label"]):
            stage_fd = _open_directory_at(parent_fd, journal["stage_label"])
            stage_snapshot = _snapshot_directory_fd(stage_fd, allow_partial=True)
            if new_matches:
                stage_expected = journal["old_snapshot"]
            elif journal.get("phase") == "prepared":
                stage_expected = journal.get("new_snapshot")
            else:
                stage_directory = journal.get("stage_directory") or {}
                actual_directory = stage_snapshot.get("directory") or {}
                if not isinstance(stage_directory, dict) or any(
                    actual_directory.get(key) != stage_directory.get(key)
                    for key in ("device", "inode", "mode", "uid")
                ):
                    return _error(
                        "host_log_recovery_state_unverifiable",
                        dry_run=False,
                        confirmation_applied=True,
                        recovery_completed=False,
                    )[0], 1
                stage_expected = stage_snapshot
            if not _snapshot_subset_matches(stage_snapshot, stage_expected or {}):
                return _error(
                    "host_log_recovery_state_unverifiable",
                    dry_run=False,
                    confirmation_applied=True,
                    recovery_completed=False,
                )[0], 1
            _cleanup_stage(
                parent_fd,
                journal["stage_label"],
                stage_fd=stage_fd,
                expected_snapshot=stage_expected,
            )
        elif new_matches and any(item["links"] != 1 for item in current["entries"].values()):
            return _error(
                "host_log_recovery_state_unverifiable",
                dry_run=False,
                confirmation_applied=True,
                recovery_completed=False,
            )[0], 1

        if (
            not _directory_name_matches_fd(parent_fd, log_dir.name, current_fd)
            or _exists_at(parent_fd, journal["stage_label"])
        ):
            raise OSError("recovered log namespace changed before journal cleanup")
        current_after = _snapshot_directory_fd(current_fd)
        if (
            not _snapshot_matches(current_after, expected_current or {})
            or any(item["links"] != 1 for item in current_after["entries"].values())
        ):
            raise OSError("recovered logs could not be verified")
        _unlink_bound_regular(
            parent_fd,
            _JOURNAL_NAME,
            journal.get("_journal_identity") or {},
        )
        _fsync_fd(parent_fd)
        return {
            "ok": False,
            "operation": "host_log_rotate",
            "dry_run": False,
            "error": "host_log_recovery_completed_replan_required",
            "recovery_completed": True,
            "recovery_state": "committed" if new_matches else "rolled_back",
            "confirmation_applied": True,
            "content_omitted": True,
            "paths_omitted": True,
            "token_omitted": True,
        }, 2
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return _error(
            "host_log_recovery_failed",
            dry_run=False,
            confirmation_applied=True,
            recovery_completed=False,
            failure_detail_omitted=True,
        )[0], 1
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        if current_fd >= 0:
            os.close(current_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _link_expected(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
    expected: dict,
) -> None:
    source = _metadata_at(source_fd, source_name)
    if source != expected or _exists_at(destination_fd, destination_name):
        raise OSError("log source changed before staging")
    os.link(
        source_name,
        destination_name,
        src_dir_fd=source_fd,
        dst_dir_fd=destination_fd,
        follow_symlinks=False,
    )
    destination = _metadata_at(destination_fd, destination_name, allowed_links=(2,))
    if not _stable_entry_matches(destination, expected):
        raise OSError("staged log identity mismatch")


def _create_empty_active(directory_fd: int) -> dict:
    descriptor = os.open(
        "host.log",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    created = _metadata_at(directory_fd, "host.log")
    if created["size_bytes"] != 0:
        raise OSError("new active log is not empty")
    return created


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        flags = 4
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        flags = 1
    else:
        function = None
        flags = 0
    if function is None:
        raise OSError("no-clobber rename unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), flags) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _rename_exchange(parent_fd: int, left: str, right: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    left_bytes = os.fsencode(left)
    right_bytes = os.fsencode(right)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
    else:
        function = None
    if function is None:
        raise OSError("atomic directory exchange unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, left_bytes, parent_fd, right_bytes, 2) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _atomic_exchange_regular(
    parent_fd: int,
    left: str,
    right: str,
    *,
    left_fd: int,
    right_fd: int,
) -> None:
    if not _regular_name_matches_fd(parent_fd, left, left_fd):
        raise OSError("left private metadata name changed before atomic exchange")
    if not _regular_name_matches_fd(parent_fd, right, right_fd):
        raise OSError("right private metadata name changed before atomic exchange")
    _rename_exchange(parent_fd, left, right)
    if not _regular_name_matches_fd(parent_fd, left, right_fd):
        raise OSError("left private metadata exchange result is unverifiable")
    if not _regular_name_matches_fd(parent_fd, right, left_fd):
        raise OSError("right private metadata exchange result is unverifiable")


def _atomic_exchange(
    parent_fd: int,
    left: str,
    right: str,
    *,
    left_fd: int,
    right_fd: int,
) -> None:
    if not _directory_name_matches_fd(parent_fd, left, left_fd):
        raise OSError("left directory name changed before atomic exchange")
    if not _directory_name_matches_fd(parent_fd, right, right_fd):
        raise OSError("right directory name changed before atomic exchange")
    _rename_exchange(parent_fd, left, right)
    if not _directory_name_matches_fd(parent_fd, left, right_fd):
        raise OSError("left directory exchange result is unverifiable")
    if not _directory_name_matches_fd(parent_fd, right, left_fd):
        raise OSError("right directory exchange result is unverifiable")


def _perform_atomic_rotation(log_dir: Path, plan: dict) -> tuple[dict, int]:
    public = plan["public"]
    inventory = plan["canonical"]["inventory"]
    old_snapshot = _snapshot_from_inventory(inventory)
    metadata_by_label = dict(old_snapshot["entries"])
    backups = int(plan["canonical"]["backups"])
    stage_label = f"{_STAGE_PREFIX}{secrets.token_hex(16)}"
    parent_fd = -1
    logs_fd = -1
    stage_fd = -1
    stage_created = False
    exchanged = False
    journal_written = False
    journal_identity = None
    new_snapshot = None
    try:
        parent_fd = _open_private_parent(log_dir.parent)
        if _read_journal(parent_fd) is not None or _stage_labels(parent_fd):
            raise OSError("pending log rotation state")
        logs_fd = _open_directory_at(parent_fd, log_dir.name)
        current = _snapshot_directory_fd(logs_fd)
        if not _snapshot_matches(current, old_snapshot):
            raise OSError("log inventory changed before staging")
        if any(item["links"] != 1 for item in current["entries"].values()):
            raise OSError("log inventory has unexpected links")

        journal = {
            "journal_schema_version": 1,
            "operation": "host_log_rotate",
            "phase": "building",
            "stage_label": stage_label,
            "plan_hash": public["plan_hash"],
            "old_snapshot": old_snapshot,
        }
        journal_written = True
        journal_identity = _write_journal(parent_fd, journal)
        os.mkdir(stage_label, mode=0o700, dir_fd=parent_fd)
        stage_created = True
        stage_fd = _open_directory_at(parent_fd, stage_label)
        journal = {
            **journal,
            "stage_directory": _directory_metadata(os.fstat(stage_fd)),
        }
        journal_identity = _write_journal(
            parent_fd,
            journal,
            expected_previous=journal_identity,
        )
        _create_empty_active(stage_fd)
        _link_expected(logs_fd, "host.log", stage_fd, "host.log.1", metadata_by_label["host.log"])
        for item in inventory["backups"]:
            suffix = int(item["label"].rsplit(".", 1)[1])
            if suffix < backups:
                _link_expected(logs_fd, item["label"], stage_fd, f"host.log.{suffix + 1}", item)
        for item in inventory.get("preserved") or []:
            _link_expected(logs_fd, item["label"], stage_fd, item["label"], item)
        _fsync_fd(stage_fd)
        new_snapshot = _snapshot_directory_fd(stage_fd)
        journal = {
            **journal,
            "phase": "prepared",
            "new_snapshot": new_snapshot,
        }
        journal_identity = _write_journal(
            parent_fd,
            journal,
            expected_previous=journal_identity,
        )

        _atomic_exchange(
            parent_fd,
            log_dir.name,
            stage_label,
            left_fd=logs_fd,
            right_fd=stage_fd,
        )
        exchanged = True
        _fsync_fd(parent_fd)
        if not _directory_name_matches_fd(parent_fd, log_dir.name, stage_fd):
            raise OSError("published log directory name is unverifiable")
        if not _directory_name_matches_fd(parent_fd, stage_label, logs_fd):
            raise OSError("retired log directory name is unverifiable")
        if not _snapshot_matches(_snapshot_directory_fd(stage_fd), new_snapshot):
            raise OSError("atomic log exchange did not publish prepared state")

        _cleanup_stage(
            parent_fd,
            stage_label,
            stage_fd=logs_fd,
            expected_snapshot=old_snapshot,
        )
        stage_created = False
        if (
            not _directory_name_matches_fd(parent_fd, log_dir.name, stage_fd)
            or _exists_at(parent_fd, stage_label)
        ):
            raise OSError("published log namespace changed before journal cleanup")
        published = _snapshot_directory_fd(stage_fd)
        if not _snapshot_matches(published, new_snapshot) or any(
            item["links"] != 1 for item in published["entries"].values()
        ):
            raise OSError("published log state could not be verified")
        _unlink_bound_regular(parent_fd, _JOURNAL_NAME, journal_identity or {})
        journal_written = False
        _fsync_fd(parent_fd)
        return {
            **public,
            "dry_run": False,
            "rotated": True,
            "atomic_directory_exchange": True,
            "crash_recovery_journal": True,
            "confirmation_applied": True,
            "written_file_count": len(published["entries"]),
            "deleted_file_count": public["discard_count"],
        }, 0
    except (OSError, UnicodeError, ValueError):
        if exchanged:
            return {
                **public,
                "ok": False,
                "dry_run": False,
                "error": "host_log_rotation_cleanup_incomplete",
                "rotated": True,
                "atomic_directory_exchange": True,
                "recovery_required": True,
                "confirmation_applied": True,
                "failure_detail_omitted": True,
            }, 1
        rollback_ok = True
        if stage_created and parent_fd >= 0:
            if stage_fd < 0:
                rollback_ok = False
            else:
                try:
                    rollback_snapshot = _snapshot_directory_fd(stage_fd, allow_partial=True)
                    _cleanup_stage(
                        parent_fd,
                        stage_label,
                        stage_fd=stage_fd,
                        expected_snapshot=new_snapshot or rollback_snapshot,
                    )
                    stage_created = False
                except OSError:
                    rollback_ok = False
        if journal_written and parent_fd >= 0 and rollback_ok:
            try:
                current_after = _snapshot_directory_fd(logs_fd)
                if (
                    not _directory_name_matches_fd(parent_fd, log_dir.name, logs_fd)
                    or _exists_at(parent_fd, stage_label)
                    or not _snapshot_matches(current_after, old_snapshot)
                    or any(item["links"] != 1 for item in current_after["entries"].values())
                ):
                    raise OSError("rolled back log namespace could not be verified")
                _unlink_bound_regular(parent_fd, _JOURNAL_NAME, journal_identity or {})
                journal_written = False
                _fsync_fd(parent_fd)
            except OSError:
                rollback_ok = False
        return {
            **public,
            "ok": False,
            "dry_run": False,
            "error": "host_log_rotation_failed",
            "confirmation_applied": True,
            "state_rolled_back": rollback_ok,
            "replan_required": True,
            "recovery_required": not rollback_ok,
            "failure_detail_omitted": True,
        }, 1
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        if logs_fd >= 0:
            os.close(logs_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def rotate_logs(
    log_dir: Path,
    *,
    max_bytes: int = HOST_LOG_ROTATE_DEFAULT_MAX_BYTES,
    backups: int = HOST_LOG_ROTATE_DEFAULT_BACKUPS,
    confirm_rotate: bool = False,
    plan_hash: str = "",
) -> tuple[dict, int]:
    if max_bytes < HOST_LOG_ROTATE_MIN_MAX_BYTES:
        return _error(
            "host_log_max_bytes_below_minimum",
            max_bytes=max_bytes,
            minimum_max_bytes=HOST_LOG_ROTATE_MIN_MAX_BYTES,
        )
    if backups < HOST_LOG_ROTATE_MIN_BACKUPS or backups > HOST_LOG_ROTATE_MAX_BACKUPS:
        return _error(
            "host_log_backups_out_of_range",
            backups=backups,
            minimum_backups=HOST_LOG_ROTATE_MIN_BACKUPS,
            maximum_backups=HOST_LOG_ROTATE_MAX_BACKUPS,
        )

    pending, pending_error = _inspect_pending_rotation(log_dir)
    if pending_error:
        return _error(pending_error.pop("error"), **pending_error)
    if pending is not None:
        if not confirm_rotate:
            return _error("host_log_recovery_required", recovery_required=True)
        return _recover_pending_rotation(log_dir, pending)

    plan, error = build_rotation_plan(log_dir, max_bytes=max_bytes, backups=backups)
    if error:
        return _error(error.pop("error"), **error)
    public = plan["public"]
    if plan_hash and not confirm_rotate:
        return {**public, "ok": False, "error": "host_log_confirmation_required", "confirmation_applied": False}, 2
    if not confirm_rotate:
        return public, 0
    if not plan_hash:
        return {**public, "ok": False, "error": "host_log_plan_hash_required", "confirmation_applied": False}, 2
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan_hash)):
        return {**public, "ok": False, "error": "host_log_plan_hash_invalid", "confirmation_applied": False}, 2
    if not hmac.compare_digest(plan_hash, public["plan_hash"]):
        return {
            **public,
            "ok": False,
            "error": "host_log_plan_mismatch",
            "stale_plan": True,
            "confirmation_applied": False,
        }, 2

    refreshed, refreshed_error = build_rotation_plan(log_dir, max_bytes=max_bytes, backups=backups)
    if refreshed_error or not hmac.compare_digest(plan_hash, refreshed.get("public", {}).get("plan_hash", "")):
        return _error(
            "host_log_inventory_changed",
            stale_plan=True,
            confirmation_applied=False,
        )[0], 2
    public = refreshed["public"]
    if not public["rotation_required"]:
        return {
            **public,
            "dry_run": False,
            "rotated": False,
            "confirmation_applied": True,
            "written_file_count": 0,
            "deleted_file_count": 0,
        }, 0
    return _perform_atomic_rotation(log_dir, refreshed)
