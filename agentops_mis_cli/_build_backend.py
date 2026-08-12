"""Tiny offline PEP 517 build backend for AgentOps MIS distributions.

The base CLI intentionally avoids runtime dependencies. Reliability Lab is
shipped in the same wheel but exposes its third-party runtime requirements as
the explicit ``reliability`` extra.  The backend lets ``pip install .`` build a
pure-Python wheel without fetching setuptools/wheel as a build dependency.
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import os
import re
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path


PROJECT = "agentops-mis-cli"
DIST = "agentops_mis_cli"
VERSION = "0.1.0"
DIST_INFO = f"{DIST}-{VERSION}.dist-info"
ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [
    ROOT / "agentops_mis_cli",
    ROOT / "agentops_mis_core",
    ROOT / "agentops_mis_runtime",
    ROOT / "open_cekura",
]
ROOT_MODULES = [ROOT / "server.py"]
OPEN_CEKURA_EXAMPLES = ROOT / "examples" / "open-cekura"
OPEN_CEKURA_RESOURCE_PREFIX = Path("open_cekura") / "resources"
OPEN_CEKURA_BUILD_COMMIT = Path("open_cekura") / "_build_commit.txt"
OPEN_CEKURA_RUNTIME_CONTRACT = (
    ROOT / "open_cekura" / "contracts" / "RELIABILITY_CAMPAIGN_EXECUTION.md"
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RELAY_DEPLOYMENT_FILES = [
    ROOT / "packaging" / "relay" / "config.example.json",
    ROOT / "packaging" / "relay" / "systemd" / "agentops-mis-relay.service",
    ROOT / "docs" / "LOCAL_RELAY_DEPLOY_CONTRACT_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_RELEASE_BUNDLE_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_OFFLINE_INSTALL_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_OFFLINE_STATUS_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_PLAN_CORE_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_CONTROLLER_SUCCESS_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_EVIDENCE_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_JOURNAL_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_JOURNAL_STATUS_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_PRODUCTION_STORE_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_RECOVERY_SNAPSHOT_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_PREVIEW_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_LINUX_SYSTEMD_RECOVERY_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_SYSTEMD_MUTATION_ADAPTER_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_CONFIG_PARSER_ACCEPTANCE.md",
    ROOT / "docs" / "RELAY_SERVICE_ACTIVATION_SPEC.md",
]
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ARCHIVE_MODE = 0o644
PORTABLE_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".service",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _metadata() -> str:
    return "\n".join([
        "Metadata-Version: 2.2",
        f"Name: {PROJECT}",
        f"Version: {VERSION}",
        "Summary: Installable AgentOps MIS Agent Gateway CLI wrapper.",
        "Requires-Python: >=3.10",
        "License: Proprietary local MVP",
        "Provides-Extra: reliability",
        'Requires-Dist: pydantic>=2.8,<3; extra == "reliability"',
        'Requires-Dist: PyYAML>=6.0,<7; extra == "reliability"',
        "",
    ])


def _wheel() -> str:
    return "\n".join([
        "Wheel-Version: 1.0",
        "Generator: agentops-mis-cli offline backend",
        "Root-Is-Purelib: true",
        "Tag: py3-none-any",
        "",
    ])


def _entry_points() -> str:
    return "\n".join([
        "[console_scripts]",
        "agentops = agentops_mis_cli.cli:main",
        "agentops-relay = agentops_mis_cli.relay_daemon:main",
        "agentops-relayctl = agentops_mis_cli.relay_admin:main",
        "agentops-worker = agentops_mis_cli.worker:main",
        "",
    ])


def _hash(data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}", str(len(data))


def _production_python_files() -> list[Path]:
    files: list[Path] = []
    for package in PACKAGES:
        for path in sorted(
            package.rglob("*.py"),
            key=lambda candidate: candidate.relative_to(ROOT).as_posix(),
        ):
            relative = path.relative_to(package)
            if "__pycache__" in relative.parts or "tests" in relative.parts:
                continue
            files.append(path)
    return files


def _portable_source_bytes(path: Path) -> bytes:
    """Return host-independent bytes for textual distribution inputs."""

    data = path.read_bytes()
    if path.suffix.lower() in PORTABLE_TEXT_SUFFIXES:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _source_commit_sha() -> str:
    for raw in (
        os.environ.get("AGENTOPS_BUILD_COMMIT_SHA"),
        _read_bundled_commit(),
        _git_commit_sha(),
    ):
        value = str(raw or "").strip().lower()
        if _COMMIT_SHA.fullmatch(value):
            return value
    raise RuntimeError(
        "cannot determine a 40-character source commit; set "
        "AGENTOPS_BUILD_COMMIT_SHA when building outside a Git checkout"
    )


def _read_bundled_commit() -> str | None:
    path = ROOT / OPEN_CEKURA_BUILD_COMMIT
    try:
        return path.read_text(encoding="ascii")
    except OSError:
        return None


def _git_commit_sha() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _build_commit_file() -> tuple[str, bytes]:
    return OPEN_CEKURA_BUILD_COMMIT.as_posix(), (
        f"{_source_commit_sha()}\n".encode("ascii")
    )


def _demo_resource_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(
        OPEN_CEKURA_EXAMPLES.rglob("*"),
        key=lambda candidate: candidate.relative_to(OPEN_CEKURA_EXAMPLES).as_posix(),
    ):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(OPEN_CEKURA_EXAMPLES)
        target = (OPEN_CEKURA_RESOURCE_PREFIX / relative).as_posix()
        files.append((target, _portable_source_bytes(path)))
    return files


def _package_files() -> list[tuple[str, bytes]]:
    files = [
        (path.relative_to(ROOT).as_posix(), _portable_source_bytes(path))
        for path in _production_python_files()
    ]
    files.extend(
        (path.relative_to(ROOT).as_posix(), _portable_source_bytes(path))
        for path in ROOT_MODULES
    )
    files.append(
        (
            OPEN_CEKURA_RUNTIME_CONTRACT.relative_to(ROOT).as_posix(),
            _portable_source_bytes(OPEN_CEKURA_RUNTIME_CONTRACT),
        )
    )
    files.extend(_demo_resource_files())
    files.append(_build_commit_file())
    return files


def _default_metadata_files() -> list[tuple[str, bytes]]:
    return sorted([
        (f"{DIST_INFO}/METADATA", _metadata().encode("utf-8")),
        (f"{DIST_INFO}/WHEEL", _wheel().encode("utf-8")),
        (f"{DIST_INFO}/entry_points.txt", _entry_points().encode("utf-8")),
    ])


def _prepared_metadata_files(metadata_directory: str) -> list[tuple[str, bytes]]:
    root = Path(metadata_directory)
    dist_info = root if root.name == DIST_INFO else root / DIST_INFO
    if not dist_info.is_dir() or dist_info.is_symlink():
        raise ValueError("prepared metadata directory is invalid")
    files: list[tuple[str, bytes]] = []
    # ``WindowsPath`` ordering is case-insensitive while wheel member ordering
    # is byte-sensitive.  Sort the portable archive name explicitly so a
    # PEP 517 build from an sdist matches a direct build on every host OS.
    for path in sorted(
        dist_info.rglob("*"),
        key=lambda candidate: candidate.relative_to(dist_info).as_posix(),
    ):
        if path.is_symlink():
            raise ValueError("prepared metadata entry is invalid")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("prepared metadata entry is invalid")
        relative = path.relative_to(dist_info).as_posix()
        if relative == "RECORD":
            raise ValueError("prepared metadata must not contain RECORD")
        files.append((f"{DIST_INFO}/{relative}", path.read_bytes()))
    required = {
        f"{DIST_INFO}/METADATA",
        f"{DIST_INFO}/WHEEL",
        f"{DIST_INFO}/entry_points.txt",
    }
    if not required.issubset(name for name, _data in files):
        raise ValueError("prepared metadata is incomplete")
    return files


def _wheel_files(metadata_directory: str | None = None) -> list[tuple[str, bytes]]:
    metadata = (
        _prepared_metadata_files(metadata_directory)
        if metadata_directory is not None
        else _default_metadata_files()
    )
    return [*_package_files(), *metadata]


def _record(rows: list[tuple[str, bytes]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for name, data in rows:
        writer.writerow([name, *_hash(data)])
    writer.writerow([f"{DIST_INFO}/RECORD", "", ""])
    return out.getvalue().encode("utf-8")


def _write_wheel_file(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | ARCHIVE_MODE) << 16
    zf.writestr(info, data)


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None) -> str:
    rows = _wheel_files(metadata_directory)
    wheel_name = f"{DIST}-{VERSION}-py3-none-any.whl"
    target = Path(wheel_directory) / wheel_name
    with zipfile.ZipFile(target, "w") as zf:
        for name, data in rows:
            _write_wheel_file(zf, name, data)
        _write_wheel_file(zf, f"{DIST_INFO}/RECORD", _record(rows))
    return wheel_name


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None) -> str:
    dist_info = Path(metadata_directory) / DIST_INFO
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_bytes(_metadata().encode("utf-8"))
    (dist_info / "WHEEL").write_bytes(_wheel().encode("utf-8"))
    (dist_info / "entry_points.txt").write_bytes(_entry_points().encode("utf-8"))
    return DIST_INFO


def build_sdist(sdist_directory: str, config_settings=None) -> str:
    sdist_name = f"{DIST}-{VERSION}.tar.gz"
    target = Path(sdist_directory) / sdist_name
    prefix = f"{DIST}-{VERSION}"
    include = [
        ROOT / "pyproject.toml",
        *_production_python_files(),
        *ROOT_MODULES,
        *(
            path
            for path in sorted(
                OPEN_CEKURA_EXAMPLES.rglob("*"),
                key=lambda candidate: candidate.relative_to(
                    OPEN_CEKURA_EXAMPLES
                ).as_posix(),
            )
            if path.is_file()
        ),
        *RELAY_DEPLOYMENT_FILES,
        OPEN_CEKURA_RUNTIME_CONTRACT,
        ROOT / "README.md",
    ]
    archive_files = [
        (
            f"{prefix}/{path.relative_to(ROOT).as_posix()}",
            _portable_source_bytes(path),
        )
        for path in include
    ]
    build_commit_name, build_commit_data = _build_commit_file()
    archive_files.append((f"{prefix}/{build_commit_name}", build_commit_data))
    archive_files.append((f"{prefix}/PKG-INFO", _metadata().encode("utf-8")))
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tf:
                for name, data in archive_files:
                    info = tarfile.TarInfo(name)
                    info.mode = ARCHIVE_MODE
                    info.mtime = 0
                    info.size = len(data)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    tf.addfile(info, io.BytesIO(data))
    return sdist_name
