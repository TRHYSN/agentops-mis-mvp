# AgentOps MIS v1.5 Agent Worker Acceptance

## Build / Syntax

Command:

```bash
python3 -m py_compile server.py scripts/*.py
git diff --check
cd ui/start-building-app && npm run build
```

Result: passed.

## Runtime Prerequisites Used

- AgentOps MIS backend: `http://127.0.0.1:8787`
- Hermes OpenAI-compatible gateway: `http://127.0.0.1:8642`
- OpenClaw CLI: `/opt/homebrew/bin/openclaw`
- Dify and Notion: not used in this acceptance pass.

## Product Validation Rule

Use real Hermes/OpenClaw runs for product-readiness, dogfood, demo, or customer
usefulness claims whenever those runtimes are locally available and explicitly
authorized. Mock adapter runs are deterministic CI/offline fallback evidence
only; they can protect regressions, but they do not prove product-level
completion by themselves. Live validation must still pass through explicit
`confirm_run`, prepared-action approval when required, summary/hash-only ledger
storage, and no raw prompt, raw response, credential, private-message, or
full-transcript persistence.

## Commander-Style Async Management Model

v1.5 is now documented as a local-first commander console, not just a worker
runner. The supported management primitives are:

- Local readiness: `GET /api/local/readiness` and `agentops local readiness`
  provide a read-only local closure check across Agent Gateway, worker fleet,
  adapter routes, evidence chains, memory, approvals, docs, and UI routes.
- Operator action plan: `GET /api/operator/action-plan` and
  `agentops operator action-plan` merge review items, failed benchmark runs,
  customer delivery gates, worker fleet recovery, adapter readiness, and
  commander inbox state into a prioritized read-only next-action queue.
- Operator runtime doctor: `GET /api/operator/runtime-doctor` and
  `agentops operator runtime-doctor` provide a lightweight first-check of local
  MIS API reachability, Hermes/OpenClaw adapter readiness, remote Agent fleet
  state, launch packet availability, handoff/evidence counts, `--confirm-run`,
  prepared-action walls and Codex supervision commands before live runtime
  dispatch. It is read-only, copy-command only, returns deeper
  `operator health` / `operator handoff` commands, and does not start runtimes
  or mutate ledgers/connectors.
- Failed benchmark run handling: `agentops eval review-case-run` can mark a
  failed `evaluation_case_runs` row as `investigating`, `acknowledged`,
  `waived`, or `open`; acknowledged/waived rows stay in the ledger but stop
  blocking review/action queues.
- Commander project board: `GET /api/commander/project-board` summarizes
  local readiness, worker health, recent work packages, pending approvals,
  artifacts, memory candidates, workflow jobs, integration gates, and safe next
  actions without mutating the ledger or executing live work.
- Commander work-package planner:
  `POST /api/commander/work-packages/plan` and
  `agentops commander plan` preview or create planned MIS tasks from one
  customer/operator goal. Preview is read-only; confirmed creation writes
  planned tasks plus commander runtime/audit evidence without executing live
  adapters.
- Commander work-package readback:
  `GET /api/commander/work-packages` and `agentops commander packages` read
  persisted work packages from normal MIS tasks, attach latest run/evidence
  counts, and remain read-only.
- Async workflow management: long Hermes/OpenClaw/customer-template work can be
  submitted as workflow jobs, polled, listed, and recovered through
  `POST /api/workflows/customer-worker-task/submit`,
  `GET /api/workflows/jobs`, `GET /api/workflows/jobs/:job_id`,
  `GET /api/workflows/jobs/stuck`, and
  `POST /api/workflows/jobs/:job_id/mark-failed`.
  The public list response projection lives in
  `agentops_mis_core/workflow_jobs.py`; `server.py` owns query parsing,
  SQLite reads/count producers, async execution, recovery writes and audit.
- Async integration inbox: `GET /api/commander/integration-inbox` provides a
  read-only commander queue for returned worker results, running lanes, blocked
  work, stale work and memory-review items. Each item now includes an
  `integration_decision` projection such as `merge_candidate`,
  `continue_running`, `needs_recovery`, or `needs_memory_review`; the projection
  proves worker output is never auto-applied and records whether a ledger
  decision is required.
- CLI/API-first agent execution: humans use browser pages for dispatch,
  supervision, approval, and review; agents execute through Agent Gateway
  CLI/API/MCP and write run/tool/evaluation/audit/artifact evidence.
- Real Hermes/OpenClaw dogfood evidence: completed live runs are cited by run
  ID only, with summaries/artifact hashes in the ledger and no credentials, raw
  prompts, raw model responses, or private transcripts in the docs.

## Worker Commands Run

Mock adapter:

```bash
python3 scripts/agent_worker.py --once --adapter mock --agent-id agt_worker_local
```

Hermes adapter:

```bash
python3 scripts/agent_worker.py \
  --once \
  --adapter hermes \
  --confirm-run \
  --agent-id agt_worker_local \
  --hermes-gateway-url http://127.0.0.1:8642
```

OpenClaw adapter:

```bash
python3 scripts/agent_worker.py \
  --once \
  --adapter openclaw \
  --confirm-run \
  --agent-id agt_worker_local \
  --openclaw-timeout 180
```

Daemon supervisor API:

```bash
curl -fsS http://127.0.0.1:8787/api/workers/status | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workers/local/start \
  -H "Content-Type: application/json" \
  -d '{"adapter":"mock","poll_interval":1,"max_tasks":1}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workers/local/stop \
  -H "Content-Type: application/json" \
  -d '{"adapter":"all"}' | jq .
```

Agent enrollment CLI/API:

```bash
./scripts/agentops enrollment create --agent-id agt_remote_cli_smoke --name "Remote CLI Smoke" --runtime mock --save-token
./scripts/agentops doctor
./scripts/agentops status
./scripts/agentops agent heartbeat --id agt_remote_cli_smoke --status idle
./scripts/agentops task create --title "Customer task" --owner-agent-id agt_remote_cli_smoke
./scripts/agentops task pull --agent-id agt_remote_cli_smoke --limit 1 --status planned
./scripts/agentops enrollment rotate --token-id agtok_...
./scripts/agentops enrollment revoke --token-id agtok_...
python3 scripts/remote_agent_token_worker_smoke.py
python3 scripts/enrollment_rotation_smoke.py
python3 scripts/kb_bot_demo_smoke.py
python3 scripts/kb_bot_workflow_api_smoke.py
python3 scripts/approval_decision_side_effect_smoke.py
python3 scripts/agentops_cli_install_smoke.py
python3 scripts/agentops_doctor_smoke.py
python3 scripts/agentops_task_create_cli_smoke.py
python3 scripts/agent_gateway_task_create_scope_smoke.py
python3 scripts/agentops_workflow_run_task_smoke.py
python3 scripts/enrollment_health_state_smoke.py
python3 scripts/agentops_status_smoke.py
python3 scripts/redaction_policy_smoke.py
python3 scripts/enrollment_launch_steps_smoke.py
python3 scripts/remote_launch_packet_worker_smoke.py
python3 scripts/agent_gateway_scope_matrix_smoke.py
python3 scripts/agent_gateway_session_smoke.py
python3 scripts/enrollment_approval_workflow_smoke.py
python3 scripts/task_claim_conflict_smoke.py
python3 scripts/worker_stuck_recovery_smoke.py
python3 scripts/worker_session_refresh_smoke.py
python3 scripts/worker_adapter_retry_smoke.py
python3 scripts/customer_task_template_smoke.py
python3 scripts/customer_project_report_smoke.py
python3 scripts/customer_project_report_artifact_smoke.py
python3 scripts/customer_project_index_smoke.py
python3 scripts/task_owner_validation_smoke.py
python3 scripts/commander_work_package_plan_smoke.py
python3 scripts/operator_action_plan_smoke.py --base-url http://127.0.0.1:8787
```

## Evidence

| Path | Adapter | Task | Run | Result |
| --- | --- | --- | --- | --- |
| CLI | mock | `tsk_worker_debug_create` | `run_gw_a20e5b2eb6e3` | completed |
| CLI | hermes | `tsk_worker_hermes_acceptance_20260617145544` | `run_gw_0d793ed6bbac` | completed |
| CLI | openclaw | `tsk_worker_openclaw_acceptance_20260617145647` | `run_gw_9b2a6550d489` | completed |
| CLI live recheck | hermes | `tsk_worker_hermes_live_20260618065503` | `run_gw_6f995c9de929` | completed |
| CLI live recheck | openclaw | `tsk_worker_openclaw_live_20260618065555` | `run_gw_c274e7d62b61` | completed |
| UI | mock | `tsk_worker_ui_mock_20260617150557_657b7768` | `run_gw_8fae81a1bfa6` | completed |
| UI dogfooding | hermes | `tsk_worker_ui_hermes_20260619051023_9265c786` | `run_gw_3b0185dbc228` | failed with `HermesExecutionFailed` and audit/eval evidence |
| UI dogfooding | openclaw | `tsk_worker_ui_openclaw_20260619051334_df7525db` | `run_gw_dc4baad1a546` | completed |
| daemon | mock | `tsk_daemon_acceptance_20260617231559` | `run_gw_6ad797929084` | completed |
| scoped token worker | mock | `tsk_remote_worker_smoke_20260617162927` | `run_gw_876a7c777841` | completed |

All CLI/live adapter runs produced:

- `runs.status = completed`
- one `tool_calls` row with `agent_worker.{adapter}`
- one `evaluations` row with `pass`
- `audit_logs` entries including `agent_worker.task_processed`
- completed task status

Latest live adapter recheck:

```text
Hermes:   run_gw_6f995c9de929 completed via agent_worker.hermes, evaluation pass
OpenClaw: run_gw_c274e7d62b61 completed via agent_worker.openclaw, evaluation pass
```

Latest self-dogfooding operator readiness check:

```text
Hermes:   run_gw_3b0185dbc228 failed after 180s with HermesExecutionFailed, evaluation fail, audit evidence present
OpenClaw: run_gw_dc4baad1a546 completed via agent_worker.openclaw, evaluation pass, audit evidence present
```

Redaction policy recheck:

```text
script: python3 scripts/redaction_policy_smoke.py
safe preserved: 127.0.0.1:8642, tsk_worker_hermes_live_20260618065503, run_gw_6f995c9de929
sensitive redacted: email, phone, bearer, token
HTTP write/read proof: run_gw_dc141fcaab51 kept loopback URL and task id readable
```

The UI-triggered mock worker run was launched from `/workspace/agents` through:

```http
POST /api/workers/local/dispatch-once
```

