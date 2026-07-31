#!/usr/bin/env python3
"""Verify the release evidence packet can report RC SHA, CI status and tests."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from github_ci_evidence import ci_status as shared_ci_status


ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "V1_5_MERGE_READINESS_CHECKLIST.md"
PACKET_DOC = ROOT / "docs" / "RELEASE_EVIDENCE_PACKET.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]+"),
]

TEST_COMMANDS = [
    {
        "id": "syntax_diff",
        "command": "python3 -m py_compile server.py agentops_mis_cli/*.py agentops_mis_core/*.py agentops_mis_runtime/*.py scripts/*.py && git diff --check",
        "summary": "Python syntax, CLI/script importability and whitespace diff hygiene.",
        "ci_step": "Syntax and diff checks",
    },
    {
        "id": "release_branch_control",
        "command": "python3 scripts/release_branch_control_smoke.py",
        "summary": "Branch identity, reviewable history, unsafe tracked runtime files and upstream drift.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "release_freeze_protocol",
        "command": "python3 scripts/release_freeze_protocol_smoke.py",
        "summary": "Hardening freeze protocol, CI-backed release gates and remote required-check evidence.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "github_ci_evidence",
        "command": "python3 scripts/github_ci_evidence_smoke.py",
        "summary": "Offline contract for shared GitHub CI evidence parsing and public HTML fallback exact-head safety.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "clean_machine_rc",
        "command": "python3 scripts/clean_machine_rc_smoke.py",
        "summary": "Temporary clean clone RC chain covering installable CLIs, reset server, safe closure and delivery-board evidence.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "run_local_stack",
        "command": "python3 scripts/run_local_stack_smoke.py",
        "summary": "One-command local stack starts an isolated backend and safe worker, preserves user CLI config, and fails closed before unconfirmed Hermes/OpenClaw workers.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_owner_browser_handoff",
        "command": "python3 scripts/private_host_owner_browser_handoff_smoke.py",
        "summary": "Setup-code-authorized Owner pairing handed to the local browser through scrubbed fragment state without argv/output/audit disclosure.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_auth_workspace_ui",
        "command": "python3 scripts/private_host_auth_workspace_ui_smoke.py",
        "summary": "Locked AppShell authentication UI, bilingual administrator setup/login/recovery, theme controls and initialization-authority browser handoff projection.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "human_password_recovery",
        "command": "python3 scripts/human_password_recovery_smoke.py",
        "summary": "Desktop-authority plus loopback-gated one-time password recovery, old-session revocation, replay rejection and credential/audit omission against an isolated database.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_service_worker_presence",
        "command": "python3 scripts/private_host_service_worker_presence_smoke.py",
        "summary": "Independent service Workers contribute heartbeat-confirmed execution capacity without claiming Host process verification or executing a Runtime.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "human_session_management",
        "command": "python3 scripts/human_session_management_smoke.py",
        "summary": "Owner-only browser Session list/revoke, current-Session preservation, role/CSRF/account isolation, safe references, audit omission and existing Workspace account UI.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_worker_machine_read",
        "command": "python3 scripts/private_host_worker_machine_read_smoke.py",
        "summary": "Private Host keeps browser Worker routes behind Human Session while Host CLI read commands use a separate machine-only Gateway path; bound Agent tokens/sessions fail closed and reads do not mutate ledger or usage state.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_worker_ownership",
        "command": "python3 scripts/private_host_worker_ownership_smoke.py",
        "summary": "Private Host refuses duplicate same-adapter local Workers, preserves explicit external Worker ownership through --no-workers, and never terminates an existing process automatically.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_rc_status",
        "command": "python3 scripts/private_host_rc_status_smoke.py",
        "summary": "Current public Private Host prerelease provenance stays aligned while Owner, physical second-device, current-package Runtime and another-Mac gates remain explicitly open.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_background_service",
        "command": "python3 scripts/private_host_background_service_smoke.py",
        "summary": "Preview-first host-only macOS LaunchAgent install/check/control/remove lifecycle with explicit confirmation, exact ownership and no Worker or credential material.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "private_host_macos_launcher",
        "command": "python3 scripts/private_host_macos_launcher_smoke.py",
        "summary": "Managed user-level macOS launcher installs in a clean HOME, pins the validated Python runtime, initializes once, starts with no live workers, opens the existing Workspace, and uninstalls without deleting Host data.",
        "ci_step": "Private Host bundle smoke",
    },
    {
        "id": "release_evidence_packet",
        "command": "python3 scripts/release_evidence_packet_smoke.py",
        "summary": "Runtime RC SHA, CI link/status, canonical test list and release-packet boundaries.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "merge_readiness_status",
        "command": "python3 scripts/merge_readiness_status_smoke.py",
        "summary": "Checklist state consistency, explicit blockers and strict READY_TO_MERGE guard.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "v1_5_product_closure_evidence",
        "command": "python3 scripts/v1_5_product_closure_evidence_smoke.py",
        "summary": "Static CI coverage matrix for v1.5 closure items; not live product-readiness proof.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "enrollment_hosted_policy_ui",
        "command": "python3 scripts/enrollment_hosted_policy_ui_smoke.py",
        "summary": "Deployment-aware Agent Gateway enrollment policy: local low-risk observer direct-create versus hosted/shared approval-gated token issue, plus UI contract.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "agent_gateway_scope_effects_ui",
        "command": "python3 scripts/agent_gateway_scope_effects_ui_smoke.py",
        "summary": "Agent Gateway enrollment UI explains selected scope effects, worker-loop viability and fail-closed HTTP 403 RBAC.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "v1_5_current_code_product_evidence",
        "command": "python3 scripts/v1_5_current_code_product_evidence.py --base-url http://127.0.0.1:<current-code-port> --db-path /tmp/<current-code-agentops>.db --confirm-live",
        "summary": "Manual current-code product evidence orchestrator: rebuilds knowledge, runs Commander synthesis, executes confirmed Hermes/OpenClaw customer-worker acceptance, verifies live readiness readback, exercises remote/scoped worker fallback, and finishes with non-live local acceptance.",
        "ci_step": "manual-live-local",
        "manual_only": True,
    },
    {
        "id": "customer_worker_real_runtime_acceptance",
        "command": "python3 scripts/customer_worker_real_runtime_acceptance.py --confirm-live --adapter hermes --adapter openclaw",
        "summary": "Manual live product-readiness dogfood gate for real Hermes/OpenClaw customer-worker execution; intentionally excluded from CI.",
        "ci_step": "manual-live-local",
        "manual_only": True,
    },
    {
        "id": "local_harness_governed_live_acceptance",
        "command": "python3 scripts/local_harness_governed_live_acceptance.py --adapter openclaw --confirm-live --auto-service-closure",
        "summary": "Manual live governed local harness runner: consumes local-harness-proof launch packet, executes confirmed customer-worker task, records scoped action receipt, and reads it back with exact filters.",
        "ci_step": "manual-live-local",
        "manual_only": True,
    },
    {
        "id": "local_harness_governed_live_acceptance_preview",
        "command": "python3 scripts/local_harness_governed_live_acceptance_smoke.py",
        "summary": "CI-safe preview smoke for governed live harness runner; proves default read-only mode and exact receipt readback filters without calling Hermes/OpenClaw.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "v1_5_live_product_readiness",
        "command": "python3 scripts/v1_5_live_product_readiness_smoke.py --require-adapter hermes --require-adapter openclaw",
        "summary": "Read-only manual live ledger proof that fresh Hermes/OpenClaw customer-worker runs have full run/tool/eval/runtime/audit/artifact/memory/approval/plan-evidence coverage.",
        "ci_step": "manual-live-local",
        "manual_only": True,
    },
    {
        "id": "hermes_http_error_redaction",
        "command": "python3 scripts/hermes_http_error_redaction_smoke.py",
        "summary": "Loopback failure smoke proving Hermes HTTP status and body hash remain diagnosable while the upstream response body stays omitted.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "customer_worker_hermes_retry_gateway",
        "command": "python3 scripts/customer_worker_hermes_retry_gateway_smoke.py",
        "summary": "Deterministic loopback Hermes-compatible gateway smoke proving customer-worker retry metadata is wired through the real adapter path after a local service receipt/readback fixture; not live product-readiness proof.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "redaction_policy",
        "command": "python3 scripts/redaction_policy_smoke.py",
        "summary": "Shared redaction policy preserves safe operational evidence while omitting credential-like values.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "sqlite_pragmas",
        "command": "python3 scripts/sqlite_pragmas_smoke.py",
        "summary": "SQLite connections enable local reliability pragmas including foreign keys, WAL, busy timeout and synchronous=NORMAL.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "module_boundary",
        "command": "python3 scripts/module_boundary_smoke.py",
        "summary": "P1-05 strangler boundary gate for extracted runtime capability, connector registry/refresh projection, trust state, read-model cache, Approval Wall resume/waiting/route blocked/access/prepare-response/prepared-action-decision/high-risk-toolcall-required/risky-tool-registry/external-side-effect-intent/resume-success/provider-result gates, Agent Plan approval-decision/create-status/bound-approval/transition-error/run-start gate/rebind/success response projections, run-start binding comparison plus contract/hash/path-scope/verification-result/pending-approval/approval-run helpers, Agent Gateway run-heartbeat update projection, Evaluation Case public projections, worker fleet remote/session/hygiene projections, workflow-job public/list/stuck/recovery response projections, Commander and Operator command-center aggregation helpers, Operator evidence-report memory/status/summary plus worker retrieval/runtime-summary projections, Operator start-check gate/local-run-path/launch-brief/loop-driver-entry/acceptance-packet/agent-loop-packet/local-loop-admission projections, Operator loop-control summary/gate projection, and Operator receipt/evaluation/control-readback public projection.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "read_model_cache",
        "command": "python3 scripts/read_model_cache_smoke.py",
        "summary": "Read-model cache scoped keying, hit/miss/bypass behavior, ledger read-only proof and token omission.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "open_source_adoption_boundary",
        "command": "python3 scripts/open_source_adoption_boundary_smoke.py",
        "summary": "Static gate proving open-source references cannot replace first-party MIS authority objects.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "open_source_adoption_packet_spec",
        "command": "python3 scripts/open_source_adoption_packet_spec_smoke.py",
        "summary": "Executable adoption packet contract for GitHub branches, local experiments, UI bases, runtime adapters and Harness-style references before merge or rejection.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "open_source_adoption_packet_catalog",
        "command": "python3 scripts/open_source_adoption_packet_catalog_smoke.py",
        "summary": "Concrete adoption packet catalog validation, including the Star Office UI read-model packet and its no-authority/no-asset-import boundary.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "open_source_mainline_governance",
        "command": "python3 scripts/open_source_mainline_governance_smoke.py",
        "summary": "Open-source/experiment branch merge governance plus Harness engineering research boundary.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "harness_engineering_control_plane",
        "command": "python3 scripts/harness_engineering_control_plane_smoke.py",
        "summary": "Harness engineering control-plane constraints: typed agent packets, first-party MIS authority, policy-style gates and release-wired static safety boundaries.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "harness_engineering_execution_constraints",
        "command": "python3 scripts/harness_engineering_execution_constraints_smoke.py",
        "summary": "Harness engineering execution constraints: work packet fields, READ/PLAN/RETRIEVE/COMPARE/EXECUTE/VERIFY/RECORD gates, real-runtime proof limits, and async lane contract.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "harness_engineering_product_constraints",
        "command": "python3 scripts/harness_engineering_product_constraints_smoke.py",
        "summary": "Harness engineering product constraints: current Worker Agent, Policy as Code, Open Source and Adaptive Auto-Harness research translated into MIS authority, policy, approval, real-runtime, open-source-base and async-commander gates.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "harness_style_agentops_operating_spec",
        "command": "python3 scripts/harness_style_agentops_operating_spec_smoke.py",
        "summary": "Harness-style AgentOps operating spec: solo/local company mode, dogfood engineering mode, remote worker mode, async lane fields, real-runtime proof limits, UI-vs-agent boundaries and product-slice scorecard.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "local_task_harness",
        "command": "python3 scripts/local_task_harness_smoke.py",
        "summary": "Plan-only local task harness packet for mock/Hermes/OpenClaw that wraps `agentops workflow run-task`, preserves live confirm gates, and emits no ledger/live/runtime side effects in CI.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "agent_task_harness_engineering_spec",
        "command": "python3 scripts/agent_task_harness_engineering_spec_smoke.py",
        "summary": "Agent task harness engineering spec: work-packet fields, intake-to-report phases, real-runtime vs mock claim limits, trajectory redaction, scorecard fields, and local harness proof next slice.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "local_harness_proof_readback",
        "command": "python3 scripts/local_harness_proof_readback_smoke.py",
        "summary": "Server-backed local task harness proof readback for mock fallback and real-runtime adapter ledger evidence without executing live runtimes or mutating the ledger.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "spatial_research_semantic_contract",
        "command": "python3 scripts/spatial_research_semantic_contract_smoke.py",
        "summary": "Spatial Research District semantic objects cover every Pixel Office zone and route back to formal AgentOps MIS authority pages without third-party assets or second-ledger state.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "spatial_zone_authority_readback",
        "command": "python3 scripts/spatial_zone_authority_readback_smoke.py",
        "summary": "Pixel Office Zone Inspector displays semantic authority class, formal route and visual non-ledger boundary from the Research District contract.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_config_boundary",
        "command": "python3 scripts/commercial_config_boundary_smoke.py",
        "summary": "Commercial config examples stay safe-by-default: Free Local, no billing calls, no hosted mode, no destructive cleanup, and no secret material.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_config_status",
        "command": "python3 scripts/commercial_config_status_smoke.py",
        "summary": "Read-only commercial config status API/CLI reports entitlement and retention gates without billing, cleanup, live execution, raw config, or token exposure.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_config_status_ui",
        "command": "python3 scripts/commercial_config_status_ui_smoke.py",
        "summary": "Admin Connectors renders commercial entitlement/retention gates with read-only, no billing, no cleanup, raw-config-omitted and token-omitted safety readback.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_config_operator_action_ui",
        "command": "python3 scripts/commercial_config_operator_action_ui_smoke.py",
        "summary": "Admin Connectors points operators to the safe commercial config CLI readback and evidence doc without billing, cleanup, hosted-readiness or live-runtime action.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_evidence_packet_index",
        "command": "python3 scripts/commercial_evidence_packet_index_smoke.py",
        "summary": "Read-only commercial evidence packet index stays static/current-source derived without billing, cleanup, hosted-readiness, live runtime execution, raw logs, prompts, responses, DBs, or secrets.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_current_evidence_status",
        "command": "python3 scripts/commercial_current_evidence_status_smoke.py",
        "summary": "Read-only current commercial evidence status reports git/checklist/CI command-manifest posture without DB, server, live runtime, billing, cleanup, hosted or Postgres claims.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_handoff_status",
        "command": "python3 scripts/commercial_handoff_status_smoke.py",
        "summary": "Read-only commercial handoff status reports clean-room lane and packet states without DB, server, live runtime, billing, cleanup, hosted, Postgres or direct PR #22 merge claims.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_promotion_preflight",
        "command": "python3 scripts/commercial_promotion_preflight_smoke.py",
        "summary": "Read-only commercial promotion preflight reports clean branch, exact-head CI and safety gate readiness without DB, server, live runtime, billing, cleanup, hosted, Postgres or PR #22 contents.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_promotion_packet",
        "command": "python3 scripts/commercial_promotion_packet_smoke.py",
        "summary": "Read-only commercial promotion packet bundles current-source preflight, release and handoff evidence references without DB, server, live runtime, billing, cleanup, hosted, Postgres or PR #22 contents.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_receipt_plan",
        "command": "python3 scripts/commercial_receipt_plan_smoke.py",
        "summary": "Read-only commercial receipt plan defines human review and prepared-action requirements before risky commercial changes without DB, server, receipt recording, live runtime, billing, cleanup, hosted, Postgres or PR #22 contents.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_receipt_recording",
        "command": "python3 scripts/commercial_receipt_recording_smoke.py",
        "summary": "Read-only commercial receipt recording packet materializes preview-only receipt requests and hashes without DB, server, ledger mutation, live runtime, billing, cleanup, hosted, Postgres or PR #22 contents.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_rerun_bundle_preview",
        "command": "python3 scripts/commercial_rerun_bundle_preview_smoke.py",
        "summary": "Read-only commercial rerun bundle preview lists deterministic reproduction commands without executing the bundle, reading DBs, mutating ledgers, running live runtimes, billing, cleanup, hosted, Postgres or PR #22 contents.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_confirmed_receipt_recording",
        "command": "python3 scripts/commercial_confirmed_receipt_recording_smoke.py",
        "summary": "Isolated commercial confirmed receipt recording proves explicit operator receipts can be written to a temporary SQLite ledger without executing billing, cleanup, hosted, Postgres, Hermes/OpenClaw or live runtime actions.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_receipt_prepared_action_binding",
        "command": "python3 scripts/commercial_receipt_prepared_action_binding_smoke.py",
        "summary": "Isolated commercial receipt binding proves a confirmed operator receipt can be attached to an exact Approval Wall prepared-action hash without executing billing, cleanup, hosted, Postgres, Hermes/OpenClaw or live runtime actions.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_prepared_action_execution_receipt",
        "command": "python3 scripts/commercial_prepared_action_execution_receipt_smoke.py",
        "summary": "Isolated commercial execution receipt proves a post-action receipt can require a consumed Approval Wall prepared action and read back its provider side-effect id without executing billing, cleanup, hosted, Postgres, Hermes/OpenClaw or live runtime actions.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commercial_migration_breakdown",
        "command": "python3 scripts/commercial_migration_breakdown_smoke.py",
        "summary": "Clean-room breakdown plan for decomposing the large conflicting commercial migration branch into reviewable product slices.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "external_connector_runtime_inventory",
        "command": "python3 scripts/external_connector_runtime_inventory_smoke.py",
        "summary": "High-risk external connector/runtime prepared-action inventory and guard coverage.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "sqlite_concurrency",
        "command": "python3 scripts/sqlite_concurrency_smoke.py",
        "summary": "SQLite pragma, concurrent read/write and long-subprocess transaction safety gate.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "secret_scan",
        "command": "python3 scripts/secret_scan_smoke.py",
        "summary": "Tracked files remain free of real credentials and token-like material.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "license_provenance",
        "command": "python3 scripts/license_provenance_smoke.py",
        "summary": "License, third-party notices, SBOM and Pixel Office asset boundary.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "public_claims",
        "command": "python3 scripts/public_claims_release_gate_smoke.py",
        "summary": "Public claims stay aligned with tested local-MVP / NOT_READY behavior.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "redaction_fuzz",
        "command": "python3 scripts/redaction_fuzz_smoke.py",
        "summary": "Shared redaction contract covers server, CLI and worker redactors against common token/API-key shapes while preserving safe operational IDs.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "shared_mode_local_write_guard",
        "command": "python3 scripts/shared_mode_local_write_guard_smoke.py",
        "summary": "Shared/production deployment mode rejects local browser write APIs without admin credentials while keeping Agent Gateway scoped writes separate.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "automatic_plan_evidence_workflow",
        "command": "python3 scripts/automatic_plan_evidence_workflow_smoke.py",
        "summary": "Worker/customer-worker paths automatically create, verify and consume plan-evidence manifests before delivery evidence is claimed.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "migration_rollback",
        "command": "python3 scripts/migration_rollback_smoke.py",
        "summary": "SQLite migration preview, backup, restore and rollback evidence.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "memory_workspace_authority_migration",
        "command": "python3 scripts/memory_workspace_authority_migration_smoke.py",
        "summary": "Legacy task-bound memories inherit task workspace authority exactly once while taskless legacy rows remain conservatively local-demo.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "approval_semantics_boundary",
        "command": "python3 scripts/approval_semantics_boundary_smoke.py",
        "summary": "Approval semantics keep role separation and do not collapse generic ledger approval into exact prepared-action execution claims.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "agent_plan_quality",
        "command": "python3 scripts/agent_plan_quality_smoke.py",
        "summary": "Agent Plan quality rubric scores READ/PLAN/RETRIEVE/COMPARE/EXECUTE/VERIFY/RECORD coverage, authority diversity, governance and evidence hygiene without raw prompt or response storage.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "retrieval_quality",
        "command": "python3 scripts/knowledge_retrieval_quality_smoke.py",
        "summary": "Bilingual knowledge retrieval quality baseline.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "worker_knowledge_evidence_consumption",
        "command": "python3 scripts/worker_knowledge_evidence_consumption_smoke.py",
        "summary": "Agent worker consumes compact versioned Knowledge and same-workspace approved project/org Memory before adapter execution while candidate and cross-workspace Memory remain excluded; MIS records bounded hashes, identifiers and metrics only.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "worker_intake_auto_plan",
        "command": "python3 scripts/worker_intake_auto_plan_smoke.py",
        "summary": "Agent worker can safely create or verify a READ/PLAN/RETRIEVE/COMPARE Agent Plan for its own low/medium-risk intake-blocked task without starting runs or executing live runtimes.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "worker_prompt_profile",
        "command": "python3 scripts/worker_prompt_profile_smoke.py",
        "summary": "Agent worker selects task-to-runtime prompt profiles and records profile id/version/hash in tool, evaluation, audit and worker result evidence without raw prompt storage.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "work_delivery_graph_readback",
        "command": "python3 scripts/work_delivery_graph_readback_smoke.py",
        "summary": "CLI/API read-only work delivery graph over MIS ledgers: task, agent, plan, run, tools, runtime events, evaluations, artifacts, audit and plan evidence.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_repo_map",
        "command": "python3 scripts/commander_repo_map_smoke.py",
        "summary": "Commander repo-map localization returns deterministic file/symbol candidates with provenance and redacted snippets.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_coding_project_template",
        "command": "python3 scripts/commander_coding_project_template_smoke.py",
        "summary": "Commander local coding project template links WorkPackage, worktree, patch, verifier and merge-gate evidence without mutating the ledger.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_coding_workspace",
        "command": "python3 scripts/commander_coding_workspace_smoke.py",
        "summary": "Commander coding workspace loop creates an isolated git worktree, collects patch/test/verifier evidence, records MIS artifacts and cleans branch/worktree residue.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_lane_packet",
        "command": "python3 scripts/commander_lane_packet_smoke.py",
        "summary": "Commander lane packets expose machine-readable objective, owner, runtime, phase, evidence refs, next command and safety gates through API/CLI without mutating the ledger.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_command_center",
        "command": "python3 scripts/operator_command_center_smoke.py",
        "summary": "Unified operator command-center BFF covers projects, blockers, approvals, deliveries, stale workers, Commander coding gates, and prioritized next actions.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_work_package_plan",
        "command": "python3 scripts/commander_work_package_plan_smoke.py",
        "summary": "Commander work-package planning creates task-bound repo-map localization artifacts without live execution.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "commander_work_package_dispatch",
        "command": "python3 scripts/commander_work_package_dispatch_smoke.py",
        "summary": "Commander targeted dispatch preserves/restores repo-map localization artifacts before worker execution.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "commander_work_package_batch_dispatch",
        "command": "python3 scripts/commander_work_package_batch_dispatch_smoke.py",
        "summary": "Commander batch dispatch queues planned work packages as async workflow jobs, records completed mock worker evidence, and fails closed before live Hermes/OpenClaw jobs without confirmation.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "commander_integration_inbox",
        "command": "python3 scripts/commander_integration_inbox_smoke.py --base-url \"$AGENTOPS_BASE_URL\" --db-path \"$AGENTOPS_DB_PATH\"",
        "summary": "Commander Integration Inbox returns read-only async-lane review items with structured merge/recovery/memory decisions and no auto-apply behavior.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "local_coding_project_template",
        "command": "python3 scripts/local_coding_project_template_smoke.py",
        "summary": "Server-backed Local Coding Project template creates Commander packages, previews coding workspace, dispatches mock worker, and records patch/test/verifier/merge evidence.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "responsiveness",
        "command": "python3 scripts/ai_employees_responsiveness_smoke.py",
        "summary": "Agent command-center API latency and fan-out budget.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_action_queue_ui",
        "command": "python3 scripts/operator_action_queue_ui_smoke.py",
        "summary": "AI Employees operator queue, loop-control readback and live API UI contract.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "worker_console_ui",
        "command": "python3 scripts/worker_console_ui_smoke.py",
        "summary": "Dedicated Worker Console route, sidebar/home entry, live Worker API wiring, dispatch/daemon controls, ledger links and live-confirmation safety wall.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "customer_dispatch_desk_ui",
        "command": "python3 scripts/customer_dispatch_desk_ui_smoke.py",
        "summary": "First-class customer Dispatch Desk route, navigation, live workflow API wiring, template/worker dispatch controls, delivery board and explicit live-confirm/prepared-action safety language.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "commander_team_board_ui",
        "command": "python3 scripts/commander_team_board_ui_smoke.py",
        "summary": "AI Employees renders the scoped Commander team board with workflow-job evidence and safe project-board readback.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_advance_loop",
        "command": "python3 scripts/operator_advance_loop_smoke.py",
        "summary": "Bounded advance-loop runner records receipts plus persisted control readback evidence.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_control",
        "command": "python3 scripts/operator_loop_control_smoke.py",
        "summary": "Lightweight read-only loop-control API/CLI plus fast advance-loop readback for real local ledgers.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_bootstrap",
        "command": "python3 scripts/operator_loop_bootstrap_smoke.py",
        "summary": "Hermes/OpenClaw local loop bootstrap packet orders service install, service-check, service closure, optional service activation, loop-driver auto service closure, stale-server recovery, endpoint-timeout fast fallback, and explicit fast startup commands without mutating ledgers, loading services, executing server shell, or running live adapters.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_bootstrap_api_ui",
        "command": "python3 scripts/operator_loop_bootstrap_api_ui_smoke.py",
        "summary": "Read-only loop-bootstrap API plus AI Employees panel exposes Hermes/OpenClaw startup packets without mutating ledgers, running service-check, loading services, executing server shell, or leaking tokens.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_driver",
        "command": "python3 scripts/operator_loop_driver_smoke.py",
        "summary": "Hermes/OpenClaw/Codex loop driver consumes start-check acceptance gates and compact work-packet decisions before confirmed bounded advance, returns machine-readable agent loop packets, previews compact launch briefs, can explicitly auto-close local service readback receipts before bounded advance, and confirms bounded multi-step advance-loop receipts without live execution.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_driver_dogfood",
        "command": "python3 scripts/operator_loop_driver_dogfood_smoke.py",
        "summary": "Hermes/OpenClaw style machine callers consume decision-gated loop-driver packets from a real isolated local MIS server without live adapter execution, DB mutation, server shell, or token-like leakage.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_supervision_work_packet_api",
        "command": "python3 scripts/operator_loop_supervision_smoke.py",
        "summary": "Loop supervision exposes the same compact agent_work_packet_bundle_v1 through CLI --work-packet and HTTP work_packet=1, plus a read-only agent_work_packet_decision_v1 through CLI --decision and HTTP decision=1 for Hermes/OpenClaw HTTP-only agents without mutating ledgers, executing server shell, running live adapters, or leaking tokens.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_launch_packet",
        "command": "python3 scripts/operator_loop_launch_packet_smoke.py",
        "summary": "Agent Work Method launch packet supports default lightweight loop-control and explicit full handoff modes without mutating ledgers.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_agent_loop_handoff",
        "command": "python3 scripts/operator_agent_loop_handoff_smoke.py",
        "summary": "Compact Hermes/OpenClaw/Codex loop handoff aggregates current-code readiness, live ledger proof, start-check gates, launch briefs, Method Block phase commands and no-server-shell safety without mutating ledgers.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_supervision",
        "command": "python3 scripts/operator_loop_supervision_smoke.py",
        "summary": "Pre-confirm Hermes/OpenClaw/Codex loop supervision reads handoff/start-check gates, Agent Plan quality audit state, service-managed receipt/readback closure, review pressure, layered next commands and no-server-shell safety without mutating ledgers.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_loop_supervision_consumption",
        "command": "python3 scripts/operator_loop_supervision_consumption_smoke.py",
        "summary": "Customer-worker and installable worker live Hermes/OpenClaw entry points consume loop-supervision before runtime execution, audit the gate, and block when bounded confirm is not ready or Agent Plan quality attention requires record-first closure; installable workers also stop before live adapter execution when service-managed receipt/readback closure is the required record-first action.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_service_closure_cli",
        "command": "python3 scripts/operator_service_closure_cli_smoke.py",
        "summary": "Hermes/OpenClaw service-managed loop closure CLI previews receipt/readback work without mutating ledgers, consumes either explicit service-check JSON or an explicit local read-only --run-service-check, and moves service closure from required to ready without executing service-control, server shell, or live adapter work.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_service_closure_fast",
        "command": "python3 scripts/operator_service_closure_fast_smoke.py",
        "summary": "Fast service-closure records canonical service-check receipt/readback without a loop-supervision pre-read, then proves the existing loop-supervision surface recognizes the receipt while preserving no-shell/no-live boundaries.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "run_start_loop_supervision_gate",
        "command": "python3 scripts/run_start_loop_supervision_gate_smoke.py",
        "summary": "Agent Gateway run_start consumes loop-supervision before Hermes/OpenClaw/Codex run creation, blocks unsafe live starts, and attaches safe gate hashes when allowed.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "run_start_work_packet_decision_gate",
        "command": "python3 scripts/run_start_work_packet_decision_gate_smoke.py",
        "summary": "Agent Gateway run_start consumes agent_work_packet_decision_v1 before Hermes/OpenClaw run creation, blocks unsafe decision packets, and attaches decision hashes when allowed.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_evidence_report",
        "command": "python3 scripts/operator_evidence_report_smoke.py",
        "summary": "Run-level evidence report checks Agent Plan binding, quality rubric, approval, verified plan evidence, tool/eval/artifact/audit rows, memory review closure, worker knowledge retrieval, worker runtime summary events, raw memory/runtime omission and read-only DB stability.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "operator_live_product_readiness",
        "command": "python3 scripts/operator_live_product_readiness_smoke.py",
        "summary": "CLI product-readiness proof reads fresh Hermes/OpenClaw live ledger evidence and fails closed without calling runtimes.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "local_runtime_identity",
        "command": "python3 scripts/local_runtime_identity_smoke.py",
        "summary": "Local MIS runtime identity proves the connected server process is current with backend source and supports strict CLI fail-closed checks.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "agentops_cli_connection_hint",
        "command": "python3 scripts/agentops_cli_connection_hint_smoke.py",
        "summary": "CLI stale-base-url failures explain whether the target came from flag/env/config/default and show the local demo repair command without leaking tokens.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "task_detail_evidence_ui",
        "command": "python3 scripts/task_detail_evidence_ui_smoke.py",
        "summary": "Task detail page exposes delivery evidence state, latest run and approval links.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "run_ledger_evidence_ui",
        "command": "python3 scripts/run_ledger_evidence_ui_smoke.py",
        "summary": "Run Ledger exposes evidence review posture and links each run to the work-delivery graph without row-by-row graph fetching.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "run_detail_evidence_ui",
        "command": "python3 scripts/run_detail_evidence_ui_smoke.py",
        "summary": "Run detail page exposes run evidence-chain state and work-delivery graph readback across tools, runtime events, evaluations, artifacts, approvals, audit refs, benchmarks and live/mock runtime posture.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "security_readiness",
        "command": "python3 scripts/security_production_readiness_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Server-backed security readiness gates.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "agentops_doctor",
        "command": "python3 scripts/agentops_doctor_smoke.py",
        "summary": "CLI doctor fails closed for unsafe shared/production targets without configured credentials while omitting token values.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "agent_plan_integrity",
        "command": "python3 scripts/agent_plan_integrity_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Agent Plan hard contract covers hash binding, approval role separation, authority references and execution gate failures.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "run_start_plan_gate",
        "command": "python3 scripts/run_start_plan_gate_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Run start rejects missing, failed, superseded, mismatched or changed Agent Plans and prevents rebinding existing runs.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "operator_runtime_doctor",
        "command": "python3 scripts/operator_runtime_doctor_smoke.py",
        "summary": "Server-backed local runtime doctor for MIS, Hermes, OpenClaw, Codex supervision, remote Agent fleet, confirm-run walls, prepared-action walls and copyable evidence commands without live execution or ledger mutation.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "operator_start_check_api",
        "command": "python3 scripts/operator_start_check_api_smoke.py",
        "summary": "Server-backed pre-task start-check API for mock/Hermes/OpenClaw that composes local readiness, worker policy, runtime doctor, launch brief, local run path, agent loop packet, local loop admission, and live-ledger proof without live execution or ledger mutation.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "operator_start_check_cli",
        "command": "python3 scripts/operator_start_check_smoke.py --base-url \"$AGENTOPS_BASE_URL\" --adapter hermes --adapter openclaw",
        "summary": "CLI/API parity smoke for the pre-task start-check command used before local Hermes/OpenClaw/Codex loop work.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "operator_execution_mode",
        "command": "python3 scripts/operator_execution_mode_smoke.py",
        "summary": "Server-backed execution-mode read model for UI/CLI/agents covering adapter route, confirm-run wall, prepared-action wall, approvals and async jobs without live execution or ledger mutation.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "runtime_capability_manifest",
        "command": "python3 scripts/runtime_capability_manifest_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Runtime connector capability manifests cover Agent Gateway, OpenClaw, Hermes and Agnesfallback with confirmation/trust policy fields.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "runtime_connector_trust",
        "command": "python3 scripts/runtime_connector_trust_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Runtime connector trust state can block confirmed live OpenClaw/Hermes customer-worker execution before adapter invocation.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "runtime_connector_trust_ui",
        "command": "python3 scripts/runtime_connector_trust_ui_smoke.py",
        "summary": "Runtime connector trust UI explains trusted/review/blocked operator impact, live worker gate readback and audit refs.",
        "ci_step": "Offline safety smokes",
    },
    {
        "id": "enrollment_launch_steps",
        "command": "python3 scripts/enrollment_launch_steps_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Enrollment create/rotate launch packets omit raw tokens and include installable worker, start-check/method-gate contract, short-lived session, service-template/install/check and preview-first service-control commands.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "remote_launch_packet_worker",
        "command": "python3 scripts/remote_launch_packet_worker_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Remote launch packet environment can mint a short-lived session, run a scoped worker, and write run/tool/evaluation ledger evidence without token leakage.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "worker_fleet_hygiene",
        "command": "python3 scripts/worker_fleet_hygiene_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Worker fleet hygiene can plan/apply stuck-task release and stale-enrollment revocation without leaking token IDs.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "prepared_action_approval_wall",
        "command": "python3 scripts/prepared_action_approval_wall_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Prepared-action Approval Wall exact hash, checkpoint, approval, resume and replay safety.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "high_risk_toolcall_prepared_action_gate",
        "command": "python3 scripts/high_risk_toolcall_prepared_action_gate_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "High-risk Agent Gateway tool calls fail closed unless recorded as an exact prepared action before approval/resume.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "worker_external_write_preflight_gate",
        "command": "python3 scripts/worker_external_write_preflight_gate_smoke.py",
        "summary": "Live Hermes/OpenClaw worker tasks with external-write intent pause before adapter execution and create prepared-action approval evidence after local service receipt/readback closure.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "runtime_probe_prepared_action_gate",
        "command": "python3 scripts/runtime_probe_prepared_action_gate_smoke.py",
        "summary": "Fixed live runtime probes use the same Approval Wall exact-resume path before calling loopback Hermes/Agnes-compatible gateways.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "customer_worker_external_write_gate",
        "command": "python3 scripts/customer_worker_external_write_gate_smoke.py",
        "summary": "Opaque confirmed customer-worker live runs that imply external writes create task/run/tool/prepared-action/approval evidence without executing the live runtime.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "generic_external_side_effect_gate",
        "command": "python3 scripts/generic_external_side_effect_gate_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Generic external side-effect detection still blocks direct high-risk writes while allowing safe loopback runtime evidence metadata.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "agent_gateway_runtime_event",
        "command": "python3 scripts/agent_gateway_runtime_event_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Scoped runtime-internal event ingestion records summary/hash evidence and redacts raw runtime payloads.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "worker_adapter_runtime_event_summary",
        "command": "python3 scripts/worker_adapter_retry_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Agent workers automatically record adapter execution summaries into runtime_events while preserving Hermes/OpenClaw opaque-runtime restrictions.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "codex_read_only_worker_adapter",
        "command": "python3 scripts/codex_worker_adapter_smoke.py",
        "summary": "A remote-shaped Codex worker uses a short-lived session, stdin-only prompts, an ephemeral read-only sandbox, structured event inspection, and full plan/run/tool/evaluation/artifact/memory/audit/manifest writeback; deterministic fixtures are never labeled product readiness proof.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "codex_workspace_write_governance",
        "command": "python3 scripts/codex_workspace_write_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Codex workspace-write remains opt-in, requires an attested bundled runtime, isolates changes in a bounded worktree, enforces exact execution leases, and fails closed on scope or protocol violations.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "agent_gateway_knowledge_scope",
        "command": "python3 scripts/agent_gateway_knowledge_scope_smoke.py",
        "summary": "Agent Gateway scoped knowledge visibility, provenance and spoof-resistance gate.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "knowledge_scope_policy",
        "command": "python3 scripts/knowledge_scope_policy_smoke.py",
        "summary": "Knowledge search returns provenance, authority class, visibility proof and redacted snippets under scoped-token visibility.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "agent_gateway_reviewable_lists",
        "command": "python3 scripts/agent_gateway_reviewable_lists_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Scoped Agent Gateway approval and memory review lists respect workspace/token visibility and omit token material.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "safe_closure_packet",
        "command": "python3 scripts/safe_closure_evidence_packet_smoke.py",
        "summary": "Safe-closure packet IDs, plan hash, reviews, manifests, artifacts and audit counts.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "delivery_approval_manifest_gate",
        "command": "python3 scripts/delivery_approval_manifest_gate_smoke.py",
        "summary": "Customer delivery approval fails closed until a verified plan-evidence manifest exists, then surfaces the manifest on the delivery board.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "workspace_isolation",
        "command": "python3 scripts/workspace_isolation_smoke.py --base-url \"$AGENTOPS_BASE_URL\"",
        "summary": "Scoped Agent Gateway tokens cannot cross workspace boundaries by body, query or header spoofing.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "protected_live_runtime_ids",
        "command": "python3 scripts/protected_live_runtime_ids_smoke.py",
        "summary": "Protected planned-task, connector, prepared-action and approval IDs without pre-approval live calls.",
        "ci_step": "Server-backed smoke suite",
    },
    {
        "id": "ui_build",
        "command": "cd ui/start-building-app && npm ci && npm run build",
        "summary": "Next.js UI build for the command center and Pixel Office surfaces.",
        "ci_step": "UI build",
    },
]


def run(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def git_text(args: list[str]) -> str:
    proc = run(["git", *args])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git command failed").strip())
    return (proc.stdout or "").strip()


def maybe_git_text(args: list[str]) -> str | None:
    proc = run(["git", *args])
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def release_status(text: str) -> str:
    match = re.search(r"Current status:\s*`([^`]+)`", text)
    return match.group(1) if match else "UNKNOWN"


def status_entries() -> list[str]:
    raw = maybe_git_text(["status", "--porcelain"]) or ""
    return [line for line in raw.splitlines() if line.strip()]


def current_branch() -> str:
    return maybe_git_text(["branch", "--show-current"]) or os.environ.get("GITHUB_REF_NAME") or "DETACHED"


def upstream_sync() -> dict[str, int | None]:
    upstream = maybe_git_text(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if not upstream:
        return {"ahead": None, "behind": None}
    counts = maybe_git_text(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
    if not counts:
        return {"ahead": None, "behind": None}
    behind_text, ahead_text = counts.split()
    return {"ahead": int(ahead_text), "behind": int(behind_text)}


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def command_script_paths(command: str) -> list[Path]:
    paths: list[Path] = []
    for match in re.finditer(r"scripts/[A-Za-z0-9_./-]+\.py", command):
        paths.append(ROOT / match.group(0))
    return paths


def validate_test_commands(ci_text: str, failures: list[str]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for item in TEST_COMMANDS:
        command = item["command"]
        manual_only = bool(item.get("manual_only"))
        scripts = command_script_paths(command)
        for path in scripts:
            require(path.exists(), f"test command references missing script: {path.relative_to(ROOT)}", failures)
        if manual_only:
            pass
        elif scripts:
            for path in scripts:
                require(path.relative_to(ROOT).as_posix() in ci_text, f"CI workflow does not run {path.relative_to(ROOT)}", failures)
        elif "py_compile" in command:
            require("python3 -m py_compile server.py agentops_mis_cli/*.py agentops_mis_core/*.py agentops_mis_runtime/*.py scripts/*.py" in ci_text, "CI workflow missing py_compile command", failures)
            require("git diff --check" in ci_text, "CI workflow missing git diff --check", failures)
        elif "npm run build" in command:
            require("npm run build" in ci_text and "ui/start-building-app" in ci_text, "CI workflow missing UI build command", failures)
        manifest.append(
            {
                "id": item["id"],
                "command": command,
                "summary": item["summary"],
                "ci_step": item["ci_step"],
                "ci_backed": not manual_only,
                "manual_only": manual_only,
            }
        )
    return manifest


def validate_docs(checklist: str, packet_doc: str, ci_text: str, failures: list[str]) -> None:
    require(PACKET_DOC.exists(), "missing docs/RELEASE_EVIDENCE_PACKET.md", failures)
    required_packet_phrases = [
        "Exact RC SHA",
        "git rev-parse HEAD",
        "CI links and status",
        "GitHub Actions",
        "Test command list and summary",
        "scripts/release_evidence_packet_smoke.py",
        "Never include raw credentials",
        "local MVP / NOT_READY",
    ]
    for phrase in required_packet_phrases:
        require(phrase in packet_doc, f"release evidence packet doc missing phrase: {phrase}", failures)
    for item in TEST_COMMANDS:
        canonical_command = str(item["command"]).replace('\\"', '"')
        require(canonical_command in packet_doc, f"release evidence packet doc missing canonical command: {canonical_command}", failures)

    checklist_requirements = [
        "- [x] Exact RC SHA.",
        "- [x] CI links and status.",
        "- [x] Test command list and summary.",
        "scripts/release_evidence_packet_smoke.py",
        "docs/RELEASE_EVIDENCE_PACKET.md",
    ]
    for phrase in checklist_requirements:
        require(phrase in checklist, f"merge readiness checklist missing release-packet closure phrase: {phrase}", failures)
    require("python3 scripts/release_evidence_packet_smoke.py" in ci_text, "CI workflow missing release evidence packet smoke", failures)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-clean", action="store_true", help="Fail on any local working-tree change.")
    parser.add_argument("--require-green-ci", action="store_true", help="Fail unless current HEAD has a successful CI run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    checklist = read(CHECKLIST) if CHECKLIST.exists() else ""
    packet_doc = read(PACKET_DOC) if PACKET_DOC.exists() else ""
    ci_text = read(CI_WORKFLOW) if CI_WORKFLOW.exists() else ""
    head_sha = git_text(["rev-parse", "HEAD"])
    branch = current_branch()
    status = status_entries()
    status_name = release_status(checklist)
    upstream = upstream_sync()
    ci = shared_ci_status(ROOT, head_sha, branch, required_before_ready=True)
    tests = validate_test_commands(ci_text, failures)
    validate_docs(checklist, packet_doc, ci_text, failures)

    green_ci = ci.get("head_matches") is True and ci.get("status") == "completed" and ci.get("conclusion") == "success"
    require(status_name != "UNKNOWN", "merge readiness checklist missing current status", failures)
    if args.require_clean:
        require(not status, "working tree must be clean for final RC evidence", failures)
    if args.require_green_ci:
        require(green_ci, f"current HEAD does not have green CI evidence: {ci}", failures)
    if args.require_clean or args.require_green_ci:
        require(upstream.get("ahead") == 0 and upstream.get("behind") == 0, f"READY checklist requires upstream sync: {upstream}", failures)
    require(not (ci.get("head_matches") is False and ci.get("head_sha")), f"CI head does not match current HEAD: {ci}", failures)

    output = {
        "ok": not failures,
        "operation": "release_evidence_packet_smoke",
        "release_status": status_name,
        "rc_sha": {
            "source": "git rev-parse HEAD",
            "value": head_sha,
            "branch": branch,
            "upstream_sync": upstream,
            "working_tree_entries": len(status),
        },
        "ci": ci,
        "test_command_summary": {
            "total": len(tests),
            "ci_backed": sum(1 for item in tests if item.get("ci_backed")),
            "commands": tests,
        },
        "contracts": [
            "Exact SHA is captured at runtime, never hard-coded into a tracked stale packet.",
            "CI status may be queued, in-progress, failed or unavailable in CI-safe default mode.",
            "Final strict evidence requires --require-clean --require-green-ci with upstream sync and successful current-head CI.",
            "Evidence output omits raw credentials, private prompts, model responses, customer bodies, local databases and unsafe logs.",
        ],
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "failures": failures,
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(serialized)]
    if secret_hits:
        output["ok"] = False
        output["failures"].append(f"release evidence output leaked token-like material: {secret_hits}")
        serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    print(serialized)
    return 1 if output["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
