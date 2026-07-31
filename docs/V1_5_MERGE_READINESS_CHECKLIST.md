# AgentOps MIS v1.5 Merge Readiness Checklist

> Frozen source: `8d1827e00629bdca4779794121ca4a31dfa3f1e1`  
> Current status: `READY_TO_MERGE`

Hardening overlay:
`docs/V1_5_AGENT_GATEWAY_HARDENING_OBJECTIVE.md`. Use it as the P0 objective
and acceptance-gate map for the first `audit/v1-5-agent-gateway-hardening`
findings before marking this branch release-candidate ready.

## 1. Branch control

- [x] Open-source adoption boundary is captured as a project rule. `PROJECT_SPEC.md`, `AGENT_WORKFLOW.md`, and `docs/V1_5_EIGHT_PRODUCT_CLOSURE_SPEC.md` point to `docs/OPEN_SOURCE_ADOPTION_BOUNDARY_SPEC.md`, which separates direct tool adoption, reference-only method adaptation, and first-party MIS authority modules.
- [x] Confirm the intended development branch HEAD. Guarded by `scripts/release_branch_control_smoke.py`, which reports the current branch, exact `HEAD` SHA, upstream sync state and `origin/main` history.
- [x] Freeze a release-candidate SHA through the release evidence runtime source and hardening freeze protocol. `docs/RELEASE_FREEZE_PROTOCOL.md` records the freeze start baseline, while `scripts/release_evidence_packet_smoke.py` emits the exact current `git rev-parse HEAD` and `scripts/release_freeze_protocol_smoke.py` keeps the freeze protocol auditable without storing stale release packets.
- [x] Pause unrelated feature work during hardening. `docs/RELEASE_FREEZE_PROTOCOL.md` now limits this branch to security, correctness, release-readiness, evidence-gate, CI, rollback, recovery and claim-narrowing changes until final RC/merge review; guarded by `scripts/release_freeze_protocol_smoke.py`.
- [x] Confirm no databases, runtime state, credentials, generated service files or logs are tracked. Guarded by `scripts/release_branch_control_smoke.py`; `.env.example` is the only allowed env-like tracked file and secret-like content remains covered by `scripts/secret_scan_smoke.py`.
- [x] Preserve reviewable functional history; do not turn all commits into one opaque change. Guarded by `scripts/release_branch_control_smoke.py`, which requires a merge-base and more than one reviewable commit ahead of `origin/main` on release/feature branches, while allowing the post-merge `main` context when `HEAD` is exactly synchronized with `origin/main`.

```bash
git fetch origin
git rev-parse codex/agent-gateway-kb-demo
git status --short
git diff --check main...codex/agent-gateway-kb-demo
python3 scripts/release_branch_control_smoke.py --expected-branch codex/agent-gateway-kb-demo --require-upstream-synced
python3 scripts/release_freeze_protocol_smoke.py
```

## 2. Agent Plan integrity

- [x] Agent-created plans can only be `draft` or `submitted`.
- [x] Human/admin/policy identity alone can approve or reject a plan.
- [x] High/critical or approval-required plans create/reference a real approval object and cannot authorize `run_start` until approved.
- [x] Plan approve/reject transitions append audit/runtime evidence with actor and approval object references.
- [x] Plan has an immutable hash/version.
- [x] Verification result has a hash and timestamp.
- [x] Every referenced spec exists and is readable.
- [x] Every referenced base exists.
- [x] Every referenced memory exists, is visible and has an allowed review status.
- [x] Proposed files stay inside the allowed workspace/repository.
- [x] Real run links an `agent_plan_id`.
- [x] Run Agent/task/workspace match the plan.
- [x] Superseded, changed, or alternate plans cannot authorize/rebind execution.
- [x] Delivery gate compares execution evidence with the declared plan: `operator evidence-report` checks run `agent_plan_id`/`plan_hash`, plan verification, plan approval, verified `plan_evidence_manifest`, tool/evaluation/artifact/audit evidence, memory review status, pending approvals, raw memory text omission, and read-only DB fingerprint stability.

Required checks:

```bash
python3 scripts/agent_plan_integrity_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/run_start_plan_gate_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/operator_evidence_report_smoke.py
```

Add new negative tests:

```text
Agent tries to create status=approved       rejected
fake memory/base/spec reference             verification fails
plan Agent differs from run Agent           execution rejected
plan changes after approval                 old approval invalid
run without verified plan                   rejected for governed workflow
execution touches undeclared file/tool      readiness gate fails
```

## 2.5 Approval Wall exact action resume

- [x] Prepared actions persist normalized arguments, policy version, checkpoint and idempotency key.
- [x] Prepared actions store and verify an immutable `action_hash`.
- [x] Approval authorizes a prepared action without performing the side effect.
- [x] Resume requires approved approval plus matching hash.
- [x] Resume writes provider side-effect evidence and `consumed_at`.
- [x] Replay after `consumed_at` is rejected.
- [x] CLI supports create/get/resume.
- [x] Customer-worker Hermes/OpenClaw external-write intent pauses before live runtime execution and creates a prepared action plus approval.
- [x] Agent Gateway high-risk external side-effect tool calls cannot be recorded as completed or with `side_effect_id` unless they create a prepared action; the KB bot external upload plan now uses the Approval Wall path. Guarded by `scripts/high_risk_toolcall_prepared_action_gate_smoke.py`.
- [x] Direct live worker and local dispatch external-write tasks pause before Hermes/OpenClaw execution and create a prepared action plus approval. Guarded by `scripts/worker_external_write_preflight_gate_smoke.py`.
- [x] Dify connector live text upload cannot call the provider with only `confirm_upload` or a generic approval id; it creates a prepared action first, waits for approval, verifies the exact upload args on resume, and consumes the prepared action with the Dify document id. Guarded by `scripts/dify_upload_prepared_action_gate_smoke.py`.
- [x] Notion live report export cannot call the provider with only `confirm_export`; it creates a prepared action first, waits for approval, verifies the exact report snapshot hash on resume, and consumes the prepared action with the Notion page id. Guarded by `scripts/notion_export_prepared_action_gate_smoke.py`.
- [x] Fixed live runtime probes cannot execute with only `confirm_run`; OpenClaw, Agnesfallback CLI/API, and Hermes default run-task probes create `runtime.fixed_probe` prepared actions first and wait for exact approved resume. Guarded by `scripts/runtime_probe_prepared_action_gate_smoke.py`.
- [x] All high-risk external connector/runtime tool paths use prepared actions before shared/commercial deployment. Agent Gateway high/critical external side-effect tool calls, KB bot external upload, customer-worker external-write intent, direct worker/dispatch external-write intent, Dify live upload, Notion live export, fixed live runtime probes, and runtime connector trust gates now use prepared-action or pre-execution trust gates; newly added connector/runtime paths must be added to the inventory gate before shared/commercial deployment. Guarded by `scripts/external_connector_runtime_inventory_smoke.py`.

