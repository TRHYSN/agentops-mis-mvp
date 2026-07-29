#!/usr/bin/env python3
"""Verify deployment-aware enrollment policy for local and hosted modes."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_EMPLOYEES = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "AIEmployees.tsx"
LIVE_API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"
OBSERVER_SCOPES = [
    "agents:heartbeat",
    "knowledge:read",
    "agent_plans:read",
    "plan_evidence:read",
    "tasks:read",
    "audit:write",
]
SECRET_PATTERNS = [
    re.compile(r"agtok_[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtsess_[A-Za-z0-9._~+/=-]+"),
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def extract_block(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start)
    if end < 0:
        return text[start:]
    return text[start:end]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(method: str, base_url: str, path: str, payload: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), raw
        except Exception:
            return exc.code, {"raw": raw}, raw


def wait_ready(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            status, _payload, _raw = http_json("GET", base_url, "/health")
            if status == 200:
                return
        except urllib.error.URLError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def secret_leaked(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def preview_in_mode(mode: str, failures: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"agentops-enrollment-{mode}-policy-") as tmp:
        db_path = Path(tmp) / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AGENTOPS_DB_PATH"] = str(db_path)
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env["HERMES_ALLOW_REAL_RUN"] = "false"
        env["AGENTOPS_DEPLOYMENT_MODE"] = mode
        if mode in {"hosted", "shared", "production"}:
            env["AGENTOPS_ADMIN_KEY"] = "agentops_admin_key_for_hosted_policy_smoke"
            env["AGENTOPS_API_KEY"] = "agentops_gateway_key_for_hosted_policy_smoke"
        else:
            env.pop("AGENTOPS_ADMIN_KEY", None)
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
            status, payload, raw = http_json("POST", base_url, "/api/agent-gateway/enrollment/policy-preview", {
                "runtime_type": "mock",
                "workspace_id": "local-demo",
                "scopes": OBSERVER_SCOPES,
            })
            require(status == 200, f"{mode} policy preview failed: {status} {payload}", failures)
            require(not secret_leaked(raw), f"{mode} policy preview leaked secret-like material", failures)
            return payload
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def static_ui_contract(failures: list[str]) -> None:
    ai = AI_EMPLOYEES.read_text(encoding="utf-8")
    live_api = LIVE_API.read_text(encoding="utf-8")
    policy_interface = extract_block(
        live_api,
        "export interface AgentGatewayEnrollmentPolicyPreview",
        "export async function loadAgentGatewayEnrollments",
    )
    policy_loader = extract_block(
        live_api,
        "export async function previewAgentGatewayEnrollmentPolicy",
        "export async function loadAgentGatewaySecurityReadiness",
    )
    hosted_gate = extract_block(
        ai,
        'data-testid="hosted-enrollment-policy-gate"',
        "enrollmentPolicy.invalid_scopes",
    )
    required_fields = [
        "deployment_mode",
        "production_security_requested",
        "admin_key_configured",
        "direct_create_allowed",
        "approval_request_required",
        "deployment_policy_summary",
    ]
    for field in required_fields:
        require(field in policy_interface, f"policy interface missing {field}", failures)
        require(field in policy_loader, f"policy loader missing {field}", failures)
        require(field in hosted_gate, f"hosted policy gate missing {field}", failures)
    require("hosted-enrollment-policy-gate" in ai, "AI Employees page missing hosted policy test id", failures)
    require("createEnrollmentBlockedByPolicy" in ai, "direct create button is not policy-gated", failures)
    require("enrollmentDeploymentPolicy" in ai, "English/Chinese deployment policy label missing", failures)
    require("可直接创建" in ai and "需要审批" in ai, "Chinese deployment policy copy incomplete", failures)


def main() -> int:
    failures: list[str] = []
    static_ui_contract(failures)
    local_preview = preview_in_mode("local", failures)
    hosted_preview = preview_in_mode("hosted", failures)

    require(local_preview.get("deployment_mode") == "local", f"local deployment mode mismatch: {local_preview}", failures)
    require(local_preview.get("production_security_requested") is False, f"local should not be production security: {local_preview}", failures)
    require(local_preview.get("direct_create_allowed") is True, f"local low-risk observer should allow direct create: {local_preview}", failures)
    require(local_preview.get("approval_request_required") is False, f"local low-risk observer should not require approval: {local_preview}", failures)
    require(local_preview.get("recommended_path") == "create_token", f"local path mismatch: {local_preview}", failures)
    local_gate = next((gate for gate in local_preview.get("gates", []) if gate.get("id") == "deployment_policy"), {})
    require(local_gate.get("status") == "pass", f"local deployment gate should pass: {local_preview}", failures)

    require(hosted_preview.get("deployment_mode") == "hosted", f"hosted deployment mode mismatch: {hosted_preview}", failures)
    require(hosted_preview.get("production_security_requested") is True, f"hosted should request production security: {hosted_preview}", failures)
    require(hosted_preview.get("admin_key_configured") is True, f"hosted should see configured admin key: {hosted_preview}", failures)
    require(hosted_preview.get("direct_create_allowed") is False, f"hosted should not allow direct create: {hosted_preview}", failures)
    require(hosted_preview.get("approval_request_required") is True, f"hosted should require approval request: {hosted_preview}", failures)
    require(hosted_preview.get("approval_recommended") is True, f"hosted should recommend approval: {hosted_preview}", failures)
    require(hosted_preview.get("recommended_path") == "request_approval", f"hosted path mismatch: {hosted_preview}", failures)
    hosted_gate = next((gate for gate in hosted_preview.get("gates", []) if gate.get("id") == "deployment_policy"), {})
    require(hosted_gate.get("status") == "warn", f"hosted deployment gate should warn: {hosted_preview}", failures)
    require("admin-issued" in str(hosted_preview.get("deployment_policy_summary", "")), f"hosted summary should name admin-issued tokens: {hosted_preview}", failures)

    print(json.dumps({
        "operation": "enrollment_hosted_policy_ui_smoke",
        "ok": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "local_direct_create_allowed": local_preview.get("direct_create_allowed") is True,
        "hosted_approval_required": hosted_preview.get("approval_request_required") is True,
        "token_omitted": True,
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
