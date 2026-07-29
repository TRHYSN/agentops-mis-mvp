#!/usr/bin/env python3
"""Verify deterministic, offline Relay release bundle construction."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_relay_release.py"
SCHEMA = "agentops.relay.release-bundle.v1"
SECRET_PATTERNS = (
    re.compile(rb"ntn_[A-Za-z0-9_-]{16,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{16,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_-]{16,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_-]{16,}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def write_guard(path: Path) -> None:
    path.mkdir()
    (path / "sitecustomize.py").write_text(
        """
import os
import socket
import subprocess
import urllib.request

def _blocked(*args, **kwargs):
    raise RuntimeError("release smoke blocked network or system-install behavior")

socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
urllib.request.urlopen = _blocked
os.system = _blocked

_real_popen = subprocess.Popen
class _GuardedPopen(_real_popen):
    def __init__(self, args, *positional, **keyword):
        command = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
        if os.path.basename(str(command)) != "git":
            raise RuntimeError("release builder subprocess is not allowlisted")
        super().__init__(args, *positional, **keyword)

subprocess.Popen = _GuardedPopen
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
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(guard),
        "TMPDIR": str(temp_dir),
        "XDG_CACHE_HOME": str(cache),
    }


def run_builder(output_dir: Path, env: dict[str, str]) -> tuple[dict, bytes]:
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--output-dir", str(output_dir)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"builder failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    archive = output_dir / payload["bundle"]
    if not archive.is_file():
        raise RuntimeError("builder did not create its declared archive")
    return payload, archive.read_bytes()


def archive_files(bundle: bytes) -> tuple[str, dict[str, bytes], list[tarfile.TarInfo]]:
    if len(bundle) < 10 or bundle[4:8] != b"\x00\x00\x00\x00":
        raise RuntimeError("gzip header does not have normalized mtime")
    if bundle[3] & 0x08:
        raise RuntimeError("gzip header retained an original filename")
    with gzip.GzipFile(fileobj=io.BytesIO(bundle), mode="rb") as compressed:
        tar_data = compressed.read()
    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as archive:
        members = archive.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise RuntimeError("bundle does not have exactly one archive root")
        root = roots.pop()
        files = {
            PurePosixPath(member.name).relative_to(root).as_posix(): archive.extractfile(member).read()
            for member in members
            if member.isfile()
        }
    return root, files, members


def parse_checksums(value: bytes) -> dict[str, str]:
    result = {}
    for line in value.decode("ascii").splitlines():
        digest, separator, path = line.partition("  ")
        if not separator or path in result:
            raise RuntimeError("invalid SHA256SUMS entry")
        result[path] = digest
    return result


def expanded_payload(files: dict[str, bytes]) -> bytes:
    content = bytearray()
    for path, data in sorted(files.items()):
        content.extend(path.encode("utf-8"))
        content.extend(b"\n")
        content.extend(data)
        content.extend(b"\n")
        if path.endswith(".whl"):
            with zipfile.ZipFile(io.BytesIO(data), "r") as wheel:
                for name in sorted(wheel.namelist()):
                    content.extend(name.encode("utf-8"))
                    content.extend(b"\n")
                    content.extend(wheel.read(name))
                    content.extend(b"\n")
    return bytes(content)


