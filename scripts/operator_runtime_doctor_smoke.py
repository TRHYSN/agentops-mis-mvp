#!/usr/bin/env python3
"""Verify operator runtime-doctor is read-only, scoped, and redacted."""

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
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"AGENTOPS_API_KEY=", re.IGNORECASE),
]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    req_headers = {"Content-Type": "application/json"}
    req_headers.update(headers or {})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(base_url.rstrip("/") + path, data=data, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=45) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def wait_ready(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            status, _ = http_json(base_url, "/api/operator/runtime-doctor?limit=1")
            if status == 200:
                return
        except URLError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def db_fingerprint(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        result = {}
        for table in [
            "runtime_connectors",
            "connectors",
            "audit_logs",
            "runtime_events",
            "tasks",
            "runs",
            "memories",
            "approvals",
            "agent_plans",
            "plan_evidence_manifests",
        ]:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                result[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        return result
    finally:
        conn.close()


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def load_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def validate_payload(payload: dict, label: str, failures: list[str]) -> None:
    require(payload.get("provider") == "agentops-operator", f"{label} provider mismatch: {payload}", failures)
    require(payload.get("operation") == "operator_runtime_doctor", f"{label} operation mismatch: {payload}", failures)
    require(payload.get("status") in {"ready", "attention", "blocked"}, f"{label} status wrong: {payload}", failures)
    require(payload.get("token_omitted") is True, f"{label} token omission missing: {payload}", failures)
    require(payload.get("live_execution_performed") is False, f"{label} executed live work: {payload}", failures)
    require(isinstance(payload.get("gates") or [], list), f"{label} gates missing: {payload}", failures)
    gate_ids = {gate.get("id") for gate in payload.get("gates") or []}
    for required_id in [
        "mis_api",
        "adapter_readiness",
        "hermes_runtime",
        "openclaw_runtime",
        "confirm_run_wall",
        "prepared_action_wall",
        "remote_worker_fleet",
        "loop_launch_packet",
        "handoff_evidence_chain",
        "codex_supervisor",
        "redaction_boundary",
    ]:
        require(required_id in gate_ids, f"{label} missing gate {required_id}: {gate_ids}", failures)
    for gate in payload.get("gates") or []:
        require(gate.get("status") in {"pass", "attention", "blocked"}, f"{label} gate status wrong: {gate}", failures)
        require(gate.get("token_omitted") is True, f"{label} gate token omission missing: {gate}", failures)
    commands = payload.get("commands") or {}
    for key in ["operator_runtime_doctor", "worker_readiness", "hermes_preflight", "openclaw_preflight", "codex_supervisor"]:
        require(key in commands, f"{label} command {key} missing: {commands}", failures)
    require("agentops operator runtime-doctor" in commands.get("operator_runtime_doctor", ""), f"{label} doctor command missing: {commands}", failures)
    require("codex resume" in commands.get("codex_supervisor", ""), f"{label} codex supervisor command missing: {commands}", failures)
    summary = payload.get("summary") or {}
    require(isinstance(summary.get("ready_adapters") or [], list), f"{label} ready adapters missing: {summary}", failures)
    require(isinstance(summary.get("requires_confirm_run") or [], list), f"{label} confirm-run summary missing: {summary}", failures)
    require(summary.get("operator_health_score") is None, f"{label} runtime doctor must stay a lightweight first-check, not run full operator health: {summary}", failures)
    require(summary.get("control_status") == "inspect_handoff", f"{label} control status should point to handoff inspection: {summary}", failures)
    sources = payload.get("sources") or {}
    for key in ["operator_health", "adapter_readiness", "worker_fleet", "handoff"]:
        require(key in sources, f"{label} source {key} missing: {sources}", failures)
        require((sources.get(key) or {}).get("token_omitted") is True, f"{label} source {key} token omission missing: {sources.get(key)}", failures)
    operator_health_source = sources.get("operator_health") or {}
    require(operator_health_source.get("status") == "not_sampled", f"{label} operator health must not be sampled inside runtime-doctor: {operator_health_source}", failures)
    require(operator_health_source.get("score") is None, f"{label} operator health score should be omitted in lightweight doctor: {operator_health_source}", failures)
    auth = payload.get("auth") or {}
    require(auth.get("mode") in {"local_dev_no_token", "global_api_key", "agent_token", "agent_session"}, f"{label} auth mode wrong: {auth}", failures)
    require(auth.get("required_scope") == "tasks:read", f"{label} auth scope wrong: {auth}", failures)
    require(auth.get("token_omitted") is True, f"{label} auth token omission missing: {auth}", failures)
    safety = payload.get("safety") or {}
    require(safety.get("read_only") is True, f"{label} safety read_only missing: {safety}", failures)
    require(safety.get("ledger_mutated") is False, f"{label} mutated ledger: {safety}", failures)
    require(safety.get("live_execution_performed") is False, f"{label} executed live work: {safety}", failures)
    require(safety.get("server_executes_shell") is False, f"{label} shell execution boundary missing: {safety}", failures)
    require(safety.get("raw_prompt_omitted") is True, f"{label} raw prompt omission missing: {safety}", failures)
    require(safety.get("raw_response_omitted") is True, f"{label} raw response omission missing: {safety}", failures)
    require("never starts runtimes" in (payload.get("contract") or ""), f"{label} contract missing runtime boundary: {payload.get('contract')}", failures)


def create_enrollment(base_url: str, workspace_id: str, agent_id: str, scopes: list[str]) -> tuple[str, str]:
    status, payload = http_json(
        base_url,
        "/api/agent-gateway/enrollment/create",
        method="POST",
        payload={
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "name": f"Runtime Doctor {agent_id}",
            "runtime_type": "mock",
            "scopes": scopes,
            "ttl_days": 1,
            "heartbeat_timeout_sec": 60,
        },
    )
    if status != 201 or not payload.get("token_id") or not payload.get("token"):
        raise RuntimeError(f"enrollment create failed: {status} {payload}")
    return str(payload["token_id"]), str(payload["token"])


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-runtime-doctor-") as tmp:
        db_path = Path(tmp) / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AGENTOPS_DB_PATH"] = str(db_path)
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env["AGENTOPS_CONFIG"] = str(Path(tmp) / "config.json")
        env["AGENTOPS_BASE_URL"] = base_url
        env.pop("AGENTOPS_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_ready(base_url, proc)
            workspace_id = "ws_runtime_doctor"
            agent_id = "agt_runtime_doctor"
            _token_id, token = create_enrollment(base_url, workspace_id, agent_id, ["tasks:read", "agents:heartbeat"])
            before = db_fingerprint(db_path)
            status, api_payload = http_json(base_url, f"/api/operator/runtime-doctor?limit=5&base_url={base_url}")
            outputs.append(json.dumps(api_payload, ensure_ascii=False))
            require(status == 200, f"API status mismatch: {status} {api_payload}", failures)
            validate_payload(api_payload, "api", failures)
            require(api_payload.get("base_url") == base_url, f"base URL not reflected safely: {api_payload.get('base_url')}", failures)

            spoof_marker = "forwarded-runtime-doctor-marker.invalid"
            status, forwarded_payload = http_json(
                base_url,
                "/api/operator/runtime-doctor?limit=3",
                headers={
                    "Forwarded": f"host={spoof_marker};proto=https",
                    "X-Forwarded-Host": spoof_marker,
                    "X-Forwarded-Proto": "https",
                },
            )
            outputs.append(json.dumps(forwarded_payload, ensure_ascii=False))
            require(status == 200, f"forwarded-header API status mismatch: {status} {forwarded_payload}", failures)
            require(
                forwarded_payload.get("base_url") == base_url,
                f"forwarded headers changed runtime-doctor base URL: {forwarded_payload.get('base_url')}",
                failures,
            )
            require(
                spoof_marker not in json.dumps(forwarded_payload, ensure_ascii=False),
                "forwarded-header marker leaked into runtime-doctor output",
                failures,
            )

            status, invalid_token_payload = http_json(base_url, "/api/operator/runtime-doctor", headers={"Authorization": "Bearer no-such-token"})
            outputs.append(json.dumps(invalid_token_payload, ensure_ascii=False))
            require(status == 401, f"invalid token should be rejected: {status} {invalid_token_payload}", failures)
            require(invalid_token_payload.get("error") == "unauthorized", f"invalid token error mismatch: {invalid_token_payload}", failures)

            status, scoped_payload = http_json(
                base_url,
                "/api/operator/runtime-doctor?limit=3",
                headers={"Authorization": f"Bearer {token}", "X-AgentOps-Workspace-Id": workspace_id},
            )
            outputs.append(json.dumps(scoped_payload, ensure_ascii=False))
            require(status == 200, f"scoped API status mismatch: {status} {scoped_payload}", failures)
            validate_payload(scoped_payload, "scoped_api", failures)
            require(scoped_payload.get("workspace_id") == workspace_id, f"scoped workspace mismatch: {scoped_payload}", failures)

            status, forbidden_payload = http_json(
                base_url,
                "/api/operator/runtime-doctor?limit=3",
                headers={"Authorization": f"Bearer {token}", "X-AgentOps-Workspace-Id": "other-workspace"},
            )
            outputs.append(json.dumps(forbidden_payload, ensure_ascii=False))
            require(status == 403, f"cross-workspace token should be forbidden: {status} {forbidden_payload}", failures)

            cli_proc = subprocess.run(
                [
                    str(CLI),
                    "--base-url",
                    base_url,
                    "operator",
                    "runtime-doctor",
                    "--limit",
                    "5",
                    "--runtime-base-url",
                    base_url,
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            outputs.extend([cli_proc.stdout, cli_proc.stderr])
            require(cli_proc.returncode == 0, f"CLI runtime-doctor failed: {cli_proc.stderr or cli_proc.stdout}", failures)
            cli_payload = load_json(cli_proc.stdout)
            validate_payload(cli_payload, "cli", failures)
            after = db_fingerprint(db_path)
            require(before == after, f"runtime-doctor mutated ledger: before={before} after={after}", failures)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        combined = "\n".join(outputs)
        require(not leaked_secret(combined), "runtime-doctor output leaked token-like material", failures)

    if failures:
        print(json.dumps({"ok": False, "failures": failures}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "operation": "operator_runtime_doctor_smoke",
        "api_status": api_payload.get("status"),
        "cli_status": cli_payload.get("status"),
        "gate_count": len(api_payload.get("gates") or []),
        "secret_leaked": False,
        "ledger_mutated": False,
        "live_execution_performed": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