It produced the same ledger evidence:

```text
runs:        run_gw_8fae81a1bfa6 completed
tool_calls:  agent_worker.mock completed
evaluations: pass
audit_logs:  task.create, worker.dispatch_task.create, task_claim, run.create, run_heartbeat, agent_worker.task_processed, worker.dispatch_once
```

The daemon-triggered mock worker run was not a one-shot dispatch. The sequence was:

```text
POST /api/workers/local/start max_tasks=1
create planned task for agt_worker_daemon_mock
worker loop polls /api/agent-gateway/tasks/pull
worker claims task and writes run/tool/eval/audit
```

Evidence:

```text
tasks:       tsk_daemon_acceptance_20260617231559 completed
runs:        run_gw_6ad797929084 completed
tool_calls:  agent_worker.mock completed
evaluations: pass
audit_logs:  agent_worker.task_processed exists
```

The persistent local daemon smoke also passed:

```text
POST /api/workers/local/start {"adapter":"mock","poll_interval":2,"max_tasks":0}
GET /api/workers/status -> mock daemon running pid=82841
POST /api/workers/local/stop {"adapter":"mock"} -> terminated
```

The daemon resilience smoke also passed:

```text
script: python3 scripts/worker_daemon_resilience_smoke.py
server daemon task: tsk_worker_daemon_resilience_20260618091145
server daemon run: run_gw_29d23509f62e
server daemon state: processed=1 iterations=1 worker_status=sleeping
daemon log: JSONL worker.iteration present
bad-url direct worker: returncode=1 total_errors=2 consecutive_errors=2 status=failed
bad-url backoff: last_sleep_reason=error_backoff last_sleep_sec=0.1
token_omitted: true
```

The scoped-token enrollment smoke passed:

```text
HTTP enrollment:
agent_id: agt_remote_enroll_smoke
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
heartbeat: 200 idle
tasks:read pull: 200
runs:start with missing runs:write: forbidden
revoke: revoked=1
post-revoke pull: 401 token revoked

CLI enrollment:
agent_id: agt_remote_cli_smoke
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
--save-token wrote only to /tmp test config
heartbeat: 200 idle
tasks:read pull: 200
revoke: revoked=1
post-revoke pull: 401 token revoked
```

The enrollment rotation smoke passed:

```text
script: python3 scripts/enrollment_rotation_smoke.py
api old token: [AGENT_GATEWAY_TOKEN_REDACTED]
api new token: [AGENT_GATEWAY_TOKEN_REDACTED]
api old_status_after_rotate: revoked
api new_status_after_rotate: active
cli old token: [AGENT_GATEWAY_TOKEN_REDACTED]
cli new token: [AGENT_GATEWAY_TOKEN_REDACTED]
token_omitted: true
cleanup_revoked: 1
```

The enrollment heartbeat health-state smoke passed:

```text
script: python3 scripts/enrollment_health_state_smoke.py
agent_id: agt_enroll_health_smoke_20260618064218
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
states verified: never_seen -> fresh -> stale -> revoked
token_omitted: true
cleanup_revoked: 1
```

The Agent Gateway CLI status smoke passed:

```text
script: python3 scripts/agentops_status_smoke.py
agent_id: agt_status_cli_smoke_20260618064953
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
states verified: never_seen -> fresh, revoked token rejected
prefix global args supported: true
token_omitted: true
```

Dogfood execution contract for the current AgentOps MIS project:

- Human/customer experience is validated at the task/result level: create a job,
  watch status, review approvals, inspect run ledger, and read artifacts.
- Agent execution is validated through Agent Gateway CLI/API, not browser clicks.
- Browser UI remains the supervision console; it is not the machine contract for
  OpenClaw, Hermes, remote workers, or future Dify/OpenAI File Search adapters.
- Real runtime actions still require explicit `--confirm-run` / `confirm_run`.
- Safe evidence must include run, tool call, evaluation, runtime event, audit,
  artifact, memory candidate, and delivery approval rows without raw prompts,
  raw responses, tokens, or private transcripts.

The remote enrollment launch-step smoke passed:

```text
script: python3 scripts/enrollment_launch_steps_smoke.py
agent_id: agt_launch_steps_smoke_20260618150315
created token: [AGENT_GATEWAY_TOKEN_REDACTED]
rotated token: [AGENT_GATEWAY_TOKEN_REDACTED]
next_steps: package install, env setup, agentops status, adapter preflight, short-lived session command, heartbeat, one-shot agentops-worker, loop agentops-worker, launchd/systemd service template/install/check commands, repo fallback worker for Hermes with --confirm-run and --use-session
raw token in commands: omitted
```

The remote launch-packet worker smoke passed:

```text
script: python3 scripts/remote_launch_packet_worker_smoke.py
agent_id: agt_launch_packet_worker_20260618150315
task_id: tsk_launch_packet_worker_20260618150315
run_id: run_gw_eed70c81def8
session_id: [AGENT_GATEWAY_SESSION_REDACTED]
token mode: agent_token
tool_calls: 1
evaluations: 1
worker used --use-session before processing the task
token_omitted: true
```

The `/workspace/agents` Agent Gateway status card was added and frontend build passed:

```text
npm run build
Agent Gateway card shows ready/auth mode/workspace/scope count/active enrollments/stale heartbeats.
```

The workspace isolation smoke passed:

```text
script: python3 scripts/workspace_isolation_smoke.py
workspace A token pulled only workspace A task: pull_count=1
workspace B task did not leak into pull results
header spoof: forbidden
query spoof: forbidden
cross-workspace claim: forbidden
cross-workspace run start: forbidden
matching workspace run heartbeat: completed
token_omitted: true
latest verified run: run_gw_0b11e7514de6
```

The Agent Gateway scope matrix smoke passed:

```text
script: python3 scripts/agent_gateway_scope_matrix_smoke.py
observer_agent: agt_scope_observer_20260618090750
worker_agent: agt_scope_worker_20260618090750
task_id: tsk_scope_matrix_20260618090750
run_id: run_gw_f06a070b28da
observer allowed: agents:heartbeat, tasks:read, audit:write
observer forbidden: artifact, claim, run_start, tool_call
HTTP status for missing scope: 403 forbidden
token_omitted: true
```

The Agent Gateway task claim conflict smoke passed:

```text
script: python3 scripts/task_claim_conflict_smoke.py
task_id: tsk_claim_conflict_20260618151100
claiming_agent: agt_claim_a_20260618151100
blocked_agent: agt_claim_b_20260618151100
both agents saw pool task before claim: true
same-agent repeat claim idempotent: true
second claim status: forbidden
second start status: forbidden
run_id: run_gw_f3766b73044d
token_omitted: true
```

The worker stuck recovery smoke passed:

```text
script: python3 scripts/worker_stuck_recovery_smoke.py
agent_id: agt_worker_stuck_20260618152538
task_id: tsk_worker_stuck_20260618152538
run_id: run_gw_988eb825e20e
task_status_after: planned
run_status_after: blocked
released_runs: run_gw_988eb825e20e
token_omitted: true
```

The Agent Gateway short-lived session smoke passed:

```text
script: python3 scripts/agent_gateway_session_smoke.py
agent_id: agt_session_smoke_20260618125910
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
session_id: [AGENT_GATEWAY_SESSION_REDACTED]
revoked_session_id: [AGENT_GATEWAY_SESSION_REDACTED]
cascade_session_id: [AGENT_GATEWAY_SESSION_REDACTED]
task_id: tsk_session_smoke_20260618125910
auth_mode: agent_session
session scopes: agents:heartbeat, tasks:read
session list without hash leakage: passed
direct session revoke rejected later use: unauthorized
session cannot mint another session: passed
expired session rejected: unauthorized
parent enrollment revoke cascaded sessions: 1
token_omitted: true
```

The worker session refresh smoke passed:

```text
script: python3 scripts/worker_session_refresh_smoke.py
agent_id: agt_session_refresh_worker_20260618153329
task_ids: tsk_session_refresh_20260618153329_1, tsk_session_refresh_20260618153329_2
run_ids: run_gw_1a886228c52d, run_gw_d43859ff81e3
session_ids: [AGENT_GATEWAY_SESSION_REDACTED], [AGENT_GATEWAY_SESSION_REDACTED], [AGENT_GATEWAY_SESSION_REDACTED]
session_refresh_count: 2
token_omitted: true
```

The worker adapter retry smoke passed:

```text
script: python3 scripts/worker_adapter_retry_smoke.py
agent_id: agt_adapter_retry_20260618153854
retry_task_id: tsk_adapter_retry_20260618153854_success
retry_run_id: run_gw_a572f60ec9f4
retry_attempt_count: 2
confirm_gate_task_id: tsk_adapter_retry_20260618153854_confirm_gate
confirm_gate_run_id: run_gw_9951c583b9a7
confirm_gate_attempt_count: 1
confirm_gate_error_type: ConfirmRunRequired
token_omitted: true
```

The enrollment approval workflow smoke passed:

```text
script: python3 scripts/enrollment_approval_workflow_smoke.py
agent_id: agt_enroll_approval_20260618125200
request_id: enroll_req_7ff7bdf1124c9a66
approval_id: ap_enroll_req_enroll_req_7ff7bdf1124c9a66
task_id: tsk_enroll_req_enroll_req_7ff7bdf1124c9a66
run_id: run_enroll_req_enroll_req_7ff7bdf1124c9a66
premature issue: approval_required
issued token: [AGENT_GATEWAY_TOKEN_REDACTED]
issued token heartbeat: idle
cleanup revoked: 1
token_omitted: true
```

MIS stores token hashes only. Raw token values are shown once at creation time and are not written into audit or runtime events.

The remote-token worker smoke also passed:

```text
script: python3 scripts/remote_agent_token_worker_smoke.py
agent_id: agt_remote_worker_smoke_20260618064228
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
task_id: tsk_remote_worker_smoke_20260618064228
run_id: run_gw_f61363bdf61d
run_status: completed
tool_calls: 1
evaluations: 1
token_omitted: true
revoked: 1
```

The knowledge-base bot customer task smoke passed:

```text
script: python3 scripts/kb_bot_demo_smoke.py
project_id: 20260617185442
created tasks: 6
created runs: 6
created tool_calls: 6
created evaluations: 6
created memories: 6
created runtime_events: 54
created audit_logs: 55
created artifacts: 1
pending approval: ap_gw_f289a8baafcd
delivery artifact: art_kb_bot_delivery_20260617185442
external_upload_performed: false
credentials_stored: false
raw_documents_stored: false
```

The delivery artifact is written through:

