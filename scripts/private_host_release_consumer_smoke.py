#!/usr/bin/env python3
"""Verify the no-repository Private Host release consumer path offline."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_private_host_bundle.py"
TEST_STORAGE_FREE_BYTES = 8 * 1024 * 1024 * 1024


def run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def require(condition: bool, message: str, process: subprocess.CompletedProcess | None = None) -> None:
    if condition:
        return
    detail = ""
    if process is not None:
        detail = (
            f" (exit={process.returncode}, stdout_omitted=true, stderr_omitted=true, "
            f"stdout_bytes={len(process.stdout)}, stderr_bytes={len(process.stderr)})"
        )
    raise RuntimeError(message + detail)


def deterministic_storage_fixture(install_root: Path, host_home: Path, bin_dir: Path, app_dir: Path) -> str:
    paths = {
        "install": [install_root],
        "data": [host_home.parent, host_home / "backups"],
        "bin": [bin_dir],
        "app": [app_dir],
    }
    return json.dumps({
        "schema_version": 1,
        "paths": [
            {
                "path": str(path.absolute()),
                "device_id": f"consumer-{role}-device",
                "free_bytes": TEST_STORAGE_FREE_BYTES,
                "storage_role": role,
            }
            for role, role_paths in paths.items()
            for path in role_paths
        ],
    }, sort_keys=True)


def main() -> int:
    require((ROOT / "ui" / "start-building-app" / "dist" / "index.html").is_file(), "production UI dist is required")
    with tempfile.TemporaryDirectory(prefix="agentops-release-consumer-") as temporary:
        temp = Path(temporary)
        output = temp / "release"
        version = "0.0.0-consumer-smoke"
        builder_home = temp / "builder-home"
        builder_tmp = temp / "tmp"
        builder_home.mkdir()
        builder_tmp.mkdir()
        base_env = {
            "HOME": str(builder_home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMPDIR": str(builder_tmp),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        built = run([sys.executable, str(BUILDER), "--output-dir", str(output), "--version", version], env=base_env)
        require(built.returncode == 0, "release fixture build failed", built)
        payload = json.loads(built.stdout)
        bootstrap = output / "install-agentops-mis-private-host.sh"
        checksums_path = Path(payload["checksums"])
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        provenance_path = Path(payload["provenance"])
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        require(bootstrap in [Path(path) for path in payload["artifacts"]], "bootstrap installer missing from release artifacts")
        require(checksums.get(bootstrap.name) == digest(bootstrap), "bootstrap installer checksum missing or invalid")
        require(checksums.get(provenance_path.name) == digest(provenance_path), "provenance checksum missing or invalid")
        require(provenance.get("git_commit") == payload.get("git_commit"), "provenance source commit mismatch")

        archive_name = f"agentops-mis-private-host-{version}.tar.gz"
        tampered_release = temp / "tampered-release"
        shutil.copytree(output, tampered_release)
        with (tampered_release / archive_name).open("ab") as handle:
            handle.write(b"tampered")
        tampered_home = temp / "tampered-home"
        tampered_env = {
            **base_env,
            "HOME": str(tampered_home),
            "AGENTOPS_INSTALL_ROOT": str(tampered_home / ".local" / "share" / "agentops-mis"),
            "AGENTOPS_BIN_DIR": str(tampered_home / ".local" / "bin"),
            "AGENTOPS_APP_DIR": str(tampered_home / "Applications"),
            "AGENTOPS_HOST_HOME": str(tampered_home / ".agentops" / "host"),
            "AGENTOPS_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_INSTALLER_TEST_RELEASE_DIR": str(tampered_release),
            "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
        }
        tampered = run(["sh", str(bootstrap), "--tag", f"v{version}"], env=tampered_env)
        require(tampered.returncode != 0, "release consumer accepted a checksum mismatch", tampered)
        require(not Path(tampered_env["AGENTOPS_INSTALL_ROOT"]).exists(), "checksum mismatch wrote an install tree")

        unsafe_release = temp / "unsafe-release"
        shutil.copytree(output, unsafe_release)
        unsafe_archive = unsafe_release / archive_name
        with tarfile.open(unsafe_archive, "w:gz") as archive:
            member = tarfile.TarInfo(f"agentops-mis-private-host-{version}/../../escaped")
            content = b"unsafe"
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        unsafe_checksums_path = unsafe_release / f"agentops-mis-private-host-{version}.sha256.json"
        unsafe_checksums = json.loads(unsafe_checksums_path.read_text(encoding="utf-8"))
        unsafe_provenance_path = unsafe_release / f"agentops-mis-private-host-{version}.provenance.json"
        unsafe_provenance = json.loads(unsafe_provenance_path.read_text(encoding="utf-8"))
        unsafe_provenance["artifacts"][archive_name] = digest(unsafe_archive)
        unsafe_provenance_path.write_text(json.dumps(unsafe_provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        unsafe_checksums[archive_name] = digest(unsafe_archive)
        unsafe_checksums[unsafe_provenance_path.name] = digest(unsafe_provenance_path)
        unsafe_checksums_path.write_text(json.dumps(unsafe_checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        unsafe_home = temp / "unsafe-home"
        unsafe_env = {
            **base_env,
            "HOME": str(unsafe_home),
            "AGENTOPS_INSTALL_ROOT": str(unsafe_home / ".local" / "share" / "agentops-mis"),
            "AGENTOPS_BIN_DIR": str(unsafe_home / ".local" / "bin"),
            "AGENTOPS_APP_DIR": str(unsafe_home / "Applications"),
            "AGENTOPS_HOST_HOME": str(unsafe_home / ".agentops" / "host"),
            "AGENTOPS_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_INSTALLER_TEST_RELEASE_DIR": str(unsafe_release),
            "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
        }
        unsafe = run(["sh", str(bootstrap), "--tag", f"v{version}"], env=unsafe_env)
        require(unsafe.returncode != 0, "release consumer accepted an unsafe archive member", unsafe)
        require(not Path(unsafe_env["AGENTOPS_INSTALL_ROOT"]).exists(), "unsafe archive wrote an install tree")
        require(not (temp / "escaped").exists(), "unsafe archive escaped the extraction root")

        invalid_provenance_release = temp / "invalid-provenance-release"
        shutil.copytree(output, invalid_provenance_release)
        invalid_provenance_path = invalid_provenance_release / provenance_path.name
        invalid_provenance = json.loads(invalid_provenance_path.read_text(encoding="utf-8"))
        invalid_provenance["source_clean"] = False
        invalid_provenance_path.write_text(json.dumps(invalid_provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        invalid_checksums_path = invalid_provenance_release / checksums_path.name
        invalid_checksums = json.loads(invalid_checksums_path.read_text(encoding="utf-8"))
        invalid_checksums[invalid_provenance_path.name] = digest(invalid_provenance_path)
        invalid_checksums_path.write_text(json.dumps(invalid_checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        invalid_home = temp / "invalid-provenance-home"
        invalid_env = {
            **base_env,
            "HOME": str(invalid_home),
            "AGENTOPS_INSTALL_ROOT": str(invalid_home / ".local" / "share" / "agentops-mis"),
            "AGENTOPS_BIN_DIR": str(invalid_home / ".local" / "bin"),
            "AGENTOPS_APP_DIR": str(invalid_home / "Applications"),
            "AGENTOPS_HOST_HOME": str(invalid_home / ".agentops" / "host"),
            "AGENTOPS_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_INSTALLER_TEST_RELEASE_DIR": str(invalid_provenance_release),
            "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
        }
        invalid_result = run(["sh", str(bootstrap), "--tag", f"v{version}"], env=invalid_env)
        require(invalid_result.returncode != 0, "release consumer accepted invalid provenance", invalid_result)
        require(not Path(invalid_env["AGENTOPS_INSTALL_ROOT"]).exists(), "invalid provenance wrote an install tree")

        home = temp / "clean-home"
        install_root = home / ".local" / "share" / "agentops-mis"
        bin_dir = home / ".local" / "bin"
        host_home = home / ".agentops" / "host"
        app_bundle = home / "Applications" / "AgentOps MIS.app"
        env = {
            **base_env,
            "HOME": str(home),
            "AGENTOPS_INSTALL_ROOT": str(install_root),
            "AGENTOPS_BIN_DIR": str(bin_dir),
            "AGENTOPS_APP_DIR": str(home / "Applications"),
            "AGENTOPS_HOST_HOME": str(host_home),
            "AGENTOPS_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_INSTALLER_TEST_RELEASE_DIR": str(output),
            "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON": deterministic_storage_fixture(
                install_root,
                host_home,
                bin_dir,
                home / "Applications",
            ),
        }
        installed = run(
            ["sh", str(bootstrap), "--tag", f"v{version}", "--init", "--start", "--port", str(free_port())],
            env=env,
        )
        agentops = bin_dir / "agentops"
        agentops_worker = bin_dir / "agentops-worker"
        stopped = None
        try:
            combined = installed.stdout + installed.stderr
            require(installed.returncode == 0, "release consumer install/start failed", installed)
            require("owner_setup_code" not in combined and "agthost_" not in combined, "release consumer output exposed credential material")
            require(
                (app_bundle / "Contents" / "MacOS" / "agentops-mis-launcher").is_file(),
                "release consumer did not install the macOS launcher",
            )
            require(agentops_worker.is_file() and os.access(agentops_worker, os.X_OK), "release consumer did not install agentops-worker")
            worker_help = run([str(agentops_worker), "--help"], env=env)
            require(worker_help.returncode == 0 and "Run an AgentOps MIS worker loop." in worker_help.stdout, "installed agentops-worker is not runnable", worker_help)

            version_result = run([str(agentops), "host", "version"], env=env)
            status_result = run([str(agentops), "host", "status"], env=env)
            version_payload = json.loads(version_result.stdout)
            status_payload = json.loads(status_result.stdout)
            require(version_result.returncode == 0 and version_payload.get("version") == version, "installed version readback failed", version_result)
            require(version_payload.get("git_commit") == payload.get("git_commit"), "installed source commit readback failed", version_result)
            require(status_result.returncode == 0 and status_payload.get("running") is True, "installed Host did not become ready", status_result)
            require((status_payload.get("human_access") or {}).get("status") == "bootstrap_required", "clean Host did not expose Owner bootstrap action")
        finally:
            if agentops.is_file():
                stopped = run([str(agentops), "host", "stop"], env=env)
        require(stopped is not None, "installed Host cleanup command was unavailable")
        require(stopped.returncode == 0, "installed Host did not stop cleanly", stopped)

        print(json.dumps({
            "ok": True,
            "operation": "private_host_release_consumer_smoke",
            "release_asset_count": len(payload["artifacts"]) + 1,
            "checksum_verified": True,
            "candidate_provenance_verified": True,
            "checksum_mismatch_rejected": True,
            "archive_traversal_rejected": True,
            "invalid_provenance_rejected": True,
            "installed_commit_verified": True,
            "clean_home": True,
            "repository_required_on_consumer": False,
            "host_started": True,
            "owner_bootstrap_required": True,
            "macos_launcher_installed": True,
            "worker_cli_installed": True,
            "credentials_omitted": True,
            "network_used": False,
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
