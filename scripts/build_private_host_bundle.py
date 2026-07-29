#!/usr/bin/env python3
"""Build versioned private-host archives from tracked source and a built UI."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UI_DIST = ROOT / "ui" / "start-building-app" / "dist"
RUNTIME_FILES = {
    "AGENT_WORKFLOW.md",
    "BASE_INDEX.md",
    "LICENSE",
    "PROJECT_SPEC.md",
    "README.md",
    "docs/AGENT_WORK_METHOD_BLOCK.md",
    "docs/PRIVATE_HOST_OPERATOR_RUNBOOK.md",
    "docs/PRIVATE_HOST_WORKER_SERVICE_ACCEPTANCE.md",
    "docs/RELEASE_PROVENANCE.md",
    "docs/REMOTE_WORKER_OPERATIONS_RUNBOOK.md",
    "docs/SBOM_MINIMAL.md",
    "docs/THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "packaging/macos/launcher.py",
    "server.py",
    "scripts/agent_worker.py",
    "scripts/agentops_local_backup.py",
    "scripts/agentops",
    "scripts/customer_worker_real_runtime_acceptance.py",
    "scripts/hermes_openclaw_loop.py",
    "scripts/local_runtime_acceptance.py",
    "scripts/run_kb_bot_demo.py",
    "scripts/run_local_stack.py",
    "scripts/v1_5_live_product_readiness_smoke.py",
    "scripts/worker_adapter_readiness_smoke.py",
}
RUNTIME_PREFIXES = (
    "agentops_mis_cli/",
    "agentops_mis_core/",
    "agentops_mis_runtime/",
    "config/",
    "knowledge/",
    "static/",
)
FORBIDDEN_PARTS = {
    ".git",
    ".agentops_runtime",
    ".env",
    "__pycache__",
    "artifacts",
    "cache",
    "caches",
    "logs",
    "node_modules",
}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo"}


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def tracked_files() -> list[str]:
    return [line for line in run_git("ls-files", "-z").split("\0") if line]


def source_timestamp() -> str:
    return run_git("show", "-s", "--format=%cI", "HEAD")


def require_clean_tracked_source() -> None:
    unstaged = subprocess.run(["git", "diff", "--quiet", "--"], cwd=ROOT, check=False)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--"], cwd=ROOT, check=False)
    if unstaged.returncode not in {0, 1} or staged.returncode not in {0, 1}:
        raise RuntimeError("unable to verify release source state")
    if unstaged.returncode or staged.returncode:
        raise RuntimeError("tracked release source is dirty; commit or restore tracked changes before building")


def safe_relative(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe bundle path: {path}")
    return rel


def is_forbidden(path: PurePosixPath) -> bool:
    lowered = tuple(part.lower() for part in path.parts)
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in lowered):
        return True
    return path.suffix.lower() in FORBIDDEN_SUFFIXES


def source_selection() -> list[str]:
    selected = []
    for path in tracked_files():
        rel = safe_relative(path)
        if is_forbidden(rel):
            continue
        if path in RUNTIME_FILES or path.startswith(RUNTIME_PREFIXES):
            selected.append(path)
    missing = sorted(RUNTIME_FILES - set(selected))
    if missing:
        raise RuntimeError("required tracked runtime files missing: " + ", ".join(missing))
    return sorted(selected)


def copy_file(source: Path, target: Path, executable: bool = False, *, source_root: Path = ROOT) -> None:
    resolved_source = source.resolve(strict=True)
    resolved_root = source_root.resolve(strict=True)
    if source.is_symlink() or resolved_root not in resolved_source.parents:
        raise RuntimeError(f"release source escapes its declared root: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved_source, target)
    mode = resolved_source.stat().st_mode
    target.chmod((mode & 0o777) | (stat.S_IXUSR if executable else 0))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def records_sha256(records: list[dict]) -> str:
    payload = json.dumps(records, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_version(value: str) -> str:
    value = value.strip().removeprefix("v")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not value or any(char not in allowed for char in value):
        raise ValueError("version may contain only letters, digits, dot, underscore, and hyphen")
    return value


def zip_tree(source: Path, destination: Path, root_name: str) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(str(PurePosixPath(root_name) / path.relative_to(source).as_posix()))
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())


def tar_tree(source: Path, destination: Path, root_name: str) -> None:
    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        return info

    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                archive.add(source, arcname=root_name, recursive=True, filter=normalize)


def build(output_dir: Path, ui_dist: Path, version: str) -> dict:
    require_clean_tracked_source()
    if not (ui_dist / "index.html").is_file():
        raise RuntimeError(f"built production UI missing: {ui_dist / 'index.html'}")
    output_dir.mkdir(parents=True, exist_ok=True)
    commit = run_git("rev-parse", "HEAD")
    bundle_name = f"agentops-mis-private-host-{version}"

    with tempfile.TemporaryDirectory(prefix="agentops-private-host-build-") as temporary:
        root = Path(temporary) / bundle_name
        payload = root / "payload"
        ui_records = []
        for rel_name in source_selection():
            copy_file(ROOT / rel_name, payload / rel_name, executable=rel_name == "scripts/agentops")
        for source in sorted(ui_dist.rglob("*")):
            if source.is_file():
                rel = safe_relative(source.relative_to(ui_dist).as_posix())
                if is_forbidden(rel):
                    raise RuntimeError(f"forbidden file in UI dist: {rel}")
                copy_file(source, payload / "ui" / "start-building-app" / "dist" / rel.as_posix(), source_root=ui_dist)
                ui_records.append({"path": rel.as_posix(), "sha256": sha256(source), "size": source.stat().st_size})
        copy_file(ROOT / "packaging" / "macos" / "install.sh", root / "install.sh", executable=True)
        copy_file(ROOT / "packaging" / "macos" / "uninstall.sh", root / "uninstall.sh", executable=True)

        file_records = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "manifest.json":
                rel = path.relative_to(root).as_posix()
                if is_forbidden(safe_relative(rel)):
                    raise RuntimeError(f"forbidden file selected for bundle: {rel}")
                file_records.append({"path": rel, "sha256": sha256(path), "size": path.stat().st_size})
        manifest = {
            "schema_version": 1,
            "product": "AgentOps MIS Private Host",
            "version": version,
            "git_commit": commit,
            "built_at": source_timestamp(),
            "platform": "macOS",
            "python_requires": ">=3.10",
            "ui_source": "prebuilt_dist",
            "ui_tree_sha256": records_sha256(ui_records),
            "file_count": len(file_records),
            "files": file_records,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        tar_path = output_dir / f"{bundle_name}.tar.gz"
        zip_path = output_dir / f"{bundle_name}.zip"
        tar_tree(root, tar_path, bundle_name)
        zip_tree(root, zip_path, bundle_name)

    bootstrap_path = output_dir / "install-agentops-mis-private-host.sh"
    copy_file(ROOT / "packaging" / "macos" / "install-private-host.sh", bootstrap_path, executable=True)
    artifact_checksums = {path.name: sha256(path) for path in (tar_path, zip_path, bootstrap_path)}
    provenance_path = output_dir / f"{bundle_name}.provenance.json"
    provenance = {
        "schema_version": 1,
        "product": "AgentOps MIS Private Host",
        "version": version,
        "git_commit": commit,
        "source_timestamp": source_timestamp(),
        "source_clean": True,
        "ui_tree_sha256": manifest["ui_tree_sha256"],
        "bundle_file_count": manifest["file_count"],
        "artifacts": artifact_checksums,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums = {**artifact_checksums, provenance_path.name: sha256(provenance_path)}
    checksum_path = output_dir / f"{bundle_name}.sha256.json"
    checksum_path.write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "version": version,
        "git_commit": commit,
        "artifacts": [str(tar_path), str(zip_path), str(bootstrap_path), str(provenance_path)],
        "checksums": str(checksum_path),
        "provenance": str(provenance_path),
        "file_count": manifest["file_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build" / "private-host")
    parser.add_argument("--ui-dist", type=Path, default=DEFAULT_UI_DIST)
    parser.add_argument("--version", default=os.environ.get("AGENTOPS_BUNDLE_VERSION") or run_git("describe", "--tags", "--always"))
    args = parser.parse_args()
    result = build(args.output_dir.expanduser().resolve(), args.ui_dist.expanduser().resolve(), normalize_version(args.version))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