```http
POST /api/agent-gateway/artifacts
```

It stores a customer-readable title, summary, URI, and content hash metadata only. It does not store raw source documents, credentials, or full private transcripts.

The live UI ledger pass also passed:

```text
page: /workspace/approvals
source: GET /api/approvals
rows: 12
latest pending approval: ap_gw_f289a8baafcd
actions: approve/reject buttons call POST /api/approvals/:id/approve|reject

page: /admin/toolcalls
source: GET /api/tool-calls
rows: 6928
latest tool: artifact.delivery_summary

page: /admin/tasks/tsk_kb_bot_20260617185442_06
source: GET /api/tasks/tsk_kb_bot_20260617185442_06
delivery artifacts: 1
artifact: art_kb_bot_delivery_20260617185442
```

These pages no longer use `mockData` for the primary ledger rows.
Task detail now surfaces delivery artifacts and links related runs to Run Detail.

The browser-facing knowledge-base bot workflow API smoke also passed:

```text
script: python3 scripts/kb_bot_workflow_api_smoke.py
endpoint: POST /api/workflows/kb-bot-project
project_id: 20260617190650
steps: 6
final task: tsk_kb_bot_20260617190650_06
final run: run_gw_b365e7e325c6
delivery artifact: art_kb_bot_delivery_20260617190650
pending approval: ap_gw_8002e643f058
```

This is the same path used by Pixel Office's "Generate KB bot project" action.

The customer task template smoke passed:

```text
script: python3 scripts/customer_task_template_smoke.py
template_count: 3
template_id: tpl_customer_kb_qa_bot
project_id: 20260618154535
steps: 6
final task: tsk_kb_bot_20260618154535_06
final run: run_gw_cfde4c4822b1
delivery artifact: art_kb_bot_delivery_20260618154535
pending approval: ap_gw_956174266d1a
```

The customer task template CLI smoke passed:

```text
script: python3 scripts/agentops_workflow_template_cli_smoke.py
commands:
  ./scripts/agentops workflow templates
  ./scripts/agentops workflow run-template --template-id tpl_customer_kb_qa_bot
template_count: 3
project_id: 20260620165509944069
steps: 6
final task: tsk_kb_bot_20260620165509944069_06
final run: run_gw_5efecc40662f
delivery artifact: art_kb_bot_delivery_20260620165509944069
pending approval: ap_gw_63ba94be6f35
report_url: /api/workflows/customer-projects/20260620165509944069/report
secret_leaked: false
```

Pixel Office's customer dispatch panel now loads `GET /api/workflows/customer-task-templates`, applies template defaults to the customer task form, and runs the selected template through `POST /api/workflows/customer-task-templates/run`.
The same template list/run path is available to external agents and operators through `agentops workflow templates` and `agentops workflow run-template`, so the browser is not required for machine-facing dispatch.
`agentops workflow run-template` now also accepts `--adapter mock|hermes|openclaw`; with an adapter it maps the selected template into the Agent Worker loop and returns run/tool/evaluation/audit/artifact/memory/approval evidence. Hermes/OpenClaw still require `--confirm-run`, and long runs should pass `--request-timeout` or set `AGENTOPS_REQUEST_TIMEOUT`.
After a template-backed KB project completes, the same panel also exposes an `Archive report to ledger` / `归档报告到账本` action backed by `POST /api/workflows/customer-projects/:project_id/report-artifact`.
The report link now opens the customer-facing route `/workspace/customer-projects/:project_id/report`, which renders counts, safety boundary, delivery/report artifact ids, approvals, and the ledger-backed markdown report instead of exposing raw API JSON.

Live template worker dogfood passed:

```text
script: scripts/template_worker_live_dogfood.py
manual commands:
  ./scripts/agentops workflow run-template --template-id tpl_customer_ui_review --adapter openclaw --confirm-run
  ./scripts/agentops workflow run-template --template-id tpl_customer_ui_review --adapter hermes --confirm-run --request-timeout 420
OpenClaw run: run_gw_f564c767fa0b
OpenClaw artifact: art_customer_worker_task_run_gw_f564c767fa0b
Hermes run: run_gw_99c4e69cae16
Hermes artifact: art_customer_worker_task_run_gw_99c4e69cae16
evidence: tool_calls>=1, evaluations>=1, audit_logs>=1, artifacts>=1, memories>=1, approvals>=1
```

Async workflow job smoke passed:

```text
script: python3 scripts/agentops_workflow_async_job_smoke.py
command:
  ./scripts/agentops workflow run-template --template-id tpl_customer_ui_review --adapter mock --async-job
  ./scripts/agentops workflow job-status --job-id wfjob_6cfffb10338d --wait
job: wfjob_6cfffb10338d
run: run_gw_8eb6ebe95392
artifact: art_customer_worker_task_run_gw_8eb6ebe95392
evidence: tool_calls=1, evaluations=1, audit_logs=10, artifacts=1, memories=2, approvals=1
secret_leaked: false
```

Pixel Office async job UI verification passed:

```text
page: /workspace/pixel-office
build: cd ui/start-building-app && npm run build
browser check: Playwright snapshot
visible region: 异步 Workflow Jobs
visible evidence: job status, adapter/template id, task link, run link, artifact id
latest visible job: wfjob_6cfffb10338d -> run_gw_8eb6ebe95392
```

The customer project report smoke passed:

```text
script: python3 scripts/customer_project_report_smoke.py
project_id: 20260618155050
report_url: /api/workflows/customer-projects/20260618155050/report
tasks: 6
runs: 6
tool_calls: 6
pending approvals: 1
delivery artifact: art_kb_bot_delivery_20260618155050
pending approval: ap_gw_3d9c930d4a92
safety boundary: external_upload=false, credentials_stored=false, raw_documents_stored=false
```

The customer project report artifact smoke passed:

```text
script: python3 scripts/customer_project_report_artifact_smoke.py
project_id: 20260618180442453801
delivery artifact: art_kb_bot_delivery_20260618180442453801
report artifact: art_customer_project_report_20260618180442453801
report_url: /api/workflows/customer-projects/20260618180442453801/report
content_hash: f359ab142aec827906a346728c3becc3949290c724f2d6ab26009e26bacb4c81
audit action: workflow.customer_project.report_artifact
raw_report_omitted: true
```

The report artifact is separate from the customer delivery artifact. `GET /api/workflows/customer-projects/:project_id/report` continues to return the delivery artifact as `artifact_id` and returns the persisted report artifact as `report_artifact_id`.
KB bot customer project IDs now include microseconds so concurrent report/report-artifact smokes do not create duplicate task IDs.
Frontend verification for the Pixel Office report-archive action: `cd ui/start-building-app && npm run build` passed.
Frontend verification for the customer-facing report route: `cd ui/start-building-app && npm run build` passed.

The customer project index smoke passed:

```text
script: python3 scripts/customer_project_index_smoke.py
project_id: 20260619050143610862
indexed: true
status: waiting_approval
delivery artifact: art_kb_bot_delivery_20260619050143610862
pending approvals: 1
total projects: 23
```

The task owner validation smoke passed:

```text
script: python3 scripts/task_owner_validation_smoke.py
status: 400
error: owner_agent_not_found
owner_agent_id: agt_missing_owner_validation_smoke
```

The product dogfooding run used AgentOps MIS to develop/review AgentOps MIS itself:

```text
Hermes worker:
task: tsk_selfdev_ux_review_hermes_20260619045910757742
run: run_gw_eb4df4e82235
tool: agent_worker.hermes completed
evaluation: eval_gw_run_gw_eb4df4e82235_rule pass
finding: Pixel Office dispatch lacks next-step/context guidance for non-technical owners.

OpenClaw worker:
task: tsk_selfdev_ux_review_openclaw_20260619045910757742
run: run_gw_8160a11a2323
tool: agent_worker.openclaw completed
evaluation: eval_gw_run_gw_8160a11a2323_rule pass
finding: First-time users may not recognize how to start real work; homepage/dashboard should surface a direct start path.
```

Dogfooding follow-up implemented:

```text
Workspace Home now exposes a direct start strip:
- Start a customer project
- Check worker readiness
- Open delivery reports

Pixel Office customer dispatch now includes a three-step owner guide:
- Choose a template
- Dispatch to AI team
- Approve and deliver

verification: cd ui/start-building-app && npm run build
```

The approval decision side-effect smoke passed:

```text
script: python3 scripts/approval_decision_side_effect_smoke.py
approved approval: ap_gw_fd2603a88c4f
approved tool: tc_gw_ddbecfc76ebd -> completed
approved run: run_gw_ccf41fbdd066 -> completed
approved task: tsk_kb_bot_20260617191012_03 -> completed
rejected approval: ap_gw_62f7412387d7
rejected tool: tc_gw_f3348b639fa3 -> blocked
rejected run: run_gw_ff3d3fbc5a90 -> blocked
rejected task: tsk_kb_bot_20260617191013_03 -> blocked
reason preservation: passed
```

The local CLI install smoke passed:

```text
script: python3 scripts/agentops_cli_install_smoke.py
installed command: agentops
temporary shim help: passed
token_written: false
```

The pip editable CLI package smoke passed:

```text
script: python3 scripts/agentops_pip_install_smoke.py
install: uv pip install <repo>, building/installing the local source package
build dependencies: offline backend, no setuptools/wheel download required
command: temporary venv/bin/agentops
help: passed
login: passed without writing token
status: provider agent_gateway
token_omitted: true
token_written: false
```

The pip worker package smoke passed:

```text
script: python3 scripts/agentops_worker_package_smoke.py
install: uv pip install <repo>, building/installing the local source package
command: temporary venv/bin/agentops-worker
help: passed
preflight: passed, read-only, token omitted
one-shot no-task loop: passed against a local Agent Gateway stub
gateway requests: register, tasks/pull, heartbeat
state_written: true, outside repo temp dir
token_omitted: true
service templates: launchd KeepAlive and systemd Restart=always rendered with token placeholder only
```

The CLI doctor smoke passed:

```text
script: python3 scripts/agentops_doctor_smoke.py
local mode: ok, api_key_source missing, token_omitted true
env scoped-token mode: ok, api_key_source env, token_omitted true
token_leaked: false
cleanup: revoked scoped token
```

The CLI worker status smoke passed:

```text
script: python3 scripts/agentops_worker_status_smoke.py
command: ./scripts/agentops worker status
provider: agentops-worker
daemon summary: present
secret_leaked: false
```

The CLI worker daemon smoke passed:

