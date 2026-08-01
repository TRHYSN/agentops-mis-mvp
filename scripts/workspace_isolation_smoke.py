#!/usr/bin/env python3
"""Smoke-test Agent Gateway workspace isolation without printing token secrets."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")


def expected_stable_id(prefix: str, *parts: object) -> str:
    raw = "::".join(str(part) for part in parts if part is not None and str(part) != "")
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    if slug and len(slug) <= 64:
        return f"{prefix}_{slug}"
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def http_json(
    method: str,
    base_url: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
    workspace_header: str | None = None,
    query: dict | None = None,
    timeout: int = 30,
) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
    headers = {"Content-Type": "application/json"}
    if workspace_header:
        headers["X-AgentOps-Workspace-Id"] = workspace_header
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def create_task(base_url: str, task_id: str, workspace_id: str, agent_id: str, title: str) -> None:
    status, body = http_json("POST", base_url, "/api/tasks", {
        "task_id": task_id,
        "workspace_id": workspace_id,
        "title": title,
        "description": f"Workspace isolation smoke task for {workspace_id}.",
        "owner_agent_id": agent_id,
        "status": "planned",
        "priority": "high",
        "risk_level": "low",
        "acceptance_criteria": "Only the matching workspace token may access this task.",
    })
    require(status == 201, f"task create failed for {task_id}: {status} {body}")


def create_verified_plan(base_url: str, workspace_id: str, agent_id: str, task_id: str, token: str) -> str:
    status, plan = http_json("POST", base_url, "/api/agent-gateway/agent-plans", {
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "task_id": task_id,
        "task_understanding": "Verify workspace isolation with a plan-bound run.",
        "referenced_specs": ["PROJECT_SPEC.md", "AGENT_WORKFLOW.md"],
        "referenced_memories": ["knowledge/shared/common_failures.md"],
        "referenced_bases": ["base_local_tasks"],
        "proposed_files_to_change": ["scripts/workspace_isolation_smoke.py"],
        "risk_level": "low",
        "execution_steps": ["READ", "PLAN", "RETRIEVE", "VERIFY"],
        "verification_plan": "Run workspace_isolation_smoke.py.",
        "rollback_plan": "Keep the task planned if plan-bound run_start fails.",
        "status": "submitted",
    }, token=token, workspace_header=workspace_id)
    require(status == 201, f"plan create failed for {task_id}: {status} {plan}")
    plan_id = (plan.get("agent_plan") or {}).get("plan_id")
    require(bool(plan_id), f"plan id missing for {task_id}: {plan}")
    status, verified = http_json("GET", base_url, f"/api/agent-gateway/agent-plans/{plan_id}/verify", token=token, workspace_header=workspace_id)
    require(status == 200 and (verified.get("verification") or {}).get("pass") is True, f"plan verify failed for {task_id}: {status} {verified}")
    return str(plan_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Agent Gateway token workspace isolation.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args(argv)

    stamp = now_stamp()
    agent_id = f"agt_workspace_iso_smoke_{stamp}"
    workspace_a = f"ws_iso_a_{stamp}"
    workspace_b = f"ws_iso_b_{stamp}"
    task_a = f"tsk_workspace_iso_a_{stamp}"
    task_a_other = f"tsk_workspace_iso_a_other_{stamp}"
    task_b = f"tsk_workspace_iso_b_{stamp}"
    artifact_collision_id = f"art_workspace_collision_{stamp}"
    approval_collision_id = f"ap_workspace_collision_{stamp}"
    plan_collision_id = f"plan_workspace_collision_{stamp}"
    plan_approval_collision_id = f"ap_plan_{plan_collision_id.lower()}"
    prepared_action_collision_id = f"pa_workspace_collision_{stamp}"
    prepared_approval_collision_id = f"ap_prepared_workspace_collision_{stamp}"
    run_task_mismatch_plan_id = f"plan_run_task_mismatch_{stamp}"
    plan_subject_collision_id = f"plan_subject_collision_{stamp}"
    plan_subject_approval_id = expected_stable_id("ap_plan", plan_subject_collision_id)
    tool_prepare_call_id = f"tc_prepare_collision_{stamp}"
    tool_prepare_idempotency_key = f"prepare-collision-{stamp}"
    token_id = None
    token_b_id = None
    token = None
    token_b = None
    result = {
        "ok": False,
        "agent_id": agent_id,
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "task_a": task_a,
        "task_b": task_b,
        "token_omitted": True,
    }

    try:
        status, created = http_json("POST", args.base_url, "/api/agent-gateway/enrollment/create", {
            "workspace_id": workspace_a,
            "agent_id": agent_id,
            "name": "Workspace Isolation Smoke",
            "runtime_type": "mock",
            "scopes": ["agents:heartbeat", "tasks:read", "tasks:claim", "agent_plans:read", "agent_plans:write", "runs:write", "toolcalls:write", "artifacts:write", "approvals:request", "evaluations:submit", "audit:write"],
            "ttl_days": 1,
            "heartbeat_timeout_sec": 60,
        })
        require(status == 201, f"enrollment create failed: {status} {created}")
        token = created["token"]
        token_id = created["token_id"]
        result["token_id"] = token_id

        status, created_b = http_json("POST", args.base_url, "/api/agent-gateway/enrollment/create", {
            "workspace_id": workspace_b,
            "agent_id": agent_id,
            "name": "Workspace Isolation Smoke B",
            "runtime_type": "mock",
            "scopes": ["agents:heartbeat", "tasks:read", "tasks:claim", "agent_plans:read", "agent_plans:write", "runs:write", "toolcalls:write", "artifacts:write", "approvals:request", "evaluations:submit", "audit:write"],
            "ttl_days": 1,
            "heartbeat_timeout_sec": 60,
        })
        require(status == 201, f"workspace B enrollment create failed: {status} {created_b}")
        token_b = created_b["token"]
        token_b_id = created_b["token_id"]
        result["token_b_id"] = token_b_id

        create_task(args.base_url, task_a, workspace_a, agent_id, "workspace A isolation smoke")
        create_task(args.base_url, task_a_other, workspace_a, agent_id, "workspace A alternate task anchor")
        create_task(args.base_url, task_b, workspace_b, agent_id, "workspace B isolation smoke")

        status, pulled = http_json(
            "GET",
            args.base_url,
            "/api/agent-gateway/tasks/pull",
            token=token,
            workspace_header=workspace_a,
            query={"status": "planned", "limit": 20},
        )
        require(status == 200, f"pull failed: {status} {pulled}")
        task_ids = {item.get("task_id") for item in pulled.get("tasks", [])}
        require(task_a in task_ids, f"workspace A task missing from pull: {task_ids}")
        require(task_b not in task_ids, f"workspace B task leaked into pull: {task_ids}")

        status, header_spoof = http_json(
            "GET",
            args.base_url,
            "/api/agent-gateway/tasks/pull",
            token=token,
            workspace_header=workspace_b,
            query={"status": "planned", "limit": 20},
        )
        require(status == 403, f"header spoof should fail: {status} {header_spoof}")

        status, query_spoof = http_json(
            "GET",
            args.base_url,
            "/api/agent-gateway/tasks/pull",
            token=token,
            workspace_header=workspace_a,
            query={"workspace_id": workspace_b, "status": "planned", "limit": 20},
        )
        require(status == 403, f"query spoof should fail: {status} {query_spoof}")

        status, claim_b = http_json(
            "POST",
            args.base_url,
            f"/api/agent-gateway/tasks/{task_b}/claim",
            payload={"workspace_id": workspace_a, "runtime_type": "mock"},
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 403, f"cross-workspace claim should fail: {status} {claim_b}")

        status, start_b = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/runs/start",
            payload={"workspace_id": workspace_a, "task_id": task_b, "runtime_type": "mock"},
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 403, f"cross-workspace run start should fail: {status} {start_b}")

        status, claim_b_owner = http_json(
            "POST",
            args.base_url,
            f"/api/agent-gateway/tasks/{task_b}/claim",
            payload={"workspace_id": workspace_b, "runtime_type": "mock"},
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status == 200, f"workspace B owner claim failed: {status} {claim_b_owner}")

        plan_b = create_verified_plan(args.base_url, workspace_b, agent_id, task_b, token_b)
        status, start_b_owner = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/runs/start",
            payload={"workspace_id": workspace_b, "task_id": task_b, "runtime_type": "mock", "agent_plan_id": plan_b},
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status in {200, 201}, f"workspace B owner run start failed: {status} {start_b_owner}")
        run_b_id = (start_b_owner.get("run") or {}).get("run_id")
        require(bool(run_b_id), f"workspace B run_id missing: {start_b_owner}")

        status, artifact_b = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/artifacts",
            payload={
                "workspace_id": workspace_b,
                "run_id": run_b_id,
                "artifact_id": artifact_collision_id,
                "title": "Workspace B authoritative artifact",
                "summary": "Must not be overwritten through another workspace run.",
            },
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status == 201, f"workspace B artifact fixture failed: {status} {artifact_b}")
        status, approval_b = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_b,
                "run_id": run_b_id,
                "approval_id": approval_collision_id,
                "reason": "Workspace B authoritative approval",
            },
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status == 201, f"workspace B approval fixture failed: {status} {approval_b}")
        status, plan_approval_b = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_b,
                "run_id": run_b_id,
                "approval_id": plan_approval_collision_id,
                "reason": "Workspace B authoritative predictable plan approval",
            },
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status == 201, f"workspace B plan approval fixture failed: {status} {plan_approval_b}")
        status, prepared_approval_b = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_b,
                "run_id": run_b_id,
                "approval_id": prepared_approval_collision_id,
                "reason": "Workspace B authoritative prepared action approval",
            },
            token=token_b,
            workspace_header=workspace_b,
        )
        require(status == 201, f"workspace B prepared approval fixture failed: {status} {prepared_approval_b}")

        cross_write_checks = {}
        cross_write_payloads = [
            ("run_heartbeat", "POST", f"/api/agent-gateway/runs/{run_b_id}/heartbeat", {"workspace_id": workspace_a, "status": "completed", "output_summary": "should be blocked"}),
            ("tool_call", "POST", "/api/agent-gateway/tool-calls", {"workspace_id": workspace_a, "run_id": run_b_id, "tool_name": "workspace.isolation.blocked"}),
            ("artifact", "POST", "/api/agent-gateway/artifacts", {"workspace_id": workspace_a, "run_id": run_b_id, "title": "blocked artifact", "summary": "should be blocked"}),
            ("approval", "POST", "/api/agent-gateway/approvals/request", {"workspace_id": workspace_a, "run_id": run_b_id, "reason": "should be blocked"}),
            ("evaluation", "POST", "/api/agent-gateway/evaluations/submit", {"workspace_id": workspace_a, "run_id": run_b_id, "score": 1.0}),
            ("audit", "POST", "/api/agent-gateway/audit", {"workspace_id": workspace_a, "run_id": run_b_id, "action": "workspace.isolation.blocked"}),
            ("audit_entity_spoof", "POST", "/api/agent-gateway/audit", {"workspace_id": workspace_a, "entity_type": "tasks", "entity_id": task_b, "action": "workspace.isolation.entity_spoof"}),
            ("audit_custom_anchor_spoof", "POST", "/api/agent-gateway/audit", {"workspace_id": workspace_a, "entity_type": "customer_extension", "entity_id": "foreign-anchor", "task_id": task_b, "action": "workspace.isolation.custom_anchor_spoof"}),
        ]
        for label, method, path, payload in cross_write_payloads:
            status, blocked = http_json(
                method,
                args.base_url,
                path,
                payload=payload,
                token=token,
                workspace_header=workspace_a,
            )
            require(status == 403, f"cross-workspace {label} should fail: {status} {blocked}")
            cross_write_checks[label] = blocked.get("error")

        status, claim_a = http_json(
            "POST",
            args.base_url,
            f"/api/agent-gateway/tasks/{task_a}/claim",
            payload={"workspace_id": workspace_a, "runtime_type": "mock"},
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"workspace A claim failed: {status} {claim_a}")

        status, plan_approval_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/agent-plans",
            payload={
                "workspace_id": workspace_a,
                "agent_id": agent_id,
                "task_id": task_a,
                "plan_id": plan_collision_id,
                "task_understanding": "Attempt the predictable approval ID collision without persisting a plan.",
                "referenced_specs": ["PROJECT_SPEC.md"],
                "referenced_memories": ["knowledge/shared/common_failures.md"],
                "referenced_bases": ["base_local_tasks"],
                "proposed_files_to_change": ["scripts/workspace_isolation_smoke.py"],
                "risk_level": "high",
                "approval_required": True,
                "execution_steps": ["READ", "PLAN", "VERIFY"],
                "verification_plan": "The conflicting plan and its generated approval must not persist.",
                "rollback_plan": "Rollback the complete plan transaction on approval authority conflict.",
                "status": "submitted",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 409 and plan_approval_collision.get("error") == "approval_id_conflict",
            f"predictable Agent Plan approval ID collision should fail: {status} {plan_approval_collision}",
        )
        status, missing_collision_plan = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/agent-plans/{plan_collision_id}",
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 404,
            f"failed Agent Plan approval collision persisted a plan: {status} {missing_collision_plan}",
        )

        plan_a = create_verified_plan(args.base_url, workspace_a, agent_id, task_a, token)
        status, start_a = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/runs/start",
            payload={"workspace_id": workspace_a, "task_id": task_a, "runtime_type": "mock", "agent_plan_id": plan_a},
            token=token,
            workspace_header=workspace_a,
        )
        require(status in {200, 201}, f"workspace A run start failed: {status} {start_a}")
        run_id = (start_a.get("run") or {}).get("run_id")
        require(bool(run_id), f"run_id missing from start response: {start_a}")

        focused_regression_failures = []

        status, run_task_mismatch_plan = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/agent-plans",
            payload={
                "workspace_id": workspace_a,
                "agent_id": agent_id,
                "task_id": task_a_other,
                "run_id": run_id,
                "plan_id": run_task_mismatch_plan_id,
                "task_understanding": "Reject a plan whose explicit task anchor differs from its run task.",
                "referenced_specs": ["PROJECT_SPEC.md"],
                "referenced_memories": ["knowledge/shared/common_failures.md"],
                "referenced_bases": ["base_local_tasks"],
                "proposed_files_to_change": ["scripts/workspace_isolation_smoke.py"],
                "risk_level": "low",
                "execution_steps": ["READ", "PLAN", "VERIFY"],
                "verification_plan": "The mismatched task/run plan must fail without persistence.",
                "rollback_plan": "Keep both task anchors and the existing run unchanged.",
                "status": "submitted",
            },
            token=token,
            workspace_header=workspace_a,
        )
        mismatch_post_status = status
        status, mismatch_plan_readback = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/agent-plans/{run_task_mismatch_plan_id}",
            token=token,
            workspace_header=workspace_a,
        )
        if mismatch_post_status != 409 or status != 404:
            focused_regression_failures.append(
                "run_id plus a different task_id must return 409 and persist no Agent Plan "
                f"(post={mismatch_post_status}, readback={status})"
            )

        status, ordinary_plan_approval = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "approval_id": plan_subject_approval_id,
                "reason": "Ordinary same-authority approval fixture; not an Agent Plan subject approval.",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 201, f"ordinary plan-subject approval fixture failed: {status} {ordinary_plan_approval}")
        status, plan_subject_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/agent-plans",
            payload={
                "workspace_id": workspace_a,
                "agent_id": agent_id,
                "task_id": task_a,
                "run_id": run_id,
                "plan_id": plan_subject_collision_id,
                "task_understanding": "Do not reuse an ordinary Approval as this high-risk Plan subject approval.",
                "referenced_specs": ["PROJECT_SPEC.md"],
                "referenced_memories": ["knowledge/shared/common_failures.md"],
                "referenced_bases": ["base_local_tasks"],
                "proposed_files_to_change": ["scripts/workspace_isolation_smoke.py"],
                "risk_level": "high",
                "approval_required": True,
                "execution_steps": ["READ", "PLAN", "VERIFY"],
                "verification_plan": "The preexisting ordinary Approval must cause a subject conflict.",
                "rollback_plan": "Rollback the complete Agent Plan transaction.",
                "status": "submitted",
            },
            token=token,
            workspace_header=workspace_a,
        )
        plan_subject_post_status = status
        status, plan_subject_readback = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/agent-plans/{plan_subject_collision_id}",
            token=token,
            workspace_header=workspace_a,
        )
        if plan_subject_post_status != 409 or status != 404:
            focused_regression_failures.append(
                "ordinary Approval must not be reused as an Agent Plan subject approval; Plan must rollback "
                f"(post={plan_subject_post_status}, readback={status})"
            )

        status, run_before_prepare_collision = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/runs/{run_id}",
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"run precondition read failed: {status} {run_before_prepare_collision}")
        status, task_before_prepare_collision = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/tasks/{task_a}",
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"task precondition read failed: {status} {task_before_prepare_collision}")
        tool_prepare_approval_id = expected_stable_id(
            "ap_prepared_action",
            run_id,
            tool_prepare_idempotency_key,
        )
        status, ordinary_tool_approval = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "approval_id": tool_prepare_approval_id,
                "reason": "Ordinary Approval fixture that must conflict with Prepared Action authority.",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 201, f"ordinary prepared-action approval fixture failed: {status} {ordinary_tool_approval}")
        status, tool_prepare_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/tool-calls",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "agent_id": agent_id,
                "tool_call_id": tool_prepare_call_id,
                "tool_name": "workspace.isolation.atomic_prepare_collision",
                "tool_category": "custom",
                "risk_level": "high",
                "status": "waiting_approval",
                "target_resource": "mock://workspace-isolation/atomic-prepare-collision",
                "args": {"operation": "bounded_collision_probe"},
                "result_summary": "Prepared action has not executed.",
                "prepare_action": True,
                "approval_id": tool_prepare_approval_id,
                "idempotency_key": tool_prepare_idempotency_key,
                "approval_reason": "The conflicting Approval must rollback the Tool Call and waiting state.",
            },
            token=token,
            workspace_header=workspace_a,
        )
        tool_prepare_post_status = status
        status, run_after_prepare_collision = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/runs/{run_id}",
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"run rollback read failed: {status} {run_after_prepare_collision}")
        status, task_after_prepare_collision = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/tasks/{task_a}",
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"task rollback read failed: {status} {task_after_prepare_collision}")
        before_run = run_before_prepare_collision.get("run") or {}
        after_run = run_after_prepare_collision.get("run") or {}
        before_task = task_before_prepare_collision.get("task") or {}
        after_task = task_after_prepare_collision.get("task") or {}
        before_tool_count = len(run_before_prepare_collision.get("tool_calls") or [])
        after_tools = run_after_prepare_collision.get("tool_calls") or []
        tool_call_persisted = any(item.get("tool_call_id") == tool_prepare_call_id for item in after_tools)
        tool_prepare_rolled_back = (
            tool_prepare_post_status == 409
            and not tool_call_persisted
            and len(after_tools) == before_tool_count
            and after_run.get("status") == before_run.get("status")
            and after_run.get("approval_required") == before_run.get("approval_required")
            and after_task.get("status") == before_task.get("status")
        )
        if not tool_prepare_rolled_back:
            focused_regression_failures.append(
                "Tool Call + Prepared Action approval conflict must return 409 and rollback Tool Call/run/task state "
                f"(post={tool_prepare_post_status}, tool_persisted={tool_call_persisted}, "
                f"run={before_run.get('status')}->{after_run.get('status')}, "
                f"task={before_task.get('status')}->{after_task.get('status')})"
            )

        require(not focused_regression_failures, "; ".join(focused_regression_failures))

        status, artifact_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/artifacts",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "artifact_id": artifact_collision_id,
                "title": "Workspace A overwrite attempt",
                "summary": "Must be rejected.",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 409 and artifact_collision.get("error") == "artifact_id_conflict",
            f"cross-workspace artifact ID collision should fail: {status} {artifact_collision}",
        )
        status, approval_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/approvals/request",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "approval_id": approval_collision_id,
                "reason": "Workspace A overwrite attempt",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 409 and approval_collision.get("error") == "approval_id_conflict",
            f"cross-workspace approval ID collision should fail: {status} {approval_collision}",
        )
        status, prepared_approval_collision = http_json(
            "POST",
            args.base_url,
            "/api/agent-gateway/prepared-actions",
            payload={
                "workspace_id": workspace_a,
                "run_id": run_id,
                "agent_id": agent_id,
                "requested_by_agent_id": agent_id,
                "action_id": prepared_action_collision_id,
                "approval_id": prepared_approval_collision_id,
                "action_type": "workspace.isolation.prepared_action_collision",
                "args": {"operation": "bounded_collision_probe"},
                "target_resource": "mock://workspace-isolation/collision",
                "risk_level": "high",
                "idempotency_key": f"workspace-prepared-collision-{stamp}",
                "reason": "Workspace A must not reuse Workspace B's approval authority.",
            },
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 409 and prepared_approval_collision.get("error") == "approval_id_conflict",
            f"Prepared Action approval ID collision should fail: {status} {prepared_approval_collision}",
        )
        status, missing_collision_action = http_json(
            "GET",
            args.base_url,
            f"/api/agent-gateway/prepared-actions/{prepared_action_collision_id}",
            token=token,
            workspace_header=workspace_a,
        )
        require(
            status == 404,
            f"failed Prepared Action approval collision persisted an action: {status} {missing_collision_action}",
        )

        status, artifacts_b = http_json(
            "GET",
            args.base_url,
            "/api/agent-gateway/artifacts",
            token=token_b,
            workspace_header=workspace_b,
            query={"run_id": run_b_id, "limit": 20},
        )
        require(status == 200, f"workspace B artifact readback failed: {status} {artifacts_b}")
        authoritative_artifact = next((
            item for item in (artifacts_b.get("artifacts") or [])
            if item.get("artifact_id") == artifact_collision_id
        ), {})
        require(
            authoritative_artifact.get("title") == "Workspace B authoritative artifact",
            f"workspace B artifact was overwritten: {authoritative_artifact}",
        )
        status, approvals_b = http_json(
            "GET",
            args.base_url,
            "/api/agent-gateway/approvals",
            token=token_b,
            workspace_header=workspace_b,
            query={"run_id": run_b_id, "limit": 20},
        )
        require(status == 200, f"workspace B approval readback failed: {status} {approvals_b}")
        authoritative_approval = next((
            item for item in (approvals_b.get("approvals") or [])
            if item.get("approval_id") == approval_collision_id
        ), {})
        require(
            authoritative_approval.get("reason") == "Workspace B authoritative approval",
            f"workspace B approval was overwritten: {authoritative_approval}",
        )
        for approval_id, expected_reason in (
            (plan_approval_collision_id, "Workspace B authoritative predictable plan approval"),
            (prepared_approval_collision_id, "Workspace B authoritative prepared action approval"),
        ):
            status, approval_readback = http_json(
                "GET",
                args.base_url,
                f"/api/agent-gateway/approvals/{approval_id}",
                token=token_b,
                workspace_header=workspace_b,
            )
            approval_row = approval_readback.get("approval") or {}
            require(status == 200, f"workspace B collision approval readback failed: {status} {approval_readback}")
            require(
                approval_row.get("run_id") == run_b_id
                and approval_row.get("task_id") == task_b
                and approval_row.get("requested_by_agent_id") == agent_id
                and approval_row.get("decision") == "pending"
                and approval_row.get("reason") == expected_reason,
                f"workspace B collision approval authority was mutated: {approval_row}",
            )

        status, heartbeat = http_json(
            "POST",
            args.base_url,
            f"/api/agent-gateway/runs/{run_id}/heartbeat",
            payload={"workspace_id": workspace_a, "status": "completed", "output_summary": "Workspace isolation smoke completed."},
            token=token,
            workspace_header=workspace_a,
        )
        require(status == 200, f"workspace A heartbeat failed: {status} {heartbeat}")

        result.update({
            "ok": True,
            "run_id": run_id,
            "pull_count": pulled.get("count"),
            "header_spoof_status": header_spoof.get("error"),
            "query_spoof_status": query_spoof.get("error"),
            "cross_claim_status": claim_b.get("error"),
            "cross_start_status": start_b.get("error"),
            "cross_write_statuses": cross_write_checks,
            "artifact_id_collision_status": artifact_collision.get("error"),
            "approval_id_collision_status": approval_collision.get("error"),
            "plan_approval_id_collision_status": plan_approval_collision.get("error"),
            "plan_collision_rolled_back": missing_collision_plan.get("error"),
            "prepared_action_approval_id_collision_status": prepared_approval_collision.get("error"),
            "prepared_action_collision_rolled_back": missing_collision_action.get("error"),
            "run_task_mismatch_plan_status": mismatch_post_status,
            "run_task_mismatch_plan_rolled_back": mismatch_plan_readback.get("error"),
            "plan_subject_collision_status": plan_subject_post_status,
            "plan_subject_collision_rolled_back": plan_subject_readback.get("error"),
            "tool_prepare_collision_status": tool_prepare_post_status,
            "tool_prepare_collision_rolled_back": tool_prepare_rolled_back,
            "run_b": run_b_id,
        })
        return 0
    except Exception as exc:
        result["error"] = str(exc)
        return 1
    finally:
        if token_id:
            status, revoked = http_json("POST", args.base_url, "/api/agent-gateway/enrollment/revoke", {"token_id": token_id})
            result["revocation"] = {"status": status, "revoked": revoked.get("revoked") if isinstance(revoked, dict) else None}
        if token_b_id:
            status, revoked = http_json("POST", args.base_url, "/api/agent-gateway/enrollment/revoke", {"token_id": token_b_id})
            result["revocation_b"] = {"status": status, "revoked": revoked.get("revoked") if isinstance(revoked, dict) else None}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
