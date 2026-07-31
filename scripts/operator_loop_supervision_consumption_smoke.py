#!/usr/bin/env python3
"""Verify Hermes/OpenClaw worker entry points consume loop-supervision before live execution."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SMOKE_DB_DIR = tempfile.TemporaryDirectory(prefix="agentops-loop-supervision-consumption-")
os.environ["AGENTOPS_DB_PATH"] = str(Path(SMOKE_DB_DIR.name) / "agentops.db")
os.environ.setdefault("AGENTOPS_SKIP_SEED_EXPORTS", "1")

import server  # noqa: E402
from agentops_mis_cli import worker  # noqa: E402


TOKEN_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+(?!\[REDACTED\])[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"agtok_[A-Za-z0-9_-]{16,}"),
    re.compile(r"agtsess_[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9_-]{8,}"),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def leaked(text: str) -> bool:
    return any(pattern.search(text) for pattern in TOKEN_PATTERNS)


def blocked_supervision(
    adapter: str,
    *,
    task_id: str | None = None,
    agent_id: str | None = None,
    plan_quality_attention: bool = False,
    service_closure_attention: bool = False,
    service_closure_hard: bool = False,
    operational_attention: bool = False,
) -> dict:
    record_first = plan_quality_attention or service_closure_attention
    status = "record_first" if record_first else "attention" if operational_attention else "blocked"
    can_confirm = True if record_first or operational_attention else False
    recommended_next = (
        f"agentops operator evidence-report --task-id {task_id or '<task_id>'} --limit 8"
        if plan_quality_attention
        else (
            f"agentops operator record-action-receipt --task-id {task_id or '<task_id>'} --adapter {adapter} --source operator_loop_supervision.{adapter} --confirm-record"
            if service_closure_attention
            else f"agentops operator loop-supervision --adapter {adapter} --task-id {task_id or '<task_id>'}"
        )
    )
    blockers = [] if record_first or operational_attention else ["smoke_blocked_supervision"]
    attention = (
        ["agent_plan_quality_attention"]
        if plan_quality_attention
        else (
            ["service_managed_loop:record_service_control_receipt"]
            if service_closure_attention
            else ["live_product_evidence_not_fresh"] if operational_attention else []
        )
    )
    service_closure = {
        "required": service_closure_attention,
        "status": "attention" if service_closure_attention else "not_applicable",
        "step": "record_service_control_receipt" if service_closure_attention else None,
        "phase": "RECORD" if service_closure_attention else None,
        "command": recommended_next if service_closure_attention else None,
        "receipt_verified": False,
        "control_readback_attached": False,
        "service_managed_loop_ready": False,
        "service_active_loop_ready": False,
        "service_loaded": False,
        "hard_run_start_gate": service_closure_hard,
        "server_executes_shell": False,
        "token_omitted": True,
    }
    return {
        "provider": "agentops-operator",
        "operation": "operator_loop_supervision",
        "status": status,
        "summary": {
            "items": 1,
            "current_code_ok": True,
            "can_confirm_all": can_confirm,
            "record_required": record_first,
            "agent_plan_quality_status": "attention" if plan_quality_attention else "not_applicable",
            "agent_plan_quality_attention": 1 if plan_quality_attention else 0,
            "agent_plan_quality_blocked": 0,
            "agent_plan_quality_min_score": 67 if plan_quality_attention else None,
        },
        "items": [
            {
                "operation": "operator_loop_supervision_item",
                "adapter": adapter,
                "status": status,
                "can_preview_loop": True,
                "can_confirm_bounded_loop": can_confirm,
                "should_record_before_execute": False,
                "ready_for_live_dispatch": False,
                "blockers": blockers,
                "attention": attention,
                "plan_quality": {
                    "status": "attention" if plan_quality_attention else "not_applicable",
                    "issue_count": 1 if plan_quality_attention else 0,
                    "command": recommended_next,
                    "hard_run_start_gate": False,
                    "token_omitted": True,
                },
                "service_closure": service_closure,
                "review_pressure": {"token_omitted": True},
                "local_deployment": {
                    "local_run_path": {
                        "operation": "local_run_path_compact",
                        "recommended_adapter": adapter,
                        "safety": {
                            "read_only": True,
                            "ledger_mutated": False,
                            "live_execution_performed": False,
                            "server_executes_shell": False,
                            "token_omitted": True,
                        },
                        "token_omitted": True,
                    },
                    "service_managed_loop": {
                        "operation": "local_service_managed_loop_readiness",
                        "adapter": adapter,
                        "commands": {
                            "service_check": f"agentops worker service-check --manager launchd --adapter {adapter}",
                            "record_verified_receipt": recommended_next if service_closure_attention else None,
                            "record_control_readback": f"agentops operator record-control-readback --source operator_loop_supervision.{adapter} --control-readback-json '{{}}' --confirm-record",
                        },
                        "token_omitted": True,
                    },
                    "token_omitted": True,
                },
                "gates": [
                    {"id": "bounded_confirm", "ok": can_confirm, "status": "pass" if can_confirm else "blocked", "confirm_required": True, "token_omitted": True},
                    {
                        "id": "plan_quality",
                        "ok": not plan_quality_attention,
                        "status": "attention" if plan_quality_attention else "pass",
                        "quality_status": "attention" if plan_quality_attention else "not_applicable",
                        "command": recommended_next,
                        "hard_run_start_gate": False,
                        "token_omitted": True,
                    },
                    {
                        "id": "service_managed_loop",
                        "ok": not service_closure_attention,
                        "status": "attention" if service_closure_attention else "pass",
                        "step": "record_service_control_receipt" if service_closure_attention else None,
                        "phase": "RECORD" if service_closure_attention else None,
                        "command": recommended_next if service_closure_attention else None,
                        "hard_run_start_gate": service_closure_hard,
                        "server_executes_shell": False,
                        "token_omitted": True,
                    },
                    {"id": "local_deployment", "ok": True, "status": "pass", "recommended_adapter": adapter, "service_managed_adapter": adapter, "server_executes_shell": False, "token_omitted": True},
                    {"id": "server_shell_boundary", "ok": True, "status": "pass", "server_executes_shell": False, "token_omitted": True},
                ],
                "commands": {
                    "recommended_next": recommended_next,
                    "record_review": "agentops review queue --limit 20",
                },
                "next_commands": {
                    "safe_read_commands": [f"agentops operator start-check --adapter {adapter} --limit 8"],
                    "preview_commands": [],
                    "confirm_required_commands": [],
                    "recommended_next": recommended_next,
                    "token_omitted": True,
                },
                "safety": {
                    "read_only": True,
                    "ledger_mutated": False,
                    "live_execution_performed": False,
                    "server_executes_shell": False,
                    "token_omitted": True,
                },
                "token_omitted": True,
            }
        ],
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "auth": {"mode": "local_dev_no_token", "required_scope": "tasks:read", "agent_id": agent_id, "token_omitted": True},
        "token_omitted": True,
    }


def verify_server_customer_worker_consumption(failures: list[str]) -> dict:
    original_operator_loop_supervision = server.operator_loop_supervision
    original_worker_adapter_readiness = server.worker_adapter_readiness
    blocked_payload = None
    external_payload = None
    try:
        def fake_operational_attention(conn, headers, qs=None, auth_ctx=None):
            adapter = ((qs or {}).get("adapter") or ["hermes"])[0]
            task_id = ((qs or {}).get("task_id") or [None])[0]
            agent_id = ((qs or {}).get("agent_id") or [None])[0]
            return blocked_supervision(adapter, task_id=task_id, agent_id=agent_id, operational_attention=True)

        server.operator_loop_supervision = fake_operational_attention
        with server.db() as conn:
            attention_gate = server.customer_worker_loop_supervision_readback(
                conn,
                adapter="hermes",
                task_id="tsk_operational_attention_smoke",
                agent_id="agt_operational_attention_smoke",
            )
        require(attention_gate.get("status") == "attention", f"operational attention status drift: {attention_gate}", failures)
        require(attention_gate.get("ok") is True, f"non-blocking operational attention should allow confirmed live execution: {attention_gate}", failures)
        require(attention_gate.get("can_confirm_bounded_loop") is True, f"operational attention lost confirm readiness: {attention_gate}", failures)
        require("live_product_evidence_not_fresh" in (attention_gate.get("attention") or []), f"operational attention evidence missing: {attention_gate}", failures)

        def fake_blocked(conn, headers, qs=None, auth_ctx=None):
            adapter = ((qs or {}).get("adapter") or ["hermes"])[0]
            task_id = ((qs or {}).get("task_id") or [None])[0]
            agent_id = ((qs or {}).get("agent_id") or [None])[0]
            return blocked_supervision(adapter, task_id=task_id, agent_id=agent_id, plan_quality_attention=True)

        def fake_adapter_ready(conn, refresh: bool = True):
            return {
                "operation": "worker_adapter_readiness",
                "status": "ready",
                "summary": {"ready": 1, "unavailable": 0, "blocked": 0, "token_omitted": True},
                "adapters": {
                    "hermes": {
                        "adapter": "hermes",
                        "readiness": "ready",
                        "recommended_action": "agentops worker preflight --adapter hermes",
                        "connector_id": "rtc_hermes_default_gateway",
                        "target_resource": "hermes://default-gateway",
                        "observation_level": "ledger_summary_only",
                        "commercial_readiness": "review_required",
                        "capability_manifest": {
                            "governance": {
                                "requires_prepared_action_for_external_write": True,
                                "token_omitted": True,
                            },
                            "token_omitted": True,
                        },
                        "token_omitted": True,
                    }
                },
                "token_omitted": True,
            }

        server.operator_loop_supervision = fake_blocked
        server.worker_adapter_readiness = fake_adapter_ready
        with server.db() as conn:
            server.refresh_runtime_connectors(conn)
            conn.execute("UPDATE runtime_connectors SET trust_status='trusted', trust_note=NULL WHERE runtime_connector_id='rtc_hermes_default_gateway'")
            blocked_payload, status = server.run_customer_worker_task_workflow(conn, {
                "adapter": "hermes",
                "confirm_run": True,
                "title": "Loop supervision blocked customer task",
                "description": "This must stop before live Hermes execution because supervision is blocked.",
                "acceptance_criteria": "Do not invoke Hermes.",
            })
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (blocked_payload.get("task_id"),)).fetchone()
            audit_count = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs WHERE entity_id=? AND action='workflow.customer_worker_task.loop_supervision_blocked'",
                (blocked_payload.get("task_id"),),
            ).fetchone()["c"]
        gate = blocked_payload.get("loop_supervision_gate") or {}
        require(status == 409, f"server supervision block status mismatch: {status} {blocked_payload}", failures)
        require(blocked_payload.get("reason") == "loop_supervision_blocked", f"server wrong block reason: {blocked_payload}", failures)
        require(blocked_payload.get("live_execution_performed") is False, f"server block should not execute live runtime: {blocked_payload}", failures)
        require(gate.get("operation") == "customer_worker_loop_supervision_gate", f"server gate missing: {blocked_payload}", failures)
        require(gate.get("ok") is False and gate.get("can_confirm_bounded_loop") is True, f"server gate should block record_first without losing confirm readiness: {gate}", failures)
        require(gate.get("status") == "record_first", f"server gate should preserve record_first status: {gate}", failures)
        plan_quality = gate.get("plan_quality") or {}
        require(plan_quality.get("status") == "attention" and plan_quality.get("issue_count") == 1, f"server gate lost plan quality attention: {gate}", failures)
        require(plan_quality.get("hard_run_start_gate") is False, f"server plan quality should remain non-hard gate: {gate}", failures)
        require(task is not None and task["status"] == "blocked", f"server blocked task missing: {blocked_payload}", failures)
        require(audit_count >= 1, "server block audit missing", failures)

        server.operator_loop_supervision = original_operator_loop_supervision
        with server.db() as conn:
            server.refresh_runtime_connectors(conn)
            conn.execute("UPDATE runtime_connectors SET trust_status='trusted', trust_note=NULL WHERE runtime_connector_id='rtc_hermes_default_gateway'")
            external_payload, status = server.run_customer_worker_task_workflow(conn, {
                "adapter": "hermes",
                "confirm_run": True,
                "external_write_intent": True,
                "external_action_type": "customer.portal.publish",
                "target_resource": "mock://customer-portal/supervision",
                "title": "Loop supervision external write gate",
                "description": "Use live Hermes only after exact external write approval.",
                "acceptance_criteria": "Prepared action must be created before live execution.",
            })
        external_gate = external_payload.get("loop_supervision_gate") or {}
        require(status == 202, f"server external write gate status mismatch: {status} {external_payload}", failures)
        require(external_payload.get("reason") == "external_write_prepared_action_required", f"server external reason mismatch: {external_payload}", failures)
        require(external_payload.get("live_execution_performed") is False, f"server external path should not execute live runtime: {external_payload}", failures)
        require(external_gate.get("operation") == "customer_worker_loop_supervision_gate", f"server external gate missing: {external_payload}", failures)
        require(external_gate.get("live_execution_performed") is not True, f"server external gate should not execute live runtime: {external_gate}", failures)
        require(external_gate.get("safety", {}).get("server_executes_shell") is False, f"server external gate shell proof missing: {external_gate}", failures)
    finally:
        server.operator_loop_supervision = original_operator_loop_supervision
        server.worker_adapter_readiness = original_worker_adapter_readiness
    return {"blocked": blocked_payload, "external": external_payload}


class FakeWorkerClient:
    workspace_id = "local-demo"
    agent_id = "agt_worker_supervision_smoke"
    api_key = ""

    def __init__(self, *, plan_quality_attention: bool = True, service_closure_attention: bool = False, service_closure_hard: bool = False) -> None:
        self.plan_quality_attention = plan_quality_attention
        self.service_closure_attention = service_closure_attention
        self.service_closure_hard = service_closure_hard
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict | None]] = []

    def get(self, path: str, query: dict | None = None):
        self.gets.append((path, query))
        if path == "/api/agent-gateway/tasks/pull":
            return {
                "tasks": [
                    {
                        "task_id": "tsk_worker_supervision_smoke",
                        "title": "Worker supervision smoke",
                        "description": "This live Hermes worker must stop before adapter execution when supervision blocks.",
                        "acceptance_criteria": "No live adapter call.",
                        "risk_level": "medium",
                    }
                ]
            }
        if path == "/api/agent-gateway/knowledge/evidence-packet":
            return {
                "operation": "knowledge_retrieval_evidence_packet",
                "status": "ready",
                "query_hash": "hash_query",
                "metrics": {"recall_at_5": 1.0, "mrr": 1.0},
                "task_context": {"task_id": "tsk_worker_supervision_smoke", "task_found": True, "query_source": "task_id", "task_text_omitted": True, "token_omitted": True},
                "primary_search": {
                    "results": [
                        {
                            "retrieval_id": "ret_worker_supervision",
                            "doc_id": "doc_method",
                            "chunk_id": "chunk_method",
                            "path": "docs/AGENT_WORK_METHOD_BLOCK.md",
                            "source_hash": "source_hash",
                            "rank": 1,
                        }
                    ]
                },
            }
        if path == "/api/operator/loop-supervision":
            return blocked_supervision(
                "hermes",
                task_id="tsk_worker_supervision_smoke",
                agent_id=self.agent_id,
                plan_quality_attention=self.plan_quality_attention,
                service_closure_attention=self.service_closure_attention,
                service_closure_hard=self.service_closure_hard,
            )
        if path.startswith("/api/agent-gateway/agent-plans/") and path.endswith("/verify"):
            return {"verification": {"pass": True, "token_omitted": True}}
        raise AssertionError(f"unexpected GET {path} {query}")

    def post(self, path: str, payload: dict, timeout: int = 180):
        self.posts.append((path, payload))
        if path.endswith("/claim"):
            return {"ok": True}
        if path == "/api/agent-gateway/agent-plans":
            return {"agent_plan": {"plan_id": "aplan_worker_supervision"}}
        if path == "/api/agent-gateway/runs/start":
            return {"run": {"run_id": "run_worker_supervision"}}
        if path.endswith("/heartbeat"):
            return {"ok": True}
        if path == "/api/agent-gateway/audit":
            return {"audit": {"audit_id": "aud_worker_supervision"}}
        raise AssertionError(f"unexpected POST {path} {payload}")


def verify_worker_consumption(failures: list[str]) -> dict:
    parser = worker.build_parser()
    args = parser.parse_args([
        "--once",
        "--adapter",
        "hermes",
        "--confirm-run",
        "--allow-high-risk",
        "--no-enforce-intake",
    ])
    called_adapter = {"value": False}
    original_execute = worker.execute_adapter_with_retries
    try:
        def fail_if_called(_task, _args):
            called_adapter["value"] = True
            raise AssertionError("adapter execution should be blocked by loop supervision")

        worker.execute_adapter_with_retries = fail_if_called
        client = FakeWorkerClient()
        result = worker.process_one_task(client, args)
    finally:
        worker.execute_adapter_with_retries = original_execute
    gate = result.get("loop_supervision_gate") or {}
    audit_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/audit"]
    run_start_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/runs/start"]
    heartbeat_posts = [payload for path, payload in client.posts if path.endswith("/heartbeat")]
    require(called_adapter["value"] is False, "worker called adapter despite blocked supervision", failures)
    require(not run_start_posts, f"worker should block before run_start when supervision blocks: {run_start_posts}", failures)
    require(result.get("reason") == "loop_supervision_blocked", f"worker wrong block reason: {result}", failures)
    require(result.get("live_execution_performed") is False, f"worker should not execute live runtime: {result}", failures)
    require(result.get("run_start_attempted") is False, f"worker should report pre-run_start block: {result}", failures)
    require(gate.get("operation") == "worker_loop_supervision_gate", f"worker gate missing: {result}", failures)
    require(gate.get("ok") is False and gate.get("can_confirm_bounded_loop") is True, f"worker gate should block record_first without losing confirm readiness: {gate}", failures)
    require(gate.get("status") == "record_first", f"worker gate should preserve record_first status: {gate}", failures)
    plan_quality = gate.get("plan_quality") or {}
    require(plan_quality.get("status") == "attention" and plan_quality.get("issue_count") == 1, f"worker gate lost plan quality attention: {gate}", failures)
    require(plan_quality.get("gate_status") == "attention", f"worker gate lost plan quality gate status: {gate}", failures)
    require(plan_quality.get("hard_run_start_gate") is False, f"worker plan quality should remain non-hard gate: {gate}", failures)
    worker_local = gate.get("local_deployment") or {}
    require(worker_local.get("local_run_path_present") is True, f"worker gate lost local run path summary: {gate}", failures)
    require(worker_local.get("service_managed_loop_present") is True, f"worker gate lost service-managed summary: {gate}", failures)
    require(worker_local.get("recommended_adapter") == "hermes", f"worker gate local adapter mismatch: {gate}", failures)
    require(worker_local.get("service_managed_adapter") == "hermes", f"worker gate service adapter mismatch: {gate}", failures)
    require(worker_local.get("server_executes_shell") is False, f"worker gate local shell proof mismatch: {gate}", failures)
    require(not heartbeat_posts, f"worker should not heartbeat a run that was never started: {heartbeat_posts}", failures)
    require(any((payload.get("metadata") or {}).get("loop_supervision") for payload in audit_posts), "worker audit missing loop supervision metadata", failures)
    return result


def verify_worker_service_closure_consumption(failures: list[str]) -> dict:
    parser = worker.build_parser()
    args = parser.parse_args([
        "--once",
        "--adapter",
        "hermes",
        "--confirm-run",
        "--allow-high-risk",
        "--no-enforce-intake",
    ])
    called_adapter = {"value": False}
    original_execute = worker.execute_adapter_with_retries
    try:
        def fail_if_called(_task, _args):
            called_adapter["value"] = True
            raise AssertionError("adapter execution should be blocked by service closure")

        worker.execute_adapter_with_retries = fail_if_called
        client = FakeWorkerClient(
            plan_quality_attention=False,
            service_closure_attention=True,
            service_closure_hard=True,
        )
        result = worker.process_one_task(client, args)
    finally:
        worker.execute_adapter_with_retries = original_execute
    gate = result.get("loop_supervision_gate") or {}
    audit_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/audit"]
    run_start_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/runs/start"]
    heartbeat_posts = [payload for path, payload in client.posts if path.endswith("/heartbeat")]
    require(called_adapter["value"] is False, "worker called adapter despite service closure attention", failures)
    require(not run_start_posts, f"worker should block before run_start when service closure is required: {run_start_posts}", failures)
    require(not heartbeat_posts, f"worker should not heartbeat a service-closure-blocked run: {heartbeat_posts}", failures)
    require(result.get("reason") == "loop_supervision_blocked", f"worker service closure wrong block reason: {result}", failures)
    require(result.get("live_execution_performed") is False, f"worker service closure should not execute live runtime: {result}", failures)
    require(result.get("run_start_attempted") is False, f"worker service closure should report pre-run_start block: {result}", failures)
    require(gate.get("operation") == "worker_loop_supervision_gate", f"worker service closure gate missing: {result}", failures)
    require(gate.get("ok") is False and gate.get("can_confirm_bounded_loop") is True, f"worker service closure should block without losing confirm readiness: {gate}", failures)
    require(gate.get("status") == "record_first", f"worker service closure should preserve record_first status: {gate}", failures)
    plan_quality = gate.get("plan_quality") or {}
    require(plan_quality.get("status") == "not_applicable" and plan_quality.get("issue_count") == 0, f"worker service closure should not invent plan quality issues: {gate}", failures)
    service_closure = gate.get("service_closure") or {}
    require(service_closure.get("required") is True, f"worker service closure required flag missing: {gate}", failures)
    require(service_closure.get("status") == "attention", f"worker service closure status missing: {gate}", failures)
    require(service_closure.get("step") == "record_service_control_receipt", f"worker service closure step missing: {gate}", failures)
    require(service_closure.get("phase") == "RECORD", f"worker service closure phase missing: {gate}", failures)
    require("--confirm-record" in str(service_closure.get("command") or ""), f"worker service closure command missing confirm proof: {gate}", failures)
    require(service_closure.get("gate_status") == "attention", f"worker service closure gate status missing: {gate}", failures)
    require(service_closure.get("hard_run_start_gate") is True, f"worker hard service closure gate missing: {gate}", failures)
    require(service_closure.get("server_executes_shell") is False, f"worker service closure shell proof mismatch: {gate}", failures)
    require(any((payload.get("metadata") or {}).get("loop_supervision") for payload in audit_posts), "worker service closure audit missing loop supervision metadata", failures)
    return result


def main() -> int:
    server.seed(reset=True)
    failures: list[str] = []
    server_result = verify_server_customer_worker_consumption(failures)
    worker_result = verify_worker_consumption(failures)
    worker_service_result = verify_worker_service_closure_consumption(failures)
    serialized = json.dumps({"server": server_result, "worker": worker_result, "worker_service": worker_service_result}, ensure_ascii=False)
    require(not leaked(serialized), "loop supervision consumption leaked token-like material", failures)
    print(json.dumps({
        "ok": not failures,
        "operation": "operator_loop_supervision_consumption_smoke",
        "server_block_reason": (server_result.get("blocked") or {}).get("reason"),
        "server_external_reason": (server_result.get("external") or {}).get("reason"),
        "worker_block_reason": worker_result.get("reason"),
        "worker_service_block_reason": worker_service_result.get("reason"),
        "live_execution_performed": False,
        "secret_leaked": leaked(serialized),
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