Required check:

```bash
python3 scripts/prepared_action_approval_wall_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/customer_worker_external_write_gate_smoke.py
python3 scripts/high_risk_toolcall_prepared_action_gate_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/worker_external_write_preflight_gate_smoke.py
python3 scripts/dify_upload_prepared_action_gate_smoke.py
python3 scripts/notion_export_prepared_action_gate_smoke.py
python3 scripts/runtime_probe_prepared_action_gate_smoke.py
python3 scripts/external_connector_runtime_inventory_smoke.py
```

## 3. Knowledge safety and quality

- [x] Knowledge documents carry workspace/project/access metadata where required.
- [x] Repo-global doctrine is separated from customer-private knowledge.
- [x] Remote/scoped search enforces authorization.
- [x] Candidate/rejected memory is not treated as authority.
- [x] Index excludes credentials, databases, runtime logs, caches and raw customer files.
- [x] Redaction occurs before indexing.
- [x] Search returns source path, hash, scope and retrieval ID.
- [x] Add heading-aware chunks rather than only full-document rows.
- [x] Add a Chinese/English retrieval test set.
- [x] Record Recall@5, MRR and p95.
- [x] Reindex is incremental and no-op for unchanged documents.
- [x] Fallback search does not silently reduce to title/summary-only without reporting it.

Current retrieval-quality baseline: `scripts/knowledge_retrieval_quality_smoke.py`
starts an isolated SQLite-backed server, rebuilds the Markdown FTS index, runs a
five-query Chinese/English top-5 test set, and records Recall@5, MRR and local
p95 latency without printing snippets, tokens or raw content. Search now uses
heading-aware `knowledge_chunks` / `knowledge_chunk_fts` first, returns
`retrieval_granularity=heading_chunk`, and falls back to document FTS for legacy
or manually inserted private rows. Recent isolated local runs keep Recall@5
`1.0`, MRR `1.0`, and p95 under `20 ms` across 85+ indexed documents.

Required checks:

```bash
python3 scripts/agent_work_method_block_smoke.py
python3 scripts/agent_gateway_scoped_read_smoke.py
python3 scripts/agent_gateway_knowledge_scope_smoke.py
python3 scripts/knowledge_scope_policy_smoke.py
python3 scripts/knowledge_retrieval_quality_smoke.py
```

## 4. Redaction and secret safety

- [x] Replace worker marker substitution with value-aware redaction.
- [x] Share one redaction library across backend, CLI, worker and tests.
- [x] Redact before truncation.
- [x] Cover authorization headers, URL parameters, JSON fields, environment assignments and common provider formats.
- [x] Raw stdout, stderr and model responses remain outside the ledger by default.
- [x] Add mixed-case and unusual-separator regression tests.
- [x] Add fuzz/property tests before shared/commercial deployment. Guarded by `scripts/redaction_fuzz_smoke.py`, which deterministically fuzzes headers, env vars, URL params, JSON fields, stdout/stderr-like lines, idempotence, truncation-before-redaction, and safe operational ID preservation across shared/server/worker redactors.

```bash
python3 scripts/redaction_fuzz_smoke.py
python3 scripts/redaction_policy_smoke.py
python3 scripts/secret_scan_smoke.py
python3 scripts/security_production_readiness_smoke.py
python3 scripts/production_security_warning_ui_smoke.py
python3 scripts/pixel_office_visualizer_boundary_smoke.py
python3 scripts/customer_delivery_boundary_smoke.py
python3 scripts/license_provenance_smoke.py
```

## 5. Authentication and deployment boundary

- [x] Loopback anonymous mode is explicitly local-development only.
- [x] Non-loopback binding requires explicit opt-in.
- [x] Non-loopback binding requires Agent Gateway authentication.
- [x] Non-loopback binding requires admin authentication.
- [x] Production/shared mode rejects `local_dev_no_token`.
- [x] Browser/local write APIs are protected in shared mode.
- [x] `security production-readiness` exposes `local_ui_write_guard`, `/workspace/agents` renders it in both Production Security and Pre-advance self-check, and operator health/action-plan/loop-self-check include it as a receipt-governed readiness signal.
- [x] `agentops doctor` blocks unsafe shared deployment.
- [x] README and runbook state the same rule.

Current v1.5 status: `startup_security_assessment` and
`scripts/startup_security_guard_smoke.py` fail closed for non-loopback or
production/shared mode without explicit opt-in plus Gateway/admin keys.
`agentops doctor` now returns exit code `2` for unsafe shared/production targets
without a Gateway token while still printing redacted JSON diagnostics.
`scripts/shared_mode_local_write_guard_smoke.py` verifies that shared/production
local UI POST/PATCH write APIs reject unauthenticated writes, accept admin-key
writes, and keep scoped Agent Gateway writes working.

Expected matrix:

```text
loopback + local development                allowed with warning
non-loopback + missing authentication       startup blocked
non-loopback + full auth + explicit opt-in  allowed
production + local_dev_no_token             blocked
```

## 6. Approval and runtime governance

### Approval

Choose one v1.5 contract.

#### Durable prepared-action contract

- [x] Prepared action exists before approval.
- [x] Approval binds normalized arguments, resource, policy version and action hash.
- [x] Checkpoint exists before pause.
- [x] Approve authorizes exact resume but does not complete the run/tool side effect directly.
- [x] Approval is one-time and expires.
- [x] Duplicate decisions cannot duplicate side effects.
- [x] Reject blocks the step and writes audit evidence.
- [x] Non-idempotent providers use idempotency keys; provider reconciliation remains connector-specific.

#### Restricted ledger/delivery contract

- [x] Existing approval is described as ledger/delivery approval. Guarded by `scripts/approval_semantics_boundary_smoke.py`, with the durable rule in `docs/APPROVAL_SEMANTICS_BOUNDARY.md`.
- [x] UI/docs do not claim exact tool-action resume. Guarded by `scripts/approval_semantics_boundary_smoke.py`; exact resume wording is allowed only for prepared-action contexts that expose action hash/checkpoint/resume semantics.
- [x] Generic external side effects are denied. Guarded by `scripts/generic_external_side_effect_gate_smoke.py`, which verifies a caller cannot mark an external upload/write as low risk and record it as completed; MIS elevates the effective risk and requires a prepared action.
- [x] Live runtimes stay within documented capabilities and sandbox boundaries. Guarded by `scripts/runtime_capability_manifest_smoke.py` and `scripts/worker_adapter_readiness_smoke.py`, which verify Agent Gateway/OpenClaw/Hermes/Agnesfallback connector manifests, mock/Hermes/OpenClaw readiness, capability fields, confirmation/trust policy, risk floors, summary-only opaque runtime disclosure, commercial restrictions, and no live execution during readiness checks.

