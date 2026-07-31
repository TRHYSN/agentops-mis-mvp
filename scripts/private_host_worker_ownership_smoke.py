#!/usr/bin/env python3
"""Verify Private Host refuses duplicate local Worker ownership."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_host(env: dict[str, str], *args: str, expected: tuple[int, ...] = (0,)) -> tuple[int, dict]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=70,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        payload = {}
    if process.returncode not in expected:
        raise RuntimeError(f"host command exited {process.returncode}; output omitted")
    return process.returncode, payload


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    fake_worker: subprocess.Popen | None = None
    host_started = False

    with tempfile.TemporaryDirectory(prefix="agentops-host-worker-ownership-") as tmp:
        tmp_path = Path(tmp)
        host_home = tmp_path / "host"
        ui_dist = tmp_path / "ui"
        ui_dist.mkdir()
        (ui_dist / "index.html").write_text("<!doctype html><div id='root'>HOST_FIXTURE</div>\n", encoding="utf-8")
        port = free_port()
        env = os.environ.copy()
        env["AGENTOPS_HOST_HOME"] = str(host_home)
        try:
            _code, init_payload = run_host(
                env,
                "init",
                "--port",
                str(port),
                "--workspace-id",
                "worker-ownership-smoke",
                "--ui-dist",
                str(ui_dist),
            )
            if init_payload.get("ok") is not True:
                failures.append("temporary Host initialization failed")

            fake_worker = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                    "-m",
                    "agentops_mis_cli.worker",
                    "--adapter",
                    "hermes",
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.1)

            conflict_code, conflict = run_host(
                env,
                "start",
                "--worker",
                "hermes",
                "--confirm-live-workers",
                expected=(2,),
            )
            conflict_rows = conflict.get("conflicts") if isinstance(conflict.get("conflicts"), list) else []
            hermes_conflict = next(
                (row for row in conflict_rows if isinstance(row, dict) and row.get("adapter") == "hermes"),
                {},
            )
            conflict_pids = hermes_conflict.get("pids") if isinstance(hermes_conflict.get("pids"), list) else []
            conflict_ok = (
                conflict_code == 2
                and conflict.get("error") == "worker_ownership_conflict"
                and fake_worker.pid in conflict_pids
                and hermes_conflict.get("process_command_omitted") is True
                and conflict.get("token_omitted") is True
                and conflict.get("live_execution_performed") is False
                and conflict.get("remediation", {}).get("automatic_process_termination") is False
                and not port_open(port)
            )
            evidence["duplicate_adapter"] = {
                "rejected": conflict_ok,
                "adapter": hermes_conflict.get("adapter"),
                "fake_worker_pid_detected": fake_worker.pid in conflict_pids,
                "process_command_omitted": hermes_conflict.get("process_command_omitted") is True,
                "host_port_closed": not port_open(port),
            }
            if not conflict_ok:
                failures.append("duplicate adapter ownership did not fail closed")

            _code, external_owner = run_host(env, "start", "--no-workers")
            host_started = external_owner.get("ok") is True and port_open(port)
            evidence["external_worker_mode"] = {
                "started": host_started,
                "workers": external_owner.get("workers"),
                "existing_worker_preserved": fake_worker.poll() is None,
            }
            if not host_started or external_owner.get("workers") != [] or fake_worker.poll() is not None:
                failures.append("--no-workers did not preserve the external Worker ownership mode")
            _code, stopped = run_host(env, "stop")
            host_started = False
            if stopped.get("status") not in {"stopped", "not_running"}:
                failures.append("temporary no-workers Host did not stop")

            terminate(fake_worker)
            fake_worker = None

            _code, mock_started = run_host(env, "start", "--worker", "mock")
            host_started = mock_started.get("ok") is True and port_open(port)
            evidence["host_owned_worker_mode"] = {
                "started": host_started,
                "workers": mock_started.get("workers"),
            }
            if not host_started or mock_started.get("workers") != ["mock"]:
                failures.append("conflict-free Host-owned mock Worker did not start")
            _code, stopped = run_host(env, "stop")
            host_started = False
            if stopped.get("status") not in {"stopped", "not_running"}:
                failures.append("temporary mock Worker Host did not stop")
        finally:
            if host_started:
                try:
                    run_host(env, "stop", expected=(0, 1, 2))
                except Exception:
                    pass
            if fake_worker is not None:
                terminate(fake_worker)

    output = {
        "ok": not failures,
        "operation": "private_host_worker_ownership_smoke",
        "evidence": evidence,
        "failures": failures,
        "temporary_host": True,
        "temporary_database": True,
        "real_runtime_called": False,
        "process_commands_omitted": True,
        "credential_values_omitted": True,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
