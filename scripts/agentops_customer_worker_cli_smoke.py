#!/usr/bin/env python3
"""Smoke test `agentops workflow customer-worker-task`."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
BASE_URL = os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787").rstrip("/")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    return subprocess.run(
        [str(CLI), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def load_json(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def http_json(path: str, timeout: int = 30) -> tuple[int, dict]:
    req = Request(BASE_URL + path, headers={"Accept": "application/json"}, method="GET")
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
        return 0, {"error": str(exc.reason)}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def secret_leaked(text: str) -> bool:
    patterns = (
        re.compile(r"Authorization:", re.IGNORECASE),
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
        re.compile(r"\bagtok_[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bagtsess_[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
        re.compile(r"\bntn_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    )
    return any(pattern.search(text) for pattern in patterns)


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def main() -> int:
    cli_agent_id = f"agt_customer_worker_cli_{stamp()}"
    os.environ["AGENTOPS_AGENT_ID"] = cli_agent_id
    registered = run([
        "agent",
        "register",
        "--id",
        cli_agent_id,
        "--name",
        "Customer Worker CLI Smoke Agent",
        "--role",
        "CLI Smoke Owner",
        "--runtime",
        "mock",
    ])
    registered_payload = load_json(registered)
    require(registered.returncode == 0, f"agent register failed: {registered.stderr or registered.stdout}")
    require((registered_payload.get("agent") or {}).get("agent_id") == cli_agent_id, f"agent register id mismatch: {registered_payload}")

    case_task_id = f"tsk_customer_worker_case_{stamp()}"
    case_id = f"evalcase_customer_worker_{stamp()}"
    created_task = run([
        "task",
        "create",
        "--task-id",
        case_task_id,
        "--title",
        "Customer worker auto evaluation case smoke",
        "--description",
        "Customer wants a worker task that automatically runs approved local benchmark cases after execution.",
        "--acceptance",
        "Worker result must include tool, evaluation, audit, artifact and evaluation case run evidence.",
        "--risk",
        "medium",
    ])
    created_task_payload = load_json(created_task)
    require(created_task.returncode == 0, f"task create failed: {created_task.stderr or created_task.stdout}")
    require(created_task_payload.get("task", {}).get("task_id") == case_task_id, f"task id mismatch: {created_task_payload}")

    proposed_case = run([
        "eval",
        "propose-case",
        "--case-id",
        case_id,
        "--task-id",
        case_task_id,
        "--case-type",
        "golden",
        "--title",
        "Customer worker auto evaluation golden case",
        "--expected-output-summary",
        "Worker result must include tool, evaluation, audit, artifact and evaluation case run evidence.",
        "--confirm-create",
    ])
    proposed_payload = load_json(proposed_case)
    require(proposed_case.returncode == 0, f"case propose failed: {proposed_case.stderr or proposed_case.stdout}")
    require(proposed_payload.get("status") == "candidate", f"case propose status wrong: {proposed_payload}")

    approved_case = run(["eval", "approve-case", "--case-id", case_id])
    approved_payload = load_json(approved_case)
    require(approved_case.returncode == 0, f"case approve failed: {approved_case.stderr or approved_case.stdout}")
    require(approved_payload.get("review_status") == "approved", f"case approve status wrong: {approved_payload}")

    auto_case = run([
        "workflow",
        "customer-worker-task",
        "--adapter",
        "mock",
        "--task-id",
        case_task_id,
        "--title",
        "Customer worker auto evaluation case smoke",
        "--description",
        "Execute the pre-created customer task and automatically run the approved evaluation case.",
        "--acceptance",
        "Worker result must include tool, evaluation, audit, artifact and evaluation case run evidence.",
        "--selected-agent-id",
        "agt_worker_local",
    ])
    auto_payload = load_json(auto_case)
    auto_evidence = auto_payload.get("evidence") or {}
    require(auto_case.returncode == 0, f"auto case worker workflow failed: {auto_case.stderr or auto_case.stdout}")
    require(auto_payload.get("ok") is True, f"auto case worker did not complete: {auto_payload}")
    require(auto_payload.get("task_id") == case_task_id, f"auto case task id mismatch: {auto_payload}")
    require(auto_evidence.get("evaluation_case_runs", 0) >= 1, f"missing automatic evaluation case evidence: {auto_evidence}")
    require((auto_payload.get("evaluation_case_result") or {}).get("summary", {}).get("created", 0) >= 1, f"missing evaluation case result payload: {auto_payload}")
    task_detail_status, task_detail = http_json(f"/api/tasks/{case_task_id}")
    require(task_detail_status == 200, f"task detail failed: {task_detail_status} {task_detail}")
    require(any(item.get("case_id") == case_id for item in task_detail.get("evaluation_case_runs", [])), f"task detail missing evaluation case run: {task_detail}")
    run_detail_status, run_detail = http_json(f"/api/runs/{auto_payload.get('run_id')}")
    require(run_detail_status == 200, f"run detail failed: {run_detail_status} {run_detail}")
    require(any(item.get("case_id") == case_id for item in run_detail.get("evaluation_case_runs", [])), f"run detail missing evaluation case run: {run_detail}")

    mock = run([
        "workflow",
        "customer-worker-task",
        "--adapter",
        "mock",
        "--title",
        "CLI customer worker smoke",
        "--description",
        "Customer sends a task through the CLI and expects a real worker ledger result.",
        "--acceptance",
        "Return run, tool, evaluation, audit and customer_worker_result artifact evidence.",
        "--selected-agent-id",
        "agt_worker_local",
    ])
    mock_payload = load_json(mock)
    evidence = mock_payload.get("evidence") or {}
    require(mock.returncode == 0, f"mock workflow CLI failed: {mock.stderr or mock.stdout}")
    require(mock_payload.get("provider") == "agentops-worker", f"wrong provider: {mock_payload}")
    require(mock_payload.get("workflow") == "customer_worker_task", f"wrong workflow: {mock_payload}")
    require(mock_payload.get("ok") is True, f"mock workflow did not complete: {mock_payload}")
    require(mock_payload.get("dry_run") is False, f"mock workflow should write real ledger evidence: {mock_payload}")
    require(bool(mock_payload.get("run_id")), f"missing run id: {mock_payload}")
    require(bool(mock_payload.get("artifact_id")), f"missing artifact id: {mock_payload}")
    require(evidence.get("tool_calls", 0) >= 1, f"missing tool evidence: {evidence}")
    require(evidence.get("evaluations", 0) >= 1, f"missing eval evidence: {evidence}")
    require(evidence.get("audit_logs", 0) >= 1, f"missing audit evidence: {evidence}")
    require(evidence.get("artifacts", 0) >= 1, f"missing artifact evidence: {evidence}")

    hermes_gate = run([
        "workflow",
        "customer-worker-task",
        "--adapter",
        "hermes",
        "--title",
        "CLI Hermes confirm gate",
        "--description",
        "This must not execute Hermes without explicit confirmation.",
    ])
    hermes_payload = load_json(hermes_gate)
    require(hermes_gate.returncode == 0, f"hermes gate command failed: {hermes_gate.stderr or hermes_gate.stdout}")
    require(hermes_payload.get("dry_run") is True, f"Hermes without confirm should be planned/dry-run: {hermes_payload}")
    require(hermes_payload.get("reason") == "confirm_run_required_for_live_adapter", f"wrong Hermes gate reason: {hermes_payload}")

    combined = "\n".join([
        registered.stdout,
        registered.stderr,
        created_task.stdout,
        created_task.stderr,
        proposed_case.stdout,
        proposed_case.stderr,
        approved_case.stdout,
        approved_case.stderr,
        auto_case.stdout,
        auto_case.stderr,
        mock.stdout,
        mock.stderr,
        hermes_gate.stdout,
        hermes_gate.stderr,
    ])
    require(not secret_leaked(combined), "CLI workflow output leaked a secret-like token")
    print(json.dumps({
        "ok": True,
        "cli_agent_id": cli_agent_id,
        "auto_case_id": case_id,
        "auto_case_task_id": case_task_id,
        "auto_case_run_id": auto_payload.get("run_id"),
        "auto_case_evidence": auto_evidence,
        "mock_run_id": mock_payload.get("run_id"),
        "mock_artifact_id": mock_payload.get("artifact_id"),
        "mock_evidence": evidence,
        "hermes_gate_task_id": hermes_payload.get("task_id"),
        "secret_leaked": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
