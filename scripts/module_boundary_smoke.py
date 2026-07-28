#!/usr/bin/env python3
"""Verify the first P1-05 strangler module boundary stays in place."""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_core.approval_wall import (
    RISKY_TOOLS,
    approval_wall_recommended_actions,
    build_high_risk_toolcall_prepared_action_required_response,
    build_prepared_action_approval_decision_response,
    build_prepared_action_blocked_response,
    build_prepared_action_agent_forbidden_response,
    build_prepared_action_get_response,
    build_prepared_action_get_not_found_response,
    build_prepared_action_hash_mismatch_response,
    build_prepared_action_prepare_response_fields,
    build_prepared_action_provider_result_fields,
    build_prepared_action_provider_resume_request,
    build_prepared_action_resume_blocked_response,
    build_prepared_action_resume_success_response,
    build_prepared_action_waiting_response,
    prepared_action_checkpoint,
    prepared_action_gate,
    prepared_action_hash,
    prepared_action_hash_payload,
    prepared_action_id_from_request,
    prepared_action_public,
    prepared_action_resume_gate_error,
    prepared_action_route_access_error,
    prepared_action_stored_args,
    prepared_action_waiting_next_action,
    runtime_probe_blocked_payload,
    runtime_probe_prepared_action_required_payload,
    tool_call_has_external_side_effect_intent,
)
from agentops_mis_core.agent_plans import (
    agent_plan_contract,
    agent_plan_verification_hash,
    build_agent_plan_approval_anchor_required_response,
    build_agent_plan_approval_decision_response,
    build_agent_plan_approval_run,
    build_agent_plan_bound_approval_forbidden_response,
    build_agent_plan_not_approvable_response,
    build_agent_plan_not_transitionable_response,
    build_agent_plan_pending_approval,
    build_agent_plan_run_agent_mismatch_response,
    build_agent_plan_run_approval_required_response,
    build_agent_plan_run_hash_mismatch_response,
    build_agent_plan_run_not_executable_response,
    build_agent_plan_run_required_response,
    build_agent_plan_run_task_mismatch_response,
    build_agent_plan_status_transition_required_response,
    build_agent_plan_verification,
    build_agent_plan_verification_failed_response,
    build_run_start_rebind_forbidden_response,
    build_run_start_success_response,
    compare_run_start_binding,
    compute_agent_plan_hash,
    load_json_list_field,
    plan_ref_is_safe_relative_path,
    plan_ref_path,
    row_field,
    resolve_agent_plan_file_scope,
    resolve_agent_plan_spec_authority,
)
from agentops_mis_core.gateway_runs import (
    build_run_heartbeat_update,
    run_heartbeat_terminal_task_status,
)
from agentops_mis_core.evaluation_cases import (
    evaluation_case_candidate_public,
    evaluation_case_run_public,
)
from agentops_mis_core.read_model_cache import ReadModelCache
from agentops_mis_core.commander_work_packages import (
    build_commander_team_board,
    build_commander_work_packages_readback,
    build_commander_project_board_gates,
    commander_project_board_next_actions,
    commander_project_board_status,
    commander_work_package_next_action,
    commander_work_package_status,
)
from agentops_mis_core.operator_command_center import (
    build_command_center_commander_gaps,
    build_command_center_project_rows,
    build_command_center_stale_worker_refs,
    command_center_status,
)
from agentops_mis_core.operator_evidence import (
    build_operator_run_memory_review,
    operator_evidence_report_status,
    operator_evidence_report_summary,
    operator_run_evidence_status,
)
from agentops_mis_core.operator_start_check import (
    compact_start_check_loop_driver_entry,
    compact_start_check_launch_brief,
    compact_start_check_local_run_path,
    operator_agent_loop_packet,
    operator_local_loop_admission_packet,
    operator_start_check_acceptance_packet,
    operator_start_check_gate,
)
from agentops_mis_core.operator_loop_control import (
    operator_loop_control_gate,
    operator_loop_control_summary_from_handoff,
)
from agentops_mis_core.operator_receipts import (
    operator_action_evaluation_public,
    operator_action_receipt_public,
    operator_control_readback_public,
    operator_receipt_requires_control_readback,
)
from agentops_mis_core.worker_fleet import (
    build_worker_remote_fleet_summary,
    build_worker_fleet_hygiene_plan,
    build_worker_fleet_view,
    build_worker_status_payload,
    public_remote_session,
    public_remote_worker,
    public_worker_enrollment_error,
    public_worker_revoked_enrollment,
    public_worker_stale_enrollment,
    worker_fleet_health,
)
from agentops_mis_core.workflow_jobs import (
    build_workflow_job_recovery_work_order,
    workflow_jobs_list_response,
    workflow_job_mark_failed_response,
    workflow_job_not_active_response,
    workflow_job_parse_iso_datetime,
    workflow_job_public,
    workflow_job_stuck_projection,
)
from agentops_mis_runtime.capabilities import (
    SCHEMA_VERSION,
    runtime_connector_capability_manifest,
    runtime_connector_for_adapter,
    runtime_connector_public_row,
)
from agentops_mis_runtime.connectors import (
    runtime_connector_refresh_rows,
    runtime_connector_rows,
    upsert_runtime_connector,
)
from agentops_mis_runtime.trust import (
    apply_runtime_connector_trust_update,
    normalize_trust_status,
    runtime_connector_trust,
)


SERVER = ROOT / "server.py"
CAPABILITIES = ROOT / "agentops_mis_runtime" / "capabilities.py"
CONNECTORS = ROOT / "agentops_mis_runtime" / "connectors.py"
TRUST = ROOT / "agentops_mis_runtime" / "trust.py"
READ_MODEL_CACHE = ROOT / "agentops_mis_core" / "read_model_cache.py"
APPROVAL_WALL = ROOT / "agentops_mis_core" / "approval_wall.py"
AGENT_PLANS = ROOT / "agentops_mis_core" / "agent_plans.py"
GATEWAY_RUNS = ROOT / "agentops_mis_core" / "gateway_runs.py"
EVALUATION_CASES = ROOT / "agentops_mis_core" / "evaluation_cases.py"
COMMANDER_WORK_PACKAGES = ROOT / "agentops_mis_core" / "commander_work_packages.py"
OPERATOR_COMMAND_CENTER = ROOT / "agentops_mis_core" / "operator_command_center.py"
OPERATOR_EVIDENCE = ROOT / "agentops_mis_core" / "operator_evidence.py"
OPERATOR_START_CHECK = ROOT / "agentops_mis_core" / "operator_start_check.py"
OPERATOR_LOOP_CONTROL = ROOT / "agentops_mis_core" / "operator_loop_control.py"
OPERATOR_RECEIPTS = ROOT / "agentops_mis_core" / "operator_receipts.py"
WORKER_FLEET = ROOT / "agentops_mis_core" / "worker_fleet.py"
WORKFLOW_JOBS = ROOT / "agentops_mis_core" / "workflow_jobs.py"
BACKLOG = ROOT / "docs" / "project" / "BACKLOG.md"
PLAN = ROOT / "docs" / "MODULE_BOUNDARY_PLAN.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / "scripts" / "release_evidence_packet_smoke.py"

FORBIDDEN_RUNTIME_MODULE_IMPORTS = {
    "sqlite3",
    "subprocess",
    "http.server",
    "urllib.request",
}
REQUIRED_MANIFEST_KEYS = {
    "schema_version",
    "connector_id",
    "provider",
    "connector_type",
    "adapter",
    "observation_level",
    "risk_floor",
    "commercial_readiness",
    "capabilities",
    "boundaries",
    "governance",
    "manifest_hash",
}
EXTRACTED_HELPERS = {
    "runtime_connector_capability_manifest",
    "runtime_connector_for_adapter",
    "runtime_connector_public_row",
}
EXTRACTED_CONNECTOR_HELPERS = {
    "hermes_runtime_config",
    "agnesfallback_config",
    "agnesfallback_cli_command",
    "runtime_connector_refresh_rows",
    "runtime_connector_rows",
    "upsert_runtime_connector",
}
SERVER_CAPABILITY_IMPORTS = {
    "runtime_connector_for_adapter",
    "runtime_connector_public_row",
}
EXTRACTED_TRUST_HELPERS = {
    "runtime_connector_trust",
}
SERVER_TRUST_IMPORTS = {
    "apply_runtime_connector_trust_update",
    "runtime_connector_trust",
}
READ_MODEL_CACHE_FORBIDDEN_SERVER_MARKERS = {
    "READ_MODEL_CACHE_LOCK",
    "entry = READ_MODEL_CACHE.get",
    "READ_MODEL_CACHE[key]",
    '"status": "hit"',
}
EXTRACTED_WORKER_FLEET_HELPERS = {
    "build_worker_remote_fleet_summary",
    "build_worker_fleet_hygiene_plan",
    "build_worker_fleet_view",
    "build_worker_status_payload",
    "public_remote_session",
    "public_remote_worker",
    "public_worker_enrollment_error",
    "public_worker_revoked_enrollment",
    "public_worker_stale_enrollment",
    "worker_fleet_health",
}
SERVER_WORKER_FLEET_IMPORTS = {
    "build_worker_remote_fleet_summary",
    "build_worker_fleet_hygiene_plan",
    "build_worker_fleet_view",
    "build_worker_status_payload",
    "public_worker_enrollment_error",
    "public_worker_revoked_enrollment",
}
EXTRACTED_WORKFLOW_JOB_HELPERS = {
    "build_workflow_job_recovery_work_order",
    "workflow_jobs_list_response",
    "workflow_job_mark_failed_response",
    "workflow_job_not_active_response",
    "workflow_job_parse_iso_datetime",
    "workflow_job_public",
    "workflow_job_stuck_projection",
}
SERVER_WORKFLOW_JOB_IMPORTS = {
    "build_workflow_job_recovery_work_order",
    "workflow_jobs_list_response",
    "workflow_job_mark_failed_response",
    "workflow_job_not_active_response",
    "workflow_job_public",
    "workflow_job_stuck_projection",
}
EXTRACTED_COMMANDER_WORK_PACKAGE_HELPERS = {
    "build_commander_lane_packet",
    "build_commander_lane_packets_readback",
    "build_commander_work_packages_readback",
    "build_commander_project_board_gates",
    "commander_work_package_blocked_reason",
    "commander_work_package_phase",
    "commander_project_board_next_actions",
    "commander_project_board_status",
    "commander_work_package_next_action",
    "commander_work_package_status",
}
SERVER_COMMANDER_WORK_PACKAGE_IMPORTS = {
    "build_commander_lane_packet",
    "build_commander_lane_packets_readback",
    "build_commander_work_packages_readback",
    "build_commander_project_board_gates",
    "commander_project_board_next_actions",
    "commander_project_board_status",
    "commander_work_package_next_action",
    "commander_work_package_status",
}
EXTRACTED_OPERATOR_COMMAND_CENTER_HELPERS = {
    "build_command_center_commander_gaps",
    "build_command_center_project_rows",
    "build_command_center_stale_worker_refs",
    "command_center_status",
}
SERVER_OPERATOR_COMMAND_CENTER_IMPORTS = {
    "build_command_center_commander_gaps",
    "build_command_center_project_rows",
    "build_command_center_stale_worker_refs",
    "command_center_status",
}
EXTRACTED_OPERATOR_EVIDENCE_HELPERS = {
    "build_operator_run_memory_review",
    "operator_evidence_report_status",
    "operator_evidence_report_summary",
    "operator_run_evidence_status",
}
SERVER_OPERATOR_EVIDENCE_IMPORTS = {
    "build_operator_run_memory_review",
    "operator_evidence_report_status",
    "operator_evidence_report_summary",
    "operator_run_evidence_status",
}
EXTRACTED_OPERATOR_START_CHECK_HELPERS = {
    "compact_start_check_loop_driver_entry",
    "compact_start_check_launch_brief",
    "compact_start_check_local_run_path",
    "operator_agent_loop_packet",
    "operator_local_loop_admission_packet",
    "operator_start_check_acceptance_packet",
    "operator_start_check_gate",
}
SERVER_OPERATOR_START_CHECK_IMPORTS = {
    "compact_start_check_loop_driver_entry",
    "compact_start_check_launch_brief",
    "compact_start_check_local_run_path",
    "operator_agent_loop_packet",
    "operator_local_loop_admission_packet",
    "operator_start_check_acceptance_packet",
    "operator_start_check_gate",
}
EXTRACTED_OPERATOR_LOOP_CONTROL_HELPERS = {
    "operator_loop_control_gate",
    "operator_loop_control_summary_from_handoff",
}
SERVER_OPERATOR_LOOP_CONTROL_IMPORTS = {
    "operator_loop_control_gate",
    "operator_loop_control_summary_from_handoff",
}
EXTRACTED_OPERATOR_RECEIPT_HELPERS = {
    "operator_action_evaluation_public",
    "operator_action_receipt_public",
    "operator_control_readback_public",
    "operator_receipt_requires_control_readback",
}
SERVER_OPERATOR_RECEIPT_IMPORTS = {
    "operator_action_evaluation_public",
    "operator_action_receipt_public",
    "operator_control_readback_public",
    "operator_receipt_requires_control_readback",
}
EXTRACTED_APPROVAL_WALL_HELPERS = {
    "approval_wall_recommended_actions",
    "build_high_risk_toolcall_prepared_action_required_response",
    "build_prepared_action_approval_decision_response",
    "build_prepared_action_agent_forbidden_response",
    "build_prepared_action_blocked_response",
    "build_prepared_action_get_response",
    "build_prepared_action_get_not_found_response",
    "build_prepared_action_hash_mismatch_response",
    "build_prepared_action_prepare_response_fields",
    "build_prepared_action_provider_result_fields",
    "build_prepared_action_provider_resume_request",
    "build_prepared_action_resume_blocked_response",
    "build_prepared_action_resume_success_response",
    "build_prepared_action_waiting_response",
    "prepared_action_checkpoint",
    "prepared_action_gate",
    "prepared_action_hash",
    "prepared_action_hash_payload",
    "prepared_action_id_from_request",
    "prepared_action_public",
    "prepared_action_resume_gate_error",
    "prepared_action_route_access_error",
    "prepared_action_stored_args",
    "prepared_action_waiting_next_action",
    "runtime_probe_blocked_payload",
    "runtime_probe_prepared_action_required_payload",
    "tool_call_has_external_side_effect_intent",
}
SERVER_APPROVAL_WALL_IMPORTS = {
    "RISKY_TOOLS",
    "approval_wall_recommended_actions",
    "build_high_risk_toolcall_prepared_action_required_response",
    "build_prepared_action_approval_decision_response",
    "build_prepared_action_blocked_response",
    "build_prepared_action_get_response",
    "build_prepared_action_hash_mismatch_response",
    "build_prepared_action_prepare_response_fields",
    "build_prepared_action_provider_result_fields",
    "build_prepared_action_provider_resume_request",
    "build_prepared_action_resume_blocked_response",
    "build_prepared_action_resume_success_response",
    "build_prepared_action_waiting_response",
    "prepared_action_checkpoint",
    "prepared_action_gate",
    "prepared_action_hash",
    "prepared_action_id_from_request",
    "prepared_action_public",
    "prepared_action_resume_gate_error",
    "prepared_action_route_access_error",
    "prepared_action_stored_args",
    "runtime_probe_blocked_payload",
    "runtime_probe_prepared_action_required_payload",
    "tool_call_has_external_side_effect_intent",
}
EXTRACTED_AGENT_PLAN_HELPERS = {
    "agent_plan_contract",
    "agent_plan_verification_hash",
    "build_agent_plan_approval_anchor_required_response",
    "build_agent_plan_approval_decision_response",
    "build_agent_plan_approval_run",
    "build_agent_plan_bound_approval_forbidden_response",
    "build_agent_plan_not_approvable_response",
    "build_agent_plan_not_transitionable_response",
    "build_agent_plan_pending_approval",
    "build_agent_plan_run_agent_mismatch_response",
    "build_agent_plan_run_approval_required_response",
    "build_agent_plan_run_hash_mismatch_response",
    "build_agent_plan_run_not_executable_response",
    "build_agent_plan_run_required_response",
    "build_agent_plan_run_task_mismatch_response",
    "build_agent_plan_status_transition_required_response",
    "build_agent_plan_verification",
    "build_agent_plan_verification_failed_response",
    "build_run_start_rebind_forbidden_response",
    "build_run_start_success_response",
    "compare_run_start_binding",
    "compute_agent_plan_hash",
    "load_json_list_field",
    "plan_ref_is_safe_relative_path",
    "plan_ref_path",
    "row_field",
    "resolve_agent_plan_file_scope",
    "resolve_agent_plan_spec_authority",
}
SERVER_AGENT_PLAN_IMPORTS = {
    "agent_plan_contract",
    "agent_plan_verification_hash",
    "build_agent_plan_approval_anchor_required_response",
    "build_agent_plan_approval_decision_response",
    "build_agent_plan_approval_run",
    "build_agent_plan_bound_approval_forbidden_response",
    "build_agent_plan_not_approvable_response",
    "build_agent_plan_not_transitionable_response",
    "build_agent_plan_pending_approval",
    "build_agent_plan_run_agent_mismatch_response",
    "build_agent_plan_run_approval_required_response",
    "build_agent_plan_run_hash_mismatch_response",
    "build_agent_plan_run_not_executable_response",
    "build_agent_plan_run_required_response",
    "build_agent_plan_run_task_mismatch_response",
    "build_agent_plan_status_transition_required_response",
    "build_agent_plan_verification",
    "build_agent_plan_verification_failed_response",
    "build_run_start_rebind_forbidden_response",
    "build_run_start_success_response",
    "compare_run_start_binding",
    "compute_agent_plan_hash",
    "load_json_list_field",
    "plan_ref_path",
    "row_field",
    "resolve_agent_plan_file_scope",
    "resolve_agent_plan_spec_authority",
}
EXTRACTED_GATEWAY_RUN_HELPERS = {
    "build_run_heartbeat_update",
    "run_heartbeat_terminal_task_status",
}
SERVER_GATEWAY_RUN_IMPORTS = {
    "build_run_heartbeat_update",
    "run_heartbeat_terminal_task_status",
}
EXTRACTED_EVALUATION_CASE_HELPERS = {
    "evaluation_case_candidate_public",
    "evaluation_case_run_public",
}
SERVER_EVALUATION_CASE_IMPORTS = {
    "evaluation_case_candidate_public",
    "evaluation_case_run_public",
}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def imported_symbol_sources(path: Path, symbols: set[str]) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sources = {symbol: set() for symbol in symbols}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name in sources:
                    sources[alias.name].add(node.module)
    return sources


