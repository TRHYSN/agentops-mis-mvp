#!/usr/bin/env python3
"""Run the local AgentOps MIS backend, UI, and bounded worker processes."""
from __future__ import annotations

import argparse
import json
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_connector_service import (  # noqa: E402
    RelayConnectorServiceError,
    load_connector_config,
)
from agentops_mis_cli.runtime_lock import (  # noqa: E402
    acquire_runtime_lock,
    release_runtime_lock,
)


UI_DIR = ROOT / "ui" / "start-building-app"
CLI = ROOT / "scripts" / "agentops"
LIVE_ADAPTERS = {"hermes", "openclaw"}
PROCESS_SHUTDOWN_GRACE_SECONDS = 6.0
PROCESS_KILL_GRACE_SECONDS = 2.0
WORKER_DENIED_HUMAN_CONTROL_ENV = frozenset({
    "AGENTOPS_ADMIN_KEY",
    "AGENTOPS_ACCEPTANCE_PASSWORD",
    "AGENTOPS_OWNER_SETUP_CODE",
    "AGENTOPS_OWNER_PASSWORD",
    "AGENTOPS_HUMAN_SESSION",
    "AGENTOPS_HUMAN_SESSION_TOKEN",
    "AGENTOPS_CSRF_TOKEN",
})
SUBPROCESS_BASE_ENV = frozenset({
    "ALL_PROXY", "CURL_CA_BUNDLE", "HOME", "HTTP_PROXY", "HTTPS_PROXY",
    "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "NO_PROXY", "PATH",
    "PYTHONPATH", "REQUESTS_CA_BUNDLE", "SHELL", "SSL_CERT_DIR",
    "SSL_CERT_FILE", "TEMP", "TMP", "TMPDIR", "USER", "VIRTUAL_ENV",
    "all_proxy", "http_proxy", "https_proxy", "no_proxy",
})
SUBPROCESS_AGENTOPS_ENV = frozenset({
    "AGENTOPS_ADAPTER_MAX_ATTEMPTS", "AGENTOPS_ADAPTER_RETRY_DELAY_SEC",
    "AGENTOPS_AGENT_ID", "AGENTOPS_API_KEY", "AGENTOPS_BASE_URL",
    "AGENTOPS_CONFIG", "AGENTOPS_DEPLOYMENT_MODE",
    "AGENTOPS_LOCAL_DEMO_DEFAULT_URL", "AGENTOPS_MOCK_FAILURES_BEFORE_SUCCESS",
    "AGENTOPS_REQUEST_TIMEOUT", "AGENTOPS_SESSION_REFRESH_MARGIN_SEC",
    "AGENTOPS_SESSION_SCOPES", "AGENTOPS_SESSION_TTL_SEC", "AGENTOPS_TASK_ID",
    "AGENTOPS_WORKSPACE_ID", "AGENTOPS_WORKER_CWD",
    "AGENTOPS_WORKER_MANAGEMENT_MODE",
    "AGENTOPS_WORKER_RUNTIME_DIR", "AGENTOPS_WORKER_STATE_PATH",
})
WORKER_RUNTIME_ENV = frozenset({
    "HERMES_GATEWAY_URL", "HERMES_MAX_TOKENS", "HERMES_MODEL",
    "HERMES_TIMEOUT", "OPENCLAW_AGENT", "OPENCLAW_BIN", "OPENCLAW_TIMEOUT",
})
AUXILIARY_ENV = frozenset({"VITE_AGENTOPS_PROXY_TARGET"})
AUXILIARY_PREFIXES = ("NPM_CONFIG_", "npm_config_")


def projected_environment(
    base_env: dict[str, str],
    *,
    include_agentops: bool = False,
    extra_allowed: frozenset[str] = frozenset(),
    allowed_prefixes: tuple[str, ...] = (),
) -> dict[str, str]:
    allowed = SUBPROCESS_BASE_ENV | extra_allowed
    if include_agentops:
        allowed |= SUBPROCESS_AGENTOPS_ENV
    return {
        key: value
        for key, value in base_env.items()
        if key not in WORKER_DENIED_HUMAN_CONTROL_ENV
        and (key in allowed or key.startswith(allowed_prefixes))
    }


