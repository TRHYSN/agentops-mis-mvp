#!/usr/bin/env python3
"""Build, scan, install, exercise, and uninstall a private-host bundle offline."""
from __future__ import annotations

import atexit
import fcntl
import hashlib
import html
import http.cookiejar
import json
import os
import re
import shutil
import socket
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_private_host_bundle.py"
FORBIDDEN_NAMES = {".git", ".agentops_runtime", "__pycache__", "artifacts", "node_modules", "logs"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo"}
ACTIVE_HOSTS: list[tuple[Path, dict[str, str]]] = []
TEST_STORAGE_FREE_BYTES = 8 * 1024 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def run(command: list[str], *, env: dict | None = None, cwd: Path | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, input=input_text, capture_output=True, text=True, timeout=120, check=False)


def register_host(cli: Path, env: dict[str, str]) -> None:
    ACTIVE_HOSTS.append((cli, dict(env)))


def unregister_host(cli: Path) -> None:
    for index in range(len(ACTIVE_HOSTS) - 1, -1, -1):
        if ACTIVE_HOSTS[index][0] == cli:
            ACTIVE_HOSTS.pop(index)
            return


def cleanup_hosts() -> None:
    while ACTIVE_HOSTS:
        cli, env = ACTIVE_HOSTS.pop()
        try:
            subprocess.run(
                [str(cli), "host", "stop"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def install_cleanup_handlers() -> None:
    atexit.register(cleanup_hosts)

    def handle_signal(signum, _frame):
        cleanup_hosts()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(opener, method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with (opener.open(request, timeout=30) if opener else urlopen(request, timeout=30)) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": "non_json_error"}


def fail(message: str, process: subprocess.CompletedProcess | None = None) -> None:
    detail = ""
    if process:
        detail = f"\nstdout:\n{process.stdout[-2000:]}\nstderr:\n{process.stderr[-2000:]}"
    raise RuntimeError(message + detail)


def forbidden(path: str) -> bool:
    rel = PurePosixPath(path)
    lower = [part.lower() for part in rel.parts]
    return (
        any(part in FORBIDDEN_NAMES or part.startswith(".env") or "token" in part for part in lower)
        or rel.suffix.lower() in FORBIDDEN_SUFFIXES
        or "sample_export" in rel.name.lower()
    )


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
                "device_id": f"bundle-{role}-device",
                "free_bytes": TEST_STORAGE_FREE_BYTES,
                "storage_role": role,
            }
            for role, role_paths in paths.items()
            for path in role_paths
        ],
    }, sort_keys=True)


