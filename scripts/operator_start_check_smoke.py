#!/usr/bin/env python3
"""Smoke-test the read-only operator start-check CLI aggregate."""
from __future__ import annotations

import argparse
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CLI = ROOT / "scripts" / "agentops"
from agentops_mis_core.operator_start_check import (
    compact_runtime_current_code_gate,
    operator_agent_loop_packet,
    operator_start_check_local_readiness_gate,
)
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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def run_start_check(base_url: str, adapter: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), "--base-url", base_url, "operator", "start-check", "--adapter", adapter, "--limit", "4"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def http_json(base_url: str, path: str, query: dict[str, str] | None = None) -> tuple[int, dict]:
    suffix = f"?{urlencode(query or {})}" if query else ""
    req = Request(base_url.rstrip("/") + path + suffix, headers={"Accept": "application/json"})
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


def validate(payload: dict, adapter: str) -> None:
    require(payload.get("provider") == "agentops-operator", f"wrong provider: {payload}")
    require(payload.get("operation") == "operator_start_check", f"wrong operation: {payload}")
    require(payload.get("adapter") == adapter, f"wrong adapter: {payload.get('adapter')}")
    require(payload.get("status") in {"ready", "attention", "blocked"}, f"bad status: {payload.get('status')}")
    require(payload.get("token_omitted") is True, "token omission proof missing")
    require(payload.get("live_execution_performed") is False, "start-check must not execute live work")
    safety = payload.get("safety") or {}
    require(safety.get("read_only") is True, f"safety.read_only missing: {safety}")
    require(safety.get("ledger_mutated") is False, f"safety.ledger_mutated must be false: {safety}")
    require(safety.get("server_executes_shell") is False, f"server shell must be false: {safety}")
    require(safety.get("token_omitted") is True, f"safety token proof missing: {safety}")
    gates = payload.get("gates") or []
    gate_ids = {gate.get("id") for gate in gates}
    for gate_id in {
        "local_readiness",
        "worker_connection_policy",
        "current_code_gate",
        "adapter_preflight",
        "runtime_doctor",
        "loop_launch_brief",
        "loop_driver_entry",
        "local_run_path",
        "agent_plan_boundary",
        "live_product_readiness",
    }:
        require(gate_id in gate_ids, f"missing start-check gate {gate_id}: {gate_ids}")
    for gate in gates:
        require(gate.get("token_omitted") is True, f"gate token proof missing: {gate}")
    worker_policy = payload.get("worker_connection_policy") or {}
    require(worker_policy.get("schema") == "agentops-worker-connection-policy-v1", f"worker policy schema missing: {worker_policy}")
    worker_policy_safety = worker_policy.get("safety") if isinstance(worker_policy.get("safety"), dict) else {}
    worker_policy_token_omitted = worker_policy.get("token_omitted") if "token_omitted" in worker_policy else worker_policy_safety.get("token_omitted", True)
    worker_policy_server_shell = worker_policy.get("server_executes_shell") if "server_executes_shell" in worker_policy else worker_policy_safety.get("server_executes_shell")
    require(worker_policy_token_omitted is not False, f"worker policy token proof missing: {worker_policy}")
    require(worker_policy_server_shell is False, f"worker policy must be copy-only: {worker_policy}")
    local_run_path = payload.get("local_run_path") or {}
    steps = local_run_path.get("steps") or []
    require(len(steps) >= 8, f"local run path too short: {local_run_path}")
    require((local_run_path.get("safety") or {}).get("server_executes_shell") is False, f"local run path safety missing: {local_run_path}")
    current_code_gate = local_run_path.get("current_code_gate") or {}
    require(current_code_gate.get("operation") == "local_current_code_gate", f"current-code gate missing: {local_run_path}")
    require(current_code_gate.get("ok") is True and current_code_gate.get("current") is True, f"current-code gate should pass: {current_code_gate}")
    require(current_code_gate.get("status") == "current", f"current-code gate should be current: {current_code_gate}")
    require("--require-current-code" in str(current_code_gate.get("command") or ""), f"current-code command missing: {current_code_gate}")
    require("--expect-head-sha" in str(current_code_gate.get("strict_command") or ""), f"strict current-code command missing expected head: {current_code_gate}")
    require("repo_root" not in current_code_gate, f"current-code gate should not expose repo root: {current_code_gate}")
    require((current_code_gate.get("safety") or {}).get("read_only") is True, f"current-code read-only proof missing: {current_code_gate}")
    require((current_code_gate.get("safety") or {}).get("server_executes_shell") is False, f"current-code server-shell boundary missing: {current_code_gate}")
    launch_brief = payload.get("launch_brief") or {}
    require(launch_brief.get("operation") == "operator_loop_launch_brief", f"launch brief missing: {launch_brief}")
    require((launch_brief.get("safety") or {}).get("read_only") is True, f"launch brief read-only proof missing: {launch_brief}")
    require((launch_brief.get("summary") or {}).get("current_code_ok") is True, f"launch brief current-code proof missing: {launch_brief}")
    required_ledgers = ((launch_brief.get("summary") or {}).get("required_ledgers") or [])
    require("memories" in required_ledgers, f"launch brief missing memories ledger: {launch_brief}")
    require("memory_review" in required_ledgers, f"launch brief missing memory review gate: {launch_brief}")
    loop_driver = payload.get("loop_driver_entry") or {}
    loop_commands = loop_driver.get("commands") or {}
    review_snapshot = loop_driver.get("review_snapshot") or {}
    review_summary = review_snapshot.get("summary") or {}
    require(loop_driver.get("operation") == "operator_start_check_loop_driver_entry", f"loop driver entry missing: {loop_driver}")
    require((loop_driver.get("safety") or {}).get("read_only") is True, f"loop driver entry read-only proof missing: {loop_driver}")
    require((loop_driver.get("safety") or {}).get("ledger_mutated") is False, f"loop driver entry mutated ledger: {loop_driver}")
    require((loop_driver.get("safety") or {}).get("server_executes_shell") is False, f"loop driver entry server shell proof missing: {loop_driver}")
    require(str(loop_commands.get("preview") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"loop driver preview command missing: {loop_driver}")
    require(str(loop_commands.get("confirm_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"loop driver confirm command missing: {loop_driver}")
    require("--confirm-loop" in str(loop_commands.get("confirm_loop") or ""), f"loop driver confirm flag missing: {loop_driver}")
    require(str(loop_commands.get("review_queue") or "").startswith("agentops review queue"), f"loop driver review command missing: {loop_driver}")
    require(review_snapshot.get("operation") == "loop_driver_record_review_snapshot", f"loop driver review snapshot missing: {loop_driver}")
    require((review_snapshot.get("safety") or {}).get("read_only") is True, f"loop driver review snapshot read-only proof missing: {loop_driver}")
    require((review_snapshot.get("safety") or {}).get("ledger_mutated") is False, f"loop driver review snapshot mutated ledger: {loop_driver}")
    for key in ["review_items_total", "returned_items", "pending_approvals", "memory_candidates"]:
        require(isinstance(review_summary.get(key), int), f"loop driver review summary {key} missing: {loop_driver}")
    require(review_snapshot.get("summary_omitted") is True, f"loop driver review summary omission proof missing: {loop_driver}")
    require(review_snapshot.get("raw_content_omitted") is True, f"loop driver review raw omission proof missing: {loop_driver}")
    require(all(item.get("summary_omitted") is True and item.get("token_omitted") is True for item in (review_snapshot.get("items") or [])), f"loop driver review items should be compact: {loop_driver}")
    acceptance_packet = payload.get("acceptance_packet") or {}
    packet_decision = acceptance_packet.get("decision") or {}
    packet_commands = acceptance_packet.get("commands") or {}
    packet_summary = acceptance_packet.get("summary") or {}
    require(acceptance_packet.get("operation") == "operator_local_loop_acceptance_packet", f"acceptance packet missing: {acceptance_packet}")
    require(acceptance_packet.get("adapter") == adapter, f"acceptance packet adapter mismatch: {acceptance_packet}")
    require(acceptance_packet.get("status") == payload.get("status"), f"acceptance status mismatch: {acceptance_packet}")
    require((acceptance_packet.get("safety") or {}).get("read_only") is True, f"acceptance read-only proof missing: {acceptance_packet}")
    require((acceptance_packet.get("safety") or {}).get("ledger_mutated") is False, f"acceptance ledger proof missing: {acceptance_packet}")
    require((acceptance_packet.get("safety") or {}).get("server_executes_shell") is False, f"acceptance server shell proof missing: {acceptance_packet}")
    require(packet_decision.get("agent_plan_required") is True, f"acceptance agent-plan gate missing: {acceptance_packet}")
    require(packet_decision.get("current_code_required") is True, f"acceptance current-code requirement missing: {acceptance_packet}")
    require(packet_decision.get("current_code_ok") is True, f"acceptance current-code proof missing: {acceptance_packet}")
    require(packet_decision.get("knowledge_search_required") is True, f"acceptance knowledge gate missing: {acceptance_packet}")
    require(packet_decision.get("base_compare_required") is True, f"acceptance base gate missing: {acceptance_packet}")
    require(packet_decision.get("receipt_required") is True, f"acceptance receipt gate missing: {acceptance_packet}")
    require(isinstance(packet_summary.get("attention_gates"), list), f"acceptance attention gates missing: {acceptance_packet}")
    require(str(packet_commands.get("start_check") or "").startswith(f"agentops operator start-check --adapter {adapter}"), f"acceptance start-check command missing: {acceptance_packet}")
    require("--require-current-code" in str(packet_commands.get("current_code_check") or ""), f"acceptance current-code command missing: {acceptance_packet}")
    require(str(packet_commands.get("loop_driver_preview") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"acceptance loop-driver preview missing: {acceptance_packet}")
    require("--confirm-loop" in str(packet_commands.get("loop_driver_confirm") or ""), f"acceptance confirm-loop missing: {acceptance_packet}")
    require(str(packet_commands.get("review_queue") or "").startswith("agentops review queue"), f"acceptance review command missing: {acceptance_packet}")
    require(packet_commands.get("receipt_readback") == "agentops operator action-receipts --limit 20", f"acceptance receipt readback missing: {acceptance_packet}")
    agent_loop_packet = payload.get("agent_loop_packet") or {}
    agent_loop_commands = agent_loop_packet.get("commands") or {}
    agent_loop_phases = {item.get("phase") for item in (agent_loop_packet.get("phases") or [])}
    agent_loop_phase_commands = agent_loop_packet.get("phase_commands") or {}
    method_gates = agent_loop_packet.get("method_gates") or []
    method_gate_ids = {gate.get("id") for gate in method_gates}
    require(agent_loop_packet.get("operation") == "operator_loop_driver_agent_loop_packet", f"agent loop packet missing: {agent_loop_packet}")
    require(agent_loop_packet.get("adapter") == adapter, f"agent loop packet adapter mismatch: {agent_loop_packet}")
    require(agent_loop_packet.get("current_phase") in {"preview", "blocked"}, f"agent loop phase mismatch: {agent_loop_packet}")
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(agent_loop_phases), f"agent loop phases missing: {agent_loop_packet}")
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(set(agent_loop_phase_commands)), f"agent loop phase command map missing: {agent_loop_packet}")
    require({"read_start_check", "read_current_code", "plan_agent_plan", "retrieve_knowledge", "compare_base_reference", "preflight_adapter", "execute_bounded_loop", "verify_loop", "record_memory_candidate"}.issubset(method_gate_ids), f"agent loop method gates missing: {agent_loop_packet}")
    require(all(gate.get("token_omitted") is True for gate in method_gates), f"agent loop method gate token proof missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("start_check") or "").startswith(f"agentops operator start-check --adapter {adapter}"), f"agent loop start-check missing: {agent_loop_packet}")
    require("--require-current-code" in str(agent_loop_commands.get("current_code_check") or ""), f"agent loop current-code command missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("agent_plan_create") or "").startswith("agentops agent-plan create"), f"agent loop plan command missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("knowledge_search") or "").startswith("agentops knowledge search"), f"agent loop knowledge command missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("base_reference") or "").startswith("agentops commander repo-map"), f"agent loop base-reference command missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("preview_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"agent loop preview missing: {agent_loop_packet}")
    require("--confirm-loop" in str(agent_loop_commands.get("confirm_loop") or ""), f"agent loop confirm missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("adapter_preflight") or "").endswith(f"--adapter {adapter}"), f"agent loop preflight missing: {agent_loop_packet}")
    require(str(agent_loop_commands.get("review_queue") or "").startswith("agentops review queue"), f"agent loop review missing: {agent_loop_packet}")
    require((agent_loop_packet.get("safety") or {}).get("read_only") is True, f"agent loop read-only proof missing: {agent_loop_packet}")
    require((agent_loop_packet.get("safety") or {}).get("ledger_mutated") is False, f"agent loop ledger proof missing: {agent_loop_packet}")
    require((agent_loop_packet.get("safety") or {}).get("server_executes_shell") is False, f"agent loop server shell proof missing: {agent_loop_packet}")
    require(agent_loop_packet.get("live_execution_performed") is False, f"agent loop live proof missing: {agent_loop_packet}")
    admission_packet = payload.get("local_loop_admission_packet") or {}
    local_run_path = payload.get("local_run_path") or {}
    local_steps = local_run_path.get("steps") or []
    local_adapter_steps = [step for step in local_steps if isinstance(step, dict) and step.get("adapter") in {"mock", "hermes", "openclaw"}]
    require(local_run_path.get("recommended_adapter") == adapter, f"top-level local run path adapter mismatch: {local_run_path}")
    require(bool(local_adapter_steps), f"top-level local run path adapter steps missing: {local_run_path}")
    require(all(step.get("adapter") == adapter for step in local_adapter_steps), f"top-level local run path contains wrong adapter step: {local_adapter_steps}")
    admission = admission_packet.get("admission") or {}
    deployment = admission_packet.get("local_deployment") or {}
    service_preview = deployment.get("service_control_preview") or {}
    service_install = deployment.get("service_install") or {}
    service_managed_loop = deployment.get("service_managed_loop") or {}
    managed_execution_path = deployment.get("managed_execution_path") or {}
    managed_execution_commands = managed_execution_path.get("commands") or {}
    managed_execution_gate_ids = {str(gate.get("id")) for gate in managed_execution_path.get("gates") or [] if isinstance(gate, dict)}
    admission_current_code = deployment.get("current_code_gate") or {}
    worker_start = deployment.get("worker_start") or {}
    customer_dispatch = deployment.get("customer_worker_dispatch") or {}
    admission_commands = admission_packet.get("commands") or {}
    require(admission_packet.get("operation") == "operator_local_loop_admission_packet", f"admission packet missing: {admission_packet}")
    require(admission_packet.get("adapter") == adapter, f"admission adapter mismatch: {admission_packet}")
    require(admission.get("method_gate_count") >= 8, f"admission method gates missing: {admission_packet}")
    require(admission.get("current_code_ok") is True, f"admission current-code proof missing: {admission_packet}")
    require(set(method_gate_ids).issubset(set(admission_packet.get("required_method_gates") or [])), f"admission gate ids mismatch: {admission_packet}")
    require({"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}.issubset(set(admission_packet.get("phase_commands") or {})), f"admission phase commands missing: {admission_packet}")
    require(service_preview.get("preview_only") is True, f"service-control preview proof missing: {admission_packet}")
    require(service_preview.get("server_executes_shell") is False, f"service-control server shell proof missing: {admission_packet}")
    require(service_managed_loop.get("operation") == "local_service_managed_loop_readiness", f"service-managed loop projection missing: {admission_packet}")
    require(service_managed_loop.get("service_check_available") is True, f"service-managed service-check missing: {admission_packet}")
    require(service_managed_loop.get("service_control_preview_available") is True, f"service-managed control preview missing: {admission_packet}")
    require(service_managed_loop.get("receipt_required") is True, f"service-managed receipt requirement missing: {admission_packet}")
    require(service_managed_loop.get("control_readback_required") is True, f"service-managed readback requirement missing: {admission_packet}")
    require(service_managed_loop.get("service_active_loop_ready") in {True, False}, f"service active-loop state missing: {service_managed_loop}")
    require(service_managed_loop.get("active_status") in {"loaded", "not_loaded", "unverified"}, f"service active status missing: {service_managed_loop}")
    require((service_managed_loop.get("safety") or {}).get("loads_service") is False, f"service-managed load boundary missing: {admission_packet}")
    require((service_managed_loop.get("safety") or {}).get("server_executes_shell") is False, f"service-managed server shell proof missing: {admission_packet}")
    require(managed_execution_path.get("operation") == "operator_service_managed_execution_path", f"managed execution path missing: {admission_packet}")
    require(managed_execution_path.get("service_managed_loop_ready") in {True, False}, f"managed execution readiness missing: {managed_execution_path}")
    require(managed_execution_path.get("service_active_loop_ready") in {True, False}, f"managed active-loop readiness missing: {managed_execution_path}")
    require({"service_managed_loop_ready", "service_active_loop_ready", "agent_plan_required", "knowledge_retrieval_required", "customer_worker_dispatch", "plan_evidence_required", "review_queue_required"}.issubset(managed_execution_gate_ids), f"managed execution gates missing: {managed_execution_path}")
    managed_dispatch = str(managed_execution_commands.get("customer_worker_dispatch") or "")
    managed_service_check = str(managed_execution_commands.get("service_check") or "")
    managed_service_receipt = str(managed_execution_commands.get("service_control_receipt") or "")
    managed_service_readback = str(managed_execution_commands.get("service_control_readback") or "")
    managed_service_load = str(managed_execution_commands.get("service_control_load_confirm") or "")
    top_service_step = next((step for step in local_run_path.get("steps") or [] if isinstance(step, dict) and step.get("step_id") == "preview_worker_service_control"), {})
    canonical_service_source = f"local_readiness.service_control_preview.{adapter}"
    canonical_service_signature = str(top_service_step.get("action_signature") or "")
    require(f"--adapter {adapter}" in managed_service_check, f"managed service-check command adapter mismatch: {managed_execution_path}")
    require(f"--adapter {adapter}" in managed_service_receipt, f"managed service receipt command adapter mismatch: {managed_execution_path}")
    require(f"--adapter {adapter}" in managed_service_readback, f"managed service readback command adapter mismatch: {managed_execution_path}")
    require(bool(canonical_service_signature), f"canonical service-control signature missing: {top_service_step}")
    require(f"--action-id {canonical_service_source}" in managed_service_receipt, f"managed service receipt action id diverged: {managed_execution_path}")
    require(f"--action-signature {canonical_service_signature}" in managed_service_receipt, f"managed service receipt signature diverged: {managed_execution_path}")
    require(f"--source {canonical_service_source}" in managed_service_receipt, f"managed service receipt source diverged: {managed_execution_path}")
    require(f"--source {canonical_service_source}.control_readback" in managed_service_readback, f"managed service readback source diverged: {managed_execution_path}")
    require(f"--adapter {adapter}" in managed_service_load and "--confirm-control" in managed_service_load, f"managed service load-confirm command missing: {managed_execution_path}")
    require(managed_dispatch.startswith(("agentops workflow run-task", "agentops workflow customer-worker-task")), f"managed dispatch command missing: {managed_execution_path}")
    require(managed_service_readback.startswith("agentops operator record-control-readback"), f"managed control-readback command missing: {managed_execution_path}")
    require(str(managed_execution_commands.get("evidence_report") or "").startswith("agentops operator evidence-report --run-id"), f"managed evidence command missing: {managed_execution_path}")
    require(str(managed_execution_commands.get("review_queue") or "").startswith("agentops review queue"), f"managed review command missing: {managed_execution_path}")
    require((managed_execution_path.get("safety") or {}).get("server_executes_shell") is False, f"managed execution server shell proof missing: {managed_execution_path}")
    require(managed_execution_path.get("live_execution_performed") is False, f"managed execution live proof missing: {managed_execution_path}")
    require(service_install.get("preview_only_by_default") is True, f"service-install preview proof missing: {admission_packet}")
    require(service_install.get("loads_service") is False, f"service-install must not load service: {admission_packet}")
    require(service_install.get("server_executes_shell") is False, f"service-install server shell proof missing: {admission_packet}")
    require(str(service_install.get("preview_command") or "").startswith("agentops worker service-install --manager launchd"), f"service-install preview command missing: {admission_packet}")
    require("--confirm-install" in str(service_install.get("confirm_command") or ""), f"service-install confirm command missing: {admission_packet}")
    require(str(admission_commands.get("service_check") or "").startswith("agentops worker service-check"), f"service-check command missing: {admission_packet}")
    require(str(admission_commands.get("service_install_preview") or "").startswith("agentops worker service-install"), f"admission service-install preview command missing: {admission_packet}")
    require("--confirm-install" in str(admission_commands.get("service_install_confirm") or ""), f"admission service-install confirm command missing: {admission_packet}")
    require("--require-current-code" in str(admission_commands.get("current_code_check") or ""), f"admission current-code command missing: {admission_packet}")
    require(admission_current_code.get("operation") == "local_current_code_gate", f"admission current-code deployment proof missing: {admission_packet}")
    require(str(admission_commands.get("preview_loop") or "").startswith(f"agentops operator loop-driver --adapter {adapter}"), f"admission preview command missing: {admission_packet}")
    require((admission_packet.get("safety") or {}).get("read_only") is True, f"admission read-only proof missing: {admission_packet}")
    require((admission_packet.get("safety") or {}).get("ledger_mutated") is False, f"admission ledger proof missing: {admission_packet}")
    require((admission_packet.get("safety") or {}).get("server_executes_shell") is False, f"admission server shell proof missing: {admission_packet}")
    require(admission_packet.get("live_execution_performed") is False, f"admission live proof missing: {admission_packet}")
    next_commands = payload.get("next_commands") or []
    require(any("--require-current-code" in str(command) for command in next_commands), f"current-code next command missing: {next_commands}")
    require(any("operator loop-launch-packet" in str(command) for command in next_commands), f"launch command missing: {next_commands}")
    require(any("operator loop-driver" in str(command) for command in next_commands), f"loop-driver command missing: {next_commands}")
    require(any("review queue" in str(command) for command in next_commands), f"review queue command missing: {next_commands}")
    if adapter in {"hermes", "openclaw"}:
        summary = payload.get("summary") or {}
        require(summary.get("requires_confirm_run") is True, f"live adapter confirm proof missing: {summary}")
        require(any("--confirm-loop" in str(command) for command in next_commands), f"confirm loop command missing: {next_commands}")
        require(packet_decision.get("live_dispatch_requires_confirm_run") is True, f"acceptance confirm-run wall missing: {acceptance_packet}")
        require(str(packet_commands.get("execution_mode_confirm") or "").endswith("--confirm-run"), f"acceptance execution-mode confirm missing: {acceptance_packet}")
        require(str(packet_commands.get("live_product_readiness") or "").endswith(f"--require-adapter {adapter}"), f"acceptance live readiness command missing: {acceptance_packet}")
        require(admission.get("live_dispatch_requires_confirm_run") is True, f"admission confirm wall missing: {admission_packet}")
        require("--confirm-run" in str(worker_start.get("command") or ""), f"admission worker start confirm missing: {admission_packet}")
        require("--confirm-run" in str(service_install.get("preview_command") or ""), f"admission service install confirm-run missing: {admission_packet}")
        require(customer_dispatch.get("requires_confirm_run_flag") is True, f"admission dispatch confirm missing: {admission_packet}")
        require("--confirm-run" in str(customer_dispatch.get("command") or ""), f"admission dispatch command confirm missing: {admission_packet}")


def main() -> int:
    cold_start_gate = operator_start_check_local_readiness_gate({
        "operation": "local_readiness",
        "status": "blocked",
        "live_execution_performed": False,
        "safety": {"server_executes_shell": False},
    })
    require(cold_start_gate.get("status") == "attention" and cold_start_gate.get("cold_start_capable") is True, f"cold-start local readiness should remain advisory: {cold_start_gate}")
    malformed_gate = operator_start_check_local_readiness_gate({"status": "blocked"})
    require(malformed_gate.get("status") == "blocked" and malformed_gate.get("ok") is False, f"malformed local readiness should stay blocked: {malformed_gate}")
    parser = argparse.ArgumentParser(description="Verify operator start-check CLI aggregate.")
    parser.add_argument("--base-url", default=os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], action="append", default=None)
    args = parser.parse_args()
    outputs: list[str] = []
    try:
        missing_runtime_gate = compact_runtime_current_code_gate({})
        require(missing_runtime_gate.get("ok") is False, f"missing runtime signal must fail closed: {missing_runtime_gate}")
        require(missing_runtime_gate.get("status") == "unknown", f"missing runtime signal status should be unknown: {missing_runtime_gate}")
        missing_launch_current_code = operator_agent_loop_packet(
            adapter="hermes",
            max_steps=1,
            acceptance_gate={
                "decision": {"can_confirm_bounded_loop": True, "current_code_ok": True},
                "commands": {"loop_driver_confirm": "agentops operator loop-driver --adapter hermes --confirm-loop"},
                "safety": {"server_executes_shell": False},
                "status": "ready",
            },
            adapter_readiness={"readiness": "ready", "commands": {"adapter_preflight": "agentops worker preflight --adapter hermes"}},
            launch_brief={"operation": "operator_loop_launch_brief", "local_run_path": {}},
            review_snapshot={"summary": {}, "status": "ready"},
            confirm_loop=False,
        )
        require(missing_launch_current_code.get("ready_to_confirm_loop") is False, f"agent loop packet must fail closed without launch current-code proof: {missing_launch_current_code}")
        require((missing_launch_current_code.get("gates") or {}).get("current_code") == "blocked", f"agent loop packet current-code gate should block without proof: {missing_launch_current_code}")
        with tempfile.TemporaryDirectory(prefix="agentops-start-check-") as tmp:
            db_path = Path(tmp) / "agentops_mis.db"
            port = free_port()
            default_base_url = parser.get_default("base_url")
            base_url = args.base_url
            owns_server = args.base_url == default_base_url
            env = os.environ.copy()
            env["AGENTOPS_CONFIG"] = str(Path(tmp) / "config.json")
            if owns_server:
                base_url = f"http://127.0.0.1:{port}"
                env["AGENTOPS_BASE_URL"] = base_url
                env["AGENTOPS_DB_PATH"] = str(db_path)
                env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
            env.pop("AGENTOPS_API_KEY", None)
            server_proc: subprocess.Popen[str] | None = None
            if owns_server:
                server_proc = subprocess.Popen(
                    [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                wait_ready(base_url, server_proc)
            try:
                before = db_counts(db_path) if owns_server else {}
                checked = []
                for adapter in (args.adapter or ["mock", "hermes", "openclaw"]):
                    api_status, api_payload = http_json(base_url, "/api/operator/start-check", {"adapter": adapter, "limit": "4"})
                    outputs.append(json.dumps(api_payload, ensure_ascii=False))
                    require(api_status == 200, f"operator start-check API failed for {adapter}: {api_status} {api_payload}")
                    validate(api_payload, adapter)
                    proc = run_start_check(base_url, adapter, env)
                    outputs.extend([proc.stdout, proc.stderr])
                    require(proc.returncode == 0, f"operator start-check failed for {adapter}: {proc.stderr or proc.stdout}")
                    payload = json.loads(proc.stdout)
                    validate(payload, adapter)
                    require(payload.get("operation") == api_payload.get("operation"), f"CLI/API operation mismatch for {adapter}")
                    require(payload.get("adapter") == api_payload.get("adapter"), f"CLI/API adapter mismatch for {adapter}")
                    checked.append({"adapter": adapter, "api_status": api_payload.get("status"), "cli_status": payload.get("status")})
                after = db_counts(db_path) if owns_server else {}
                require(not owns_server or before == after, f"operator start-check CLI mutated ledger counts: before={before} after={after}")
            finally:
                if server_proc is not None:
                    server_proc.terminate()
                    try:
                        server_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server_proc.kill()
                        server_proc.wait(timeout=5)
        require(not leaked_secret("\n".join(outputs)), "operator start-check leaked token-like material")
        print(json.dumps({
            "ok": True,
            "operation": "operator_start_check_smoke",
            "checked": checked,
            "ledger_mutated": False,
            "secret_leaked": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