def without_human_control_secrets(base_env: dict[str, str]) -> dict[str, str]:
    return projected_environment(base_env, extra_allowed=AUXILIARY_ENV, allowed_prefixes=AUXILIARY_PREFIXES)


def cli_environment(base_env: dict[str, str]) -> dict[str, str]:
    return projected_environment(base_env, include_agentops=True)


def worker_environment(base_env: dict[str, str], adapter: str) -> dict[str, str]:
    worker_env = projected_environment(base_env, include_agentops=True, extra_allowed=WORKER_RUNTIME_ENV)
    worker_env["AGENTOPS_AGENT_ID"] = f"agt_worker_local_stack_{adapter}"
    worker_env["AGENTOPS_WORKER_MANAGEMENT_MODE"] = "host_stack"
    return worker_env


def request_shutdown(_signum, _frame) -> None:
    raise KeyboardInterrupt


def close_ready_fd(ready_fd: int | None) -> None:
    if ready_fd is None:
        return
    try:
        os.close(ready_fd)
    except OSError:
        pass


def inherited_runtime_fds(
    runtime_lock_fd: int | None,
    *additional_fds: int,
) -> tuple[int, ...]:
    return tuple(
        descriptor
        for descriptor in (runtime_lock_fd, *additional_fds)
        if isinstance(descriptor, int) and not isinstance(descriptor, bool) and descriptor >= 3
    )


