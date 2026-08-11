"""Transactional projection of campaign facts into the canonical MIS ledgers.

This module intentionally imports ``server`` and the vertical SQLite repository
only inside the public operation.  The production server can therefore import
OpenCekura API code later without creating a module-import cycle.
"""

from __future__ import annotations

import itertools
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from open_cekura.campaigns.runner import CampaignExecution
from open_cekura.domain.enums import CampaignStatus, EvaluationStatus, RunFinalState
from open_cekura.domain.ids import stable_id


class MISBridgeError(RuntimeError):
    """Base failure for the authority-to-projection bridge."""


class CallerTransactionRequiredError(MISBridgeError):
    """The bridge never owns commits and requires an active caller transaction."""


class MISBridgeConflictError(MISBridgeError):
    """A stable authority ID is already bound to different facts."""


@dataclass(frozen=True, slots=True)
class PersistedCampaignMappings:
    """Stable MIS identifiers assigned to one vertical campaign graph."""

    campaign_id: str
    mis_agent_id: str
    mis_task_id: str
    mis_plan_id: str
    mis_run_ids: dict[str, str]
    mis_tool_call_ids: dict[str, str]
    mis_evaluation_ids: dict[str, str]
    mis_memory_ids: dict[str, str]


_SAVEPOINTS = itertools.count()
_HARNESS_NAME = "OpenCekura Reliability Harness"
_HARNESS_TOOLS = (
    "cancel_booking",
    "list_available_slots",
    "lookup_booking",
    "update_booking",
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|cookie(?:jar)?|session(?:_id)?|credentials?|"
    r"auth[_ -]?token|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
    r"raw[_ -]?prompt|hidden[_ -]?prompt|system[_ -]?prompt|"
    r"raw[_ -]?(?:model[_ -]?)?response)\s*[:=]\s*[^,;]+"
)


def persist_campaign_execution(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    *,
    workspace_id: str,
) -> PersistedCampaignMappings:
    """Persist a closed execution without committing the caller's transaction."""

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be sqlite3.Connection")
    if not isinstance(execution, CampaignExecution):
        raise TypeError("execution must be CampaignExecution")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id must be a non-empty string")
    if not conn.in_transaction:
        raise CallerTransactionRequiredError(
            "persist_campaign_execution requires an active transaction owned by its caller"
        )
    if conn.row_factory is not sqlite3.Row:
        raise TypeError("conn.row_factory must be sqlite3.Row")
    if execution.agent.workspace_id not in {None, workspace_id}:
        raise MISBridgeConflictError("campaign agent belongs to another workspace")

    # Lazy imports preserve the server/OpenCekura import boundary.
    import server as mis
    from open_cekura.storage.repository import (
        AuthorityMappingError,
        RepositoryConflictError,
        RepositoryError,
    )
    from open_cekura.storage.sqlite_repository import SQLiteRepository

    savepoint = f"open_cekura_mis_bridge_{next(_SAVEPOINTS)}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        result = _persist(
            conn,
            execution,
            workspace_id=workspace_id,
            mis=mis,
            repository_type=SQLiteRepository,
        )
    except (AuthorityMappingError, RepositoryConflictError, RepositoryError) as exc:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise MISBridgeConflictError(str(exc)) from exc
    except Exception:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise
    conn.execute(f"RELEASE {savepoint}")
    return result