```bash
python3 scripts/approval_decision_side_effect_smoke.py
python3 scripts/enrollment_approval_workflow_smoke.py
python3 scripts/enrollment_credential_ui_smoke.py
python3 scripts/review_queue_smoke.py
python3 scripts/agent_gateway_review_queue_smoke.py
python3 scripts/prepared_action_approval_wall_smoke.py
python3 scripts/approval_semantics_boundary_smoke.py
python3 scripts/generic_external_side_effect_gate_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/agent_gateway_runtime_event_smoke.py
python3 scripts/runtime_capability_manifest_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/worker_adapter_readiness_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/kb_bot_demo_smoke.py
```

### Runtime capabilities

- [x] Runtime execution is not always recorded as low risk. Guarded by `scripts/worker_adapter_retry_smoke.py`, which verifies Hermes worker evidence uses the medium runtime capability risk floor instead of low.
- [x] Each adapter/connector declares filesystem, shell, network, Git, secret, external-write, confirmation, trust-policy and runtime-event capabilities through a runtime capability manifest. Guarded by `scripts/runtime_capability_manifest_smoke.py`.
- [x] Live execution requires compatible trust and policy decisions. Guarded by `scripts/runtime_connector_trust_smoke.py`, `scripts/worker_live_confirm_gate_smoke.py`, and external-write prepared-action gate smokes.
- [x] Work directory and write boundaries are explicit in the manifest/readiness payload.
- [x] Runtime tool events are ingested when available through the scoped `POST /api/agent-gateway/runtime-events` and `agentops runtime-event record` contract. Guarded by `scripts/agent_gateway_runtime_event_smoke.py`, which records a hash-only runtime-internal event with a scoped token and verifies run readback, audit evidence and redaction.
- [x] Shared/commercial mode is restricted when detailed tool events are unavailable.
- [x] Secrets are consumed inside trusted tools rather than returned to model output. Guarded by `scripts/worker_secret_boundary_smoke.py`, which injects fake task/env/URL secrets, verifies the worker prompt/output/run/tool/evaluation/audit surfaces omit raw values, and requires `secret_boundary=trusted_worker_client_v1` evidence.

Current v1.5 hardening status: `GET /api/runtime-connectors` and
`agentops runtime connectors` expose all connector manifests, including Agent
Gateway, OpenClaw, Hermes, Agnesfallback CLI and Agnesfallback OpenAI-compatible
gateway. `GET /api/workers/adapter-readiness` and `agentops worker readiness`
expose route-selection manifests for mock, Hermes and OpenClaw. Worker
tool-call risk now consumes the manifest:
Hermes/OpenClaw are recorded at a medium risk floor with
`ledger_summary_only`, `restricted_until_runtime_tool_events`, and
`requires_prepared_action_for_external_write` metadata. Confirmed
customer-worker Hermes/OpenClaw tasks with explicit or obvious external-write
intent now stop before runtime invocation and create task/run/tool/prepared
action/approval/audit evidence. This is a disclosed governance boundary and a
first enforced entry gate, not full internal runtime tracing or complete
coverage for every external side-effect path.

### Module boundaries

- [x] P1-05 has started as a strangler-style split, not a big-bang rewrite.
  Runtime connector capability policy now lives in
  `agentops_mis_runtime/capabilities.py`, and runtime connector config/registry
  rows plus the upsert helper now live in `agentops_mis_runtime/connectors.py`;
  runtime connector health snapshot to row-status projection now also lives in
  `agentops_mis_runtime/connectors.py`;
  runtime connector trust state helpers now live in
  `agentops_mis_runtime/trust.py`; read-model cache behavior now lives in
  `agentops_mis_core/read_model_cache.py`; Approval Wall prepared-action
  hash, public projection, gate, readback, resume-gate mismatch, waiting-response,
  route blocked/error, route access/error, prepare-response, prepared-action decision projection, high-risk tool-call required response, risky tool registry, external side-effect intent detection, metadata-only false-positive guard, resume-success, provider-result reconciliation and
  next-action helpers now live in
  `agentops_mis_core/approval_wall.py`; Agent Plan approval-decision,
  create-status, bound-approval-forbidden, transition-error, run-start gate,
  run-start rebind/success, run-start binding comparison, contract, hash,
  path-scope, verification-result, pending-approval and approval-run helpers now live in
  `agentops_mis_core/agent_plans.py`; Agent Gateway run heartbeat update and
  terminal task-status projection now live in
  `agentops_mis_core/gateway_runs.py`; worker status/fleet lane/health,
  remote worker/session public readback, and hygiene token-omitting
  projections now live in `agentops_mis_core/worker_fleet.py`; workflow-job
  public/list/stuck/recovery response projections now live in `agentops_mis_core/workflow_jobs.py`; Commander
  work-package status/action/readback summary and project-board gate aggregation
  now live in `agentops_mis_core/commander_work_packages.py`; Operator
  command-center gap/project/stale-ref/status aggregation now lives in
  `agentops_mis_core/operator_command_center.py`; `server.py`
  keeps HTTP routes, health probing, refresh orchestration, trust-route runtime
  events, audit writes, endpoint auth checks, read-model producers,
  Agent Gateway enrollment/session and remote-agent metadata reads,
  repo-local daemon/process reads, workflow-job reads/writes/list filters/count
  producers/status transitions/threshold-limit normalization/status validation/stuck-job recovery routing, tool-call row construction/upsert,
  risk default application, provider calls and side-effect id construction,
  Approval Wall exact-once resume invocation/writes,
  route-level hash-mismatch audit writes, Agent Plan DB-backed memory/base
  checks, run-start plan lookup/matching/approval/hash checks,
  rebind-blocked audit writes, run-start row upserts/status-code selection, stable
  approval run ids/timestamps, verification persistence/status transitions,
  heartbeat redaction/parsing/default selection, run/task/agent heartbeat writes,
  fleet hygiene mutations, and Commander write workflows.
  Guarded by
  `scripts/module_boundary_smoke.py`, `docs/MODULE_BOUNDARY_PLAN.md`, and the
  existing runtime capability/readiness/trust plus read-model cache and worker
  fleet smokes.