```text
script: python3 scripts/agentops_worker_daemon_cli_smoke.py
commands: ./scripts/agentops worker start/status/logs/stop
adapter: mock
secret_leaked: false
```

On this machine, `python3 scripts/install_agentops_cli.py --force` installed:

```text
~/.local/bin/agentops -> repo scripts/agentops shim
```

For remote machines with Python, the package path is now:

```bash
python3 -m pip install .
agentops doctor
agentops status
agentops-worker --once --adapter mock --use-session --session-ttl-sec 900
```

This keeps the same env/config auth model and JSON output contract as `./scripts/agentops`.

Latest repeat run after adding the enrollment UI:

```text
script: python3 scripts/remote_agent_token_worker_smoke.py
agent_id: agt_remote_worker_smoke_20260617170534
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
task_id: tsk_remote_worker_smoke_20260617170534
run_id: run_gw_4b4e7af5275c
run_status: completed
tool_calls: 1
evaluations: 1
token_omitted: true
revoked: 1
```

Latest repeat run after adding worker fleet telemetry:

```text
script: python3 scripts/remote_agent_token_worker_smoke.py
agent_id: agt_remote_worker_smoke_20260617180040
token_id: [AGENT_GATEWAY_TOKEN_REDACTED]
task_id: tsk_remote_worker_smoke_20260617180040
run_id: run_gw_38fe52256f67
run_status: completed
tool_calls: 1
evaluations: 1
token_omitted: true
revoked: 1
```

The `/workspace/agents` enrollment UI was verified in the in-app browser:

```text
url: http://127.0.0.1:19001/workspace/agents
visible labels:
  AI 员工
  本地 Worker 循环
  远程 Agent 接入
  创建接入 token
  最近接入记录
```

The `/workspace/agents` approval-gated enrollment UI was verified with Playwright snapshot:

```text
url: http://127.0.0.1:19001/workspace/agents
visible labels:
  提交审批申请
  审批式接入申请
  审批后发 token
```

The `/workspace/agents` short-lived session controls were verified with Playwright snapshot:

```text
url: http://127.0.0.1:19001/workspace/agents
visible labels:
  有效 Session
  最近短期 Session
  吊销 session
```

The UI reads:

```http
GET /api/agent-gateway/enrollments
GET /api/agent-gateway/sessions
```

and can call:

```http
POST /api/agent-gateway/enrollment/create
POST /api/agent-gateway/enrollment/revoke
POST /api/agent-gateway/enrollment/rotate
POST /api/agent-gateway/session/revoke
```

Raw tokens are displayed only in the create/rotate response panel and are not persisted in frontend state beyond the current page session.

The `/workspace/agents` worker fleet telemetry UI was verified in the in-app browser:

```text
url: http://127.0.0.1:19001/workspace/agents
visible labels:
  运营就绪
  本地 worker 循环
  真实运行派发
  远程 agent 接入
  恢复队列
  Worker Fleet 观测
  Daemon 日志
  最近网关事件
  卡住任务
  暂无卡住的运行中任务。
  mock
  hermes
  openclaw
```

The same Playwright snapshot showed release runtime evidence:

```text
Released tsk_worker_stuck_20260618152332 back to planned worker queue.
```

The log API also returned daemon log evidence:

```text
GET /api/workers/local/logs?adapter=mock
adapter: mock
status: stopped
log_tail_lines: 80
log_path_present: true
```

The operator readiness strip is intentionally product-facing rather than developer-only. It makes the self-dogfooding model explicit:

- local worker loop: safe `mock` dry-run versus confirmed Hermes/OpenClaw live dispatch;
- real runtime dispatch: Hermes/OpenClaw adapter runs are normal MIS tasks and write ledger evidence;
- remote agent entry: scoped enrollment token, short-lived session, heartbeat, and workspace-bound permissions;
- recovery queue: stale running tasks can be released back to planned with linked run/audit evidence.

`python3 scripts/demo_acceptance.py` also passed after this UI change.

## Operator CLI Preflight And Live Gate

The main `agentops` CLI now exposes the same read-only adapter preflight path
as `agentops-worker`:

```bash
agentops worker preflight --adapter mock
agentops worker preflight --adapter hermes
agentops worker preflight --adapter openclaw
```

The preflight command checks Agent Gateway plus adapter readiness and returns
`live_execution_performed=false`; it does not pull a task, claim work, start a
run, execute a runtime, or write ledger rows.

Latest smoke evidence:

```text
script: python3 scripts/agentops_worker_preflight_smoke.py
mock_ready: true
hermes_preflight_returned: true
live_execution_performed: false
secret_leaked: false
```

Hermes/OpenClaw local daemon starts also fail closed without explicit
confirmation:

```text
script: python3 scripts/worker_live_confirm_gate_smoke.py
hermes: blocked without --confirm-run
openclaw: blocked without --confirm-run
secret_leaked: false
```

The pip source-package smoke now verifies installed `agentops worker preflight`
as well as installed `agentops worker status/logs`:

```text
script: python3 scripts/agentops_pip_install_smoke.py
worker_preflight_provider: agentops-worker
worker_preflight_live_execution_performed: false
token_written: false
```

The end-to-end operator/customer runbook is
`docs/REMOTE_WORKER_OPERATIONS_RUNBOOK.md`.

## Customer Worker Task Workflow

Customer-facing task dispatch now has a direct worker-backed workflow:

```http
POST /api/workflows/customer-worker-task
```

This route creates a normal MIS task, executes it through the Agent Gateway
worker loop, and writes customer-delivery evidence back to:

- `runs`
- `tool_calls`
- `evaluations`
- `runtime_events`
- `audit_logs`
- `artifacts` with type `customer_worker_result`

It is intentionally different from the admin-only worker panel: the customer
provides the goal and acceptance criteria, while the AI employee uses the
machine-facing Agent Gateway/worker path to do the work.

Latest safe smoke:

```text
script: python3 scripts/customer_worker_task_workflow_smoke.py
mock run: run_gw_1a8bc4a76537
artifact: art_customer_worker_task_run_gw_1a8bc4a76537
tool_calls: 1
evaluations: 1
runtime_events: 8
audit_logs: 8
artifacts: 1
Hermes without confirm_run: planned task, confirm_run_required_for_live_adapter
```

Latest CLI smoke:

```text
script: python3 scripts/agentops_customer_worker_cli_smoke.py
command: ./scripts/agentops workflow customer-worker-task
mock run: run_gw_c42822816a69
artifact: art_customer_worker_task_run_gw_c42822816a69
tool_calls: 1
evaluations: 1
runtime_events: 8
audit_logs: 8
artifacts: 1
Hermes without confirm_run: planned task, confirm_run_required_for_live_adapter
secret_leaked: false
```

Latest async customer worker job smoke:

```text
script: python3 scripts/customer_worker_async_job_smoke.py
command: ./scripts/agentops workflow customer-worker-task --async-job
job: wfjob_ea7b472330ab
status: completed
run: run_gw_daa11c182e8e
artifact: art_customer_worker_task_run_gw_daa11c182e8e
evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
secret_leaked: false
```

Latest `/workspace/agents` UI async job verification:

```text
page: http://127.0.0.1:19001/workspace/agents
Playwright snapshot found: 异步提交 Job, 异步 Workflow Jobs, recent wfjob rows
clicked: 异步提交 Job
created job: wfjob_883db124baf5
status via API: completed
run: run_gw_d6265c5457f5
artifact: art_customer_worker_task_run_gw_d6265c5457f5
evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
```

Latest workflow job stuck recovery smoke:

```text
script: python3 scripts/workflow_job_stuck_recovery_smoke.py
stale job: wfjob_stuck_smoke_20260621101938286295
agentops workflow stuck-jobs: listed stale queued/running job
agentops workflow job-mark-failed: marked job failed after operator review
final_status: failed
secret_leaked: false
```

Latest `/workspace/agents` workflow job recovery UI verification:

```text
page: http://127.0.0.1:19001/workspace/agents
synthetic stale job: wfjob_ui_stuck_recovery_20260621102100
Playwright snapshot found: 卡住的 Workflow Jobs, job row, 标记 failed
clicked: 标记 failed
API status: failed
error_message: 操作台标记卡住 workflow job 为 failed
token_omitted: true
```

Latest task-create CLI smoke:

```text
script: python3 scripts/agentops_task_create_cli_smoke.py
command: ./scripts/agentops task create -> scripts/agent_worker.py --once
task: tsk_task_create_cli_smoke_20260619185821167609
run: run_gw_cf5f926cf3d0
tool_calls: 1
evaluations: 1
task_status: completed
run_status: completed
token_omitted: true
```

Repeat verification during final acceptance also passed with run `run_gw_32d433dee013`.

Latest CLI inspect smoke:

```text
script: python3 scripts/agentops_cli_inspect_smoke.py
workflow path: ./scripts/agentops workflow customer-worker-task --adapter mock
task: tsk_worker_ui_mock_20260621102821_3d8b0329
run: run_gw_43af64100b4f
artifact: art_customer_worker_task_run_gw_43af64100b4f
commands verified: task get, task list, run get, run list, run graph, artifact list
task evidence: runs 1, approvals 1, evaluations 1, memories 2, artifacts 1
run evidence: tool_calls 1, approvals 1, evaluations 1, artifacts 1
secret_leaked: false
```

Latest scoped Agent Gateway readback smoke:

```text
script: python3 scripts/agent_gateway_scoped_read_smoke.py
workspace A: ws_read_a_20260621103754750346
workspace B: ws_read_b_20260621103754750346
task A: tsk_scoped_read_a_20260621103754750346
task B read with workspace A token: forbidden
run: run_gw_6068ddc0b9bd
artifact: art_gw_run_gw_6068ddc0b9bd_scoped_read_fixture_artifact
commands/endpoints verified: GET /api/agent-gateway/tasks, tasks/:id, runs, runs/:id, runs/:id/graph, artifacts
scope: tasks:read, constrained to token workspace and visible task/run/artifact evidence
secret_leaked: false
```

Latest workspace visual style switch verification:

```text
page: http://127.0.0.1:19001/workspace/agents
styles: enterprise, ops, workforce
default migration: light/enterprise/unknown -> enterprise; ops/dark -> ops; workforce -> workforce
Playwright: button changed from 控制面 to 员工 OS, then to 企业版 after fresh snapshot/click
build: cd ui/start-building-app && npm run build
note: this is a presentation/customer usability layer only; it does not change Agent Gateway, worker, or ledger behavior
```

Latest scoped Gateway task-create smoke:

