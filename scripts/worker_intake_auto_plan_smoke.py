#!/usr/bin/env python3
"""Verify workers can safely auto-plan intake-blocked assigned tasks."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli import worker  # noqa: E402


TOKEN_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+(?!\\[REDACTED\\])[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"agtok_[A-Za-z0-9_-]{16,}"),
    re.compile(r"agtsess_[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9_-]{8,}"),
]
RECORDED_POSTS: list[tuple[str, dict]] = []


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def leaked(text: str) -> bool:
    return any(pattern.search(text) for pattern in TOKEN_PATTERNS)


class AutoPlanClient:
    workspace_id = "local-demo"
    agent_id = "agt_worker_auto_plan_openclaw"
    api_key = ""

    def __init__(self, *, risk_level: str = "low", plan_id: str | None = None, reject_heartbeat: bool = False, unassigned_head: bool = False) -> None:
        self.risk_level = risk_level
        self.plan_id = plan_id
        self.reject_heartbeat = reject_heartbeat
        self.unassigned_head = unassigned_head
        self.gets: list[tuple[str, dict | None]] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, path: str, query: dict | None = None):
        self.gets.append((path, query))
        if path == "/api/agent-gateway/tasks/pull":
            failed = ["verified_agent_plan"] if self.plan_id else ["agent_plan", "verified_agent_plan", "knowledge_retrieval", "base_reference"]
            blocked_tasks = []
            if self.unassigned_head:
                blocked_tasks.append({
                    "task_id": "tsk_unassigned_head",
                    "title": "Unassigned queue head",
                    "status": "planned",
                    "priority": "low",
                    "risk_level": "low",
                    "assigned_adapter": None,
                    "assigned_agent_ids": [],
                    "plan_id": None,
                    "failed_gate_ids": ["assigned_agent", "agent_plan"],
                    "token_omitted": True,
                })
            blocked_tasks.append(
                {
                    "task_id": "tsk_worker_auto_plan_smoke",
                    "title": "Auto plan smoke",
                    "status": "planned",
                    "priority": "medium",
                    "risk_level": self.risk_level,
                    "assigned_adapter": "openclaw",
                    "assigned_agent_ids": [self.agent_id],
                    "plan_id": self.plan_id,
                    "failed_gate_ids": failed,
                    "token_omitted": True,
                }
            )
            return {
                "tasks": [],
                "intake": {
                    "blocked": len(blocked_tasks),
                    "next_actions": ["agentops knowledge search \"Auto plan smoke\" --limit 10"],
                    "blocked_tasks": blocked_tasks,
                    "token_omitted": True,
                },
            }
        if path == "/api/agent-gateway/tasks/tsk_worker_auto_plan_smoke":
            return {
                "task": {
                    "task_id": "tsk_worker_auto_plan_smoke",
                    "title": "Auto plan smoke",
                    "description": "Worker should create a verified Agent Plan before pulling this assigned task.",
                    "acceptance_criteria": "No run_start or adapter execution happens during intake auto-plan.",
                    "risk_level": self.risk_level,
                },
                "token_omitted": True,
            }
        if path == "/api/agent-gateway/knowledge/evidence-packet":
            return {
                "operation": "knowledge_retrieval_evidence_packet",
                "status": "ready",
                "query_hash": "kqh_auto_plan_smoke",
                "packet_hash": "kph_auto_plan_smoke",
                "metrics": {"recall_at_5": 1.0, "mrr": 1.0},
                "task_context": {
                    "task_id": "tsk_worker_auto_plan_smoke",
                    "task_found": True,
                    "query_source": "task_id",
                    "source_fields": ["title", "description", "acceptance_criteria", "risk_level"],
                    "task_text_omitted": True,
                    "token_omitted": True,
                },
                "primary_search": {
                    "results": [
                        {
                            "retrieval_id": "kret_auto_plan",
                            "doc_id": "doc_project_spec",
                            "chunk_id": "chunk_project_spec",
                            "path": "docs/AGENT_WORK_METHOD_BLOCK.md",
                            "source_hash": "source_hash_auto_plan",
                            "rank": 1,
                        }
                    ]
                },
                "token_omitted": True,
            }
        if path.startswith("/api/agent-gateway/agent-plans/") and path.endswith("/verify"):
            return {"verification": {"pass": True, "failed_checks": [], "token_omitted": True}, "token_omitted": True}
        raise AssertionError(f"unexpected GET {path} {query}")

    def post(self, path: str, payload: dict, timeout: int = 180):
        self.posts.append((path, payload))
        RECORDED_POSTS.append((path, payload))
        if path == "/api/agent-gateway/heartbeat":
            if self.reject_heartbeat:
                raise RuntimeError("heartbeat rejected")
            return {
                "agent_id": self.agent_id,
                "status": payload.get("status"),
                "ledger_recorded": True,
                "token_omitted": True,
            }
        if path == "/api/agent-gateway/agent-plans":
            return {"agent_plan": {"plan_id": "plan_worker_auto_plan_smoke"}, "token_omitted": True}
        if path == "/api/agent-gateway/audit":
            return {"audit": {"audit_id": "aud_worker_auto_plan_smoke"}, "token_omitted": True}
        raise AssertionError(f"unexpected POST {path} {payload}")


def require_blocked_heartbeat(client: AutoPlanClient, label: str, failures: list[str]) -> None:
    heartbeat_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/heartbeat"]
    require(len(heartbeat_posts) == 1, f"{label} blocked heartbeat count mismatch: {heartbeat_posts}", failures)
    if not heartbeat_posts:
        return
    heartbeat = heartbeat_posts[0]
    require(heartbeat.get("workspace_id") == client.workspace_id, f"{label} heartbeat workspace mismatch: {heartbeat}", failures)
    require(heartbeat.get("agent_id") == client.agent_id, f"{label} heartbeat agent mismatch: {heartbeat}", failures)
    require(heartbeat.get("status") == "idle", f"{label} heartbeat status mismatch: {heartbeat}", failures)
    require(heartbeat.get("runtime_type") == "openclaw", f"{label} heartbeat runtime mismatch: {heartbeat}", failures)
    require(
        heartbeat.get("summary") == "Worker intake is blocked pending required planning or review.",
        f"{label} heartbeat summary mismatch: {heartbeat}",
        failures,
    )


def run_auto_create_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw", "--confirm-run"])
    client = AutoPlanClient(risk_level="low")
    result = worker.process_one_task(client, args)
    plan_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/agent-plans"]
    audit_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/audit"]
    require(result.get("reason") == "intake_auto_planned", f"auto-plan reason mismatch: {result}", failures)
    require(result.get("processed") is False, f"auto-plan must not count as task execution: {result}", failures)
    require(result.get("run_start_attempted") is False, f"auto-plan must not start run: {result}", failures)
    require(result.get("live_execution_performed") is False, f"auto-plan must not execute live runtime: {result}", failures)
    require(result.get("plan_id") == "plan_worker_auto_plan_smoke", f"plan id missing: {result}", failures)
    require(bool(plan_posts), "agent plan was not created", failures)
    if plan_posts:
        payload = plan_posts[0]
        require(payload.get("referenced_specs"), f"plan missing referenced specs: {payload}", failures)
        require(payload.get("referenced_memories"), f"plan missing referenced memories: {payload}", failures)
        require(payload.get("referenced_bases") == ["base_local_tasks", "base_local_memory"], f"plan bases mismatch: {payload}", failures)
        require(payload.get("execution_steps") == ["READ", "PLAN", "RETRIEVE", "COMPARE", "EXECUTE", "VERIFY", "RECORD"], f"plan steps mismatch: {payload}", failures)
    require(bool(audit_posts), "auto-plan audit missing", failures)
    require_blocked_heartbeat(client, "auto-plan", failures)
    return result


def run_existing_verify_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw", "--confirm-run"])
    client = AutoPlanClient(risk_level="low", plan_id="plan_existing_auto_plan_smoke")
    result = worker.process_one_task(client, args)
    plan_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/agent-plans"]
    require(result.get("reason") == "intake_plan_verified", f"existing plan verify reason mismatch: {result}", failures)
    require(result.get("processed") is False, f"verify-only must not count as task execution: {result}", failures)
    require(result.get("plan_id") == "plan_existing_auto_plan_smoke", f"existing plan id mismatch: {result}", failures)
    require(not plan_posts, f"verify-only should not create a duplicate plan: {plan_posts}", failures)
    require_blocked_heartbeat(client, "existing-plan", failures)
    return result


def run_unassigned_head_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw", "--confirm-run"])
    client = AutoPlanClient(risk_level="low", unassigned_head=True)
    result = worker.process_one_task(client, args)
    pull_queries = [query for path, query in client.gets if path == "/api/agent-gateway/tasks/pull"]
    require(result.get("reason") == "intake_auto_planned", f"unassigned queue head blocked assigned task: {result}", failures)
    require(result.get("task_id") == "tsk_worker_auto_plan_smoke", f"wrong blocked task selected behind unassigned head: {result}", failures)
    require(
        bool(pull_queries) and pull_queries[0].get("limit") == worker.WORKER_PULL_CANDIDATE_LIMIT,
        f"Worker did not request a bounded candidate window: {pull_queries}",
        failures,
    )
    return {
        "reason": result.get("reason"),
        "task_id": result.get("task_id"),
        "pull_candidate_limit": pull_queries[0].get("limit") if pull_queries else None,
    }


def run_intake_reuse_helper_case(failures: list[str]) -> dict:
    client = AutoPlanClient(risk_level="low", plan_id="plan_existing_auto_plan_smoke")
    plan_id, verified = worker.verified_intake_plan_for_task(client, {
        "task_id": "tsk_worker_auto_plan_smoke",
        "intake": {
            "plan_id": "plan_existing_auto_plan_smoke",
            "plan_verified": True,
            "token_omitted": True,
        },
    })
    require(plan_id == "plan_existing_auto_plan_smoke", f"verified intake plan was not reused: {plan_id} {verified}", failures)
    require((verified or {}).get("verification", {}).get("pass") is True, f"verified intake plan did not pass: {verified}", failures)
    require(not client.posts, f"reuse helper should not mutate ledger: {client.posts}", failures)
    return {"plan_id": plan_id, "verification_pass": (verified or {}).get("verification", {}).get("pass")}


def run_high_risk_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw", "--confirm-run"])
    client = AutoPlanClient(risk_level="critical")
    result = worker.process_one_task(client, args)
    plan_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/agent-plans"]
    require(result.get("reason") == "intake_auto_plan_risk_blocked", f"high-risk block reason mismatch: {result}", failures)
    require(result.get("processed") is False, f"high-risk block must not count as task execution: {result}", failures)
    require(not plan_posts, f"high-risk task should not be auto-planned without allow-high-risk: {plan_posts}", failures)
    require_blocked_heartbeat(client, "high-risk", failures)
    return result


def run_auto_plan_disabled_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw", "--confirm-run", "--no-auto-plan-intake"])
    client = AutoPlanClient(risk_level="low")
    result = worker.process_one_task(client, args)
    plan_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/agent-plans"]
    require(result.get("reason") == "intake_blocked", f"disabled auto-plan reason mismatch: {result}", failures)
    require(result.get("processed") is False, f"disabled auto-plan must not count as task execution: {result}", failures)
    require(not plan_posts, f"disabled auto-plan should not create a plan: {plan_posts}", failures)
    require_blocked_heartbeat(client, "disabled-auto-plan", failures)
    return result


def run_heartbeat_cadence_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args([
        "--once",
        "--adapter",
        "openclaw",
        "--confirm-run",
        "--no-auto-plan-intake",
        "--heartbeat-interval-sec",
        "60",
    ])
    client = AutoPlanClient(risk_level="low")
    results = [worker.process_one_task(client, args) for _ in range(20)]
    heartbeat_posts = [payload for path, payload in client.posts if path == "/api/agent-gateway/heartbeat"]
    require(len(heartbeat_posts) == 1, f"repeated blocked polls amplified heartbeats: {len(heartbeat_posts)}", failures)
    require(all(result.get("reason") == "intake_blocked" for result in results), f"cadence case changed intake result: {results}", failures)
    return {"iterations": len(results), "heartbeat_posts": len(heartbeat_posts)}


def run_heartbeat_rejection_case(failures: list[str]) -> dict:
    args = worker.build_parser().parse_args([
        "--once",
        "--adapter",
        "openclaw",
        "--confirm-run",
        "--no-auto-plan-intake",
    ])
    client = AutoPlanClient(risk_level="low", reject_heartbeat=True)
    propagated = False
    try:
        worker.process_one_task(client, args)
    except RuntimeError as exc:
        propagated = str(exc) == "heartbeat rejected"
    require(propagated, "blocked heartbeat rejection was silently treated as healthy", failures)
    return {"rejection_propagated": propagated}


def main() -> int:
    failures: list[str] = []
    state_args = worker.build_parser().parse_args(["--once", "--adapter", "openclaw"])
    state_shape = worker.WorkerState(state_args).data
    require(state_shape.get("state_schema_version") == 2, f"Worker state schema version missing: {state_shape}", failures)
    require("last_heartbeat_at" in state_shape and "last_iteration_at" in state_shape,
            f"Worker state compatibility fields missing: {state_shape}", failures)
    create_result = run_auto_create_case(failures)
    verify_result = run_existing_verify_case(failures)
    unassigned_head_result = run_unassigned_head_case(failures)
    reuse_result = run_intake_reuse_helper_case(failures)
    high_risk_result = run_high_risk_case(failures)
    disabled_result = run_auto_plan_disabled_case(failures)
    cadence_result = run_heartbeat_cadence_case(failures)
    rejection_result = run_heartbeat_rejection_case(failures)
    serialized = json.dumps({
        "create": create_result,
        "verify": verify_result,
        "unassigned_head": unassigned_head_result,
        "reuse": reuse_result,
        "high_risk": high_risk_result,
        "disabled": disabled_result,
        "cadence": cadence_result,
        "rejection": rejection_result,
        "recorded_posts": RECORDED_POSTS,
    }, ensure_ascii=False)
    require(not leaked(serialized), "worker intake auto-plan leaked token-like material", failures)
    print(json.dumps({
        "ok": not failures,
        "operation": "worker_intake_auto_plan_smoke",
        "auto_create_reason": create_result.get("reason"),
        "existing_verify_reason": verify_result.get("reason"),
        "unassigned_head_reason": unassigned_head_result.get("reason"),
        "pull_candidate_limit": unassigned_head_result.get("pull_candidate_limit"),
        "intake_reuse_plan_id": reuse_result.get("plan_id"),
        "high_risk_reason": high_risk_result.get("reason"),
        "disabled_auto_plan_reason": disabled_result.get("reason"),
        "blocked_heartbeat_paths": 4,
        "cadence_iterations": cadence_result.get("iterations"),
        "cadence_heartbeat_posts": cadence_result.get("heartbeat_posts"),
        "heartbeat_rejection_propagated": rejection_result.get("rejection_propagated"),
        "worker_state_schema_version": state_shape.get("state_schema_version"),
        "legacy_heartbeat_field_retained": "last_heartbeat_at" in state_shape,
        "live_execution_performed": False,
        "secret_leaked": leaked(serialized),
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