Agent Gateway tool-call recording now also rejects high/critical external
side-effect intents unless `prepare_action=true` is used. The KB bot demo's
external OpenAI/Dify/AnythingLLM upload plan therefore creates a prepared action
plus pending approval and leaves that run/tool in `waiting_approval`; approving
the approval requires an explicit prepared-action resume before any provider
side-effect evidence can be recorded.

Dify connector live text upload now uses the same exact-resume wall. A request
that has live-upload prerequisites but lacks `prepared_action_id` creates the
waiting-approval run/tool/prepared action and does not call Dify. The approved
resume path verifies the normalized Dify upload args and consumes the prepared
action with the provider document id.

The shared worker loop now applies the same preflight before live adapter
execution. Confirmed Hermes/OpenClaw worker tasks whose title, description,
acceptance criteria, or target metadata indicates publish/upload/deploy/webhook/
external-write intent create a waiting-approval tool call plus prepared action
after run start and before `execute_adapter_with_retries`, so daemon mode,
direct `agentops-worker --once`, and UI dispatch do not rely on a later
tool-call record after the side effect.

```bash
python3 scripts/runtime_connector_trust_smoke.py
python3 scripts/runtime_capability_manifest_smoke.py
python3 scripts/worker_live_confirm_gate_smoke.py
python3 scripts/worker_external_write_preflight_gate_smoke.py
python3 scripts/worker_adapter_readiness_smoke.py
python3 scripts/worker_adapter_retry_smoke.py
```

## 7. Workspace and Agent isolation

- [x] Replace JSON-text `LIKE` collaborator authorization for Agent Gateway task/run/artifact/approval/memory list paths.
- [x] Use exact JSON-array comparison via `agentops_json_array_contains`; normalized collaborator rows remain a later storage refactor.
- [x] Task, run, artifact, approval, memory and review queue use the same scope service. Guarded by `agent_gateway_scope_v1`, exposed in `gateway_scope.scope_service`, with scoped task/run/artifact readback, approval/memory list, and review queue smokes asserting the shared service.
- [x] Knowledge search applies its intended workspace/access policy.
- [x] Add similar-ID regression with `scripts/collaborator_exact_scope_smoke.py`.
- [x] Add special-character-ID regression with `scripts/agent_gateway_special_char_scope_smoke.py`.
- [x] Scoped review queue applies scope before limit.
- [x] Scoped totals are not distorted by global truncation.

Current v1.5 hardening status: `agent_gateway_review_queue` now calls
`human_review_queue` with the bound Agent Gateway identity, so pending
approvals, memory candidates, evaluation-case candidates, and failed
evaluation-case runs apply workspace/agent visibility in SQL before the
requested `limit`. Delivery and Commander synthesis lanes are safety-filtered
with the same task/run visibility helper before the combined queue is sorted.
`scripts/agent_gateway_review_queue_smoke.py` creates 60 hidden candidates in a
different workspace and requires the visible scoped item plus scoped totals to
survive `limit=1`.
`scripts/agent_gateway_special_char_scope_smoke.py` runs against an isolated
SQLite database and verifies URL-encoded task/run/approval path ids plus
workspace, agent, and task ids containing spaces, `+`, `%`, quotes, commas, and
an encoded slash. It requires exact collaborator visibility, workspace-spoof
rejection, scoped ledger list isolation, scoped review queue visibility, and no
token-like leakage.
`agent_gateway_scope_v1` centralizes the Agent Gateway task/run/approval/memory
visibility SQL helpers and the `gateway_scope` evidence envelope used by task,
run, artifact, approval, memory and review-queue readbacks.

```bash
python3 scripts/workspace_isolation_smoke.py
python3 scripts/collaborator_exact_scope_smoke.py
python3 scripts/agent_gateway_special_char_scope_smoke.py
python3 scripts/agent_gateway_scope_matrix_smoke.py
python3 scripts/agent_gateway_scoped_read_smoke.py
python3 scripts/agent_gateway_reviewable_lists_smoke.py
python3 scripts/agent_gateway_review_queue_smoke.py
```

## 8. SQLite reliability and performance

- [x] Centralize connection initialization through `server.db()`.
- [x] Enable foreign keys.
- [x] Enable WAL.
- [x] Set a busy timeout.
- [x] Use autocommit connections to avoid implicit write transactions spanning subprocess/model calls.
- [x] Use an appropriate local synchronous mode.
- [x] Keep the verified write path short in the SQLite reliability smoke.
- [x] Audit every long-running workflow path for model/network/subprocess calls held inside a write transaction.
- [x] Each server request/background path owns a `server.db_session()` that preserves transaction semantics and closes its SQLite connection on normal and exceptional exit.
- [x] Add a schema migration version table.

Recommended local settings:

```sql
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=30000;
PRAGMA synchronous=NORMAL;
```

Concurrency acceptance:

```text
100 concurrent reads: pass via scripts/sqlite_reliability_smoke.py
20 concurrent short writes: pass via scripts/sqlite_reliability_smoke.py
heartbeat + knowledge search + queue + approval: pass
long subprocess held-write-transaction audit: pass via scripts/sqlite_long_transaction_audit_smoke.py
locked/busy failures: zero
1,000 concurrent dashboard reads: pass; macOS process FDs 41 -> 41 and idle SQLite handles 0 -> 0
```

Required checks:

```bash
python3 scripts/sqlite_pragmas_smoke.py
python3 scripts/sqlite_connection_lifecycle_smoke.py
python3 scripts/sqlite_reliability_smoke.py
python3 scripts/sqlite_concurrency_smoke.py
python3 scripts/sqlite_long_transaction_audit_smoke.py
```

## 9. UI/API responsiveness