```text
script: python3 scripts/agent_gateway_task_create_scope_smoke.py
task: tsk_gateway_task_create_scope_20260619190748215805
allowed token had tasks:create
missing tasks:create scope: rejected
other agent impersonation: rejected
other workspace impersonation: rejected
token_omitted: true
```

Latest one-command workflow run-task smoke:

```text
script: python3 scripts/agentops_workflow_run_task_smoke.py
command: ./scripts/agentops workflow run-task
mock task: tsk_7ff17228045a
mock run: run_gw_518e62e77161
agent_plan: plan_57220e046f04bf45 verified=true
plan_evidence: pem_1907d07d2e43ee13 verified=true
tool_calls: 1
evaluations: 1
manifest evidence: tool_calls 1, evaluations 1, artifacts 1, audit_logs 11
Hermes without confirm_run: planned task tsk_1ee9039cee39, confirm_run_required_for_live_adapter
secret_leaked: false
```

Latest CLI/API-first local live dogfood runs for the current AgentOps MIS project:

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter openclaw --adapter hermes --request-timeout 720 --hermes-timeout 600
execution contract: customer/operator uses browser for oversight; agents use Agent Gateway CLI/API for execution
title: 通过 Agent Gateway CLI 让真实 Hermes / OpenClaw 优化 AgentOps MIS
OpenClaw run: run_gw_7ede8c8cc5c9
OpenClaw artifact: art_customer_worker_task_run_gw_7ede8c8cc5c9
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
Hermes run: run_gw_1e864c5f6b18
Hermes artifact: art_customer_worker_task_run_gw_1e864c5f6b18
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
failures: []
contract: agents execute through Agent Gateway CLI/API; the browser only dispatches, supervises, approves, and reads delivery evidence.
```

Latest live commander-management proof runs for the current AgentOps MIS project:

```text
source: local MIS ledger, not hard-coded test fixtures
execution contract: Agent Gateway CLI/API-first live dogfood; browser is commander oversight only
OpenClaw run: run_gw_5f4a3320a4d3
OpenClaw task: tsk_worker_ui_openclaw_20260621114057_4f5cbc75
OpenClaw status: completed
OpenClaw evidence: tool_calls 1, evaluations 1, audit references 3, artifacts 1
Hermes run: run_gw_f7fe3a78cadb
Hermes task: tsk_worker_ui_hermes_20260621114203_9e6cc64a
Hermes status: completed
Hermes evidence: tool_calls 2, evaluations 1, audit references 6, artifacts 2
safety: credentials, raw prompts, raw model responses, and private transcripts omitted
```

Previous CLI/API-first local live dogfood runs:

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter openclaw --request-timeout 420
execution contract: customer/operator uses browser for oversight; agents use Agent Gateway CLI/API for execution
title: 通过 Agent Gateway CLI 让真实 Hermes / OpenClaw 优化 AgentOps MIS
OpenClaw run: run_gw_d99689fecad9
OpenClaw artifact: art_customer_worker_task_run_gw_d99689fecad9
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
failures: []
```

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter hermes --request-timeout 720 --hermes-timeout 600
execution contract: customer/operator uses browser for oversight; agents use Agent Gateway CLI/API for execution
Hermes run: run_gw_1f4a26e22d50
Hermes artifact: art_customer_worker_task_run_gw_1f4a26e22d50
Hermes status: failed
Hermes error: Remote end closed connection without response
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 9, audit_logs 10, artifacts 1, memories 1, approvals 1
product implication: MIS CLI/API and ledger writeback are working; the Hermes gateway/runtime needs crash recovery or daemon supervision before it can be treated as a reliable long-running customer worker.
```

Latest real OpenClaw live dogfood plus CLI inspect:

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter openclaw --request-timeout 420
execution contract: customer/operator uses browser for oversight; agents use Agent Gateway CLI/API for execution
OpenClaw run: run_gw_2e170f71d433
OpenClaw task: tsk_worker_ui_openclaw_20260621102836_2cf776dc
OpenClaw artifact: art_customer_worker_task_run_gw_2e170f71d433
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
inspect commands: ./scripts/agentops run get --run-id run_gw_2e170f71d433; ./scripts/agentops artifact list --run-id run_gw_2e170f71d433; ./scripts/agentops run graph --run-id run_gw_2e170f71d433
inspect result: run status completed, runtime_type openclaw, artifact count 1, token_omitted true
```

Earlier local live dogfood runs for the current AgentOps MIS project:

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter hermes
run: run_gw_4b92508d1e33
artifact: art_customer_worker_task_run_gw_4b92508d1e33
tool_calls: 1
evaluations: 1
runtime_events: 8
audit_logs: 8
artifacts: 1
```

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter openclaw
run: run_gw_328d56c280fa
artifact: art_customer_worker_task_run_gw_328d56c280fa
tool_calls: 1
evaluations: 1
runtime_events: 8
audit_logs: 8
artifacts: 1
```

Latest combined live dogfood run after adding the `/workspace/agents` customer task dispatch entry:

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter hermes --adapter openclaw
title: 用真实 Hermes / OpenClaw 优化 AgentOps MIS 客户工作台
Hermes run: run_gw_2d0834183ca0
Hermes artifact: art_customer_worker_task_run_gw_2d0834183ca0
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 8, audit_logs 8, artifacts 1
OpenClaw run: run_gw_409f4f1d3063
OpenClaw artifact: art_customer_worker_task_run_gw_409f4f1d3063
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 8, audit_logs 8, artifacts 1
failures: []
```

Latest governance-closure verification for generic customer worker tasks:

```text
script: python3 scripts/customer_worker_task_workflow_smoke.py
mock run: run_gw_161d789c4469
mock artifact: art_customer_worker_task_run_gw_161d789c4469
mock evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
confirm gate task: tsk_customer_worker_plan_b76345a484f55007
failures: []
```

```text
script: python3 scripts/customer_worker_live_dogfood.py --adapter hermes --adapter openclaw
title: 用真实 Hermes / OpenClaw 优化 AgentOps MIS 客户工作台
Hermes run: run_gw_5d998a53e469
Hermes artifact: art_customer_worker_task_run_gw_5d998a53e469
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
OpenClaw run: run_gw_4c3b2d5b43ac
OpenClaw artifact: art_customer_worker_task_run_gw_4c3b2d5b43ac
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 10, audit_logs 10, artifacts 1, memories 2, approvals 1
failures: []
```

Generic customer worker tasks now create a customer delivery artifact, a memory
candidate, and a pending delivery acceptance approval after the worker run
finishes. The run remains the machine execution record; the task moves into
human delivery review through normal approval UI instead of treating raw adapter
output as automatically accepted.

Latest runtime connector trust registry smoke:

```text
script: python3 scripts/runtime_connector_trust_smoke.py
connector: rtc_openclaw_local
blocked_task: tsk_customer_worker_trust_blocked_30651ba025db2763
blocked_reason: runtime_connector_trust_blocked
restore_status: 200
failures: []
```

The smoke marks the OpenClaw runtime connector `blocked`, attempts a confirmed
OpenClaw customer worker run, verifies that live execution is rejected before
adapter invocation, and then restores the connector to `trusted`. The trust
decision writes runtime event and audit evidence; it does not store secrets.

Latest worker service diagnostics smoke:

```text
script: python3 scripts/agentops_worker_service_check_smoke.py
direct_ok: true
cli_ok: true
unsafe_detected: true
failures: []
```

`agentops-worker service-check` and `agentops worker service-check` now provide
read-only launchd/systemd diagnostics for agent machines. The check validates a
generated worker service template, confirms raw service content is omitted, and
fails closed when token-like values are present without printing those values.
It also reports a machine-readable relaunch policy:

```text
launchd: KeepAlive=true
systemd: Restart=always, RestartSec=5
```

It does not install, load, restart, or execute a service.

Latest worker service install smoke:

```text
script: python3 scripts/agentops_worker_service_install_smoke.py
dry_run_ok: true
install_wrote: true
wrapper_wrote: true
unsafe_blocked: true
failures: []
```

`agentops-worker service-install` and `agentops worker service-install` now add
a dry-run-by-default installation path for long-running agent machines. Without
`--confirm-install` the command only returns the target path, template hash, and
manual load commands. With `--confirm-install` it writes a placeholder-based
service file with `0600` permissions, refuses to overwrite existing files unless
`--overwrite` is present, and blocks token-like placeholders without leaking
them. `agentops-worker service-control` and `agentops worker service-control`
now add preview-first launchd/systemd load, unload, and restart control. Without
`--confirm-control` the command only returns planned OS commands and service
check evidence. With `--confirm-control` it may mutate local OS service state on
the agent machine, while still refusing load/restart for token-like service
files or Hermes/OpenClaw templates missing `--confirm-run`. Confirmed `load`
is idempotent when the service is already loaded: it returns
`service_control_skipped:true`, does not call launchd/systemd again, and does
not count as live runtime execution.

Latest remote worker fleet status smoke:

```text
script: python3 scripts/worker_remote_fleet_status_smoke.py
states: never_seen -> fresh -> stale
active_sessions: 1
token_omitted: true
secret_leaked: false
```

`GET /api/workers/status` and `agentops worker status` now include remote worker
fleet health derived from scoped enrollment and short-lived session metadata:
active remote worker count, total enrollment count, heartbeat state counts,
active session count, and recent remote worker rows. Token and session IDs are
represented only by short irreversible refs, so `agtok_` / `agtsess_` shaped
values do not appear in the worker status output.

The remote worker/session fleet summary public projections now live in
`agentops_mis_core/worker_fleet.py`. `server.py` still owns scoped enrollment
and session SQLite reads plus the agent-row lookup, but raw token ids, parent
token ids and session ids are omitted before the data becomes a public worker
status payload.

The same status payload now includes a machine-facing `fleet_health` block for
agent operators and automation:

```text
overall: ready | attention | blocked
contract: agents execute through Agent Gateway CLI/API; browser UI is an operator console only
gates: worker_task_recovery, workflow_job_recovery, execution_capacity,
       remote_heartbeats, session_hygiene, local_daemons
