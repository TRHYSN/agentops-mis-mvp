# Release Evidence Packet

This packet defines the auditable release evidence shape for AgentOps MIS. It is
not a static generated artifact because the Exact RC SHA changes with every
commit. The authoritative packet is emitted at runtime by:

```bash
python3 scripts/release_evidence_packet_smoke.py
```

For a final RC candidate, run the stricter form:

```bash
python3 scripts/github_required_checks_smoke.py
python3 scripts/release_evidence_packet_smoke.py --require-clean --require-green-ci
python3 scripts/release_freeze_protocol_smoke.py --require-clean --require-green-ci --require-remote-checks
python3 scripts/merge_readiness_status_smoke.py --require-ready-to-merge
```

## Required Evidence

### Exact RC SHA

- Source: `git rev-parse HEAD`.
- The packet records the current branch, upstream ahead/behind counts and dirty
  working-tree entry count.
- The SHA must not be hand-copied into a tracked document as the release source
  of truth.

### CI Links and Status

- Checklist phrase: CI links and status.
- In GitHub Actions, the packet derives the run URL from `GITHUB_SERVER_URL`,
  `GITHUB_REPOSITORY` and `GITHUB_RUN_ID`.
- Outside CI, the packet may use `gh run list` to find a run for the current
  head SHA.
- If the GitHub API is unavailable, the packet may use a public GitHub Actions
  HTML fallback only when the run page contains the full current HEAD SHA and
  an explicit success status. Short SHA matches are not sufficient.
- If no current-head run is available, the packet must say so and keep the
  release in local MVP / NOT_READY posture.
- READY evidence requires current-head CI with status `completed` and conclusion
  `success`.

### Release Freeze And Required Checks

- Source: `docs/RELEASE_FREEZE_PROTOCOL.md`.
- Default check: `python3 scripts/release_freeze_protocol_smoke.py`.
- Strict check:
  `python3 scripts/release_freeze_protocol_smoke.py --require-clean --require-green-ci --require-remote-checks`.
- Required checks before merge are verified by
  `python3 scripts/github_required_checks_smoke.py`.
- In GitHub Actions, this smoke may report `ci_permission_limited` if the
  short-lived workflow token cannot read branch protection; final local review
  must use an authenticated `gh` session that can read the live protection rule.

### Merge Readiness State

- Source: `docs/V1_5_MERGE_READINESS_CHECKLIST.md`.
- Default check: `python3 scripts/merge_readiness_status_smoke.py`.
- Strict check: `python3 scripts/merge_readiness_status_smoke.py --require-ready-to-merge`.
- The default check may pass while the branch is still `NOT_READY`; it proves
  that the checklist is honest, the final state matches the header state, and
  remaining blockers are explicit.
- The strict check must fail until the checklist is advanced to
  `READY_TO_MERGE`, the working tree is clean, upstream is synchronized, the
  exact HEAD has green CI, and no unchecked blocker remains.

### Test Command List and Summary

Checklist phrase: Test command list and summary.

The packet includes the canonical command manifest used for release review:

- `python3 -m py_compile server.py agentops_mis_cli/*.py agentops_mis_core/*.py agentops_mis_runtime/*.py scripts/*.py && git diff --check`
- `python3 scripts/release_branch_control_smoke.py`
- `python3 scripts/release_freeze_protocol_smoke.py`
- `python3 scripts/github_ci_evidence_smoke.py`
- `python3 scripts/clean_machine_rc_smoke.py`
- `python3 scripts/run_local_stack_smoke.py`
- `python3 scripts/private_host_owner_browser_handoff_smoke.py`
- `python3 scripts/private_host_auth_workspace_ui_smoke.py`
- `python3 scripts/human_password_recovery_smoke.py`
- `python3 scripts/private_host_service_worker_presence_smoke.py`
- `python3 scripts/human_session_management_smoke.py`
- `python3 scripts/private_host_worker_machine_read_smoke.py`
- `python3 scripts/private_host_worker_ownership_smoke.py`
- `python3 scripts/private_host_rc_status_smoke.py`
- `python3 scripts/private_host_background_service_smoke.py`
- `python3 scripts/private_host_macos_launcher_smoke.py`
- `python3 scripts/release_evidence_packet_smoke.py`
- `python3 scripts/merge_readiness_status_smoke.py`
- `python3 scripts/v1_5_product_closure_evidence_smoke.py`
- `python3 scripts/enrollment_hosted_policy_ui_smoke.py`
- `python3 scripts/agent_gateway_scope_effects_ui_smoke.py`
- Manual current-code product evidence gate, intentionally not CI-backed:
  `python3 scripts/v1_5_current_code_product_evidence.py --base-url http://127.0.0.1:<current-code-port> --db-path /tmp/<current-code-agentops>.db --confirm-live`