- [x] Measure AI Employees initial request count: `scripts/ai_employees_responsiveness_smoke.py` models the `/workspace/agents` initial loader and currently measures 29 API reads split into 9 core command-center reads, 15 deferred governance reads, 4 loop-scoped deferred reads, and 1 deferred `/api/agents` read; the budget is <=32 and daemon log prefetch stays absent.
- [x] Measure time to first useful panel: `scripts/ai_employees_responsiveness_smoke.py` measures core command-center readiness as the first useful panel against a <1.5s budget, critical command-center endpoint latency against a <1s budget, and background panel completion against a <2s budget on an isolated local DB; recent runs stayed below budget, with no ledger mutation and no token leakage.
- [x] Do not fetch daemon logs before the panel is opened: AI Employees removes the initial `loadWorkerDaemonLogs` fan-out, loads only the selected adapter after the log panel is opened, and UI smoke forbids the old prefetch marker.
- [x] Make panels independently loadable: AI Employees now uses `AI_EMPLOYEES_CORE_PANEL_LOADERS`, `AI_EMPLOYEES_DEFERRED_PANEL_LOADERS`, and `AI_EMPLOYEES_SCOPED_PANEL_LOADERS` with `Promise.allSettled` plus `panelLoadState`; core command-center panels render first, deferred governance/scoped panels merge later, and one panel endpoint can become unavailable without failing the whole `/workspace/agents` page loader. Key panel headers render ready/unavailable/loading badges, local refresh controls, retry/error evidence (`attempts`, `updated_at`, `last_error`), copyable redacted panel diagnostics, and `ui.panel_diagnostics` Action Queue receipt recording so panel failures enter loop-audit/handoff evidence; repeated failed panel diagnostic receipts feed the same receipt-failure memory candidate/work-order lane as other failed recovery receipts. `scripts/ai_employees_responsiveness_smoke.py` forbids the old page-level `useLiveData` and monolithic destructured loaders, and `scripts/operator_panel_diagnostics_receipt_smoke.py` verifies the receipt plus repeated-failure memory path end to end.
- [x] Add a lightweight command-center read model or equivalent aggregation: `/workspace/agents` now loads `operator command-center` as a core panel and Action Queue source, while `operator evidence-report` aggregates Agent Plan, approval, plan_evidence_manifest, memory review status and ledger evidence by run.
- [x] Add run-level evidence to operator handoff: `operator handoff` now includes an `evidence_report` source and read-only work order so Hermes/OpenClaw/Codex can inherit delivery evidence gaps and commands.
- [x] Bounded advance consumes handoff evidence work: unscoped `agentops operator advance-loop` prioritizes the read-only `evidence_report` work order for blocked/attention run evidence, verifies through handoff, records action receipt/evaluation proof, feeds that receipt back into handoff/UI, skips the same evidence work order after it is verified, and then continues into the first read-only `evidence_remediation` preview while preserving the `handoff.evidence_remediation` receipt source; scoped `--loop-id` still advances that loop's gate.
- [x] Evidence remediation chain is explicit in handoff: each non-ready evidence-report run has preview, create, plan-evidence, close-gap, verify, and receipt commands; preview can be consumed by bounded advance, while create/close/confirm mutating steps remain explicit operator actions and are never auto-run by handoff/advance-loop.
- [x] Evidence remediation handoff items expose stage-level workflow state: `workflow_steps` covers preview, create task, dispatch package, plan evidence, synthesis, and close-gap, with only preview marked auto-advanceable and mutating stages marked receipt-gated explicit work. Each command-bearing stage has its own receipt readback, generated record/verify receipt commands, blocked/ready reason, prerequisite step, next safe command kind, and receipt-next command. Handoff and loop-self-check both expose `evidence_remediation_workflow` gates, while `/workspace/agents` shows ready/blocked counts plus a copy-only remediation workflow table for the leading runs.
- [x] Action Queue projects the current remediation workflow stage: `operator action-plan` now exposes `evidence_remediation_workflow` source/summary/items, including step id, mutating/confirm flags, next-safe command kind, receipt source, and verify command. `/workspace/agents` labels these stage actions so operators can continue from preview into explicit package, plan-evidence, synthesis, or close-gap work without reading raw JSON.
- [x] Operator Action Queue recovery items can record action receipts and show VERIFY commands in action-plan / loop-audit readback.
- [x] Paginate large run, tool and audit lists: `/api/runs`, `/api/tool-calls`, and `/api/audit` accept `limit`/`offset`; `include_page=true` returns an envelope with page metadata while legacy array responses remain compatible. UI ledger loaders use bounded limits by default, and `scripts/ledger_pagination_smoke.py` verifies array compatibility plus paginated metadata.
- [x] Briefly cache expensive aggregate read models: dashboard metrics plus operator action-plan, evidence-report, health, handoff, and loop-self-check use a 2s in-process read-model cache keyed by query and auth/workspace profile; responses include `read_model_cache` miss/hit/bypass proof, `refresh_cache=true` bypasses cache, Action Queue receipt/memory writes invalidate cached operator readbacks, and `scripts/read_model_cache_smoke.py` plus `scripts/operator_advance_loop_smoke.py` verify scoped-token separation, read-only ledger behavior, and write-after-receipt freshness.
- [x] One optional endpoint failure must not block the whole page: UI optional loaders return explicit `unavailable` fallback payloads, and AI Employees renders the operator evidence matrix without blocking the rest of the console.

Initial budgets:

```text
ordinary control-plane read p95     <150 ms
scoped queue/knowledge p95          <200 ms
workflow accepted latency           <300 ms
approval decision latency           <300 ms
useful command-center summary       <1 s
```

## 10. Automated CI

- [x] Add GitHub Actions.
- [x] Require checks before merge. `main` is protected with strict required status checks for `Backend deterministic smokes` and `UI build`; guarded by `scripts/github_required_checks_smoke.py`. CI may report `ci_permission_limited` when the workflow token cannot read branch protection, while final local review must read the live rule with authenticated `gh`.
- [x] Use clean temporary database/runtime directories.
- [x] Keep deterministic CI free of external runtime/provider credentials. GitHub's short-lived workflow token may be used only for normal workflow metadata/current-run evidence; branch-protection readback stays in the strict final RC command path with local/admin GitHub credentials. No Hermes/OpenClaw/Dify/Notion/customer secrets are present.
- [x] Keep live Hermes/OpenClaw as protected local/manual jobs.

Current automation:

```text
.github/workflows/ci.yml
backend-deterministic: py_compile, diff-check, redaction, SQLite pragmas,
startup security, production readiness, Agent Plan integrity, run-start plan
gate, exact collaborator scoping, operator task intake, operator action plan,
Gateway scope matrix, special-character scoped IDs, scoped reads,
task-claim conflict, workspace isolation, bounded operator loop advance, and
operator evidence report, and v1.5 local product acceptance against a temporary
SQLite DB.

ui-build: npm ci + npm run build under ui/start-building-app.
AI Employees optional endpoint fallback: `cd ui/start-building-app && npm run build`.
AI Employees lazy daemon logs: `python3 scripts/operator_action_queue_ui_smoke.py`.
AI Employees responsiveness baseline: `python3 scripts/ai_employees_responsiveness_smoke.py`.
Review decision local refresh: `python3 scripts/review_decision_local_refresh_smoke.py`.
Real-runtime UI confirmation: `python3 scripts/real_runtime_ui_confirm_smoke.py`.
```

The workflow intentionally keeps Hermes/OpenClaw live runtime work out of CI.
Those checks remain protected local/manual acceptance commands with explicit
confirmation and no default credentials.