recommended_actions: safe next CLI commands
token_omitted: true
```

This is the product boundary for debugging with real OpenClaw/Hermes/remote
agents: the browser can supervise results, but workers should consume CLI/API
health, pull tasks, claim runs, and write evidence through Agent Gateway.

`GET /api/workers/adapter-readiness` and `agentops worker readiness` now expose
the route-selection layer for workers. It reports `mock`, `hermes`, and
`openclaw` readiness in one safe read-only payload, including runtime connector
trust status, target resource, availability checks, `summary.recommended_adapter`,
and `live_execution_performed:false`. This lets a local or remote agent decide
which adapter path is currently usable before pulling customer work.
The `/workspace/agents` operator console now renders the same adapter route
state, including trust status, target resource, live-ready marker, and the next
safe CLI action for each adapter. The customer dispatch form also shows the
selected adapter's route readiness and disables confirmed live dispatch when the
selected Hermes/OpenClaw route is unavailable or blocked.

Confirmed live customer-worker dispatch now consumes that readiness signal. If a
selected Hermes/OpenClaw adapter is `unavailable` or `blocked`, MIS returns
`reason: adapter_not_ready` for adapter availability failures or
`runtime_connector_trust_blocked` for trust-policy blocks, creates a blocked
recovery task, records runtime/audit evidence, and does not execute the runtime.
Async customer-worker submit uses the same gate: it returns
the same early rejection reason, creates a failed workflow job plus blocked task
evidence, and does not queue background execution.
Template async jobs that use a live worker adapter now use the same early gate.

The `/workspace/agents` UI now exposes the same customer-worker path that the
CLI and Pixel Office use: a human creates a normal business task, chooses
mock/Hermes/OpenClaw, and MIS displays the resulting task, run, artifact, and
evidence counts. This is still governed by the same live confirmation boundary:
mock is safe by default, while Hermes/OpenClaw require explicit confirmation.

A real queue collision was found and fixed during this dogfood pass: the first
Hermes live run picked an older planned confirmation-gate task because it reused
the same worker agent id. The workflow now creates a unique customer worker
agent id per dispatch unless explicitly overridden, so each customer task is
processed by its intended worker queue.

Hermes live execution also exposed a fixed 180s HTTP timeout. The worker now
supports `--hermes-timeout` / `HERMES_TIMEOUT`, and the customer worker workflow
passes a 300s Hermes timeout for live dogfood runs.

Hermes acceptance now also supports `--hermes-max-tokens` /
`HERMES_MAX_TOKENS`. The worker sends this as the OpenAI-compatible
`max_tokens` field, defaults to `512`, and clamps the value between `64` and
`4096`. This lets live customer-worker acceptance stay bounded enough for loop
supervision while still returning a usable Chinese delivery summary.

Hermes/OpenClaw customer-worker prompts now explicitly declare the
`ledger_summary_only` execution boundary. The model turn must not call
terminal/shell, browser, filesystem, MIS/API, external tools, or
publish/upload/deploy targets, and must not claim those actions were performed.
When such actions are needed, the runtime should return them as next-step
recommendations for the MIS Agent Gateway / approval / evidence path to execute
and verify.

Hermes transient disconnect handling is now covered at the customer-worker
workflow boundary. The worker already had retry support; the customer-worker
sync path now forwards `adapter_max_attempts` and `adapter_retry_delay_sec` to
the repo-local worker process, and the CLI exposes the same knobs as
`agentops workflow customer-worker-task --adapter-max-attempts ...`.
The server-side subprocess timeout budget now scales with
`adapter_max_attempts * hermes_timeout + retry_delay`, so a retry-capable Hermes
worker is not killed after only the first long request window.

Deterministic retry smoke:

```text
script: python3 scripts/customer_worker_hermes_retry_gateway_smoke.py
evidence_class: deterministic_loopback_hermes_adapter_retry
product_readiness_proof: false
fake gateway behavior: first /v1/chat/completions disconnects, second succeeds
observed request count: 2
run: run_gw_3ba5637ddb55
tool args: attempt_count=2, max_attempts=2, retry_history recorded
```

This is CI regression evidence for the real Hermes adapter path, not a
replacement for `customer_worker_real_runtime_acceptance.py --confirm-live`
against the local Hermes/OpenClaw runtimes.

`agentops operator live-acceptance` is the read-only live proof gate. It now
surfaces `active_attempt` for in-flight `agt_customer_worker_*` Hermes/OpenClaw
runs before the customer delivery artifact exists, but only marks an adapter
`fresh` after the run is completed and tool/evaluation/runtime/audit/artifact/
memory/approval/plan-evidence evidence all line up.

## What This Proves

The v1.5 worker loop can process normal MIS tasks, not just connector probes:

```text
planned MIS task
  -> Agent Gateway task pull
  -> claim
  -> run start
  -> adapter execution
  -> tool call record
  -> run heartbeat completed/failed
  -> evaluation submit
  -> audit emit
  -> optional memory proposal
```

## Boundaries

- Hermes/OpenClaw live execution requires `--confirm-run`.
- Runtime connectors can be marked trusted, review-required, or blocked; blocked Hermes/OpenClaw connectors prevent confirmed live customer worker execution.
- The worker does not call Dify or Notion.
- Dify live sync, Notion live/bidirectional sync, hosted SaaS billing, and
  hosted multi-tenant operations remain excluded from this v1.5 acceptance.
- The worker does not store full prompts or raw responses.
- The worker is installable as a Python source package and can render/write/check launchd/systemd templates with OS-managed relaunch policy; it is not yet an npm package, signed binary, hosted fleet manager, or automatic OS service loader.
- The UI/CLI worker status path now supports one-shot dispatch, local daemon start/stop, daemon state counters, daemon backoff state, daemon log tails, recent gateway events, remote enrollment/session health summaries, operator readiness cards, and stuck-task release controls; it is not a production fleet manager.
- `/workspace/workers` now surfaces the same fleet hygiene path as the API/CLI:
  it previews stuck worker task release plus never-seen / heartbeat-stale remote
  enrollment cleanup, requires explicit cleanup confirmation before apply, and
  displays token omission plus no-live-execution boundaries.
  Local developer verification should prefer
  `python3 scripts/worker_fleet_hygiene_smoke.py --isolated-fixture` so historic
  demo enrollments cannot hide the fixture rows.
- The worker status API/CLI now returns `fleet_health` gates and recommended
  CLI actions, so external agents and operator scripts can reason about whether
  the worker fleet is ready without scraping the browser UI.
- The worker readiness API/CLI now returns adapter route readiness for
  mock/Hermes/OpenClaw without executing live work, so external agents can choose
  a viable route before starting a task.
- The same readiness API/CLI now returns `worker_connection_policy` with schema
  `agentops-worker-connection-policy-v1`, making remote worker session refresh
  and reconnection/backoff policy machine-readable:
  - session: `--use-session`, ttl 900s, refresh margin 60s, parent token in process memory only;
  - loop: poll 5s, idle/error backoff max 30s, factor 2;
  - adapter retry: retryable failures may retry, non-retryable safety gates do not;
  - daemon: `--continue-on-error`, max errors 5, state/jsonl evidence.
- The `/workspace/agents` UI now surfaces that route-readiness state for human
  operators without changing the machine-facing execution contract.
- The same page surfaces selected-adapter readiness inside the customer dispatch
  form, so a human operator sees the live-route gate before pressing confirm.
- Confirmed customer-worker dispatch now fails early with `adapter_not_ready`
  when the selected live adapter is unavailable, instead of producing an opaque
  worker execution failure.
- Async customer-worker submit now fails early and records a failed workflow job
  instead of accepting a job that cannot run.
- Async template worker submit now uses the same live-route gate.
- Remote enrollment token issuance/revocation/rotation, approval-gated enrollment request UI, deployment-aware hosted policy gate, endpoint-level scope enforcement, selected-scope worker-loop readiness, short-lived session tokens with list/revoke controls and worker-loop refresh, scope presets, a first enrollment UI, and minimal Agent Gateway workspace isolation now exist. Full RBAC, hosted multi-tenant isolation, and production-grade hosted enrollment administration remain future work.

## 2026-06-22 Fleet Hygiene Recovery

Worker fleet management now has a safe hygiene path:

```bash
agentops worker hygiene
agentops worker hygiene --apply --confirm-cleanup
```

The default path is read-only and reports:

- running worker tasks that exceeded the stuck threshold;
- active remote enrollments that have never heartbeated after the enrollment age
  threshold;
- active remote enrollments whose last heartbeat is stale after the same
  threshold;
- safe recommended actions, with `token_omitted:true` and
  `live_execution_performed:false`.
- stale enrollment rows expose only `token_ref` plus `token_id_omitted:true`;
  raw `agtok_...` token ids must not appear in read-only or confirmed cleanup
  responses.

The confirmed apply path releases stale running tasks back to `planned`, blocks
linked running runs, revokes never-seen and heartbeat-stale enrollments,
cascades child sessions, and writes runtime/audit evidence. It does not execute
Hermes/OpenClaw live work.

Validated on the local demo server:

```text
python3 scripts/worker_fleet_hygiene_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/v1_5_demo_readiness_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/v1_5_local_product_acceptance.py --base-url http://127.0.0.1:8787
python3 scripts/remote_worker_product_acceptance.py --base-url http://127.0.0.1:8787
```

The hygiene run cleared historical stuck worker tasks and never-seen demo
enrollments so `agentops worker status` reports `fleet_health.overall=ready`.
`worker status` also redacts historical token/session-like ids in recent runtime
events before returning them to CLI/UI clients.

Follow-up hardening found that the read-only hygiene response could still expose
raw enrollment `token_id` values. The public response now uses `token_ref` and
`token_id_omitted:true` for plan, rejected-apply, and confirmed-apply payloads;
`worker_fleet_hygiene_smoke.py` fails if `agtok_...` appears in any hygiene
response.
The public hygiene plan/revoke/error projections now live in
`agentops_mis_core/worker_fleet.py`; `server.py` keeps only the SQLite reads,
task release, enrollment revoke invocation, runtime events and audit writes.

Latest isolated real OpenClaw customer-worker acceptance:

```text
script: python3 scripts/customer_worker_real_runtime_acceptance.py --base-url http://127.0.0.1:18891 --confirm-live --adapter openclaw
OpenClaw run: run_gw_336f0c2e3244
OpenClaw task: tsk_worker_ui_openclaw_20260622181338_cabe96c5
OpenClaw artifact: art_customer_worker_task_run_gw_336f0c2e3244
OpenClaw plan: plan_182f0e9028306fe0
OpenClaw manifest: pem_35d37a327c3115e5
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 14, audit_logs 12, artifacts 2, memories 2, approvals 1
```

## 2026-06-23 Real Hermes/OpenClaw Product Acceptance

Latest local live product-readiness run used the normal customer worker
workflow against the running local MIS server. This was not a mock path and not
a dry-run path.

```text
script: python3 scripts/customer_worker_real_runtime_acceptance.py --base-url http://127.0.0.1:8787 --confirm-live --adapter hermes --adapter openclaw --request-timeout 900 --hermes-timeout 480

