#!/usr/bin/env python3
"""Exercise the generated macOS launcher in an isolated HOME without real Runtimes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "macos" / "install.sh"
UNINSTALLER = ROOT / "packaging" / "macos" / "uninstall.sh"
LAUNCHER_SOURCE = ROOT / "packaging" / "macos" / "launcher.py"
VERSION = "0.0.0-launcher-smoke"
TEST_STORAGE_ENV = "AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON"
TEST_STORAGE_FREE_BYTES = (2 * 1024 * 1024 * 1024) + (64 * 1024 * 1024)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False)


def require(condition: bool, message: str, process: subprocess.CompletedProcess | None = None) -> None:
    if condition:
        return
    detail = ""
    if process is not None:
        detail = f" (exit={process.returncode}, stdout_bytes={len(process.stdout)}, stderr_bytes={len(process.stderr)})"
    raise RuntimeError(message + detail)


def make_bundle(destination: Path) -> Path:
    bundle = destination / "bundle"
    payload = bundle / "payload"
    module = payload / "agentops_mis_cli"
    launcher_target = payload / "packaging" / "macos" / "launcher.py"
    module.mkdir(parents=True)
    launcher_target.parent.mkdir(parents=True)
    shutil.copy2(INSTALLER, bundle / "install.sh")
    shutil.copy2(UNINSTALLER, bundle / "uninstall.sh")
    shutil.copy2(LAUNCHER_SOURCE, launcher_target)
    (bundle / "install.sh").chmod(0o755)
    (bundle / "uninstall.sh").chmod(0o755)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "__main__.py").write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys
            from pathlib import Path

            home = Path(os.environ["AGENTOPS_HOST_HOME"])
            home.mkdir(parents=True, exist_ok=True)
            calls = home / "launcher-calls.jsonl"
            arguments = sys.argv[1:]
            with calls.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(arguments) + "\\n")
            command = arguments[:2]
            if command == ["host", "init"]:
                (home / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": int(arguments[-1])}) + "\\n", encoding="utf-8")
                sensitive = "fixture-" + "sensitive-value-should-not-escape"
                (home / "secrets.json").write_text(json.dumps({"owner_setup_code": sensitive}) + "\\n", encoding="utf-8")
                print(json.dumps({"ok": True, "owner_setup_code": sensitive}))
            elif command == ["host", "status"]:
                print(json.dumps({"ok": True, "running": (home / "running").is_file()}))
            elif command == ["host", "start"]:
                if arguments != ["host", "start", "--no-workers"]:
                    raise SystemExit(3)
                (home / "running").write_text("running\\n", encoding="utf-8")
                print(json.dumps({"ok": True, "running": True}))
            elif command == ["host", "open-console"]:
                print(json.dumps({"ok": True, "opened": True, "setup_code_omitted": True}))
            else:
                print(json.dumps({"ok": False, "error": "unsupported_fixture_command"}))
                raise SystemExit(2)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    records = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            records.append(
                {
                    "path": path.relative_to(bundle).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": digest(path),
                }
            )
    (bundle / "manifest.json").write_text(
        json.dumps({"version": VERSION, "files": records}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bundle


def environment(home: Path) -> dict[str, str]:
    values = {
        **os.environ,
        "HOME": str(home),
        "AGENTOPS_APP_DIR": str(home / "Applications"),
        "AGENTOPS_BIN_DIR": str(home / ".local" / "bin"),
        "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
        "AGENTOPS_HOST_HOME": str(home / ".agentops" / "host"),
        "AGENTOPS_INSTALL_ROOT": str(home / ".local" / "share" / "agentops-mis"),
    }
    managed_paths = {
        "install": Path(values["AGENTOPS_INSTALL_ROOT"]),
        "data": Path(values["AGENTOPS_HOST_HOME"]),
        "bin": Path(values["AGENTOPS_BIN_DIR"]),
        "app": Path(values["AGENTOPS_APP_DIR"]),
    }
    values[TEST_STORAGE_ENV] = json.dumps({
        "schema_version": 1,
        "paths": [
            {
                "path": str(path.absolute()),
                "device_id": f"launcher-{role}-device",
                "free_bytes": TEST_STORAGE_FREE_BYTES,
                "storage_role": role,
            }
            for role, path in managed_paths.items()
        ] + [
            {
                "path": str((managed_paths["data"].parent).absolute()),
                "device_id": "launcher-data-device",
                "free_bytes": TEST_STORAGE_FREE_BYTES,
                "storage_role": "data",
            },
            {
                "path": str((managed_paths["data"] / "backups").absolute()),
                "device_id": "launcher-data-device",
                "free_bytes": TEST_STORAGE_FREE_BYTES,
                "storage_role": "data",
            },
        ],
    }, sort_keys=True)
    return values


def read_calls(host_home: Path) -> list[list[str]]:
    path = host_home / "launcher-calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentops-macos-launcher-") as temporary:
        temp = Path(temporary)
        bundle = make_bundle(temp)

        home = temp / "clean home with $shell ' quote"
        env = environment(home)
        installed = run(["sh", str(bundle / "install.sh")], env=env)
        if installed.returncode != 0:
            try:
                storage_error = json.loads(installed.stderr.splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                storage_error = {}
            require(
                not (
                    storage_error.get("operation") == "host_bundle_storage_preflight"
                    and storage_error.get("test_override_applied") is not True
                ),
                "installer ignored the deterministic test-only storage fixture",
                installed,
            )
        require(installed.returncode == 0, "launcher fixture install failed", installed)
        install_payload = json.loads(installed.stdout)
        storage_preflight = install_payload.get("storage_preflight")
        require(isinstance(storage_preflight, list), "installer omitted deterministic storage evidence")
        storage_roles: set[str] = set()
        for record in storage_preflight:
            if not isinstance(record, dict) or record.get("status") != "ready":
                continue
            roles = record.get("storage_roles")
            if not isinstance(roles, list):
                roles = [record.get("storage_role")]
            storage_roles.update(role for role in roles if isinstance(role, str))
            for target_kind in record.get("target_kinds") or []:
                if target_kind in {"data", "backup"}:
                    storage_roles.add("data")
                elif target_kind in {"install", "bin", "app"}:
                    storage_roles.add(target_kind)
        require(
            {"install", "data", "bin", "app"}.issubset(storage_roles),
            "installer did not budget every managed launcher volume",
        )
        require(
            all(record.get("test_override_applied") is True for record in storage_preflight),
            "launcher smoke did not use deterministic test-only storage observations",
        )
        app = home / "Applications" / "AgentOps MIS.app"
        executable = app / "Contents" / "MacOS" / "agentops-mis-launcher"
        resources = app / "Contents" / "Resources"
        config_path = resources / "launcher-config.json"
        marker_path = resources / "agentops-mis-launcher.json"
        require(install_payload.get("launcher_installed") is True, "installer did not report launcher installation")
        require(Path(str(install_payload.get("launcher"))) == app, "installer returned the wrong launcher path")
        require(executable.is_file() and os.access(executable, os.X_OK), "launcher executable is missing")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
        require(config.get("credentials_included") is False, "launcher config claimed credential inclusion")
        require(Path(str(config.get("python_path"))).is_absolute(), "launcher did not pin an absolute Python runtime")
        require(config.get("default_port") == 18878, "launcher did not pin the product Host port")
        require(marker.get("managed") is True and marker.get("product") == "AgentOps MIS Private Host Launcher", "launcher ownership marker is invalid")
        require(
            "<key>CFBundleExecutable</key><string>agentops-mis-launcher</string>" in plist
            and "<key>LSUIElement</key><true/>" in plist,
            "launcher Info.plist is invalid",
        )

        finder_env = {**env, "PATH": "/usr/bin:/bin"}
        launched = run([str(executable)], env=finder_env)
        require(launched.returncode == 0, "clean HOME launcher run failed", launched)
        sensitive = "fixture-" + "sensitive-value-should-not-escape"
        require(sensitive not in launched.stdout + launched.stderr, "launcher exposed initialization output")
        calls = read_calls(Path(env["AGENTOPS_HOST_HOME"]))
        require(calls[:4] == [
            ["host", "init", "--port", "18878"],
            ["host", "status"],
            ["host", "start", "--no-workers"],
            ["host", "open-console"],
        ], "launcher did not use the safe first-run sequence")

        launched_again = run([str(executable)], env=finder_env)
        require(launched_again.returncode == 0, "second launcher run failed", launched_again)
        calls = read_calls(Path(env["AGENTOPS_HOST_HOME"]))
        require(calls.count(["host", "init", "--port", "18878"]) == 1, "launcher initialized twice")
        require(calls.count(["host", "start", "--no-workers"]) == 1, "launcher restarted an already-running Host")
        require(calls[-2:] == [["host", "status"], ["host", "open-console"]], "second launcher run changed Host state")
        require(not any("--worker" in part or "--confirm-live-workers" in part for call in calls for part in call), "launcher enabled a live Runtime")

        parallel = [subprocess.Popen([str(executable)], env=finder_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(10)]
        parallel_results = [process.communicate(timeout=120) + (process.returncode,) for process in parallel]
        require(all(return_code == 0 for _stdout, _stderr, return_code in parallel_results), "parallel launcher runs were not idempotent")
        calls = read_calls(Path(env["AGENTOPS_HOST_HOME"]))
        require(calls.count(["host", "init", "--port", "18878"]) == 1, "parallel launch initialized more than once")
        require(calls.count(["host", "start", "--no-workers"]) == 1, "parallel launch restarted the Host")

        app_bytes = b"".join(path.read_bytes() for path in app.rglob("*") if path.is_file())
        require(sensitive.encode("utf-8") not in app_bytes, "launcher application persisted setup material")

        marker_bytes = marker_path.read_bytes()
        marker_path.write_text("{}\n", encoding="utf-8")
        rejected_uninstall = run(["sh", str(bundle / "uninstall.sh")], env=env)
        require(rejected_uninstall.returncode != 0 and app.is_dir(), "uninstaller accepted a modified launcher marker", rejected_uninstall)
        marker_path.write_bytes(marker_bytes)

        host_service = home / "Library" / "LaunchAgents" / "dev.agentops.mis.private-host.plist"
        host_service.parent.mkdir(parents=True, exist_ok=True)
        host_service.write_text("service fixture\n", encoding="utf-8")
        service_guarded_uninstall = run(["sh", str(bundle / "uninstall.sh")], env=env)
        require(
            service_guarded_uninstall.returncode != 0 and app.is_dir(),
            "uninstaller accepted an installed Host LaunchAgent",
            service_guarded_uninstall,
        )
        host_service.unlink()

        uninstalled = run(["sh", str(bundle / "uninstall.sh")], env=env)
        require(uninstalled.returncode == 0, "launcher fixture uninstall failed", uninstalled)
        uninstall_payload = json.loads(uninstalled.stdout)
        require(uninstall_payload.get("launcher_removed") is True and not app.exists(), "uninstaller left the managed launcher behind")
        require(Path(env["AGENTOPS_HOST_HOME"]).is_dir(), "uninstaller removed Host data by default")

        stranger_home = temp / "stranger-home"
        stranger_env = environment(stranger_home)
        stranger_app = stranger_home / "Applications" / "AgentOps MIS.app"
        stranger_app.mkdir(parents=True)
        sentinel = stranger_app / "sentinel"
        sentinel.write_text("preserve\n", encoding="utf-8")
        rejected_install = run(["sh", str(bundle / "install.sh")], env=stranger_env)
        require(rejected_install.returncode != 0 and sentinel.read_text(encoding="utf-8") == "preserve\n", "installer overwrote a foreign application", rejected_install)

        partial_home = temp / "partial-home"
        partial_env = environment(partial_home)
        partial_installed = run(["sh", str(bundle / "install.sh")], env=partial_env)
        require(partial_installed.returncode == 0, "partial-state fixture install failed", partial_installed)
        partial_host = Path(partial_env["AGENTOPS_HOST_HOME"])
        partial_host.mkdir(parents=True)
        (partial_host / "config.json").write_text("{}\n", encoding="utf-8")
        partial_executable = partial_home / "Applications" / "AgentOps MIS.app" / "Contents" / "MacOS" / "agentops-mis-launcher"
        partial_launch = run([str(partial_executable)], env={**partial_env, "PATH": "/usr/bin:/bin"})
        require(partial_launch.returncode != 0 and not (partial_host / "secrets.json").exists(), "launcher overwrote partial Host state", partial_launch)

        print(json.dumps({
            "ok": True,
            "operation": "private_host_macos_launcher_smoke",
            "clean_home_install": True,
            "finder_style_path": "passed",
            "absolute_python_runtime": True,
            "first_run_init": True,
            "start_no_workers": True,
            "parallel_launch_count": 10,
            "parallel_idempotent": True,
            "setup_material_omitted": True,
            "foreign_app_rejected": True,
            "modified_marker_uninstall_rejected": True,
            "installed_host_service_uninstall_rejected": True,
            "partial_host_state_rejected": True,
            "uninstall_preserved_host_data": True,
            "deterministic_storage_fixture": True,
            "storage_roles": sorted(storage_roles),
            "real_runtime_called": False,
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