Merge readiness state itself is also machine-checked:

```bash
python3 scripts/merge_readiness_status_smoke.py
python3 scripts/github_required_checks_smoke.py
python3 scripts/merge_readiness_status_smoke.py --require-ready-to-merge
```

The first command is CI-safe and may pass while the branch is still
`NOT_READY`; it proves the checklist state is internally consistent and the
remaining blockers are explicit. The strict command is for the final merge
candidate and must fail until no unchecked blocker remains, the exact HEAD has
green CI, the working tree is clean and the branch is upstream-synced.

Minimum jobs:

```text
backend-syntax
core-smoke
method-and-knowledge
agent-gateway-security
workspace-scope
review-queue
worker-package
ui-build
secret-scan
diff-check
```

Suggested deterministic suite:

```bash
python3 -m py_compile server.py agentops_mis_cli/*.py agentops_mis_core/*.py agentops_mis_runtime/*.py scripts/*.py
python3 scripts/demo_acceptance.py --start-server
python3 scripts/local_readiness_smoke.py
python3 scripts/secret_scan_smoke.py
python3 scripts/agent_work_method_block_smoke.py
python3 scripts/agentops_cli_install_smoke.py
python3 scripts/agentops_pip_install_smoke.py
python3 scripts/agentops_status_smoke.py
python3 scripts/agentops_doctor_smoke.py
python3 scripts/agent_gateway_scope_matrix_smoke.py
python3 scripts/agent_gateway_special_char_scope_smoke.py
python3 scripts/agent_gateway_session_smoke.py
python3 scripts/workspace_isolation_smoke.py
python3 scripts/task_claim_conflict_smoke.py
python3 scripts/agent_gateway_scoped_read_smoke.py
python3 scripts/agent_gateway_reviewable_lists_smoke.py
python3 scripts/agent_gateway_review_queue_smoke.py
python3 scripts/agent_gateway_runtime_event_smoke.py
python3 scripts/operator_advance_loop_smoke.py
python3 scripts/redaction_policy_smoke.py
python3 scripts/security_production_readiness_smoke.py
python3 scripts/worker_adapter_retry_smoke.py
python3 scripts/worker_stuck_recovery_smoke.py
python3 scripts/workflow_job_stuck_recovery_smoke.py
python3 scripts/kb_bot_demo_smoke.py
python3 scripts/kb_bot_workflow_api_smoke.py
python3 scripts/v1_5_demo_readiness_smoke.py
python3 scripts/v1_5_local_product_acceptance.py
git diff --check
cd ui/start-building-app && npm ci && npm run build
```

Split tests into:

```text
CI-safe deterministic suite
local integration suite
live runtime suite
```

## 11. Functional release gates

### Agent Gateway

- [x] Enrollment request/approval/issuance works. Guarded by `scripts/enrollment_approval_workflow_smoke.py`.
- [x] Raw credential is shown once. Guarded by `scripts/enrollment_credential_ui_smoke.py`.
- [x] Database stores hashes only. Guarded by `scripts/enrollment_credential_ui_smoke.py`, `scripts/enrollment_rotation_smoke.py`, and `scripts/agent_gateway_session_smoke.py`.
- [x] Rotation invalidates old credential. Guarded by `scripts/enrollment_rotation_smoke.py`.
- [x] Revocation cascades sessions. Guarded by `scripts/agent_gateway_session_smoke.py`.
- [x] Expired credentials are rejected. Guarded by `scripts/agent_gateway_session_smoke.py`.
- [x] Missing scope returns 403. Guarded by `scripts/agent_gateway_task_create_scope_smoke.py` and `scripts/agent_gateway_scope_matrix_smoke.py`.
- [x] Workspace/header/query spoofing returns 403. Guarded by `scripts/agent_gateway_task_create_scope_smoke.py`, `scripts/agent_gateway_scoped_read_smoke.py`, and `scripts/agent_gateway_special_char_scope_smoke.py`.
- [x] Task claim is atomic. Guarded by `scripts/task_claim_conflict_smoke.py`.
- [x] Scoped lists and review queue are exact. Guarded by `scripts/collaborator_exact_scope_smoke.py`, `scripts/agent_gateway_review_queue_smoke.py`, and `scripts/agent_gateway_scoped_read_smoke.py`.

### Worker

- [x] Mock one-shot and daemon pass. Guarded by `scripts/agentops_customer_worker_cli_smoke.py` and `scripts/agentops_worker_daemon_cli_smoke.py`.
- [x] API-started local daemon start/restart targets the trusted configured base URL or canonical request origin instead of assuming port 8787. Guarded by `scripts/worker_daemon_resilience_smoke.py` and `docs/WORKER_DAEMON_REQUEST_ORIGIN_ACCEPTANCE.md` on an isolated non-default port.
- [x] Retry/backoff pass. Guarded by `scripts/worker_adapter_retry_smoke.py`.
- [x] Session refresh passes. Guarded by `scripts/worker_session_refresh_smoke.py` against an isolated server with `--no-enforce-intake` for the session-specific contract.
- [x] Stuck recovery passes. Guarded by `scripts/worker_stuck_recovery_smoke.py`.
- [x] Adapter preflight is read-only. Guarded by `scripts/agentops_worker_preflight_smoke.py`.
- [x] Hermes/OpenClaw require explicit confirmation. Guarded by `scripts/worker_live_confirm_gate_smoke.py`.
- [x] Blocked trust prevents live execution. Guarded by `scripts/runtime_connector_trust_smoke.py`.
- [x] Failure writes run/evaluation/audit evidence. Guarded by `scripts/worker_adapter_retry_smoke.py` and `scripts/worker_external_write_preflight_gate_smoke.py`.
- [x] Generic success produces a deliverable Artifact or explicitly declares none required. Guarded by `scripts/agentops_customer_worker_cli_smoke.py`.

### Method and knowledge

- [x] A new Agent can find project spec, workflow and base notes. Guarded by `scripts/agent_plan_integrity_smoke.py` and `scripts/run_start_plan_gate_smoke.py` using `PROJECT_SPEC.md`, `AGENT_WORKFLOW.md`, `knowledge/shared/common_failures.md`, and `base_local_tasks`.
- [x] A plan can be created and verified. Guarded by `scripts/agent_plan_integrity_smoke.py`.
- [x] Plan approval is role-separated. Guarded by `scripts/agent_plan_integrity_smoke.py`, including bound-agent approval rejection.
- [x] Governed execution requires the verified plan. Guarded by `scripts/run_start_plan_gate_smoke.py`.
- [x] Retrieval references are real and visible. Guarded by `scripts/agent_plan_integrity_smoke.py` negative checks for missing specs, missing bases, candidate memory, and unsafe file scope.
- [x] No secret values appear in indexed content: `scripts/knowledge_scope_policy_smoke.py` injects a fake provider token into a temporary knowledge doc, rebuilds the index, and verifies both search output and DB/FTS storage surfaces contain only redacted content.