Hermes run: run_gw_ee70f20c021c
Hermes task: tsk_worker_ui_hermes_20260623062626_2fc8c2b3
Hermes artifact: art_customer_worker_task_run_gw_ee70f20c021c
Hermes approval: ap_customer_worker_delivery_run_gw_ee70f20c021c
Hermes plan: plan_a1c439e073775da1
Hermes manifest: pem_daf7d404a2e9024b
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 14, audit_logs 7, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

OpenClaw run: run_gw_4a58476b7d09
OpenClaw task: tsk_worker_ui_openclaw_20260623062652_7e64b47f
OpenClaw artifact: art_customer_worker_task_run_gw_4a58476b7d09
OpenClaw approval: ap_customer_worker_delivery_run_gw_4a58476b7d09
OpenClaw plan: plan_9dd24ddbffbd74a2
OpenClaw manifest: pem_1e63d0f6dcd96bf5
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 14, audit_logs 7, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

CLI readback:
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops run get --run-id run_gw_ee70f20c021c
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops artifact list --run-id run_gw_ee70f20c021c
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops run graph --run-id run_gw_ee70f20c021c
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops run get --run-id run_gw_4a58476b7d09
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops artifact list --run-id run_gw_4a58476b7d09
AGENTOPS_BASE_URL=http://127.0.0.1:8787 ./scripts/agentops run graph --run-id run_gw_4a58476b7d09
```

Safety observations:

- Both adapters returned `ok:true` from the live acceptance script.
- Both runs are ledger `completed` runs with rule evaluations passing.
- Both created customer delivery approvals before treating output as accepted
  delivery.
- CLI readback showed `token_omitted:true`.
- Ledger rows store summaries and hashes/evidence; raw prompt, raw response,
  credentials, private messages, and full transcripts are not documented here.
- Hermes/OpenClaw remain `ledger_summary_only` and
  `restricted_until_runtime_tool_events` for commercial claims until runtime
  internal tool events are ingested or high-risk actions are routed through
  prepared actions.

## Minimal Local Live Demo Slice

`scripts/live_worker_loop_demo_slice.py` is the single entrypoint for recording
or self-dogfooding the local real worker loop. It deliberately does not use the
saved `agentops` CLI config, because a stale saved base URL can point to an old
demo server. Pass the current server URL explicitly or through
`AGENTOPS_BASE_URL`.

Plan-only path:

```bash
python3 scripts/live_worker_loop_demo_slice.py \
  --base-url http://127.0.0.1:8787
```

This prints the exact readiness, start-check, service-closure receipt, real-run
and readback commands, probes local readiness if the server is reachable, and
performs no live runtime execution and no ledger mutation. The service-closure
commands are the fast path for the `record_first` / service-control receipt gate
that can block confirmed Hermes/OpenClaw dispatch before runtime execution.

Non-mutating preflight path:

```bash
python3 scripts/live_worker_loop_demo_slice.py \
  --base-url http://127.0.0.1:8787 \
  --preflight \
  --service-control-service-path-template "$HOME/Library/LaunchAgents/local.agentops.worker.agt_worker_daemon_{adapter}.plist"
```

This still does not run Hermes/OpenClaw and does not write ledger rows. It runs
the preview-only `agentops worker service-control` path without
`--confirm-control`, renders the adapter-specific service path template, and
adds advisory loop-supervision readback for each selected adapter. Use it before
the confirmed live path to catch missing/unsafe service files or still-open
service-closure gates while preserving `live_execution_performed:false`,
`service_control_mutated:false`, and `ledger_mutated:false`.

Confirmed live path:

```bash
python3 scripts/live_worker_loop_demo_slice.py \
  --base-url http://127.0.0.1:8787 \
  --confirm-live \
  --confirm-service-control \
  --confirm-service-closure \
  --adapter hermes \
  --adapter openclaw \
  --service-control-service-path-template "$HOME/Library/LaunchAgents/local.agentops.worker.agt_worker_daemon_{adapter}.plist" \
  --request-timeout 720 \
  --hermes-timeout 600 \
  --hermes-max-tokens 512
```

Confirmed live now has a hard current-code gate. The wrapper first reads
`/api/local/readiness` and refuses to enter service-control, service-closure, or
live runtime dispatch unless the selected MIS server reports
`running_instance_current:true`. If the operator points the wrapper at a stale
server or an unavailable server, it returns
`local_readiness_current_code_required` with `live_execution_performed:false`,
`service_control_attempted:false`, `service_closure_attempted:false`, and
`ledger_mutated:false`.

This wraps the existing product path:

```text
local readiness probe
  -> operator start-check --adapter hermes/openclaw
  -> optional confirmed worker service-control --action load --confirm-control
  -> optional confirmed operator service-closure --fast --run-service-check --confirm-record
  -> customer_worker_real_runtime_acceptance.py --confirm-live
  -> v1_5_live_product_readiness_smoke.py readback
  -> local readiness reread
```

`--confirm-service-closure` is intentionally separate from `--confirm-live`.
`--confirm-service-control` is another separate wall because it may mutate local
launchd/systemd state on the operator machine. It runs the existing preview-first
`agentops worker service-control --confirm-control` path for each selected
adapter before service-closure. If service-control fails or the service file is
missing/unsafe, the wrapper stops before receipt recording or live dispatch. For
recording or isolated tests, `--service-control-service-path-template` can pass
adapter-specific reviewed service files into that control step by rendering
`{adapter}` for each selected runtime; otherwise the normal worker service path
is used.
`--confirm-service-closure` then writes the service-control receipt/readback
evidence needed by the `record_first` gate before runtime dispatch, rereads
loop-supervision, and requires the service-closure projection to be closed
(`required:false`, `status:pass`) with receipt, control-readback, service-check,
service-file, confirm-gate, relaunch-policy, loaded-service, ready-loop, and
active-loop evidence. If that post-readback closure is still required or only
recorded as attention, the script fails closed and does not enter the live
customer-worker run. The output is a compact redacted JSON proof with
service-control status, service-closure receipt/readback ids, and
run/task/artifact/approval/plan-evidence ids only. It does not store raw
prompts, raw responses, tokens, credentials, private messages, or full
transcripts in docs or committed state. Mock/offline evidence remains CI
fallback only.

Preflight slice verification:

```text
python3 scripts/live_worker_loop_demo_slice_smoke.py
python3 -m py_compile scripts/live_worker_loop_demo_slice.py scripts/live_worker_loop_demo_slice_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

Result: all passed locally on branch `codex/live-demo-preflight`. The smoke uses
temporary launchd plist fixtures and verifies `--preflight` returns dry-run
service-control summaries for Hermes/OpenClaw while preserving no live runtime,
no service mutation, no ledger mutation, no saved CLI config, and no token-like
output.

Negative isolated validation on a `/tmp` SQLite server showed the wrapper now
records service-closure receipt/readback evidence but returns
`*_service_closure_still_required` with `live_execution_performed:false` when
loop-supervision still projects `record_first` / `attention`; in that state it
does not call the customer-worker live runtime acceptance script.

## 2026-06-24 Current-Code Isolated Product Evidence

Latest current-code evidence used an isolated `/tmp` SQLite database and a
temporary local server, so the default repo database and tracked sample exports
were not touched. The run rebuilt knowledge, created Commander synthesis
evidence, executed real Hermes and OpenClaw customer-worker tasks, verified live
readiness, exercised remote worker mock fallback, and finished with non-live
local acceptance.

```text
head: af09ca85b4f91c0d5b97406cfb74e8f2712496d9
server: http://127.0.0.1:57591
db: /tmp/agentops_v15_current_exact_57591.db
script: AGENTOPS_DB_PATH=/tmp/agentops_v15_current_exact_57591.db python3 scripts/v1_5_current_code_product_evidence.py --base-url http://127.0.0.1:57591 --db-path /tmp/agentops_v15_current_exact_57591.db --confirm-live --timeout 900 --hermes-timeout 600 --hermes-max-tokens 512

before readiness: attention
after readiness: ready
product_readiness_proof: true
knowledge documents/chunks: 95 / 1001
commander synthesis artifacts: 1
commander promoted deliveries: 1
fresh live adapters: 2
closed-loop runs after evidence: 16

Hermes run: run_gw_37fbb0125371
Hermes task: tsk_worker_ui_hermes_20260624004030_e4fc9c30
Hermes artifact: art_customer_worker_task_run_gw_37fbb0125371
Hermes approval: ap_customer_worker_delivery_run_gw_37fbb0125371
Hermes manifest: pem_be3fbff0dfec04e1
Hermes status: completed
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 15, audit_logs 12, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

OpenClaw run: run_gw_ad5d349494b4
OpenClaw task: tsk_worker_ui_openclaw_20260624004111_8fdac32b
OpenClaw artifact: art_customer_worker_task_run_gw_ad5d349494b4
OpenClaw approval: ap_customer_worker_delivery_run_gw_ad5d349494b4
OpenClaw manifest: pem_649e9fec7abef9f5
OpenClaw status: completed
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 15, audit_logs 12, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

remote worker token fallback run: run_gw_e1bc3347526d
remote launch/session fallback run: run_gw_f147132f9027
non-live local acceptance: 169 checks, 0 failures
safety: raw_prompt_omitted true, raw_response_omitted true, token_omitted true, repo_artifacts_written false, sample exports not committed
```

The same HEAD also fixed the remote worker token baseline so scoped worker tokens
include `runtime_events:write`. Without that scope, a remote worker can
pull/claim/start work but fails when writing runtime evidence. Guard evidence:
`scripts/remote_agent_token_worker_smoke.py --adapter mock`,
`scripts/remote_worker_product_acceptance.py`, and the current-code product
evidence command above.

## 2026-06-24 Main Current-Code Live Recheck

After the v1.5 branch was merged to `main`, the local product proof was rechecked
against the merged current code with real Hermes and OpenClaw adapters. The
server used an isolated `/tmp` SQLite database and `AGENTOPS_SKIP_SEED_EXPORTS=1`,
so the tracked repo database, sample exports, credentials, prompts, responses,
and transcripts were not written.

