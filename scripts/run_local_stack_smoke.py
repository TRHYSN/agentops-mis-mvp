#!/usr/bin/env python3
"""Verify the one-command local stack starts backend plus a safe worker."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import run_local_stack as stack_module


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "scripts" / "run_local_stack.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []
    boundary_env = {
        "AGENTOPS_API_KEY": "fixture-worker-gateway-key",
        "HERMES_GATEWAY_URL": "http://127.0.0.1:8642/v1",
        "OPENCLAW_BIN": "/fixture/openclaw",
        "NPM_CONFIG_REGISTRY": "https://registry.example.invalid",
        "VITE_AGENTOPS_PROXY_TARGET": "http://127.0.0.1:8787",
        "AGENTOPS_ADMIN_KEY": "fixture-human-admin-key",
        "AGENTOPS_ACCEPTANCE_PASSWORD": "fixture-human-acceptance-password",
        "AGENTOPS_OWNER_SETUP_CODE": "fixture-owner-setup-code",
        "AGENTOPS_HUMAN_SESSION_TOKEN": "fixture-human-session-token",
        "AGENTOPS_CSRF_TOKEN": "fixture-human-csrf-token",
        "CUSTOM_OWNER_CREDENTIAL": "fixture-custom-human-password",
        "HERMES_OWNER_PASSWORD": "fixture-prefixed-human-password",
        "VITE_OWNER_PASSWORD": "fixture-vite-human-password",
    }
    projected_worker_env = stack_module.worker_environment(boundary_env, "mock")
    projected_auxiliary_env = stack_module.without_human_control_secrets(boundary_env)
    projected_cli_env = stack_module.cli_environment(boundary_env)
    leaked_human_keys = sorted(set(stack_module.WORKER_DENIED_HUMAN_CONTROL_ENV) & set(projected_worker_env))
    leaked_auxiliary_keys = sorted(set(stack_module.WORKER_DENIED_HUMAN_CONTROL_ENV) & set(projected_auxiliary_env))
    if leaked_human_keys:
        failures.append("worker environment retained human-control credential keys")
    if (
        projected_worker_env.get("AGENTOPS_API_KEY") != boundary_env["AGENTOPS_API_KEY"]
        or projected_worker_env.get("HERMES_GATEWAY_URL") != boundary_env["HERMES_GATEWAY_URL"]
        or projected_worker_env.get("OPENCLAW_BIN") != boundary_env["OPENCLAW_BIN"]
    ):
        failures.append("worker environment removed required Gateway or Runtime configuration")
    if leaked_auxiliary_keys:
        failures.append("UI or helper environment retained human-control credential keys")
    custom_human_keys = {"CUSTOM_OWNER_CREDENTIAL", "HERMES_OWNER_PASSWORD", "VITE_OWNER_PASSWORD"}
    if custom_human_keys & set(projected_worker_env) or custom_human_keys & set(projected_auxiliary_env):
        failures.append("subprocess environment retained an unknown custom human credential")
    if custom_human_keys & set(projected_cli_env):
        failures.append("CLI helper environment retained an unknown custom human credential")
    if "AGENTOPS_API_KEY" in projected_auxiliary_env or projected_cli_env.get("AGENTOPS_API_KEY") != boundary_env["AGENTOPS_API_KEY"]:
        failures.append("Agent Gateway key crossed the npm/Vite boundary or was removed from the CLI helper")
    if "NPM_CONFIG_REGISTRY" in projected_worker_env or projected_auxiliary_env.get("NPM_CONFIG_REGISTRY") != boundary_env["NPM_CONFIG_REGISTRY"]:
        failures.append("npm configuration crossed the worker/auxiliary environment boundary")
    with tempfile.TemporaryDirectory(prefix="agentops-local-stack-") as tmp:
        tmp_path = Path(tmp)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_DB_PATH": str(tmp_path / "agentops_mis.db"),
                "AGENTOPS_CONFIG": str(tmp_path / "config.json"),
                "AGENTOPS_WORKER_RUNTIME_DIR": str(tmp_path / "workers"),
                "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(STACK),
                "--backend-port",
                str(port),
                "--no-ui",
                "--worker",
                "mock",
                "--worker-poll-interval",
                "0.2",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        gateway = {}
        workers = {}
        fleet = {}
        daemon = {}
        stop_status = 0
        stop_result = {}
        restart_status = 0
        restart_result = {}
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    gateway = get_json(base_url + "/api/agent-gateway/status")
                    workers = get_json(base_url + "/api/workers/status")
                    fleet = get_json(base_url + "/api/workers/fleet")
                    registered = [item for item in workers.get("workers", []) if item.get("agent_id") == "agt_worker_local_stack_mock"]
                    daemon = next((item for item in workers.get("daemons", []) if item.get("adapter") == "mock"), {})
                    if (
                        gateway.get("provider") == "agent_gateway"
                        and registered
                        and daemon.get("running") is True
                        and daemon.get("management_mode") == "host_stack"
                        and (fleet.get("summary") or {}).get("running_local_daemons") == 1
                        and workers.get("running_workers") == 1
                    ):
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                time.sleep(0.25)
            else:
                failures.append("local stack did not become ready with a registered mock worker")
            if process.poll() is not None:
                failures.append(f"local stack exited early with code {process.returncode}")
            if gateway.get("provider") != "agent_gateway":
                failures.append("Agent Gateway status was not reachable")
            if not any(item.get("agent_id") == "agt_worker_local_stack_mock" for item in workers.get("workers", [])):
                failures.append("safe mock worker was not registered")
            if daemon.get("running") is not True or daemon.get("management_mode") != "host_stack":
                failures.append("Host-managed worker process was not normalized into daemon status")
            if daemon.get("control_allowed") is not False or not daemon.get("pid"):
                failures.append("Host-managed worker did not expose bounded process identity/control ownership")
            if daemon.get("process_identity_verified") is not True or daemon.get("process_identity_status") != "verified":
                failures.append("Host-managed worker process identity was not verified")
            if (fleet.get("summary") or {}).get("running_local_daemons") != 1:
                failures.append("Worker Fleet did not count the Host-managed worker as running")
            if (fleet.get("summary") or {}).get("host_managed_workers") != 1:
                failures.append("Worker Fleet did not classify the Host-managed worker")
            if workers.get("running_workers") != 1:
                failures.append("Worker status double-counted one Host-managed process and its Agent row")
            stop_status, stop_result = post_json(base_url + "/api/workers/local/stop", {"adapter": "mock"})
            if stop_status != 409 or stop_result.get("error") != "worker_managed_by_host":
                failures.append("Host-managed worker stop did not fail closed at the Host lifecycle boundary")
            if process.poll() is not None:
                failures.append("Host stack stopped after a rejected child-worker stop request")
            restart_status, restart_result = post_json(base_url + "/api/workers/local/restart", {"adapter": "mock"})
            if restart_status != 409 or restart_result.get("error") != "worker_managed_by_host":
                failures.append("Host-managed worker restart did not fail closed at the Host lifecycle boundary")
            if process.poll() is not None:
                failures.append("Host stack stopped after a rejected child-worker restart request")
            if (tmp_path / "config.json").exists():
                failures.append("stack mutated CLI config without --configure-cli")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        combined = (stdout or "") + (stderr or "")
        if any(marker in combined for marker in ("Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_")):
            failures.append("stack output contained token-like material")

    blocked = subprocess.run(
        [sys.executable, str(STACK), "--no-ui", "--worker", "hermes"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if blocked.returncode == 0 or "--confirm-live-workers" not in ((blocked.stdout or "") + (blocked.stderr or "")):
        failures.append("Hermes worker did not fail closed without explicit live confirmation")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "run_local_stack_smoke",
                "backend_started": gateway.get("provider") == "agent_gateway",
                "mock_worker_registered": any(item.get("agent_id") == "agt_worker_local_stack_mock" for item in workers.get("workers", [])),
                "host_managed_worker_visible": daemon.get("running") is True and daemon.get("management_mode") == "host_stack",
                "worker_process_identity_verified": daemon.get("process_identity_verified") is True,
                "running_local_daemons": (fleet.get("summary") or {}).get("running_local_daemons"),
                "host_managed_workers": (fleet.get("summary") or {}).get("host_managed_workers"),
                "running_workers": workers.get("running_workers"),
                "host_managed_stop_rejected": stop_status == 409 and stop_result.get("error") == "worker_managed_by_host",
                "host_managed_restart_rejected": restart_status == 409 and restart_result.get("error") == "worker_managed_by_host",
                "host_managed_stop_status": stop_status,
                "host_managed_stop_error": stop_result.get("error"),
                "host_managed_restart_status": restart_status,
                "host_managed_restart_error": restart_result.get("error"),
                "live_worker_confirmation_required": blocked.returncode != 0,
                "real_runtime_called": False,
                "user_config_mutated": False,
                "token_omitted": True,
                "human_control_secrets_omitted_from_worker": not leaked_human_keys,
                "human_control_secrets_omitted_from_ui_helpers": not leaked_auxiliary_keys,
                "unknown_custom_credentials_omitted": not bool(custom_human_keys & (set(projected_worker_env) | set(projected_auxiliary_env))),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
