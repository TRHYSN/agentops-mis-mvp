#!/usr/bin/env python3
"""Build a deterministic, offline AgentOps MIS Relay release bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "agentops.relay.release-bundle.v1"
BACKEND_RELATIVE = Path("agentops_mis_cli/_build_backend.py")
CONFIG_RELATIVE = Path("packaging/relay/config.example.json")
SYSTEMD_RELATIVE = Path("packaging/relay/systemd/agentops-mis-relay.service")
RELEASE_INPUTS = (
    "agentops_mis_cli",
    "agentops_mis_core",
    "packaging/relay/config.example.json",
    "packaging/relay/systemd/agentops-mis-relay.service",
    "pyproject.toml",
    "scripts/build_relay_release.py",
)
VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise RuntimeError(message)
    return result


def current_commit() -> str:
    commit = run_git("rev-parse", "HEAD").stdout.strip().lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise RuntimeError("HEAD did not resolve to a full hexadecimal commit")
    return commit


def require_committed_release_inputs(commit: str) -> None:
    result = run_git("diff", "--quiet", commit, "--", *RELEASE_INPUTS, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError("unable to verify Relay release input state")
    if result.returncode == 1:
        raise RuntimeError(
            "Relay release inputs differ from the selected commit; "
            "commit or restore them before building"
        )
    untracked = run_git(
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *RELEASE_INPUTS,
    ).stdout.splitlines()
    if untracked:
        raise RuntimeError(
            "untracked Relay release inputs are not allowed: " + ", ".join(sorted(untracked))
        )


def read_regular_source(path: Path, source_root: Path) -> bytes:
    resolved = path.resolve(strict=True)
    if (
        path.is_symlink()
        or source_root.resolve() not in resolved.parents
        or not resolved.is_file()
    ):
        raise RuntimeError(
            f"unsafe Relay release source: {path.relative_to(source_root)}"
        )
    return resolved.read_bytes()


def safe_archive_name(name: str, *, kind: str) -> str:
    if "\\" in name:
        raise RuntimeError(f"unsafe {kind} member: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RuntimeError(f"unsafe {kind} member: {name}")
    return path.as_posix()


def materialize_commit_snapshot(commit: str, destination: Path) -> None:
    result = subprocess.run(
        [
            "git",
            "archive",
            "--format=tar",
            commit,
            "--",
            *RELEASE_INPUTS,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "unable to read Relay release inputs from Git")

    destination.mkdir()
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            name = safe_archive_name(member.name, kind="Git snapshot")
            if name in seen:
                raise RuntimeError(f"Git snapshot contains duplicate member: {name}")
            seen.add(name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"Git snapshot contains unsupported member: {name}")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Git snapshot member is unreadable: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())

    for relative in (BACKEND_RELATIVE, CONFIG_RELATIVE, SYSTEMD_RELATIVE):
        path = destination / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Git snapshot is missing required source: {relative}")


def load_snapshot_backend(snapshot_root: Path):
    backend_path = snapshot_root / BACKEND_RELATIVE
    spec = importlib.util.spec_from_file_location(
        "_agentops_relay_release_build_backend",
        backend_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load the snapshotted offline build backend")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def canonicalize_wheel(raw_wheel: bytes) -> bytes:
    source = io.BytesIO(raw_wheel)
    target = io.BytesIO()
    with zipfile.ZipFile(source, "r") as incoming:
        infos = incoming.infolist()
        names = [
            safe_archive_name(info.filename, kind="wheel")
            for info in infos
        ]
        if len(names) != len(set(names)):
            raise RuntimeError("wheel contains duplicate members")
        members = []
        for info, name in zip(infos, names):
            if info.flag_bits & 0x1:
                raise RuntimeError("encrypted wheel members are not supported")
            members.append((name, info.is_dir(), b"" if info.is_dir() else incoming.read(info)))

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as outgoing:
        outgoing.comment = b""
        for name, is_dir, data in sorted(members, key=lambda item: item[0]):
            normalized_name = name.rstrip("/") + "/" if is_dir else name
            info = zipfile.ZipInfo(normalized_name, date_time=FIXED_ZIP_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = ((0o40755 if is_dir else 0o100644) & 0xFFFF) << 16
            outgoing.writestr(info, data)
    return target.getvalue()


def build_canonical_wheel(
    temporary_root: Path,
    build_backend,
) -> tuple[str, bytes]:
    wheel_dir = temporary_root / "wheel-build"
    wheel_dir.mkdir()
    wheel_name = build_backend.build_wheel(str(wheel_dir))
    if Path(wheel_name).name != wheel_name or not wheel_name.endswith(".whl"):
        raise RuntimeError("offline build backend returned an unsafe wheel name")
    wheel_path = wheel_dir / wheel_name
    if not wheel_path.is_file():
        raise RuntimeError("offline build backend did not create the declared wheel")
    canonical = canonicalize_wheel(wheel_path.read_bytes())
    with zipfile.ZipFile(io.BytesIO(canonical), "r") as wheel:
        required = {
            f"{build_backend.DIST_INFO}/METADATA",
            f"{build_backend.DIST_INFO}/RECORD",
            f"{build_backend.DIST_INFO}/WHEEL",
            f"{build_backend.DIST_INFO}/entry_points.txt",
        }
        if not required.issubset(set(wheel.namelist())):
            raise RuntimeError("canonical wheel is missing required metadata")
    return wheel_name, canonical


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def add_tar_member(
    archive: tarfile.TarFile,
    name: str,
    *,
    data: bytes | None = None,
    directory: bool = False,
) -> None:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if directory:
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
        archive.addfile(info)
        return
    if data is None:
        raise RuntimeError("file tar member requires data")
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def canonical_tar_gz(root_name: str, files: dict[str, bytes]) -> bytes:
    tar_buffer = io.BytesIO()
    directories = set()
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe bundle member: {name}")
        for parent in path.parents:
            if parent != PurePosixPath("."):
                directories.add(parent.as_posix())

    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        add_tar_member(archive, root_name, directory=True)
        for directory in sorted(directories):
            add_tar_member(archive, f"{root_name}/{directory}", directory=True)
        for name in sorted(files):
            add_tar_member(archive, f"{root_name}/{name}", data=files[name])

    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=compressed,
        compresslevel=9,
        mtime=0,
    ) as gzip_file:
        gzip_file.write(tar_buffer.getvalue())
    return compressed.getvalue()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_safe_output(output_dir: Path, bundle_name: str) -> Path:
    unresolved = output_dir.expanduser()
    if unresolved.exists() and unresolved.is_symlink():
        raise RuntimeError("output directory must not be a symlink")
    resolved = unresolved.resolve()
    root = ROOT.resolve()
    repo_dist = root / "dist"
    if resolved == repo_dist or repo_dist in resolved.parents:
        raise RuntimeError("Relay releases must not be written under the repository dist directory")

    target = resolved / bundle_name
    if is_within(target, root):
        relative = target.relative_to(root).as_posix()
        ignored = run_git("check-ignore", "--quiet", "--", relative, check=False)
        if ignored.returncode not in {0, 1}:
            raise RuntimeError("unable to verify repository output ignore policy")
        if ignored.returncode != 0:
            raise RuntimeError(
                "repository-local release output is not ignored; choose an external or ignored output directory"
            )
    if target.exists():
        raise RuntimeError(f"release bundle already exists: {target.name}")
    return target


def build_release(output_dir: Path) -> dict[str, object]:
    commit = current_commit()
    require_committed_release_inputs(commit)

    with tempfile.TemporaryDirectory(prefix="agentops-relay-release-") as temporary:
        temporary_root = Path(temporary)
        snapshot_root = temporary_root / "snapshot"
        materialize_commit_snapshot(commit, snapshot_root)
        build_backend = load_snapshot_backend(snapshot_root)
        version = str(build_backend.VERSION).strip()
        if not VERSION_PATTERN.fullmatch(version):
            raise RuntimeError("offline build backend returned an unsafe version")
        root_name = f"agentops-mis-relay-{version}"
        archive_name = f"{root_name}-{commit[:12]}.tar.gz"
        target = require_safe_output(output_dir, archive_name)
        wheel_name, wheel_data = build_canonical_wheel(
            temporary_root,
            build_backend,
        )

        payload = {
            f"wheel/{wheel_name}": wheel_data,
            "systemd/agentops-mis-relay.service": read_regular_source(
                snapshot_root / SYSTEMD_RELATIVE,
                snapshot_root,
            ),
            "config/config.example.json": read_regular_source(
                snapshot_root / CONFIG_RELATIVE,
                snapshot_root,
            ),
        }
        file_records = [
            {
                "path": path,
                "sha256": sha256_bytes(data),
                "size": len(data),
            }
            for path, data in sorted(payload.items())
        ]
        manifest = {
            "files": file_records,
            "git_commit": commit,
            "schema": SCHEMA,
            "version": version,
        }
        manifest_data = canonical_json(manifest)
        checksum_inputs = {**payload, "manifest.json": manifest_data}
        checksums_data = "".join(
            f"{sha256_bytes(data)}  {path}\n"
            for path, data in sorted(checksum_inputs.items())
        ).encode("ascii")
        bundle_files = {
            **payload,
            "manifest.json": manifest_data,
            "SHA256SUMS": checksums_data,
        }
        archive_data = canonical_tar_gz(root_name, bundle_files)

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary_target = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(archive_data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_target, target)
        except FileExistsError as exc:
            raise RuntimeError(f"release bundle already exists: {target.name}") from exc
        directory_descriptor = os.open(
            target.parent,
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
        temporary_target.unlink(missing_ok=True)
    return {
        "bundle": target.name,
        "bundle_sha256": sha256_bytes(archive_data),
        "file_count": len(bundle_files),
        "git_commit": commit,
        "ok": True,
        "schema": SCHEMA,
        "version": version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Explicit external or gitignored directory for the release archive.",
    )
    args = parser.parse_args()
    try:
        result = build_release(args.output_dir)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(
            json.dumps(
                {"error": str(exc), "ok": False},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
