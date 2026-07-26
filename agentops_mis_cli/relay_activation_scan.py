"""Read-only, FD-anchored prerequisite scanner for Relay activation plans."""
from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from agentops_mis_cli.relay_activation import (
    CONFIG_PATH,
    ENABLEMENT_LINK_PATH,
    RUNTIME_DIRECTORY,
    STATE_DIRECTORY,
    SYSTEMCTL_PATHS,
    UNIT_PATH,
    ActivationPrerequisiteSnapshot,
    DirectoryIdentity,
    FileIdentity,
    LinkIdentity,
    RootIdentity,
)
from agentops_mis_cli.relay_admin import (
    MAX_STATUS_DIRECTORY_ENTRIES,
    MAX_STATUS_RELEASE_BYTES,
    MAX_STATUS_RELEASE_DIRECTORIES,
    MAX_STATUS_RELEASE_FILES,
    MAX_WHEEL_MEMBER_SIZE,
    RelayStatusInvalid,
    _status_directory_flags,
    _status_file_fingerprint,
    _status_file_flags,
    _status_safe_native_name,
    _status_scan_activation_locked_anchored,
    _status_scan_anchored,
)
from agentops_mis_cli.relay_activation_journal import (
    _LockedActivationScanCapability,
)
from agentops_mis_cli.relay_daemon import (
    MAX_CONFIG_BYTES,
    MAX_KEY_FILE_BYTES,
    RelayDaemonError,
    parse_config_bytes,
)


SERVICE_ACCOUNT_NAME = "agentops-relay"
MAX_CERTIFICATE_BYTES = 1024 * 1024
MAX_PRIVATE_KEY_BYTES = 1024 * 1024
MAX_HELD_DESCRIPTORS = 512
SCAN_ERROR_ID = "activation_prerequisite_scan_invalid"
_CONFIG_BASE = PurePosixPath("/etc/agentops-mis-relay")
_ROUTE_KEY_BASE = _CONFIG_BASE / "routes"


class RelayActivationScanError(Exception):
    """Bounded scanner failure that never includes host or config content."""

    def __init__(self, error_id: str = SCAN_ERROR_ID):
        self.error_id = error_id
        super().__init__(error_id)


class _ScanInvalid(Exception):
    pass


@dataclass(frozen=True)
class _AccountIdentity:
    uid: int
    gid: int
    group_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ObservedFile:
    data: bytes
    metadata: os.stat_result


@dataclass(frozen=True)
class _ObservedLink:
    metadata: os.stat_result
    target: str


AccountResolver = Callable[[str], tuple[int, int, tuple[int, ...]]]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
    )


def _path_parts(path: str) -> tuple[str, ...]:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or len(path) > 4096
        or "\x00" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise _ScanInvalid
    try:
        path.encode("ascii")
    except UnicodeEncodeError:
        raise _ScanInvalid from None
    if path == "/":
        return ()
    parsed = PurePosixPath(path)
    if (
        parsed.as_posix() != path
        or any(part in {"", ".", "..", "~"} for part in path.split("/")[1:])
    ):
        raise _ScanInvalid
    parts = parsed.parts[1:]
    for part in parts:
        try:
            _status_safe_native_name(part)
        except RelayStatusInvalid:
            raise _ScanInvalid from None
    return tuple(parts)


def _bounded_uint(value: object) -> int:
    if type(value) is not int or value < 0 or value > (2**63) - 1:
        raise _ScanInvalid
    return value


def _account_from_tuple(value: object) -> _AccountIdentity:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or not isinstance(value[2], tuple)
    ):
        raise _ScanInvalid
    uid = _bounded_uint(value[0])
    gid = _bounded_uint(value[1])
    groups = tuple(_bounded_uint(group) for group in value[2])
    if (
        uid == 0
        or gid == 0
        or not groups
        or groups != tuple(sorted(set(groups)))
        or gid not in groups
        or len(groups) > 256
    ):
        raise _ScanInvalid
    return _AccountIdentity(uid=uid, gid=gid, group_ids=groups)


def _resolve_production_account(name: str) -> tuple[int, int, tuple[int, ...]]:
    if name != SERVICE_ACCOUNT_NAME:
        raise _ScanInvalid
    try:
        user = pwd.getpwnam(name)
        primary = grp.getgrgid(user.pw_gid)
        memberships = {
            record.gr_gid
            for record in grp.getgrall()
            if name in record.gr_mem
        }
    except (KeyError, OSError):
        raise _ScanInvalid from None
    if user.pw_name != name or primary.gr_name != name:
        raise _ScanInvalid
    memberships.add(user.pw_gid)
    return user.pw_uid, user.pw_gid, tuple(sorted(memberships))


