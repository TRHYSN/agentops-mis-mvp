"""Crash-safe epoch allocation for an outbound Relay connector."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


SCHEMA_VERSION = 1
MAX_EPOCH = (1 << 63) - 1


class RelayEpochStoreError(RuntimeError):
    """Fail-closed persistent epoch state error."""


class PersistentRelayEpochStore:
    """Allocate a strictly increasing epoch under an exclusive file lock."""

    def __init__(self, path: Path, *, connector_identity: bytes) -> None:
        if not isinstance(path, Path):
            raise TypeError("epoch path must be a Path")
        if not isinstance(connector_identity, (bytes, bytearray)) or not connector_identity:
            raise ValueError("connector identity is required")
        self._path = Path(os.path.abspath(path.expanduser()))
        self._lock_path = self._path.with_name(f".{self._path.name}.lock")
        self._connector_ref = "rce_" + hashlib.sha256(bytes(connector_identity)).hexdigest()

    @staticmethod
    def _open_regular_private(path: Path, flags: int) -> int:
        descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise RelayEpochStoreError("epoch state is not a regular file")
        if metadata.st_uid != os.getuid():
            os.close(descriptor)
            raise RelayEpochStoreError("epoch state owner mismatch")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            os.close(descriptor)
            raise RelayEpochStoreError("epoch state permissions must be 0600")
        return descriptor

    def _ensure_private_parent(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = self._path.parent.lstat()
        except OSError as exc:
            raise RelayEpochStoreError("epoch state directory unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or self._path.parent.is_symlink():
            raise RelayEpochStoreError("epoch state directory is not trusted")
        if metadata.st_uid != os.getuid():
            raise RelayEpochStoreError("epoch state directory owner mismatch")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise RelayEpochStoreError("epoch state directory permissions must be 0700")

    def _read_locked(self) -> int:
        if not self._path.exists():
            return 0
        try:
            descriptor = self._open_regular_private(self._path, os.O_RDONLY)
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError) as exc:
            raise RelayEpochStoreError("invalid epoch state") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "connector_ref",
            "last_epoch",
            "schema_version",
        }:
            raise RelayEpochStoreError("invalid epoch state schema")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise RelayEpochStoreError("unsupported epoch state schema")
        if payload.get("connector_ref") != self._connector_ref:
            raise RelayEpochStoreError("epoch state connector mismatch")
        last_epoch = payload.get("last_epoch")
        if (
            not isinstance(last_epoch, int)
            or isinstance(last_epoch, bool)
            or not (1 <= last_epoch <= MAX_EPOCH)
        ):
            raise RelayEpochStoreError("invalid persisted epoch")
        return last_epoch

    def _write_locked(self, epoch: int) -> None:
        payload = {
            "connector_ref": self._connector_ref,
            "last_epoch": epoch,
            "schema_version": SCHEMA_VERSION,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = -1
            with handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if self._path.is_symlink():
                raise RelayEpochStoreError("epoch state symlink rejected")
            os.replace(temporary, self._path)
            self._path.chmod(0o600)
            directory_descriptor = os.open(
                self._path.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def next_epoch(self) -> int:
        """Persist and return the next epoch before network use."""
        self._ensure_private_parent()
        lock_descriptor = self._open_regular_private(
            self._lock_path,
            os.O_RDWR | os.O_CREAT,
        )
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            last_epoch = self._read_locked()
            if last_epoch >= MAX_EPOCH:
                raise RelayEpochStoreError("epoch space exhausted")
            next_epoch = last_epoch + 1
            self._write_locked(next_epoch)
            return next_epoch
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
