#!/usr/bin/env python3
"""Verify Worker PID reuse/tamper checks fail closed without signaling the process."""

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


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "scripts" / "run_local_stack.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
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


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_status(base_url: str) -> dict:
    payload = get_json(base_url + "/api/workers/status")
    return next((item for item in payload.get("daemons", []) if item.get("adapter") == "mock"), {})


def wait_for(predicate, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return last


def main() -> int:
    failures: list[str] = []
    result: dict = {
        "ok": False,
        "operation": "worker_process_identity_smoke",
        "real_runtime_called": False,
        "token_omitted": True,
    }
    with tempfile.TemporaryDirectory(prefix="agentops-worker-identity-") as tmp:
        tmp_path = Path(tmp)
        runtime_dir = tmp_path / "workers"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "AGENTOPS_DB_PATH": str(tmp_path / "agentops_mis.db"),
            "AGENTOPS_CONFIG": str(tmp_path / "config.json"),
            "AGENTOPS_WORKER_RUNTIME_DIR": str(runtime_dir),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "HERMES_ALLOW_REAL_RUN": "false",
        })
        stack = subprocess.Popen(
            [
                sys.executable,
                str(STACK),
                "--backend-port",
                str(port),
                "--no-ui",
                "--worker",
                "mock",
                "--worker-poll-interval",
                "30",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            verified = wait_for(
                lambda: (
                    item
                    if (item := daemon_status(base_url)).get("process_identity_verified") is True
                    and item.get("worker_status") == "sleeping"
                    else {}
                )
            )
            pid = int(verified.get("pid") or 0)
            if not verified or not pid:
                failures.append(f"Host-managed Worker never reached verified identity: {verified}")
            if verified.get("running") is not True or verified.get("process_identity_status") != "verified":
                failures.append(f"verified Worker status is inconsistent: {verified}")
            if verified.get("management_mode") != "host_stack" or verified.get("control_allowed") is not False:
                failures.append(f"Host ownership boundary is missing: {verified}")

            state_path = runtime_dir / "mock.state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["process_identity_hash"] = "0" * 64
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            unverified = wait_for(lambda: (item if (item := daemon_status(base_url)).get("status") == "identity_unverified" else {}), timeout=10)
            if unverified.get("running") is not False:
                failures.append(f"tampered Worker was still reported running: {unverified}")
            if unverified.get("process_identity_verified") is not False:
                failures.append(f"tampered Worker identity was not rejected: {unverified}")
            if unverified.get("process_source") != "worker_state_unverified":
                failures.append(f"tampered Worker source was not bounded: {unverified}")
            worker_payload = get_json(base_url + "/api/workers/status")
            fleet_payload = get_json(base_url + "/api/workers/fleet")
            if worker_payload.get("running_workers") != 0 or worker_payload.get("unverified_process_claims") != 1:
                failures.append(f"tampered Worker was counted as running capacity: {worker_payload}")
            if (fleet_payload.get("summary") or {}).get("unverified_process_claims") != 1 or fleet_payload.get("status") != "attention":
                failures.append(f"Fleet did not surface the unverified process claim: {fleet_payload}")

            stop_status, stop_payload = post_json(base_url + "/api/workers/local/stop", {"adapter": "mock"})
            restart_status, restart_payload = post_json(base_url + "/api/workers/local/restart", {"adapter": "mock"})
            if stop_status != 409 or stop_payload.get("error") != "worker_managed_by_host":
                failures.append(f"tampered Host Worker stop was not rejected: {stop_status} {stop_payload}")
            if restart_status != 409 or restart_payload.get("error") != "worker_managed_by_host":
                failures.append(f"tampered Host Worker restart was not rejected: {restart_status} {restart_payload}")
            if not process_alive(pid):
                failures.append("Worker process was signaled after identity verification failed")
            if stack.poll() is not None:
                failures.append("Host stack exited after rejected Worker controls")

            result.update({
                "verified_before_tamper": verified.get("process_identity_verified") is True,
                "status_after_tamper": unverified.get("status"),
                "running_after_tamper": unverified.get("running"),
                "stop_failed_closed": stop_status == 409,
                "restart_failed_closed": restart_status == 409,
                "worker_survived_rejected_controls": process_alive(pid),
                "running_workers_after_tamper": worker_payload.get("running_workers"),
                "fleet_unverified_process_claims": (fleet_payload.get("summary") or {}).get("unverified_process_claims"),
            })
        except Exception as exc:
            failures.append(str(exc))
        finally:
            stack.terminate()
            try:
                stdout, stderr = stack.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stack.kill()
                stdout, stderr = stack.communicate(timeout=5)
            combined = (stdout or "") + (stderr or "")
            if any(marker in combined for marker in ("Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_")):
                failures.append("stack output contained token-like material")

    result["failures"] = failures
    result["ok"] = not failures
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
