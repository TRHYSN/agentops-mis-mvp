"""Private process-lifetime lock shared by the Host stack and maintenance CLI."""
from __future__ import annotations

import errno
import fcntl
import os
import stat
from pathlib import Path


class RuntimeLockError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def private_runtime_parent(metadata: os.stat_result) -> bool:
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )


def private_runtime_lock(metadata: os.stat_result) -> bool:
    return bool(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    )


def acquire_runtime_lock(lock_path: Path) -> int:
    """Acquire one private lock without following links or leaking its path."""
    lock_path = lock_path.expanduser()
    if lock_path.name in {"", ".", ".."}:
        raise RuntimeLockError("invalid_path")
    parent_fd = -1
    lock_fd = -1
    acquired = False
    try:
        parent_before = lock_path.parent.lstat()
        if lock_path.parent.is_symlink() or not private_runtime_parent(parent_before):
            raise RuntimeLockError("unsafe_parent")
        parent_fd = os.open(
            lock_path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        parent_open = os.fstat(parent_fd)
        if (
            not private_runtime_parent(parent_open)
            or (parent_open.st_dev, parent_open.st_ino) != (parent_before.st_dev, parent_before.st_ino)
        ):
            raise RuntimeLockError("parent_changed")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            lock_fd = os.open(
                lock_path.name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            os.fchmod(lock_fd, 0o600)
        except FileExistsError:
            lock_fd = os.open(lock_path.name, flags, dir_fd=parent_fd)
        lock_metadata = os.fstat(lock_fd)
        if not private_runtime_lock(lock_metadata):
            raise RuntimeLockError("unsafe_lock")
        lock_entry = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (lock_entry.st_dev, lock_entry.st_ino) != (lock_metadata.st_dev, lock_metadata.st_ino):
            raise RuntimeLockError("lock_changed")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise RuntimeLockError("held") from None
            raise RuntimeLockError("unavailable") from None
        parent_after = lock_path.parent.lstat()
        lock_after = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not private_runtime_parent(parent_after)
            or (parent_after.st_dev, parent_after.st_ino) != (parent_open.st_dev, parent_open.st_ino)
            or not private_runtime_lock(lock_after)
            or (lock_after.st_dev, lock_after.st_ino) != (lock_metadata.st_dev, lock_metadata.st_ino)
        ):
            raise RuntimeLockError("state_changed")
        acquired = True
        return lock_fd
    except RuntimeLockError:
        raise
    except OSError:
        raise RuntimeLockError("unavailable") from None
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)
        if lock_fd >= 0 and not acquired:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


def release_runtime_lock(lock_fd: int | None) -> None:
    if lock_fd is None:
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(lock_fd)