class _AnchoredInventory:
    """Holds every traversed descriptor until final namespace revalidation."""

    def __init__(self, root_descriptor: int):
        self.root_descriptor = root_descriptor
        self._directories: dict[tuple[str, ...], tuple[int, tuple[int, ...]]] = {
            (): (root_descriptor, _fingerprint(os.fstat(root_descriptor)))
        }
        self._files: dict[
            tuple[str, ...], tuple[int, tuple[int, ...], _ObservedFile]
        ] = {}
        self._metadata_files: dict[
            tuple[str, ...], tuple[int, tuple[int, ...], os.stat_result]
        ] = {}
        self._absent: set[tuple[str, ...]] = set()
        self._links: dict[
            tuple[str, ...], tuple[tuple[int, ...], _ObservedLink]
        ] = {}
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for parts, (descriptor, _fingerprint_value, _observed) in sorted(
            self._files.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            del parts, _fingerprint_value, _observed
            try:
                os.close(descriptor)
            except OSError:
                pass
        for parts, (descriptor, _fingerprint_value, _observed) in sorted(
            self._metadata_files.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            del parts, _fingerprint_value, _observed
            try:
                os.close(descriptor)
            except OSError:
                pass
        for parts, (descriptor, _observed) in sorted(
            self._directories.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if not parts:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass

    def directory(self, path: str) -> os.stat_result:
        parts = _path_parts(path)
        descriptor = self._open_directory(parts)
        return os.fstat(descriptor)

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        if parts in self._directories:
            return self._directories[parts][0]
        if self._descriptor_count() >= MAX_HELD_DESCRIPTORS:
            raise _ScanInvalid
        parent_parts = parts[:-1]
        parent = self._open_directory(parent_parts)
        name = parts[-1]
        descriptor = -1
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise _ScanInvalid
            descriptor = os.open(
                name,
                _status_directory_flags(),
                dir_fd=parent,
            )
            opened = os.fstat(descriptor)
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except (FileNotFoundError, OSError):
            if descriptor >= 0:
                os.close(descriptor)
            raise _ScanInvalid from None
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
        ):
            os.close(descriptor)
            raise _ScanInvalid
        self._directories[parts] = (descriptor, _fingerprint(opened))
        return descriptor

    def list_directory(self, path: str) -> tuple[str, ...]:
        descriptor = self._open_directory(_path_parts(path))
        before = _fingerprint(os.fstat(descriptor))
        try:
            names = os.listdir(descriptor)
        except OSError:
            raise _ScanInvalid from None
        if len(names) > MAX_STATUS_DIRECTORY_ENTRIES:
            raise _ScanInvalid
        for name in names:
            try:
                _status_safe_native_name(name)
            except RelayStatusInvalid:
                raise _ScanInvalid from None
        if _fingerprint(os.fstat(descriptor)) != before:
            raise _ScanInvalid
        return tuple(sorted(names))

    def read_file(self, path: str, *, maximum: int) -> _ObservedFile:
        parts = _path_parts(path)
        if not parts or maximum < 0:
            raise _ScanInvalid
        cached = self._files.get(parts)
        if cached is not None:
            observed = cached[2]
            if len(observed.data) > maximum:
                raise _ScanInvalid
            return observed
        if self._descriptor_count() >= MAX_HELD_DESCRIPTORS:
            raise _ScanInvalid
        parent = self._open_directory(parts[:-1])
        name = parts[-1]
        descriptor = -1
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise _ScanInvalid
            descriptor = os.open(name, _status_file_flags(), dir_fd=parent)
            opened = os.fstat(descriptor)
            if _status_file_fingerprint(before) != _status_file_fingerprint(opened):
                raise _ScanInvalid
            if opened.st_size < 0 or opened.st_size > maximum:
                raise _ScanInvalid
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise _ScanInvalid
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise _ScanInvalid
            after = os.fstat(descriptor)
            if _fingerprint(opened) != _fingerprint(after):
                raise _ScanInvalid
            path_after = os.stat(
                name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            if _fingerprint(opened) != _fingerprint(path_after):
                raise _ScanInvalid
            observed = _ObservedFile(data=b"".join(chunks), metadata=opened)
            self._files[parts] = (descriptor, _fingerprint(opened), observed)
            descriptor = -1
            return observed
        except (FileNotFoundError, OSError):
            raise _ScanInvalid from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def observe_optional_regular(self, path: str) -> os.stat_result | None:
        parts = _path_parts(path)
        if not parts:
            raise _ScanInvalid
        if parts in self._metadata_files:
            return self._metadata_files[parts][2]
        if parts in self._absent:
            return None
        if self._descriptor_count() >= MAX_HELD_DESCRIPTORS:
            raise _ScanInvalid
        parent = self._open_directory(parts[:-1])
        name = parts[-1]
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            self._absent.add(parts)
            return None
        except OSError:
            raise _ScanInvalid from None
        if not stat.S_ISREG(before.st_mode):
            raise _ScanInvalid
        descriptor = -1
        try:
            descriptor = os.open(name, _status_file_flags(), dir_fd=parent)
            opened = os.fstat(descriptor)
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise _ScanInvalid from None
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
        ):
            os.close(descriptor)
            raise _ScanInvalid
        self._metadata_files[parts] = (
            descriptor,
            _fingerprint(opened),
            opened,
        )
        return opened

    def lstat_optional(
        self,
        path: str,
        *,
        record_absence: bool = False,
    ) -> os.stat_result | None:
        parts = _path_parts(path)
        if not parts:
            return os.fstat(self.root_descriptor)
        parent = self._open_directory(parts[:-1])
        try:
            return os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if record_absence:
                self._absent.add(parts)
            return None
        except OSError:
            raise _ScanInvalid from None

    def read_link(self, path: str) -> _ObservedLink:
        parts = _path_parts(path)
        if not parts:
            raise _ScanInvalid
        parent = self._open_directory(parts[:-1])
        name = parts[-1]
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISLNK(before.st_mode):
                raise _ScanInvalid
            target = os.readlink(name, dir_fd=parent)
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except (FileNotFoundError, OSError):
            raise _ScanInvalid from None
        if (
            _fingerprint(before) != _fingerprint(after)
            or not isinstance(target, str)
            or not target
            or len(target) > 4096
            or "\x00" in target
            or any(ord(character) < 32 or ord(character) == 127 for character in target)
        ):
            raise _ScanInvalid
        try:
            target.encode("ascii")
        except UnicodeEncodeError:
            raise _ScanInvalid from None
        observed = _ObservedLink(metadata=before, target=target)
        self._links[parts] = (_fingerprint(before), observed)
        return observed

    def verify(self) -> None:
        for _parts, (descriptor, observed, _value) in self._files.items():
            if _fingerprint(os.fstat(descriptor)) != observed:
                raise _ScanInvalid
        for _parts, (descriptor, observed, _value) in self._metadata_files.items():
            if _fingerprint(os.fstat(descriptor)) != observed:
                raise _ScanInvalid
        for _parts, (descriptor, observed) in self._directories.items():
            if _fingerprint(os.fstat(descriptor)) != observed:
                raise _ScanInvalid
        for parts, (observed, _value) in self._links.items():
            parent = self._reopen_directory(parts[:-1])
            try:
                current = os.stat(
                    parts[-1],
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except OSError:
                raise _ScanInvalid from None
            finally:
                os.close(parent)
            if _fingerprint(current) != observed:
                raise _ScanInvalid
        self._verify_namespace()

    def _descriptor_count(self) -> int:
        return (
            len(self._directories)
            + len(self._files)
            + len(self._metadata_files)
        )

    def _reopen_directory(self, parts: tuple[str, ...]) -> int:
        descriptor = os.dup(self.root_descriptor)
        try:
            for part in parts:
                child = os.open(
                    part,
                    _status_directory_flags(),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            return descriptor
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise _ScanInvalid from None

    def _verify_namespace(self) -> None:
        for parts, (_descriptor, observed) in sorted(
            self._directories.items(),
            key=lambda item: (len(item[0]), item[0]),
        ):
            reopened = self._reopen_directory(parts)
            try:
                current = os.fstat(reopened)
            finally:
                os.close(reopened)
            if _fingerprint(current) != observed:
                raise _ScanInvalid
        for parts, (_descriptor, observed, _value) in self._files.items():
            parent = self._reopen_directory(parts[:-1])
            try:
                current = os.stat(
                    parts[-1],
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except OSError:
                raise _ScanInvalid from None
            finally:
                os.close(parent)
            if _fingerprint(current) != observed:
                raise _ScanInvalid
        for parts, (_descriptor, observed, _value) in self._metadata_files.items():
            parent = self._reopen_directory(parts[:-1])
            try:
                current = os.stat(
                    parts[-1],
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except OSError:
                raise _ScanInvalid from None
            finally:
                os.close(parent)
            if _fingerprint(current) != observed:
                raise _ScanInvalid
        for parts in self._absent:
            parent = self._reopen_directory(parts[:-1])
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError:
                raise _ScanInvalid from None
            else:
                raise _ScanInvalid
            finally:
                os.close(parent)


def _directory_payload(
    path: str,
    metadata: os.stat_result,
    *,
    owner_id: int | None = None,
    group_id: int | None = None,
) -> dict[str, object]:
    return {
        "canonical_path": path,
        "device_id": metadata.st_dev,
        "group_id": metadata.st_gid if group_id is None else group_id,
        "inode": metadata.st_ino,
        "kind": "directory",
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
        "owner_id": metadata.st_uid if owner_id is None else owner_id,
    }


def _directory_identity(
    path: str,
    metadata: os.stat_result,
    *,
    owner_id: int | None = None,
    group_id: int | None = None,
) -> DirectoryIdentity:
    payload = _directory_payload(
        path,
        metadata,
        owner_id=owner_id,
        group_id=group_id,
    )
    return DirectoryIdentity(**payload)


def _file_identity(
    path: str,
    observed: _ObservedFile,
    *,
    owner_id: int | None = None,
    group_id: int | None = None,
) -> FileIdentity:
    metadata = observed.metadata
    return FileIdentity(
        kind="regular",
        canonical_path=path,
        device_id=metadata.st_dev,
        inode=metadata.st_ino,
        owner_id=metadata.st_uid if owner_id is None else owner_id,
        group_id=metadata.st_gid if group_id is None else group_id,
        mode=stat.S_IMODE(metadata.st_mode),
        nlink=metadata.st_nlink,
        size=metadata.st_size,
        content_sha256=_sha256(observed.data),
    )


def _validate_file_metadata(
    observed: _ObservedFile,
    *,
    uid: int,
    gid: int,
    mode: int | None = None,
    executable: bool = False,
) -> None:
    metadata = observed.metadata
    actual_mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or metadata.st_nlink != 1
        or actual_mode & 0o022
        or (mode is not None and actual_mode != mode)
        or (executable and not actual_mode & 0o111)
    ):
        raise _ScanInvalid


def _validate_unit_account(data: bytes) -> None:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise _ScanInvalid from None
    if (
        tuple(line for line in lines if line.startswith("User="))
        != (f"User={SERVICE_ACCOUNT_NAME}",)
        or tuple(line for line in lines if line.startswith("Group="))
        != (f"Group={SERVICE_ACCOUNT_NAME}",)
    ):
        raise _ScanInvalid


def _validate_directory_metadata(
    metadata: os.stat_result,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_nlink < 2
    ):
        raise _ScanInvalid


def _is_direct_child(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return (
        path.parent == parent
        and path.name not in {"", ".", ".."}
        and path.name[0].isascii()
        and path.name[0].isalnum()
        and all(
            character.isascii()
            and (character.isalnum() or character in {"-", "_", "."})
            for character in path.name
        )
    )


def _is_descendant(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts)


def _trusted_service_parent_group(
    metadata_gid: int,
    *,
    expected_gid: int,
    service_group_ids: tuple[int, ...],
) -> bool:
    return metadata_gid == expected_gid or metadata_gid in service_group_ids


def _release_tree_digest(
    inventory: _AnchoredInventory,
    release_id: str,
    *,
    expected_uid: int,
    expected_gid: int,
) -> str:
    root_path = f"/opt/agentops-mis-relay/releases/{release_id}"
    records: list[dict[str, object]] = []
    total_bytes = 0
    directory_count = 0
    file_count = 0

    def scan(path: str) -> None:
        nonlocal directory_count, file_count, total_bytes
        metadata = inventory.directory(path)
        _validate_directory_metadata(
            metadata,
            uid=expected_uid,
            gid=expected_gid,
            mode=0o755,
        )
        directory_count += 1
        if directory_count > MAX_STATUS_RELEASE_DIRECTORIES:
            raise _ScanInvalid
        records.append(_directory_payload(path, metadata))
        for name in inventory.list_directory(path):
            child_path = f"{path}/{name}"
            observed = inventory.lstat_optional(child_path)
            if observed is None:
                raise _ScanInvalid
            if stat.S_ISDIR(observed.st_mode):
                scan(child_path)
                continue
            if not stat.S_ISREG(observed.st_mode):
                raise _ScanInvalid
            file_count += 1
            if file_count > MAX_STATUS_RELEASE_FILES:
                raise _ScanInvalid
            file_observed = inventory.read_file(
                child_path,
                maximum=MAX_WHEEL_MEMBER_SIZE,
            )
            expected_mode = (
                0o755
                if child_path == f"{root_path}/bin/agentops-relay"
                else 0o644
            )
            _validate_file_metadata(
                file_observed,
                uid=expected_uid,
                gid=expected_gid,
                mode=expected_mode,
            )
            total_bytes += len(file_observed.data)
            if total_bytes > MAX_STATUS_RELEASE_BYTES:
                raise _ScanInvalid
            identity = _file_identity(child_path, file_observed)
            records.append(
                {
                    "canonical_path": identity.canonical_path,
                    "content_sha256": identity.content_sha256,
                    "device_id": identity.device_id,
                    "group_id": identity.group_id,
                    "inode": identity.inode,
                    "kind": identity.kind,
                    "mode": identity.mode,
                    "nlink": identity.nlink,
                    "owner_id": identity.owner_id,
                    "size": identity.size,
                }
            )

    scan(root_path)
    records.sort(key=lambda value: str(value["canonical_path"]))
    return _sha256(_canonical_json(records))


def _trusted_parent_hash(
    inventory: _AnchoredInventory,
    paths: tuple[str, ...],
    *,
    expected_uid: int,
    expected_gid: int,
    logical_root_uid: int,
    logical_root_gid: int,
    service_uid: int,
    service_gid: int,
    service_group_ids: tuple[int, ...],
    service_paths: tuple[str, ...],
) -> str:
    parents: set[str] = {"/"}
    service_parents: set[str] = {"/"}
    for value in paths:
        parts = _path_parts(value)
        for index in range(1, len(parts)):
            parents.add("/" + "/".join(parts[:index]))
    for value in service_paths:
        parts = _path_parts(value)
        for index in range(1, len(parts)):
            service_parents.add("/" + "/".join(parts[:index]))
    records: list[dict[str, object]] = []
    for path in sorted(parents):
        metadata = inventory.directory(path)
        if path in {STATE_DIRECTORY, RUNTIME_DIRECTORY}:
            _validate_directory_metadata(
                metadata,
                uid=service_uid,
                gid=service_gid,
                mode=0o700,
            )
        elif (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or (
                path not in service_parents
                and metadata.st_gid != expected_gid
            )
            or (
                path in service_parents
                and not _trusted_service_parent_group(
                    metadata.st_gid,
                    expected_gid=expected_gid,
                    service_group_ids=service_group_ids,
                )
            )
        ):
            raise _ScanInvalid
        if path in service_parents:
            mode = stat.S_IMODE(metadata.st_mode)
            if path in {STATE_DIRECTORY, RUNTIME_DIRECTORY}:
                semantic_owner = service_uid
                semantic_group = service_gid
            else:
                semantic_owner = (
                    logical_root_uid
                    if metadata.st_uid == expected_uid
                    else metadata.st_uid
                )
                semantic_group = (
                    logical_root_gid
                    if metadata.st_gid == expected_gid
                    else metadata.st_gid
                )
            if semantic_owner == service_uid:
                traversable = bool(mode & 0o100)
            elif semantic_group in service_group_ids:
                traversable = bool(mode & 0o010)
            else:
                traversable = bool(mode & 0o001)
            if not traversable:
                raise _ScanInvalid
        records.append(_directory_payload(path, metadata))
    records.sort(key=lambda value: str(value["canonical_path"]))
    return _sha256(_canonical_json(records))


def _validate_mutable_leaf(
    metadata: os.stat_result | None,
    *,
    service_uid: int,
    service_gid: int,
) -> None:
    if metadata is None:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != service_uid
        or metadata.st_gid != service_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise _ScanInvalid


def _select_systemctl(
    inventory: _AnchoredInventory,
    *,
    root_uid: int,
    root_gid: int,
    logical_root_uid: int,
    logical_root_gid: int,
) -> FileIdentity:
    for path in ("/usr/bin/systemctl", "/bin/systemctl"):
        if path not in SYSTEMCTL_PATHS:
            continue
        try:
            observed = inventory.read_file(path, maximum=16 * 1024 * 1024)
            _validate_file_metadata(
                observed,
                uid=root_uid,
                gid=root_gid,
                executable=True,
            )
            if not observed.data:
                raise _ScanInvalid
        except _ScanInvalid:
            continue
        return _file_identity(
            path,
            observed,
            owner_id=logical_root_uid,
            group_id=logical_root_gid,
        )
    raise _ScanInvalid


def _scan_anchored(
    root_descriptor: int,
    *,
    root_metadata: os.stat_result,
    account: _AccountIdentity,
    logical_root_uid: int,
    logical_root_gid: int,
    activation_locked: bool,
) -> ActivationPrerequisiteSnapshot:
    status_reader = (
        _status_scan_activation_locked_anchored
        if activation_locked
        else _status_scan_anchored
    )
    status_payload, status_code = status_reader(root_descriptor)
    if (
        status_code != 0
        or status_payload.get("state_id") != "installed_valid"
        or status_payload.get("installed") is not True
    ):
        raise _ScanInvalid
    release_id = status_payload.get("release_id")
    version_id = status_payload.get("version_id")
    if not isinstance(release_id, str) or not isinstance(version_id, str):
        raise _ScanInvalid

    inventory = _AnchoredInventory(root_descriptor)
    try:
        release_tree_sha256 = _release_tree_digest(
            inventory,
            release_id,
            expected_uid=root_metadata.st_uid,
            expected_gid=root_metadata.st_gid,
        )
        unit_observed = inventory.read_file(UNIT_PATH, maximum=1024 * 1024)
        _validate_file_metadata(
            unit_observed,
            uid=root_metadata.st_uid,
            gid=root_metadata.st_gid,
            mode=0o644,
        )
        _validate_unit_account(unit_observed.data)
        config_observed = inventory.read_file(
            CONFIG_PATH,
            maximum=MAX_CONFIG_BYTES,
        )
        _validate_file_metadata(
            config_observed,
            uid=root_metadata.st_uid,
            gid=account.gid,
            mode=0o640,
        )
        try:
            config = parse_config_bytes(config_observed.data)
        except RelayDaemonError:
            raise _ScanInvalid from None

        certificate_path = PurePosixPath(config.connector_cert_file.as_posix())
        private_key_path = PurePosixPath(config.connector_key_file.as_posix())
        state_path = PurePosixPath(config.state_path.as_posix())
        status_path = PurePosixPath(config.status_path.as_posix())
        route_paths = tuple(
            PurePosixPath(route.key_file.as_posix()) for route in config.routes
        )
        for path in (
            certificate_path,
            private_key_path,
            state_path,
            status_path,
            *route_paths,
        ):
            _path_parts(path.as_posix())
        if (
            not _is_descendant(certificate_path, _CONFIG_BASE)
            or not _is_descendant(private_key_path, _CONFIG_BASE)
            or not _is_direct_child(state_path, PurePosixPath(STATE_DIRECTORY))
            or not _is_direct_child(status_path, PurePosixPath(RUNTIME_DIRECTORY))
            or not route_paths
            or any(
                not _is_direct_child(path, _ROUTE_KEY_BASE)
                for path in route_paths
            )
        ):
            raise _ScanInvalid
        sensitive_paths = (
            PurePosixPath(CONFIG_PATH),
            certificate_path,
            private_key_path,
            *route_paths,
        )
        if len(set(sensitive_paths)) != len(sensitive_paths):
            raise _ScanInvalid

        certificate_observed = inventory.read_file(
            certificate_path.as_posix(),
            maximum=MAX_CERTIFICATE_BYTES,
        )
        _validate_file_metadata(
            certificate_observed,
            uid=root_metadata.st_uid,
            gid=account.gid,
            mode=0o640,
        )
        if not certificate_observed.data:
            raise _ScanInvalid
        private_key_observed = inventory.read_file(
            private_key_path.as_posix(),
            maximum=MAX_PRIVATE_KEY_BYTES,
        )
        _validate_file_metadata(
            private_key_observed,
            uid=account.uid,
            gid=account.gid,
            mode=0o600,
        )
        if not private_key_observed.data:
            raise _ScanInvalid

        route_observations: list[tuple[str, _ObservedFile]] = []
        route_key_values: set[bytes] = set()
        for path in sorted(route_paths):
            observed = inventory.read_file(
                path.as_posix(),
                maximum=MAX_KEY_FILE_BYTES,
            )
            _validate_file_metadata(
                observed,
                uid=account.uid,
                gid=account.gid,
                mode=0o600,
            )
            try:
                encoded = observed.data.decode("ascii").strip()
                key = bytes.fromhex(encoded)
            except (UnicodeDecodeError, ValueError):
                raise _ScanInvalid from None
            if len(key) != 32 or key in route_key_values:
                raise _ScanInvalid
            route_key_values.add(key)
            route_observations.append((path.as_posix(), observed))

        state_metadata = inventory.directory(STATE_DIRECTORY)
        runtime_metadata = inventory.directory(RUNTIME_DIRECTORY)
        _validate_directory_metadata(
            state_metadata,
            uid=account.uid,
            gid=account.gid,
            mode=0o700,
        )
        _validate_directory_metadata(
            runtime_metadata,
            uid=account.uid,
            gid=account.gid,
            mode=0o700,
        )
        if (
            state_metadata.st_dev,
            state_metadata.st_ino,
        ) == (
            runtime_metadata.st_dev,
            runtime_metadata.st_ino,
        ):
            raise _ScanInvalid
        _validate_mutable_leaf(
            inventory.observe_optional_regular(state_path.as_posix()),
            service_uid=account.uid,
            service_gid=account.gid,
        )
        _validate_mutable_leaf(
            inventory.observe_optional_regular(status_path.as_posix()),
            service_uid=account.uid,
            service_gid=account.gid,
        )
        systemctl = _select_systemctl(
            inventory,
            root_uid=root_metadata.st_uid,
            root_gid=root_metadata.st_gid,
            logical_root_uid=logical_root_uid,
            logical_root_gid=logical_root_gid,
        )

        enablement_links: tuple[LinkIdentity, ...]
        enablement_metadata = inventory.lstat_optional(
            ENABLEMENT_LINK_PATH,
            record_absence=True,
        )
        if enablement_metadata is None:
            enablement_links = ()
        else:
            observed_link = inventory.read_link(ENABLEMENT_LINK_PATH)
            if (
                observed_link.target != UNIT_PATH
                or observed_link.metadata.st_uid != root_metadata.st_uid
                or observed_link.metadata.st_gid != root_metadata.st_gid
                or observed_link.metadata.st_nlink != 1
            ):
                raise _ScanInvalid
            enablement_links = (
                LinkIdentity(
                    kind="symlink",
                    canonical_path=ENABLEMENT_LINK_PATH,
                    target=observed_link.target,
                    device_id=observed_link.metadata.st_dev,
                    inode=observed_link.metadata.st_ino,
                    owner_id=logical_root_uid,
                    group_id=logical_root_gid,
                    nlink=observed_link.metadata.st_nlink,
                ),
            )

        trusted_paths = (
            UNIT_PATH,
            CONFIG_PATH,
            certificate_path.as_posix(),
            private_key_path.as_posix(),
            state_path.as_posix(),
            status_path.as_posix(),
            systemctl.canonical_path,
            ENABLEMENT_LINK_PATH,
            *(path.as_posix() for path in route_paths),
        )
        trusted_parent_chain_sha256 = _trusted_parent_hash(
            inventory,
            trusted_paths,
            expected_uid=root_metadata.st_uid,
            expected_gid=root_metadata.st_gid,
            logical_root_uid=logical_root_uid,
            logical_root_gid=logical_root_gid,
            service_uid=account.uid,
            service_gid=account.gid,
            service_group_ids=account.group_ids,
            service_paths=(
                CONFIG_PATH,
                certificate_path.as_posix(),
                private_key_path.as_posix(),
                state_path.as_posix(),
                status_path.as_posix(),
                *(path.as_posix() for path in route_paths),
            ),
        )

        identities = (
            unit_observed,
            config_observed,
            certificate_observed,
            private_key_observed,
            *(value for _path, value in route_observations),
        )
        inode_keys = (
            (systemctl.device_id, systemctl.inode),
            *(
            (value.metadata.st_dev, value.metadata.st_ino) for value in identities
            ),
        )
        if len(set(inode_keys)) != len(inode_keys):
            raise _ScanInvalid

        status_after, status_after_code = status_reader(root_descriptor)
        if (
            status_after_code != status_code
            or status_after != status_payload
        ):
            raise _ScanInvalid
        inventory.verify()
        return ActivationPrerequisiteSnapshot(
            root=RootIdentity(
                kind="directory",
                canonical_path="/",
                device_id=root_metadata.st_dev,
                inode=root_metadata.st_ino,
                owner_id=logical_root_uid,
                group_id=logical_root_gid,
                mode=stat.S_IMODE(root_metadata.st_mode),
            ),
            release_id=release_id,
            version_id=version_id,
            release_tree_sha256=release_tree_sha256,
            unit=_file_identity(
                UNIT_PATH,
                unit_observed,
                owner_id=logical_root_uid,
                group_id=logical_root_gid,
            ),
            config=_file_identity(
                CONFIG_PATH,
                config_observed,
                owner_id=logical_root_uid,
                group_id=account.gid,
            ),
            certificate=_file_identity(
                certificate_path.as_posix(),
                certificate_observed,
                owner_id=logical_root_uid,
                group_id=account.gid,
            ),
            private_key=_file_identity(
                private_key_path.as_posix(),
                private_key_observed,
            ),
            route_keys=tuple(
                _file_identity(path, observed)
                for path, observed in route_observations
            ),
            state_directory=_directory_identity(
                STATE_DIRECTORY,
                state_metadata,
            ),
            runtime_directory=_directory_identity(
                RUNTIME_DIRECTORY,
                runtime_metadata,
            ),
            trusted_parent_chain_sha256=trusted_parent_chain_sha256,
            service_uid=account.uid,
            service_gid=account.gid,
            service_group_ids=account.group_ids,
            systemctl=systemctl,
            enablement_links=enablement_links,
        )
    finally:
        inventory.close()


def _scan_root(
    root: Path,
    *,
    account_resolver: AccountResolver,
    fixture: bool,
    activation_capability: _LockedActivationScanCapability | None = None,
) -> ActivationPrerequisiteSnapshot:
    root_descriptor = -1
    snapshot: ActivationPrerequisiteSnapshot | None = None
    try:
        if (
            activation_capability is not None
            and type(activation_capability)
            is not _LockedActivationScanCapability
        ):
            raise _ScanInvalid
        journal_snapshot_before = (
            activation_capability._snapshot_sha256_for_root(root)
            if activation_capability is not None
            else None
        )
        if not fixture and os.geteuid() != 0:
            raise _ScanInvalid
        if not root.is_absolute():
            raise _ScanInvalid
        before = os.stat(root, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise _ScanInvalid
        root_descriptor = os.open(root, _status_directory_flags())
        opened = os.fstat(root_descriptor)
        path_opened = os.stat(root, follow_symlinks=False)
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(path_opened)
        ):
            raise _ScanInvalid
        if (
            stat.S_IMODE(opened.st_mode) & 0o022
            or (not fixture and (opened.st_uid != 0 or opened.st_gid != 0))
        ):
            raise _ScanInvalid
        account = _account_from_tuple(account_resolver(SERVICE_ACCOUNT_NAME))
        candidate = _scan_anchored(
            root_descriptor,
            root_metadata=opened,
            account=account,
            logical_root_uid=0,
            logical_root_gid=0,
            activation_locked=activation_capability is not None,
        )
        if _account_from_tuple(
            account_resolver(SERVICE_ACCOUNT_NAME)
        ) != account:
            raise _ScanInvalid
        if not fixture and os.geteuid() != 0:
            raise _ScanInvalid
        held_after = os.fstat(root_descriptor)
        path_after = os.stat(root, follow_symlinks=False)
        if (
            _fingerprint(held_after) != _fingerprint(opened)
            or _fingerprint(path_after) != _fingerprint(opened)
            or (
                activation_capability is not None
                and activation_capability._snapshot_sha256_for_root(root)
                != journal_snapshot_before
            )
        ):
            raise _ScanInvalid
        snapshot = candidate
    except Exception:
        snapshot = None
    finally:
        if root_descriptor >= 0:
            try:
                os.close(root_descriptor)
            except OSError:
                pass
    if snapshot is None:
        raise RelayActivationScanError()
    return snapshot


def scan_activation_prerequisites() -> ActivationPrerequisiteSnapshot:
    """Scan the real host root without exposing a root or resolver override."""

    return _scan_root(
        Path("/"),
        account_resolver=_resolve_production_account,
        fixture=False,
    )


def _scan_activation_prerequisites_while_locked(
    capability: _LockedActivationScanCapability,
) -> ActivationPrerequisiteSnapshot:
    """Scan the real host while one exact journal lifecycle lock remains live."""

    return _scan_root(
        Path("/"),
        account_resolver=_resolve_production_account,
        fixture=False,
        activation_capability=capability,
    )


def _scan_fixture_activation_prerequisites(
    root: Path,
    *,
    account_resolver: AccountResolver,
) -> ActivationPrerequisiteSnapshot:
    """Test-only root relocation; intentionally private and absent from any CLI."""

    return _scan_root(
        root,
        account_resolver=account_resolver,
        fixture=True,
    )


def _scan_fixture_activation_prerequisites_while_locked(
    root: Path,
    *,
    account_resolver: AccountResolver,
    capability: _LockedActivationScanCapability,
) -> ActivationPrerequisiteSnapshot:
    """Test-only locked scan for an isolated absolute fixture root."""

    return _scan_root(
        root,
        account_resolver=account_resolver,
        fixture=True,
        activation_capability=capability,
    )