def main() -> int:
    failures: list[str] = []
    server_text = SERVER.read_text(encoding="utf-8")
    approval_wall_text = APPROVAL_WALL.read_text(encoding="utf-8") if APPROVAL_WALL.exists() else ""
    read_model_cache_text = READ_MODEL_CACHE.read_text(encoding="utf-8") if READ_MODEL_CACHE.exists() else ""
    backlog_text = BACKLOG.read_text(encoding="utf-8")
    plan_text = PLAN.read_text(encoding="utf-8") if PLAN.exists() else ""
    ci_text = CI.read_text(encoding="utf-8")
    release_text = RELEASE.read_text(encoding="utf-8")

    require(CAPABILITIES.exists(), "runtime capability module missing", failures)
    require(CONNECTORS.exists(), "runtime connector registry module missing", failures)
    require(TRUST.exists(), "runtime connector trust module missing", failures)
    require(READ_MODEL_CACHE.exists(), "read model cache core module missing", failures)
    require(APPROVAL_WALL.exists(), "approval wall core module missing", failures)
    require(AGENT_PLANS.exists(), "agent plans core module missing", failures)
    require(GATEWAY_RUNS.exists(), "gateway runs core module missing", failures)
    require(EVALUATION_CASES.exists(), "evaluation cases core module missing", failures)
    require(COMMANDER_WORK_PACKAGES.exists(), "commander work packages core module missing", failures)
    require(OPERATOR_COMMAND_CENTER.exists(), "operator command center core module missing", failures)
    require(OPERATOR_EVIDENCE.exists(), "operator evidence core module missing", failures)
    require(OPERATOR_START_CHECK.exists(), "operator start-check core module missing", failures)
    require(OPERATOR_LOOP_CONTROL.exists(), "operator loop-control core module missing", failures)
    require(OPERATOR_RECEIPTS.exists(), "operator receipts core module missing", failures)
    require(WORKER_FLEET.exists(), "worker fleet core module missing", failures)
    require(WORKFLOW_JOBS.exists(), "workflow jobs core module missing", failures)
    require("from agentops_mis_core.approval_wall import" in server_text, "server.py must import approval wall core module", failures)
    require("from agentops_mis_core.agent_plans import" in server_text, "server.py must import agent plans core module", failures)
    require("from agentops_mis_core.gateway_runs import" in server_text, "server.py must import gateway runs core module", failures)
    require("from agentops_mis_core.evaluation_cases import" in server_text, "server.py must import evaluation cases core module", failures)
    require("from agentops_mis_core.read_model_cache import ReadModelCache" in server_text, "server.py must import read model cache core module", failures)
    require("from agentops_mis_core.commander_work_packages import" in server_text, "server.py must import commander work packages core module", failures)
    require("from agentops_mis_core.operator_command_center import" in server_text, "server.py must import operator command center core module", failures)
    require("from agentops_mis_core.operator_evidence import" in server_text, "server.py must import operator evidence core module", failures)
    require("from agentops_mis_core.operator_start_check import" in server_text, "server.py must import operator start-check core module", failures)
    require("from agentops_mis_core.operator_loop_control import" in server_text, "server.py must import operator loop-control core module", failures)
    require("from agentops_mis_core.operator_receipts import" in server_text, "server.py must import operator receipts core module", failures)
    require("from agentops_mis_core.worker_fleet import" in server_text, "server.py must import worker fleet core module", failures)
    require("from agentops_mis_core.workflow_jobs import" in server_text, "server.py must import workflow jobs core module", failures)
    require("from agentops_mis_runtime.capabilities import" in server_text, "server.py must import runtime capability module", failures)
    require("from agentops_mis_runtime.connectors import" in server_text, "server.py must import runtime connector registry module", failures)
    require("from agentops_mis_runtime.trust import" in server_text, "server.py must import runtime connector trust module", failures)
    server_functions = function_names(SERVER)
    approval_wall_functions = function_names(APPROVAL_WALL) if APPROVAL_WALL.exists() else set()
    agent_plan_functions = function_names(AGENT_PLANS) if AGENT_PLANS.exists() else set()
    gateway_run_functions = function_names(GATEWAY_RUNS) if GATEWAY_RUNS.exists() else set()
    evaluation_case_functions = function_names(EVALUATION_CASES) if EVALUATION_CASES.exists() else set()
    commander_work_package_functions = function_names(COMMANDER_WORK_PACKAGES) if COMMANDER_WORK_PACKAGES.exists() else set()
    operator_command_center_functions = function_names(OPERATOR_COMMAND_CENTER) if OPERATOR_COMMAND_CENTER.exists() else set()
    operator_evidence_functions = function_names(OPERATOR_EVIDENCE) if OPERATOR_EVIDENCE.exists() else set()
    operator_start_check_functions = function_names(OPERATOR_START_CHECK) if OPERATOR_START_CHECK.exists() else set()
    operator_loop_control_functions = function_names(OPERATOR_LOOP_CONTROL) if OPERATOR_LOOP_CONTROL.exists() else set()
    operator_receipt_functions = function_names(OPERATOR_RECEIPTS) if OPERATOR_RECEIPTS.exists() else set()
    worker_fleet_functions = function_names(WORKER_FLEET) if WORKER_FLEET.exists() else set()
    workflow_job_functions = function_names(WORKFLOW_JOBS) if WORKFLOW_JOBS.exists() else set()
    for helper in sorted(EXTRACTED_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
    for helper in sorted(EXTRACTED_CONNECTOR_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
    for helper in sorted(EXTRACTED_TRUST_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
    for helper in sorted(EXTRACTED_WORKER_FLEET_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in worker_fleet_functions, f"worker fleet module missing {helper}", failures)
    for helper in sorted(EXTRACTED_WORKFLOW_JOB_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in workflow_job_functions, f"workflow jobs module missing {helper}", failures)
    for helper in sorted(EXTRACTED_COMMANDER_WORK_PACKAGE_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in commander_work_package_functions, f"commander work packages module missing {helper}", failures)
    for helper in sorted(EXTRACTED_OPERATOR_COMMAND_CENTER_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in operator_command_center_functions, f"operator command center module missing {helper}", failures)
    for helper in sorted(EXTRACTED_OPERATOR_EVIDENCE_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in operator_evidence_functions, f"operator evidence module missing {helper}", failures)
    for helper in sorted(EXTRACTED_OPERATOR_START_CHECK_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in operator_start_check_functions, f"operator start-check module missing {helper}", failures)
    for helper in sorted(EXTRACTED_OPERATOR_LOOP_CONTROL_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in operator_loop_control_functions, f"operator loop-control module missing {helper}", failures)
    for helper in sorted(EXTRACTED_OPERATOR_RECEIPT_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in operator_receipt_functions, f"operator receipts module missing {helper}", failures)
    for helper in sorted(EXTRACTED_APPROVAL_WALL_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in approval_wall_functions, f"approval wall module missing {helper}", failures)
    require("RISKY_TOOLS =" not in server_text, "server.py still defines RISKY_TOOLS", failures)
    require("RISKY_TOOLS =" in approval_wall_text, "approval wall module missing RISKY_TOOLS", failures)
    for helper in sorted(EXTRACTED_AGENT_PLAN_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in agent_plan_functions, f"agent plans module missing {helper}", failures)
    for helper in sorted(EXTRACTED_GATEWAY_RUN_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in gateway_run_functions, f"gateway runs module missing {helper}", failures)
    for helper in sorted(EXTRACTED_EVALUATION_CASE_HELPERS):
        require(helper not in server_functions, f"server.py still defines {helper}", failures)
        require(helper in evaluation_case_functions, f"evaluation cases module missing {helper}", failures)
    require("worker_adapter_readiness" in server_functions, "worker_adapter_readiness must remain server-owned for runtime probing", failures)
    require("worker_adapter_readiness" not in worker_fleet_functions, "worker fleet module must not own runtime adapter probing", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_CAPABILITY_IMPORTS).items():
        require(sources == {"agentops_mis_runtime.capabilities"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, EXTRACTED_CONNECTOR_HELPERS).items():
        require(sources == {"agentops_mis_runtime.connectors"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_TRUST_IMPORTS).items():
        require(sources == {"agentops_mis_runtime.trust"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_WORKER_FLEET_IMPORTS).items():
        require(sources == {"agentops_mis_core.worker_fleet"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_WORKFLOW_JOB_IMPORTS).items():
        require(sources == {"agentops_mis_core.workflow_jobs"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_COMMANDER_WORK_PACKAGE_IMPORTS).items():
        require(sources == {"agentops_mis_core.commander_work_packages"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_OPERATOR_COMMAND_CENTER_IMPORTS).items():
        require(sources == {"agentops_mis_core.operator_command_center"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_OPERATOR_EVIDENCE_IMPORTS).items():
        require(sources == {"agentops_mis_core.operator_evidence"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_OPERATOR_START_CHECK_IMPORTS).items():
        require(sources == {"agentops_mis_core.operator_start_check"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_OPERATOR_LOOP_CONTROL_IMPORTS).items():
        require(sources == {"agentops_mis_core.operator_loop_control"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_OPERATOR_RECEIPT_IMPORTS).items():
        require(sources == {"agentops_mis_core.operator_receipts"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_APPROVAL_WALL_IMPORTS).items():
        require(sources == {"agentops_mis_core.approval_wall"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_AGENT_PLAN_IMPORTS).items():
        require(sources == {"agentops_mis_core.agent_plans"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_GATEWAY_RUN_IMPORTS).items():
        require(sources == {"agentops_mis_core.gateway_runs"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)
    for helper, sources in imported_symbol_sources(SERVER, SERVER_EVALUATION_CASE_IMPORTS).items():
        require(sources == {"agentops_mis_core.evaluation_cases"}, f"{helper} imported from wrong or multiple modules: {sorted(sources)}", failures)

    imports = imported_modules(CAPABILITIES)
    connector_imports = imported_modules(CONNECTORS) if CONNECTORS.exists() else set()
    trust_imports = imported_modules(TRUST) if TRUST.exists() else set()
    read_model_cache_imports = imported_modules(READ_MODEL_CACHE) if READ_MODEL_CACHE.exists() else set()
    approval_wall_imports = imported_modules(APPROVAL_WALL) if APPROVAL_WALL.exists() else set()
    agent_plan_imports = imported_modules(AGENT_PLANS) if AGENT_PLANS.exists() else set()
    gateway_run_imports = imported_modules(GATEWAY_RUNS) if GATEWAY_RUNS.exists() else set()
    evaluation_case_imports = imported_modules(EVALUATION_CASES) if EVALUATION_CASES.exists() else set()
    commander_work_package_imports = imported_modules(COMMANDER_WORK_PACKAGES) if COMMANDER_WORK_PACKAGES.exists() else set()
    operator_command_center_imports = imported_modules(OPERATOR_COMMAND_CENTER) if OPERATOR_COMMAND_CENTER.exists() else set()
    operator_evidence_imports = imported_modules(OPERATOR_EVIDENCE) if OPERATOR_EVIDENCE.exists() else set()
    operator_start_check_imports = imported_modules(OPERATOR_START_CHECK) if OPERATOR_START_CHECK.exists() else set()
    operator_loop_control_imports = imported_modules(OPERATOR_LOOP_CONTROL) if OPERATOR_LOOP_CONTROL.exists() else set()
    operator_receipt_imports = imported_modules(OPERATOR_RECEIPTS) if OPERATOR_RECEIPTS.exists() else set()
    worker_fleet_imports = imported_modules(WORKER_FLEET) if WORKER_FLEET.exists() else set()
    workflow_job_imports = imported_modules(WORKFLOW_JOBS) if WORKFLOW_JOBS.exists() else set()
    forbidden = sorted(module for module in imports if module in FORBIDDEN_RUNTIME_MODULE_IMPORTS)
    require(not forbidden, f"runtime capability module imports forbidden app/runtime dependencies: {forbidden}", failures)
    require("server" not in imports, "runtime capability module must not import server module", failures)
    connector_forbidden = sorted(module for module in connector_imports if module in {"subprocess", "http.server", "urllib.request"})
    require(not connector_forbidden, f"runtime connector module imports forbidden execution/server dependencies: {connector_forbidden}", failures)
    require("server" not in connector_imports, "runtime connector module must not import server module", failures)
    trust_forbidden = sorted(module for module in trust_imports if module in {"subprocess", "http.server", "urllib.request"})
    require(not trust_forbidden, f"runtime trust module imports forbidden execution/server dependencies: {trust_forbidden}", failures)
    require("server" not in trust_imports, "runtime trust module must not import server module", failures)
    cache_forbidden = sorted(module for module in read_model_cache_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not cache_forbidden, f"read model cache module imports forbidden app/runtime dependencies: {cache_forbidden}", failures)
    require("server" not in read_model_cache_imports, "read model cache module must not import server module", failures)
    approval_wall_forbidden = sorted(module for module in approval_wall_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not approval_wall_forbidden, f"approval wall module imports forbidden app/runtime dependencies: {approval_wall_forbidden}", failures)
    require("server" not in approval_wall_imports, "approval wall module must not import server module", failures)
    agent_plan_forbidden = sorted(module for module in agent_plan_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not agent_plan_forbidden, f"agent plans module imports forbidden app/runtime dependencies: {agent_plan_forbidden}", failures)
    require("server" not in agent_plan_imports, "agent plans module must not import server module", failures)
    gateway_run_forbidden = sorted(module for module in gateway_run_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not gateway_run_forbidden, f"gateway runs module imports forbidden app/runtime dependencies: {gateway_run_forbidden}", failures)
    require("server" not in gateway_run_imports, "gateway runs module must not import server module", failures)
    evaluation_case_forbidden = sorted(module for module in evaluation_case_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not evaluation_case_forbidden, f"evaluation cases module imports forbidden app/runtime dependencies: {evaluation_case_forbidden}", failures)
    require("server" not in evaluation_case_imports, "evaluation cases module must not import server module", failures)
    commander_work_package_forbidden = sorted(module for module in commander_work_package_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not commander_work_package_forbidden, f"commander work packages module imports forbidden app/runtime dependencies: {commander_work_package_forbidden}", failures)
    require("server" not in commander_work_package_imports, "commander work packages module must not import server module", failures)
    operator_command_center_forbidden = sorted(module for module in operator_command_center_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not operator_command_center_forbidden, f"operator command center module imports forbidden app/runtime dependencies: {operator_command_center_forbidden}", failures)
    require("server" not in operator_command_center_imports, "operator command center module must not import server module", failures)
    operator_evidence_forbidden = sorted(module for module in operator_evidence_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not operator_evidence_forbidden, f"operator evidence module imports forbidden app/runtime dependencies: {operator_evidence_forbidden}", failures)
    require("server" not in operator_evidence_imports, "operator evidence module must not import server module", failures)
    operator_start_check_forbidden = sorted(module for module in operator_start_check_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not operator_start_check_forbidden, f"operator start-check module imports forbidden app/runtime dependencies: {operator_start_check_forbidden}", failures)
    require("server" not in operator_start_check_imports, "operator start-check module must not import server module", failures)
    operator_loop_control_forbidden = sorted(module for module in operator_loop_control_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not operator_loop_control_forbidden, f"operator loop-control module imports forbidden app/runtime dependencies: {operator_loop_control_forbidden}", failures)
    require("server" not in operator_loop_control_imports, "operator loop-control module must not import server module", failures)
    operator_receipt_forbidden = sorted(module for module in operator_receipt_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not operator_receipt_forbidden, f"operator receipts module imports forbidden app/runtime dependencies: {operator_receipt_forbidden}", failures)
    require("server" not in operator_receipt_imports, "operator receipts module must not import server module", failures)
    worker_fleet_forbidden = sorted(module for module in worker_fleet_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not worker_fleet_forbidden, f"worker fleet module imports forbidden app/runtime dependencies: {worker_fleet_forbidden}", failures)
    require("server" not in worker_fleet_imports, "worker fleet module must not import server module", failures)
    workflow_job_forbidden = sorted(module for module in workflow_job_imports if module in {"sqlite3", "subprocess", "http.server", "urllib.request"})
    require(not workflow_job_forbidden, f"workflow jobs module imports forbidden app/runtime dependencies: {workflow_job_forbidden}", failures)
    require("server" not in workflow_job_imports, "workflow jobs module must not import server module", failures)
    require('"rtc_hermes_default_gateway"' not in server_text[server_text.find("def refresh_runtime_connectors"):server_text.find("def run_hermes_probe")], "server.py refresh_runtime_connectors still owns connector-specific refresh policy", failures)
    for marker in sorted(READ_MODEL_CACHE_FORBIDDEN_SERVER_MARKERS):
        require(marker not in server_text, f"server.py still contains read-model cache implementation marker: {marker}", failures)
    require('"status": "hit"' in read_model_cache_text, "read model cache module missing hit metadata", failures)

    manifest = runtime_connector_capability_manifest(
        "rtc_openclaw_local",
        "openclaw",
        "local_cli",
        repo_root=ROOT,
    )
    require(REQUIRED_MANIFEST_KEYS.issubset(manifest), f"manifest missing keys: {sorted(REQUIRED_MANIFEST_KEYS - set(manifest))}", failures)
    require(manifest.get("schema_version") == SCHEMA_VERSION, "manifest schema mismatch", failures)
    require(manifest.get("boundaries", {}).get("workdir") == str(ROOT), "repo_root boundary was not injected", failures)
    require(runtime_connector_for_adapter("openclaw") == "rtc_openclaw_local", "adapter mapping failed", failures)
    require(runtime_connector_for_adapter("codex") == "rtc_codex_local", "Codex adapter mapping failed", failures)
    public = runtime_connector_public_row({
        "runtime_connector_id": "rtc_openclaw_local",
        "capability_manifest_json": json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        "capability_policy_hash": manifest["manifest_hash"],
    })
    require(public.get("capability_manifest", {}).get("manifest_hash") == manifest["manifest_hash"], "public row did not parse manifest", failures)
    require(public.get("token_omitted") is True and public.get("raw_prompt_omitted") is True, "public row omission proof missing", failures)
    connector_rows = runtime_connector_rows()
    connector_ids = {row.get("runtime_connector_id") for row in connector_rows}
    require({"rtc_agent_gateway_local", "rtc_codex_local", "rtc_openclaw_local", "rtc_hermes_default_gateway", "rtc_agnesfallback_cli", "rtc_agnesfallback_openai_api"}.issubset(connector_ids), f"runtime connector rows missing expected IDs: {sorted(connector_ids)}", failures)
    require(all(row.get("capability_manifest_json") and row.get("capability_policy_hash") for row in connector_rows), "runtime connector rows missing manifest/hash", failures)
    refreshed_rows = runtime_connector_refresh_rows({
        "default_gateway": {
            "api_server_listening": False,
            "last_error": "Hermes API gateway is not listening.",
        },
        "agnesfallback": {
            "binary_exists": True,
            "api_server_listening": False,
        },
    }, now="2026-06-22T00:00:00+00:00")
    refreshed_by_id = {row.get("runtime_connector_id"): row for row in refreshed_rows}
    require(refreshed_by_id["rtc_hermes_default_gateway"]["status"] == "unavailable", "Hermes refresh status projection failed", failures)
    require(refreshed_by_id["rtc_agnesfallback_cli"]["status"] == "available", "Agnesfallback CLI refresh status projection failed", failures)
    require(refreshed_by_id["rtc_agnesfallback_openai_api"]["status"] == "unavailable", "Agnesfallback API refresh status projection failed", failures)
    require(refreshed_by_id["rtc_hermes_default_gateway"]["last_health_at"] == "2026-06-22T00:00:00+00:00", "runtime connector refresh health timestamp failed", failures)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """CREATE TABLE runtime_connectors(
                runtime_connector_id TEXT PRIMARY KEY,
                provider TEXT,
                connector_type TEXT,
                profile_name TEXT,
                base_url TEXT,
                binary_path TEXT,
                status TEXT,
                allow_real_run INTEGER,
                require_confirm_run INTEGER,
                observation_level TEXT,
                capability_manifest_json TEXT,
                capability_policy_hash TEXT,
                last_health_at TEXT,
                last_error TEXT,
                trust_status TEXT DEFAULT 'trusted',
                trust_note TEXT,
                trust_updated_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        upsert_runtime_connector(conn, connector_rows[0])
        count = conn.execute("SELECT COUNT(*) FROM runtime_connectors").fetchone()[0]
        require(count == 1, "runtime connector upsert did not insert row", failures)
        require(normalize_trust_status("nonsense") == "review_required", "trust status fallback failed", failures)
        trust_row = runtime_connector_trust(conn, connector_rows[0]["runtime_connector_id"], refresh=False)
        require(trust_row and trust_row.get("trust_status") == "trusted", "runtime connector trust read failed", failures)
        update = apply_runtime_connector_trust_update(
            conn,
            connector_rows[0]["runtime_connector_id"],
            {"trust_status": "blocked", "trust_note": "Smoke blocks this connector."},
            now="2026-06-22T00:00:00+00:00",
            redact_text=lambda value, limit: str(value or "")[:limit],
        )
        require(update and update.get("after", {}).get("trust_status") == "blocked", "runtime connector trust update failed", failures)
    finally:
        conn.close()
    cache = ReadModelCache(ttl_sec=10, max_items=2)
    headers = {"X-AgentOps-Workspace-Id": "ws_smoke"}
    auth_ctx = {"mode": "agent_token", "workspace_id": "ws_smoke", "agent_id": "agt_smoke", "scopes": ["tasks:read"], "token_id": "fixture_token_ref"}
    first = cache.cached("smoke", {"limit": ["1"]}, headers, lambda: {"value": "one"}, auth_ctx)
    second = cache.cached("smoke", {"limit": ["1"]}, headers, lambda: {"value": "two"}, auth_ctx)
    bypass = cache.cached("smoke", {"limit": ["1"], "refresh_cache": ["true"]}, headers, lambda: {"value": "fresh"}, auth_ctx)
    require(first.get("read_model_cache", {}).get("status") == "miss", "read model cache first read should miss", failures)
    require(second.get("read_model_cache", {}).get("status") == "hit" and second.get("value") == "one", "read model cache second read should hit original payload", failures)
    require(bypass.get("read_model_cache", {}).get("status") == "bypass" and bypass.get("value") == "fresh", "read model cache refresh should bypass", failures)
    require("fixture_token_ref" not in json.dumps([first, second, bypass], ensure_ascii=False), "read model cache leaked token-like auth ref", failures)
    prepared_row = {
        "action_id": "pa_smoke",
        "workspace_id": "local-demo",
        "task_id": "tsk_pa_smoke",
        "run_id": "run_pa_smoke",
        "tool_call_id": "tc_pa_smoke",
        "approval_id": "ap_pa_smoke",
        "requested_by_agent_id": "agt_research",
        "action_type": "external.publish",
        "normalized_args_json": json.dumps({"operation": "publish", "token": "api_key=fixture_secret_value"}, sort_keys=True),
        "target_resource": "mock://customer/delivery",
        "risk_level": "critical",
        "policy_version": "approval-wall-v1",
        "checkpoint_json": json.dumps({"checkpoint": "before_publish", "session": "password=fixture_session_value"}, sort_keys=True),
        "action_hash": "",
        "idempotency_key": "pa-smoke-idempotency",
        "status": "prepared",
        "provider_side_effect_id": None,
        "result_summary": None,
        "created_at": "2026-06-22T00:00:00+00:00",
        "approved_at": None,
        "consumed_at": None,
        "expires_at": "2099-06-24T00:00:00+00:00",
    }
    require(prepared_action_hash_payload(prepared_row).get("policy_version") == "approval-wall-v1", "prepared action hash payload policy fallback failed", failures)
    prepared_row["action_hash"] = prepared_action_hash(prepared_row)
    prepared_public = prepared_action_public(prepared_row)
    prepared_get = build_prepared_action_get_response(prepared_row, {"approval_id": "ap_pa_smoke", "decision": "pending"})
    prepared_get_missing = build_prepared_action_get_not_found_response("pa_missing_smoke")
    prepared_inspect_forbidden = build_prepared_action_agent_forbidden_response(operation="inspect")
    prepared_resume_forbidden = build_prepared_action_agent_forbidden_response(operation="resume")
    inspect_missing_access = prepared_action_route_access_error(
        action_id="pa_missing_smoke",
        row=None,
        identity={"workspace_id": "local-demo", "agent_id": "agt_smoke"},
        operation="inspect",
        enforce_agent_match=True,
    )
    resume_missing_access = prepared_action_route_access_error(
        action_id="pa_missing_smoke",
        row=None,
        identity={"workspace_id": "local-demo", "agent_id": "agt_smoke"},
        operation="resume",
        enforce_agent_match=True,
    )
    workspace_forbidden_access = prepared_action_route_access_error(
        action_id="pa_smoke",
        row={**prepared_row, "workspace_id": "other-workspace"},
        identity={"workspace_id": "local-demo", "agent_id": "agt_research"},
        operation="inspect",
        enforce_agent_match=True,
    )
    agent_forbidden_access = prepared_action_route_access_error(
        action_id="pa_smoke",
        row=prepared_row,
        identity={"workspace_id": "local-demo", "agent_id": "agt_other_smoke"},
        operation="resume",
        enforce_agent_match=True,
    )
    agent_unenforced_access = prepared_action_route_access_error(
        action_id="pa_smoke",
        row=prepared_row,
        identity={"workspace_id": "local-demo", "agent_id": "agt_other_smoke"},
        operation="inspect",
        enforce_agent_match=False,
    )
    prepared_gate = prepared_action_gate(prepared_row)
    prepared_actions = approval_wall_recommended_actions({"decision": "pending"}, prepared_row, "ap_pa_smoke")
    prepared_serialized = json.dumps([prepared_public, prepared_get], ensure_ascii=False)
    require(prepared_gate.get("hash_match") is True, "prepared action gate hash verification failed", failures)
    require((prepared_get.get("hash_verification") or {}).get("match") is True, "prepared action get response hash verification failed", failures)
    require(prepared_get_missing.get("error") == "not_found" and "pa_missing_smoke" in prepared_get_missing.get("message", ""), "prepared action get missing response failed", failures)
    require(prepared_inspect_forbidden.get("message") == "Agent token cannot inspect another agent's prepared action.", "prepared action inspect-forbidden response failed", failures)
    require(prepared_resume_forbidden.get("message") == "Agent token cannot resume another agent's prepared action.", "prepared action resume-forbidden response failed", failures)
    require(inspect_missing_access and inspect_missing_access[0].get("error") == "not_found" and inspect_missing_access[1] == 404, "prepared action inspect route missing access failed", failures)
    require(resume_missing_access and resume_missing_access[0].get("error") == "prepared_action_not_found" and resume_missing_access[1] == 404, "prepared action resume route missing access failed", failures)
    require(workspace_forbidden_access and workspace_forbidden_access[0].get("error") == "forbidden" and "other-workspace" in workspace_forbidden_access[0].get("message", ""), "prepared action route workspace access failed", failures)
    require(agent_forbidden_access and agent_forbidden_access[0].get("message") == "Agent token cannot resume another agent's prepared action." and agent_forbidden_access[1] == 403, "prepared action route agent access failed", failures)
    require(agent_unenforced_access is None, "prepared action route access must allow agent mismatch when not enforced", failures)
    require("approval prepared-action resume" in " ".join(prepared_actions), "approval wall recommended actions missing prepared-action resume", failures)
    require("fixture_secret_value" not in prepared_serialized and "fixture_session_value" not in prepared_serialized, "prepared action public projection leaked token-like metadata", failures)
    require(prepared_action_id_from_request({"prepared_action_id": "pa_smoke"}) == "pa_smoke", "prepared action request id helper failed", failures)
    require(prepared_action_stored_args(prepared_row).get("operation") == "publish", "prepared action stored args helper failed", failures)
    require(prepared_action_checkpoint(prepared_row).get("checkpoint") == "before_publish", "prepared action checkpoint helper failed", failures)
    external_side_effect_detected = tool_call_has_external_side_effect_intent(
        "custom.integration.note",
        "custom",
        "https://api.example.local/upload",
        {"operation": "upload", "summary_only": True},
    )
    metadata_side_effect_ignored = tool_call_has_external_side_effect_intent(
        "runtime.capability.report",
        "custom",
        None,
        {
            "requires_prepared_action_for_external_write": True,
            "knowledge_retrieval_task_context": {
                "task_id": "tsk_smoke",
                "query_source": "task_id",
                "task_text_omitted": True,
                "token_omitted": True,
            },
            "raw_payload_stored": False,
        },
    )
    require("openai.file_search.upload" in RISKY_TOOLS, "Approval Wall risky tool registry missing file-search upload", failures)
    require(external_side_effect_detected is True, "Approval Wall external side-effect detection failed", failures)
    require(metadata_side_effect_ignored is False, "Approval Wall metadata-only side-effect detection should be ignored", failures)
    missing_gate = prepared_action_resume_gate_error(
        action_id=None,
        row=None,
        approval=None,
        expected_args={"operation": "publish"},
        expected_action_type="external.publish",
        comparable_fields=("operation",),
        missing_error="external_publish_prepared_action_required",
        missing_message="External publish requires a prepared action.",
        approval_message="External publish can execute only after approval.",
    )
    pending_gate = prepared_action_resume_gate_error(
        action_id="pa_smoke",
        row=prepared_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "pending"},
        expected_args={"operation": "publish"},
        expected_action_type="external.publish",
        comparable_fields=("operation",),
        missing_error="external_publish_prepared_action_required",
        missing_message="External publish requires a prepared action.",
        approval_message="External publish can execute only after approval.",
    )
    mismatch_gate = prepared_action_resume_gate_error(
        action_id="pa_smoke",
        row=prepared_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
        expected_args={"operation": "archive"},
        expected_action_type="external.publish",
        comparable_fields=("operation",),
        missing_error="external_publish_prepared_action_required",
        missing_message="External publish requires a prepared action.",
        approval_message="External publish can execute only after approval.",
    )
    consumed_row = {**prepared_row, "status": "consumed", "consumed_at": "2026-06-22T00:01:00+00:00"}
    consumed_gate = prepared_action_resume_gate_error(
        action_id="pa_smoke",
        row=consumed_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
        expected_args={"operation": "publish"},
        expected_action_type="external.publish",
        comparable_fields=("operation",),
        missing_error="external_publish_prepared_action_required",
        missing_message="External publish requires a prepared action.",
        approval_message="External publish can execute only after approval.",
    )
    missing_resume_response = build_prepared_action_resume_blocked_response(
        action_id="pa_missing_smoke",
        row=None,
        approval=None,
    )
    pending_resume_response = build_prepared_action_resume_blocked_response(
        action_id="pa_smoke",
        row=prepared_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "pending"},
    )
    consumed_resume_response = build_prepared_action_resume_blocked_response(
        action_id="pa_smoke",
        row=consumed_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
    )
    tampered_row = {**prepared_row, "target_resource": "mock://customer/changed-delivery"}
    hash_mismatch_response = build_prepared_action_hash_mismatch_response(
        tampered_row,
        message="Prepared action changed after approval request; create a new prepared action.",
        approval={"approval_id": "ap_pa_smoke", "decision": "pending"},
        include_prepared_action=True,
    )
    resume_hash_mismatch_response = build_prepared_action_resume_blocked_response(
        action_id="pa_smoke",
        row=tampered_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
    )
    success_resume_response = build_prepared_action_resume_success_response(
        prepared_action=consumed_row,
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
        provider_side_effect_id="side_effect_smoke",
        hash_verification={
            "stored_action_hash": prepared_row["action_hash"],
            "current_action_hash": prepared_row["action_hash"],
            "match": True,
        },
    )
    provider_resume_request = build_prepared_action_provider_resume_request(
        prepared_row,
        provider_side_effect_id="provider_side_effect_smoke",
        result_summary="Provider created external object after approval.",
    )
    provider_result_fields = build_prepared_action_provider_result_fields(
        prepared_row,
        {"prepared_action": prepared_action_public(consumed_row)},
        200,
    )
    prepare_response_fields = build_prepared_action_prepare_response_fields({
        "prepared_action": prepared_action_public(prepared_row),
        "approval": {"approval_id": "ap_pa_smoke", "decision": "pending"},
        "resume_contract": "Approve then resume exactly once.",
        "operation": "prepared_action_prepare",
        "outcome": "created",
    })
    approval_decision_response = build_prepared_action_approval_decision_response(
        approval={"approval_id": "ap_pa_smoke", "decision": "approved"},
        prepared_action={**prepared_row, "status": "approved"},
        decision="approved",
    )
    agent_plan_row = {
        "workspace_id": "local-demo",
        "task_id": "tsk_plan_contract_smoke",
        "run_id": None,
        "agent_id": "agt_plan_contract_smoke",
        "task_understanding": "Test Agent Plan contract hashing.",
        "referenced_specs_json": '["PROJECT_SPEC.md"]',
        "referenced_memories_json": '["knowledge/shared/common_failures.md"]',
        "referenced_bases_json": '["agent_gateway_ledger"]',
        "proposed_files_to_change_json": '["server.py"]',
        "risk_level": "medium",
        "approval_required": 1,
        "execution_steps_json": '["read", "execute", "verify"]',
        "verification_plan": "Run module boundary smoke.",
        "rollback_plan": "Revert the helper extraction.",
        "plan_version": 1,
    }
    agent_plan_contract_payload = agent_plan_contract(agent_plan_row)
    agent_plan_hash = compute_agent_plan_hash(agent_plan_row)
    agent_plan_verification_digest = agent_plan_verification_hash(
        "plan_contract_smoke",
        {
            "plan_hash": agent_plan_hash,
            "pass": False,
            "failed_checks": [{"id": "read_specs"}],
            "summary": {"execution_steps": 3},
        },
    )
    spec_authority = resolve_agent_plan_spec_authority(["PROJECT_SPEC.md"], repo_root=ROOT)
    missing_spec_authority = resolve_agent_plan_spec_authority(["missing/not-real.md"], repo_root=ROOT)
    file_scope = resolve_agent_plan_file_scope(["server.py", "docs/MODULE_BOUNDARY_PLAN.md"], repo_root=ROOT)
    unsafe_file_scope = resolve_agent_plan_file_scope(["../outside.py", "https://example.com/file.py"], repo_root=ROOT)
    agent_plan_verification = build_agent_plan_verification(
        agent_plan_row,
        spec_authority=spec_authority,
        memory_authority={"ok": True, "approved": [{"memory_id": "mem_smoke"}], "non_authoritative": [], "missing": [], "knowledge_context": []},
        base_authority={"ok": True, "table_bases": [], "file_bases": [], "virtual_bases": [{"ref": "agent_gateway_ledger"}]},
        file_scope=file_scope,
    )
    agent_plan_bad_verification = build_agent_plan_verification(
        {**agent_plan_row, "risk_level": "critical", "approval_required": 0, "execution_steps_json": '["read"]', "verification_plan": "", "rollback_plan": ""},
        spec_authority=missing_spec_authority,
        memory_authority={"ok": False, "approved": [], "non_authoritative": [{"memory_id": "mem_candidate"}], "missing": [], "knowledge_context": []},
        base_authority={"ok": False, "table_bases": [], "file_bases": [], "virtual_bases": [], "missing": ["missing_base"]},
        file_scope=unsafe_file_scope,
    )
    agent_plan_pending_approval = build_agent_plan_pending_approval(
        {**agent_plan_row, "plan_id": "plan_pending_smoke", "plan_hash": agent_plan_hash, "risk_level": "high"},
        approval_id="ap_plan_pending_smoke",
        created_at="2026-06-22T00:00:00+00:00",
        expires_at="2026-06-29T00:00:00+00:00",
    )
    agent_plan_approval_run = build_agent_plan_approval_run(
        {**agent_plan_row, "plan_id": "plan_pending_smoke", "plan_hash": agent_plan_hash},
        run_id="run_plan_approval_smoke",
        trace_id="trace_plan_approval_smoke",
        delegation_id="del_plan_approval_smoke",
        created_at="2026-06-22T00:00:00+00:00",
    )
    agent_plan_not_transitionable_response = build_agent_plan_not_transitionable_response(
        plan_id="plan_superseded_smoke",
        status="superseded",
        message="Superseded plans cannot be approved or rejected.",
    )
    agent_plan_not_approvable_response = build_agent_plan_not_approvable_response(
        plan_id="plan_draft_smoke",
        status="draft",
        message="Only submitted plans can be approved.",
    )
    agent_plan_verification_failed_response = build_agent_plan_verification_failed_response(
        plan_id="plan_failed_smoke",
        failed_checks=[{"id": "read_specs"}],
        message="Agent Plan must pass verification before approval.",
    )
    agent_plan_approval_decision_response = build_agent_plan_approval_decision_response(
        approval={"approval_id": "ap_plan_smoke", "decision": "approved"},
        agent_plan_decision={
            "agent_plan": {"plan_id": "plan_smoke", "status": "approved"},
            "verification": {"pass": True},
            "verification_result_hash": "hash_plan_verification_smoke",
        },
    )
    agent_plan_anchor_response = build_agent_plan_approval_anchor_required_response(
        plan_id="plan_anchor_smoke",
    )
    agent_plan_status_response = build_agent_plan_status_transition_required_response(
        requested_status="approved",
    )
    agent_plan_bound_response = build_agent_plan_bound_approval_forbidden_response(
        auth_ctx={"mode": "session", "agent_id": "agt_bound_smoke"},
    )
    agent_plan_run_required_response = build_agent_plan_run_required_response(
        task_id="tsk_run_gate_smoke",
        agent_id="agt_run_gate_smoke",
    )
    agent_plan_run_task_mismatch_response = build_agent_plan_run_task_mismatch_response(
        plan_id="plan_run_gate_smoke",
    )
    agent_plan_run_agent_mismatch_response = build_agent_plan_run_agent_mismatch_response(
        plan_id="plan_run_gate_smoke",
    )
    agent_plan_run_not_executable_response = build_agent_plan_run_not_executable_response(
        plan_id="plan_run_gate_smoke",
        status="draft",
    )
    agent_plan_run_approval_required_response = build_agent_plan_run_approval_required_response(
        plan={"plan_id": "plan_run_gate_smoke", "status": "submitted", "approval_id": "ap_plan_run_gate_smoke"},
        approval={"approval_id": "ap_plan_run_gate_smoke", "decision": "pending"},
    )
    agent_plan_run_hash_mismatch_response = build_agent_plan_run_hash_mismatch_response(
        plan_id="plan_run_gate_smoke",
        stored_plan_hash="stored_hash_smoke",
        current_plan_hash="current_hash_smoke",
    )
    run_start_rebind_response = build_run_start_rebind_forbidden_response(
        {"agent_plan_id": "plan_existing_smoke", "plan_hash": "hash_existing_smoke"},
        run_id="run_rebind_smoke",
        requested_agent_plan_id="plan_requested_smoke",
        requested_plan_hash="hash_requested_smoke",
        mismatches=["agent_plan_id", "plan_hash"],
    )
    run_start_binding_comparison = compare_run_start_binding(
        {
            "workspace_id": "local-demo",
            "task_id": "tsk_binding_smoke",
            "agent_id": "agt_binding_smoke",
            "agent_plan_id": "plan_existing_smoke",
            "plan_hash": "hash_existing_smoke",
        },
        workspace_id="local-demo",
        task_id="tsk_binding_smoke",
        agent_id="agt_binding_smoke",
        plan_binding={"plan_id": "plan_requested_smoke", "plan_hash": "hash_requested_smoke"},
    )
    run_start_success_response = build_run_start_success_response(
        run={"run_id": "run_start_success_smoke", "agent_plan_id": "plan_success_smoke", "plan_hash": "hash_success_smoke"},
        outcome="created",
        plan_binding={
            "plan_id": "plan_success_smoke",
            "plan_hash": "hash_success_smoke",
            "verification_result_hash": "hash_verification_success_smoke",
            "verification": {"pass": True},
        },
    )
    run_heartbeat_update = build_run_heartbeat_update(
        {"run_id": "run_heartbeat_smoke", "status": "running"},
        status="completed",
        ended_at="2026-06-22T00:00:00+00:00",
        duration_ms=1234,
        output_summary="Heartbeat complete.",
        error_type=None,
        error_message=None,
        output_tokens=42,
        cost_usd=0.0,
    )
    high_risk_required_response = build_high_risk_toolcall_prepared_action_required_response(
        tool_name="openai.file_search.upload",
        risk_level="critical",
        requested_status="completed",
        external_side_effect_intent=True,
        run_id="run_high_risk_smoke",
        task_id="tsk_high_risk_smoke",
    )
    require(missing_gate and missing_gate.get("error") == "external_publish_prepared_action_required", "resume gate missing-id error failed", failures)
    require(pending_gate and pending_gate.get("error") == "approval_required", "resume gate approval-required error failed", failures)
    require(mismatch_gate and mismatch_gate.get("error") == "prepared_action_request_mismatch" and "operation" in mismatch_gate.get("mismatched_fields", []), "resume gate mismatch error failed", failures)
    require(consumed_gate and consumed_gate.get("error") == "prepared_action_already_consumed", "resume gate consumed error failed", failures)
    require(missing_resume_response and missing_resume_response[0].get("error") == "prepared_action_not_found" and missing_resume_response[1] == 404, "resume route missing response failed", failures)
    require(pending_resume_response and pending_resume_response[0].get("error") == "approval_required" and pending_resume_response[1] == 409, "resume route approval-required response failed", failures)
    require(consumed_resume_response and consumed_resume_response[0].get("error") == "prepared_action_already_consumed" and consumed_resume_response[1] == 409, "resume route consumed response failed", failures)
    require(hash_mismatch_response.get("error") == "action_hash_mismatch", "prepared-action hash mismatch response failed", failures)
    require(hash_mismatch_response.get("approval", {}).get("approval_id") == "ap_pa_smoke", "prepared-action hash mismatch approval field failed", failures)
    require((hash_mismatch_response.get("prepared_action") or {}).get("raw_prompt_omitted") is True, "prepared-action hash mismatch public projection failed", failures)
    require(resume_hash_mismatch_response and resume_hash_mismatch_response[0].get("error") == "action_hash_mismatch" and resume_hash_mismatch_response[1] == 409, "resume route hash mismatch response failed", failures)
    require(success_resume_response.get("status") == "completed" and success_resume_response.get("execute_once") is True, "resume success response failed", failures)
    require((success_resume_response.get("hash_verification") or {}).get("match") is True, "resume success hash verification failed", failures)
    require(provider_resume_request.get("workspace_id") == "local-demo", "provider resume request workspace failed", failures)
    require(provider_resume_request.get("provider_side_effect_id") == "provider_side_effect_smoke", "provider resume request side-effect failed", failures)
    require(provider_resume_request.get("result_summary") == "Provider created external object after approval.", "provider resume request summary failed", failures)
    require(provider_result_fields.get("approval_id") == "ap_pa_smoke", "provider result approval id failed", failures)
    require(provider_result_fields.get("prepared_action_resume_status") == 200, "provider result resume status failed", failures)
    require((provider_result_fields.get("prepared_action") or {}).get("status") == "consumed", "provider result prepared action failed", failures)
    require(provider_result_fields.get("token_omitted") is True, "provider result omission proof missing", failures)
    require((prepare_response_fields.get("approval_wall") or {}).get("outcome") == "created", "prepare response approval wall outcome failed", failures)
    require((prepare_response_fields.get("approval_wall") or {}).get("token_omitted") is True, "prepare response omission proof missing", failures)
    require("approval prepared-action resume --action-id pa_smoke" in prepare_response_fields.get("next_action", ""), "prepare response next action failed", failures)
    require(approval_decision_response.get("resume_required") is True, "approval decision response resume-required flag failed", failures)
    require((approval_decision_response.get("prepared_action") or {}).get("status") == "approved", "approval decision response prepared action failed", failures)
    require(approval_decision_response.get("token_omitted") is True, "approval decision response omission proof missing", failures)
    require(load_json_list_field({"items": "[1, 2]"}, "items") == [1, 2], "agent plan json-list parser failed", failures)
    require(load_json_list_field({"items": "{\"bad\": true}"}, "items") == [], "agent plan json-list parser accepted non-list", failures)
    require(row_field(None, "missing", "fallback") == "fallback", "agent plan row field fallback failed", failures)
    require(agent_plan_contract_payload.get("referenced_specs") == ["PROJECT_SPEC.md"], "agent plan contract specs failed", failures)
    require(agent_plan_contract_payload.get("execution_steps") == ["read", "execute", "verify"], "agent plan contract steps failed", failures)
    require(agent_plan_contract_payload.get("approval_required") is True, "agent plan contract approval flag failed", failures)
    require(isinstance(agent_plan_hash, str) and len(agent_plan_hash) == 64 and agent_plan_hash == compute_agent_plan_hash(agent_plan_row), "agent plan hash stability failed", failures)
    require(isinstance(agent_plan_verification_digest, str) and len(agent_plan_verification_digest) == 64, "agent plan verification hash failed", failures)
    require(plan_ref_is_safe_relative_path("server.py") is True, "agent plan safe path helper rejected relative path", failures)
    require(plan_ref_is_safe_relative_path("../server.py") is False, "agent plan safe path helper accepted parent traversal", failures)
    require(plan_ref_path("PROJECT_SPEC.md", repo_root=ROOT) is not None, "agent plan path resolver failed for repo file", failures)
    require(spec_authority.get("ok") is True and spec_authority.get("readable"), "agent plan spec authority failed for readable spec", failures)
    require(missing_spec_authority.get("ok") is False and missing_spec_authority.get("missing") == ["missing/not-real.md"], "agent plan spec authority missing-file failed", failures)
    require(file_scope.get("ok") is True and len(file_scope.get("scoped") or []) == 2, "agent plan file scope failed for repo paths", failures)
    require(unsafe_file_scope.get("ok") is False and len(unsafe_file_scope.get("unsafe") or []) == 2, "agent plan file scope accepted unsafe paths", failures)
    require(agent_plan_verification.get("pass") is True, "agent plan verification builder failed passing plan", failures)
    require(agent_plan_verification.get("summary", {}).get("resolved_base_refs") == 1, "agent plan verification summary failed", failures)
    require(agent_plan_bad_verification.get("pass") is False, "agent plan verification builder failed negative plan", failures)
    require({"read_specs", "memory_authority", "compare_bases", "execution_steps", "verification_plan", "rollback_plan", "risk_gate", "file_scope"}.issubset({check.get("id") for check in agent_plan_bad_verification.get("failed_checks") or []}), "agent plan verification failed-check coverage missing", failures)
    require(agent_plan_pending_approval.get("approval_id") == "ap_plan_pending_smoke", "agent plan pending approval id failed", failures)
    require(agent_plan_pending_approval.get("decision") == "pending" and agent_plan_pending_approval.get("decided_at") is None, "agent plan pending approval decision fields failed", failures)
    require(agent_plan_pending_approval.get("created_at") == "2026-06-22T00:00:00+00:00" and agent_plan_pending_approval.get("expires_at") == "2026-06-29T00:00:00+00:00", "agent plan pending approval timestamps failed", failures)
    require("plan_pending_smoke" in agent_plan_pending_approval.get("reason", "") and agent_plan_hash[:12] in agent_plan_pending_approval.get("reason", ""), "agent plan pending approval reason failed", failures)
    require(agent_plan_approval_run.get("runtime_type") == "governance" and agent_plan_approval_run.get("status") == "waiting_approval", "agent plan approval run governance state failed", failures)
    require(agent_plan_approval_run.get("approval_required") == 1 and agent_plan_approval_run.get("agent_plan_id") == "plan_pending_smoke", "agent plan approval run plan linkage failed", failures)
    require(agent_plan_approval_run.get("trace_id") == "trace_plan_approval_smoke" and agent_plan_approval_run.get("delegation_id") == "del_plan_approval_smoke", "agent plan approval run trace fields failed", failures)
    require(agent_plan_approval_run.get("plan_hash") == agent_plan_hash and "plan_pending_smoke" in agent_plan_approval_run.get("input_summary", ""), "agent plan approval run summary/hash failed", failures)
    require(agent_plan_not_transitionable_response.get("error") == "agent_plan_not_transitionable" and agent_plan_not_transitionable_response.get("status") == "superseded", "agent plan not-transitionable response failed", failures)
    require(agent_plan_not_approvable_response.get("error") == "agent_plan_not_approvable" and agent_plan_not_approvable_response.get("status") == "draft", "agent plan not-approvable response failed", failures)
    require(agent_plan_verification_failed_response.get("error") == "agent_plan_verification_failed" and agent_plan_verification_failed_response.get("failed_checks") == [{"id": "read_specs"}], "agent plan verification-failed response failed", failures)
    require(all(payload.get("token_omitted") is True for payload in [agent_plan_not_transitionable_response, agent_plan_not_approvable_response, agent_plan_verification_failed_response]), "agent plan transition error omission proof missing", failures)
    require((agent_plan_approval_decision_response.get("agent_plan") or {}).get("status") == "approved", "agent plan approval decision response plan failed", failures)
    require(agent_plan_approval_decision_response.get("verification_result_hash") == "hash_plan_verification_smoke", "agent plan approval decision response hash failed", failures)
    require(agent_plan_approval_decision_response.get("token_omitted") is True, "agent plan approval decision response omission proof missing", failures)
    require(agent_plan_anchor_response.get("error") == "agent_plan_approval_anchor_required", "agent plan anchor-required response failed", failures)
    require(agent_plan_anchor_response.get("plan_id") == "plan_anchor_smoke", "agent plan anchor-required plan id failed", failures)
    require(agent_plan_status_response.get("error") == "plan_status_transition_required", "agent plan status-transition response failed", failures)
    require(agent_plan_status_response.get("allowed_create_statuses") == ["draft", "submitted"], "agent plan status allowed statuses failed", failures)
    require(agent_plan_bound_response.get("error") == "agent_plan_human_approval_required", "agent plan bound approval response failed", failures)
    require(agent_plan_bound_response.get("agent_id") == "agt_bound_smoke", "agent plan bound approval agent failed", failures)
    require(agent_plan_run_required_response.get("error") == "agent_plan_required" and agent_plan_run_required_response.get("task_id") == "tsk_run_gate_smoke", "agent plan run-required response failed", failures)
    require("agent-plan create" in agent_plan_run_required_response.get("hint", ""), "agent plan run-required hint failed", failures)
    require(agent_plan_run_task_mismatch_response.get("error") == "agent_plan_task_mismatch", "agent plan run task-mismatch response failed", failures)
    require(agent_plan_run_agent_mismatch_response.get("error") == "agent_plan_agent_mismatch", "agent plan run agent-mismatch response failed", failures)
    require(agent_plan_run_not_executable_response.get("error") == "agent_plan_not_executable" and agent_plan_run_not_executable_response.get("status") == "draft", "agent plan run not-executable response failed", failures)
    require(agent_plan_run_approval_required_response.get("error") == "agent_plan_approval_required" and agent_plan_run_approval_required_response.get("approval_decision") == "pending", "agent plan run approval-required response failed", failures)
    require(agent_plan_run_hash_mismatch_response.get("error") == "agent_plan_hash_mismatch" and agent_plan_run_hash_mismatch_response.get("current_plan_hash") == "current_hash_smoke", "agent plan run hash-mismatch response failed", failures)
    require(run_start_rebind_response.get("error") == "run_start_rebind_forbidden", "run-start rebind response error failed", failures)
    require(run_start_rebind_response.get("existing_agent_plan_id") == "plan_existing_smoke" and run_start_rebind_response.get("requested_agent_plan_id") == "plan_requested_smoke", "run-start rebind response plan ids failed", failures)
    require(run_start_rebind_response.get("mismatches") == ["agent_plan_id", "plan_hash"], "run-start rebind response mismatches failed", failures)
    require(run_start_binding_comparison.get("mismatches") == ["agent_plan_id", "plan_hash"], "run-start binding comparison mismatches failed", failures)
    require((run_start_binding_comparison.get("expected") or {}).get("plan_hash") == "hash_requested_smoke", "run-start binding comparison expected hash failed", failures)
    require((run_start_binding_comparison.get("actual") or {}).get("plan_hash") == "hash_existing_smoke", "run-start binding comparison actual hash failed", failures)
    require((run_start_success_response.get("run") or {}).get("run_id") == "run_start_success_smoke", "run-start success response run failed", failures)
    require((run_start_success_response.get("agent_plan") or {}).get("plan_hash") == "hash_success_smoke", "run-start success response plan hash failed", failures)
    require((run_start_success_response.get("agent_plan") or {}).get("verification_pass") is True, "run-start success response verification flag failed", failures)
    require(run_heartbeat_update.get("run_id") == "run_heartbeat_smoke" and run_heartbeat_update.get("status") == "completed", "run heartbeat update projection failed", failures)
    require(run_heartbeat_update.get("token_omitted") is True, "run heartbeat update token omission proof failed", failures)
    require(run_heartbeat_terminal_task_status("completed") == "completed", "run heartbeat completed task status failed", failures)
    require(run_heartbeat_terminal_task_status("blocked") == "blocked", "run heartbeat blocked task status failed", failures)
    require(run_heartbeat_terminal_task_status("running") is None, "run heartbeat non-terminal task status failed", failures)
    require(all(payload.get("token_omitted") is True for payload in [
        agent_plan_anchor_response,
        agent_plan_status_response,
        agent_plan_bound_response,
        agent_plan_run_required_response,
        agent_plan_run_task_mismatch_response,
        agent_plan_run_agent_mismatch_response,
        agent_plan_run_not_executable_response,
        agent_plan_run_approval_required_response,
        agent_plan_run_hash_mismatch_response,
        run_start_rebind_response,
    ]), "agent plan response omission proof missing", failures)
    require(high_risk_required_response.get("error") == "high_risk_prepared_action_required", "high-risk prepared-action required response error failed", failures)
    require(high_risk_required_response.get("external_side_effect_intent") is True, "high-risk prepared-action required response intent failed", failures)
    require("prepare_action=true" in high_risk_required_response.get("message", ""), "high-risk prepared-action guidance failed", failures)
    require(high_risk_required_response.get("token_omitted") is True, "high-risk prepared-action omission proof missing", failures)
    runtime_waiting_payload = runtime_probe_prepared_action_required_payload(
        prepared={
            "run_id": "run_runtime_probe_smoke",
            "tool_call_id": "tc_runtime_probe_smoke",
            "approval_wall": {
                "approval": {"approval_id": "ap_runtime_probe_smoke"},
                "prepared_action": {"action_id": "pa_runtime_probe_smoke", "action_hash": "hash_runtime_probe_smoke"},
            },
        },
        provider="hermes",
        mode="default_gateway_fixed_probe",
        task_id="tsk_runtime_probe_smoke",
        prompt_hash="prompt_hash_runtime_probe_smoke",
    )
    shared_waiting_payload = build_prepared_action_waiting_response(
        base={"provider": "smoke", "dry_run": True, "run_id": "run_waiting_smoke"},
        approval_wall={
            "approval": {"approval_id": "ap_waiting_smoke"},
            "prepared_action": {"action_id": "pa_waiting_smoke", "action_hash": "hash_waiting_smoke"},
        },
        reason="smoke_prepared_action_required",
        resume_instruction="POST /api/smoke with prepared_action_id={prepared_action_id}",
    )
    shared_next_action = prepared_action_waiting_next_action(
        approval_id="ap_waiting_smoke",
        prepared_action_id="pa_waiting_smoke",
        resume_instruction="resume action {action_id}",
    )
    runtime_blocked_payload = runtime_probe_blocked_payload(
        provider="hermes",
        mode="default_gateway_fixed_probe",
        gate_error={"error": "prepared_action_request_mismatch", "mismatched_fields": ["prompt_hash"], "token_omitted": True},
        created=False,
    )
    shared_blocked_payload = build_prepared_action_blocked_response(
        base={
            "provider": "smoke",
            "dry_run": True,
            "live_export_performed": False,
            "configured": True,
        },
        gate_error={
            "error": "prepared_action_request_mismatch",
            "prepared_action_id": "pa_blocked_smoke",
            "mismatched_fields": ["title"],
            "token_omitted": True,
        },
    )
    require(shared_waiting_payload.get("status") == "waiting_approval", "shared waiting response status failed", failures)
    require(shared_waiting_payload.get("approval_id") == "ap_waiting_smoke", "shared waiting response approval id failed", failures)
    require(shared_waiting_payload.get("prepared_action_id") == "pa_waiting_smoke", "shared waiting response prepared action id failed", failures)
    require(shared_waiting_payload.get("prepared_action_hash") == "hash_waiting_smoke", "shared waiting response action hash failed", failures)
    require("prepared_action_id=pa_waiting_smoke" in shared_waiting_payload.get("next_action", ""), "shared waiting response next action failed", failures)
    require(shared_waiting_payload.get("token_omitted") is True, "shared waiting response omission proof missing", failures)
    require(shared_next_action.endswith("resume action pa_waiting_smoke"), "prepared action waiting next-action template failed", failures)
    require(shared_blocked_payload.get("status") == "blocked", "shared blocked response status failed", failures)
    require(shared_blocked_payload.get("reason") == "prepared_action_request_mismatch", "shared blocked response reason failed", failures)
    require(shared_blocked_payload.get("provider") == "smoke", "shared blocked response base field failed", failures)
    require(shared_blocked_payload.get("live_export_performed") is False, "shared blocked response execution proof failed", failures)
    require(shared_blocked_payload.get("token_omitted") is True, "shared blocked response omission proof missing", failures)
    require(runtime_waiting_payload.get("reason") == "runtime_probe_prepared_action_required", "runtime prepared-action waiting response reason failed", failures)
    require(runtime_waiting_payload.get("prepared_action_id") == "pa_runtime_probe_smoke", "runtime prepared-action waiting response id failed", failures)
    require(runtime_waiting_payload.get("live_probe_performed") is False, "runtime waiting response must not claim live execution", failures)
    require("prepared_action_id=pa_runtime_probe_smoke" in runtime_waiting_payload.get("next_action", ""), "runtime waiting next_action missing prepared action id", failures)
    require(runtime_blocked_payload.get("created") is False, "runtime blocked payload created flag failed", failures)
    require(runtime_blocked_payload.get("reason") == "prepared_action_request_mismatch", "runtime blocked payload reason failed", failures)
    require(runtime_blocked_payload.get("live_probe_performed") is False, "runtime blocked payload must not claim live execution", failures)
    daemons = [{
        "adapter": "mock",
        "agent_id": "agt_worker_local_smoke",
        "running": True,
        "worker_status": "running",
        "pid": 4242,
        "processed": 3,
        "iterations": 4,
        "consecutive_errors": 0,
        "total_errors": 0,
        "state_updated_at": "2026-06-22T00:00:00+00:00",
    }]
    remote_fleet = {
        "status": "attention",
        "remote_worker_count": 1,
        "total_remote_enrollments": 1,
        "active_enrollments": 1,
        "fresh_enrollments": 0,
        "stale_enrollments": 1,
        "never_seen_enrollments": 0,
        "active_sessions": 0,
        "remote_workers": [{
            "agent_id": "agt_worker_remote_smoke",
            "agent_name": "Remote Smoke Worker",
            "workspace_id": "local-demo",
            "runtime_type": "mock",
            "token_status": "active",
            "heartbeat_state": "stale",
            "active_session_count": 0,
            "last_heartbeat_at": "2026-06-22T00:00:00+00:00",
            "scope_count": 3,
            "token_ref": "safe_ref_remote",
        }],
    }
    worker_agents = [{
        "agent_id": "agt_worker_local_smoke",
        "name": "Local Smoke Worker",
        "runtime_type": "mock",
        "status": "running",
        "updated_at": "2026-06-22T00:00:00+00:00",
    }, {
        "agent_id": "agt_worker_registered_smoke",
        "name": "Registered Smoke Worker",
        "runtime_type": "mock",
        "status": "idle",
        "updated_at": "2026-06-22T00:00:00+00:00",
    }]
    stuck_tasks = [{"task_id": "tsk_worker_stuck_smoke"}]
    stuck_jobs = [{"job_id": "job_worker_stuck_smoke", "workflow_type": "customer_worker", "status": "running", "age_sec": 901, "stuck_reason": "threshold"}]
    adapter_readiness = {"summary": {"recommended_adapter": "mock"}}
    status_payload = build_worker_status_payload(
        worker_agents=worker_agents,
        worker_runs=[{"run_id": "run_worker_smoke", "status": "completed"}],
        worker_tasks=[{"task_id": "tsk_worker_pending_smoke", "status": "planned"}],
        worker_events=[{"event_id": "evt_worker_smoke", "event_type": "task.pull"}],
        daemons=daemons,
        stuck_tasks=stuck_tasks,
        remote_fleet=remote_fleet,
        stuck_workflow_jobs=stuck_jobs,
        adapter_readiness=adapter_readiness,
    )
    fleet_view = build_worker_fleet_view(
        daemons=daemons,
        remote_fleet=remote_fleet,
        adapter_readiness=adapter_readiness["summary"],
        stuck_tasks=stuck_tasks,
        stuck_workflow_jobs=stuck_jobs,
        worker_agents=worker_agents,
    )
    health = worker_fleet_health(status_payload)
    stale_enrollment = {
        "token_id": "fixture_module_boundary_token_ref",
        "agent_id": "agt_worker_remote_smoke",
        "workspace_id": "local-demo",
        "status": "active",
    }
    public_stale = public_worker_stale_enrollment(stale_enrollment)
    hygiene_plan = build_worker_fleet_hygiene_plan(
        stuck_tasks=stuck_tasks,
        stale_enrollments=[stale_enrollment],
        threshold_sec=900,
        enrollment_age_sec=900,
        apply=False,
    )
    revoked_enrollment = public_worker_revoked_enrollment(stale_enrollment, sessions_revoked=2)
    revoke_error = public_worker_enrollment_error(stale_enrollment, status=404, error={"error": "missing"})
    remote_enrollment = {
        "token_id": "fixture_remote_fleet_token_ref",
        "agent_id": "agt_worker_remote_smoke",
        "workspace_id": "local-demo",
        "status": "active",
        "label": "Remote Smoke Worker",
        "heartbeat_state": "fresh",
        "heartbeat_timeout_sec": 30,
        "scopes": ["tasks:read", "agents:heartbeat"],
    }
    remote_session = {
        "session_id": "fixture_remote_session_ref",
        "parent_token_id": "fixture_remote_fleet_token_ref",
        "agent_id": "agt_worker_remote_smoke",
        "workspace_id": "local-demo",
        "status": "active",
        "session_state": "active",
        "scopes": ["tasks:read"],
    }
    public_remote = public_remote_worker(
        remote_enrollment,
        agent={"agent_id": "agt_worker_remote_smoke", "name": "Remote Smoke Worker", "runtime_type": "mock", "status": "idle"},
        active_session_count=1,
    )
    public_session = public_remote_session(remote_session)
    remote_summary = build_worker_remote_fleet_summary(
        enrollments=[remote_enrollment],
        sessions=[remote_session],
        agents_by_id={"agt_worker_remote_smoke": {"name": "Remote Smoke Worker", "runtime_type": "mock", "status": "idle"}},
    )
    require(status_payload.get("status") == "attention", "worker status payload did not reflect stale remote attention", failures)
    require(status_payload.get("fleet_health", {}).get("overall") == "blocked", "worker status payload missing blocked fleet health", failures)
    require(fleet_view.get("summary", {}).get("lane_count") == 3, "worker fleet view did not build expected lanes", failures)
    require(fleet_view.get("summary", {}).get("lane_counts", {}).get("local_daemon") == 1, "worker fleet view missing local daemon lane", failures)
    require(fleet_view.get("summary", {}).get("lane_counts", {}).get("remote_worker") == 1, "worker fleet view missing remote worker lane", failures)
    require(fleet_view.get("summary", {}).get("lane_counts", {}).get("registered_worker") == 1, "worker fleet view missing registered worker lane", failures)
    require(fleet_view.get("safety", {}).get("read_only") is True, "worker fleet view must remain read-only", failures)
    require(all(lane.get("token_omitted") is True and lane.get("session_id_omitted") is True for lane in fleet_view.get("lanes", [])), "worker fleet lanes missing omission proof", failures)
    require(health.get("recommended_actions"), "worker fleet health missing recommended actions", failures)
    require(public_stale.get("token_id_omitted") is True and not public_stale.get("token_id") and public_stale.get("token_ref"), "worker stale enrollment projection leaked/missed token ref", failures)
    require((hygiene_plan.get("stale_never_seen_enrollments") or [{}])[0].get("token_id_omitted") is True, "worker hygiene plan projection missing token omission proof", failures)
    require("fixture_module_boundary_token_ref" not in json.dumps(hygiene_plan, ensure_ascii=False), "worker hygiene plan leaked token id", failures)
    require(revoked_enrollment.get("sessions_revoked") == 2 and revoked_enrollment.get("token_id_omitted") is True and not revoked_enrollment.get("token_id"), "worker revoked enrollment projection leaked token id", failures)
    require(revoke_error.get("token_id_omitted") is True and revoke_error.get("kind") == "enrollment_revoke" and not revoke_error.get("token_id"), "worker enrollment error projection leaked token id", failures)
    require(public_remote.get("token_id_omitted") is True and public_remote.get("token_ref") and not public_remote.get("token_id"), "remote worker projection leaked token id", failures)
    require(public_session.get("session_id_omitted") is True and public_session.get("session_ref") and public_session.get("parent_token_ref") and not public_session.get("session_id"), "remote session projection leaked session id", failures)
    remote_summary_serialized = json.dumps(remote_summary, ensure_ascii=False)
    require(remote_summary.get("status") == "ready" and remote_summary.get("active_sessions") == 1, "remote fleet summary failed active session aggregation", failures)
    require("fixture_remote_fleet_token_ref" not in remote_summary_serialized and "fixture_remote_session_ref" not in remote_summary_serialized, "remote fleet summary leaked raw token/session ids", failures)
    workflow_job_projection = workflow_job_public({
        "job_id": "wfjob_projection_smoke",
        "workspace_id": "local-demo",
        "workflow_type": "customer_worker",
        "status": "running",
        "template_id": "tpl_smoke",
        "adapter": "mock",
        "confirm_run": 0,
        "title": "Projection smoke",
        "input_summary": "Safe summary only.",
        "request_hash": "hash_projection_smoke",
        "result_json": '{"ok": true, "task_id": "tsk_projection_smoke"}',
        "result_task_id": "tsk_projection_smoke",
        "result_run_id": "run_projection_smoke",
        "result_artifact_id": None,
        "error_message": None,
        "created_at": "2026-06-22T00:00:00+00:00",
        "started_at": "2026-06-22T00:00:01+00:00",
        "completed_at": None,
        "updated_at": "2026-06-22T00:00:02+00:00",
    })
    workflow_job_bad_result_projection = workflow_job_public({
        "job_id": "wfjob_bad_json_smoke",
        "result_json": "{not-json",
    })
    workflow_now = workflow_job_parse_iso_datetime("2026-06-22T00:20:00+00:00")
    workflow_stuck_projection = workflow_job_stuck_projection({
        "job_id": "wfjob_stuck_projection_smoke",
        "workspace_id": "local-demo",
        "workflow_type": "customer_worker",
        "status": "running",
        "result_json": "{}",
        "updated_at": "2026-06-22T00:00:00+00:00",
    }, now_dt=workflow_now, threshold_sec=900)
    workflow_fresh_projection = workflow_job_stuck_projection({
        "job_id": "wfjob_fresh_projection_smoke",
        "status": "running",
        "result_json": "{}",
        "updated_at": "2026-06-22T00:18:00+00:00",
    }, now_dt=workflow_now, threshold_sec=900)
    workflow_not_active_response = workflow_job_not_active_response({
        "job_id": "wfjob_done_projection_smoke",
        "status": "completed",
        "result_json": "{}",
    })
    workflow_mark_failed_response = workflow_job_mark_failed_response({
        "job_id": "wfjob_marked_projection_smoke",
        "status": "failed",
        "result_json": "{}",
        "error_message": "operator recovery smoke",
    }, "wfjob_marked_projection_smoke")
    workflow_list_response = workflow_jobs_list_response(
        rows=[
            {
                "job_id": "wfjob_list_running_smoke",
                "workflow_type": "customer_worker_task",
                "status": "running",
                "adapter": "mock",
                "result_json": "{}",
                "request_hash": "hash_list_running",
            },
            {
                "job_id": "wfjob_list_completed_smoke",
                "workflow_type": "customer_task_template",
                "status": "completed",
                "adapter": "mock",
                "result_json": '{"ok": true}',
                "request_hash": "hash_list_completed",
            },
        ],
        limit=20,
        statuses={"running", "completed"},
        workflow_types={"customer_worker_task"},
        summary_rows=[{"status": "running", "c": 1}, {"status": "completed", "c": 1}],
        workflow_type_rows=[{"workflow_type": "customer_worker_task", "c": 1}, {"workflow_type": "customer_task_template", "c": 1}],
        active_count=1,
        stuck_count=0,
    )
    workflow_recovery_work_order = build_workflow_job_recovery_work_order(
        workspace_id="local-demo",
        stuck_jobs=[{
            "job_id": "wfjob_recovery_stuck_projection_smoke",
            "workflow_type": "customer_worker_task",
            "status": "running",
            "adapter": "mock",
            "title": "Workflow recovery projection smoke",
            "age_sec": 1200,
            "threshold_sec": 900,
            "stuck_reason": "workflow_job_exceeded_threshold",
            "raw_request_omitted": True,
            "token_omitted": True,
        }],
        retryable_failed_jobs=[{
            "job_id": "wfjob_recovery_retry_projection_smoke",
            "workflow_type": "customer_worker_task",
            "status": "failed",
            "adapter": "mock",
            "result_task_id": "tsk_retry_projection_smoke",
            "error_message": "smoke retryable failure",
            "raw_request_omitted": True,
            "token_omitted": True,
        }],
        receipt_rows=[],
        limit=8,
    )
    require(workflow_job_projection and workflow_job_projection.get("result", {}).get("ok") is True, "workflow job public projection result parse failed", failures)
    require(workflow_job_projection.get("raw_request_omitted") is True and workflow_job_projection.get("token_omitted") is True, "workflow job public projection omission proof missing", failures)
    require(workflow_job_bad_result_projection and workflow_job_bad_result_projection.get("result") == {}, "workflow job public projection bad JSON fallback failed", failures)
    require(workflow_stuck_projection and workflow_stuck_projection.get("age_sec") == 1200 and workflow_stuck_projection.get("stuck_reason") == "workflow_job_exceeded_threshold", "workflow job stuck projection failed", failures)
    require(workflow_stuck_projection.get("token_omitted") is True and workflow_stuck_projection.get("raw_request_omitted") is True, "workflow job stuck projection omission proof missing", failures)
    require(workflow_fresh_projection is None, "workflow job fresh projection should not be marked stuck", failures)
    require(workflow_not_active_response.get("reason") == "workflow_job_not_active" and workflow_not_active_response.get("token_omitted") is True, "workflow job not-active response failed", failures)
    require(workflow_mark_failed_response.get("marked_failed") is True and workflow_mark_failed_response.get("provider") == "agentops-workflow-job", "workflow job mark-failed response failed", failures)
    require((workflow_mark_failed_response.get("job") or {}).get("status") == "failed", "workflow job mark-failed response job projection failed", failures)
    require(workflow_list_response.get("operation") == "workflow_jobs_list" and workflow_list_response.get("count") == 2, "workflow jobs list response basic shape failed", failures)
    require((workflow_list_response.get("summary") or {}).get("active_jobs") == 1 and (workflow_list_response.get("summary") or {}).get("by_status", {}).get("running") == 1, "workflow jobs list summary failed", failures)
    require((workflow_list_response.get("filters") or {}).get("status") == ["completed", "running"], "workflow jobs list filters failed", failures)
    require(workflow_list_response.get("safety", {}).get("read_only") is True and workflow_list_response.get("token_omitted") is True, "workflow jobs list safety/token proof failed", failures)
    require("agentops workflow job-status --job-id <job_id> --wait" in (workflow_list_response.get("next_actions") or []), "workflow jobs list next action missing", failures)
    require(workflow_recovery_work_order.get("operation") == "workflow_job_recovery_work_order", "workflow job recovery work-order operation failed", failures)
    require((workflow_recovery_work_order.get("summary") or {}).get("items") == 2, "workflow job recovery work-order item summary failed", failures)
    recovery_commands = "\n".join(workflow_recovery_work_order.get("commands") or [])
    require("agentops workflow recover-job --job-id wfjob_recovery_stuck_projection_smoke --mode mark-failed" in recovery_commands, "workflow job recovery mark-failed command missing", failures)
    require("agentops workflow recover-job --job-id wfjob_recovery_retry_projection_smoke --mode retry" in recovery_commands, "workflow job recovery retry command missing", failures)
    require(workflow_recovery_work_order.get("safety", {}).get("read_only") is True and workflow_recovery_work_order.get("token_omitted") is True, "workflow job recovery work-order safety/token proof failed", failures)
    planned_task = {"task_id": "tsk_cmd_smoke_strategy", "status": "planned"}
    completed_task = {"task_id": "tsk_cmd_smoke_qa", "status": "completed"}
    require(commander_work_package_status(planned_task, None, {}) == "planned", "commander planned package status failed", failures)
    require(commander_work_package_status(completed_task, None, {"artifacts": 1}) == "ready_for_review", "commander ready-for-review status failed", failures)
    commander_item = {
        "task_id": "tsk_cmd_smoke_strategy",
        "project_id": "proj_cmd_smoke",
        "status": "planned",
        "package_status": "planned",
        "localization_gate": {"status": "recorded"},
        "coding_evidence_gate": {"status": "partial"},
        "recommended_action": commander_work_package_next_action({"task_id": "tsk_cmd_smoke_strategy", "package_status": "planned"}),
    }
    commander_readback = build_commander_work_packages_readback(
        packages=[commander_item],
        workspace_id="local-demo",
        project_id="proj_cmd_smoke",
        plan_id="cmdplan_smoke",
        status_filter="planned",
        limit=5,
        localization_artifact_type="commander_repo_map_localization",
        coding_evidence_artifact_types=["commander_patch_manifest", "commander_test_log"],
    )
    require(commander_readback.get("operation") == "work_packages_readback", "commander readback operation mismatch", failures)
    require(commander_readback.get("summary", {}).get("total") == 1, "commander readback summary total failed", failures)
    require((commander_readback.get("summary", {}).get("localization") or {}).get("coverage_percent") == 100.0, "commander localization coverage failed", failures)
    require((commander_readback.get("summary", {}).get("coding_evidence") or {}).get("partial") == 1, "commander coding evidence summary failed", failures)
    require(commander_readback.get("safety", {}).get("read_only") is True, "commander readback must stay read-only", failures)
    require(commander_readback.get("recommended_next_actions"), "commander readback missing next actions", failures)
    commander_team_board = build_commander_team_board(
        packages=[
            {**commander_item, "owner_agent_id": "agt_builder", "lane_id": "implementation", "dependencies": ["tsk_cmd_dependency"], "latest_run": {"run_id": "run_cmd_smoke", "status": "completed", "created_at": "2026-06-23T00:00:00+00:00"}},
            {"task_id": "tsk_cmd_blocked", "project_id": "proj_cmd_smoke", "package_status": "blocked", "owner_agent_id": "agt_reviewer", "coding_evidence_gate": {"status": "missing"}},
        ],
        workspace_id="local-demo",
        project_id="proj_cmd_smoke",
        plan_id="cmdplan_smoke",
    )
    require(commander_team_board.get("status") == "blocked", "commander team board blocked status failed", failures)
    require((commander_team_board.get("summary") or {}).get("total_lanes") == 2, "commander team board lane summary failed", failures)
    require((commander_team_board.get("summary") or {}).get("dependency_edges") == 1, "commander team board dependency summary failed", failures)
    require((commander_team_board.get("safety") or {}).get("read_only") is True, "commander team board must stay read-only", failures)
    commander_gates = build_commander_project_board_gates(
        closed_loop_runs=0,
        worker_status={"status": "ready", "running_workers": 0, "stuck_worker_tasks": 0},
        worker_fleet={"overall": "attention", "recommended_actions": ["agentops worker status"]},
        pending_approval_count=1,
        memory_candidate_count=0,
        approved_memory_count=2,
        synthesis_lifecycle={"status": "promotion_available", "summary": {"synthesis_artifacts": 1, "pending_reviews": 0, "promoted_delivery_artifacts": 0}, "next_actions": ["agentops commander promote-synthesis --artifact-id art_smoke"]},
        adapter_status="ready",
        adapter_summary={"recommended_adapter": "mock", "ready_adapters": ["mock"]},
        live_acceptance_status="attention",
        live_acceptance_summary={"fresh": 1, "latest_failed": 0, "latest_incomplete": 1, "missing": 0, "stale": 0},
    )
    commander_gate_ids = {gate.get("id") for gate in commander_gates}
    require({"evidence_chain", "worker_fleet_health", "approvals_pending", "memory_review", "synthesis_lifecycle", "adapter_readiness", "live_acceptance_freshness"}.issubset(commander_gate_ids), "commander project-board gates missing expected IDs", failures)
    require(commander_project_board_status(commander_gates) == "attention", "commander project-board status aggregation failed", failures)
    commander_board_actions = commander_project_board_next_actions(commander_gates, ["agentops local readiness"])
    require("agentops local readiness" in commander_board_actions, "commander project-board readiness next action merge failed", failures)
    command_center_package = {
        "task_id": "tsk_cmd_center_smoke",
        "project_id": "proj_cmd_center_smoke",
        "plan_id": "cmdplan_center_smoke",
        "lane_id": "implementation",
        "title": "Command center smoke package",
        "package_status": "planned",
        "localization_gate": {"status": "recorded"},
        "coding_evidence_gate": {"status": "missing", "artifact_types": ["commander_patch_manifest"]},
        "latest_run": {"run_id": "run_cmd_center_smoke"},
        "recommended_action": "agentops commander dispatch-package --task-id tsk_cmd_center_smoke --adapter mock",
    }
    command_center_gaps = build_command_center_commander_gaps([command_center_package])
    require(command_center_gaps and command_center_gaps[0].get("gap_type") == "coding_evidence_required", "operator command center gap aggregation failed", failures)
    require(command_center_gaps[0].get("raw_patch_omitted") is True, "operator command center gap omission proof missing", failures)
    project_rows = build_command_center_project_rows(
        commander_packages=[command_center_package],
        deliveries=[{"project_id": "proj_cmd_center_smoke", "status": "waiting_approval", "next_action": "agentops workflow delivery-board"}],
        limit=5,
    )
    require(project_rows and project_rows[0].get("pending_approvals") == 1, "operator command center project aggregation failed", failures)
    stale_refs = build_command_center_stale_worker_refs({
        "stuck_tasks": [{"task_id": "tsk_stuck_smoke", "title": "Stuck smoke", "owner_agent_id": "agt_worker_smoke", "status": "running"}],
        "stuck_workflow_job_refs": [{"job_id": "wfjob_stuck_smoke", "workflow_type": "customer_worker", "status": "running", "age_sec": 901, "stuck_reason": "threshold"}],
    }, 5)
    require(len(stale_refs) == 2 and all(item.get("token_omitted") is True for item in stale_refs), "operator command center stale worker refs failed", failures)
    require(command_center_status(
        blocked_runs=[],
        action_plan_summary={},
        commander_gaps=command_center_gaps,
        stale_worker_refs=[],
        pending_approvals=[],
        next_actions=[],
    ) == "attention", "operator command center status aggregation failed", failures)
    memory_review_projection = build_operator_run_memory_review([
        {"memory_id": "mem_candidate", "review_status": "candidate"},
        {"memory_id": "mem_approved", "review_status": "approved"},
    ])
    require(memory_review_projection.get("status") == "pending_review", "operator evidence memory review status failed", failures)
    require(memory_review_projection.get("pending_review") == 1 and memory_review_projection.get("approved") == 1, "operator evidence memory counts failed", failures)
    run_evidence_blocked = operator_run_evidence_status([
        {"id": "agent_plan_bound", "ok": False},
        {"id": "tool_evidence", "ok": True},
    ])
    run_evidence_attention = operator_run_evidence_status([
        {"id": "artifact_evidence", "ok": False},
    ])
    require(run_evidence_blocked.get("status") == "blocked", "operator evidence blocked status failed", failures)
    require(run_evidence_attention.get("status") == "attention", "operator evidence attention status failed", failures)
    evidence_summary = operator_evidence_report_summary(
        [
            {
                "status": "ready",
                "plan_evidence_manifest": {"manifest_id": "pem_smoke", "verification_pass": True},
                "approvals": {"pending": 0},
                "memory_review": {"status": "reviewed", "total": 1, "pending_review": 0},
                "worker_knowledge_retrieval": {"applicable": True, "status": "ready"},
                "worker_runtime_summary": {"applicable": True, "status": "ready"},
                "agent_plan": {"approval_required": True, "approval_decision": "approved"},
            },
            {
                "status": "attention",
                "plan_evidence_manifest": {},
                "approvals": {"pending": 1},
                "memory_review": {"status": "pending_review", "total": 1, "pending_review": 1},
                "worker_knowledge_retrieval": {"applicable": True, "status": "unavailable"},
                "worker_runtime_summary": {"applicable": True, "status": "missing"},
                "agent_plan": {"approval_required": False},
            },
        ],
        {"receipts": 2, "verified": 1, "evaluated": 1},
    )
    require(evidence_summary.get("runs") == 2 and evidence_summary.get("ready") == 1, "operator evidence report summary counts failed", failures)
    require(evidence_summary.get("missing_plan_evidence_manifests") == 1, "operator evidence report manifest summary failed", failures)
    require(evidence_summary.get("pending_memory_reviews") == 1, "operator evidence report memory summary failed", failures)
    require(evidence_summary.get("worker_runs") == 2, "operator evidence report worker run summary failed", failures)
    require(evidence_summary.get("worker_knowledge_retrieval_ready") == 1, "operator evidence report worker knowledge ready summary failed", failures)
    require(evidence_summary.get("worker_knowledge_retrieval_unavailable") == 1, "operator evidence report worker knowledge unavailable summary failed", failures)
    require(evidence_summary.get("worker_runtime_summary_ready") == 1, "operator evidence report worker runtime summary ready count failed", failures)
    require(evidence_summary.get("worker_runtime_summary_missing") == 1, "operator evidence report worker runtime summary missing count failed", failures)
    require(operator_evidence_report_status(evidence_summary) == "attention", "operator evidence report status failed", failures)
    start_gate = operator_start_check_gate(
        "adapter_preflight",
        label="Adapter preflight",
        ok=False,
        detail="readiness=missing",
        command="agentops worker preflight --adapter hermes",
    )
    require(start_gate.get("status") == "attention" and start_gate.get("token_omitted") is True, "operator start-check gate projection failed", failures)
    local_run_path = compact_start_check_local_run_path({
        "operation": "local_readiness",
        "status": "attention",
        "summary": {"recommended_adapter": "hermes"},
        "running_instance": {
            "operation": "running_instance_identity",
            "status": "current",
            "current": True,
            "server_started_after_source_mtime": True,
            "git_head_sha": "abc123",
            "git_head_short": "abc123",
            "git_branch": "codex/agent-gateway-kb-demo",
            "git_dirty_entries": 0,
            "latest_source_path": "server.py",
            "safety": {"read_only": True, "server_executes_shell": False, "token_omitted": True},
            "token_omitted": True,
        },
        "gates": [
            {
                "id": "running_instance_freshness",
                "ok": True,
                "status": "current",
                "next_action": "agentops local readiness --require-current-code",
                "token_omitted": True,
            },
        ],
        "local_run_path": [
            {
                "step_id": "preview_worker_service_control",
                "phase": "service",
                "status": "ready",
                "adapter": "hermes",
                "command": "agentops worker service-control --adapter hermes",
                "verify_command": "agentops worker status",
                "confirm_required": False,
                "service_control_preview": True,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            {
                "step_id": "dispatch_customer_task",
                "phase": "dispatch",
                "status": "attention",
                "command": "agentops workflow run-task --adapter hermes --confirm-run",
                "confirm_required": True,
                "writes_ledger": True,
                "live_execution": True,
                "server_executes_shell": False,
            },
        ],
    })
    require(local_run_path.get("operation") == "local_run_path_compact", "operator start-check local path operation failed", failures)
    require((local_run_path.get("current_code_gate") or {}).get("ok") is True, "operator start-check current-code fixture gate failed", failures)
    require((local_run_path.get("safety") or {}).get("server_executes_shell") is False, "operator start-check local path server-shell proof failed", failures)
    require(bool(local_run_path.get("service_control_preview")), "operator start-check service preview projection failed", failures)
    launch_brief = compact_start_check_launch_brief(
        {
            "operation": "operator_loop_launch_packet",
            "status": "attention",
            "workspace_id": "local-demo",
            "task_id": "tsk_smoke",
            "agent_id": "agt_smoke",
            "method": "READ PLAN RETRIEVE COMPARE VERIFY RECORD",
            "summary": {"handoff_mode": "lightweight"},
            "control_summary": {
                "status": "attention",
                "mode": "fast",
                "recommended_step": {"step_id": "runtime_doctor", "label": "Runtime doctor", "command": "agentops operator runtime-doctor --limit 8"},
                "requires_human": False,
                "requires_receipt": True,
                "policy_id": "bounded_runner_v1",
                "server_executes_shell": False,
                "copy_only": True,
            },
            "evaluation_contract": {"required_ledgers": ["runs", "tool_calls", "memories", "memory_review"]},
            "audit_contract": {"bounded_runner": {"policy_id": "bounded_runner_v1", "server_executes_shell": False}},
            "agent_plan_draft": {"risk_level": "medium", "approval_required": True},
            "execution_chain": [
                {"step_id": "runtime_doctor", "phase": "VERIFY", "label": "Runtime doctor", "step_status": "ready", "next_safe_command": "agentops operator runtime-doctor --limit 8", "receipt_required": True}
            ],
            "safety": {"read_only": True, "ledger_mutated": False, "live_execution_performed": False, "token_omitted": True},
        },
        adapter="hermes",
        local_run_path=local_run_path,
    )
    require(launch_brief.get("operation") == "operator_loop_launch_brief", "operator start-check launch brief operation failed", failures)
    require("--confirm-run" in str(launch_brief.get("live_run_command") or ""), "operator start-check launch brief live command confirm gate failed", failures)
    require((launch_brief.get("policy") or {}).get("server_executes_shell") is False, "operator start-check launch brief server-shell policy failed", failures)
    require("memory_review" in ((launch_brief.get("summary") or {}).get("required_ledgers") or []), "operator start-check launch brief memory review ledger failed", failures)
    loop_driver_entry = compact_start_check_loop_driver_entry(
        {
            "operation": "human_review_queue",
            "summary": {
                "review_items_total": 1,
                "returned_items": 1,
                "pending_approvals": 1,
                "memory_candidates": 0,
                "retrieved_pending_approvals": 1,
                "retrieved_memory_candidates": 0,
            },
            "review_items": [
                {
                    "item_id": "ap_smoke",
                    "item_type": "approval",
                    "kind": "agent_plan",
                    "status": "pending",
                    "priority": "high",
                    "task_id": "tsk_smoke",
                    "run_id": "run_smoke",
                    "next_action": "agentops approval inspect --approval-id ap_smoke",
                }
            ],
        },
        adapter="openclaw",
        limit=8,
        loop_id="loop_smoke",
        task_id="tsk_smoke",
        agent_id="agt_smoke",
    )
    require(loop_driver_entry.get("operation") == "operator_start_check_loop_driver_entry", "operator start-check loop-driver operation failed", failures)
    require("--confirm-loop" in str((loop_driver_entry.get("commands") or {}).get("confirm_loop") or ""), "operator start-check loop-driver confirm command failed", failures)
    require(((loop_driver_entry.get("review_snapshot") or {}).get("summary") or {}).get("pending_approvals") == 1, "operator start-check loop-driver review summary failed", failures)
    require((loop_driver_entry.get("safety") or {}).get("server_executes_shell") is False, "operator start-check loop-driver server-shell proof failed", failures)
    require(all(item.get("summary_omitted") is True for item in ((loop_driver_entry.get("review_snapshot") or {}).get("items") or [])), "operator start-check loop-driver item omission failed", failures)
    acceptance_packet = operator_start_check_acceptance_packet(
        status="attention",
        adapter="hermes",
        workspace_id="local-demo",
        task_id="tsk_smoke",
        agent_id="agt_smoke",
        gates=[
            operator_start_check_gate("runtime_doctor", label="Runtime doctor", ok=True, command="agentops operator runtime-doctor --limit 8"),
            operator_start_check_gate("live_product_readiness", label="Live product readiness", ok=False, command="agentops operator live-product-readiness --require-adapter hermes"),
        ],
        worker_connection_policy={"schema": "agentops-worker-connection-policy-v1", "server_executes_shell": False},
        adapter_readiness={"adapter": "hermes", "readiness": "review_required", "requires_confirm_run": True, "token_omitted": True},
        runtime_doctor={"status": "attention"},
        launch_brief=launch_brief,
        loop_driver_entry=loop_driver_entry,
        local_run_path=local_run_path,
        live_product_readiness={"product_readiness_proof": False},
        next_commands=[
            "agentops local readiness",
            "agentops worker readiness",
            "agentops operator runtime-doctor --limit 8",
            "agentops operator loop-launch-packet --brief --adapter hermes --limit 8",
            (loop_driver_entry.get("commands") or {}).get("preview"),
            (loop_driver_entry.get("commands") or {}).get("confirm_loop"),
            "agentops review queue --limit 20",
            "agentops operator action-receipts --limit 20",
        ],
    )
    require(acceptance_packet.get("operation") == "operator_local_loop_acceptance_packet", "operator start-check acceptance packet operation failed", failures)
    require((acceptance_packet.get("decision") or {}).get("can_confirm_bounded_loop") is True, "operator start-check acceptance packet loop decision failed", failures)
    require((acceptance_packet.get("decision") or {}).get("live_dispatch_allowed") is False, "operator start-check acceptance packet live dispatch gate failed", failures)
    require((acceptance_packet.get("commands") or {}).get("loop_driver_confirm") and "--confirm-loop" in str((acceptance_packet.get("commands") or {}).get("loop_driver_confirm")), "operator start-check acceptance packet confirm command failed", failures)
    require((acceptance_packet.get("commands") or {}).get("execution_mode_confirm") == "agentops operator execution-mode --adapter hermes --confirm-run", "operator start-check acceptance packet execution-mode command failed", failures)
    require((acceptance_packet.get("safety") or {}).get("server_executes_shell") is False, "operator start-check acceptance packet server-shell proof failed", failures)
    agent_loop_packet = operator_agent_loop_packet(
        adapter="hermes",
        max_steps=3,
        acceptance_gate=acceptance_packet,
        adapter_readiness={"adapter": "hermes", "readiness": "review_required", "requires_confirm_run": True, "token_omitted": True},
        launch_brief=launch_brief,
        review_snapshot=(loop_driver_entry.get("review_snapshot") or {}),
        confirm_loop=False,
    )
    admission_packet = operator_local_loop_admission_packet(
        status="attention",
        adapter="hermes",
        workspace_id="local-demo",
        task_id="tsk_smoke",
        agent_id="agt_smoke",
        acceptance_packet=acceptance_packet,
        agent_loop_packet=agent_loop_packet,
        local_run_path=local_run_path,
        adapter_readiness={"adapter": "hermes", "readiness": "review_required", "requires_confirm_run": True, "token_omitted": True},
        loop_driver_entry=loop_driver_entry,
    )
    require(admission_packet.get("operation") == "operator_local_loop_admission_packet", "operator local loop admission operation failed", failures)
    require((admission_packet.get("admission") or {}).get("method_gate_count") >= 8, "operator local loop admission method gates failed", failures)
    require(((admission_packet.get("local_deployment") or {}).get("service_control_preview") or {}).get("preview_only") is True, "operator local loop admission service preview failed", failures)
    service_install = ((admission_packet.get("local_deployment") or {}).get("service_install") or {})
    require(service_install.get("preview_only_by_default") is True, "operator local loop admission service install preview failed", failures)
    require(service_install.get("loads_service") is False, "operator local loop admission service install load boundary failed", failures)
    require(service_install.get("server_executes_shell") is False, "operator local loop admission service install server-shell proof failed", failures)
    require("--confirm-run" in str(service_install.get("preview_command") or ""), "operator local loop admission service install confirm-run failed", failures)
    require("--confirm-install" in str(service_install.get("confirm_command") or ""), "operator local loop admission service install confirm command failed", failures)
    require((admission_packet.get("safety") or {}).get("server_executes_shell") is False, "operator local loop admission server-shell proof failed", failures)
    loop_control = operator_loop_control_summary_from_handoff(
        {
            "status": "attention",
            "selected_item": {
                "gate_id": "runtime_doctor",
                "gate_label": "Runtime doctor",
                "gate_status": "attention",
                "verify_command": "agentops operator loop-control --limit 5",
                "receipt_verify_record_command": "agentops operator action-receipt record --source runtime_doctor",
                "source": "operator_loop_control.runtime_doctor",
                "action_signature": "sig_smoke",
            },
            "confirm_command": "agentops operator advance-loop --fast-control --confirm-advance --limit 5",
            "preview_command": "agentops operator advance-loop --fast-control --limit 5",
            "policy": {"policy_id": "bounded_runner_v1"},
            "safety": {"server_shell_execution": False},
        },
        {"status": "attention"},
        loop_id="loop_smoke",
    )
    loop_control_gate = operator_loop_control_gate(loop_control)
    require(loop_control.get("status") == "attention", "operator loop-control selected status failed", failures)
    require(loop_control.get("requires_human") is True and loop_control.get("copy_only") is True, "operator loop-control selected safety flags failed", failures)
    require(loop_control.get("selected_gate") == "runtime_doctor", "operator loop-control selected gate failed", failures)
    require((loop_control.get("recommended_step") or {}).get("receipt_required") is True, "operator loop-control receipt requirement failed", failures)
    require(loop_control_gate.get("status") == "attention", "operator loop-control gate status failed", failures)
    require(loop_control_gate.get("requires_receipt") is True, "operator loop-control gate receipt flag failed", failures)
    require(loop_control_gate.get("server_executes_shell") is False, "operator loop-control gate server-shell proof failed", failures)
    require(loop_control_gate.get("refresh_cache_required_after_receipt") is True, "operator loop-control cache refresh flag failed", failures)
    ready_loop_control = operator_loop_control_summary_from_handoff(
        {
            "status": "ready",
            "preview_command": "agentops operator advance-loop --limit 5",
        },
        {"status": "ready"},
    )
    require(ready_loop_control.get("status") == "ready", "operator loop-control ready status failed", failures)
    require(ready_loop_control.get("mode") == "read_only_copy", "operator loop-control ready mode failed", failures)
    require(ready_loop_control.get("requires_human") is False, "operator loop-control ready human flag failed", failures)
    receipt_public = operator_action_receipt_public({
        "audit_id": "audit_receipt_smoke",
        "actor_id": "usr_smoke",
        "entity_id": "oar_smoke",
        "metadata_json": json.dumps({
            "workspace_id": "local-demo",
            "status": "verified",
            "source": "advance_loop:runtime_doctor",
            "action_id": "action_smoke",
            "action_signature": "sig_receipt_smoke",
            "action_command": "agentops operator runtime-doctor",
            "action_hash": "hash_action_smoke",
            "verify_command": "agentops operator loop-control",
            "verify_hash": "hash_verify_smoke",
            "result_summary": "Receipt verified.",
        }, sort_keys=True),
        "tamper_chain_hash": "tamper_receipt_smoke",
        "created_at": "2026-06-23T00:00:00+00:00",
    })
    evaluation_public = operator_action_evaluation_public({
        "evaluation_id": "oae_smoke",
        "receipt_id": "oar_smoke",
        "workspace_id": "local-demo",
        "action_id": "action_smoke",
        "action_signature": "sig_receipt_smoke",
        "action_hash": "hash_action_smoke",
        "verify_hash": "hash_verify_smoke",
        "source": "advance_loop:runtime_doctor",
        "evaluator_type": "rule",
        "score": "1.0",
        "pass_fail": "pass",
        "rubric_json": '{"token_omitted": true, "receipt_status": "verified"}',
        "notes": "Verified.",
        "created_at": "2026-06-23T00:00:01+00:00",
    })
    readback_public = operator_control_readback_public({
        "entity_id": "oar_smoke",
        "metadata_json": json.dumps({
            "readback_id": "ocr_smoke",
            "receipt_id": "oar_smoke",
            "workspace_id": "local-demo",
            "action_id": "action_smoke",
            "action_signature": "sig_receipt_smoke",
            "source": "operator.action_queue_control_readback",
            "control_readback": {"before": {"selected_gate": "runtime_doctor"}, "after": {"selected_gate": "none"}},
        }, sort_keys=True),
        "tamper_chain_hash": "tamper_readback_smoke",
        "created_at": "2026-06-23T00:00:02+00:00",
    })
    require(receipt_public.get("receipt_id") == "oar_smoke", "operator receipt public id failed", failures)
    require(receipt_public.get("status") == "verified" and receipt_public.get("token_omitted") is True, "operator receipt public status/omission failed", failures)
    require(operator_receipt_requires_control_readback(receipt_public) is True, "operator receipt control-readback requirement failed", failures)
    require(operator_receipt_requires_control_readback({"source": "operator_action_queue"}) is False, "operator receipt control-readback false-positive failed", failures)
    require(evaluation_public and evaluation_public.get("score") == 1.0 and (evaluation_public.get("rubric") or {}).get("receipt_status") == "verified", "operator receipt evaluation projection failed", failures)
    require(readback_public and readback_public.get("readback_id") == "ocr_smoke" and readback_public.get("tamper_chain_hash") == "tamper_readback_smoke", "operator control readback projection failed", failures)
    require(((readback_public.get("control_readback") or {}).get("before") or {}).get("selected_gate") == "runtime_doctor", "operator control readback payload projection failed", failures)
    case_public = evaluation_case_candidate_public({
        "case_id": "case_smoke",
        "rubric_json": json.dumps({"quality_gate": "pass"}, ensure_ascii=False, sort_keys=True),
    })
    case_run_public = evaluation_case_run_public({
        "case_run_id": "ecr_smoke",
        "checks_json": "{not-valid-json",
    })
    require(case_public.get("rubric", {}).get("quality_gate") == "pass" and case_public.get("token_omitted") is True, "evaluation case candidate projection failed", failures)
    require(case_run_public.get("checks") == {} and case_run_public.get("token_omitted") is True, "evaluation case run invalid-json fallback failed", failures)

    command = "python3 scripts/module_boundary_smoke.py"
    require(command in ci_text, "module boundary smoke missing from CI", failures)
    require(command in release_text, "module boundary smoke missing from release evidence", failures)
    require("P1-05" in backlog_text and "module_boundary_smoke.py" in backlog_text, "backlog missing P1-05 module boundary evidence", failures)
    require("agentops_mis_runtime/capabilities.py" in plan_text, "module boundary plan missing runtime capability module", failures)
    require("agentops_mis_runtime/connectors.py" in plan_text, "module boundary plan missing runtime connector module", failures)
    require("agentops_mis_runtime/trust.py" in plan_text, "module boundary plan missing runtime trust module", failures)
    require("agentops_mis_core/read_model_cache.py" in plan_text, "module boundary plan missing read model cache module", failures)
    require("agentops_mis_core/approval_wall.py" in plan_text, "module boundary plan missing approval wall module", failures)
    require("agentops_mis_core/agent_plans.py" in plan_text, "module boundary plan missing agent plans module", failures)
    require("agentops_mis_core/evaluation_cases.py" in plan_text, "module boundary plan missing evaluation cases module", failures)
    require("agentops_mis_core/commander_work_packages.py" in plan_text, "module boundary plan missing commander work packages module", failures)
    require("agentops_mis_core/operator_command_center.py" in plan_text, "module boundary plan missing operator command center module", failures)
    require("agentops_mis_core/operator_evidence.py" in plan_text, "module boundary plan missing operator evidence module", failures)
    require("agentops_mis_core/operator_start_check.py" in plan_text, "module boundary plan missing operator start-check module", failures)
    require("agentops_mis_core/operator_loop_control.py" in plan_text, "module boundary plan missing operator loop-control module", failures)
    require("agentops_mis_core/operator_receipts.py" in plan_text, "module boundary plan missing operator receipts module", failures)
    require("agentops_mis_core/worker_fleet.py" in plan_text, "module boundary plan missing worker fleet module", failures)
    require("agentops_mis_core/workflow_jobs.py" in plan_text, "module boundary plan missing workflow jobs module", failures)

    output = {
        "ok": not failures,
        "operation": "module_boundary_smoke",
        "boundary": "agentops_mis_runtime.capabilities+connectors+trust + agentops_mis_core.read_model_cache+approval_wall+agent_plans+gateway_runs+evaluation_cases+commander_work_packages+operator_command_center+operator_evidence+operator_start_check+operator_loop_control+operator_receipts+worker_fleet+workflow_jobs",
        "server_line_count": len(server_text.splitlines()),
        "module_imports": {
            "capabilities": sorted(imports),
            "connectors": sorted(connector_imports),
            "trust": sorted(trust_imports),
            "read_model_cache": sorted(read_model_cache_imports),
            "approval_wall": sorted(approval_wall_imports),
            "agent_plans": sorted(agent_plan_imports),
            "gateway_runs": sorted(gateway_run_imports),
            "evaluation_cases": sorted(evaluation_case_imports),
            "commander_work_packages": sorted(commander_work_package_imports),
            "operator_command_center": sorted(operator_command_center_imports),
            "operator_evidence": sorted(operator_evidence_imports),
            "operator_start_check": sorted(operator_start_check_imports),
            "operator_loop_control": sorted(operator_loop_control_imports),
            "operator_receipts": sorted(operator_receipt_imports),
            "worker_fleet": sorted(worker_fleet_imports),
            "workflow_jobs": sorted(workflow_job_imports),
        },
        "live_execution_performed": False,
        "ledger_mutated": False,
        "token_omitted": True,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