def _persist(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    *,
    workspace_id: str,
    mis: Any,
    repository_type: type,
) -> PersistedCampaignMappings:
    repo = repository_type(conn, workspace_id=workspace_id)

    created_at = execution.campaign.created_at.isoformat()
    mis_agent_id = stable_id(
        "agtoc", workspace_id, "open-cekura-reliability-harness"
    )
    mis_task_id = stable_id("tskoc", workspace_id, execution.campaign.id)
    mis_plan_id = stable_id("planoc", mis_task_id, execution.campaign.id)
    _validate_preexisting_mappings(
        execution,
        mis_task_id=mis_task_id,
        mis_plan_id=mis_plan_id,
    )
    repo.initialize_schema()

    agent_row = {
        "agent_id": mis_agent_id,
        "name": _HARNESS_NAME,
        "role": "OpenCekura Reliability Lab authority bridge",
        "description": (
            "Stable MIS harness that owns OpenCekura campaign authority facts; "
            "agents under test remain in the reliability vertical projection."
        ),
        "runtime_type": "mock",
        "model_provider": "open_cekura",
        "model_name": None,
        "status": "idle",
        "permission_level": "reliability_test",
        "allowed_tools": _json(_HARNESS_TOOLS),
        "budget_limit_usd": 0.0,
        "owner_user_id": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
    _upsert_exact(
        conn,
        table="agents",
        key="agent_id",
        expected=agent_row,
        label="stable MIS agent",
        helper=mis.upsert_agent,
        actor_id="open-cekura-bridge",
        ignored={"created_at", "updated_at"},
    )

    task_row = {
        "task_id": mis_task_id,
        "workspace_id": workspace_id,
        "title": f"Reliability campaign: {execution.campaign.id}",
        "description": (
            "OpenCekura Reliability Lab deterministic campaign projection; "
            "raw transcript and model payloads are omitted from the MIS task ledger."
        ),
        "requester_id": None,
        "owner_agent_id": mis_agent_id,
        "collaborator_agent_ids": "[]",
        "status": _task_status(execution.campaign.status),
        "priority": "high",
        "due_date": None,
        "acceptance_criteria": (
            "Scenario -> Run -> ToolCall -> Evaluation -> Failure -> Regression "
            "facts retain stable MIS mappings."
        ),
        "risk_level": "low",
        "budget_limit_usd": 0.0,
        "created_at": created_at,
        "updated_at": created_at,
    }
    _upsert_exact(
        conn,
        table="tasks",
        key="task_id",
        expected=task_row,
        label="stable MIS task",
        helper=mis.upsert_task,
        actor_id="open-cekura-bridge",
    )

    _ensure_verified_plan(
        conn,
        mis=mis,
        plan_id=mis_plan_id,
        task_id=mis_task_id,
        agent_id=mis_agent_id,
        workspace_id=workspace_id,
        created_at=created_at,
    )

    repo.upsert_agent(
        _preserve_vertical_created_at(
            conn,
            execution.agent,
            table="reliability_agents",
            id_column="agent_id",
            workspace_id=workspace_id,
        )
    )
    repo.upsert_agent_version(
        _preserve_vertical_created_at(
            conn,
            execution.agent_version,
            table="reliability_agent_versions",
            id_column="agent_version_id",
            workspace_id=workspace_id,
        )
    )
    repo.upsert_scenario_suite(
        _preserve_vertical_created_at(
            conn,
            execution.scenario_suite,
            table="reliability_scenario_suites",
            id_column="suite_id",
            workspace_id=workspace_id,
        )
    )
    for record in execution.records:
        repo.upsert_persona(
            _preserve_vertical_created_at(
                conn,
                record.persona,
                table="reliability_personas",
                id_column="persona_id",
                workspace_id=workspace_id,
            )
        )
        repo.upsert_scenario(
            _preserve_vertical_created_at(
                conn,
                record.scenario,
                table="reliability_scenarios",
                id_column="scenario_id",
                workspace_id=workspace_id,
            ),
            contract=record.scenario_contract,
        )

    mapped_campaign = execution.campaign.model_copy(
        update={"mis_task_id": mis_task_id, "mis_plan_id": mis_plan_id}
    )
    repo.upsert_campaign(mapped_campaign)

    mis_run_ids: dict[str, str] = {}
    mis_tool_call_ids: dict[str, str] = {}
    mis_evaluation_ids: dict[str, str] = {}
    mis_memory_ids: dict[str, str] = {}

    for record in execution.records:
        simulation = record.simulation
        vertical_run = simulation.run
        mis_run_id = stable_id("runoc", mis_task_id, vertical_run.id)
        mis_run_ids[vertical_run.id] = mis_run_id
        run_row = {
            "run_id": mis_run_id,
            "workspace_id": workspace_id,
            "task_id": mis_task_id,
            "agent_id": mis_agent_id,
            "runtime_type": execution.agent_version.adapter_kind.value,
            "status": (
                "failed"
                if vertical_run.status is RunFinalState.ERROR
                else "completed"
            ),
            "started_at": simulation.started_at.isoformat(),
            "ended_at": simulation.finished_at.isoformat(),
            "duration_ms": simulation.duration_ms,
            "input_summary": f"Reliability scenario {record.scenario.id}",
            "output_summary": f"OpenCekura run final_state={vertical_run.status.value}",
            "model_provider": "open_cekura",
            "model_name": execution.agent_version.version,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "error_type": _run_error_type(simulation),
            "error_message": None,
            "trace_id": stable_id("traceoc", mis_run_id),
            "parent_run_id": None,
            "delegation_id": None,
            "approval_required": 0,
            "agent_plan_id": mis_plan_id,
            "plan_hash": conn.execute(
                "SELECT plan_hash FROM agent_plans WHERE plan_id=?", (mis_plan_id,)
            ).fetchone()[0],
            "created_at": vertical_run.created_at.isoformat(),
        }
        _upsert_exact(
            conn,
            table="runs",
            key="run_id",
            expected=run_row,
            label="stable MIS run",
            helper=mis.upsert_run,
            actor_id="open-cekura-bridge",
            audit_metadata={
                "campaign_id": execution.campaign.id,
                "scenario_id": record.scenario.id,
                "raw_transcript_omitted": True,
            },
        )

        mapped_calls = []
        for call in simulation.tool_calls:
            mis_tool_call_id = stable_id("tcloc", mis_run_id, call.id)
            mis_tool_call_ids[call.id] = mis_tool_call_id
            ended_at = call.created_at + timedelta(milliseconds=call.duration_ms)
            call_row = {
                "tool_call_id": mis_tool_call_id,
                "run_id": mis_run_id,
                "agent_id": mis_agent_id,
                "tool_name": call.name,
                "tool_version": "open-cekura-observation.v1",
                "tool_category": "custom",
                "normalized_args_json": _json(_safe_metadata(mis, call.arguments)),
                "target_resource": None,
                "risk_level": "medium" if call.is_mutation else "low",
                "status": "failed" if call.error else "completed",
                "result_summary": (
                    "OpenCekura observed tool error; raw error omitted."
                    if call.error
                    else f"Observed {call.name} completion; raw result omitted."
                ),
                "side_effect_id": (
                    stable_id("sideoc", mis_tool_call_id) if call.is_mutation else None
                ),
                "started_at": call.created_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "created_at": call.created_at.isoformat(),
            }
            _upsert_exact(
                conn,
                table="tool_calls",
                key="tool_call_id",
                expected=call_row,
                label="stable MIS tool call",
                helper=mis.upsert_tool_call,
                actor_id="open-cekura-bridge",
                audit_metadata={
                    "campaign_id": execution.campaign.id,
                    "vertical_tool_call_id": call.id,
                    "raw_result_omitted": True,
                },
            )
            mapped_calls.append(
                call.model_copy(update={"mis_tool_call_id": mis_tool_call_id})
            )

        mapped_evaluations = []
        for evaluation in record.evaluations:
            mis_evaluation_id = None
            if evaluation.status is not EvaluationStatus.SKIPPED:
                mis_evaluation_id = stable_id("evaloc", mis_run_id, evaluation.id)
                mis_evaluation_ids[evaluation.id] = mis_evaluation_id
                evaluation_row = _evaluation_row(
                    evaluation,
                    evaluation_id=mis_evaluation_id,
                    task_id=mis_task_id,
                    run_id=mis_run_id,
                    agent_id=mis_agent_id,
                    mis=mis,
                )
                _upsert_exact(
                    conn,
                    table="evaluations",
                    key="evaluation_id",
                    expected=evaluation_row,
                    label="stable MIS evaluation",
                    helper=mis.upsert_evaluation,
                    actor_id="open-cekura-bridge",
                )
            mapped_evaluations.append(
                evaluation.model_copy(update={"mis_evaluation_id": mis_evaluation_id})
            )

        mapped_run = vertical_run.model_copy(update={"mis_run_id": mis_run_id})
        repo.upsert_run_projection(
            mapped_run,
            turns=simulation.turns,
            tool_calls=tuple(mapped_calls),
            evaluations=tuple(mapped_evaluations),
        )

        for failure in record.failures:
            repo.upsert_failure(failure)
        for regression in record.regressions:
            mis_memory_id = stable_id("memoc", mis_task_id, regression.id)
            mis_memory_ids[regression.id] = mis_memory_id
            memory_row = _memory_row(
                regression,
                memory_id=mis_memory_id,
                workspace_id=workspace_id,
                task_id=mis_task_id,
                agent_id=mis_agent_id,
                source_run_id=mis_run_id,
                mis=mis,
            )
            _upsert_exact(
                conn,
                table="memories",
                key="memory_id",
                expected=memory_row,
                label="stable MIS failure-case memory",
                helper=mis.upsert_memory_candidate,
                actor_id="open-cekura-bridge",
            )
            _audit_exact(
                conn,
                mis=mis,
                actor_type="system",
                actor_id="open-cekura-bridge",
                action="open_cekura.regression_memory.propose",
                entity_type="memories",
                entity_id=mis_memory_id,
                before=None,
                after={"review_status": "candidate"},
                metadata={
                    "campaign_id": execution.campaign.id,
                    "regression_case_id": regression.id,
                    "basis": "deterministic_failure_evidence",
                    "authority_granted": False,
                    "raw_transcript_omitted": True,
                },
                audit_id=stable_id("audocmem", mis_memory_id, "propose"),
            )
            repo.upsert_regression(
                regression.model_copy(update={"mis_memory_id": mis_memory_id})
            )

    completion = {
        "campaign_id": execution.campaign.id,
        "workspace_id": workspace_id,
        "mis_task_id": mis_task_id,
        "mis_plan_id": mis_plan_id,
        "run_count": len(mis_run_ids),
        "tool_call_count": len(mis_tool_call_ids),
        "evaluation_count": len(mis_evaluation_ids),
        "memory_count": len(mis_memory_ids),
        "raw_transcript_omitted": True,
        "raw_prompt_omitted": True,
        "raw_response_omitted": True,
    }
    _audit_exact(
        conn,
        mis=mis,
        actor_type="system",
        actor_id="open-cekura-bridge",
        action="open_cekura.campaign.persist",
        entity_type="reliability_campaigns",
        entity_id=execution.campaign.id,
        before=None,
        after=completion,
        metadata=completion,
        audit_id=stable_id("audoc", workspace_id, execution.campaign.id),
    )
    return PersistedCampaignMappings(
        campaign_id=execution.campaign.id,
        mis_agent_id=mis_agent_id,
        mis_task_id=mis_task_id,
        mis_plan_id=mis_plan_id,
        mis_run_ids=mis_run_ids,
        mis_tool_call_ids=mis_tool_call_ids,
        mis_evaluation_ids=mis_evaluation_ids,
        mis_memory_ids=mis_memory_ids,
    )


def _validate_preexisting_mappings(
    execution: CampaignExecution,
    *,
    mis_task_id: str,
    mis_plan_id: str,
) -> None:
    _require_compatible_mapping(
        execution.campaign.mis_task_id,
        mis_task_id,
        label=f"campaign {execution.campaign.id} mis_task_id",
    )
    _require_compatible_mapping(
        execution.campaign.mis_plan_id,
        mis_plan_id,
        label=f"campaign {execution.campaign.id} mis_plan_id",
    )
    for record in execution.records:
        vertical_run = record.simulation.run
        mis_run_id = stable_id("runoc", mis_task_id, vertical_run.id)
        _require_compatible_mapping(
            vertical_run.mis_run_id,
            mis_run_id,
            label=f"run {vertical_run.id} mis_run_id",
        )
        for call in record.simulation.tool_calls:
            _require_compatible_mapping(
                call.mis_tool_call_id,
                stable_id("tcloc", mis_run_id, call.id),
                label=f"tool call {call.id} mis_tool_call_id",
            )
        for evaluation in record.evaluations:
            expected = (
                None
                if evaluation.status is EvaluationStatus.SKIPPED
                else stable_id("evaloc", mis_run_id, evaluation.id)
            )
            _require_compatible_mapping(
                evaluation.mis_evaluation_id,
                expected,
                label=f"evaluation {evaluation.id} mis_evaluation_id",
            )
        for regression in record.regressions:
            _require_compatible_mapping(
                regression.mis_memory_id,
                stable_id("memoc", mis_task_id, regression.id),
                label=f"regression {regression.id} mis_memory_id",
            )


def _require_compatible_mapping(
    actual: str | None,
    expected: str | None,
    *,
    label: str,
) -> None:
    if actual is not None and actual != expected:
        raise MISBridgeConflictError(
            f"pre-existing MIS mapping conflicts for {label}"
        )


def _run_error_type(simulation: Any) -> str | None:
    if simulation.timed_out:
        return "open_cekura.timeout"
    if simulation.adapter_error:
        return "open_cekura.adapter_error"
    if simulation.run.status is RunFinalState.ERROR:
        return "open_cekura.run_error"
    return None


def _ensure_verified_plan(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    plan_id: str,
    task_id: str,
    agent_id: str,
    workspace_id: str,
    created_at: str,
) -> None:
    plan = {
        "plan_id": plan_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "run_id": None,
        "agent_id": agent_id,
        "task_understanding": (
            "Persist an OpenCekura campaign into the existing AgentOps MIS "
            "authority ledgers and reliability projection."
        ),
        "referenced_specs_json": _json(["docs/open-cekura/ARCHITECTURE.md"]),
        "referenced_memories_json": _json(
            ["OpenCekura Reliability Lab campaign execution contract"]
        ),
        "referenced_bases_json": _json(["agentops_mis.db"]),
        "proposed_files_to_change_json": "[]",
        "risk_level": "low",
        "approval_required": 0,
        "execution_steps_json": _json(
            [
                "READ validated campaign facts",
                "PLAN stable MIS mappings",
                "RETRIEVE existing authority rows",
                "COMPARE stable facts and fail closed",
                "EXECUTE authority and projection writes",
                "VERIFY mappings and evaluator rubrics",
                "RECORD an idempotent Audit fact",
            ]
        ),
        "verification_plan": (
            "Verify the real MIS schema, foreign keys, stable mappings, and projection graph."
        ),
        "rollback_plan": (
            "Roll back the caller transaction when any authority or projection check fails."
        ),
        "status": "submitted",
        "plan_version": 1,
        "plan_hash": None,
        "verified_at": None,
        "verification_result_hash": None,
        "approval_id": None,
        "approved_by_user_id": None,
        "approved_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
    plan["plan_hash"] = mis.compute_agent_plan_hash(plan)
    existing = conn.execute(
        "SELECT * FROM agent_plans WHERE plan_id=?", (plan_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO agent_plans(
                plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
                referenced_specs_json,referenced_memories_json,referenced_bases_json,
                proposed_files_to_change_json,risk_level,approval_required,
                execution_steps_json,verification_plan,rollback_plan,status,
                plan_version,plan_hash,verified_at,verification_result_hash,
                approval_id,approved_by_user_id,approved_at,created_at,updated_at
            ) VALUES(
                :plan_id,:workspace_id,:task_id,:run_id,:agent_id,:task_understanding,
                :referenced_specs_json,:referenced_memories_json,:referenced_bases_json,
                :proposed_files_to_change_json,:risk_level,:approval_required,
                :execution_steps_json,:verification_plan,:rollback_plan,:status,
                :plan_version,:plan_hash,:verified_at,:verification_result_hash,
                :approval_id,:approved_by_user_id,:approved_at,:created_at,:updated_at
            )""",
            plan,
        )
        existing = conn.execute(
            "SELECT * FROM agent_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
    else:
        expected_contract = mis.agent_plan_contract(plan)
        existing_contract = mis.agent_plan_contract(existing)
        if existing_contract != expected_contract:
            raise MISBridgeConflictError(
                "stable MIS plan is already bound to different facts"
            )
        if existing["plan_hash"] != plan["plan_hash"]:
            raise MISBridgeConflictError("stable MIS plan hash conflicts with its facts")

    _validate_plan_run_start_authority(conn, mis=mis, plan=existing)

    _audit_exact(
        conn,
        mis=mis,
        actor_type="agent",
        actor_id=agent_id,
        action="agent_gateway.agent_plan_create",
        entity_type="agent_plans",
        entity_id=plan_id,
        before=None,
        after=plan,
        metadata={"raw_omitted": True, "plan_hash": plan["plan_hash"]},
        audit_id=stable_id("audocplan", plan_id, "create"),
    )

    verification = mis.verify_agent_plan_row(existing, conn)
    if not verification.get("pass"):
        failed = ",".join(
            str(item.get("id")) for item in verification.get("failed_checks") or []
        )
        raise MISBridgeConflictError(f"MIS plan verification failed: {failed}")
    expected_verification_hash = mis.agent_plan_verification_hash(
        plan_id, verification
    )
    if existing["verified_at"] is None:
        mis.persist_agent_plan_verification(conn, plan_id, verification)
    elif existing["verification_result_hash"] != expected_verification_hash:
        raise MISBridgeConflictError(
            "stable MIS plan verification result conflicts with current authority"
        )


def _validate_plan_run_start_authority(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    plan: sqlite3.Row,
) -> None:
    status = str(plan["status"])
    if status not in {"submitted", "approved"}:
        raise MISBridgeConflictError(
            f"stable MIS plan is not executable: status={status}"
        )

    approval_required = bool(plan["approval_required"])
    approval_id = plan["approval_id"]
    if approval_required and status != "approved":
        raise MISBridgeConflictError(
            "stable MIS plan cannot authorize run start without approval"
        )
    if status != "approved":
        if approval_id:
            raise MISBridgeConflictError(
                "submitted MIS plan has an unexpected approval authority binding"
            )
        return

    if not approval_id:
        raise MISBridgeConflictError(
            "approved MIS plan is missing approval authority"
        )
    approval = conn.execute(
        "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if approval is None or approval["decision"] != "approved":
        raise MISBridgeConflictError(
            "approved MIS plan lacks an approved approval authority decision"
        )
    if mis.validate_agent_plan_approval_binding(conn, plan, approval) is not None:
        raise MISBridgeConflictError(
            "approved MIS plan approval authority conflicts with its immutable binding"
        )


def _evaluation_row(
    evaluation: Any,
    *,
    evaluation_id: str,
    task_id: str,
    run_id: str,
    agent_id: str,
    mis: Any,
) -> dict[str, Any]:
    vertical_status = evaluation.status.value
    score = 0.0 if evaluation.status is EvaluationStatus.ERROR else evaluation.score
    if score is None:
        raise MISBridgeConflictError(
            f"non-skipped evaluation {evaluation.id} has no score"
        )
    rubric = _safe_metadata(mis, {
        "schema_version": "open_cekura.mis_evaluation.v1",
        "evaluator_id": evaluation.evaluator_id,
        "vertical_status": vertical_status,
        "threshold": evaluation.threshold,
        "reason_codes": list(evaluation.reason_codes),
        "evidence_refs": list(evaluation.evidence_refs),
    })
    return {
        "evaluation_id": evaluation_id,
        "task_id": task_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "evaluator_type": "rule",
        "score": score,
        "pass_fail": "pass" if evaluation.status is EvaluationStatus.PASS else "fail",
        "rubric_json": _json(rubric),
        "notes": f"OpenCekura {evaluation.evaluator_id}: {vertical_status}",
        "created_at": evaluation.created_at.isoformat(),
    }


def _memory_row(
    regression: Any,
    *,
    memory_id: str,
    workspace_id: str,
    task_id: str,
    agent_id: str,
    source_run_id: str,
    mis: Any,
) -> dict[str, Any]:
    canonical = _safe_metadata(
        mis,
        {
            "schema_version": "open_cekura.regression_memory.v1",
            "regression_case_id": regression.id,
            "original_failing_input": regression.original_input,
            "expected_state": regression.expected,
            "observed_state": regression.observed,
            "failure_reason": regression.reason_code,
            "source_run": source_run_id,
            "evaluator": regression.evaluator_id,
            "evidence_refs": regression.evidence_refs,
        }
    )
    timestamp = regression.created_at.isoformat()
    return {
        "memory_id": memory_id,
        "workspace_id": workspace_id,
        "scope": "task",
        "memory_type": "failure_case",
        "canonical_text": mis.redact_text(_json(canonical), 10_000),
        "source_type": "run_log",
        "source_ref": source_run_id,
        "project_id": "open-cekura-reliability-lab",
        "task_id": task_id,
        "agent_id": agent_id,
        "confidence": 1.0,
        "review_status": "candidate",
        "owner_user_id": None,
        "ttl_review_due_at": None,
        "supersedes_memory_id": None,
        "access_tags": _json(["open-cekura", "reliability-regression"]),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _upsert_exact(
    conn: sqlite3.Connection,
    *,
    table: str,
    key: str,
    expected: dict[str, Any],
    label: str,
    helper: Any,
    actor_id: str,
    audit_metadata: dict[str, Any] | None = None,
    ignored: set[str] | frozenset[str] = frozenset(),
) -> None:
    existing = conn.execute(
        f"SELECT * FROM {table} WHERE {key}=?", (expected[key],)
    ).fetchone()
    if existing is not None:
        mismatches = [
            field
            for field, expected_value in expected.items()
            if field not in ignored
            and field in existing.keys()
            and existing[field] != expected_value
        ]
        if mismatches:
            raise MISBridgeConflictError(
                f"{label} is already bound to different facts: {','.join(mismatches)}"
            )
    kwargs: dict[str, Any] = {"actor_id": actor_id}
    if audit_metadata is not None:
        kwargs["audit_metadata"] = audit_metadata
    outcome = helper(conn, dict(expected), **kwargs)
    if outcome not in {"created", "unchanged"}:
        raise MISBridgeConflictError(f"{label} unexpectedly changed existing facts")


def _audit_exact(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    actor_type: str,
    actor_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before: Any,
    after: Any,
    metadata: dict[str, Any],
    audit_id: str,
) -> str:
    expected = {
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before_hash": mis.stable_hash(before) if before is not None else None,
        "after_hash": mis.stable_hash(after) if after is not None else None,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    existing = conn.execute(
        "SELECT * FROM audit_logs WHERE audit_id=?", (audit_id,)
    ).fetchone()
    if existing is not None:
        mismatches = [
            field for field, value in expected.items() if existing[field] != value
        ]
        if mismatches:
            raise MISBridgeConflictError(
                "stable MIS audit is already bound to different facts: "
                + ",".join(mismatches)
            )
        return audit_id
    return mis.audit(
        conn,
        actor_type,
        actor_id,
        action,
        entity_type,
        entity_id,
        before,
        after,
        metadata,
        audit_id=audit_id,
    )


def _safe_metadata(mis: Any, value: Any) -> Any:
    """Bound and recursively redact metadata before it enters core MIS rows."""

    return _redact_metadata_tree(mis, mis.safe_json_metadata(value))


def _redact_metadata_tree(mis: Any, value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _metadata_key_is_sensitive(text_key):
                redacted[text_key] = "[REDACTED]"
            else:
                redacted[text_key] = _redact_metadata_tree(mis, item)
        return redacted
    if isinstance(value, list):
        return [_redact_metadata_tree(mis, item) for item in value]
    if isinstance(value, str):
        bounded = mis.redact_text(value, 240)
        return _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", bounded)
    return value


def _metadata_key_is_sensitive(key: str) -> bool:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", snake.lower()).strip("_")
    if normalized in {
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "secrets",
        "token",
        "tokens",
        "api_key",
        "api_keys",
        "password",
        "cookie",
        "cookies",
        "cookie_jar",
        "credentials",
        "session",
        "raw_prompt",
        "hidden_prompt",
        "system_prompt",
        "developer_prompt",
        "raw_response",
        "raw_model_response",
        "messages",
        "session_id",
        "token_id",
        "credentials_id",
    }:
        return True
    if any(part in normalized for part in ("cookie", "credential", "password")):
        return True
    if normalized.endswith(("_token", "_tokens", "_secret", "_secrets")):
        return True
    return False


def _preserve_vertical_created_at(
    conn: sqlite3.Connection,
    model: Any,
    *,
    table: str,
    id_column: str,
    workspace_id: str,
) -> Any:
    existing = conn.execute(
        f"SELECT created_at FROM {table} WHERE workspace_id=? AND {id_column}=?",
        (workspace_id, model.id),
    ).fetchone()
    if existing is None:
        return model
    created_at_text = str(existing["created_at"])
    if created_at_text.endswith("Z"):
        created_at_text = created_at_text[:-1] + "+00:00"
    created_at = datetime.fromisoformat(created_at_text)
    return model.model_copy(update={"created_at": created_at})


def _task_status(status: CampaignStatus) -> str:
    return {
        CampaignStatus.PENDING: "planned",
        CampaignStatus.RUNNING: "running",
        CampaignStatus.COMPLETED: "completed",
        CampaignStatus.ERROR: "failed",
    }[status]


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "CallerTransactionRequiredError",
    "MISBridgeConflictError",
    "MISBridgeError",
    "PersistedCampaignMappings",
    "persist_campaign_execution",
]
