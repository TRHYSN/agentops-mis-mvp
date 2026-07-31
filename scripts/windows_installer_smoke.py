#!/usr/bin/env python3
"""Reproducible static and Windows E2E acceptance for the CLI installer."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "packaging" / "windows"
REQUIRED_FILES = (
    WINDOWS_DIR / "install.ps1",
    WINDOWS_DIR / "uninstall.ps1",
    WINDOWS_DIR / "launcher.ps1",
    WINDOWS_DIR / "README.md",
    WINDOWS_DIR / "ACCEPTANCE.md",
)
PROHIBITED_TEXT = (
    "AGENTOPS_API_KEY=",
    "Authorization: Bearer",
    "ntn_",
    "sk-",
    "BEGIN PRIVATE KEY",
    "Invoke-WebRequest",
    "Invoke-RestMethod",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        **kwargs,
    )


def build_wheel(destination: Path) -> Path:
    sys.path.insert(0, str(ROOT))
    try:
        from agentops_mis_cli._build_backend import build_wheel as backend_build_wheel
    finally:
        sys.path.pop(0)
    name = backend_build_wheel(str(destination))
    return destination / name


def assert_static_contract() -> dict[str, object]:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file()]
    if missing:
        raise AssertionError(f"missing Windows packaging files: {missing}")
    scripts = [WINDOWS_DIR / "install.ps1", WINDOWS_DIR / "uninstall.ps1", WINDOWS_DIR / "launcher.ps1"]
    contents: dict[str, str] = {}
    for path in scripts:
        raw = path.read_bytes()
        text = raw.decode("ascii")
        contents[path.name] = text
        if "Set-StrictMode -Version Latest" not in text:
            raise AssertionError(f"strict mode missing: {path.name}")
        for marker in PROHIBITED_TEXT:
            if marker.lower() in text.lower():
                raise AssertionError(f"prohibited marker in {path.name}: {marker}")
    install = contents["install.ps1"]
    uninstall = contents["uninstall.ps1"]
    launcher = contents["launcher.ps1"]
    required_install_markers = (
        '"--no-index", "--no-deps"',
        "Python 3.10 or newer is required",
        "installed version collision",
        "agentops-worker.exe",
        "credentials_stored = $false",
        "Assert-NoReparsePoint",
        "if ($AddToPath)",
        "path_added_by_installer = $pathAdded",
    )
    required_uninstall_markers = (
        'Join-Path $env:LOCALAPPDATA "AgentOps MIS"',
        "managed Windows CLI marker is missing",
        "-PurgeData requires -ConfirmPurgeData",
        "data_preserved",
        "Assert-NoReparsePoint",
    )
    required_launcher_markers = (
        'ValidateSet("agentops", "agentops-worker")',
        "invalid AgentOps MIS current manifest",
        "@ToolArguments",
    )
    for marker in required_install_markers:
        if marker not in install:
            raise AssertionError(f"install contract marker missing: {marker}")
    if "[switch]$AddToPath" not in install or "NoPathUpdate" in install:
        raise AssertionError("user PATH must be explicit opt-in")
    for marker in required_uninstall_markers:
        if marker not in uninstall:
            raise AssertionError(f"uninstall contract marker missing: {marker}")
    for marker in required_launcher_markers:
        if marker not in launcher:
            raise AssertionError(f"launcher contract marker missing: {marker}")
    return {
        "powershell_ascii": True,
        "strict_mode": True,
        "network_downloads_absent": True,
        "credential_literals_absent": True,
        "reparse_point_guards": True,
        "managed_uninstall_guard": True,
    }


def assert_deterministic_wheel() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agentops-windows-wheel-a-") as first_dir, tempfile.TemporaryDirectory(
        prefix="agentops-windows-wheel-b-"
    ) as second_dir:
        first = build_wheel(Path(first_dir))
        second = build_wheel(Path(second_dir))
        first_hash = digest(first)
        second_hash = digest(second)
        if first.read_bytes() != second.read_bytes():
            raise AssertionError("offline wheel is not deterministic")
        with zipfile.ZipFile(first) as archive:
            entry_name = next(name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt"))
            entry_points = archive.read(entry_name).decode("utf-8")
        for command in ("agentops = agentops_mis_cli.cli:main", "agentops-worker = agentops_mis_cli.worker:main"):
            if command not in entry_points:
                raise AssertionError(f"wheel entry point missing: {command}")
        return {
            "wheel_name": first.name,
            "wheel_sha256": first_hash,
            "second_wheel_sha256": second_hash,
            "byte_identical": True,
            "entry_points_verified": ["agentops", "agentops-worker"],
        }


def powershell_command() -> str | None:
    for candidate in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def parse_last_json(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError("PowerShell command did not emit a JSON result")


def assert_no_sensitive_output(*values: str) -> None:
    joined = "\n".join(values).lower()
    for marker in ("authorization: bearer", "begin private key", "ntn_", "sk-"):
        if marker in joined:
            raise AssertionError(f"sensitive marker in acceptance output: {marker}")


def run_windows_e2e(shell: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agentops-windows-e2e-") as temporary:
        root = Path(temporary)
        data_root = root / "AgentOps MIS"
        install_root = data_root / "cli"
        bin_dir = data_root / "bin"
        data_root.mkdir()
        sentinel = data_root / "preserve-me.txt"
        sentinel.write_text("preserved\n", encoding="ascii")
        base = [shell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        install_command = [
            *base,
            str(WINDOWS_DIR / "install.ps1"),
            "-SourceRoot",
            str(ROOT),
            "-InstallRoot",
            str(install_root),
            "-BinDir",
            str(bin_dir),
            "-TestMode",
        ]
        first = run(install_command, cwd=ROOT, timeout=180)
        assert_no_sensitive_output(first.stdout, first.stderr)
        if first.returncode != 0:
            raise AssertionError(f"Windows install failed: {first.stderr.strip()}")
        first_payload = parse_last_json(first.stdout)
        if first_payload.get("ok") is not True or first_payload.get("credentials_stored") is not False:
            raise AssertionError("Windows install result failed its safety contract")
        for command in ("agentops.cmd", "agentops-worker.cmd"):
            launched = run(["cmd.exe", "/d", "/c", str(bin_dir / command), "--help"], timeout=60)
            assert_no_sensitive_output(launched.stdout, launched.stderr)
            if launched.returncode != 0:
                raise AssertionError(f"installed entry point failed: {command}")
        second = run(install_command, cwd=ROOT, timeout=180)
        if second.returncode != 0 or parse_last_json(second.stdout).get("reused_version") is not True:
            raise AssertionError("exact Windows reinstall was not idempotent")
        uninstall = run(
            [
                *base,
                str(WINDOWS_DIR / "uninstall.ps1"),
                "-InstallRoot",
                str(install_root),
                "-BinDir",
                str(bin_dir),
                "-DataRoot",
                str(data_root),
                "-KeepPath",
                "-TestMode",
            ],
            cwd=ROOT,
            timeout=120,
        )
        assert_no_sensitive_output(second.stdout, second.stderr, uninstall.stdout, uninstall.stderr)
        uninstall_payload = parse_last_json(uninstall.stdout)
        if uninstall.returncode != 0 or uninstall_payload.get("data_preserved") is not True:
            raise AssertionError("Windows uninstall failed")
        if install_root.exists() or (bin_dir / "agentops.cmd").exists() or not sentinel.is_file():
            raise AssertionError("Windows uninstall ownership/data-preservation contract failed")
        return {
            "actual_windows_execution": True,
            "install_ok": True,
            "entry_points_ok": True,
            "idempotent_reinstall": True,
            "uninstall_ok": True,
            "data_preserved": True,
        }


def main() -> int:
    static = assert_static_contract()
    wheel = assert_deterministic_wheel()
    shell = powershell_command()
    actual_windows = os.name == "nt" and shell is not None
    windows = run_windows_e2e(shell) if actual_windows and shell else {
        "actual_windows_execution": False,
        "status": "static_cross_platform_only",
        "reason": "Windows PowerShell process execution is required for the E2E lane",
    }
    payload = {
        "ok": True,
        "operation": "windows_installer_smoke",
        "python_version": sys.version.split()[0],
        "host_platform": sys.platform,
        "static": static,
        "wheel": wheel,
        "windows_e2e": windows,
        "credentials_read": False,
        "database_content_read": False,
        "token_omitted": True,
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
