#!/usr/bin/env python3
"""Smoke-test pip source installation of the AgentOps CLI package."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except FileNotFoundError as exc:
        return CompletedProcess(cmd, 127, "", str(exc))


def server_unreachable(proc: subprocess.CompletedProcess[str]) -> bool:
    text = f"{proc.stdout}\n{proc.stderr}"
    return proc.returncode != 0 and "Cannot reach http://127.0.0.1:" in text


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentops-pip-install-") as tmp:
        tmp_path = Path(tmp)
        venv_path = tmp_path / "venv"
        config_path = tmp_path / "config.json"
        base_url = os.environ.get("AGENTOPS_PIP_SMOKE_BASE_URL", "http://127.0.0.1:8787")
        env = os.environ.copy()
        env["AGENTOPS_CONFIG"] = str(config_path)
        env.pop("AGENTOPS_API_KEY", None)

        uv = shutil.which("uv")
        create_cmd = [uv, "venv", str(venv_path)] if uv else [sys.executable, "-m", "venv", str(venv_path)]
        create = run(create_cmd, cwd=ROOT, env=env)
        if create.returncode != 0:
            print(create.stderr or create.stdout, file=sys.stderr)
            return 1

        bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
        executable_suffix = ".exe" if os.name == "nt" else ""
        python = bin_dir / f"python{executable_suffix}"
        agentops = bin_dir / f"agentops{executable_suffix}"
        agentops_worker = bin_dir / f"agentops-worker{executable_suffix}"

        install_cmd = [uv, "pip", "install", "--python", str(python), str(ROOT)] if uv else [str(python), "-m", "pip", "install", str(ROOT)]
        install = run(install_cmd, cwd=tmp_path, env=env)
        help_run = run([str(agentops), "--help"], cwd=tmp_path, env=env)
        worker_entry_help_run = run([str(agentops_worker), "--help"], cwd=tmp_path, env=env)
        login_run = run(
            [
                str(agentops),
                "login",
                "--base-url",
                base_url,
                "--workspace-id",
                "local-demo",
                "--agent-id",
                "agt_pip_cli_smoke",
            ],
            cwd=tmp_path,
            env=env,
        )
        status_run = run([str(agentops), "status"], cwd=tmp_path, env=env)
        worker_status_run = run([str(agentops), "worker", "status"], cwd=tmp_path, env=env)
        worker_preflight_run = run([str(agentops), "worker", "preflight", "--adapter", "mock"], cwd=tmp_path, env=env)
        worker_service_check_help_run = run([str(agentops), "worker", "service-check", "--help"], cwd=tmp_path, env=env)
        worker_service_install_help_run = run([str(agentops), "worker", "service-install", "--help"], cwd=tmp_path, env=env)
        worker_service_control_help_run = run([str(agentops), "worker", "service-control", "--help"], cwd=tmp_path, env=env)
        worker_logs_run = run([str(agentops), "worker", "logs", "--adapter", "mock"], cwd=tmp_path, env=env)
        task_create_help_run = run([str(agentops), "task", "create", "--help"], cwd=tmp_path, env=env)
        workflow_run_task_help_run = run([str(agentops), "workflow", "run-task", "--help"], cwd=tmp_path, env=env)
        workflow_run_template_help_run = run([str(agentops), "workflow", "run-template", "--help"], cwd=tmp_path, env=env)
        workflow_job_status_help_run = run([str(agentops), "workflow", "job-status", "--help"], cwd=tmp_path, env=env)
        workflow_help_run = run([str(agentops), "workflow", "customer-worker-task", "--help"], cwd=tmp_path, env=env)

        login_payload = {}
        status_payload = {}
        worker_status_payload = {}
        worker_preflight_payload = {}
        worker_logs_payload = {}
        try:
            login_payload = json.loads(login_run.stdout) if login_run.stdout.strip() else {}
        except json.JSONDecodeError:
            pass
        try:
            status_payload = json.loads(status_run.stdout) if status_run.stdout.strip() else {}
        except json.JSONDecodeError:
            pass
        try:
            worker_status_payload = json.loads(worker_status_run.stdout) if worker_status_run.stdout.strip() else {}
        except json.JSONDecodeError:
            pass
        try:
            worker_preflight_payload = json.loads(worker_preflight_run.stdout) if worker_preflight_run.stdout.strip() else {}
        except json.JSONDecodeError:
            pass
        try:
            worker_logs_payload = json.loads(worker_logs_run.stdout) if worker_logs_run.stdout.strip() else {}
        except json.JSONDecodeError:
            pass

        status_check_ok = (
            (
                status_run.returncode == 0
                and status_payload.get("provider") == "agent_gateway"
                and status_payload.get("token_omitted") is True
            )
            or server_unreachable(status_run)
        )
        worker_status_check_ok = (
            (
                worker_status_run.returncode == 0
                and worker_status_payload.get("provider") == "agentops-worker"
            )
            or server_unreachable(worker_status_run)
        )
        worker_logs_check_ok = (
            (
                worker_logs_run.returncode == 0
                and worker_logs_payload.get("provider") == "agentops-worker"
            )
            or server_unreachable(worker_logs_run)
        )
        ok = (
            install.returncode == 0
            and help_run.returncode == 0
            and "AgentOps MIS local Agent Gateway CLI" in help_run.stdout
            and worker_entry_help_run.returncode == 0
            and "usage: agentops-worker" in worker_entry_help_run.stdout
            and login_run.returncode == 0
            and login_payload.get("ok") is True
            and login_payload.get("has_api_key") is False
            and status_check_ok
            and worker_status_check_ok
            and worker_preflight_run.returncode == 0
            and worker_preflight_payload.get("provider") == "agentops-worker"
            and worker_preflight_payload.get("live_execution_performed") is False
            and worker_service_check_help_run.returncode == 0
            and "usage: agentops worker service-check" in worker_service_check_help_run.stdout
            and worker_service_install_help_run.returncode == 0
            and "usage: agentops worker service-install" in worker_service_install_help_run.stdout
            and worker_service_control_help_run.returncode == 0
            and "usage: agentops worker service-control" in worker_service_control_help_run.stdout
            and worker_logs_check_ok
            and task_create_help_run.returncode == 0
            and "usage: agentops task create" in task_create_help_run.stdout
            and "--owner-agent-id" in task_create_help_run.stdout
            and workflow_run_task_help_run.returncode == 0
            and "usage: agentops workflow run-task" in workflow_run_task_help_run.stdout
            and workflow_run_template_help_run.returncode == 0
            and "usage: agentops workflow run-template" in workflow_run_template_help_run.stdout
            and "--adapter" in workflow_run_template_help_run.stdout
            and "--request-timeout" in workflow_run_template_help_run.stdout
            and "--async-job" in workflow_run_template_help_run.stdout
            and workflow_job_status_help_run.returncode == 0
            and "usage: agentops workflow job-status" in workflow_job_status_help_run.stdout
            and workflow_help_run.returncode == 0
            and "customer-worker-task" in workflow_help_run.stdout
        )
        print(json.dumps({
            "ok": ok,
            "install_returncode": install.returncode,
            "install_mode": "source_wheel",
            "help_returncode": help_run.returncode,
            "worker_entry_help_returncode": worker_entry_help_run.returncode,
            "login_returncode": login_run.returncode,
            "status_returncode": status_run.returncode,
            "status_server_unreachable_allowed": server_unreachable(status_run),
            "worker_status_returncode": worker_status_run.returncode,
            "worker_status_server_unreachable_allowed": server_unreachable(worker_status_run),
            "worker_preflight_returncode": worker_preflight_run.returncode,
            "worker_service_check_help_returncode": worker_service_check_help_run.returncode,
            "worker_service_install_help_returncode": worker_service_install_help_run.returncode,
            "worker_service_control_help_returncode": worker_service_control_help_run.returncode,
            "worker_logs_returncode": worker_logs_run.returncode,
            "worker_logs_server_unreachable_allowed": server_unreachable(worker_logs_run),
            "task_create_help_returncode": task_create_help_run.returncode,
            "workflow_run_task_help_returncode": workflow_run_task_help_run.returncode,
            "workflow_run_template_help_returncode": workflow_run_template_help_run.returncode,
            "workflow_job_status_help_returncode": workflow_job_status_help_run.returncode,
            "workflow_help_returncode": workflow_help_run.returncode,
            "command": str(agentops),
            "worker_command": str(agentops_worker),
            "base_url": base_url,
            "config_path": str(config_path),
            "config_created": config_path.exists(),
            "token_written": bool(login_payload.get("has_api_key")),
            "status_provider": status_payload.get("provider"),
            "worker_status_provider": worker_status_payload.get("provider"),
            "worker_preflight_provider": worker_preflight_payload.get("provider"),
            "worker_logs_provider": worker_logs_payload.get("provider"),
            "worker_preflight_live_execution_performed": worker_preflight_payload.get("live_execution_performed"),
            "token_omitted": status_payload.get("token_omitted"),
            "venv_tool": "uv" if uv else "venv",
        }, ensure_ascii=False, indent=2, sort_keys=True))
        if not ok:
            print("pip install stderr:", install.stderr[-1200:], file=sys.stderr)
            print("help stderr:", help_run.stderr[-1200:], file=sys.stderr)
            print("agentops-worker help stderr:", worker_entry_help_run.stderr[-1200:], file=sys.stderr)
            print("login stderr:", login_run.stderr[-1200:], file=sys.stderr)
            print("status stderr:", status_run.stderr[-1200:], file=sys.stderr)
            print("worker status stderr:", worker_status_run.stderr[-1200:], file=sys.stderr)
            print("worker preflight stderr:", worker_preflight_run.stderr[-1200:], file=sys.stderr)
            print("worker service-check help stderr:", worker_service_check_help_run.stderr[-1200:], file=sys.stderr)
            print("worker service-install help stderr:", worker_service_install_help_run.stderr[-1200:], file=sys.stderr)
            print("worker service-control help stderr:", worker_service_control_help_run.stderr[-1200:], file=sys.stderr)
            print("worker logs stderr:", worker_logs_run.stderr[-1200:], file=sys.stderr)
            print("task create help stderr:", task_create_help_run.stderr[-1200:], file=sys.stderr)
            print("workflow run-task help stderr:", workflow_run_task_help_run.stderr[-1200:], file=sys.stderr)
            print("workflow run-template help stderr:", workflow_run_template_help_run.stderr[-1200:], file=sys.stderr)
            print("workflow job-status help stderr:", workflow_job_status_help_run.stderr[-1200:], file=sys.stderr)
            print("workflow help stderr:", workflow_help_run.stderr[-1200:], file=sys.stderr)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