- Manual product-readiness gate, intentionally not CI-backed:
  `python3 scripts/customer_worker_real_runtime_acceptance.py --confirm-live --adapter hermes --adapter openclaw`
- Manual governed local harness live gate, intentionally not CI-backed:
  `python3 scripts/local_harness_governed_live_acceptance.py --adapter openclaw --confirm-live --auto-service-closure`
- CI-safe governed local harness preview:
  `python3 scripts/local_harness_governed_live_acceptance_smoke.py`
- `python3 scripts/customer_worker_hermes_retry_gateway_smoke.py`
- `python3 scripts/hermes_http_error_redaction_smoke.py`
- `python3 scripts/redaction_policy_smoke.py`
- `python3 scripts/sqlite_pragmas_smoke.py`
- `python3 scripts/module_boundary_smoke.py`
- `python3 scripts/read_model_cache_smoke.py`
- `python3 scripts/open_source_adoption_boundary_smoke.py`
- `python3 scripts/open_source_adoption_packet_spec_smoke.py`
- `python3 scripts/open_source_adoption_packet_catalog_smoke.py`
- `python3 scripts/open_source_mainline_governance_smoke.py`
- `python3 scripts/harness_engineering_control_plane_smoke.py`
- `python3 scripts/harness_engineering_execution_constraints_smoke.py`
- `python3 scripts/harness_engineering_product_constraints_smoke.py`
- `python3 scripts/harness_style_agentops_operating_spec_smoke.py`
- `python3 scripts/local_task_harness_smoke.py`
- `python3 scripts/agent_task_harness_engineering_spec_smoke.py`
- `python3 scripts/local_harness_proof_readback_smoke.py`
- `python3 scripts/spatial_research_semantic_contract_smoke.py`
- `python3 scripts/spatial_zone_authority_readback_smoke.py`
- `python3 scripts/commercial_config_boundary_smoke.py`
- `python3 scripts/commercial_config_status_smoke.py`
- `python3 scripts/commercial_config_status_ui_smoke.py`
- `python3 scripts/commercial_config_operator_action_ui_smoke.py`
- `python3 scripts/commercial_evidence_packet_index_smoke.py`
- `python3 scripts/commercial_current_evidence_status_smoke.py`
- `python3 scripts/commercial_handoff_status_smoke.py`
- `python3 scripts/commercial_promotion_preflight_smoke.py`
- `python3 scripts/commercial_promotion_packet_smoke.py`
- `python3 scripts/commercial_receipt_plan_smoke.py`
- `python3 scripts/commercial_receipt_recording_smoke.py`
- `python3 scripts/commercial_rerun_bundle_preview_smoke.py`
- `python3 scripts/commercial_confirmed_receipt_recording_smoke.py`
- `python3 scripts/commercial_receipt_prepared_action_binding_smoke.py`
- `python3 scripts/commercial_prepared_action_execution_receipt_smoke.py`
- `python3 scripts/commercial_migration_breakdown_smoke.py`
- `python3 scripts/external_connector_runtime_inventory_smoke.py`
- `python3 scripts/sqlite_concurrency_smoke.py`
- `python3 scripts/secret_scan_smoke.py`
- `python3 scripts/license_provenance_smoke.py`
- `python3 scripts/public_claims_release_gate_smoke.py`
- `python3 scripts/redaction_fuzz_smoke.py`
- `python3 scripts/shared_mode_local_write_guard_smoke.py`
- `python3 scripts/automatic_plan_evidence_workflow_smoke.py`
- `python3 scripts/migration_rollback_smoke.py`
- `python3 scripts/memory_workspace_authority_migration_smoke.py`
- `python3 scripts/approval_semantics_boundary_smoke.py`
- `python3 scripts/agent_plan_quality_smoke.py`
- `python3 scripts/knowledge_retrieval_quality_smoke.py`
- `python3 scripts/worker_knowledge_evidence_consumption_smoke.py`
- `python3 scripts/worker_intake_auto_plan_smoke.py`
- `python3 scripts/worker_prompt_profile_smoke.py`
- `python3 scripts/work_delivery_graph_readback_smoke.py`
- `python3 scripts/commander_repo_map_smoke.py`
- `python3 scripts/commander_coding_project_template_smoke.py`
- `python3 scripts/commander_coding_workspace_smoke.py`
- `python3 scripts/commander_lane_packet_smoke.py`
- `python3 scripts/operator_command_center_smoke.py`
- `python3 scripts/commander_work_package_plan_smoke.py`
- `python3 scripts/commander_work_package_dispatch_smoke.py`
- `python3 scripts/commander_work_package_batch_dispatch_smoke.py`
- `python3 scripts/commander_integration_inbox_smoke.py --base-url "$AGENTOPS_BASE_URL" --db-path "$AGENTOPS_DB_PATH"`
- `python3 scripts/local_coding_project_template_smoke.py`
- `python3 scripts/ai_employees_responsiveness_smoke.py`
- `python3 scripts/operator_action_queue_ui_smoke.py`
- `python3 scripts/worker_console_ui_smoke.py`
- `python3 scripts/customer_dispatch_desk_ui_smoke.py`
- `python3 scripts/commander_team_board_ui_smoke.py`
- `python3 scripts/operator_advance_loop_smoke.py`
- `python3 scripts/operator_loop_control_smoke.py`
- `python3 scripts/operator_loop_bootstrap_smoke.py`
- `python3 scripts/operator_loop_bootstrap_api_ui_smoke.py`
- `python3 scripts/operator_loop_driver_smoke.py`
- `python3 scripts/operator_loop_driver_dogfood_smoke.py`
- `python3 scripts/operator_loop_launch_packet_smoke.py`
- `python3 scripts/operator_agent_loop_handoff_smoke.py`
- `python3 scripts/operator_loop_supervision_smoke.py`
- `python3 scripts/operator_loop_supervision_consumption_smoke.py`
- `python3 scripts/operator_service_closure_cli_smoke.py`
- `python3 scripts/operator_service_closure_fast_smoke.py`
- `python3 scripts/run_start_loop_supervision_gate_smoke.py`
- `python3 scripts/run_start_work_packet_decision_gate_smoke.py`
- `python3 scripts/operator_evidence_report_smoke.py`
- `python3 scripts/operator_live_product_readiness_smoke.py`
- `python3 scripts/local_runtime_identity_smoke.py`
- `python3 scripts/agentops_cli_connection_hint_smoke.py`
- `python3 scripts/task_detail_evidence_ui_smoke.py`
- `python3 scripts/run_ledger_evidence_ui_smoke.py`
- `python3 scripts/run_detail_evidence_ui_smoke.py`
- `python3 scripts/security_production_readiness_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/agentops_doctor_smoke.py`
- `python3 scripts/agent_plan_integrity_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/run_start_plan_gate_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/operator_runtime_doctor_smoke.py`
- `python3 scripts/operator_start_check_api_smoke.py`
- `python3 scripts/operator_start_check_smoke.py --base-url "$AGENTOPS_BASE_URL" --adapter hermes --adapter openclaw`
- `python3 scripts/operator_execution_mode_smoke.py`
- `python3 scripts/runtime_capability_manifest_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/runtime_connector_trust_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/runtime_connector_trust_ui_smoke.py`
- `python3 scripts/worker_fleet_hygiene_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/prepared_action_approval_wall_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/high_risk_toolcall_prepared_action_gate_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/worker_external_write_preflight_gate_smoke.py`
- `python3 scripts/runtime_probe_prepared_action_gate_smoke.py`
- `python3 scripts/customer_worker_external_write_gate_smoke.py`
- `python3 scripts/generic_external_side_effect_gate_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/agent_gateway_runtime_event_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/worker_adapter_retry_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/codex_worker_adapter_smoke.py`
- `python3 scripts/codex_workspace_write_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/agent_gateway_knowledge_scope_smoke.py`
- `python3 scripts/knowledge_scope_policy_smoke.py`
- `python3 scripts/agent_gateway_reviewable_lists_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/safe_closure_evidence_packet_smoke.py`
- `python3 scripts/delivery_approval_manifest_gate_smoke.py`
- `python3 scripts/workspace_isolation_smoke.py --base-url "$AGENTOPS_BASE_URL"`
- `python3 scripts/protected_live_runtime_ids_smoke.py`
- `cd ui/start-building-app && npm ci && npm run build`

