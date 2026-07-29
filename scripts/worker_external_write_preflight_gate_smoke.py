#!/usr/bin/env python3
"""Verify live workers pause external-write tasks before adapter execution."""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.worker import worker_external_write_intent


SERVER = ROOT / "server.py"
SECRET_RE = re.compile(r"(Authorization:|Bearer\s+[A-Za-z0-9._~+/=-]+|agtok_[A-Za-z0-9_-]{16,}|agtsess_[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,}|ntn_[A-Za-z0-9_-]{8,})")


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def http_json(method: str, base_url: str, path: str, payload: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), raw
        except Exception:
            return exc.code, {"raw": raw}, raw


def wait_ready(base_url: str, proc: subprocess.Popen[str], timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/api/dashboard/metrics", timeout=1) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    return False


def record_loopback_service_closure(base_url: str, adapter: str, failures: list[str], outputs: list[str]) -> str | None:
    action_command = f"agentops worker service-control --manager launchd --action restart --adapter {adapter} --agent-id agt_worker_daemon_{adapter}"
    verify_command = f"agentops worker service-check --manager launchd --adapter {adapter} --agent-id agt_worker_daemon_{adapter}"
    status, receipt_payload, raw = http_json("POST", base_url, "/api/operator/action-receipts", {
        "workspace_id": "local-demo",
        "actor_id": "usr_worker_external_gate_smoke",
        "action_command": action_command,
        "verify_command": verify_command,
        "action_id": f"local_readiness.service_control_preview.{adapter}",
        "source": f"local_readiness.service_control_preview.{adapter}",
        "status": "verified",
        "result_summary": f"{adapter} worker service-control preview inspected and service-check readback recorded.",
    })
    outputs.append(raw)
    receipt = receipt_payload.get("receipt") or {}
    receipt_id = receipt.get("receipt_id")
    require(status == 201 and bool(receipt_id), f"{adapter} service closure receipt failed: {status} {receipt_payload}", failures)
    if not receipt_id:
        return None
    status, readback_payload, raw = http_json("POST", base_url, "/api/operator/action-receipts/control-readback", {
        "workspace_id": "local-demo",
        "actor_id": "usr_worker_external_gate_smoke",
        "receipt_id": receipt_id,
        "source": f"local_readiness.service_control_preview.{adapter}.control_readback",
        "control_readback": {
            "before": {
                "step_id": "preview_worker_service_control",
                "status": "preview",
                "adapter": adapter,
                "service_control_preview": True,
            },
            "after": {
                "verify_command": verify_command,
                "service_check_expected": True,
                "service_check_ok": True,
                "service_file_exists": True,
                "service_loaded": True,
                "confirm_gate_ok": True,
                "relaunch_policy_ok": True,
                "confirmed_os_mutation": False,
                "loopback_fixture": True,
            },
            "self_check": {
                "copy_only": True,
                "server_executes_shell": False,
                "writes_ledger_for_service_control": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
    })
    outputs.append(raw)
    require(status == 201, f"{adapter} service closure readback failed: {status} {readback_payload}", failures)
    return receipt_id


def start_server(db_path: Path, port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["AGENTOPS_DB_PATH"] = str(db_path)
    env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
    return subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def run_worker(base_url: str, agent_id: str, task_id: str, adapter: str) -> tuple[int, dict, str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    env["AGENTOPS_BASE_URL"] = base_url
    env["AGENTOPS_WORKSPACE_ID"] = "local-demo"
    env["AGENTOPS_AGENT_ID"] = agent_id
    env["HERMES_GATEWAY_URL"] = "http://127.0.0.1:9"
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/agent_worker.py",
            "--once",
            "--adapter",
            adapter,
            "--agent-id",
            agent_id,
            "--task-id",
            task_id,
            "--base-url",
            base_url,
            "--confirm-run",
            "--no-enforce-intake",
            "--hermes-gateway-url",
            "http://127.0.0.1:9",
            "--openclaw-bin",
            "/bin/false",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        payload = {}
    return proc.returncode, payload, proc.stdout + proc.stderr


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    port = choose_port()
    base_url = f"http://127.0.0.1:{port}"
    server: subprocess.Popen[str] | None = None
    stamp = str(int(time.time() * 1000))
    worker_results: list[dict] = []
    intent_args = SimpleNamespace(adapter="openclaw", confirm_run=True)
    intent_capability = {"requires_prepared_action_for_external_write": True}
    require(
        not worker_external_write_intent(
            {
                "title": "Local acceptance summary",
                "description": "Do not perform external writes. Keep every operation local and read-only.",
                "acceptance_criteria": "Return a bounded local summary.",
            },
            intent_args,
            intent_capability,
        ),
        "negated external-write wording must not create a prepared action",
        failures,
    )
    require(
        not worker_external_write_intent(
            {
                "title": "Read-only integration review",
                "description": "Return evidence without modifying files or using external tools.",
                "acceptance_criteria": "Summarize the connector status notionally.",
            },
            intent_args,
            intent_capability,
        ),
        "connector-name substrings inside ordinary words must not create a prepared action",
        failures,
    )
    require(
        not worker_external_write_intent(
            {
                "title": "本地验收摘要",
                "description": "不要上传或发布任何内容，仅限本地只读分析。",
                "acceptance_criteria": "返回简短摘要。",
            },
            intent_args,
            intent_capability,
        ),
        "Chinese negated external-write wording must not create a prepared action",
        failures,
    )
    require(
        worker_external_write_intent(
            {
                "title": "Customer delivery",
                "description": "Do not upload drafts. Publish the approved release to the customer portal.",
                "acceptance_criteria": "Record the delivery receipt.",
            },
            intent_args,
            intent_capability,
        ),
        "a later positive write intent must still create a prepared action",
        failures,
    )

    with tempfile.TemporaryDirectory(prefix="agentops-worker-external-gate-") as tmp:
        db_path = Path(tmp) / "agentops_worker_gate.db"
        try:
            server = start_server(db_path, port)
            require(wait_ready(base_url, server), "isolated server did not become ready", failures)
            if failures:
                raise AssertionError(failures[-1])

            for adapter in ("hermes", "openclaw"):
                record_loopback_service_closure(base_url, adapter, failures, outputs)
                agent_id = f"agt_worker_external_gate_{adapter}_{stamp}"
                task_id = f"tsk_worker_external_gate_{adapter}_{stamp}"
                status, registered, raw = http_json("POST", base_url, "/api/agent-gateway/register", {
                    "workspace_id": "local-demo",
                    "agent_id": agent_id,
                    "name": f"Worker External Gate Smoke {adapter}",
                    "role": f"{adapter} Worker",
                    "runtime_type": adapter,
                    "permission_level": "standard",
                })
                outputs.append(raw)
                require(status in {200, 201}, f"{adapter} agent register failed: {status} {registered}", failures)

                status, task, raw = http_json("POST", base_url, "/api/tasks", {
                    "task_id": task_id,
                    "workspace_id": "local-demo",
                    "title": f"Upload Dify customer knowledge base through {adapter}",
                    "description": f"Use {adapter} to upload approved customer delivery content to an external Dify dataset.",
                    "owner_agent_id": agent_id,
                    "status": "planned",
                    "priority": "high",
                    "risk_level": "medium",
                    "acceptance_criteria": "The live worker must stop before any external upload until a prepared action is approved.",
                })
                outputs.append(raw)
                require(status in {200, 201}, f"{adapter} task create failed: {status} {task}", failures)

                code, worker_payload, raw = run_worker(base_url, agent_id, task_id, adapter)
                outputs.append(raw)
                require(code == 0, f"{adapter} worker should pause safely with exit 0: {raw}", failures)
                result = (worker_payload.get("results") or [{}])[0]
                worker_results.append(result)
                require(worker_payload.get("ok") is True, f"{adapter} worker payload should be ok: {worker_payload}", failures)
                require(result.get("reason") == "external_write_prepared_action_required", f"{adapter} wrong worker reason: {worker_payload}", failures)
                require(result.get("live_execution_performed") is False, f"{adapter} worker should not execute adapter: {worker_payload}", failures)
                require(bool(result.get("prepared_action_id") and result.get("approval_id") and result.get("tool_call_id")), f"{adapter} missing approval wall ids: {worker_payload}", failures)
                require("HermesExecutionFailed" not in raw and "OpenClawExecutionFailed" not in raw and "ConfirmRunRequired" not in raw, f"{adapter} worker appears to have reached live adapter: {raw}", failures)

                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    action = conn.execute("SELECT * FROM prepared_actions WHERE action_id=?", (result.get("prepared_action_id"),)).fetchone()
                    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (result.get("run_id"),)).fetchone()
                    tool = conn.execute("SELECT * FROM tool_calls WHERE tool_call_id=?", (result.get("tool_call_id"),)).fetchone()
                    audit_count = conn.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='agent_worker.external_write_prepared_action_required'").fetchone()["c"]
                require(action and action["status"] == "prepared", f"{adapter} worker prepared action missing: {dict(action) if action else None}", failures)
                require(run and run["status"] == "waiting_approval" and run["approval_required"] == 1, f"{adapter} worker run should wait approval: {dict(run) if run else None}", failures)
                require(tool and tool["status"] == "waiting_approval" and tool["side_effect_id"] is None, f"{adapter} worker tool should wait approval: {dict(tool) if tool else None}", failures)
                require(audit_count >= 1, f"{adapter} worker audit evidence missing: {audit_count}", failures)

                status, dispatch, raw = http_json("POST", base_url, "/api/workers/local/dispatch-once", {
                    "adapter": adapter,
                    "confirm_run": True,
                    "agent_id": f"{agent_id}_ui",
                    "title": f"Publish customer portal update through {adapter}",
                    "description": "Publish final customer delivery to an external customer portal.",
                    "acceptance_criteria": "Prepared action must be approved before the local worker process starts.",
                })
                outputs.append(raw)
                require(status == 201, f"{adapter} dispatch gate request failed: {status} {dispatch}", failures)
                require(dispatch.get("reason") == "external_write_prepared_action_required", f"{adapter} dispatch wrong reason: {dispatch}", failures)
                require(dispatch.get("workflow") == "worker_dispatch_once", f"{adapter} dispatch workflow marker missing: {dispatch}", failures)
                require(dispatch.get("live_execution_performed") is False, f"{adapter} dispatch should not start worker process: {dispatch}", failures)
                require(bool(dispatch.get("prepared_action_id") and dispatch.get("approval_id")), f"{adapter} dispatch approval wall ids missing: {dispatch}", failures)
        finally:
            if server and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()

    require(not SECRET_RE.search("\n".join(outputs)), "smoke output leaked token-like material", failures)
    print(json.dumps({
        "ok": not failures,
        "operation": "worker_external_write_preflight_gate",
        "worker_prepared_action_ids": [item.get("prepared_action_id") for item in worker_results],
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