### Customer workflow

- [x] Synchronous and asynchronous submission work. Template/customer-worker sync paths are covered by existing customer workflow smokes; async template jobs are covered by `scripts/workflow_jobs_list_poll_smoke.py`.
- [x] Jobs can be listed and polled. `GET /api/workflows/jobs` now supports status/type filters, queue summary, and recovery next-actions; `agentops workflow jobs` plus `agentops workflow job-status --wait` are covered by `scripts/workflow_jobs_list_poll_smoke.py`.
- [x] Stuck jobs can be recovered. Existing operator recovery path is covered by `scripts/workflow_job_stuck_recovery_smoke.py`; run it against the target MIS database before shared/commercial deployment evidence capture.
- [x] Delivery board links task/run/artifact/approval/evaluation/audit. `scripts/customer_delivery_board_smoke.py` creates a customer KB template fixture, verifies the board entry links the delivery artifact to task/run URLs, `artifact_link` / `artifact_url`, project-level `approval_links`, `evaluation_links`, `tool_call_links`, `audit_links`, evidence counts, delivery approval gate, and CLI readback.
- [x] Customer report excludes internal prompts and private transcripts. Customer-facing markdown now shows only delivery summary, safety boundary and progress, while run/tool/approval/audit IDs stay in a separate internal evidence payload. Guarded by `scripts/customer_delivery_boundary_smoke.py` and `scripts/customer_project_report_smoke.py`.
- [x] Delivery approval is not confused with tool-action execution approval: KB bot external upload approval is an Approval Wall prepared-action gate; approval alone does not complete the tool/run, and delivery/report approval remains a separate plan-evidence/customer handoff gate. Guarded by `scripts/approval_decision_side_effect_smoke.py`, `scripts/prepared_action_approval_wall_smoke.py`, `scripts/kb_bot_demo_smoke.py`, and `scripts/delivery_approval_manifest_gate_smoke.py`.

### UI

- [x] AI Employees remains useful under partial endpoint failure: optional loaders, panel `ready/running/unavailable` states, local panel refresh, and `Promise.allSettled` contracts are guarded by `scripts/operator_action_queue_ui_smoke.py` and `scripts/ai_employees_responsiveness_smoke.py`.
- [x] Review decisions refresh only needed data: Approvals Inbox, Workspace Home, Evaluation Room, AI Employees review queue, loop record review, and enrollment approval decisions use local state updates or targeted panel refreshes instead of whole-page `refresh()`; guarded by `scripts/review_decision_local_refresh_smoke.py`.
- [x] Real-runtime controls show explicit confirmation: AI Employees and Pixel Office customer dispatch show an explicit live Hermes/OpenClaw confirmation latch; live worker, daemon, async job, Commander package and customer real-run controls are disabled until confirmed, while mock remains the safe default. Guarded by `scripts/real_runtime_ui_confirm_smoke.py`.
- [x] Production-security warnings are prominent: `/workspace/agents` renders a top-level Production Security Boundary strip before operator controls, showing readiness, local write guard, deployment mode, startup security and the copyable next check command. Guarded by `scripts/production_security_warning_ui_smoke.py`, `scripts/security_production_readiness_smoke.py`, and `scripts/operator_action_queue_ui_smoke.py`.
- [x] Issued credentials cannot be re-read: Agent Gateway enrollment list payloads expose `token_omitted:true` and never include raw token/hash fields; `/workspace/agents` displays a fresh create/issue/rotate token only inside a one-time credential card, clears the raw token after copy, clears the card on ordinary refresh/panel refresh/revoke/session actions, and provides explicit copy/clear controls. Guarded by `scripts/enrollment_credential_ui_smoke.py`.
- [x] Pixel Office remains a visualizer: `/workspace/pixel-office` is a native React/CSS MIS operating map, Star-Office stays optional legacy link only, no third-party pixel assets are imported, and customer dispatch routes through MIS workflow APIs with ledger/approval/evidence readback. Guarded by `scripts/pixel_office_visualizer_boundary_smoke.py`.
- [x] Customer delivery is separated from internal evidence: customer report UI separates delivery artifact from report artifact, safety defaults show no raw document/credential storage, delivery board exposes only read-only counts/gates/links, and delivery approval consumes verified plan-evidence gates instead of raw internal transcripts. Guarded by `scripts/customer_delivery_boundary_smoke.py`, `scripts/customer_project_report_artifact_smoke.py`, and `scripts/delivery_approval_manifest_gate_smoke.py`.

## 12. License and provenance

- [x] Add root license: root `LICENSE` declares the proprietary local MVP boundary and preserves third-party license separation.
- [x] Align package metadata: `pyproject.toml` keeps `Proprietary local MVP`; the private UI package and lockfile declare `UNLICENSED`.
- [x] Add third-party notices: `docs/THIRD_PARTY_NOTICES.md` records first-party components, package-manager authority and reference-only project boundaries.
- [x] Record source repository/version/license/usage: `docs/RELEASE_PROVENANCE.md`, `docs/THIRD_PARTY_NOTICES.md`, `docs/PIXEL_OFFICE_REFERENCE_AUDIT.md` and `docs/SBOM_MINIMAL.md` record the local MVP, direct package versions and reference-only OSS usage.
- [x] Record UI/icon/art provenance: Pixel Office is documented as first-party React/CSS geometry with Lucide/package icons only; Star-Office stays optional `VITE_STAR_OFFICE_URL` legacy link.
- [x] Exclude non-commercial Pixel Office assets from commercial build: product UI source contains no bitmap/sprite/tile assets and the release gate blocks Star-Office/LimeZu/paid/unclear-license art until an original `assets/pixel-office/` pack has its own license/provenance.
- [x] Generate minimal SBOM: `docs/SBOM_MINIMAL.md` lists first-party components plus direct npm/Python package inventory; transitive npm versions remain pinned by `ui/start-building-app/package-lock.json`. Guarded by `scripts/license_provenance_smoke.py`.

## 13. Clean-machine RC

- [x] Clean clone RC command chain. Guarded by `scripts/clean_machine_rc_smoke.py`, which clones the exact current HEAD into a temporary directory, rejects tracked runtime/generated files, installs the package, verifies `agentops --help` and `agentops-worker --help`, runs release gates, creates an isolated safe-closure packet, starts a reset local server, inspects the delivery board, and emits only safe statuses, IDs, counts and summaries.