```text
head: 8f2fa73ab72821ed7fe7c062f9e04dd84af05c20
server: http://127.0.0.1:8796
db: /tmp/agentops_mis_loop_896ee1a.db
script: AGENTOPS_BASE_URL=http://127.0.0.1:8796 python3 scripts/customer_worker_real_runtime_acceptance.py --base-url http://127.0.0.1:8796 --confirm-live --adapter hermes --adapter openclaw --request-timeout 720 --hermes-timeout 600 --hermes-max-tokens 512

local readiness: ok true, status attention, current_code current, live_acceptance ready
live product readiness proof: true
fresh live adapters: 2
knowledge documents/chunks: 95 / 1001
closed-loop runs: 12
non-live local acceptance: 169 checks, 0 failures

Hermes run: run_gw_a3f710ec3d20
Hermes task: tsk_worker_ui_hermes_20260624034655_e2dbdda0
Hermes artifact: art_customer_worker_task_run_gw_a3f710ec3d20
Hermes approval: ap_customer_worker_delivery_run_gw_a3f710ec3d20
Hermes plan: plan_bace786efca44cd2
Hermes manifest: pem_fda6767d98fa64cb
Hermes status: completed
Hermes evidence: tool_calls 1, evaluations 1, runtime_events 15, audit_logs 7, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

OpenClaw run: run_gw_e653bc7754b7
OpenClaw task: tsk_worker_ui_openclaw_20260624034753_2a357cc3
OpenClaw artifact: art_customer_worker_task_run_gw_e653bc7754b7
OpenClaw approval: ap_customer_worker_delivery_run_gw_e653bc7754b7
OpenClaw plan: plan_0d681c393933fa5e
OpenClaw manifest: pem_94eabef754cb61de
OpenClaw status: completed
OpenClaw evidence: tool_calls 1, evaluations 1, runtime_events 15, audit_logs 7, artifacts 2, memories 2, approvals 1, plan_evidence_manifests 1

safety: raw_prompt_omitted true, raw_response_omitted true, token_omitted true, repo_artifacts_written false
commercial boundary: Hermes/OpenClaw remain ledger_summary_only and restricted_until_runtime_tool_events until runtime-internal tool events are ingested or high-risk actions use prepared-action gates.
```

Readback commands:

```bash
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops operator live-product-readiness --require-adapter hermes --require-adapter openclaw
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops local readiness --require-current-code --expect-head-sha 8f2fa73ab72821ed7fe7c062f9e04dd84af05c20
AGENTOPS_BASE_URL=http://127.0.0.1:8796 python3 scripts/v1_5_local_product_acceptance.py --base-url http://127.0.0.1:8796
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops run get run_gw_a3f710ec3d20
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops run get run_gw_e653bc7754b7
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops artifact list --run-id run_gw_a3f710ec3d20
AGENTOPS_BASE_URL=http://127.0.0.1:8796 ./scripts/agentops artifact list --run-id run_gw_e653bc7754b7
```

## 2026-06-22 Remote Worker Scope Baseline

The worker now writes `agent_plan`, `plan_evidence_manifest`, `artifact`,
`memory`, evaluation, tool-call, run, and audit evidence for normal task
execution. Therefore the product worker scope baseline for both direct
enrollment and approval-gated enrollment includes:

```text
agents:heartbeat
knowledge:read
agent_plans:read
agent_plans:write
plan_evidence:read
plan_evidence:write
tasks:create
tasks:read
tasks:claim
runs:write
runtime_events:write
toolcalls:write
artifacts:write
memories:propose
evaluations:submit
audit:write
```

`agents:write` is additionally used by admin/operator-created launch packets.

Validated on the running local server:

```text
python3 scripts/remote_worker_product_acceptance.py --base-url http://127.0.0.1:8787
python3 scripts/worker_session_refresh_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/enrollment_approval_workflow_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/v1_5_local_product_acceptance.py --base-url http://127.0.0.1:8787
```

Observed evidence:

- direct remote token worker path passed with mock run `run_gw_c65670dafaf8`,
  `worker_processed=1`, verified plan evidence, and revoked one-time token;
- launch-packet worker session path passed with mock run evidence and
  `token_omitted:true`;
- launch-packet worker session path revalidated with run `run_gw_860a79aee458`;
- session refresh processed two tasks and refreshed short-lived sessions;
- approval-gated enrollment still blocks premature token issue and then issues a
  usable scoped token after approval;
- local product acceptance remains non-live. If the long-running demo server has
  active workers, ledger count drift is now reported as concurrent environment
  activity instead of mislabeling the read-only acceptance runner as mutating.

Remote worker first-run acceptance now pins the exact smoke task with
`--task-id` and uses `--no-enforce-intake` only for the initial self-planning
worker execution. This does not bypass the execution authority chain: the worker
still creates and verifies an Agent Plan before `run_start`, then writes a
verified plan-evidence manifest after tool/evaluation/artifact evidence. The
strict intake-enforced daemon path remains available for pre-planned operator
queues.

## 2026-06-24 Worker Runtime Event Summary Gate

Agent workers now record an automatic `agent_worker.adapter_execution_summary`
runtime event after adapter execution and before tool/evaluation/artifact
writeback. The event stores only summaries, prompt/payload hashes, latency,
status, adapter, model name, and omission proof. It does not store raw prompts,
raw responses, credentials, private messages, or full transcripts.

For Hermes and OpenClaw this is intentionally a worker-side execution summary,
not a claim that runtime-internal tool calls are fully structured or observable.
Those adapters still carry `observation_level=ledger_summary_only` and
`commercial_readiness=restricted_until_runtime_tool_events` until structured
per-action runtime events are available from the runtime itself.

Validated against an isolated local server:

```text
python3 scripts/worker_adapter_retry_smoke.py --base-url http://127.0.0.1:61575
```

Observed evidence:

- mock retry success wrote runtime event `rte_d94ec882ab26`;
- Hermes confirm-run gate failure wrote runtime event `rte_ee7872afdb40`;
- tool-call args include `worker_runtime_event_id`,
  `worker_runtime_event_summary_recorded:true`, and, for Hermes,
  `runtime_internal_tools_remain_opaque:true`.
- `operator evidence-report` now exposes `worker_runtime_summary.status=ready`
  and top-level `worker_runtime_summary_ready` counts for worker runs.

## 2026-07-15 Long-Lived Idle Backoff Saturation

Real preview.29 service dogfood exposed a long-running Worker defect rather than
a Runtime outage. After many idle polling iterations, `backoff_sleep` computed
`factor ** consecutive_idle` before applying the configured 30-second cap. The
unbounded intermediate exponent eventually raised `(34, 'Result too large')`,
so fresh Hermes/OpenClaw service Workers alternated valid idle heartbeats with
an incorrect Agent `error` status even though task pull and adapter preflight
remained available.

The Worker now calculates the saturation step before exponentiation and treats
the configured cap as a hard upper bound, including when `cap < base`. The
daemon resilience smoke directly covers a one-million-iteration idle streak,
normal growth, zero base, unit factor and cap-below-base behavior. It also keeps
the existing isolated mock-daemon and bad-URL error-recovery coverage.

Validated without the real Host database or live adapters:

```text
python3 scripts/worker_daemon_resilience_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/worker_adapter_readiness_smoke.py
```

The first command completed a fixture task with run, tool, evaluation,
artifact, memory, audit and verified plan-evidence records; the large idle
streak returned exactly 30 seconds with no overflow. This source fix is not
retroactive evidence for the already-running preview.29 Worker processes. A
new versioned package, explicit Worker service restart and fresh real adapter
task are still required before claiming installed closure.

## 2026-07-22 Private Host OpenClaw Commander Dogfood

Codex used the installed Private Host as an MIS commander and delegated one
bounded, read-only product review task to the separately configured real
OpenClaw Runtime. This was not a Codex model switch and did not use the
browser-only Human Workspace mutation route. The task was created, planned,
retrieved, claimed, executed and recorded through Agent Gateway authority.

```text
task: tsk_dogfood_openclaw_p1_20260722T105525Z
task status after delivery-review request: waiting_approval
run: run_gw_8bf447f335f9
runtime: openclaw
plan: plan_80193ccd7b87f073
plan-evidence manifest: pem_1505188534d20dae
manifest status: verified
runtime event: rte_bb4982bcc1e5
audit: aud_1c491af58256
delivery approval: ap_gw_4440d60621ec
delivery decision: pending
evidence: tool call 1, evaluation 1, artifact 1, memory candidate 1
adapter attempts: 1
```

The first machine-token attempt against the Human Workspace workflow correctly
returned `human_auth_required` before creating a Runtime run. Re-dispatching
through Agent Gateway succeeded. No raw prompt, raw response, credential,
private message or transcript was retained in this acceptance record.

This dogfood also exposed two installed-service defects that must remain visible
until a new package is installed:

- local readiness generated service commands for `agt_worker_daemon_*`, while
  the installed Host services use `agt_worker_local_stack_*`;
- Preview38 service templates emit a full JSON result on every idle poll, and
  their unbounded stdout files filled the low-space Host volume, producing
  repeated launchd exit code `120`.

The source fix selects only a canonical registered local-stack identity, even
before it owns a task and while it is paused or reporting an error, and
propagates it through service check/install/control, start-check, admission and
loop-bootstrap commands. Service-control receipts now match the command/hash
and the current action signature together; a same-command receipt with a stale
signature cannot satisfy the current gate. The two installed Worker services were temporarily
unloaded and only their generated diagnostic logs were cleared without reading
their contents. Host and Runtime gateway configuration remain intact, and
bounded one-shot Agent Gateway execution remains available. This is not yet
installed-service closure and is not Hermes live evidence.

Generated service templates, API-started daemons, remote launch packets and the
managed local stack now keep heartbeat/state observability but omit per-poll
JSONL stdout by default. `--jsonl-log` remains an explicit bounded foreground diagnostic mode;
an output `OSError` or broken pipe disables further JSONL writes without
stopping task polling. Long-running workers retain only a bounded recent-result
window (20 results for an unlimited daemon, at most 100 for a finite batch) and
report `results_seen` plus `results_omitted` on exit, so idle polling cannot
grow process memory without bound. Session refresh history is independently
bounded to the newest 20 entries, and any result that explicitly returns
`ok:false` makes the final Worker outcome fail even when it did not claim a
task; intentional approval and intake pauses remain successful only when they
explicitly return `ok:true`.

Source verification, without a real Runtime call:

```text
python3 scripts/worker_service_output_resilience_smoke.py
python3 scripts/worker_daemon_resilience_smoke.py --base-url <isolated_server_url>
python3 scripts/local_readiness_smoke.py --isolated-fixture
python3 scripts/operator_start_check_smoke.py
python3 scripts/operator_start_check_api_smoke.py --isolated-fixture
python3 scripts/agentops_worker_local_config_smoke.py
python3 scripts/agentops_worker_package_smoke.py
python3 scripts/run_local_stack_smoke.py
python3 scripts/module_boundary_smoke.py
```
