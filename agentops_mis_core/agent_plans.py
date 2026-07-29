"""Pure Agent Plan response helpers."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

AGENT_PLAN_METHOD_STEPS = ("READ", "PLAN", "RETRIEVE", "COMPARE", "EXECUTE", "VERIFY", "RECORD")
AGENT_PLAN_QUALITY_VERSION = "agent_plan_quality_v1"


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_json_list_field(row: Any, field: str) -> list:
    try:
        value = row[field]
    except Exception:
        value = "[]"
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        parsed = []
    return parsed if isinstance(parsed, list) else []


def row_field(row: Any | None, field: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[field]
    except Exception:
        return row.get(field, default) if hasattr(row, "get") else default


def agent_plan_contract(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": row_field(row, "workspace_id"),
        "task_id": row_field(row, "task_id"),
        "run_id": row_field(row, "run_id"),
        "agent_id": row_field(row, "agent_id"),
        "task_understanding": row_field(row, "task_understanding") or "",
        "referenced_specs": load_json_list_field(row, "referenced_specs_json"),
        "referenced_memories": load_json_list_field(row, "referenced_memories_json"),
        "referenced_bases": load_json_list_field(row, "referenced_bases_json"),
        "proposed_files_to_change": load_json_list_field(row, "proposed_files_to_change_json"),
        "risk_level": row_field(row, "risk_level"),
        "approval_required": bool(row_field(row, "approval_required")),
        "execution_steps": load_json_list_field(row, "execution_steps_json"),
        "verification_plan": row_field(row, "verification_plan") or "",
        "rollback_plan": row_field(row, "rollback_plan") or "",
        "plan_version": int(row_field(row, "plan_version", 1) or 1),
    }


def compute_agent_plan_hash(row: Any) -> str:
    return _stable_hash(agent_plan_contract(row))


def agent_plan_verification_hash(plan_id: str, verification: dict[str, Any]) -> str:
    quality = verification.get("quality") or {}
    return _stable_hash({
        "plan_id": plan_id,
        "plan_hash": verification.get("plan_hash"),
        "pass": verification.get("pass"),
        "failed_checks": [check.get("id") for check in verification.get("failed_checks") or []],
        "summary": verification.get("summary") or {},
        "quality": {
            "version": quality.get("version"),
            "score": quality.get("score"),
            "status": quality.get("status"),
            "failed_rubric_ids": quality.get("failed_rubric_ids") or [],
        },
    })


def plan_ref_is_safe_relative_path(ref: str) -> bool:
    value = str(ref or "").strip()
    if not value or value.startswith(("http://", "https://", "file://", "~")):
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def plan_ref_path(ref: str, repo_root: Path | str) -> Path | None:
    if not plan_ref_is_safe_relative_path(ref):
        return None
    root = Path(repo_root)
    try:
        resolved = (root / ref).resolve(strict=False)
        root_resolved = root.resolve(strict=True)
    except Exception:
        return None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def resolve_agent_plan_spec_authority(refs: list, repo_root: Path | str) -> dict[str, Any]:
    readable: list[dict[str, Any]] = []
    missing: list[str] = []
    unsafe: list[str] = []
    root = Path(repo_root)
    root_resolved = root.resolve(strict=True)
    for item in refs:
        ref = str(item or "").strip()
        if not ref:
            continue
        path = plan_ref_path(ref, root)
        if not path:
            unsafe.append(ref)
            continue
        if path.exists() and path.is_file():
            readable.append({"ref": ref, "path": str(path.relative_to(root_resolved)), "bytes": path.stat().st_size})
        else:
            missing.append(ref)
    return {
        "ok": bool(readable) and not missing and not unsafe,
        "readable": readable,
        "missing": missing,
        "unsafe": unsafe,
        "message": "Referenced specs must be readable files inside the repository.",
        "token_omitted": True,
    }


def resolve_agent_plan_file_scope(refs: list, repo_root: Path | str) -> dict[str, Any]:
    scoped: list[dict[str, Any]] = []
    unsafe: list[str] = []
    root = Path(repo_root)
    root_resolved = root.resolve(strict=True)
    for item in refs:
        ref = str(item or "").strip()
        if not ref:
            continue
        path = plan_ref_path(ref, root)
        if not path:
            unsafe.append(ref)
            continue
        scoped.append({"ref": ref, "path": str(path.relative_to(root_resolved)), "exists": path.exists()})
    return {
        "ok": not unsafe,
        "scoped": scoped,
        "unsafe": unsafe,
        "message": "Proposed file changes must stay inside the repository and use relative paths.",
        "token_omitted": True,
    }


def agent_plan_method_block_coverage(steps: list[Any]) -> dict[str, Any]:
    present: list[str] = []
    positions: dict[str, int] = {}
    for index, step in enumerate(steps):
        normalized = re.sub(r"[^A-Z]+", " ", str(step or "").upper())
        tokens = set(normalized.split())
        for required in AGENT_PLAN_METHOD_STEPS:
            if required in tokens and required not in present:
                present.append(required)
                positions[required] = index
    missing = [step for step in AGENT_PLAN_METHOD_STEPS if step not in present]
    ordered_positions = [positions[step] for step in AGENT_PLAN_METHOD_STEPS if step in positions]
    in_order = ordered_positions == sorted(ordered_positions)
    return {
        "required_steps": list(AGENT_PLAN_METHOD_STEPS),
        "present_steps": present,
        "missing_steps": missing,
        "coverage_ratio": round(len(present) / len(AGENT_PLAN_METHOD_STEPS), 3),
        "in_order": in_order,
        "token_omitted": True,
    }


def _quality_item(item_id: str, score: int, max_score: int, ok: bool, message: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "score": int(score),
        "max_score": int(max_score),
        "ok": bool(ok),
        "message": message,
    }


def build_agent_plan_quality(
    row: Any,
    *,
    verification_summary: dict[str, Any] | None = None,
    hard_checks_pass: bool | None = None,
) -> dict[str, Any]:
    summary = verification_summary or {}
    steps = load_json_list_field(row, "execution_steps_json")
    files = load_json_list_field(row, "proposed_files_to_change_json")
    risk = str(row_field(row, "risk_level") or "").strip().lower()
    approval_required = bool(row_field(row, "approval_required"))
    method = agent_plan_method_block_coverage(steps)

    coverage_score = int(round(24 * float(method["coverage_ratio"])))
    method_score = min(30, coverage_score + (6 if method["in_order"] and not method["missing_steps"] else 0))

    spec_ok = int(summary.get("readable_spec_refs") or 0) > 0
    memory_ok = int(summary.get("approved_memory_refs") or 0) > 0 or int(summary.get("knowledge_context_refs") or 0) > 0
    base_ok = int(summary.get("resolved_base_refs") or 0) > 0
    authority_clean = not any(
        int(summary.get(key) or 0) > 0
        for key in ("missing_memory_refs", "non_authoritative_memory_refs")
    )
    authority_score = (8 if spec_ok else 0) + (8 if memory_ok else 0) + (7 if base_ok else 0) + (2 if authority_clean else 0)

    verification_text = str(row_field(row, "verification_plan") or "").strip()
    rollback_text = str(row_field(row, "rollback_plan") or "").strip()
    scoped_files = int(summary.get("scoped_file_refs") or 0)
    file_scope_ok = risk == "low" or (bool(files) and scoped_files == len(files))
    readiness_score = (
        (5 if len(steps) >= len(AGENT_PLAN_METHOD_STEPS) else 0)
        + (5 if len(verification_text) >= 20 else 0)
        + (5 if len(rollback_text) >= 20 else 0)
        + (5 if file_scope_ok else 0)
    )

    risk_valid = risk in {"low", "medium", "high", "critical"}
    approval_posture_ok = risk not in {"high", "critical"} or approval_required
    governance_score = (5 if risk_valid else 0) + (5 if approval_posture_ok else 0) + (5 if hard_checks_pass else 0)

    plan_hash_present = bool(row_field(row, "plan_hash") or compute_agent_plan_hash(row))
    file_refs_safe = scoped_files == len(files)
    hygiene_score = (4 if plan_hash_present else 0) + 3 + (3 if file_refs_safe else 0)

    rubric = [
        _quality_item(
            "method_block_coverage",
            method_score,
            30,
            not method["missing_steps"] and method["in_order"],
            "Execution steps cover READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD in order.",
        ),
        _quality_item(
            "authority_diversity",
            authority_score,
            25,
            spec_ok and memory_ok and base_ok and authority_clean,
            "Plan cites readable specs, approved/knowledge memory context, and base constraints without non-authoritative refs.",
        ),
        _quality_item(
            "execution_readiness",
            readiness_score,
            20,
            readiness_score >= 15,
            "Plan has enough steps, verification, rollback, and scoped file intent for operators to assess execution.",
        ),
        _quality_item(
            "governance_posture",
            governance_score,
            15,
            risk_valid and approval_posture_ok and bool(hard_checks_pass),
            "Risk level is valid, high/critical work requires approval, and hard method-block checks pass.",
        ),
        _quality_item(
            "evidence_hygiene",
            hygiene_score,
            10,
            plan_hash_present and file_refs_safe,
            "Quality projection uses hashes/counts/flags only and keeps raw prompts, responses and token values omitted.",
        ),
    ]
    score = sum(item["score"] for item in rubric)
    failed = [item["id"] for item in rubric if not item["ok"]]
    status = "ready" if score >= 85 and not failed else "attention" if score >= 60 else "blocked"
    return {
        "version": AGENT_PLAN_QUALITY_VERSION,
        "score": score,
        "max_score": 100,
        "status": status,
        "rubric": rubric,
        "failed_rubric_ids": failed,
        "method_block": method,
        "summary": {
            "referenced_specs": int(summary.get("referenced_specs") or 0),
            "readable_spec_refs": int(summary.get("readable_spec_refs") or 0),
            "referenced_memories": int(summary.get("referenced_memories") or 0),
            "approved_memory_refs": int(summary.get("approved_memory_refs") or 0),
            "knowledge_context_refs": int(summary.get("knowledge_context_refs") or 0),
            "referenced_bases": int(summary.get("referenced_bases") or 0),
            "resolved_base_refs": int(summary.get("resolved_base_refs") or 0),
            "proposed_files_to_change": int(summary.get("proposed_files_to_change") or 0),
            "scoped_file_refs": scoped_files,
            "execution_steps": len(steps),
            "risk_level": risk,
            "approval_required": approval_required,
        },
        "raw_plan_body_omitted": True,
        "raw_prompt_omitted": True,
        "raw_response_omitted": True,
        "token_omitted": True,
    }


def build_agent_plan_verification(
    row: Any,
    *,
    spec_authority: dict[str, Any],
    memory_authority: dict[str, Any],
    base_authority: dict[str, Any],
    file_scope: dict[str, Any],
) -> dict[str, Any]:
    specs = load_json_list_field(row, "referenced_specs_json")
    memories = load_json_list_field(row, "referenced_memories_json")
    bases = load_json_list_field(row, "referenced_bases_json")
    files = load_json_list_field(row, "proposed_files_to_change_json")
    steps = load_json_list_field(row, "execution_steps_json")
    risk = row_field(row, "risk_level")
    approval_required = bool(row_field(row, "approval_required"))
    checks = [
        {"id": "read_specs", "ok": bool(specs) and bool(spec_authority.get("ok")), "message": "Plan references readable specs or workflow docs.", "details": spec_authority},
        {"id": "retrieve_memory", "ok": bool(memories), "message": "Plan references memory, knowledge, or failure-case context."},
        {"id": "memory_authority", "ok": bool(memory_authority.get("ok")), "message": "Referenced memory ids exist and are approved before acting as authority.", "details": memory_authority},
        {"id": "compare_bases", "ok": bool(bases) and bool(base_authority.get("ok")), "message": "Plan references existing base constraints or reusable foundations.", "details": base_authority},
        {"id": "execution_steps", "ok": len(steps) >= 3, "message": "Plan includes concrete execution steps."},
        {"id": "verification_plan", "ok": bool(str(row_field(row, "verification_plan") or "").strip()), "message": "Plan includes verification path."},
        {"id": "rollback_plan", "ok": bool(str(row_field(row, "rollback_plan") or "").strip()), "message": "Plan includes rollback path."},
        {"id": "risk_gate", "ok": risk not in {"high", "critical"} or approval_required, "message": "High/critical risk requires approval."},
        {"id": "file_scope", "ok": (bool(files) or risk == "low") and bool(file_scope.get("ok")), "message": "Non-low work names proposed files or surfaces inside the repository.", "details": file_scope},
    ]
    failed = [check for check in checks if not check["ok"]]
    summary = {
        "referenced_specs": len(specs),
        "readable_spec_refs": len(spec_authority.get("readable") or []),
        "referenced_memories": len(memories),
        "approved_memory_refs": len(memory_authority.get("approved") or []),
        "non_authoritative_memory_refs": len(memory_authority.get("non_authoritative") or []),
        "missing_memory_refs": len(memory_authority.get("missing") or []),
        "knowledge_context_refs": len(memory_authority.get("knowledge_context") or []),
        "referenced_bases": len(bases),
        "resolved_base_refs": len((base_authority.get("table_bases") or []) + (base_authority.get("file_bases") or []) + (base_authority.get("virtual_bases") or [])),
        "proposed_files_to_change": len(files),
        "scoped_file_refs": len(file_scope.get("scoped") or []),
        "execution_steps": len(steps),
        "risk_level": risk,
        "approval_required": approval_required,
    }
    quality = build_agent_plan_quality(row, verification_summary=summary, hard_checks_pass=not failed)
    return {
        "pass": not failed,
        "plan_hash": row_field(row, "plan_hash") or compute_agent_plan_hash(row),
        "checks": checks,
        "failed_checks": failed,
        "summary": {**summary, "quality_score": quality["score"], "quality_status": quality["status"]},
        "quality": quality,
        "token_omitted": True,
    }


def build_agent_plan_pending_approval(
    row: Any,
    *,
    approval_id: Any,
    created_at: str,
    expires_at: str,
    approver_user_id: str = "usr_founder",
) -> dict[str, Any]:
    plan_id = row_field(row, "plan_id")
    reason = (
        f"Agent Plan approval required before execution: {plan_id} "
        f"risk={row_field(row, 'risk_level')} hash={(row_field(row, 'plan_hash') or '')[:12]}"
    )
    return {
        "approval_id": approval_id,
        "task_id": row_field(row, "task_id"),
        "run_id": row_field(row, "run_id"),
        "tool_call_id": None,
        "requested_by_agent_id": row_field(row, "agent_id"),
        "approver_user_id": approver_user_id,
        "decision": "pending",
        "reason": reason[:500],
        "subject_type": "agent_plan",
        "subject_id": plan_id,
        "subject_hash": row_field(row, "plan_hash"),
        "expires_at": expires_at,
        "created_at": created_at,
        "decided_at": None,
    }


def build_agent_plan_approval_run(
    row: Any,
    *,
    run_id: str,
    trace_id: str,
    delegation_id: str,
    created_at: str,
) -> dict[str, Any]:
    plan_id = row_field(row, "plan_id")
    return {
        "run_id": run_id,
        "workspace_id": row_field(row, "workspace_id") or "local-demo",
        "task_id": row_field(row, "task_id"),
        "agent_id": row_field(row, "agent_id"),
        "runtime_type": "governance",
        "status": "waiting_approval",
        "started_at": created_at,
        "ended_at": None,
        "duration_ms": None,
        "input_summary": f"Governance anchor for Agent Plan approval {plan_id}.",
        "output_summary": None,
        "model_provider": "agentops",
        "model_name": "agent-plan-approval-gate",
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "error_type": None,
        "error_message": None,
        "trace_id": trace_id,
        "parent_run_id": None,
        "delegation_id": delegation_id,
        "approval_required": 1,
        "agent_plan_id": plan_id,
        "plan_hash": row_field(row, "plan_hash"),
        "created_at": created_at,
    }


def build_agent_plan_not_transitionable_response(*, plan_id: Any, status: Any, message: str) -> dict[str, Any]:
    return {
        "error": "agent_plan_not_transitionable",
        "message": message,
        "plan_id": plan_id,
        "status": status,
        "token_omitted": True,
    }


def build_agent_plan_not_approvable_response(*, plan_id: Any, status: Any, message: str) -> dict[str, Any]:
    return {
        "error": "agent_plan_not_approvable",
        "message": message,
        "plan_id": plan_id,
        "status": status,
        "token_omitted": True,
    }


def build_agent_plan_verification_failed_response(*, plan_id: Any, failed_checks: list, message: str) -> dict[str, Any]:
    return {
        "error": "agent_plan_verification_failed",
        "message": message,
        "plan_id": plan_id,
        "failed_checks": failed_checks or [],
        "token_omitted": True,
    }


def build_agent_plan_approval_anchor_required_response(*, plan_id: Any = None) -> dict[str, Any]:
    payload = {
        "error": "agent_plan_approval_anchor_required",
        "message": "Approval-required Agent Plans must be attached to a task or run so the approval ledger has task/run anchors.",
        "token_omitted": True,
    }
    if plan_id is not None:
        payload["plan_id"] = plan_id
    return payload


def build_agent_plan_status_transition_required_response(*, requested_status: Any) -> dict[str, Any]:
    return {
        "error": "plan_status_transition_required",
        "message": "Agent-created plans may only start as draft or submitted. Human/admin approval must be a separate transition.",
        "requested_status": requested_status,
        "allowed_create_statuses": ["draft", "submitted"],
        "token_omitted": True,
    }


def build_agent_plan_bound_approval_forbidden_response(*, auth_ctx: dict[str, Any]) -> dict[str, Any]:
    context = auth_ctx or {}
    return {
        "error": "agent_plan_human_approval_required",
        "message": "Bound Agent Gateway tokens and sessions cannot approve or reject Agent Plans.",
        "auth_mode": context.get("mode"),
        "agent_id": context.get("agent_id"),
        "token_omitted": True,
    }


def build_agent_plan_approval_decision_response(*, approval: Any, agent_plan_decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval": dict(approval) if approval is not None else None,
        "agent_plan": agent_plan_decision["agent_plan"],
        "verification": agent_plan_decision["verification"],
        "verification_result_hash": agent_plan_decision["verification_result_hash"],
        "token_omitted": True,
    }


def build_agent_plan_run_required_response(*, task_id: Any, agent_id: Any) -> dict[str, Any]:
    return {
        "error": "agent_plan_required",
        "message": "Agent Gateway run_start requires a submitted, verified Agent Plan for this task and agent.",
        "task_id": task_id,
        "agent_id": agent_id,
        "hint": "Create and verify a plan first: agentops agent-plan create ... && agentops agent-plan verify --plan-id <plan_id>",
        "token_omitted": True,
    }


def build_agent_plan_run_task_mismatch_response(*, plan_id: Any) -> dict[str, Any]:
    return {
        "error": "agent_plan_task_mismatch",
        "message": "Agent Plan task_id must match run_start task_id.",
        "plan_id": plan_id,
        "token_omitted": True,
    }


def build_agent_plan_run_agent_mismatch_response(*, plan_id: Any) -> dict[str, Any]:
    return {
        "error": "agent_plan_agent_mismatch",
        "message": "Agent Plan agent_id must match run_start agent_id.",
        "plan_id": plan_id,
        "token_omitted": True,
    }


def build_agent_plan_run_not_executable_response(*, plan_id: Any, status: Any) -> dict[str, Any]:
    return {
        "error": "agent_plan_not_executable",
        "message": "Agent Plan must be submitted or approved before run_start.",
        "plan_id": plan_id,
        "status": status,
        "token_omitted": True,
    }


def build_agent_plan_run_approval_required_response(*, plan: Any, approval: Any = None) -> dict[str, Any]:
    return {
        "error": "agent_plan_approval_required",
        "message": "This Agent Plan requires human/admin/policy approval before run_start.",
        "plan_id": row_field(plan, "plan_id"),
        "status": row_field(plan, "status"),
        "approval_id": row_field(plan, "approval_id"),
        "approval_decision": row_field(approval, "decision") if approval is not None else None,
        "token_omitted": True,
    }


def build_agent_plan_run_hash_mismatch_response(*, plan_id: Any, stored_plan_hash: Any, current_plan_hash: Any) -> dict[str, Any]:
    return {
        "error": "agent_plan_hash_mismatch",
        "message": "Agent Plan content no longer matches its stored plan_hash.",
        "plan_id": plan_id,
        "stored_plan_hash": stored_plan_hash,
        "current_plan_hash": current_plan_hash,
        "token_omitted": True,
    }


def build_run_start_rebind_forbidden_response(
    existing_run: Any,
    *,
    run_id: Any,
    requested_agent_plan_id: Any,
    requested_plan_hash: Any,
    mismatches: list[str],
) -> dict[str, Any]:
    return {
        "error": "run_start_rebind_forbidden",
        "message": "Existing runs cannot be rebound to a different workspace, task, agent, agent_plan_id, or plan_hash.",
        "run_id": run_id,
        "existing_agent_plan_id": row_field(existing_run, "agent_plan_id"),
        "existing_plan_hash": row_field(existing_run, "plan_hash"),
        "requested_agent_plan_id": requested_agent_plan_id,
        "requested_plan_hash": requested_plan_hash,
        "mismatches": mismatches,
        "token_omitted": True,
    }


def compare_run_start_binding(
    existing_run: Any,
    *,
    workspace_id: Any,
    task_id: Any,
    agent_id: Any,
    plan_binding: dict[str, Any],
) -> dict[str, Any]:
    expected = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "agent_plan_id": plan_binding["plan_id"],
        "plan_hash": plan_binding["plan_hash"],
    }
    actual = {key: row_field(existing_run, key) for key in expected}
    return {
        "expected": expected,
        "actual": actual,
        "mismatches": [key for key, value in expected.items() if actual.get(key) != value],
        "token_omitted": True,
    }


def build_run_start_success_response(*, run: Any, outcome: Any, plan_binding: dict[str, Any]) -> dict[str, Any]:
    verification = plan_binding.get("verification") or {}
    return {
        "run": dict(run),
        "outcome": outcome,
        "agent_plan": {
            "plan_id": plan_binding["plan_id"],
            "plan_hash": plan_binding["plan_hash"],
            "verification_result_hash": plan_binding.get("verification_result_hash"),
            "verification_pass": bool(verification.get("pass")),
        },
    }