```bash
python3 scripts/clean_machine_rc_smoke.py
python3 -m pip install .
agentops --help
agentops-worker --help
python3 server.py --reset
cd ui/start-building-app && npm ci && npm run build
```

Then run a local mock closure using a submitted and verified Agent Plan, inspect the review queue and delivery board, and record only safe IDs, hashes, statuses and summaries.

Optional live runtime checks occur only after preflight and explicit human confirmation.

## 14. Release evidence packet

- [x] Exact RC SHA. Guarded by `scripts/release_evidence_packet_smoke.py`, which emits the current `git rev-parse HEAD` value at runtime with branch, upstream sync and working-tree summary instead of storing a stale tracked SHA.
- [x] Hardening freeze protocol. Guarded by `scripts/release_freeze_protocol_smoke.py` and `docs/RELEASE_FREEZE_PROTOCOL.md`; default mode proves the freeze protocol and CI-backed release chain, while strict mode requires clean tree, green current-head CI and remote required-check enforcement.
- [x] CI links and status. Guarded by `scripts/release_evidence_packet_smoke.py` and `docs/RELEASE_EVIDENCE_PACKET.md`; CI run URL/status is derived from GitHub Actions env or `gh run list`, and missing/non-green current-head CI keeps the release in `NOT_READY` unless the stricter RC flags pass.
- [x] Test command list and summary. Guarded by `scripts/release_evidence_packet_smoke.py`, which verifies the canonical release command manifest is backed by `.github/workflows/ci.yml` and that referenced script files exist.
- [x] Performance and retrieval baseline. Guarded by `scripts/ai_employees_responsiveness_smoke.py` and `scripts/knowledge_retrieval_quality_smoke.py`; current local evidence stayed within the `/workspace/agents` API fan-out/latency budgets and achieved Recall@5/MRR 1.0 on the bilingual retrieval baseline.
- [x] Migration and rollback result. Guarded by `scripts/migration_rollback_smoke.py`, which writes an isolated `/api/migration/preview` row with rollback steps and audit evidence, then verifies local backup, hash/integrity check, dry-run restore confirmation gate, confirmed restore, and restored migration/audit counts.
- [x] Security readiness and secret scan. Guarded by `scripts/security_production_readiness_smoke.py` plus `scripts/secret_scan_smoke.py`, which scans tracked files for token-like credentials while allowing only narrow fake-token smoke fixtures.
- [x] License/provenance/SBOM. Guarded by `scripts/license_provenance_smoke.py` and `scripts/pixel_office_visualizer_boundary_smoke.py`; evidence files are `LICENSE`, `docs/THIRD_PARTY_NOTICES.md`, `docs/RELEASE_PROVENANCE.md`, `docs/SBOM_MINIMAL.md`, `docs/PIXEL_OFFICE_REFERENCE_AUDIT.md`, and `docs/PIXEL_OFFICE_ASSET_REPLACEMENT_PLAN.md`.
- [x] Plan, task, run, artifact and review IDs for safe closure. Guarded by `scripts/safe_closure_evidence_packet_smoke.py`, which builds an isolated high-risk safe-closure packet and verifies the task, run, Agent Plan, plan hash, tool call, evaluation, artifact, plan-evidence manifest, plan approval, delivery review approval, review-queue observation, audit counts and operator evidence-report readiness without raw prompts, credentials or model output.
- [x] Optional protected live-runtime IDs. Guarded by `scripts/protected_live_runtime_ids_smoke.py`, which records only protected planned-task, connector, prepared-action and approval IDs from read-only adapter readiness, confirm-run gates, blocked runtime trust and fixed-probe prepared actions; the smoke proves no Hermes/OpenClaw provider call or live execution happens before explicit approval/resume.
- [x] Known limitations and public-claims checklist. Guarded by `scripts/public_claims_release_gate_smoke.py` and `docs/PUBLIC_CLAIMS_AND_LIMITATIONS.md`; public/demo copy must keep the local-MVP/NOT_READY, no-hosted-SaaS, no-billing, no-production-fleet, protected-runtime, connector-prepared-action, first-party Pixel Office, license/provenance and no-raw-credentials/prompts/responses/customer-bodies boundaries aligned with tested behavior.

Never include raw credentials, private prompts, raw model responses, customer document bodies, local databases or unsafe runtime logs.

## 15. Final decision

Allowed states:

```text
NOT_READY
READY_FOR_RC
RC_FAILED
RC_PASSED
READY_TO_MERGE
MERGED
```

`READY_TO_MERGE` requires:

```text
[x] all blocking security/correctness gates pass. Guarded by the CI-backed command manifest in `scripts/release_evidence_packet_smoke.py`, the required `Backend deterministic smokes` and `UI build` checks, and the strict local RC gates: `scripts/github_required_checks_smoke.py`, `scripts/release_evidence_packet_smoke.py --require-clean --require-green-ci`, and `scripts/release_freeze_protocol_smoke.py --require-clean --require-green-ci --require-remote-checks`.
[x] CI is green on the exact HEAD. Guarded by `scripts/merge_readiness_status_smoke.py --require-ready-to-merge` and `scripts/release_evidence_packet_smoke.py --require-clean --require-green-ci`, which read the current `git rev-parse HEAD` at runtime and require both protected PR checks to pass: `Backend deterministic smokes` and `UI build`.
[x] clean-machine RC passes. Guarded by `scripts/clean_machine_rc_smoke.py`, which clones the current repository into a temporary directory, checks out the exact current `HEAD`, rejects tracked runtime/generated files, verifies installable CLI entrypoints, runs core RC evidence gates with isolated SQLite state, creates safe closure evidence, starts a reset server, inspects the delivery board, and relies on the dedicated CI UI build job for frontend build evidence.
[x] migration and rollback pass. Guarded by `scripts/migration_rollback_smoke.py`, which uses isolated SQLite state, writes migration preview/audit evidence, verifies backup integrity, requires explicit restore confirmation, and checks restored migration/audit counts.
[x] public claims match tested behavior. Guarded by `scripts/public_claims_release_gate_smoke.py`, which keeps public/demo copy aligned with local-MVP/NOT_READY, no-hosted-SaaS, no-billing, no-production-fleet, protected-runtime and no-raw-secret boundaries.
[x] license and provenance are resolved. Guarded by `scripts/license_provenance_smoke.py`, release provenance/SBOM docs, and Pixel Office commercial asset exclusion evidence.
```

Current state:

```text
READY_TO_MERGE
```