def main() -> int:
    failures: list[str] = []
    status_before = git_output("status", "--porcelain=v1", "--untracked-files=all")
    commit = git_output("rev-parse", "HEAD").strip()

    with tempfile.TemporaryDirectory(prefix="relay-release-smoke-") as temporary_name:
        temporary = Path(temporary_name)
        guard = temporary / "guard"
        write_guard(guard)
        env = isolated_env(temporary, guard)
        excludes = temporary / "git-excludes"
        ignored_canary = ROOT / "agentops_mis_cli" / "relay_release_ignored_canary.py"
        excludes.write_text(
            f"/{ignored_canary.relative_to(ROOT).as_posix()}\n",
            encoding="utf-8",
        )
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.excludesFile",
                "GIT_CONFIG_VALUE_0": str(excludes),
            }
        )

        guard_probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import socket, subprocess\n"
                    "blocked = 0\n"
                    "try:\n"
                    "    socket.create_connection(('127.0.0.1', 9), timeout=0.01)\n"
                    "except RuntimeError:\n"
                    "    blocked += 1\n"
                    "try:\n"
                    "    subprocess.run(['pip', '--version'], check=False)\n"
                    "except RuntimeError:\n"
                    "    blocked += 1\n"
                    "raise SystemExit(0 if blocked == 2 else 1)\n"
                ),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        guard_enforced = guard_probe.returncode == 0
        require(guard_enforced, "network/system-install guard was not enforced", failures)

        missing_arg = subprocess.run(
            [sys.executable, str(BUILDER)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        require(
            missing_arg.returncode != 0 and "--output-dir" in missing_arg.stderr,
            "builder did not require explicit --output-dir",
            failures,
        )

        unignored_output = ROOT / "RELAY_RELEASE_BUNDLE_SHOULD_NOT_EXIST"
        require(not unignored_output.exists(), "unignored output fixture already exists", failures)
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", unignored_output.name],
            cwd=ROOT,
            check=False,
        )
        require(ignored.returncode == 1, "unignored output fixture is unexpectedly ignored", failures)
        rejected = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--output-dir",
                str(unignored_output),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        require(rejected.returncode != 0, "unignored repository output was accepted", failures)
        require(not unignored_output.exists(), "rejected repository output was created", failures)

        dist_rejected = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--output-dir",
                str(ROOT / "dist" / "relay-release-smoke"),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        require(dist_rejected.returncode != 0, "repository dist output was accepted", failures)

        ignored_canary_recognized = False
        try:
            ignored_canary.write_text(
                'RELAY_RELEASE_LIVE_ONLY_CANARY = "must-not-ship"\n',
                encoding="utf-8",
            )
            ignored_canary_recognized = (
                subprocess.run(
                    ["git", "check-ignore", "--quiet", "--", str(ignored_canary)],
                    cwd=ROOT,
                    env=env,
                    check=False,
                ).returncode
                == 0
            )
            result_one, bundle_one = run_builder(temporary / "one", env)
            result_two, bundle_two = run_builder(temporary / "two", env)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(str(exc))
            result_one = result_two = {}
            bundle_one = bundle_two = b""
        finally:
            ignored_canary.unlink(missing_ok=True)
        require(
            ignored_canary_recognized,
            "ignored live-source canary was not covered by the test fixture",
            failures,
        )

        require(bundle_one == bundle_two and bool(bundle_one), "two builds were not byte-identical", failures)
        require(
            result_one.get("bundle_sha256") == result_two.get("bundle_sha256") == sha256(bundle_one),
            "reported bundle hashes were not deterministic",
            failures,
        )
        require(result_one.get("git_commit") == commit, "builder provenance commit mismatch", failures)
        require(result_one.get("schema") == SCHEMA, "builder schema mismatch", failures)
        first_archive = temporary / "one" / str(result_one.get("bundle") or "")
        first_archive_before = first_archive.read_bytes() if first_archive.is_file() else b""
        duplicate = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--output-dir",
                str(temporary / "one"),
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        require(
            duplicate.returncode != 0
            and first_archive.is_file()
            and first_archive.read_bytes() == first_archive_before,
            "existing release archive was overwritten or changed",
            failures,
        )
        require(
            first_archive.is_file()
            and stat.S_IMODE(first_archive.stat().st_mode) == 0o644,
            "release archive mode is not 0644",
            failures,
        )
        require(
            not list((temporary / "one").glob(".*.tmp")),
            "atomic release temporary file was retained",
            failures,
        )

        files: dict[str, bytes] = {}
        members: list[tarfile.TarInfo] = []
        root_name = ""
        if bundle_one:
            try:
                root_name, files, members = archive_files(bundle_one)
            except (OSError, RuntimeError, tarfile.TarError, gzip.BadGzipFile) as exc:
                failures.append(f"archive inspection failed: {exc}")

        wheel_paths = sorted(path for path in files if path.startswith("wheel/") and path.endswith(".whl"))
        expected_paths = {
            "SHA256SUMS",
            "config/config.example.json",
            "manifest.json",
            "systemd/agentops-mis-relay.service",
            *wheel_paths,
        }
        require(len(wheel_paths) == 1, "bundle must contain exactly one wheel", failures)
        ignored_canary_absent = False
        if len(wheel_paths) == 1:
            try:
                with zipfile.ZipFile(io.BytesIO(files[wheel_paths[0]]), "r") as wheel:
                    ignored_canary_absent = not any(
                        name.endswith("/relay_release_ignored_canary.py")
                        or name == "agentops_mis_cli/relay_release_ignored_canary.py"
                        for name in wheel.namelist()
                    )
            except zipfile.BadZipFile:
                pass
        require(
            ignored_canary_absent,
            "ignored live-source Python file entered the committed release wheel",
            failures,
        )
        require(set(files) == expected_paths, f"unexpected bundle members: {sorted(files)}", failures)
        require(
            root_name == f"agentops-mis-relay-{result_one.get('version', '')}",
            "archive root does not match release version",
            failures,
        )
        for member in members:
            require(member.uid == 0 and member.gid == 0, f"non-normalized owner: {member.name}", failures)
            require(member.uname == "" and member.gname == "", f"named owner retained: {member.name}", failures)
            require(member.mtime == 0, f"non-normalized mtime: {member.name}", failures)
            require(not member.issym() and not member.islnk(), f"link member is not allowed: {member.name}", failures)
            expected_mode = 0o755 if member.isdir() else 0o644
            require(
                stat.S_IMODE(member.mode) == expected_mode,
                f"non-normalized mode: {member.name}",
                failures,
            )

        manifest = {}
        if "manifest.json" in files:
            try:
                manifest = json.loads(files["manifest.json"])
            except json.JSONDecodeError as exc:
                failures.append(f"manifest is invalid JSON: {exc}")
        require(
            set(manifest) == {"files", "git_commit", "schema", "version"},
            f"manifest has unexpected fields: {sorted(manifest)}",
            failures,
        )
        require(manifest.get("schema") == SCHEMA, "manifest schema mismatch", failures)
        require(manifest.get("git_commit") == commit, "manifest commit mismatch", failures)
        require(manifest.get("version") == result_one.get("version"), "manifest version mismatch", failures)

        manifest_rows = manifest.get("files", [])
        manifest_records = {
            row.get("path"): row
            for row in manifest_rows
            if isinstance(row, dict) and isinstance(row.get("path"), str)
        }
        payload_paths = expected_paths - {"SHA256SUMS", "manifest.json"}
        require(
            isinstance(manifest_rows, list)
            and len(manifest_rows) == len(manifest_records),
            "manifest contains duplicate or malformed file records",
            failures,
        )
        require(set(manifest_records) == payload_paths, "manifest payload file set mismatch", failures)
        for path in sorted(payload_paths):
            record = manifest_records.get(path) or {}
            data = files.get(path, b"")
            require(record.get("sha256") == sha256(data), f"manifest hash mismatch: {path}", failures)
            require(record.get("size") == len(data), f"manifest size mismatch: {path}", failures)

        checksum_records = {}
        if "SHA256SUMS" in files:
            try:
                checksum_records = parse_checksums(files["SHA256SUMS"])
            except (UnicodeDecodeError, RuntimeError) as exc:
                failures.append(str(exc))
        checksum_paths = expected_paths - {"SHA256SUMS"}
        require(set(checksum_records) == checksum_paths, "SHA256SUMS file set mismatch", failures)
        for path in sorted(checksum_paths):
            require(
                checksum_records.get(path) == sha256(files.get(path, b"")),
                f"SHA256SUMS mismatch: {path}",
                failures,
            )

        manifest_bytes = files.get("manifest.json", b"").lower()
        for forbidden in (
            b"/users/",
            b"127.0.0.1",
            b"localhost",
            b"token",
            b"endpoint",
            b"database",
            b"sqlite",
            b".db",
        ):
            require(forbidden not in manifest_bytes, f"manifest retained forbidden value: {forbidden!r}", failures)

        expanded = expanded_payload(files) if files else b""
        local_markers = {
            str(ROOT).encode("utf-8"),
            str(Path.home()).encode("utf-8"),
            b"/Users/wuji/",
        }
        for marker in local_markers:
            require(marker not in expanded, f"bundle retained local path: {marker!r}", failures)
        for pattern in SECRET_PATTERNS:
            require(pattern.search(expanded) is None, f"bundle matched secret pattern: {pattern.pattern!r}", failures)

    status_after = git_output("status", "--porcelain=v1", "--untracked-files=all")
    require(status_after == status_before, "smoke changed repository status", failures)
    require(not (ROOT / "RELAY_RELEASE_BUNDLE_SHOULD_NOT_EXIST").exists(), "safety fixture leaked", failures)

    report = {
        "bundle_byte_reproducible": bundle_one == bundle_two and bool(bundle_one),
        "bundle_sha256": sha256(bundle_one) if bundle_one else None,
        "explicit_output_required": True,
        "git_commit": commit,
        "manifest_schema": SCHEMA,
        "network_and_installer_subprocesses_blocked": guard_enforced,
        "repository_status_unchanged": status_after == status_before,
        "failures": failures,
        "ok": not failures,
    }
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
