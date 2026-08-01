#!/usr/bin/env python3
"""Verify Agent Gateway live run_start consumes loop-supervision before run creation."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SMOKE_DB_DIR = tempfile.TemporaryDirectory(prefix="agentops-run-start-loop-supervision-")
os.environ["AGENTOPS_DB_PATH"] = str(Path(SMOKE_DB_DIR.name) / "agentops.db")
os.environ.setdefault("AGENTOPS_SKIP_SEED_EXPORTS", "1")

import server  # noqa: E402


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


def require_gate_safety(gate: dict, label: str, failures: list[str]) -> None:
    safety = gate.get("safety") or {}
    require(gate.get("token_omitted") is True, f"{label} token omission missing: {gate}", failures)
    require(gate.get("live_execution_performed") is False, f"{label} live execution proof missing: {gate}", failures)
    require(gate.get("server_executes_shell") is False, f"{label} shell proof missing: {gate}", failures)
    require(safety.get("read_only") is True, f"{label} read-only proof missing: {safety}", failures)
    require(safety.get("ledger_mutated") is False, f"{label} ledger mutation proof missing: {safety}", failures)
    require(safety.get("live_execution_performed") is False, f"{label} safety live proof missing: {safety}", failures)
    require(safety.get("server_executes_shell") is False, f"{label} safety shell proof missing: {safety}", failures)
    require(safety.get("raw_prompt_omitted") is True, f"{label} raw prompt omission missing: {safety}", failures)
    require(safety.get("raw_response_omitted") is True, f"{label} raw response omission missing: {safety}", failures)
    require(safety.get("raw_content_omitted") is True, f"{label} raw content omission missing: {safety}", failures)
    require(safety.get("token_omitted") is True, f"{label} safety token omission missing: {safety}", failures)


def fake_supervision(adapter: str = "hermes", *, status: str = "record_first", can_confirm: bool = True) -> dict:
    blockers = [] if can_confirm else ["smoke_loop_supervision_blocked"]
    packet = {
        "operation": "agent_work_packet",
        "adapter": adapter,
        "packet_hash": f"pkt_run_start_loop_{adapter}_{status}",
        "status": status,
        "recommended_next": f"agentops operator loop-driver --adapter {adapter} --confirm-loop",
        "primary_next_action": {
            "phase": "RECORD" if status == "record_first" else "EXECUTE",
            "command": f"agentops operator loop-driver --adapter {adapter} --confirm-loop",
            "blockers": blockers,
            "confirm_required": True,
            "receipt_required": status == "record_first",
            "safe_to_auto_continue": False,
            "token_omitted": True,
        },
        "blockers": blockers,
        "attention": ["record_before_execute"] if status == "record_first" else [],
        "evidence_contract": {
            "service_managed_loop": {
                "required": status == "record_first",
                "status": "attention" if status == "record_first" else "not_applicable",
            },
            "token_omitted": True,
        },
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "copy_only": True,
            "token_omitted": True,
        },
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
            "record_required": status == "record_first",
        },
        "items": [
            {
                "operation": "operator_loop_supervision_item",
                "adapter": adapter,
                "status": status,
                "can_preview_loop": True,
                "can_confirm_bounded_loop": can_confirm,
                "should_record_before_execute": status == "record_first",
                "ready_for_live_dispatch": can_confirm,
                "blockers": blockers,
                "attention": ["record_before_execute"] if status == "record_first" else [],
                "review_pressure": {"token_omitted": True},
                "agent_work_packet": packet,
                "gates": [
                    {"id": "bounded_confirm", "ok": can_confirm, "status": "pass" if can_confirm else "blocked", "confirm_required": True, "token_omitted": True},
                    {"id": "server_shell_boundary", "ok": True, "status": "pass", "server_executes_shell": False, "token_omitted": True},
                ],
                "commands": {
                    "recommended_next": "agentops review queue --limit 20" if status == "record_first" else f"agentops operator loop-driver --adapter {adapter} --confirm-loop",
                    "preview_loop": f"agentops operator loop-driver --adapter {adapter} --max-steps 3",
                    "confirm_loop": f"agentops operator loop-driver --adapter {adapter} --max-steps 3 --confirm-loop",
                    "record_review": "agentops review queue --limit 20",
                },
                "next_commands": {
                    "safe_read_commands": [f"agentops operator start-check --adapter {adapter} --limit 8"],
                    "preview_commands": [f"agentops operator loop-driver --adapter {adapter} --max-steps 3"],
                    "confirm_required_commands": [f"agentops operator loop-driver --adapter {adapter} --max-steps 3 --confirm-loop"],
                    "recommended_next": "agentops review queue --limit 20",
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
        "work_packets": [packet],
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def fake_codex_supervision() -> dict:
    return {
        "provider": "agentops-operator",
        "operation": "operator_loop_supervision",
        "status": "ready_to_confirm",
        "summary": {
            "items": 0,
            "current_code_ok": True,
            "can_confirm_all": True,
            "record_required": False,
        },
        "items": [
            {
                "adapter": "hermes",
                "status": "blocked",
                "blockers": ["hermes_runtime_unavailable"],
                "safety": {
                    "read_only": True,
                    "live_execution_performed": False,
                    "server_executes_shell": False,
                    "token_omitted": True,
                },
            }
        ],
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def create_task_and_plan(conn, *, task_id: str, agent_id: str, runtime_type: str) -> str:
    server.ensure_gateway_agent(conn, agent_id, name=f"{runtime_type.title()} Gate Smoke", role="Loop Gate Smoke", runtime_type=runtime_type)
    now = server.now_iso()
    server.upsert_task(conn, {
        "task_id": task_id,
        "workspace_id": "local-demo",
        "title": f"{runtime_type} run-start loop supervision smoke",
        "description": "Verify run_start consumes loop-supervision before live runtime run creation.",
        "requester_id": "usr_founder",
        "owner_agent_id": agent_id,
        "collaborator_agent_ids": "[]",
        "status": "planned",
        "priority": "medium",
        "due_date": None,
        "acceptance_criteria": "No live run is created unless loop supervision is ready.",
        "risk_level": "low",
        "budget_limit_usd": 1.0,
        "created_at": now,
        "updated_at": now,
    }, "run-start-loop-supervision-smoke")
    payload, status = server.agent_gateway_create_agent_plan(conn, {
        "workspace_id": "local-demo",
        "agent_id": agent_id,
        "task_id": task_id,
        "task_understanding": "Use loop supervision plus Agent Plan before run_start.",
        "referenced_specs": ["PROJECT_SPEC.md", "AGENT_WORKFLOW.md"],
        "referenced_memories": ["knowledge/shared/common_failures.md"],
        "referenced_bases": ["base_local_tasks"],
        "proposed_files_to_change": ["server.py"],
        "risk_level": "low",
        "approval_required": False,
        "execution_steps": ["READ", "PLAN", "RETRIEVE", "COMPARE", "EXECUTE", "VERIFY", "RECORD"],
        "verification_plan": "Run run_start_loop_supervision_gate_smoke.py.",
        "rollback_plan": "Leave the task planned if loop supervision blocks run_start.",
        "status": "submitted",
    })
    if status != 201:
        raise RuntimeError(f"agent plan create failed: {status} {payload}")
    plan_id = payload["agent_plan"]["plan_id"]
    verify, verify_status = server.verify_agent_plan(conn, plan_id)
    if verify_status != 200 or (verify.get("verification") or {}).get("pass") is not True:
        raise RuntimeError(f"agent plan verify failed: {verify_status} {verify}")
    return plan_id


def run_start(conn, *, task_id: str, agent_id: str, plan_id: str, runtime_type: str) -> tuple[dict, int]:
    return server.agent_gateway_start_run(conn, {
        "workspace_id": "local-demo",
        "agent_id": agent_id,
        "task_id": task_id,
        "agent_plan_id": plan_id,
        "runtime_type": runtime_type,
        "input_summary": f"{runtime_type} run_start loop supervision smoke.",
    })


def main() -> int:
    server.seed(reset=True)
    failures: list[str] = []
    outputs: list[str] = []
    original_operator_loop_supervision = server.operator_loop_supervision
    call_count = {"value": 0}
    try:
        with server.db() as conn:
            blocked_task = "tsk_run_start_loop_blocked"
            blocked_agent = "agt_run_start_loop_blocked"
            blocked_plan = create_task_and_plan(conn, task_id=blocked_task, agent_id=blocked_agent, runtime_type="hermes")

            def blocked_supervision(conn_arg, headers, qs=None, auth_ctx=None):
                call_count["value"] += 1
                return fake_supervision("hermes", status="blocked", can_confirm=False)

            server.operator_loop_supervision = blocked_supervision
            blocked_payload, blocked_status = run_start(conn, task_id=blocked_task, agent_id=blocked_agent, plan_id=blocked_plan, runtime_type="hermes")
            outputs.append(json.dumps(blocked_payload, ensure_ascii=False))
            blocked_run_count = conn.execute("SELECT COUNT(*) c FROM runs WHERE task_id=?", (blocked_task,)).fetchone()["c"]
            blocked_audit_count = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs WHERE entity_id=? AND action='agent_gateway.run_start_loop_supervision_blocked'",
                (blocked_task,),
            ).fetchone()["c"]
            require(blocked_status == 428, f"Hermes blocked supervision should reject run_start: {blocked_status} {blocked_payload}", failures)
            require(blocked_payload.get("error") == "run_start_loop_supervision_blocked", f"wrong block error: {blocked_payload}", failures)
            require(blocked_payload.get("live_execution_performed") is False, f"blocked path must not execute live runtime: {blocked_payload}", failures)
            require((blocked_payload.get("loop_supervision_gate") or {}).get("operation") == "agent_gateway_run_start_loop_supervision_gate", f"missing blocked gate: {blocked_payload}", failures)
            require_gate_safety(blocked_payload.get("loop_supervision_gate") or {}, "blocked gate", failures)
            require(blocked_run_count == 0, f"blocked supervision created a run for {blocked_task}", failures)
            require(blocked_audit_count >= 1, "blocked run_start audit missing", failures)

            ready_task = "tsk_run_start_loop_ready"
            ready_agent = "agt_run_start_loop_ready"
            ready_plan = create_task_and_plan(conn, task_id=ready_task, agent_id=ready_agent, runtime_type="hermes")

            def ready_supervision(conn_arg, headers, qs=None, auth_ctx=None):
                call_count["value"] += 1
                return fake_supervision("hermes", status="record_first", can_confirm=True)

            server.operator_loop_supervision = ready_supervision
            ready_payload, ready_status = run_start(conn, task_id=ready_task, agent_id=ready_agent, plan_id=ready_plan, runtime_type="hermes")
            outputs.append(json.dumps(ready_payload, ensure_ascii=False))
            ready_gate = ready_payload.get("loop_supervision_gate") or {}
            require(ready_status == 201, f"Hermes ready supervision should allow run_start: {ready_status} {ready_payload}", failures)
            require(ready_gate.get("ok") is True and ready_gate.get("status") == "record_first", f"ready gate should be attached: {ready_gate}", failures)
            require(bool(ready_gate.get("supervision_hash")), f"ready gate missing supervision_hash: {ready_gate}", failures)
            require_gate_safety(ready_gate, "ready gate", failures)
            require((ready_payload.get("agent_plan") or {}).get("loop_supervision_hash") == ready_gate.get("supervision_hash"), f"run_start response missing plan supervision hash: {ready_payload}", failures)

            codex_task = "tsk_run_start_loop_codex"
            codex_agent = "agt_run_start_loop_codex"
            codex_plan = create_task_and_plan(conn, task_id=codex_task, agent_id=codex_agent, runtime_type="codex")

            def codex_supervision(conn_arg, headers, qs=None, auth_ctx=None):
                call_count["value"] += 1
                return fake_codex_supervision()

            server.operator_loop_supervision = codex_supervision
            codex_payload, codex_status = run_start(conn, task_id=codex_task, agent_id=codex_agent, plan_id=codex_plan, runtime_type="codex")
            outputs.append(json.dumps(codex_payload, ensure_ascii=False))
            require(codex_status == 201, f"Codex current-code supervision should allow run_start: {codex_status} {codex_payload}", failures)
            require((codex_payload.get("loop_supervision_gate") or {}).get("runtime_type") == "codex", f"Codex gate missing: {codex_payload}", failures)
            require_gate_safety(codex_payload.get("loop_supervision_gate") or {}, "codex gate", failures)

            mock_task = "tsk_run_start_loop_mock"
            mock_agent = "agt_run_start_loop_mock"
            mock_plan = create_task_and_plan(conn, task_id=mock_task, agent_id=mock_agent, runtime_type="mock")

            def fail_if_mock_calls_supervision(conn_arg, headers, qs=None, auth_ctx=None):
                raise AssertionError("mock run_start should not read loop supervision")

            server.operator_loop_supervision = fail_if_mock_calls_supervision
            mock_payload, mock_status = run_start(conn, task_id=mock_task, agent_id=mock_agent, plan_id=mock_plan, runtime_type="mock")
            outputs.append(json.dumps(mock_payload, ensure_ascii=False))
            require(mock_status == 201, f"mock run_start should stay unaffected: {mock_status} {mock_payload}", failures)
            require("loop_supervision_gate" not in mock_payload, f"mock response should not include live supervision gate: {mock_payload}", failures)
    finally:
        server.operator_loop_supervision = original_operator_loop_supervision
    serialized = "\n".join(outputs)
    require(not leaked(serialized), "run_start loop supervision smoke leaked token-like material", failures)
    print(json.dumps({
        "ok": not failures,
        "operation": "run_start_loop_supervision_gate_smoke",
        "blocked_rejected": True,
        "ready_allowed": True,
        "codex_allowed": True,
        "mock_unaffected": True,
        "operator_loop_supervision_calls": call_count["value"],
        "secret_leaked": leaked(serialized),
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
