#!/usr/bin/env python3
"""Exercise Relayctl offline install transactions in disposable roots only."""
from __future__ import annotations

import base64
import csv
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from types import ModuleType


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "agentops_mis_cli" / "relay_admin.py"
BUNDLE_SCHEMA = "agentops.relay.release-bundle.v1"
CANARY = b"RELAY_EPOCH_CANARY_DO_NOT_TOUCH"
ENV_CANARY = "RELAY_ENV_CREDENTIAL_CANARY_DO_NOT_PRINT"
BODY_CANARIES = (
    b"relay.invalid",
    b"[Unit]",
    b"agentops_mis_cli.relay_daemon",
)
SECRET_PATTERNS = (
    re.compile(rb"ntn_[A-Za-z0-9_-]{16,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{16,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_-]{16,}"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def load_admin() -> ModuleType:
    name = "_agentops_relay_admin_install_smoke"
    spec = importlib.util.spec_from_file_location(name, ADMIN)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Relay admin fixture")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_guard(path: Path) -> None:
    path.mkdir()
    (path / "sitecustomize.py").write_text(
        """
import os
import socket
import subprocess
import urllib.request

def _blocked(*args, **kwargs):
    raise RuntimeError("relay install smoke blocked external behavior")

socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
urllib.request.urlopen = _blocked
os.system = _blocked
subprocess.Popen = _blocked
""".lstrip(),
        encoding="utf-8",
    )


def isolated_env(temporary: Path, guard: Path) -> dict[str, str]:
    home = temporary / "home"
    temp_dir = temporary / "tmp"
    cache = temporary / "cache"
    for path in (home, temp_dir, cache):
        path.mkdir()
    return {
        "AGENTOPS_TEST_CREDENTIAL": ENV_CANARY,
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(guard),
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(cache),
    }


def build_real_bundle(temporary: Path) -> tuple[Path, str]:
    source = temporary / "clean-source"
    commit = git_output("rev-parse", "HEAD").strip()
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--no-hardlinks",
            "--quiet",
            "--no-checkout",
            str(ROOT),
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if clone.returncode != 0:
        raise RuntimeError("unable to create clean local source fixture")
    checkout = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", commit],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if checkout.returncode != 0:
        raise RuntimeError("unable to select clean fixture commit")
    output = temporary / "bundle"
    output.mkdir()
    builder_guard = temporary / "builder-guard"
    builder_guard.mkdir()
    (builder_guard / "sitecustomize.py").write_text(
        """
import os
import socket
import subprocess
import urllib.request

def _blocked(*args, **kwargs):
    raise RuntimeError("bundle fixture blocked external behavior")

socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
urllib.request.urlopen = _blocked
os.system = _blocked

_real_popen = subprocess.Popen
class _GitOnlyPopen(_real_popen):
    def __init__(self, args, *positional, **keyword):
        command = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
        if os.path.basename(str(command)) != "git":
            raise RuntimeError("bundle fixture subprocess is not allowlisted")
        super().__init__(args, *positional, **keyword)

subprocess.Popen = _GitOnlyPopen
""".lstrip(),
        encoding="utf-8",
    )
    environment = {
        "HOME": str(temporary / "home"),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(builder_guard),
        "TMPDIR": str(temporary / "tmp"),
    }
    built = subprocess.run(
        [
            sys.executable,
            str(source / "scripts" / "build_relay_release.py"),
            "--output-dir",
            str(output),
        ],
        cwd=source,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if built.returncode != 0:
        raise RuntimeError("clean bundle fixture build failed")
    result = json.loads(built.stdout)
    bundle = (output / result["bundle"]).resolve()
    if not bundle.is_file():
        raise RuntimeError("clean bundle fixture is missing")
    return bundle, result["bundle_sha256"]


def run_admin(
    env: dict[str, str],
    root: Path,
    operation: str,
    bundle: Path,
    bundle_sha256: str,
    *extra: str,
) -> tuple[int, dict[str, object], bytes]:
    command = [
        sys.executable,
        str(ADMIN),
        "--root",
        str(root.absolute()),
        operation,
        "--bundle",
        str(bundle.resolve()),
        "--expect-sha256",
        bundle_sha256,
        *extra,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        payload = json.loads(stream.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return result.returncode, payload, combined


def archive_parts(bundle: Path) -> tuple[str, dict[str, bytes]]:
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise RuntimeError("fixture root invalid")
        root = roots.pop()
        files = {
            PurePosixPath(member.name).relative_to(root).as_posix(): archive.extractfile(
                member
            ).read()
            for member in members
            if member.isfile()
        }
    return root, files


def add_tar_file(
    archive: tarfile.TarFile,
    name: str,
    data: bytes,
    *,
    type_: bytes = tarfile.REGTYPE,
    size: int | None = None,
) -> None:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o644
    info.type = type_
    info.size = len(data) if size is None else size
    archive.addfile(info, io.BytesIO(data) if type_ == tarfile.REGTYPE and data else None)


def add_tar_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755
    info.type = tarfile.DIRTYPE
    archive.addfile(info)


def make_archive(
    target: Path,
    root: str,
    files: dict[str, bytes],
    *,
    duplicate: str | None = None,
    special: tuple[str, bytes] | None = None,
    oversized: bool = False,
) -> tuple[Path, str]:
    raw = io.BytesIO()
    if oversized:
        for name in (root, *(f"{root}/{value}" for value in ("config", "systemd", "wheel"))):
            info = tarfile.TarInfo(name)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o755
            info.type = tarfile.DIRTYPE
            raw.write(info.tobuf(format=tarfile.USTAR_FORMAT))
        info = tarfile.TarInfo(f"{root}/oversized.bin")
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.mode = 0o644
        info.type = tarfile.REGTYPE
        info.size = 193 * 1024 * 1024
        raw.write(info.tobuf(format=tarfile.USTAR_FORMAT))
        raw.write(b"\0" * 1024)
    else:
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            add_tar_directory(archive, root)
            for directory in ("config", "systemd", "wheel"):
                add_tar_directory(archive, f"{root}/{directory}")
            for path, data in sorted(files.items()):
                add_tar_file(archive, f"{root}/{path}", data)
                if duplicate == path:
                    add_tar_file(archive, f"{root}/{path}", data)
            if special is not None:
                name, type_ = special
                add_tar_file(archive, name, b"", type_=type_)
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        fileobj=compressed,
        mode="wb",
        compresslevel=9,
        mtime=0,
    ) as output:
        output.write(raw.getvalue())
    target.write_bytes(compressed.getvalue())
    target.chmod(0o600)
    return target.resolve(), sha256(compressed.getvalue())


def rebuild_contract(
    target: Path,
    root: str,
    files: dict[str, bytes],
    *,
    version: str | None = None,
    commit: str | None = None,
    wheel_data: bytes | None = None,
    wheel_path_override: str | None = None,
) -> tuple[Path, str]:
    manifest = json.loads(files["manifest.json"])
    if version is not None:
        manifest["version"] = version
        root = f"agentops-mis-relay-{version}"
    if commit is not None:
        manifest["git_commit"] = commit
    original_wheel_path = next(
        record["path"]
        for record in manifest["files"]
        if record["path"].endswith(".whl")
    )
    wheel_path = wheel_path_override or (
        f"wheel/agentops_mis_cli-{version}-py3-none-any.whl"
        if version is not None
        else original_wheel_path
    )
    for record in manifest["files"]:
        if record["path"] == original_wheel_path:
            record["path"] = wheel_path
    payload = {
        path: value
        for path, value in files.items()
        if path not in {"manifest.json", "SHA256SUMS"}
    }
    if wheel_path != original_wheel_path:
        payload[wheel_path] = payload.pop(original_wheel_path)
    if wheel_data is not None:
        payload[wheel_path] = wheel_data
    elif version is not None:
        payload[wheel_path] = reversion_wheel(payload[wheel_path], version)
    records = []
    for record in manifest["files"]:
        data = payload[record["path"]]
        records.append(
            {
                "path": record["path"],
                "sha256": sha256(data),
                "size": len(data),
            }
        )
    manifest["files"] = sorted(records, key=lambda record: record["path"])
    manifest_data = canonical_json(manifest)
    checksum_inputs = {**payload, "manifest.json": manifest_data}
    checksums = "".join(
        f"{sha256(data)}  {path}\n"
        for path, data in sorted(checksum_inputs.items())
    ).encode("ascii")
    return make_archive(
        target,
        root,
        {**payload, "manifest.json": manifest_data, "SHA256SUMS": checksums},
    )


def malicious_wheel(original: bytes, *, path: str, symlink: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original), "r") as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for info, data in members:
            target.writestr(info, data)
        info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (
            (stat.S_IFLNK | 0o777) if symlink else (stat.S_IFREG | 0o644)
        ) << 16
        target.writestr(info, b"outside")
    return output.getvalue()


def rewrite_wheel(
    original: bytes,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: dict[str, bytes] | None = None,
    removals: set[str] | None = None,
    refresh_record: bool,
) -> bytes:
    with zipfile.ZipFile(io.BytesIO(original), "r") as source:
        members = {
            info.filename: source.read(info)
            for info in source.infolist()
            if not info.is_dir()
        }
    members.update(replacements or {})
    members.update(additions or {})
    for name in removals or set():
        members.pop(name, None)
    return serialize_wheel(members, refresh_record=refresh_record)


def serialize_wheel(
    members: dict[str, bytes],
    *,
    refresh_record: bool,
) -> bytes:
    record_names = [name for name in members if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        raise RuntimeError("wheel fixture RECORD missing")
    record_name = record_names[0]
    if refresh_record:
        record_output = io.StringIO()
        writer = csv.writer(record_output, lineterminator="\n")
        for name, data in sorted(members.items()):
            if name == record_name:
                continue
            digest = (
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            writer.writerow([name, digest, str(len(data))])
        writer.writerow([record_name, "", ""])
        members[record_name] = record_output.getvalue().encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            target.writestr(info, data)
    return output.getvalue()


def reversion_wheel(original: bytes, version: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(original), "r") as source:
        original_members = {
            info.filename: source.read(info)
            for info in source.infolist()
            if not info.is_dir()
        }
    dist_info_roots = {
        PurePosixPath(name).parts[0]
        for name in original_members
        if PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if len(dist_info_roots) != 1:
        raise RuntimeError("wheel fixture dist-info missing")
    old_root = next(iter(dist_info_roots))
    new_root = f"agentops_mis_cli-{version}.dist-info"
    members = {
        (
            new_root + name[len(old_root) :]
            if name == old_root or name.startswith(old_root + "/")
            else name
        ): data
        for name, data in original_members.items()
    }
    metadata_name = f"{new_root}/METADATA"
    metadata = members[metadata_name]
    members[metadata_name] = re.sub(
        rb"(?m)^Version: [^\r\n]+$",
        f"Version: {version}".encode("ascii"),
        metadata,
        count=1,
    )
    return serialize_wheel(members, refresh_record=True)


def snapshot(path: Path) -> tuple[int, bytes, int, int, int, int]:
    metadata = path.stat()
    return (
        metadata.st_ino,
        path.read_bytes(),
        metadata.st_mtime_ns,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
    )


def tree_digest(root: Path) -> str:
    records: list[bytes] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            value = os.readlink(path).encode("utf-8")
            kind = b"L"
        elif stat.S_ISDIR(metadata.st_mode):
            value = b""
            kind = b"D"
        elif stat.S_ISREG(metadata.st_mode):
            value = path.read_bytes()
            kind = b"F"
        else:
            value = b""
            kind = b"X"
        identity = (
            f"{stat.S_IMODE(metadata.st_mode)}:{metadata.st_uid}:{metadata.st_gid}"
        ).encode("ascii")
        records.append(kind + b"\0" + relative + b"\0" + identity + b"\0" + value)
    return sha256(b"\n".join(records))


def descriptor_count() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def lifecycle_lock_cases(
    admin: ModuleType,
    base: Path,
    failures: list[str],
) -> dict[str, object]:
    base.mkdir(mode=0o700)

    def lock_fixture(name: str) -> tuple[Path, int, int]:
        parent = base / name
        parent.mkdir(mode=0o700)
        metadata = parent.stat()
        return parent / "lifecycle.lock", metadata.st_uid, metadata.st_gid

    def close_lock(result: tuple[object, ...]) -> None:
        admin_descriptor = int(result[0])
        lock_descriptor = int(result[2])
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        finally:
            try:
                os.close(lock_descriptor)
            finally:
                os.close(admin_descriptor)

    safe_path, safe_uid, safe_gid = lock_fixture("safe")
    (
        admin_descriptor,
        admin_fingerprint,
        descriptor,
        fingerprint,
    ) = admin._open_install_lifecycle_lock(
        safe_path,
        expected_uid=safe_uid,
        expected_gid=safe_gid,
    )
    safe_metadata = safe_path.lstat()
    safe_created = (
        stat.S_ISREG(safe_metadata.st_mode)
        and stat.S_IMODE(safe_metadata.st_mode) == 0o600
        and safe_metadata.st_nlink == 1
        and safe_metadata.st_size == 0
        and admin._install_lock_fingerprint(safe_metadata) == fingerprint
    )
    try:
        try:
            unexpected = admin._open_install_lifecycle_lock(
                safe_path,
                expected_uid=safe_uid,
                expected_gid=safe_gid,
            )
            close_lock(unexpected)
            contention_rejected = False
        except admin.RelayAdminError as exc:
            contention_rejected = exc.error_id == "lifecycle_lock_failed"
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        os.close(admin_descriptor)
    (
        reopened_admin,
        reopened_admin_fingerprint,
        reopened,
        reopened_fingerprint,
    ) = admin._open_install_lifecycle_lock(
        safe_path,
        expected_uid=safe_uid,
        expected_gid=safe_gid,
    )
    try:
        safe_reopened = (
            reopened_admin_fingerprint == admin_fingerprint
            and reopened_fingerprint == fingerprint
        )
    finally:
        fcntl.flock(reopened, fcntl.LOCK_UN)
        os.close(reopened)
        os.close(reopened_admin)

    admin_acquisition_path, admin_race_uid, admin_race_gid = lock_fixture(
        "admin-acquisition-race"
    )
    admin_acquisition_parent = admin_acquisition_path.parent
    admin_acquisition_retired = admin_acquisition_parent.with_name(
        "admin-acquisition-race-retired"
    )
    acquisition_before_descriptors = descriptor_count()
    original_open = admin.os.open
    acquisition_injected = False

    def replace_admin_before_open(path, *args, **kwargs):
        nonlocal acquisition_injected
        if (
            not acquisition_injected
            and Path(path) == admin_acquisition_parent
        ):
            acquisition_injected = True
            admin_acquisition_parent.rename(admin_acquisition_retired)
            admin_acquisition_parent.mkdir(mode=0o700)
        return original_open(path, *args, **kwargs)

    admin.os.open = replace_admin_before_open
    try:
        try:
            unexpected = admin._open_install_lifecycle_lock(
                admin_acquisition_path,
                expected_uid=admin_race_uid,
                expected_gid=admin_race_gid,
            )
            close_lock(unexpected)
            admin_acquisition_race_rejected = False
        except admin.RelayAdminError as exc:
            admin_acquisition_race_rejected = (
                exc.error_id == "lifecycle_lock_failed"
            )
    finally:
        admin.os.open = original_open
    acquisition_after_descriptors = descriptor_count()
    admin_acquisition_race_rejected = (
        admin_acquisition_race_rejected
        and acquisition_injected
        and not admin_acquisition_path.exists()
        and not (admin_acquisition_retired / "lifecycle.lock").exists()
        and (
            acquisition_before_descriptors is None
            or acquisition_after_descriptors is None
            or acquisition_before_descriptors
            == acquisition_after_descriptors
        )
    )

    post_open_path, post_open_uid, post_open_gid = lock_fixture(
        "admin-post-open-race"
    )
    post_open_parent = post_open_path.parent
    post_open_retired = post_open_parent.with_name(
        "admin-post-open-race-retired"
    )
    post_open_before_descriptors = descriptor_count()
    original_open = admin.os.open
    post_open_injected = False

    def replace_admin_before_lock(path, *args, **kwargs):
        nonlocal post_open_injected
        if not post_open_injected and path == "lifecycle.lock":
            post_open_injected = True
            post_open_parent.rename(post_open_retired)
            post_open_parent.mkdir(mode=0o700)
        return original_open(path, *args, **kwargs)

    admin.os.open = replace_admin_before_lock
    try:
        try:
            unexpected = admin._open_install_lifecycle_lock(
                post_open_path,
                expected_uid=post_open_uid,
                expected_gid=post_open_gid,
            )
            close_lock(unexpected)
            admin_post_open_race_rejected = False
        except admin.RelayAdminError as exc:
            admin_post_open_race_rejected = (
                exc.error_id == "lifecycle_lock_failed"
            )
    finally:
        admin.os.open = original_open
    post_open_after_descriptors = descriptor_count()
    admin_post_open_race_rejected = (
        admin_post_open_race_rejected
        and post_open_injected
        and not post_open_path.exists()
        and not (post_open_retired / "lifecycle.lock").exists()
        and (
            post_open_before_descriptors is None
            or post_open_after_descriptors is None
            or post_open_before_descriptors
            == post_open_after_descriptors
        )
    )

    admin_binding_path, binding_uid, binding_gid = lock_fixture(
        "admin-binding-race"
    )
    (
        binding_admin_descriptor,
        binding_admin_fingerprint,
        binding_lock_descriptor,
        binding_lock_fingerprint,
    ) = admin._open_install_lifecycle_lock(
        admin_binding_path,
        expected_uid=binding_uid,
        expected_gid=binding_gid,
    )
    binding_parent = admin_binding_path.parent
    binding_retired = binding_parent.with_name("admin-binding-race-retired")
    binding_parent.rename(binding_retired)
    binding_parent.mkdir(mode=0o700)
    replacement_lock = binding_parent / "lifecycle.lock"
    replacement_lock.write_bytes(b"")
    replacement_lock.chmod(0o600)
    try:
        try:
            admin._validate_install_lock_binding(
                binding_parent,
                binding_admin_descriptor,
                binding_admin_fingerprint,
                binding_lock_descriptor,
                binding_lock_fingerprint,
            )
            admin_binding_race_rejected = False
        except admin.RelayAdminError as exc:
            admin_binding_race_rejected = (
                exc.error_id == "recovery_required"
            )
    finally:
        fcntl.flock(binding_lock_descriptor, fcntl.LOCK_UN)
        os.close(binding_lock_descriptor)
        os.close(binding_admin_descriptor)

    unsafe_unchanged = True
    unsafe_cases = (
        "nonempty",
        "mode",
        "hardlink",
        "symlink",
        "fifo",
        "directory",
    )
    for case in unsafe_cases:
        path, expected_uid, expected_gid = lock_fixture(f"unsafe-{case}")
        if case == "nonempty":
            path.write_bytes(b"x")
            path.chmod(0o600)
        elif case == "mode":
            path.write_bytes(b"")
            path.chmod(0o644)
        elif case == "hardlink":
            path.write_bytes(b"")
            path.chmod(0o600)
            os.link(path, path.with_name("lifecycle.lock.peer"))
        elif case == "symlink":
            path.symlink_to("missing")
        elif case == "fifo":
            os.mkfifo(path, mode=0o600)
        else:
            path.mkdir(mode=0o700)
        before = tree_digest(path.parent)
        try:
            unexpected = admin._open_install_lifecycle_lock(
                path,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            close_lock(unexpected)
            rejected = False
        except admin.RelayAdminError as exc:
            rejected = exc.error_id == "lifecycle_lock_failed"
        after = tree_digest(path.parent)
        unsafe_unchanged = unsafe_unchanged and rejected and before == after

    failure_cleanup = True
    for label in ("fchmod", "flock"):
        path, expected_uid, expected_gid = lock_fixture(f"failure-{label}")
        before_descriptors = descriptor_count()
        if label == "fchmod":
            original = admin.os.fchmod

            def injected_failure(*_args, **_kwargs):
                raise OSError("injected fchmod failure")

            admin.os.fchmod = injected_failure
        else:
            original = admin.fcntl.flock

            def injected_failure(*_args, **_kwargs):
                raise OSError("injected flock failure")

            admin.fcntl.flock = injected_failure
        try:
            try:
                unexpected = admin._open_install_lifecycle_lock(
                    path,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
                close_lock(unexpected)
                rejected = False
            except admin.RelayAdminError as exc:
                rejected = exc.error_id == "lifecycle_lock_failed"
        finally:
            if label == "fchmod":
                admin.os.fchmod = original
            else:
                admin.fcntl.flock = original
        after_descriptors = descriptor_count()
        stable = (
            before_descriptors is None
            or after_descriptors is None
            or before_descriptors == after_descriptors
        )
        failure_cleanup = (
            failure_cleanup
            and rejected
            and stable
            and not path.exists()
        )

    race_path, race_uid, race_gid = lock_fixture("post-flock-race")
    race_path.write_bytes(b"")
    race_path.chmod(0o600)
    race_before_descriptors = descriptor_count()
    original_flock = admin.fcntl.flock
    injected = False

    def replace_after_flock(descriptor: int, operation: int):
        nonlocal injected
        result = original_flock(descriptor, operation)
        if not injected and operation & fcntl.LOCK_EX:
            injected = True
            race_path.rename(race_path.with_name("lifecycle.lock.retired"))
            race_path.write_bytes(b"")
            race_path.chmod(0o600)
        return result

    admin.fcntl.flock = replace_after_flock
    try:
        try:
            unexpected = admin._open_install_lifecycle_lock(
                race_path,
                expected_uid=race_uid,
                expected_gid=race_gid,
            )
            close_lock(unexpected)
            race_rejected = False
        except admin.RelayAdminError as exc:
            race_rejected = exc.error_id == "lifecycle_lock_failed"
    finally:
        admin.fcntl.flock = original_flock
    race_after_descriptors = descriptor_count()
    race_fd_stable = (
        race_before_descriptors is None
        or race_after_descriptors is None
        or race_before_descriptors == race_after_descriptors
    )
    race_rejected = race_rejected and injected and race_fd_stable

    require(safe_created, "safe lifecycle lock was not created exactly", failures)
    require(safe_reopened, "safe existing lifecycle lock changed", failures)
    require(
        contention_rejected,
        "lifecycle lock contention did not fail closed",
        failures,
    )
    require(
        admin_acquisition_race_rejected,
        "admin path replacement during lock acquisition was not rejected",
        failures,
    )
    require(
        admin_post_open_race_rejected,
        "admin replacement before lock creation was not rejected",
        failures,
    )
    require(
        admin_binding_race_rejected,
        "admin path replacement after lock acquisition was not rejected",
        failures,
    )
    require(
        unsafe_unchanged,
        "unsafe existing lifecycle lock was accepted or modified",
        failures,
    )
    require(
        failure_cleanup,
        "lifecycle lock acquisition failure leaked state or descriptors",
        failures,
    )
    require(
        race_rejected,
        "post-flock lifecycle lock replacement was not rejected",
        failures,
    )
    return {
        "admin_acquisition_race_rejected": (
            admin_acquisition_race_rejected
        ),
        "admin_binding_race_rejected": admin_binding_race_rejected,
        "admin_post_open_race_rejected": admin_post_open_race_rejected,
        "contention_rejected": contention_rejected,
        "failure_cleanup": failure_cleanup,
        "post_flock_race_rejected": race_rejected,
        "safe_created": safe_created,
        "safe_reopened": safe_reopened,
        "unsafe_cases": len(unsafe_cases),
        "unsafe_unchanged": unsafe_unchanged,
    }


def main() -> int:
    failures: list[str] = []
    status_before = git_output("status", "--porcelain=v1", "--untracked-files=all")
    with tempfile.TemporaryDirectory(prefix="relay-offline-install-") as temporary_name:
        temporary = Path(temporary_name).resolve()
        guard = temporary / "guard"
        write_guard(guard)
        env = isolated_env(temporary, guard)
        try:
            bundle, bundle_sha = build_real_bundle(temporary)
            root_name, files = archive_parts(bundle)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"fixture_error:{type(exc).__name__}")
            bundle = temporary / "missing"
            bundle_sha = "0" * 64
            root_name = "agentops-mis-relay-0"
            files = {}

        guard_probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, socket, subprocess\n"
                    "blocked = 0\n"
                    "for action in (\n"
                    "  lambda: socket.create_connection(('127.0.0.1', 9)),\n"
                    "  lambda: subprocess.run(['systemctl', '--version']),\n"
                    "  lambda: os.system('useradd should-not-run'),\n"
                    "):\n"
                    "  try:\n"
                    "    action()\n"
                    "  except RuntimeError:\n"
                    "    blocked += 1\n"
                    "raise SystemExit(0 if blocked == 3 else 1)\n"
                ),
            ],
            env=env,
            check=False,
            capture_output=True,
            timeout=20,
        )
        require(guard_probe.returncode == 0, "external behavior guard not enforced", failures)
        admin = load_admin()
        lock_results = lifecycle_lock_cases(
            admin,
            temporary / "lifecycle-lock-cases",
            failures,
        )
        try:
            inspected_bundle = admin.inspect_bundle(bundle, bundle_sha)
            publish_preflight_zero_write = True
            for case in ("admin-mode", "lock-mode"):
                root = temporary / f"lifecycle-publish-{case}"
                root.mkdir(mode=0o700)
                admin_state = root / "var" / "lib" / "agentops-relayctl"
                admin_state.mkdir(parents=True, mode=0o700)
                if case == "admin-mode":
                    admin_state.chmod(0o755)
                else:
                    lock = admin_state / "lifecycle.lock"
                    lock.write_bytes(b"")
                    lock.chmod(0o644)
                before = tree_digest(root)
                try:
                    plan = admin._plan_for_install(
                        root,
                        inspected_bundle,
                    )
                except admin.RelayAdminError as exc:
                    rejected = exc.error_id == "recovery_required"
                else:
                    try:
                        admin._publish_install(plan)
                        rejected = False
                    except admin.RelayAdminError as exc:
                        rejected = exc.error_id in {
                            "lifecycle_lock_failed",
                            "recovery_required",
                        }
                publish_preflight_zero_write = (
                    publish_preflight_zero_write
                    and rejected
                    and tree_digest(root) == before
                )
            require(
                publish_preflight_zero_write,
                "unsafe installer lock preflight wrote install state",
                failures,
            )
        except (OSError, ValueError, admin.RelayAdminError):
            publish_preflight_zero_write = False
            failures.append("lifecycle lock publish preflight fixture failed")
        lock_results["publish_preflight_zero_write"] = (
            publish_preflight_zero_write
        )

        root_target = temporary / "root-target"
        root_target.mkdir(mode=0o700)
        root_link = temporary / "root-link"
        root_link.symlink_to(root_target, target_is_directory=True)
        code, root_link_result, root_link_output = run_admin(
            env, root_link, "inspect", bundle, bundle_sha
        )
        require(
            code != 0 and root_link_result.get("error_id") == "root_not_directory",
            "symlinked --root was not rejected",
            failures,
        )

        replacement_root = temporary / "replacement-root"
        replacement_root.mkdir(mode=0o700)
        code, replacement_preview, replacement_preview_output = run_admin(
            env, replacement_root, "install", bundle, bundle_sha
        )
        replacement_plan = replacement_preview.get("plan_sha256")
        retired_root = temporary / "replacement-root-retired"
        replacement_root.rename(retired_root)
        replacement_root.mkdir(mode=0o700)
        code, replacement_result, replacement_output = run_admin(
            env,
            replacement_root,
            "install",
            bundle,
            bundle_sha,
            "--confirm-install",
            "--plan-sha256",
            str(replacement_plan),
        )
        require(
            code != 0
            and replacement_result.get("error_id") == "plan_sha256_mismatch",
            "plan hash did not bind the target root identity",
            failures,
        )

        recovery_root = temporary / "recovery-root"
        recovery_root.mkdir(mode=0o700)
        transaction = (
            recovery_root
            / "var"
            / "lib"
            / "agentops-relayctl"
            / "transaction.json"
        )
        transaction.parent.mkdir(parents=True, mode=0o700)
        transaction.write_text('{"state_id":"prepared"}\n', encoding="ascii")
        transaction.chmod(0o600)
        code, recovery_result, recovery_output = run_admin(
            env, recovery_root, "install", bundle, bundle_sha
        )
        require(
            code != 0 and recovery_result.get("error_id") == "recovery_required",
            "interrupted transaction marker did not fail closed",
            failures,
        )

        prepublish_root = temporary / "prepublish-recovery-root"
        prepublish_root.mkdir(mode=0o700)
        prepublish_preview_code, prepublish_preview, prepublish_preview_output = run_admin(
            env, prepublish_root, "install", bundle, bundle_sha
        )
        prepublish_plan = str(prepublish_preview.get("plan_sha256") or "")
        require(
            prepublish_preview_code == 0 and len(prepublish_plan) == 64,
            "pre-publication recovery fixture did not produce a plan",
            failures,
        )
        prepublish_admin = (
            prepublish_root / "var" / "lib" / "agentops-relayctl"
        )
        prepublish_admin.mkdir(parents=True, mode=0o700)
        prepublish_marker = (
            prepublish_admin / f".transaction-{prepublish_plan[:16]}.tmp"
        )
        prepublish_marker.write_text(
            '{"state_id":"prepared"}\n',
            encoding="ascii",
        )
        prepublish_marker.chmod(0o600)
        code, prepublish_result, prepublish_output = run_admin(
            env, prepublish_root, "install", bundle, bundle_sha
        )
        require(
            code != 0
            and prepublish_result.get("error_id") == "recovery_required",
            "pre-publication transaction marker did not fail closed",
            failures,
        )

        rollback_root = temporary / "rollback-root"
        rollback_root.mkdir(mode=0o700)
        rollback_canary = (
            rollback_root / "var" / "lib" / "agentops-mis-relay" / "epochs.json"
        )
        rollback_canary.parent.mkdir(parents=True, mode=0o700)
        rollback_canary.write_bytes(CANARY + b"_ROLLBACK")
        rollback_before = snapshot(rollback_canary)
        try:
            rollback_bundle = admin.inspect_bundle(bundle, bundle_sha)
            rollback_plan = admin._plan_for_install(rollback_root, rollback_bundle)
            rollback_paths = rollback_plan.paths
            original_symlink = admin.os.symlink

            def fail_controller_link(source, destination, *args, **kwargs):
                if Path(destination).name == "controller":
                    raise OSError("injected publish failure")
                return original_symlink(source, destination, *args, **kwargs)

            admin.os.symlink = fail_controller_link
            try:
                admin._publish_install(rollback_plan)
                rollback_error = ""
            except admin.RelayAdminError as exc:
                rollback_error = exc.error_id
            finally:
                admin.os.symlink = original_symlink
            refreshed_rollback_plan = admin._plan_for_install(
                rollback_root,
                rollback_bundle,
            )
            rollback_clean = (
                rollback_error == "install_publish_failed"
                and rollback_plan.activation_namespace_state == "missing"
                and refreshed_rollback_plan.activation_namespace_state
                == "exact_empty"
                and not refreshed_rollback_plan.no_op
                and not any(
                    os.path.lexists(path)
                    for path in (
                        rollback_paths.release,
                        rollback_paths.current,
                        rollback_paths.controller,
                        rollback_paths.stable_launcher,
                        rollback_paths.unit,
                        rollback_paths.transaction,
                    )
                )
                and snapshot(rollback_canary) == rollback_before
            )
        except (OSError, RuntimeError, ValueError) as exc:
            del exc
            rollback_clean = False
        require(
            rollback_clean,
            "mid-publish failure did not roll back exact install artifacts",
            failures,
        )

        swap_root = temporary / "root-swap"
        swap_root.mkdir(mode=0o700)
        swap_canary = swap_root / "var" / "lib" / "agentops-mis-relay" / "epochs.json"
        swap_canary.parent.mkdir(parents=True, mode=0o700)
        swap_canary.write_bytes(CANARY + b"_ROOT_SWAP")
        swap_before = snapshot(swap_canary)
        swap_retired = temporary / "root-swap-retired"
        root_swap_safe = False
        try:
            swap_plan = admin._plan_for_install(swap_root, rollback_bundle)
            original_check_install_state = admin._check_install_state
            swapped = False

            def swap_before_first_write(paths, candidate_bundle):
                nonlocal swapped
                if not swapped and paths.root == Path("."):
                    swap_root.rename(swap_retired)
                    swap_root.mkdir(mode=0o700)
                    swapped = True
                return original_check_install_state(paths, candidate_bundle)

            admin._check_install_state = swap_before_first_write
            try:
                admin._publish_install(swap_plan)
                swap_error = ""
            except admin.RelayAdminError as exc:
                swap_error = exc.error_id
            finally:
                admin._check_install_state = original_check_install_state
            retired_paths = admin.InstallPaths.for_bundle(
                swap_retired,
                rollback_bundle,
            )
            root_swap_safe = (
                swapped
                and swap_error == "plan_stale"
                and not any(swap_root.iterdir())
                and snapshot(
                    swap_retired
                    / "var"
                    / "lib"
                    / "agentops-mis-relay"
                    / "epochs.json"
                )
                == swap_before
                and not any(
                    os.path.lexists(path)
                    for path in (
                        retired_paths.release,
                        retired_paths.current,
                        retired_paths.controller,
                        retired_paths.stable_launcher,
                        retired_paths.unit,
                        retired_paths.transaction,
                    )
                )
            )
        except Exception:
            root_swap_safe = False
        require(
            root_swap_safe,
            "confirmed install wrote through a swapped root path",
            failures,
        )

        install_root = temporary / "install-root"
        install_root.mkdir(mode=0o700)
        protected_config = install_root / "etc" / "agentops-mis-relay" / "config.json"
        protected_epoch = install_root / "var" / "lib" / "agentops-mis-relay" / "epochs.json"
        protected_config.parent.mkdir(parents=True, mode=0o700)
        protected_epoch.parent.mkdir(parents=True, mode=0o700)
        protected_config.write_bytes(CANARY + b"_CONFIG")
        protected_epoch.write_bytes(CANARY + b"_EPOCH")
        old_ns = 1_600_000_000_123_456_789
        os.utime(protected_config, ns=(old_ns, old_ns))
        os.utime(protected_epoch, ns=(old_ns + 1, old_ns + 1))
        config_before = snapshot(protected_config)
        epoch_before = snapshot(protected_epoch)
        pristine_digest = tree_digest(install_root)

        code, inspected, output = run_admin(
            env, install_root, "inspect", bundle, bundle_sha
        )
        require(code == 0 and inspected.get("ok") is True, "inspect failed", failures)
        require(
            inspected.get("schema_id") == BUNDLE_SCHEMA
            and inspected.get("checksums_verified") is True
            and inspected.get("wheel_safe") is True,
            "inspect did not verify bundle contract",
            failures,
        )

        code, preview_one, preview_output = run_admin(
            env, install_root, "install", bundle, bundle_sha
        )
        code_two, preview_two, _ = run_admin(
            env, install_root, "install", bundle, bundle_sha
        )
        plan = preview_one.get("plan_sha256")
        require(
            code == code_two == 0
            and preview_one == preview_two
            and isinstance(plan, str)
            and len(plan) == 64,
            "dry-run plan was not stable",
            failures,
        )
        require(
            preview_one.get("dry_run") is True
            and preview_one.get("confirmed") is False
            and preview_one.get("installed") is False,
            "install was not dry-run by default",
            failures,
        )
        require(
            tree_digest(install_root) == pristine_digest,
            "dry-run changed sandbox state",
            failures,
        )

        code, wrong_plan, _ = run_admin(
            env,
            install_root,
            "install",
            bundle,
            bundle_sha,
            "--confirm-install",
            "--plan-sha256",
            "f" * 64,
        )
        require(
            code != 0 and wrong_plan.get("error_id") == "plan_sha256_mismatch",
            "wrong plan hash was not rejected",
            failures,
        )
        code, missing_plan, _ = run_admin(
            env,
            install_root,
            "install",
            bundle,
            bundle_sha,
            "--confirm-install",
        )
        require(
            code != 0 and missing_plan.get("error_id") == "plan_sha256_required",
            "confirmation without plan hash was not rejected",
            failures,
        )
        require(
            tree_digest(install_root) == pristine_digest,
            "rejected confirmation changed sandbox state",
            failures,
        )

        code, installed, installed_output = run_admin(
            env,
            install_root,
            "install",
            bundle,
            bundle_sha,
            "--confirm-install",
            "--plan-sha256",
            str(plan),
        )
        require(
            code == 0
            and installed.get("installed") is True
            and installed.get("confirmed") is True,
            "confirmed install failed",
            failures,
        )
        release_id = str(installed.get("release_id") or "")
        release = install_root / "opt" / "agentops-mis-relay" / "releases" / release_id
        current = install_root / "opt" / "agentops-mis-relay" / "current"
        controller = install_root / "opt" / "agentops-mis-relay" / "controller"
        stable = install_root / "usr" / "local" / "bin" / "agentops-relay"
        unit = install_root / "etc" / "systemd" / "system" / "agentops-mis-relay.service"
        require(release.is_dir() and not release.is_symlink(), "release missing", failures)
        require(
            current.is_symlink()
            and controller.is_symlink()
            and os.readlink(current) == f"releases/{release_id}"
            and os.readlink(controller) == f"releases/{release_id}",
            "current/controller links invalid",
            failures,
        )
        require(
            stable.is_symlink()
            and os.readlink(stable)
            == "../../../opt/agentops-mis-relay/current/bin/agentops-relay",
            "stable launcher link invalid",
            failures,
        )
        require(
            (release / "private" / "site-packages" / "agentops_mis_cli" / "relay_daemon.py").is_file(),
            "wheel was not unpacked into private site-packages",
            failures,
        )
        require(
            stat.S_IMODE((release / "bin" / "agentops-relay").stat().st_mode) == 0o755,
            "release launcher mode invalid",
            failures,
        )
        require(
            unit.read_bytes() == files.get("systemd/agentops-mis-relay.service", b""),
            "installed unit did not match verified bundle",
            failures,
        )
        require(
            not (install_root / "etc" / "agentops-mis-relay" / "config.example.json").exists(),
            "installer generated or installed live configuration",
            failures,
        )
        require(
            not list((install_root / "opt" / "agentops-mis-relay" / "releases").glob(".installing-*"))
            and not list(unit.parent.glob(".*.tmp")),
            "install staging files remained",
            failures,
        )
        require(snapshot(protected_config) == config_before, "config canary changed", failures)
        require(snapshot(protected_epoch) == epoch_before, "epoch canary changed", failures)

        installed_digest = tree_digest(install_root)
        code, noop_preview, _ = run_admin(
            env, install_root, "install", bundle, bundle_sha
        )
        noop_plan = noop_preview.get("plan_sha256")
        code_confirm, noop_confirm, _ = run_admin(
            env,
            install_root,
            "install",
            bundle,
            bundle_sha,
            "--confirm-install",
            "--plan-sha256",
            str(noop_plan),
        )
        require(
            code == code_confirm == 0
            and noop_preview.get("no_op") is True
            and noop_confirm.get("no_op") is True
            and noop_confirm.get("installed") is False,
            "same release was not an idempotent no-op",
            failures,
        )
        require(
            tree_digest(install_root) == installed_digest,
            "same release no-op changed installed state",
            failures,
        )

        if files:
            different_bundle, different_sha = rebuild_contract(
                temporary / "different-version.tar.gz",
                root_name,
                files,
                version="9.9.9",
            )
            code, upgrade, _ = run_admin(
                env, install_root, "install", different_bundle, different_sha
            )
            require(
                code != 0
                and upgrade.get("error_id") == "upgrade_required"
                and upgrade.get("future_operation_id") == "upgrade",
                "different version did not fail closed for future upgrade",
                failures,
            )
            require(
                tree_digest(install_root) == installed_digest,
                "different version attempt changed installed state",
                failures,
            )

        attack_root = temporary / "attack-root"
        attack_root.mkdir(mode=0o700)
        code, wrong_hash, _ = run_admin(
            env, attack_root, "inspect", bundle, "0" * 64
        )
        require(
            code != 0 and wrong_hash.get("error_id") == "archive_sha256_mismatch",
            "archive SHA mismatch was not rejected",
            failures,
        )

        if files:
            tampered_files = dict(files)
            tampered_files["config/config.example.json"] += b"\n"
            tampered, tampered_sha = make_archive(
                temporary / "tampered.tar.gz", root_name, tampered_files
            )
            code, tamper_result, _ = run_admin(
                env, attack_root, "inspect", tampered, tampered_sha
            )
            require(
                code != 0
                and tamper_result.get("error_id")
                in {"manifest_file_mismatch", "checksum_mismatch"},
                "manifest/checksum tamper was not rejected",
                failures,
            )

            deeply_nested_files = dict(files)
            deeply_nested_files["manifest.json"] = b"[" * 2048 + b"]" * 2048
            deeply_nested, deeply_nested_sha = make_archive(
                temporary / "deeply-nested-json.tar.gz",
                root_name,
                deeply_nested_files,
            )
            code, deeply_nested_result, deeply_nested_output = run_admin(
                env,
                attack_root,
                "inspect",
                deeply_nested,
                deeply_nested_sha,
            )
            require(
                code != 0
                and deeply_nested_result.get("error_id")
                in {"invalid_json", "manifest_shape_invalid"}
                and b"Traceback" not in deeply_nested_output
                and str(ROOT).encode("utf-8") not in deeply_nested_output,
                "deeply nested JSON bypassed the redacted error contract",
                failures,
            )

            duplicate, duplicate_sha = make_archive(
                temporary / "duplicate.tar.gz",
                root_name,
                files,
                duplicate="manifest.json",
            )
            code, duplicate_result, _ = run_admin(
                env, attack_root, "inspect", duplicate, duplicate_sha
            )
            require(
                code != 0
                and duplicate_result.get("error_id") == "duplicate_archive_member",
                "duplicate archive member was not rejected",
                failures,
            )

            traversal, traversal_sha = make_archive(
                temporary / "traversal.tar.gz",
                root_name,
                files,
                special=(f"{root_name}/../outside", tarfile.REGTYPE),
            )
            code, traversal_result, _ = run_admin(
                env, attack_root, "inspect", traversal, traversal_sha
            )
            require(
                code != 0
                and traversal_result.get("error_id") == "unsafe_archive_member",
                "archive traversal was not rejected",
                failures,
            )

            for label, type_ in (
                ("symlink", tarfile.SYMTYPE),
                ("device", tarfile.CHRTYPE),
            ):
                special, special_sha = make_archive(
                    temporary / f"{label}.tar.gz",
                    root_name,
                    files,
                    special=(f"{root_name}/unsafe-{label}", type_),
                )
                code, special_result, _ = run_admin(
                    env, attack_root, "inspect", special, special_sha
                )
                require(
                    code != 0
                    and special_result.get("error_id")
                    == "archive_special_member_rejected",
                    f"archive {label} was not rejected",
                    failures,
                )

            oversized, oversized_sha = make_archive(
                temporary / "oversized.tar.gz",
                root_name,
                files,
                oversized=True,
            )
            code, oversized_result, _ = run_admin(
                env, attack_root, "inspect", oversized, oversized_sha
            )
            require(
                code != 0
                and oversized_result.get("error_id") == "archive_member_size_invalid",
                "oversized archive member was not rejected",
                failures,
            )

            manifest = json.loads(files["manifest.json"])
            wheel_path = next(
                record["path"]
                for record in manifest["files"]
                if record["path"].endswith(".whl")
            )
            for label, bad_wheel in (
                (
                    "wheel-traversal",
                    malicious_wheel(files[wheel_path], path="../outside.py"),
                ),
                (
                    "wheel-symlink",
                    malicious_wheel(
                        files[wheel_path],
                        path="agentops_mis_cli/unsafe-link",
                        symlink=True,
                    ),
                ),
            ):
                bad_bundle, bad_sha = rebuild_contract(
                    temporary / f"{label}.tar.gz",
                    root_name,
                    files,
                    wheel_data=bad_wheel,
                )
                code, bad_result, _ = run_admin(
                    env, attack_root, "inspect", bad_bundle, bad_sha
                )
                expected_errors = (
                    {"unsafe_archive_member"}
                    if label == "wheel-traversal"
                    else {"wheel_special_member_rejected"}
                )
                require(
                    code != 0 and bad_result.get("error_id") in expected_errors,
                    f"{label} was not rejected",
                    failures,
                )

            with zipfile.ZipFile(io.BytesIO(files[wheel_path]), "r") as source_wheel:
                source_names = source_wheel.namelist()
            entry_points_name = next(
                name
                for name in source_names
                if name.endswith(".dist-info/entry_points.txt")
            )
            daemon_name = "agentops_mis_cli/relay_daemon.py"
            native_wheel = rewrite_wheel(
                files[wheel_path],
                additions={"agentops_mis_cli/native_extension.so": b"native"},
                refresh_record=True,
            )
            entrypoint_wheel = rewrite_wheel(
                files[wheel_path],
                replacements={
                    entry_points_name: (
                        "[console_scripts]\n"
                        "agentops = agentops_mis_cli.cli:main\n"
                        "agentops-worker = agentops_mis_cli.worker:main\n"
                    ).encode("ascii")
                },
                refresh_record=True,
            )
            record_tamper_wheel = rewrite_wheel(
                files[wheel_path],
                replacements={daemon_name: b"# tampered\n"},
                refresh_record=False,
            )
            incomplete_wheel = rewrite_wheel(
                files[wheel_path],
                removals={"agentops_mis_cli/relay_tunnel.py"},
                refresh_record=True,
            )
            unexpected_module_wheel = rewrite_wheel(
                files[wheel_path],
                additions={"agentops_mis_cli/unexpected.py": b"VALUE = 1\n"},
                refresh_record=True,
            )
            for label, bad_wheel, expected_error in (
                ("wheel-native", native_wheel, "wheel_member_invalid"),
                (
                    "wheel-entrypoint",
                    entrypoint_wheel,
                    "wheel_entrypoint_invalid",
                ),
                (
                    "wheel-record",
                    record_tamper_wheel,
                    "wheel_record_invalid",
                ),
                (
                    "wheel-incomplete",
                    incomplete_wheel,
                    "wheel_metadata_invalid",
                ),
                (
                    "wheel-unexpected-module",
                    unexpected_module_wheel,
                    "wheel_metadata_invalid",
                ),
            ):
                bad_bundle, bad_sha = rebuild_contract(
                    temporary / f"{label}.tar.gz",
                    root_name,
                    files,
                    wheel_data=bad_wheel,
                )
                code, bad_result, _ = run_admin(
                    env,
                    attack_root,
                    "inspect",
                    bad_bundle,
                    bad_sha,
                )
                require(
                    code != 0 and bad_result.get("error_id") == expected_error,
                    f"{label} was not rejected",
                    failures,
                )

            misnamed_bundle, misnamed_sha = rebuild_contract(
                temporary / "wheel-misnamed.tar.gz",
                root_name,
                files,
                wheel_path_override=(
                    "wheel/relay_package-0.1.0-py3-none-any.whl"
                ),
            )
            code, misnamed_result, _ = run_admin(
                env,
                attack_root,
                "inspect",
                misnamed_bundle,
                misnamed_sha,
            )
            require(
                code != 0
                and misnamed_result.get("error_id") == "manifest_files_invalid",
                "wheel filename was not bound to the manifest version",
                failures,
            )

        outside = temporary / "outside"
        outside.mkdir()
        outside_canary = outside / "canary"
        outside_canary.write_bytes(CANARY)
        symlink_root = temporary / "symlink-root"
        symlink_root.mkdir(mode=0o700)
        (symlink_root / "opt").symlink_to(outside, target_is_directory=True)
        code, unsafe_parent, _ = run_admin(
            env, symlink_root, "install", bundle, bundle_sha
        )
        require(
            code != 0
            and unsafe_parent.get("error_id") == "install_parent_invalid"
            and outside_canary.read_bytes() == CANARY,
            "symlinked install parent was not rejected safely",
            failures,
        )

        all_output = (
            output
            + preview_output
            + installed_output
            + root_link_output
            + replacement_preview_output
            + replacement_output
            + recovery_output
            + prepublish_preview_output
            + prepublish_output
        )
        require(
            ENV_CANARY.encode("ascii") not in all_output,
            "environment credential canary leaked to output",
            failures,
        )
        for canary in BODY_CANARIES:
            require(canary not in all_output, "bundle body leaked to output", failures)
        for pattern in SECRET_PATTERNS:
            require(not pattern.search(all_output), "credential pattern found in output", failures)
        require(snapshot(protected_config) == config_before, "final config canary changed", failures)
        require(snapshot(protected_epoch) == epoch_before, "final epoch canary changed", failures)

    status_after = git_output("status", "--porcelain=v1", "--untracked-files=all")
    require(status_after == status_before, "smoke changed repository status", failures)
    result = {
        "archive_attack_cases": 15,
        "archive_sha256_verified": True,
        "bundle_schema_id": BUNDLE_SCHEMA,
        "canaries_preserved": not failures,
        "confirmed_install": not failures,
        "dry_run_stable": not failures,
        "failures": failures,
        "no_external_behavior": guard_probe.returncode == 0,
        "ok": not failures,
        "lifecycle_lock_cases": lock_results,
        "publish_failure_rolled_back": rollback_clean,
        "root_swap_anchored": root_swap_safe,
        "same_release_no_op": not failures,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