def extract_bundle(tar_path: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(tar_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.issym() or member.islnk() or forbidden(member.name):
                fail(f"forbidden archive member: {member.name}")
            target = (destination / member.name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                fail(f"archive traversal path: {member.name}")
        archive.extractall(destination, filter="data")
    return next(destination.iterdir())


def main() -> int:
    install_cleanup_handlers()
    ui = ROOT / "ui" / "start-building-app" / "dist" / "index.html"
    if not ui.is_file():
        fail("production UI dist is required; run npm run build first")

    with tempfile.TemporaryDirectory(prefix="agentops-private-host-smoke-") as temporary:
        temp = Path(temporary)
        dirty_source = temp / "dirty-source"
        cloned = run(["git", "clone", "--quiet", "--no-local", str(ROOT), str(dirty_source)])
        if cloned.returncode != 0:
            fail("release source fixture clone failed", cloned)
        with (dirty_source / "README.md").open("a", encoding="utf-8") as handle:
            handle.write("\ntracked dirty release fixture\n")
        dirty_output = temp / "dirty-output"
        dirty_build = run(
            [
                sys.executable,
                str(dirty_source / "scripts" / "build_private_host_bundle.py"),
                "--ui-dist",
                str(ui.parent),
                "--output-dir",
                str(dirty_output),
                "--version",
                "0.0.0-dirty-source",
            ],
            cwd=dirty_source,
        )
        if dirty_build.returncode == 0 or dirty_output.exists():
            fail("bundle builder accepted dirty tracked release source", dirty_build)

        output = temp / "out"
        version = "0.0.0-smoke"
        built = run([sys.executable, str(BUILDER), "--output-dir", str(output), "--version", version])
        if built.returncode != 0:
            fail("bundle build failed", built)
        build_result = json.loads(built.stdout)
        reproducible_output = temp / "out-reproducible"
        rebuilt = run([sys.executable, str(BUILDER), "--output-dir", str(reproducible_output), "--version", version])
        if rebuilt.returncode != 0:
            fail("repeat bundle build failed", rebuilt)
        for first in sorted(output.iterdir()):
            second = reproducible_output / first.name
            if not second.is_file() or digest(first) != digest(second):
                fail(f"bundle build is not reproducible: {first.name}", rebuilt)
        tar_path = next(Path(path) for path in build_result["artifacts"] if path.endswith(".tar.gz"))
        zip_path = next(Path(path) for path in build_result["artifacts"] if path.endswith(".zip"))
        provenance_path = Path(build_result["provenance"])
        checksum_path = Path(build_result["checksums"])
        checksums = json.loads(checksum_path.read_text(encoding="utf-8"))
        for release_path in (tar_path, zip_path, provenance_path):
            if checksums.get(release_path.name) != digest(release_path):
                fail(f"release checksum mismatch: {release_path.name}")

        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if forbidden(info.filename):
                    fail(f"forbidden zip member: {info.filename}")
                target = (temp / "zip-scan" / info.filename).resolve()
                zip_root = (temp / "zip-scan").resolve()
                if zip_root not in target.parents and target != zip_root:
                    fail(f"zip traversal path: {info.filename}")

        bundle = extract_bundle(tar_path, temp / "extract")
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if manifest["version"] != version or manifest["git_commit"] != subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip():
            fail("manifest version or commit mismatch")
        if (
            provenance.get("version") != version
            or provenance.get("git_commit") != manifest["git_commit"]
            or provenance.get("source_clean") is not True
            or provenance.get("ui_tree_sha256") != manifest.get("ui_tree_sha256")
            or provenance.get("artifacts", {}).get(tar_path.name) != digest(tar_path)
            or provenance.get("artifacts", {}).get(zip_path.name) != digest(zip_path)
        ):
            fail("candidate provenance does not bind source, UI, and release assets")
        for record in manifest["files"]:
            path = bundle / record["path"]
            if not path.is_file() or digest(path) != record["sha256"]:
                fail(f"manifest file checksum mismatch: {record['path']}")

        home = temp / "home with $shell ' quote"
        install_root = home / ".local" / "share" / "agentops-mis"
        bin_dir = home / ".local" / "bin"
        host_data = home / ".agentops" / "host"
        app_dir = home / "Applications"
        app_bundle = app_dir / "AgentOps MIS.app"
        sentinel = host_data / "preserve-me"
        env = {
            **{
                key: value
                for key, value in os.environ.items()
                if key not in {"AGENTOPS_AGENT_ID", "AGENTOPS_API_KEY", "AGENTOPS_BASE_URL", "AGENTOPS_CONFIG", "AGENTOPS_WORKSPACE_ID"}
            },
            "HOME": str(home),
            "AGENTOPS_INSTALL_ROOT": str(install_root),
            "AGENTOPS_BIN_DIR": str(bin_dir),
            "AGENTOPS_APP_DIR": str(app_dir),
            "AGENTOPS_HOST_HOME": str(host_data),
            "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE": "1",
            "AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON": deterministic_storage_fixture(
                install_root,
                host_data,
                bin_dir,
                app_dir,
            ),
        }
        host_data.parent.mkdir(parents=True)
        lifecycle_lock = host_data.parent / ".agentops-mis-host-lifecycle.lock"
        lock_symlink_target = home / "unrelated-lock-target"
        lock_symlink_target.write_text("preserve", encoding="utf-8")
        lifecycle_lock.symlink_to(lock_symlink_target)
        symlink_lock_install = run(["sh", str(bundle / "install.sh")], env=env)
        lifecycle_lock.unlink()
        if (
            symlink_lock_install.returncode == 0
            or install_root.exists()
            or lock_symlink_target.read_text(encoding="utf-8") != "preserve"
        ):
            fail("installer followed a symlinked lifecycle lock", symlink_lock_install)

        tampered = temp / "tampered"
        shutil.copytree(bundle, tampered)
        with (tampered / "payload" / "LICENSE").open("a", encoding="utf-8") as handle:
            handle.write("tampered\n")
        rejected = run(["sh", str(tampered / "install.sh")], env=env)
        if rejected.returncode == 0 or install_root.exists():
            fail("installer accepted a payload that did not match its manifest", rejected)

        installed = run(["sh", str(bundle / "install.sh")], env=env)
        if installed.returncode != 0:
            fail("bundle install failed", installed)
        installed_payload = json.loads(installed.stdout)
        installed_cli = bin_dir / "agentops"
        installed_worker = bin_dir / "agentops-worker"
        initial_cli_inode = installed_cli.stat().st_ino
        initial_worker_inode = installed_worker.stat().st_ino
        launcher_executable = app_bundle / "Contents" / "MacOS" / "agentops-mis-launcher"
        launcher_config_path = app_bundle / "Contents" / "Resources" / "launcher-config.json"
        if (
            installed_payload.get("launcher_installed") is not True
            or Path(str(installed_payload.get("launcher") or "")) != app_bundle
            or not launcher_executable.is_file()
            or not os.access(launcher_executable, os.X_OK)
            or not launcher_config_path.is_file()
            or Path(str(installed_payload.get("worker_shim") or "")) != installed_worker
            or not installed_worker.is_file()
            or not os.access(installed_worker, os.X_OK)
        ):
            fail("bundle did not install the managed macOS launcher", installed)
        launcher_config = json.loads(launcher_config_path.read_text(encoding="utf-8"))
        if (
            launcher_config.get("credentials_included") is not False
            or launcher_config.get("default_port") != 18878
            or not Path(str(launcher_config.get("python_path") or "")).is_absolute()
            or launcher_config.get("current_path") != str(install_root / "current")
        ):
            fail("installed macOS launcher config is unsafe", installed)
        version_status = run(
            [str(bin_dir / "agentops"), "host", "version"],
            env={**env, "PYTHONPATH": str(ROOT)},
            cwd=ROOT,
        )
        version_payload = json.loads(version_status.stdout)
        if (
            version_status.returncode != 0
            or version_payload.get("packaged_install") is not True
            or version_payload.get("version") != version
            or version_payload.get("git_commit") != manifest["git_commit"]
        ):
            fail("installed shim was shadowed by source checkout or PYTHONPATH", version_status)
        release_env_probe = run(
            [
                sys.executable,
                "-c",
                (
                    "import json; from agentops_mis_cli.host import host_env; "
                    "value=host_env({'database_path':'/tmp/omitted.db','cookie_secure':False,"
                    "'allowed_origins':[],'workspace_id':'local-demo'},"
                    "{'api_key':'omitted','admin_key':'omitted','owner_setup_code':'omitted'}); "
                    "print(json.dumps({'version':value.get('AGENTOPS_HOST_VERSION'),"
                    "'git_commit':value.get('AGENTOPS_GIT_COMMIT')}))"
                ),
            ],
            env={**env, "PYTHONPATH": str(install_root / "current")},
            cwd=install_root / "current",
        )
        release_env = json.loads(release_env_probe.stdout)
        if (
            release_env_probe.returncode != 0
            or release_env.get("version") != version
            or release_env.get("git_commit") != manifest["git_commit"]
        ):
            fail("packaged Host environment omitted release provenance", release_env_probe)
        help_result = run([str(bin_dir / "agentops"), "host", "--help"], env=env)
        if help_result.returncode != 0 or "Manage the private local AgentOps MIS host" not in help_result.stdout:
            fail("installed agentops host --help failed", help_result)
        worker_help = run([str(installed_worker), "--help"], env=env)
        if worker_help.returncode != 0 or "Run an AgentOps MIS worker loop." not in worker_help.stdout:
            fail("installed agentops-worker --help failed", worker_help)
        for command in ("bootstrap-owner", "configure-cli", "backup", "backup-verify", "restore"):
            command_help = run([str(bin_dir / "agentops"), "host", command, "--help"], env=env)
            if command_help.returncode != 0:
                fail(f"installed agentops host {command} --help failed", command_help)
        if not (install_root / "current" / "ui" / "start-building-app" / "dist" / "index.html").is_file():
            fail("installed production UI missing")
        if not (install_root / "current" / "scripts" / "v1_5_live_product_readiness_smoke.py").is_file():
            fail("installed real-runtime ledger readback client missing")
        for release_doc in (
            "docs/PRIVATE_HOST_OPERATOR_RUNBOOK.md",
            "docs/PRIVATE_HOST_WORKER_SERVICE_ACCEPTANCE.md",
            "docs/RELEASE_PROVENANCE.md",
            "docs/REMOTE_WORKER_OPERATIONS_RUNBOOK.md",
            "docs/SBOM_MINIMAL.md",
            "docs/THIRD_PARTY_NOTICES.md",
        ):
            if not (install_root / "current" / release_doc).is_file():
                fail(f"installed release evidence missing: {release_doc}")
        for plan_spec in (
            "PROJECT_SPEC.md",
            "AGENT_WORKFLOW.md",
            "BASE_INDEX.md",
            "docs/AGENT_WORK_METHOD_BLOCK.md",
        ):
            if not (install_root / "current" / plan_spec).is_file():
                fail(f"installed Agent Plan spec missing: {plan_spec}")
        initialized = run([str(bin_dir / "agentops"), "host", "init", "--port", str(free_port())], env=env)
        if initialized.returncode != 0:
            fail("installed agentops host init failed", initialized)
        init_payload = json.loads(initialized.stdout)
        sentinel.write_text("user data", encoding="utf-8")
        if (
            "Run: agentops host start" not in (init_payload.get("next_actions") or [])
            or any("--build-ui" in action for action in (init_payload.get("next_actions") or []))
        ):
            fail("installed Host init advertised a repository UI build", initialized)
        doctor = run([str(bin_dir / "agentops"), "host", "doctor"], env=env)
        if doctor.returncode != 0 or not json.loads(doctor.stdout).get("ok"):
            fail("installed agentops host doctor failed", doctor)

        config = json.loads((host_data / "config.json").read_text(encoding="utf-8"))
        base_url = f"http://127.0.0.1:{config['port']}"
        register_host(bin_dir / "agentops", env)
        started = run([str(bin_dir / "agentops"), "host", "start", "--no-workers"], env=env)
        if started.returncode != 0 or not json.loads(started.stdout).get("ok"):
            fail("installed Host failed to start for Agent Plan verification", started)
        configure_cli = run([str(bin_dir / "agentops"), "host", "configure-cli", "--confirm"], env=env)
        configure_cli_payload = json.loads(configure_cli.stdout or "{}")
        configured_machine_key = str(json.loads((host_data / "secrets.json").read_text(encoding="utf-8")).get("api_key") or "")
        worker_preflight = run([str(bin_dir / "agentops"), "worker", "preflight", "--adapter", "mock"], env=env)
        worker_preflight_payload = json.loads(worker_preflight.stdout or "{}")
        worker_service_path = temp / "local.agentops.worker.agt_bundle_local_config.plist"
        worker_service = run([
            str(installed_worker),
            "service-install",
            "--manager",
            "launchd",
            "--adapter",
            "mock",
            "--agent-id",
            "agt_bundle_local_config",
            "--credential-source",
            "local_config",
            "--service-path",
            str(worker_service_path),
            "--confirm-install",
        ], env=env)
        worker_service_payload = json.loads(worker_service.stdout or "{}")
        worker_service_text = worker_service_path.read_text(encoding="utf-8") if worker_service_path.is_file() else ""
        worker_service_cwd_match = re.search(r"<key>WorkingDirectory</key>\s*<string>([^<]+)</string>", worker_service_text)
        worker_service_cwd = html.unescape(worker_service_cwd_match.group(1)) if worker_service_cwd_match else ""
        bootstrap_password = "fixture-bundle-smoke-password"
        bootstrap_cli = run(
            [
                str(bin_dir / "agentops"),
                "host",
                "bootstrap-owner",
                "--username",
                "bundle-smoke-owner",
                "--display-name",
                "Bundle Smoke Owner",
                "--password-stdin",
                "--confirm",
            ],
            env=env,
            input_text=bootstrap_password + "\n",
        )
        bootstrap_payload = json.loads(bootstrap_cli.stdout or "{}")
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth_status, authenticated = http_json(
            opener,
            "POST",
            base_url + "/api/human-auth/login",
            {
                "username": "bundle-smoke-owner",
                "password": bootstrap_password,
            },
            {"Origin": base_url},
        )
        csrf = str(authenticated.get("csrf_token") or "")
        workflow_status, workflow = http_json(
            opener,
            "POST",
            base_url + "/api/workflows/customer-worker-task",
            {
                "adapter": "mock",
                "confirm_run": True,
                "title": "Installed bundle Agent Plan verification",
                "description": "Verify the installed runtime can read its packaged authority specs.",
                "acceptance_criteria": "Mock run writes bounded plan, run, evaluation, artifact, and audit evidence.",
                "priority": "high",
                "risk_level": "low",
            },
            {"Origin": base_url, "X-AgentOps-CSRF": csrf},
        )
        running_uninstall = run(["sh", str(bundle / "uninstall.sh")], env=env)
        if running_uninstall.returncode == 0 or not install_root.is_dir() or not (bin_dir / "agentops").is_file() or not installed_worker.is_file():
            fail("uninstaller removed product files while the managed Host was running", running_uninstall)
        stopped = run([str(bin_dir / "agentops"), "host", "stop"], env=env)
        if stopped.returncode == 0:
            unregister_host(bin_dir / "agentops")
        evidence = workflow.get("evidence") or {}
        if (
            configure_cli.returncode != 0
            or configure_cli_payload.get("machine_credential_configured") is not True
            or configure_cli_payload.get("browser_session_reused") is not False
            or not configured_machine_key
            or configured_machine_key in ((configure_cli.stdout or "") + (configure_cli.stderr or "") + (worker_preflight.stdout or "") + (worker_preflight.stderr or ""))
            or worker_preflight.returncode != 0
            or worker_preflight_payload.get("ok") is not True
            or worker_service.returncode != 0
            or worker_service_payload.get("wrote") is not True
            or worker_service_payload.get("credential_source") != "local_config"
            or (worker_service_path.stat().st_mode & 0o777 if worker_service_path.exists() else 0) != 0o600
            or "AGENTOPS_API_KEY" in worker_service_text
            or "AGENTOPS_WORKER_CREDENTIAL_SOURCE" not in worker_service_text
            or "--use-session" not in worker_service_text
            or Path(worker_service_cwd) != install_root / "current"
            or bootstrap_cli.returncode != 0
            or bootstrap_payload.get("owner_created") is not True
            or bootstrap_password in ((bootstrap_cli.stdout or "") + (bootstrap_cli.stderr or ""))
            or auth_status != 200
            or not csrf
            or workflow_status != 201
            or workflow.get("ok") is not True
            or not workflow.get("run_id")
            or workflow.get("plan_evidence_pass") is not True
            or int(evidence.get("evaluations") or 0) < 1
            or int(evidence.get("artifacts") or 0) < 1
            or stopped.returncode != 0
        ):
            fail(
                "installed Host could not complete Agent Plan verification",
                subprocess.CompletedProcess(
                    args=["installed-host-agent-plan"],
                    returncode=1,
                    stdout=json.dumps({
                        "auth_status": auth_status,
                        "configure_cli_ok": configure_cli.returncode == 0,
                        "worker_preflight_ok": worker_preflight_payload.get("ok"),
                        "worker_service": {
                            "returncode": worker_service.returncode,
                            "wrote": worker_service_payload.get("wrote"),
                            "credential_source": worker_service_payload.get("credential_source"),
                            "mode_ok": (worker_service_path.stat().st_mode & 0o777 if worker_service_path.exists() else 0) == 0o600,
                            "api_key_omitted": "AGENTOPS_API_KEY" not in worker_service_text,
                            "local_config_present": "AGENTOPS_WORKER_CREDENTIAL_SOURCE" in worker_service_text,
                            "use_session_present": "--use-session" in worker_service_text,
                            "follows_current": Path(worker_service_cwd) == install_root / "current",
                            "working_directory": worker_service_cwd,
                        },
                        "bootstrap_cli_ok": bootstrap_cli.returncode == 0,
                        "bootstrap_owner_created": bootstrap_payload.get("owner_created"),
                        "workflow_status": workflow_status,
                        "workflow_ok": workflow.get("ok"),
                        "run_id_present": bool(workflow.get("run_id")),
                        "plan_evidence_pass": workflow.get("plan_evidence_pass"),
                        "evidence": evidence,
                        "host_stopped": stopped.returncode == 0,
                    }, sort_keys=True),
                    stderr="",
                ),
            )

        database = Path(config["database_path"])
        with sqlite3.connect(database) as conn:
            conn.execute("CREATE TABLE product_upgrade_smoke(marker TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO product_upgrade_smoke VALUES('preserved-across-binary-switch')")

        custom_ui = install_root / "versions" / "custom-ui-fixture" / "dist"
        custom_ui.mkdir(parents=True)
        (custom_ui / "index.html").write_text("CUSTOM_UI_FIXTURE\n", encoding="utf-8")
        config["ui_dist"] = str(custom_ui)
        (host_data / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        custom_status = run([str(bin_dir / "agentops"), "host", "status"], env=env)
        custom_status_payload = json.loads(custom_status.stdout)
        if custom_status_payload.get("ui_dist") != str(custom_ui.resolve()) or custom_status_payload.get("ui_dist_managed") is not False:
            fail("custom UI below the versions directory was incorrectly replaced", custom_status)
        config["ui_dist"] = str((install_root / "versions" / version / "ui" / "start-building-app" / "dist").resolve())
        (host_data / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        version_two = "0.0.1-smoke"
        output_two = temp / "out-two"
        ui_two = temp / "ui-two"
        shutil.copytree(ROOT / "ui" / "start-building-app" / "dist", ui_two)
        ui_marker = "AGENTOPS_BUNDLE_SMOKE_UI_V2"
        with (ui_two / "index.html").open("a", encoding="utf-8") as handle:
            handle.write(f"\n<!-- {ui_marker} -->\n")
        built_two = run([
            sys.executable,
            str(BUILDER),
            "--output-dir",
            str(output_two),
            "--ui-dist",
            str(ui_two),
            "--version",
            version_two,
        ])
        if built_two.returncode != 0:
            fail("second bundle build failed", built_two)
        build_two_result = json.loads(built_two.stdout)
        tar_two = next(Path(path) for path in build_two_result["artifacts"] if path.endswith(".tar.gz"))
        bundle_two = extract_bundle(tar_two, temp / "extract-two")

        claim_home = temp / "claim-home"
        claim_install_root = claim_home / ".local" / "share" / "agentops-mis"
        claim_install_root.mkdir(parents=True)
        claim_sentinel = claim_install_root / "unrelated-user-file"
        claim_sentinel.write_text("preserve", encoding="utf-8")
        claim_env = {
            **env,
            "HOME": str(claim_home),
            "AGENTOPS_INSTALL_ROOT": str(claim_install_root),
            "AGENTOPS_BIN_DIR": str(claim_home / ".local" / "bin"),
            "AGENTOPS_APP_DIR": str(claim_home / "Applications"),
            "AGENTOPS_HOST_HOME": str(claim_home / ".agentops" / "host"),
        }
        claimed_install = run(["sh", str(bundle_two / "install.sh")], env=claim_env)
        if claimed_install.returncode == 0 or not claim_sentinel.is_file() or (claim_install_root / "versions").exists():
            fail("installer claimed a non-empty unrelated install root", claimed_install)

        pid_path = host_data / "run" / "host.pid.json"
        lock_descriptor = os.open(lifecycle_lock, os.O_RDWR)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            locked_install = run(["sh", str(bundle_two / "install.sh")], env=env)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        if locked_install.returncode == 0 or (install_root / "versions" / version_two).exists():
            fail("installer ignored the active Host lifecycle lock", locked_install)

        pid_path.write_text('{"pid":"invalid"}\n', encoding="utf-8")
        invalid_pid_install = run(["sh", str(bundle_two / "install.sh")], env=env)
        pid_path.unlink()
        if invalid_pid_install.returncode == 0 or (install_root / "versions" / version_two).exists():
            fail("installer accepted an invalid managed Host PID record", invalid_pid_install)

        pid_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        running_rejected = run(["sh", str(bundle_two / "install.sh")], env=env)
        pid_path.unlink()
        if running_rejected.returncode == 0 or (install_root / "versions" / version_two).exists():
            fail("installer did not reject an update while the managed Host PID was alive", running_rejected)

        install_marker = install_root / ".agentops-mis-install.json"
        install_marker.unlink()
        upgraded = run(["sh", str(bundle_two / "install.sh")], env=env)
        if upgraded.returncode != 0:
            fail("second bundle install failed", upgraded)
        try:
            migrated_install_marker = json.loads(install_marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            migrated_install_marker = {}
        if migrated_install_marker != {
            "schema_version": 1,
            "product": "AgentOps MIS Private Host",
            "managed": True,
        }:
            fail("legacy product install did not migrate to the ownership marker", upgraded)
        upgraded_payload = json.loads(upgraded.stdout)
        if upgraded_payload.get("previous_version") != version:
            fail("upgrade did not retain the previous version pointer", upgraded)
        if installed_cli.stat().st_ino == initial_cli_inode or installed_worker.stat().st_ino == initial_worker_inode:
            fail("upgrade rewrote an installed CLI shim in place instead of replacing it atomically", upgraded)
        if not Path(str(upgraded_payload.get("pre_update_backup_path") or "")).is_file():
            fail("upgrade did not create a verified pre-update ledger backup", upgraded)
        upgraded_launcher_config = json.loads(launcher_config_path.read_text(encoding="utf-8"))
        if (
            upgraded_payload.get("launcher_installed") is not True
            or upgraded_launcher_config.get("version") != version_two
            or upgraded_launcher_config.get("current_path") != str(install_root / "current")
        ):
            fail("upgrade did not refresh the managed launcher", upgraded)
        version_status = run([str(bin_dir / "agentops"), "host", "version"], env=env)
        version_payload = json.loads(version_status.stdout)
        if version_status.returncode != 0 or version_payload.get("version") != version_two or version_payload.get("previous_version") != version:
            fail("installed version provenance is incorrect after upgrade", version_status)
        upgraded_status = run([str(bin_dir / "agentops"), "host", "status"], env=env)
        upgraded_status_payload = json.loads(upgraded_status.stdout)
        expected_upgraded_ui = str((install_root / "versions" / version_two / "ui" / "start-building-app" / "dist").resolve())
        if upgraded_status_payload.get("ui_dist") != expected_upgraded_ui or upgraded_status_payload.get("ui_dist_managed") is not True:
            fail("upgrade continued serving the previous release UI", upgraded_status)
        register_host(bin_dir / "agentops", env)
        upgraded_started = run([str(bin_dir / "agentops"), "host", "start", "--no-workers"], env=env)
        try:
            with urlopen(base_url + "/", timeout=10) as response:
                upgraded_html = response.read().decode("utf-8")
        finally:
            upgraded_stopped = run([str(bin_dir / "agentops"), "host", "stop"], env=env)
            if upgraded_stopped.returncode == 0:
                unregister_host(bin_dir / "agentops")
        if upgraded_started.returncode != 0 or upgraded_stopped.returncode != 0 or ui_marker not in upgraded_html:
            fail("upgraded Host did not serve the current release UI", upgraded_started)
        update_check = run([str(bin_dir / "agentops"), "host", "update", "--check"], env=env)
        if update_check.returncode != 0 or json.loads(update_check.stdout).get("check_only") is not True:
            fail("side-effect-free update check failed", update_check)

        rollback_dry = run([str(bin_dir / "agentops"), "host", "rollback"], env=env)
        if rollback_dry.returncode != 2 or json.loads(rollback_dry.stdout).get("dry_run") is not True:
            fail("binary rollback did not require explicit confirmation", rollback_dry)
        rolled_back = run([str(bin_dir / "agentops"), "host", "rollback", "--confirm-rollback"], env=env)
        if rolled_back.returncode != 0:
            fail("binary rollback failed", rolled_back)
        rollback_payload = json.loads(rolled_back.stdout)
        if rollback_payload.get("to_version") != version or not Path(str(rollback_payload.get("pre_rollback_backup_path") or "")).is_file():
            fail("binary rollback lacked target version or verified pre-rollback backup", rolled_back)
        rolled_back_status = run([str(bin_dir / "agentops"), "host", "version"], env=env)
        rolled_back_payload = json.loads(rolled_back_status.stdout)
        if rolled_back_payload.get("version") != version or rolled_back_payload.get("previous_version") != version_two:
            fail("rollback did not atomically swap current and previous versions", rolled_back_status)
        rolled_back_host_status = run([str(bin_dir / "agentops"), "host", "status"], env=env)
        rolled_back_host_payload = json.loads(rolled_back_host_status.stdout)
        expected_rolled_back_ui = str((install_root / "versions" / version / "ui" / "start-building-app" / "dist").resolve())
        if rolled_back_host_payload.get("ui_dist") != expected_rolled_back_ui or rolled_back_host_payload.get("ui_dist_managed") is not True:
            fail("rollback did not restore the matching release UI", rolled_back_host_status)
        register_host(bin_dir / "agentops", env)
        rolled_back_started = run([str(bin_dir / "agentops"), "host", "start", "--no-workers"], env=env)
        try:
            with urlopen(base_url + "/", timeout=10) as response:
                rolled_back_html = response.read().decode("utf-8")
        finally:
            rolled_back_stopped = run([str(bin_dir / "agentops"), "host", "stop"], env=env)
            if rolled_back_stopped.returncode == 0:
                unregister_host(bin_dir / "agentops")
        if rolled_back_started.returncode != 0 or rolled_back_stopped.returncode != 0 or ui_marker in rolled_back_html:
            fail("rolled-back Host did not serve the restored release UI", rolled_back_started)
        with sqlite3.connect(database) as conn:
            marker = conn.execute("SELECT marker FROM product_upgrade_smoke").fetchone()
        if not marker or marker[0] != "preserved-across-binary-switch" or not sentinel.is_file():
            fail("upgrade or rollback changed Host product data")

        data_marker = host_data / ".agentops-host-data.json"
        if not install_marker.is_file() or not data_marker.is_file() or not lifecycle_lock.is_file():
            fail("installed Host ownership or lifecycle marker is missing")

        lifecycle_lock.unlink()
        lifecycle_lock.symlink_to(lock_symlink_target)
        symlink_lock_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        lifecycle_lock.unlink()
        lifecycle_lock.touch(mode=0o600)
        if (
            symlink_lock_uninstall.returncode == 0
            or not install_root.is_dir()
            or not (bin_dir / "agentops").is_file()
            or lock_symlink_target.read_text(encoding="utf-8") != "preserve"
        ):
            fail("uninstaller followed a symlinked lifecycle lock", symlink_lock_uninstall)

        lock_descriptor = os.open(lifecycle_lock, os.O_RDWR)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            locked_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        if locked_uninstall.returncode == 0 or not install_root.is_dir() or not (bin_dir / "agentops").is_file():
            fail("uninstaller ignored the active Host lifecycle lock", locked_uninstall)

        install_marker_bytes = install_marker.read_bytes()
        install_marker.unlink()
        missing_marker_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        install_marker.write_bytes(install_marker_bytes)
        install_marker.chmod(0o600)
        if missing_marker_uninstall.returncode == 0 or not install_root.is_dir() or not (bin_dir / "agentops").is_file():
            fail("uninstaller accepted a missing product ownership marker", missing_marker_uninstall)

        shim_path = bin_dir / "agentops"
        shim_bytes = shim_path.read_bytes()
        with shim_path.open("ab") as handle:
            handle.write(b"# unexpected content\n")
        modified_shim_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        shim_path.write_bytes(shim_bytes)
        shim_path.chmod(0o755)
        if modified_shim_uninstall.returncode == 0 or not install_root.is_dir() or not shim_path.is_file():
            fail("uninstaller accepted a modified CLI shim", modified_shim_uninstall)

        worker_shim_bytes = installed_worker.read_bytes()
        with installed_worker.open("ab") as handle:
            handle.write(b"# unexpected content\n")
        modified_worker_shim_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        installed_worker.write_bytes(worker_shim_bytes)
        installed_worker.chmod(0o755)
        if modified_worker_shim_uninstall.returncode == 0 or not install_root.is_dir() or not installed_worker.is_file():
            fail("uninstaller accepted a modified worker shim", modified_worker_shim_uninstall)

        dangerous_purge_env = {
            **env,
            "AGENTOPS_HOST_HOME": str(home),
            "AGENTOPS_PURGE_DATA": "true",
        }
        dangerous_purge = run(["sh", str(bundle_two / "uninstall.sh")], env=dangerous_purge_env)
        if dangerous_purge.returncode == 0 or not home.is_dir() or not install_root.is_dir() or not sentinel.is_file():
            fail("uninstaller accepted a dangerous HOME-root data purge", dangerous_purge)

        overlapping_purge_env = {
            **env,
            "AGENTOPS_HOST_HOME": str(bin_dir),
            "AGENTOPS_PURGE_DATA": "true",
        }
        overlapping_purge = run(["sh", str(bundle_two / "uninstall.sh")], env=overlapping_purge_env)
        if overlapping_purge.returncode == 0 or not bin_dir.is_dir() or not shim_path.is_file():
            fail("uninstaller accepted overlapping Host data and binary roots", overlapping_purge)

        pid_path.write_text('{"pid":"invalid"}\n', encoding="utf-8")
        invalid_pid_uninstall = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        if invalid_pid_uninstall.returncode == 0 or not install_root.is_dir() or not (bin_dir / "agentops").is_file():
            fail("uninstaller accepted an invalid managed Host PID record", invalid_pid_uninstall)
        pid_path.unlink()
        uninstalled = run(["sh", str(bundle_two / "uninstall.sh")], env=env)
        if uninstalled.returncode != 0:
            fail("bundle uninstall failed", uninstalled)
        if install_root.exists() or (bin_dir / "agentops").exists() or app_bundle.exists():
            fail("uninstall left product files behind")
        if installed_worker.exists():
            fail("uninstall left agentops-worker behind")
        if not sentinel.is_file():
            fail("uninstall removed user data without explicit purge")

        print(json.dumps({
            "ok": True,
            "operation": "private_host_bundle_smoke",
            "archive_forbidden_scan": "tar_and_zip_passed",
            "managed_host_cleanup": "signal_and_exception_safe",
            "manifest_checksums": "passed",
            "candidate_provenance": "source_ui_and_assets_bound",
            "installed_sbom_notices_and_runbook": "passed",
            "tampered_payload_rejected": True,
            "installed_host_help": "passed",
            "installed_shim_source_shadowing": "rejected",
            "installed_worker_shim": "passed",
            "installed_host_release_env": "passed",
            "installed_macos_launcher": "passed",
            "launcher_absolute_python": "passed",
            "launcher_no_live_workers_default": True,
            "installed_backup_restore_commands": "passed",
            "installed_host_init_and_doctor": "passed",
            "installed_agent_plan_specs": "passed",
            "installed_agent_plan_runtime_verification": "passed",
            "installed_owner_bootstrap_cli": "passed",
            "installed_host_cli_configuration": "passed",
            "installed_worker_service_local_config": "passed",
            "installed_live_readback_client": "passed",
            "running_host_update_rejected": True,
            "lifecycle_locked_install_rejected": True,
            "symlinked_lifecycle_lock_install_rejected": True,
            "invalid_pid_install_rejected": True,
            "unrelated_root_claim_rejected": True,
            "legacy_install_marker_migrated": True,
            "running_host_uninstall_rejected": True,
            "invalid_pid_uninstall_rejected": True,
            "lifecycle_locked_uninstall_rejected": True,
            "symlinked_lifecycle_lock_uninstall_rejected": True,
            "missing_marker_uninstall_rejected": True,
            "modified_shim_uninstall_rejected": True,
            "modified_worker_shim_uninstall_rejected": True,
            "dangerous_root_purge_rejected": True,
            "overlapping_root_purge_rejected": True,
            "two_version_upgrade_and_rollback": "passed",
            "pre_rollback_backup": "passed",
            "pre_update_backup": "passed",
            "upgrade_data_preserved": True,
            "upgrade_ui_followed_current_release": True,
            "upgrade_shims_atomically_replaced": True,
            "rollback_ui_followed_current_release": True,
            "custom_ui_preserved": True,
            "served_ui_followed_upgrade_and_rollback": True,
            "uninstall_preserved_user_data": True,
            "uninstall_removed_launcher": True,
            "network_used": False,
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