The smoke verifies each CI command is backed by `.github/workflows/ci.yml` and
that referenced script files exist. `customer_worker_hermes_retry_gateway_smoke`
uses a deterministic loopback OpenAI-compatible gateway to prove retry metadata
is wired through the real customer-worker Hermes adapter path; it is still not
live product-readiness proof. Manual-live commands are tracked in the packet but
intentionally excluded from CI because they call real local Hermes/OpenClaw
runtimes and require explicit operator confirmation.

`hermes_http_error_redaction_smoke` proves an upstream Hermes HTTP failure keeps
its status, retry policy, and response hash while omitting the response body
from worker evidence.

### Manual Live Product Evidence

CI/offline smokes prove merge hygiene and regression coverage; they do not prove
product readiness by themselves. Product-readiness, demo, dogfood, or
customer-usefulness claims require current manual live evidence when local
Hermes/OpenClaw are available and authorized:

```bash
python3 scripts/v1_5_current_code_product_evidence.py \
  --base-url http://127.0.0.1:<current-code-port> \
  --db-path /tmp/<current-code-agentops>.db \
  --confirm-live

python3 scripts/customer_worker_real_runtime_acceptance.py \
  --confirm-live \
  --adapter hermes \
  --adapter openclaw

python3 scripts/v1_5_live_product_readiness_smoke.py \
  --require-adapter hermes \
  --require-adapter openclaw
```

