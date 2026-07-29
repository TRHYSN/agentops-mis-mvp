#!/usr/bin/env python3
"""Smoke-test the read-only operator start-check API surface."""
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_-]{12,}"),
    re.compile(r"agtsess_[A-Za-z0-9_-]{12,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ntn_[A-Za-z0-9_-]{16,}"),
    re.compile(r"AGENTOPS_API_KEY\s*=", re.IGNORECASE),
]
LEDGER_TABLES = [
    "tasks",
    "runs",
    "tool_calls",
    "runtime_events",
    "evaluations",
    "audit_logs",
    "artifacts",
    "approvals",
    "memories",
    "agent_plans",
    "plan_evidence_manifests",
    "workflow_jobs",
]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(base_url: str, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = Request(base_url.rstrip("/") + path, headers=headers or {}, method="GET")
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
            status, payload = http_json(base_url, "/api/local/readiness")
            if status == 200 and payload.get("operation") == "local_readiness":
                return
        except URLError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def db_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        counts: dict[str, int] = {}
        for table in LEDGER_TABLES:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        return counts
    finally:
        conn.close()


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def validate_payload(payload: dict, adapter: str, failures: list[str]) -> None:
    require(payload.get("provider") == "agentops-operator", f"{adapter} provider mismatch: {payload}", failures)
    require(payload.get("operation") == "operator_start_check", f"{adapter} operation mismatch: {payload}", failures)
    require(payload.get("adapter") == adapter, f"{adapter} adapter mismatch: {payload.get('adapter')}", failures)
    require(payload.get("status") in {"ready", "attention", "blocked"}, f"{adapter} bad status: {payload.get('status')}", failures)
    require(payload.get("token_omitted") is True, f"{adapter} token omission missing", failures)
    require(payload.get("live_execution_performed") is False, f"{adapter} must not execute live work", failures)
    safety = payload.get("safety") or {}
    require(safety.get("read_only") is True, f"{adapter} read_only safety missing: {safety}", failures)
    require(safety.get("ledger_mutated") is False, f"{adapter} ledger_mutated must be false: {safety}", failures)
    require(safety.get("server_executes_shell") is False, f"{adapter} server shell must be false: {safety}", failures)
    require(safety.get("token_omitted") is True, f"{adapter} safety token proof missing: {safety}", failures)
    gates = payload.get("gates") or []
    gate_ids = {gate.get("id") for gate in gates}
    for gate_id in {
        "local_readiness",
        "worker_connection_policy",
        "adapter_preflight",
        "runtime_doctor",
        "loop_launch_brief",
        "loop_driver_entry",
        "local_run_path",
        "agent_plan_boundary",
        "live_product_readiness",
    }:
        require(gate_id in gate_ids, f"{adapter} missing gate {gate_id}: {gate_ids}", failures)
    for gate in gates:
        require(gate.get("token_omitted") is True, f"{adapter} gate token proof missing: {gate}", failures)
    worker_policy = payload.get("worker_connection_policy") or {}
    require(worker_policy.get("schema") == "agentops-worker-connection-policy-v1", f"{adapter} worker policy schema missing: {worker_policy}", failures)
    policy_safety = worker_policy.get("safety") if isinstance(worker_policy.get("safety"), dict) else {}
    policy_server_shell = worker_policy.get("server_executes_shell") if "server_executes_shell" in worker_policy else policy_safety.get("server_executes_shell")
    require(policy_server_shell is False, f"{adapter} worker policy server-shell proof missing: {worker_policy}", failures)
    local_run_path = payload.get("local_run_path") or {}
    steps = local_run_path.get("steps") or []
    require(len(steps) >= 8, f"{adapter} local run path too short: {local_run_path}", failures)
    require((local_run_path.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} local run path safety missing", failures)
    current_code_gate = local_run_path.get("current_code_gate") or {}
    require(current_code_gate.get("operation") == "local_current_code_gate", f"{adapter} current-code gate missing: {local_run_path}", failures)
    require(current_code_gate.get("ok") is True and current_code_gate.get("current") is True, f"{adapter} current-code gate should pass: {current_code_gate}", failures)
    require(current_code_gate.get("status") == "current", f"{adapter} current-code gate should be current: {current_code_gate}", failures)
    require("--require-current-code" in str(current_code_gate.get("command") or ""), f"{adapter} current-code command missing: {current_code_gate}", failures)
    require("--expect-head-sha" in str(current_code_gate.get("strict_command") or ""), f"{adapter} strict current-code command missing expected head: {current_code_gate}", failures)
    require("repo_root" not in current_code_gate, f"{adapter} current-code gate should not expose repo root: {current_code_gate}", failures)
    require((current_code_gate.get("safety") or {}).get("read_only") is True, f"{adapter} current-code read-only proof missing: {current_code_gate}", failures)
    require((current_code_gate.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} current-code server-shell boundary missing: {current_code_gate}", failures)
    launch_brief = payload.get("launch_brief") or {}
    require(launch_brief.get("operation") == "operator_loop_launch_brief", f"{adapter} launch brief missing: {launch_brief}", failures)
    require((launch_brief.get("safety") or {}).get("read_only") is True, f"{adapter} launch brief read-only proof missing: {launch_brief}", failures)
    require((launch_brief.get("summary") or {}).get("current_code_ok") is True, f"{adapter} launch brief current-code proof missing: {launch_brief}", failures)
    loop_driver = payload.get("loop_driver_entry") or {}
    loop_commands = loop_driver.get("commands") or {}
    review_snapshot = loop_driver.get("review_snapshot") or {}
    review_summary = review_snapshot.get("summary") or {}
    require(loop_driver.get("operation") == "operator_start_check_loop_driver_entry", f"{adapter} loop driver entry missing: {loop_driver}", failures)
    require((loop_driver.get("safety") or {}).get("read_only") is True, f"{adapter} loop driver entry read-only proof missing: {loop_driver}", failures)
    require((loop_driver.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} loop driver entry mutated ledger: {loop_driver}", failures)
    require((loop_driver.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} loop driver entry server shell proof missing: {loop_driver}", failures)
    require(str(loop_commands.get("preview") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"{adapter} loop driver preview missing: {loop_driver}", failures)
    require(str(loop_commands.get("confirm_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"{adapter} loop driver confirm missing: {loop_driver}", failures)
    require("--confirm-loop" in str(loop_commands.get("confirm_loop") or ""), f"{adapter} loop driver confirm flag missing: {loop_driver}", failures)
    require(str(loop_commands.get("review_queue") or "").startswith("agentops review queue"), f"{adapter} loop driver review command missing: {loop_driver}", failures)
    require(review_snapshot.get("operation") == "loop_driver_record_review_snapshot", f"{adapter} review snapshot missing: {loop_driver}", failures)
    require((review_snapshot.get("safety") or {}).get("read_only") is True, f"{adapter} review snapshot read-only proof missing: {loop_driver}", failures)
    require((review_snapshot.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} review snapshot mutated ledger: {loop_driver}", failures)
    for key in ["review_items_total", "returned_items", "pending_approvals", "memory_candidates"]:
        require(isinstance(review_summary.get(key), int), f"{adapter} review summary {key} missing: {loop_driver}", failures)
    require(review_snapshot.get("summary_omitted") is True, f"{adapter} review summary omission proof missing: {loop_driver}", failures)
    require(review_snapshot.get("raw_content_omitted") is True, f"{adapter} review raw omission proof missing: {loop_driver}", failures)
    require(all(item.get("summary_omitted") is True and item.get("token_omitted") is True for item in (review_snapshot.get("items") or [])), f"{adapter} review items should be compact: {loop_driver}", failures)
    acceptance_packet = payload.get("acceptance_packet") or {}
    packet_decision = acceptance_packet.get("decision") or {}
    packet_commands = acceptance_packet.get("commands") or {}
    packet_summary = acceptance_packet.get("summary") or {}
    require(acceptance_packet.get("operation") == "operator_local_loop_acceptance_packet", f"{adapter} acceptance packet missing: {acceptance_packet}", failures)
    require(acceptance_packet.get("adapter") == adapter, f"{adapter} acceptance packet adapter mismatch: {acceptance_packet}", failures)
    require(acceptance_packet.get("status") == payload.get("status"), f"{adapter} acceptance status mismatch: {acceptance_packet}", failures)
    require((acceptance_packet.get("safety") or {}).get("read_only") is True, f"{adapter} acceptance read-only proof missing: {acceptance_packet}", failures)
    require((acceptance_packet.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} acceptance ledger proof missing: {acceptance_packet}", failures)
    require((acceptance_packet.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} acceptance server-shell proof missing: {acceptance_packet}", failures)
    require(packet_decision.get("agent_plan_required") is True, f"{adapter} acceptance agent-plan gate missing: {acceptance_packet}", failures)
    require(packet_decision.get("current_code_required") is True, f"{adapter} acceptance current-code requirement missing: {acceptance_packet}", failures)
    require(packet_decision.get("current_code_ok") is True, f"{adapter} acceptance current-code proof missing: {acceptance_packet}", failures)
    require(packet_decision.get("knowledge_search_required") is True, f"{adapter} acceptance knowledge gate missing: {acceptance_packet}", failures)
    require(packet_decision.get("base_compare_required") is True, f"{adapter} acceptance base gate missing: {acceptance_packet}", failures)
    require(packet_decision.get("receipt_required") is True, f"{adapter} acceptance receipt gate missing: {acceptance_packet}", failures)
    require(isinstance(packet_summary.get("attention_gates"), list), f"{adapter} acceptance attention gates missing: {acceptance_packet}", failures)
    require(str(packet_commands.get("start_check") or "").startswith(f"agentops operator start-check --adapter {adapter}"), f"{adapter} acceptance start-check command missing: {acceptance_packet}", failures)
    require("--require-current-code" in str(packet_commands.get("current_code_check") or ""), f"{adapter} acceptance current-code command missing: {acceptance_packet}", failures)
    require(str(packet_commands.get("loop_driver_preview") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"{adapter} acceptance loop-driver preview missing: {acceptance_packet}", failures)
    require("--confirm-loop" in str(packet_commands.get("loop_driver_confirm") or ""), f"{adapter} acceptance confirm-loop missing: {acceptance_packet}", failures)
    require(str(packet_commands.get("review_queue") or "").startswith("agentops review queue"), f"{adapter} acceptance review command missing: {acceptance_packet}", failures)
    require(packet_commands.get("receipt_readback") == "agentops operator action-receipts --limit 20", f"{adapter} acceptance receipt readback missing: {acceptance_packet}", failures)
    agent_loop_packet = payload.get("agent_loop_packet") or {}
    agent_loop_commands = agent_loop_packet.get("commands") or {}
    agent_loop_phases = {item.get("phase") for item in (agent_loop_packet.get("phases") or [])}
    agent_loop_phase_commands = agent_loop_packet.get("phase_commands") or {}
    method_gates = agent_loop_packet.get("method_gates") or []
    method_gate_ids = {gate.get("id") for gate in method_gates}
    require(agent_loop_packet.get("operation") == "operator_loop_driver_agent_loop_packet", f"{adapter} agent loop packet missing: {agent_loop_packet}", failures)
    require(agent_loop_packet.get("adapter") == adapter, f"{adapter} agent loop packet adapter mismatch: {agent_loop_packet}", failures)
    require(agent_loop_packet.get("current_phase") in {"preview", "blocked"}, f"{adapter} agent loop phase mismatch: {agent_loop_packet}", failures)
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(agent_loop_phases), f"{adapter} agent loop phases missing: {agent_loop_packet}", failures)
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(set(agent_loop_phase_commands)), f"{adapter} phase command map missing: {agent_loop_packet}", failures)
    require({"read_start_check", "read_current_code", "plan_agent_plan", "retrieve_knowledge", "compare_base_reference", "preflight_adapter", "execute_bounded_loop", "verify_loop", "record_memory_candidate"}.issubset(method_gate_ids), f"{adapter} method gates missing: {agent_loop_packet}", failures)
    require(all(gate.get("token_omitted") is True for gate in method_gates), f"{adapter} method gate token proof missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("start_check") or "").startswith(f"agentops operator start-check --adapter {adapter}"), f"{adapter} agent loop start-check missing: {agent_loop_packet}", failures)
    require("--require-current-code" in str(agent_loop_commands.get("current_code_check") or ""), f"{adapter} agent loop current-code command missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("agent_plan_create") or "").startswith("agentops agent-plan create"), f"{adapter} agent loop plan command missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("knowledge_search") or "").startswith("agentops knowledge search"), f"{adapter} agent loop knowledge command missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("base_reference") or "").startswith("agentops commander repo-map"), f"{adapter} agent loop base-reference command missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("preview_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"{adapter} agent loop preview missing: {agent_loop_packet}", failures)
    require("--confirm-loop" in str(agent_loop_commands.get("confirm_loop") or ""), f"{adapter} agent loop confirm missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("adapter_preflight") or "").endswith(f"--adapter {adapter}"), f"{adapter} agent loop preflight missing: {agent_loop_packet}", failures)
    require(str(agent_loop_commands.get("review_queue") or "").startswith("agentops review queue"), f"{adapter} agent loop review missing: {agent_loop_packet}", failures)
    require((agent_loop_packet.get("safety") or {}).get("read_only") is True, f"{adapter} agent loop read-only proof missing: {agent_loop_packet}", failures)
    require((agent_loop_packet.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} agent loop ledger proof missing: {agent_loop_packet}", failures)
    require((agent_loop_packet.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} agent loop server-shell proof missing: {agent_loop_packet}", failures)
    require(agent_loop_packet.get("live_execution_performed") is False, f"{adapter} agent loop live proof missing: {agent_loop_packet}", failures)
    admission_packet = payload.get("local_loop_admission_packet") or {}
    local_run_path = payload.get("local_run_path") or {}
    local_steps = local_run_path.get("steps") or []
    local_adapter_steps = [step for step in local_steps if isinstance(step, dict) and step.get("adapter") in {"mock", "hermes", "openclaw"}]
    require(local_run_path.get("recommended_adapter") == adapter, f"{adapter} top-level local run path adapter mismatch: {local_run_path}", failures)
    require(bool(local_adapter_steps), f"{adapter} top-level local run path adapter steps missing: {local_run_path}", failures)
    require(all(step.get("adapter") == adapter for step in local_adapter_steps), f"{adapter} top-level local run path contains wrong adapter step: {local_adapter_steps}", failures)
    admission = admission_packet.get("admission") or {}
    deployment = admission_packet.get("local_deployment") or {}
    admission_current_code = deployment.get("current_code_gate") or {}
    service_preview = deployment.get("service_control_preview") or {}
    service_install = deployment.get("service_install") or {}
    service_managed_loop = deployment.get("service_managed_loop") or {}
    managed_execution_path = deployment.get("managed_execution_path") or {}
    managed_execution_commands = managed_execution_path.get("commands") or {}
    managed_execution_gate_ids = {str(gate.get("id")) for gate in managed_execution_path.get("gates") or [] if isinstance(gate, dict)}
    worker_start = deployment.get("worker_start") or {}
    customer_dispatch = deployment.get("customer_worker_dispatch") or {}
    admission_commands = admission_packet.get("commands") or {}
    require(admission_packet.get("operation") == "operator_local_loop_admission_packet", f"{adapter} admission packet missing: {admission_packet}", failures)
    require(admission_packet.get("adapter") == adapter, f"{adapter} admission adapter mismatch: {admission_packet}", failures)
    require(admission.get("method_gate_count") >= 8, f"{adapter} admission method gates missing: {admission_packet}", failures)
    require(admission.get("current_code_ok") is True, f"{adapter} admission current-code proof missing: {admission_packet}", failures)
    require(set(method_gate_ids).issubset(set(admission_packet.get("required_method_gates") or [])), f"{adapter} admission gate ids mismatch: {admission_packet}", failures)
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(set(admission_packet.get("phase_commands") or {})), f"{adapter} admission phase commands missing: {admission_packet}", failures)
    require(service_preview.get("preview_only") is True, f"{adapter} service-control preview proof missing: {admission_packet}", failures)
    require(service_preview.get("server_executes_shell") is False, f"{adapter} service-control server-shell proof missing: {admission_packet}", failures)
    require(service_managed_loop.get("operation") == "local_service_managed_loop_readiness", f"{adapter} service-managed loop projection missing: {admission_packet}", failures)
    require(service_managed_loop.get("service_check_available") is True, f"{adapter} service-managed service-check missing: {admission_packet}", failures)
    require(service_managed_loop.get("service_control_preview_available") is True, f"{adapter} service-managed control preview missing: {admission_packet}", failures)
    require(service_managed_loop.get("receipt_required") is True, f"{adapter} service-managed receipt requirement missing: {admission_packet}", failures)
    require(service_managed_loop.get("control_readback_required") is True, f"{adapter} service-managed readback requirement missing: {admission_packet}", failures)
    require(service_managed_loop.get("service_active_loop_ready") in {True, False}, f"{adapter} service active loop state missing: {service_managed_loop}", failures)
    require(service_managed_loop.get("active_status") in {"loaded", "not_loaded", "unverified"}, f"{adapter} service active status missing: {service_managed_loop}", failures)
    require((service_managed_loop.get("safety") or {}).get("loads_service") is False, f"{adapter} service-managed load boundary missing: {admission_packet}", failures)
    require((service_managed_loop.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} service-managed server-shell proof missing: {admission_packet}", failures)
    top_service_step = next((step for step in local_steps if isinstance(step, dict) and step.get("step_id") == "preview_worker_service_control"), {})
    top_receipt_state = top_service_step.get("receipt_state") or {}
    require(top_service_step.get("source") == f"local_readiness.service_control_preview.{adapter}", f"{adapter} top service-control source mismatch: {top_service_step}", failures)
    require(f"--adapter {adapter}" in str(top_service_step.get("verify_command") or ""), f"{adapter} top service-control verify adapter mismatch: {top_service_step}", failures)
    if service_managed_loop.get("receipt_id"):
        require(top_receipt_state.get("receipt_id") == service_managed_loop.get("receipt_id"), f"{adapter} top service-control receipt mismatch: {top_service_step} vs {service_managed_loop}", failures)
    if service_managed_loop.get("control_readback_id"):
        require(top_receipt_state.get("control_readback_id") == service_managed_loop.get("control_readback_id"), f"{adapter} top service-control readback mismatch: {top_service_step} vs {service_managed_loop}", failures)
    require(managed_execution_path.get("operation") == "operator_service_managed_execution_path", f"{adapter} managed execution path missing: {admission_packet}", failures)
    require(managed_execution_path.get("service_managed_loop_ready") in {True, False}, f"{adapter} managed execution readiness missing: {managed_execution_path}", failures)
    require(managed_execution_path.get("service_active_loop_ready") in {True, False}, f"{adapter} managed active-loop readiness missing: {managed_execution_path}", failures)
    require({"service_managed_loop_ready", "service_active_loop_ready", "agent_plan_required", "knowledge_retrieval_required", "customer_worker_dispatch", "plan_evidence_required", "review_queue_required"}.issubset(managed_execution_gate_ids), f"{adapter} managed execution gates missing: {managed_execution_path}", failures)
    managed_dispatch = str(managed_execution_commands.get("customer_worker_dispatch") or "")
    managed_service_check = str(managed_execution_commands.get("service_check") or "")
    managed_service_receipt = str(managed_execution_commands.get("service_control_receipt") or "")
    managed_service_readback = str(managed_execution_commands.get("service_control_readback") or "")
    managed_service_load = str(managed_execution_commands.get("service_control_load_confirm") or "")
    canonical_service_source = f"local_readiness.service_control_preview.{adapter}"
    canonical_service_signature = str(top_service_step.get("action_signature") or "")
    require(f"--adapter {adapter}" in managed_service_check, f"{adapter} managed service-check command adapter mismatch: {managed_execution_path}", failures)
    require(f"--adapter {adapter}" in managed_service_receipt, f"{adapter} managed service receipt command adapter mismatch: {managed_execution_path}", failures)
    require(f"--adapter {adapter}" in managed_service_readback, f"{adapter} managed service readback command adapter mismatch: {managed_execution_path}", failures)
    require(bool(canonical_service_signature), f"{adapter} canonical service-control signature missing: {top_service_step}", failures)
    require(f"--action-id {canonical_service_source}" in managed_service_receipt, f"{adapter} managed service receipt action id diverged: {managed_execution_path}", failures)
    require(f"--action-signature {canonical_service_signature}" in managed_service_receipt, f"{adapter} managed service receipt signature diverged: {managed_execution_path}", failures)
    require(f"--source {canonical_service_source}" in managed_service_receipt, f"{adapter} managed service receipt source diverged: {managed_execution_path}", failures)
    require(f"--source {canonical_service_source}.control_readback" in managed_service_readback, f"{adapter} managed service readback source diverged: {managed_execution_path}", failures)
    require(f"--adapter {adapter}" in managed_service_load and "--confirm-control" in managed_service_load, f"{adapter} managed service load-confirm command missing: {managed_execution_path}", failures)
    require(managed_dispatch.startswith(("agentops workflow run-task", "agentops workflow customer-worker-task")), f"{adapter} managed dispatch command missing: {managed_execution_path}", failures)
    require(managed_service_readback.startswith("agentops operator record-control-readback"), f"{adapter} managed control-readback command missing: {managed_execution_path}", failures)
    require(str(managed_execution_commands.get("evidence_report") or "").startswith("agentops operator evidence-report --run-id"), f"{adapter} managed evidence command missing: {managed_execution_path}", failures)
    require(str(managed_execution_commands.get("review_queue") or "").startswith("agentops review queue"), f"{adapter} managed review command missing: {managed_execution_path}", failures)
    require((managed_execution_path.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} managed execution server-shell proof missing: {managed_execution_path}", failures)
    require(managed_execution_path.get("live_execution_performed") is False, f"{adapter} managed execution live proof missing: {managed_execution_path}", failures)
    require(service_install.get("preview_only_by_default") is True, f"{adapter} service-install preview proof missing: {admission_packet}", failures)
    require(service_install.get("loads_service") is False, f"{adapter} service-install must not load service: {admission_packet}", failures)
    require(service_install.get("server_executes_shell") is False, f"{adapter} service-install server-shell proof missing: {admission_packet}", failures)
    require(str(service_install.get("preview_command") or "").startswith("agentops worker service-install --manager launchd"), f"{adapter} service-install preview command missing: {admission_packet}", failures)
    require("--confirm-install" in str(service_install.get("confirm_command") or ""), f"{adapter} service-install confirm command missing: {admission_packet}", failures)
    require(str(admission_commands.get("service_check") or "").startswith("agentops worker service-check"), f"{adapter} service-check command missing: {admission_packet}", failures)
    require(str(admission_commands.get("service_install_preview") or "").startswith("agentops worker service-install"), f"{adapter} admission service-install preview command missing: {admission_packet}", failures)
    require("--confirm-install" in str(admission_commands.get("service_install_confirm") or ""), f"{adapter} admission service-install confirm command missing: {admission_packet}", failures)
    require("--require-current-code" in str(admission_commands.get("current_code_check") or ""), f"{adapter} admission current-code command missing: {admission_packet}", failures)
    require(admission_current_code.get("operation") == "local_current_code_gate", f"{adapter} admission current-code deployment proof missing: {admission_packet}", failures)
    require(str(admission_commands.get("preview_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"{adapter} admission preview command missing: {admission_packet}", failures)
    require((admission_packet.get("safety") or {}).get("read_only") is True, f"{adapter} admission read-only proof missing: {admission_packet}", failures)
    require((admission_packet.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} admission ledger proof missing: {admission_packet}", failures)
    require((admission_packet.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} admission server-shell proof missing: {admission_packet}", failures)
    require(admission_packet.get("live_execution_performed") is False, f"{adapter} admission live proof missing: {admission_packet}", failures)
    commands = payload.get("next_commands") or []
    require(any("--require-current-code" in str(command) for command in commands), f"{adapter} current-code next command missing: {commands}", failures)
    require(any("operator loop-launch-packet" in str(command) for command in commands), f"{adapter} launch command missing: {commands}", failures)
    require(any("operator loop-driver" in str(command) for command in commands), f"{adapter} loop-driver command missing: {commands}", failures)
    require(any("review queue" in str(command) for command in commands), f"{adapter} review queue command missing: {commands}", failures)
    if adapter in {"hermes", "openclaw"}:
        summary = payload.get("summary") or {}
        require(summary.get("requires_confirm_run") is True, f"{adapter} confirm-run proof missing: {summary}", failures)
        require(any("--confirm-loop" in str(command) for command in commands), f"{adapter} confirm-loop command missing: {commands}", failures)
        require(packet_decision.get("live_dispatch_requires_confirm_run") is True, f"{adapter} acceptance confirm-run wall missing: {acceptance_packet}", failures)
        require(str(packet_commands.get("execution_mode_confirm") or "").endswith("--confirm-run"), f"{adapter} acceptance execution-mode confirm missing: {acceptance_packet}", failures)
        require(str(packet_commands.get("live_product_readiness") or "").endswith(f"--require-adapter {adapter}"), f"{adapter} acceptance live readiness command missing: {acceptance_packet}", failures)
        require(admission.get("live_dispatch_requires_confirm_run") is True, f"{adapter} admission confirm wall missing: {admission_packet}", failures)
        require("--confirm-run" in str(worker_start.get("command") or ""), f"{adapter} admission worker start confirm missing: {admission_packet}", failures)
        require("--confirm-run" in str(service_install.get("preview_command") or ""), f"{adapter} admission service install confirm-run missing: {admission_packet}", failures)
        require(customer_dispatch.get("requires_confirm_run_flag") is True, f"{adapter} admission dispatch confirm missing: {admission_packet}", failures)
        require("--confirm-run" in str(customer_dispatch.get("command") or ""), f"{adapter} admission dispatch command confirm missing: {admission_packet}", failures)
        live = payload.get("live_product_readiness") or {}
        require(live.get("operation") == "operator_live_product_readiness", f"{adapter} live readiness readback missing: {live}", failures)


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-start-check-api-") as tmp:
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
            before = db_counts(db_path)
            checked = []
            for adapter in ("mock", "hermes", "openclaw"):
                query = urlencode({"adapter": adapter, "limit": "4"})
                status, payload = http_json(base_url, f"/api/operator/start-check?{query}")
                outputs.append(json.dumps(payload, ensure_ascii=False))
                require(status == 200, f"{adapter} API status mismatch: {status} {payload}", failures)
                validate_payload(payload, adapter, failures)
                checked.append({"adapter": adapter, "status": payload.get("status")})
            after = db_counts(db_path)
            require(before == after, f"operator start-check API mutated ledger counts: before={before} after={after}", failures)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            if proc.stdout:
                outputs.append(proc.stdout.read() or "")
            if proc.stderr:
                outputs.append(proc.stderr.read() or "")
    require(not leaked_secret("\n".join(outputs)), "operator start-check API leaked token-like material", failures)
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "operation": "operator_start_check_api_smoke",
        "checked": checked,
        "ledger_mutated": False,
        "secret_leaked": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
