#!/usr/bin/env python3
"""Smoke-test `agentops task create` feeding a real worker loop."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
CLI_COMMAND = (
    [sys.executable, "-B", "-m", "agentops_mis_cli"]
    if os.name == "nt"
    else [str(CLI)]
)


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def run_cli(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    env.pop("AGENTOPS_AGENT_ID", None)
    return subprocess.run(
        [*CLI_COMMAND, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def http_json(method: str, path: str, payload: dict | None = None, timeout: int = 60) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(
        "http://127.0.0.1:8787" + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}
    except URLError as exc:
        raise RuntimeError(f"Cannot reach MIS server: {exc.reason}") from exc


def load_json(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def secret_leaked(text: str) -> bool:
    return any(marker in text for marker in ["Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_"])


def main() -> int:
    suffix = stamp()
    agent_id = f"agt_task_create_cli_smoke_{suffix}"
    task_id = f"tsk_task_create_cli_smoke_{suffix}"

    register = run_cli([
        "agent",
        "register",
        "--id",
        agent_id,
        "--name",
        "Task Create CLI Smoke Worker",
        "--role",
        "CLI Worker",
        "--runtime",
        "mock",
    ])
    register_payload = load_json(register)
    registered_agent = register_payload.get("agent") or register_payload
    require(register.returncode == 0, f"agent register failed: {register.stderr or register.stdout}")
    require(registered_agent.get("agent_id") == agent_id, f"wrong agent registration payload: {register_payload}")

    created = run_cli([
        "task",
        "create",
        "--task-id",
        task_id,
        "--title",
        "CLI-created customer task smoke",
        "--description",
        "Customer/API creates a normal MIS task; a worker must pull it and write ledger evidence.",
        "--owner-agent-id",
        agent_id,
        "--priority",
        "high",
        "--risk",
        "medium",
        "--acceptance",
        "Worker must complete the task and write run, tool call, evaluation and audit evidence.",
        "--budget",
        "2.5",
    ])
    created_payload = load_json(created)
    task = created_payload.get("task") or {}
    require(created.returncode == 0, f"task create CLI failed: {created.stderr or created.stdout}")
    require(created_payload.get("operation") == "task_create", f"wrong operation: {created_payload}")
    require(created_payload.get("task_id") == task_id, f"wrong task id: {created_payload}")
    require(created_payload.get("outcome") in {"created", "updated", "unchanged"}, f"wrong outcome: {created_payload}")
    require(task.get("owner_agent_id") == agent_id, f"task not assigned to smoke worker: {task}")
    require(task.get("status") == "planned", f"task not planned: {task}")

    worker_runs: list[subprocess.CompletedProcess[str]] = []
    worker_payload: dict = {}
    for attempt in range(2):
        worker = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "agent_worker.py"),
                "--once",
                "--adapter",
                "mock",
                "--agent-id",
                agent_id,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=140,
            check=False,
        )
        worker_runs.append(worker)
        require(worker.returncode == 0, f"worker failed: {worker.stderr or worker.stdout}")
        worker_payload = json.loads(worker.stdout or "{}")
        if worker_payload.get("processed") == 1:
            break
        result = ((worker_payload.get("results") or [{}])[0] or {})
        require(
            attempt == 0
            and result.get("reason") == "intake_auto_planned"
            and result.get("verification_pass") is True,
            f"worker did not process or produce a verified intake plan: {worker_payload}",
        )

    result = ((worker_payload.get("results") or [{}])[0] or {})
    run_id = result.get("run_id")
    require(worker_payload.get("processed") == 1, f"worker did not process exactly one task: {worker_payload}")
    require(run_id, f"worker did not return run_id: {worker_payload}")

    task_status, task_detail = http_json("GET", f"/api/tasks/{task_id}")
    run_status, run_detail = http_json("GET", f"/api/runs/{run_id}")
    require(task_status == 200, f"task detail failed: {task_status} {task_detail}")
    require(run_status == 200, f"run detail failed: {run_status} {run_detail}")
    run = run_detail.get("run") or {}
    tool_calls = run_detail.get("tool_calls") or []
    evaluations = run_detail.get("evaluations") or []
    require(run.get("task_id") == task_id, f"run not linked to task: {run}")
    require(run.get("status") == "completed", f"run not completed: {run}")
    require(len(tool_calls) >= 1, f"missing tool call evidence: {run_detail}")
    require(len(evaluations) >= 1, f"missing evaluation evidence: {run_detail}")
    worker_output = [text for proc in worker_runs for text in (proc.stdout, proc.stderr)]
    require(
        not secret_leaked("\n".join([register.stdout, register.stderr, created.stdout, created.stderr, *worker_output])),
        "secret-like token leaked",
    )

    print(json.dumps({
        "ok": True,
        "agent_id": agent_id,
        "task_id": task_id,
        "run_id": run_id,
        "task_status": (task_detail.get("task") or {}).get("status"),
        "run_status": run.get("status"),
        "tool_calls": len(tool_calls),
        "evaluations": len(evaluations),
        "token_omitted": True,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