def signal_ready(ready_fd: int | None) -> None:
    if ready_fd is None:
        return
    try:
        if os.write(ready_fd, b"\x01") != 1:
            raise RuntimeError("Local stack readiness signal failed")
    finally:
        close_ready_fd(ready_fd)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def gateway_ready(base_url: str, api_key: str = "") -> bool:
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/api/agent-gateway/status")
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(request, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("provider") == "agent_gateway"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def wait_ready(check, label: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise RuntimeError(f"{label} did not become ready")


def terminate_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for _label, process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    graceful_deadline = time.monotonic() + PROCESS_SHUTDOWN_GRACE_SECONDS
    for _label, process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0.0, graceful_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for _label, process in reversed(processes):
        if process.poll() is None:
            process.kill()
    kill_deadline = time.monotonic() + PROCESS_KILL_GRACE_SECONDS
    for _label, process in reversed(processes):
        if process.poll() is None:
            process.wait(timeout=max(0.0, kill_deadline - time.monotonic()))


def worker_command(adapter: str, poll_interval: float, confirm_live_workers: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "agentops_mis_cli.worker",
        "--adapter",
        adapter,
        "--agent-id",
        f"agt_worker_local_stack_{adapter}",
        "--poll-interval",
        str(poll_interval),
        "--max-tasks",
        "0",
        "--continue-on-error",
        "--write-state",
    ]
    if adapter in LIVE_ADAPTERS and confirm_live_workers:
        command.append("--confirm-run")
    return command


def relay_connector_config(args: argparse.Namespace) -> dict | None:
    relay_paths = (
        args.relay_config,
        args.relay_secrets,
        args.relay_epoch_state,
        args.relay_status,
    )
    if not any(relay_paths):
        return None
    if not all(relay_paths):
        raise RuntimeError("Relay connector paths must be supplied as one managed set")
    if not args.relay_config.exists() and not args.relay_config.is_symlink():
        return None
    try:
        config = load_connector_config(args.relay_config)
    except RelayConnectorServiceError as exc:
        raise RuntimeError("Relay connector configuration failed closed") from exc
    if config.get("enabled") is False:
        return None
    if config.get("host_http_port") != args.backend_port:
        raise RuntimeError("Relay connector Host port does not match the managed backend")
    return config


def relay_connector_command(args: argparse.Namespace, ready_fd: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentops_mis_cli.relay_connector_service",
        "--config",
        str(args.relay_config),
        "--secrets",
        str(args.relay_secrets),
        "--epoch-state",
        str(args.relay_epoch_state),
        "--status",
        str(args.relay_status),
        "--managed-by-host-stack",
        "--ready-fd",
        str(ready_fd),
    ]


def private_status_snapshot(path: Path) -> tuple[dict, tuple[int, int, int] | None]:
    descriptor = -1
    try:
        parent = path.parent.lstat()
        if (
            path.parent.is_symlink()
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            return {}, None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > 16 * 1024
        ):
            return {}, None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}, None
        return payload, (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
    except (OSError, UnicodeError, ValueError):
        return {}, None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def wait_relay_connector_started(
    process: subprocess.Popen,
    ready_fd: int,
    status_path: Path,
    timeout: float = 8.0,
) -> None:
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Relay connector exited during startup")
            readable, _writable, _exceptional = select.select([ready_fd], [], [], 0.05)
            if not readable:
                continue
            marker = os.read(ready_fd, 1)
            if marker != b"\x01":
                raise RuntimeError("Relay connector closed its readiness channel")
            status, _signature = private_status_snapshot(status_path)
            if (
                status.get("enabled") is True
                and status.get("host_lifecycle_integrated") is True
                and status.get("host_tls_ready") is True
                and isinstance(status.get("current_epoch"), int)
                and status["current_epoch"] > 0
                and status.get("state") in {"connecting", "connected", "backoff"}
            ):
                return
            raise RuntimeError("Relay connector published invalid startup status")
        raise RuntimeError("Relay connector did not complete its startup handshake")
    finally:
        os.close(ready_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local AgentOps MIS backend, UI, and worker loop.")
    parser.add_argument("--install-ui", action="store_true", help="Run npm ci --prefer-offline if UI dependencies are missing.")
    parser.add_argument("--production-ui", action="store_true", help="Serve the built React UI from the backend instead of starting Vite.")
    parser.add_argument("--build-ui", action="store_true", help="Build the React UI before starting; implies --production-ui.")
    parser.add_argument("--ui-dist", type=Path, help="Use an existing packaged UI directory; implies --production-ui.")
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8787)
    parser.add_argument("--ui-host", default="127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=19001)
    parser.add_argument("--no-ui", action="store_true", help="Start the backend and workers without the browser UI.")
    parser.add_argument("--worker", action="append", choices=["mock", "hermes", "openclaw"], help="Worker adapter to start. Repeat for multiple workers; defaults to mock.")
    parser.add_argument("--no-workers", action="store_true", help="Do not start repo-local worker processes.")
    parser.add_argument("--confirm-live-workers", action="store_true", help="Explicitly allow requested Hermes/OpenClaw workers to execute live tasks.")
    parser.add_argument("--worker-poll-interval", type=float, default=5.0)
    parser.add_argument("--configure-cli", action="store_true", help="Explicitly update the saved CLI base URL/workspace for this local stack.")
    parser.add_argument("--relay-config", type=Path)
    parser.add_argument("--relay-secrets", type=Path)
    parser.add_argument("--relay-epoch-state", type=Path)
    parser.add_argument("--relay-status", type=Path)
    parser.add_argument("--stack-ready-fd", type=int)
    parser.add_argument(
        "--runtime-lock",
        type=Path,
        help="Hold an exclusive lock in a private runtime directory for the stack lifetime.",
    )
    args = parser.parse_args()
    if args.stack_ready_fd is not None and args.stack_ready_fd < 3:
        parser.error("--stack-ready-fd must be an inherited private descriptor")
    if args.build_ui:
        args.production_ui = True
    if args.ui_dist:
        args.production_ui = True
    if args.no_ui and args.production_ui:
        parser.error("--no-ui cannot be combined with --production-ui or --build-ui")
    signal.signal(signal.SIGTERM, request_shutdown)

    backend_url = f"http://{args.backend_host}:{args.backend_port}"
    workers = [] if args.no_workers else list(dict.fromkeys(args.worker or ["mock"]))
    live_workers = sorted(set(workers) & LIVE_ADAPTERS)
    if live_workers and not args.confirm_live_workers:
        parser.error(
            "Hermes/OpenClaw workers require --confirm-live-workers; "
            "use --worker mock or --no-workers for a non-live stack"
        )
    if args.backend_host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("run_local_stack.py is local-only; keep --backend-host on loopback")
    managed_relay_config = relay_connector_config(args)

    env = os.environ.copy()
    env["AGENTOPS_BASE_URL"] = backend_url
    env["AGENTOPS_LOCAL_DEMO_DEFAULT_URL"] = backend_url
    env["AGENTOPS_WORKSPACE_ID"] = env.get("AGENTOPS_WORKSPACE_ID", "local-demo")
    env["VITE_AGENTOPS_PROXY_TARGET"] = backend_url
    auxiliary_env = without_human_control_secrets(env)
    cli_env = cli_environment(env)
    gateway_api_key = env.get("AGENTOPS_API_KEY", "").strip()
    production_ui_dist = (args.ui_dist or (UI_DIR / "dist")).expanduser().resolve()
    processes: list[tuple[str, subprocess.Popen]] = []
    stack_ready_fd = args.stack_ready_fd
    runtime_lock_fd: int | None = None

    try:
        if args.runtime_lock is not None:
            runtime_lock_fd = acquire_runtime_lock(args.runtime_lock)
        if args.production_ui:
            if args.build_ui:
                if not UI_DIR.exists():
                    raise RuntimeError(f"missing UI directory: {UI_DIR}")
                if not (UI_DIR / "node_modules").exists():
                    if args.install_ui:
                        subprocess.run(["npm", "ci", "--prefer-offline"], cwd=UI_DIR, env=auxiliary_env, check=True)
                    else:
                        raise RuntimeError("UI dependencies missing. Add --install-ui before building the production UI")
                subprocess.run(["npm", "run", "build"], cwd=UI_DIR, env=auxiliary_env, check=True)
            if not (production_ui_dist / "index.html").is_file():
                raise RuntimeError(f"production UI missing at {production_ui_dist}. Run with --build-ui or pass --ui-dist")
        if port_open(args.backend_port, args.backend_host):
            if args.production_ui:
                raise RuntimeError(
                    f"port {args.backend_port} is already in use; stop the existing backend before starting production UI mode"
                )
            if not gateway_ready(backend_url, gateway_api_key):
                raise RuntimeError(f"port {args.backend_port} is occupied by a non-AgentOps service")
            print(f"backend already running at {backend_url}/dashboard")
        else:
            backend_command = [sys.executable, "server.py", "--host", args.backend_host, "--port", str(args.backend_port)]
            if args.production_ui:
                backend_command.extend(["--ui-dist", str(production_ui_dist)])
            backend = subprocess.Popen(
                backend_command,
                cwd=ROOT,
                env=env,
                pass_fds=inherited_runtime_fds(runtime_lock_fd),
            )
            processes.append(("backend", backend))
            wait_ready(lambda: gateway_ready(backend_url, gateway_api_key), f"backend on {backend_url}")

        if managed_relay_config is not None:
            ready_read_fd, ready_write_fd = os.pipe()
            try:
                try:
                    relay_connector = subprocess.Popen(
                        relay_connector_command(args, ready_write_fd),
                        cwd=ROOT,
                        env=projected_environment(env),
                        pass_fds=inherited_runtime_fds(runtime_lock_fd, ready_write_fd),
                    )
                    processes.append(("relay-connector", relay_connector))
                except Exception:
                    os.close(ready_read_fd)
                    raise
            finally:
                os.close(ready_write_fd)
            wait_relay_connector_started(relay_connector, ready_read_fd, args.relay_status)

        if args.configure_cli:
            configured = subprocess.run(
                [str(CLI), "login", "--base-url", backend_url, "--workspace-id", env["AGENTOPS_WORKSPACE_ID"]],
                cwd=ROOT,
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if configured.returncode != 0:
                raise RuntimeError("failed to update local CLI connection; no token output was retained")

        if not args.no_ui and not args.production_ui:
            if not UI_DIR.exists():
                raise RuntimeError(f"missing UI directory: {UI_DIR}")
            if not (UI_DIR / "node_modules").exists():
                if args.install_ui:
                    subprocess.run(["npm", "ci", "--prefer-offline"], cwd=UI_DIR, env=auxiliary_env, check=True)
                else:
                    raise RuntimeError("UI dependencies missing. Run: python3 scripts/run_local_stack.py --install-ui")
            if port_open(args.ui_port, args.ui_host):
                print(f"ui already running at http://{args.ui_host}:{args.ui_port}/")
            else:
                ui = subprocess.Popen(
                    ["npm", "run", "dev", "--", "--host", args.ui_host, "--port", str(args.ui_port)],
                    cwd=UI_DIR,
                    env=auxiliary_env,
                    pass_fds=inherited_runtime_fds(runtime_lock_fd),
                )
                processes.append(("ui", ui))
                wait_ready(lambda: port_open(args.ui_port, args.ui_host), f"UI on {args.ui_host}:{args.ui_port}")

        for adapter in workers:
            worker_env = worker_environment(env, adapter)
            worker = subprocess.Popen(
                worker_command(adapter, args.worker_poll_interval, args.confirm_live_workers),
                cwd=ROOT,
                env=worker_env,
                pass_fds=inherited_runtime_fds(runtime_lock_fd),
            )
            processes.append((f"worker:{adapter}", worker))
            time.sleep(0.2)
            if worker.poll() is not None:
                raise RuntimeError(f"{adapter} worker exited during startup with code {worker.returncode}")

        for label, process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"{label} exited during stack startup with code {process.returncode}")
        signal_ready(stack_ready_fd)
        stack_ready_fd = None

        print("")
        print("AgentOps MIS local stack is running:")
        print(f"  backend: http://{args.backend_host}:{args.backend_port}/dashboard")
        if args.production_ui:
            print(f"  workspace: {backend_url}/workspace")
            print(f"  workers:   {backend_url}/workspace/workers")
            print("  UI mode:   production same-origin")
        elif not args.no_ui:
            print(f"  workspace: http://{args.ui_host}:{args.ui_port}/workspace")
            print(f"  workers:   http://{args.ui_host}:{args.ui_port}/workspace/workers")
            print("  UI mode:   Vite development")
        print(f"  adapters:  {', '.join(workers) if workers else 'none'}")
        print(f"  live mode: {'confirmed' if live_workers else 'off'}")
        print(f"  relay:     {'Host-managed' if managed_relay_config is not None else 'off'}")
        if not args.configure_cli:
            print(f"  CLI check: AGENTOPS_BASE_URL={backend_url} agentops doctor")
            print(f"  save URL:  agentops login --base-url {backend_url} --workspace-id {env['AGENTOPS_WORKSPACE_ID']}")
        print("")
        print("Press Ctrl-C here to stop processes started by this script.")

        while processes:
            for label, process in list(processes):
                returncode = process.poll()
                if returncode is None:
                    continue
                if label == "relay-connector":
                    raise RuntimeError(f"{label} exited with code {returncode}")
                if returncode != 0:
                    raise RuntimeError(f"{label} exited with code {returncode}")
                processes.remove((label, process))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping AgentOps MIS local stack")
    finally:
        close_ready_fd(stack_ready_fd)
        terminate_processes(processes)
        release_runtime_lock(runtime_lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
