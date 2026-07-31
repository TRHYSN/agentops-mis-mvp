#!/usr/bin/env python3
"""Verify local end-to-end readiness API and CLI output."""
from __future__ import annotations

import argparse
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
CLI = ROOT / "scripts" / "agentops"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/api/dashboard/metrics", timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"server did not become ready: {last_error}")


def start_isolated_server(db_path: Path, port: int, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["AGENTOPS_DB_PATH"] = str(db_path)
    env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
    log_fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
        cwd=ROOT,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._agentops_log_fh = log_fh  # type: ignore[attr-defined]
    return proc


def stop_isolated_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log_fh = getattr(proc, "_agentops_log_fh", None)
    if log_fh:
        log_fh.close()


def http_json(base_url: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": exc.reason}
        return exc.code, body


def http_post_json(base_url: str, path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": exc.reason}
        return exc.code, body


def run_cli(base_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    return subprocess.run(
        [str(CLI), "--base-url", base_url, "local", "readiness"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def leaked_secret(text: str) -> bool:
    markers = ["AGENTOPS_API_KEY", "Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_"]
    return any(marker in text for marker in markers)


def seed_local_stack_service_workers(base_url: str) -> dict[str, str]:
    expected: dict[str, str] = {}
    for adapter in ("hermes", "openclaw"):
        agent_id = f"agt_worker_local_stack_{adapter}"
        expected_status = "error" if adapter == "hermes" else "idle"
        register_status, register_payload = http_post_json(base_url, "/api/agent-gateway/register", {
            "workspace_id": "local-demo",
            "agent_id": agent_id,
            "name": f"Local Stack {adapter.title()} Worker",
            "role": f"{adapter.title()} Worker",
            "runtime_type": adapter,
            "status": expected_status,
        })
        require(register_status in {200, 201}, f"{adapter} local-stack worker registration failed: {register_payload}")
        require((register_payload.get("agent") or {}).get("status") == expected_status, f"{adapter} fixture status mismatch: {register_payload}")
        expected[adapter] = agent_id
    return expected


def seed_fresh_daemon_service_workers(base_url: str) -> dict[str, str]:
    service_scopes = [
        "agents:write",
        "agents:heartbeat",
        "agent_plans:read",
        "agent_plans:write",
        "plan_evidence:read",
        "plan_evidence:write",
        "knowledge:read",
        "knowledge:write",
        "tasks:read",
        "tasks:claim",
        "runs:write",
        "runtime_events:write",
        "toolcalls:write",
        "artifacts:write",
        "memories:propose",
        "evaluations:submit",
        "audit:write",
    ]
    expected: dict[str, str] = {}
    for adapter in ("hermes", "openclaw"):
        agent_id = f"agt_worker_daemon_{adapter}"
        status, enrollment = http_post_json(base_url, "/api/agent-gateway/enrollment/create", {
            "workspace_id": "local-demo",
            "agent_id": agent_id,
            "name": f"Daemon {adapter.title()} Worker",
            "runtime_type": adapter,
            "scopes": service_scopes,
            "ttl_days": 1,
            "heartbeat_timeout_sec": 90,
        })
        require(status == 201 and enrollment.get("token"), f"{adapter} daemon enrollment failed: {status}")
        status, session = http_post_json(
            base_url,
            "/api/agent-gateway/session/create",
            {"ttl_sec": 900, "scopes": service_scopes},
            token=enrollment["token"],
        )
        require(status == 201 and session.get("session_token"), f"{adapter} daemon session failed: {status}")
        status, heartbeat = http_post_json(
            base_url,
            "/api/agent-gateway/heartbeat",
            {"status": "idle", "summary": "Fresh daemon identity precedence fixture."},
            token=session["session_token"],
        )
        require(
            status == 200 and heartbeat.get("session_observation_recorded") is True,
            f"{adapter} daemon heartbeat observation failed: {status}",
        )
        expected[adapter] = agent_id
    return expected


def validate_start_check_service_identity(base_url: str, expected: dict[str, str]) -> None:
    for adapter, expected_agent_id in expected.items():
        status, payload = http_json(base_url, f"/api/operator/start-check?adapter={adapter}&limit=4")
        require(status == 200, f"{adapter} start-check failed: {status} {payload}")
        local_run_path = payload.get("local_run_path") or {}
        local_service = local_run_path.get("service_managed_loop") or {}
        admission = payload.get("local_loop_admission_packet") or {}
        deployment = admission.get("local_deployment") or {}
        admission_service = deployment.get("service_managed_loop") or {}
        managed_commands = (deployment.get("managed_execution_path") or {}).get("commands") or {}
        require(local_service.get("agent_id") == expected_agent_id, f"{adapter} compact start-check lost service identity: {local_service}")
        require(admission_service.get("agent_id") == expected_agent_id, f"{adapter} admission packet lost service identity: {admission_service}")
        require(f"--agent-id {expected_agent_id}" in str(managed_commands.get("service_check") or ""), f"{adapter} managed service-check targets wrong identity: {managed_commands}")
        require(f"--agent-id {expected_agent_id}" in str(managed_commands.get("service_control_load_confirm") or ""), f"{adapter} managed service load targets wrong identity: {managed_commands}")
        bootstrap_status, bootstrap = http_json(base_url, f"/api/operator/loop-bootstrap?adapter={adapter}&limit=4")
        require(bootstrap_status == 200, f"{adapter} loop-bootstrap failed: {bootstrap_status} {bootstrap}")
        bootstrap_item = next((item for item in (bootstrap.get("items") or []) if item.get("adapter") == adapter), {})
        bootstrap_commands = bootstrap_item.get("commands") or {}
        for command_name in ("service_check", "service_install_preview", "service_install_confirm", "service_control_load_confirm"):
            command = str(bootstrap_commands.get(command_name) or "")
            require(f"--agent-id {expected_agent_id}" in command, f"{adapter} loop-bootstrap {command_name} targets wrong identity: {command}")
            other_agent_id = (
                f"agt_worker_local_stack_{adapter}"
                if expected_agent_id == f"agt_worker_daemon_{adapter}"
                else f"agt_worker_daemon_{adapter}"
            )
            require(f"--agent-id {other_agent_id}" not in command, f"{adapter} loop-bootstrap retained another service identity: {command}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate(payload: dict) -> None:
    require(payload.get("provider") == "agentops-local", f"wrong provider: {payload}")
    require(payload.get("operation") == "local_readiness", f"wrong operation: {payload}")
    require(payload.get("status") in {"ready", "attention", "blocked"}, f"bad status: {payload}")
    require(payload.get("live_execution_performed") is False, "readiness must not execute live work")
    require(payload.get("token_omitted") is True, "token omission proof missing")
    require(isinstance(payload.get("local_demo_ready"), bool), "local_demo_ready flag missing")
    require(isinstance(payload.get("local_security_boundary_ok"), bool), "local_security_boundary_ok flag missing")
    require(isinstance(payload.get("production_ready"), bool), "production_ready flag missing")
    require(payload.get("local_security_boundary_ok") is True, f"loopback local security boundary should be accepted: {payload.get('security_production_readiness')}")
    gates = payload.get("gates") or []
    gate_ids = {gate.get("id") for gate in gates}
    for gate_id in {"agent_gateway", "worker_fleet", "production_security", "adapter_route", "knowledge_memory", "evidence_chain", "commander_synthesis_loop", "local_harness_proof", "runbook"}:
        require(gate_id in gate_ids, f"missing gate {gate_id}: {payload}")
    require("live_acceptance_freshness" in gate_ids, f"missing live acceptance gate: {payload}")
    instance_gate = next((gate for gate in gates if gate.get("id") == "running_instance_freshness"), {})
    require(instance_gate.get("ok") is True, f"running instance freshness gate should pass: {instance_gate}")
    require("require-current-code" in (instance_gate.get("next_action") or ""), f"running instance gate should route strict CLI: {instance_gate}")
    running_instance = payload.get("running_instance") or {}
    require(running_instance.get("operation") == "running_instance_identity", f"running instance identity missing: {running_instance}")
    require(running_instance.get("status") == "current", f"running instance should be current: {running_instance}")
    require(running_instance.get("server_started_after_source_mtime") is True, f"running instance source freshness missing: {running_instance}")
    require((running_instance.get("safety") or {}).get("read_only") is True, f"running instance identity must be read-only: {running_instance}")
    evidence = payload.get("evidence") or {}
    for key in ["tasks", "runs", "tool_calls", "evaluations", "audit_logs", "artifacts", "memories", "approvals", "closed_loop_runs"]:
        require(isinstance(evidence.get(key), int), f"missing evidence count {key}: {evidence}")
    for key in ["knowledge_documents", "knowledge_chunks", "knowledge_chunk_fts_rows", "knowledge_workspace_documents", "knowledge_workspace_chunks"]:
        require(isinstance(evidence.get(key), int), f"missing knowledge evidence count {key}: {evidence}")
    for key in ["has_indexed_knowledge", "has_workspace_knowledge", "has_memory_or_knowledge"]:
        require(isinstance(evidence.get(key), bool), f"missing knowledge readiness bool {key}: {evidence}")
    knowledge_gate = next((gate for gate in gates if gate.get("id") == "knowledge_memory"), {})
    require("knowledge" in (knowledge_gate.get("detail") or "").lower(), f"knowledge gate should expose indexed knowledge counts: {knowledge_gate}")
    require(
        "knowledge evidence-packet" in (knowledge_gate.get("next_action") or "")
        or "knowledge search" in (knowledge_gate.get("next_action") or "")
        or "knowledge index" in (knowledge_gate.get("next_action") or ""),
        f"knowledge gate should route to knowledge CLI action: {knowledge_gate}",
    )
    packet = payload.get("knowledge_retrieval_evidence") or {}
    require(packet.get("operation") == "knowledge_retrieval_evidence_packet", f"knowledge retrieval evidence packet missing: {packet}")
    require((packet.get("safety") or {}).get("read_only") is True, f"knowledge retrieval packet must be read-only: {packet}")
    require(packet.get("query_omitted") is True, f"knowledge retrieval packet must omit query: {packet}")
    require((packet.get("primary_search") or {}).get("query_omitted") is True, f"knowledge primary search must omit query: {packet}")
    require((packet.get("metrics") or {}).get("recall_at_5") is not None, f"knowledge retrieval metrics missing: {packet}")
    for key in ["commander_synthesis_artifacts", "commander_synthesis_pending_reviews", "commander_synthesis_promoted_memories", "commander_synthesis_promoted_deliveries"]:
        require(isinstance(evidence.get(key), int), f"missing synthesis evidence count {key}: {evidence}")
    for key in ["live_acceptance_fresh_adapters", "live_acceptance_latest_failed_adapters", "live_acceptance_missing_adapters"]:
        require(isinstance(evidence.get(key), int), f"missing live acceptance evidence count {key}: {evidence}")
    for key in [
        "local_harness_proof_fresh_adapters",
        "local_harness_proof_fresh_real_runtime_adapters",
        "local_harness_proof_fresh_mock_fallback",
        "local_harness_proof_latest_failed_adapters",
        "local_harness_proof_missing_adapters",
    ]:
        require(isinstance(evidence.get(key), int), f"missing local harness proof evidence count {key}: {evidence}")
    live = payload.get("live_acceptance_readiness") or {}
    require(live.get("operation") == "live_acceptance_readiness", f"live acceptance readiness missing: {live}")
    require(live.get("live_execution_performed") is False, f"live acceptance readback must be read-only: {live}")
    require((live.get("safety") or {}).get("read_only") is True, f"live acceptance safety missing: {live}")
    adapters = live.get("adapters") or {}
    for adapter in ["hermes", "openclaw"]:
        require(adapter in adapters, f"live acceptance missing adapter {adapter}: {live}")
        require(adapters[adapter].get("token_omitted") is True, f"live acceptance adapter token omission missing: {adapters[adapter]}")
    harness = payload.get("local_harness_proof_readiness") or {}
    require(harness.get("operation") == "local_harness_proof_readiness", f"local harness proof missing: {harness}")
    require(harness.get("live_execution_performed") is False, f"local harness proof must be read-only: {harness}")
    require((harness.get("safety") or {}).get("read_only") is True, f"local harness proof safety missing: {harness}")
    governed_launch = harness.get("governed_launch_packet") or {}
    require(governed_launch.get("operation") == "local_harness_proof_governed_launch_packet", f"local harness governed launch packet missing: {harness}")
    require((governed_launch.get("safety") or {}).get("read_only") is True, f"local harness governed launch must be read-only: {governed_launch}")
    require("agentops workflow customer-worker-task" in (governed_launch.get("preferred_entrypoint") or ""), f"local harness governed launch should use worker workflow: {governed_launch}")
    harness_gate = next((gate for gate in gates if gate.get("id") == "local_harness_proof"), {})
    require("local-harness-proof" in (harness_gate.get("next_action") or ""), f"local harness gate should route to CLI proof: {harness_gate}")
    harness_adapters = harness.get("adapters") or {}
    for adapter in ["mock", "hermes", "openclaw"]:
        require(adapter in harness_adapters, f"local harness proof missing adapter {adapter}: {harness}")
        require(harness_adapters[adapter].get("token_omitted") is True, f"local harness adapter token omission missing: {harness_adapters[adapter]}")
        adapter_launch = harness_adapters[adapter].get("governed_launch") or {}
        require("agentops workflow customer-worker-task" in (adapter_launch.get("preview_command") or ""), f"{adapter} governed launch preview must use worker workflow: {adapter_launch}")
        if adapter in {"hermes", "openclaw"}:
            require(adapter_launch.get("confirm_required") is True, f"{adapter} governed launch should require confirm: {adapter_launch}")
            require("--confirm-run" in (adapter_launch.get("confirmed_command") or ""), f"{adapter} governed launch confirmed command missing confirm: {adapter_launch}")
    lifecycle = payload.get("commander_synthesis_lifecycle") or {}
    require(lifecycle.get("status") in {"empty", "created", "review_pending", "promotion_available", "promoted"}, f"bad synthesis lifecycle: {lifecycle}")
    require((lifecycle.get("safety") or {}).get("read_only") is True, f"synthesis lifecycle must be read-only: {lifecycle}")
    if lifecycle.get("status") == "empty":
        lifecycle_next = " ".join(str(action) for action in (lifecycle.get("next_actions") or []))
        require("commander plan" in lifecycle_next, f"empty synthesis lifecycle should route agents to planning before synthesis: {lifecycle}")
        require("commander synthesize" not in lifecycle_next, f"empty synthesis lifecycle should not suggest impossible synthesis: {lifecycle}")
    require(isinstance(payload.get("next_actions"), list), "next_actions must be a list")
    local_run_path = payload.get("local_run_path") or []
    require(isinstance(local_run_path, list) and len(local_run_path) >= 8, f"local_run_path missing or too short: {local_run_path}")
    step_ids = {step.get("step_id") for step in local_run_path}
    for step_id in {"start_local_stack", "inspect_local_readiness", "select_worker_adapter", "start_selected_worker", "preview_worker_service_control", "dispatch_customer_task", "verify_ledger_evidence", "prove_live_product_readiness"}:
        require(step_id in step_ids, f"missing local run path step {step_id}: {local_run_path}")
    service_step = next((step for step in local_run_path if step.get("step_id") == "preview_worker_service_control"), {})
    require("service-control" in service_step.get("command", ""), f"service-control preview command missing: {service_step}")
    require(service_step.get("service_control_preview") is True, f"service-control preview flag missing: {service_step}")
    require(service_step.get("mutating") is False and service_step.get("live_execution") is False, f"service-control preview should be non-mutating: {service_step}")
    require(service_step.get("receipt_required") is True, f"service-control receipt flag missing: {service_step}")
    require(service_step.get("control_readback_required") is True, f"service-control control readback flag missing: {service_step}")
    require("record-action-receipt" in str(service_step.get("receipt_record_command") or ""), f"service-control receipt preview command missing: {service_step}")
    require("--confirm-record" not in str(service_step.get("receipt_record_command") or ""), f"service-control receipt preview should not confirm: {service_step}")
    require("record-action-receipt" in str(service_step.get("receipt_verify_record_command") or ""), f"service-control verify receipt command missing: {service_step}")
    require("--confirm-record" in str(service_step.get("receipt_verify_record_command") or ""), f"service-control verify receipt command should confirm record: {service_step}")
    require(str(service_step.get("action_signature") or ""), f"service-control action signature missing: {service_step}")
    service_managed_loop = payload.get("service_managed_loop") or {}
    require(service_managed_loop.get("operation") == "local_service_managed_loop_readiness", f"service-managed loop projection missing: {service_managed_loop}")
    require(service_managed_loop.get("adapter") in {"mock", "hermes", "openclaw"}, f"service-managed loop adapter missing: {service_managed_loop}")
    require(service_managed_loop.get("install_preview_available") is True, f"service-managed install preview missing: {service_managed_loop}")
    require(service_managed_loop.get("install_confirm_available") is True, f"service-managed install confirm missing: {service_managed_loop}")
    require(service_managed_loop.get("service_check_available") is True, f"service-managed service-check missing: {service_managed_loop}")
    require(service_managed_loop.get("service_control_preview_available") is True, f"service-managed control preview missing: {service_managed_loop}")
    require(service_managed_loop.get("receipt_required") is True, f"service-managed receipt requirement missing: {service_managed_loop}")
    require(service_managed_loop.get("control_readback_required") is True, f"service-managed readback requirement missing: {service_managed_loop}")
    require(service_managed_loop.get("service_active_loop_ready") in {True, False}, f"service-managed active loop state missing: {service_managed_loop}")
    require(service_managed_loop.get("active_status") in {"loaded", "not_loaded", "unverified"}, f"service-managed active status missing: {service_managed_loop}")
    service_managed_commands = service_managed_loop.get("commands") or {}
    require(str(service_managed_commands.get("service_control_load_confirm") or "").startswith("agentops worker service-control"), f"service-managed load confirm command missing: {service_managed_loop}")
    require("--confirm-control" in str(service_managed_commands.get("service_control_load_confirm") or ""), f"service-managed load command must require confirm-control: {service_managed_loop}")
    require(str(service_managed_commands.get("record_control_readback") or "").startswith("agentops operator record-control-readback"), f"service-managed control-readback command missing: {service_managed_loop}")
    require("--confirm-record" in str(service_managed_commands.get("record_control_readback") or ""), f"service-managed control-readback command should be explicit-confirm: {service_managed_loop}")
    require("service_check_ok" in str(service_managed_commands.get("record_control_readback") or ""), f"service-managed control-readback command should carry service-check proof fields: {service_managed_loop}")
    require("operator_must_update_after_service_check" in str(service_managed_commands.get("record_control_readback") or ""), f"service-managed control-readback command should warn operators to update readback after service-check: {service_managed_loop}")
    require((service_managed_loop.get("safety") or {}).get("server_executes_shell") is False, f"service-managed server-shell proof missing: {service_managed_loop}")
    require((service_managed_loop.get("safety") or {}).get("loads_service") is False, f"service-managed load boundary missing: {service_managed_loop}")
    require((service_managed_loop.get("safety") or {}).get("token_omitted") is True, f"service-managed token omission missing: {service_managed_loop}")
    service_managed_loops = payload.get("service_managed_loops") or {}
    require({"hermes", "openclaw"}.issubset(set(service_managed_loops)), f"adapter-scoped service-managed loops missing: {service_managed_loops}")
    for adapter_name in ["hermes", "openclaw"]:
        scoped_loop = service_managed_loops.get(adapter_name) or {}
        scoped_commands = scoped_loop.get("commands") or {}
        service_agent_id = scoped_loop.get("agent_id")
        require(scoped_loop.get("adapter") == adapter_name, f"{adapter_name} service-managed loop adapter mismatch: {scoped_loop}")
        require(service_agent_id in {f"agt_worker_daemon_{adapter_name}", f"agt_worker_local_stack_{adapter_name}"}, f"{adapter_name} service identity is not canonical: {scoped_loop}")
        require(f"--agent-id {service_agent_id}" in str(scoped_commands.get("service_check") or ""), f"{adapter_name} service-check identity mismatch: {scoped_loop}")
        require(f"--agent-id {service_agent_id}" in str(scoped_commands.get("service_control_restart_confirm") or ""), f"{adapter_name} restart identity mismatch: {scoped_loop}")
        require(f"--adapter {adapter_name}" in str(scoped_commands.get("service_check") or ""), f"{adapter_name} service-check command mismatch: {scoped_loop}")
        require(f"--adapter {adapter_name}" in str(scoped_commands.get("record_control_readback") or ""), f"{adapter_name} readback command mismatch: {scoped_loop}")
        require(f"--adapter {adapter_name}" in str(scoped_commands.get("service_control_load_confirm") or ""), f"{adapter_name} load confirm command mismatch: {scoped_loop}")
        require("service_check_ok" in str(scoped_commands.get("record_control_readback") or ""), f"{adapter_name} readback proof fields missing: {scoped_loop}")
    for step in local_run_path:
        require(step.get("command"), f"local run path step missing command: {step}")
        require(step.get("copy_only") is True, f"local run path step must be copy-only: {step}")
        require(step.get("server_executes_shell") is False, f"local run path must not grant server shell execution: {step}")
        require(step.get("token_omitted") is True, f"local run path step must omit tokens: {step}")
        require("Authorization:" not in json.dumps(step), f"local run path leaked auth header: {step}")
    require(payload.get("contract") and "single local" in payload.get("contract"), "local contract missing")
    security = payload.get("security_production_readiness") or {}
    require(security.get("operation") == "production_readiness", f"security readiness missing: {security}")
    require(security.get("token_omitted") is True, "security readiness token omission proof missing")
    require(security.get("live_execution_performed") is False, "security readiness must not execute live work")
    security_gate = next((gate for gate in gates if gate.get("id") == "production_security"), {})
    require(security_gate.get("ok") is True, f"local readiness should accept loopback local-dev security boundary: {security_gate}")
    require("production_ready=" in (security_gate.get("detail") or ""), f"security gate should still expose production readiness: {security_gate}")


def exercise_service_control_receipt_readback(base_url: str, payload: dict) -> dict:
    local_run_path = payload.get("local_run_path") or []
    service_step = next((step for step in local_run_path if step.get("step_id") == "preview_worker_service_control"), {})
    receipt_body = {
        "action_command": service_step.get("command"),
        "verify_command": service_step.get("verify_command"),
        "action_id": service_step.get("step_id"),
        "action_signature": service_step.get("action_signature"),
        "source": service_step.get("source") or "local_readiness.service_control_preview",
        "status": "verified",
        "result_summary": "Worker service-control preview inspected and service-check reviewed.",
    }
    expected_signature = str(receipt_body.get("action_signature") or "")
    require(expected_signature, f"service-control receipt signature missing: {service_step}")
    wrong_signature = f"{expected_signature[:-1]}{'0' if expected_signature[-1] != '0' else '1'}"
    stale_status, stale_payload = http_post_json(base_url, "/api/operator/action-receipts", {
        **receipt_body,
        "action_signature": wrong_signature,
        "result_summary": "Stale signature fixture; must not satisfy the current action.",
    })
    require(stale_status == 201, f"stale-signature receipt record failed: {stale_status} {stale_payload}")
    status_code, stale_readiness = http_json(base_url, "/api/local/readiness")
    require(status_code == 200, f"local readiness reread after stale signature failed: {status_code} {stale_readiness}")
    stale_step = next((step for step in (stale_readiness.get("local_run_path") or []) if step.get("step_id") == "preview_worker_service_control"), {})
    stale_state = stale_step.get("receipt_state") or {}
    require(stale_state.get("match") == "stale", f"same-command wrong-signature receipt was accepted as current: {stale_step}")
    require(stale_state.get("current") is False, f"stale signature receipt reported current: {stale_step}")
    require(stale_state.get("verified") is False, f"stale signature receipt reported verified: {stale_step}")

    receipt_status, receipt_payload = http_post_json(base_url, "/api/operator/action-receipts", receipt_body)
    require(receipt_status == 201, f"service-control receipt record failed: {receipt_status} {receipt_payload}")
    receipt = receipt_payload.get("receipt") or {}
    receipt_id = receipt.get("receipt_id")
    require(str(receipt_id or ""), f"service-control receipt id missing: {receipt_payload}")
    require(receipt.get("status") == "verified", f"service-control receipt should be verified: {receipt}")
    require(receipt.get("source") == "local_readiness.service_control_preview", f"service-control source mismatch: {receipt}")
    readback_status, readback_payload = http_post_json(base_url, "/api/operator/action-receipts/control-readback", {
        "receipt_id": receipt_id,
        "source": "local_readiness.service_control_preview.control_readback",
        "control_readback": {
            "before": {
                "step_id": service_step.get("step_id"),
                "status": service_step.get("status"),
                "service_control_preview": True,
            },
            "after": {
                "verify_command": service_step.get("verify_command"),
                "service_check_expected": True,
                "service_check_ok": False,
                "service_file_exists": False,
                "confirm_gate_ok": False,
                "relaunch_policy_ok": False,
                "confirmed_os_mutation": False,
                "operator_must_update_after_service_check": True,
            },
            "self_check": {
                "copy_only": True,
                "server_executes_shell": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "cache": {
                "refresh_cache_required_after_receipt": True,
            },
            "token_omitted": True,
        },
    })
    require(readback_status == 201, f"service-control readback record failed: {readback_status} {readback_payload}")
    status_code, failed_readback_payload = http_json(base_url, "/api/local/readiness")
    require(status_code == 200, f"local readiness reread after failed service-check failed: {status_code} {failed_readback_payload}")
    validate(failed_readback_payload)
    failed_service_loop = failed_readback_payload.get("service_managed_loop") or {}
    require(failed_service_loop.get("service_managed_loop_ready") is False, f"service-managed loop should not be ready when service_check_ok=false: {failed_service_loop}")
    require(failed_service_loop.get("checked_status") == "service_check_failed", f"failed service-check readback should be explicit: {failed_service_loop}")

    receipt_status, receipt_payload = http_post_json(base_url, "/api/operator/action-receipts", receipt_body)
    require(receipt_status == 201, f"service-control second receipt record failed: {receipt_status} {receipt_payload}")
    receipt = receipt_payload.get("receipt") or {}
    receipt_id = receipt.get("receipt_id")
    require(str(receipt_id or ""), f"service-control second receipt id missing: {receipt_payload}")
    readback_status, readback_payload = http_post_json(base_url, "/api/operator/action-receipts/control-readback", {
        "receipt_id": receipt_id,
        "source": "local_readiness.service_control_preview.control_readback",
        "control_readback": {
            "before": {
                "step_id": service_step.get("step_id"),
                "status": service_step.get("status"),
                "service_control_preview": True,
            },
            "after": {
                "verify_command": service_step.get("verify_command"),
                "service_check_expected": True,
                "service_check_ok": True,
                "service_file_exists": True,
                "service_loaded": False,
                "confirm_gate_ok": True,
                "relaunch_policy_ok": True,
                "confirmed_os_mutation": False,
            },
            "self_check": {
                "copy_only": True,
                "server_executes_shell": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "cache": {
                "refresh_cache_required_after_receipt": True,
            },
            "token_omitted": True,
        },
    })
    require(readback_status == 201, f"service-control passing readback record failed: {readback_status} {readback_payload}")
    status_code, reread_payload = http_json(base_url, "/api/local/readiness")
    require(status_code == 200, f"local readiness reread failed: {status_code} {reread_payload}")
    validate(reread_payload)
    updated_step = next((step for step in (reread_payload.get("local_run_path") or []) if step.get("step_id") == "preview_worker_service_control"), {})
    receipt_state = updated_step.get("receipt_state") or {}
    require(receipt_state.get("verified") is True, f"service-control receipt not read back as verified: {updated_step}")
    require(receipt_state.get("control_readback_attached") is True, f"service-control control readback not attached: {updated_step}")
    require(str(receipt_state.get("control_readback_hash") or ""), f"service-control control readback hash missing: {updated_step}")
    service_managed_loop = reread_payload.get("service_managed_loop") or {}
    require(service_managed_loop.get("service_managed_loop_ready") is True, f"service-managed loop not ready after readback: {service_managed_loop}")
    require(service_managed_loop.get("installed_status") == "operator_verified_service_check", f"service-managed install status not verified: {service_managed_loop}")
    require(service_managed_loop.get("checked_status") == "operator_verified_service_check", f"service-managed checked status not verified: {service_managed_loop}")
    require(service_managed_loop.get("service_check_ok") is True, f"service-managed service_check_ok not read back: {service_managed_loop}")
    require(service_managed_loop.get("service_file_exists") is True, f"service-managed service_file_exists not read back: {service_managed_loop}")
    require(service_managed_loop.get("service_confirm_gate_ok") is True, f"service-managed confirm gate not read back: {service_managed_loop}")
    require(service_managed_loop.get("service_relaunch_policy_ok") is True, f"service-managed relaunch policy not read back: {service_managed_loop}")
    require(service_managed_loop.get("service_loaded") is False, f"isolated fixture should distinguish installed service from loaded service: {service_managed_loop}")
    require(service_managed_loop.get("service_active_loop_ready") is False, f"unloaded service must not be active-loop ready: {service_managed_loop}")
    require(service_managed_loop.get("active_status") == "not_loaded", f"unloaded service active status mismatch: {service_managed_loop}")
    require(str(service_managed_loop.get("control_readback_hash") or ""), f"service-managed readback hash missing: {service_managed_loop}")
    service_managed_loops = reread_payload.get("service_managed_loops") or {}
    recommended_adapter = service_managed_loop.get("adapter")
    for adapter_name, scoped_loop in service_managed_loops.items():
        if adapter_name == recommended_adapter:
            require(scoped_loop.get("service_managed_loop_ready") is True, f"recommended adapter scoped loop should mirror ready state: {service_managed_loops}")
        else:
            require(scoped_loop.get("service_managed_loop_ready") is False, f"non-reviewed adapter inherited service readiness: {service_managed_loops}")
    return reread_payload


def run_checks(base_url: str, *, exercise_writeback: bool = False, expected_service_agents: dict[str, str] | None = None) -> int:
    try:
        status_code, api_payload = http_json(base_url, "/api/local/readiness")
        require(status_code == 200, f"local readiness API failed: {status_code} {api_payload}")
        validate(api_payload)
        for adapter, expected_agent_id in (expected_service_agents or {}).items():
            scoped_loop = (api_payload.get("service_managed_loops") or {}).get(adapter) or {}
            require(scoped_loop.get("agent_id") == expected_agent_id, f"{adapter} did not select fresh service identity: {scoped_loop}")
            require(scoped_loop.get("agent_identity_source") == "fresh_service_worker", f"{adapter} identity source mismatch: {scoped_loop}")
            require(scoped_loop.get("agent_registered") is True, f"{adapter} registered identity proof missing: {scoped_loop}")
        if expected_service_agents:
            validate_start_check_service_identity(base_url, expected_service_agents)
        receipt_readback_exercised = False
        if exercise_writeback:
            api_payload = exercise_service_control_receipt_readback(base_url, api_payload)
            receipt_readback_exercised = True

        proc = run_cli(base_url)
        require(proc.returncode == 0, f"CLI local readiness failed: {proc.stderr or proc.stdout}")
        require(not leaked_secret(proc.stdout + proc.stderr), "CLI local readiness leaked token-like material")
        cli_payload = json.loads(proc.stdout)
        validate(cli_payload)

        result = {
            "ok": True,
            "api_status": api_payload.get("status"),
            "cli_status": cli_payload.get("status"),
            "gate_count": len(api_payload.get("gates") or []),
            "closed_loop_runs": (api_payload.get("evidence") or {}).get("closed_loop_runs"),
            "recommended_adapter": (api_payload.get("adapter_readiness") or {}).get("recommended_adapter"),
            "service_control_receipt_readback_exercised": receipt_readback_exercised,
            "secret_leaked": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AgentOps MIS local readiness closure.")
    parser.add_argument("--base-url", default=os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--isolated-fixture", action="store_true", help="Run against a temporary server and SQLite database.")
    args = parser.parse_args()
    if args.isolated_fixture:
        with tempfile.TemporaryDirectory(prefix="agentops-local-readiness-") as tmp:
            tmp_path = Path(tmp)
            port = free_port()
            base_url = f"http://127.0.0.1:{port}"
            proc = start_isolated_server(tmp_path / "agentops_mis.db", port, tmp_path / "server.log")
            try:
                wait_for_server(base_url)
                registered_service_agents = seed_local_stack_service_workers(base_url)
                validate_start_check_service_identity(base_url, registered_service_agents)
                expected_service_agents = seed_fresh_daemon_service_workers(base_url)
                return run_checks(
                    base_url,
                    exercise_writeback=True,
                    expected_service_agents=expected_service_agents,
                )
            finally:
                stop_isolated_server(proc)
    return run_checks(args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