Canonical combined current-code proof command:
`python3 scripts/v1_5_current_code_product_evidence.py --base-url http://127.0.0.1:<current-code-port> --db-path /tmp/<current-code-agentops>.db --confirm-live`.
This command rebuilds knowledge evidence, runs Commander synthesis, executes
confirmed Hermes/OpenClaw customer-worker acceptance, verifies live readback,
exercises the remote/scoped worker mock fallback with short-lived session
launch-packet evidence, and finishes with non-live local acceptance.

Canonical read-only proof command:
`python3 scripts/v1_5_live_product_readiness_smoke.py --require-adapter hermes --require-adapter openclaw`.

The release note or handoff must cite the resulting Hermes/OpenClaw run IDs and
artifact IDs. The read-only live product-readiness smoke must report
`product_readiness_proof:true`; it only reads the MIS ledger and does not call
Hermes/OpenClaw. Mock-only evidence must be described as CI/offline fallback,
not as product-level completion.

When the current-code server uses an isolated SQLite database, pass the matching
`--db-path`; some server-backed smokes verify ledger rows directly and will read
the default repo DB otherwise.

Server-backed commands, including
`python3 scripts/local_coding_project_template_smoke.py`,
`python3 scripts/enrollment_launch_steps_smoke.py --base-url "$AGENTOPS_BASE_URL"`,
and `python3 scripts/remote_launch_packet_worker_smoke.py --base-url "$AGENTOPS_BASE_URL"`,
require a running AgentOps MIS server selected by `AGENTOPS_BASE_URL`. The
GitHub Actions backend suite starts an isolated `127.0.0.1:8787` server with a
temporary SQLite database and live Hermes/OpenClaw/Dify/Notion disabled before
running them. The enrollment/remote-worker smokes prove that launch packets
contain preview-first service-control commands, omit raw tokens, mint
short-lived sessions, and can write scoped worker evidence.

### Clean-Machine RC

Checklist phrase: clean-machine RC.

`python3 scripts/clean_machine_rc_smoke.py` clones the exact current HEAD into a
temporary directory, rejects tracked runtime/generated files, uses isolated
SQLite state, verifies source-package installation including `agentops --help`
and `agentops-worker --help`, runs release gates, creates a safe closure packet
with submitted/verified Agent Plan evidence, starts a reset local server, and
checks the read-only delivery board. The UI build remains covered by the
dedicated CI `UI build` job and by the packet's canonical command manifest.

## Forbidden Evidence

Never include raw credentials, private prompts, raw model responses, customer
document bodies, local databases, private transcripts, or unsafe runtime logs in
release evidence. The packet output must stay summary-only and token-omitted.
