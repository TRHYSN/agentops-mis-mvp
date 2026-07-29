# API Spec

本 MVP 暴露 REST 风格 API。所有响应均为 JSON。真实产品可迁移为 OpenAPI 3.1。

## Agents

```http
GET /api/agents
POST /api/agents
GET /api/agents/:id
GET /api/agents/:id/performance
```

POST body 示例：

```json
{
  "name": "Research Agent",
  "role": "Researcher",
  "runtime_type": "mock",
  "model_provider": "anthropic",
  "model_name": "claude-sonnet-4.5",
  "budget_limit_usd": 5,
  "allowed_tools": ["browser.search", "github.read", "memory.propose"]
}
```

## Tasks

```http
GET /api/tasks
POST /api/tasks
GET /api/tasks/:id
PATCH /api/tasks/:id/status
PATCH /api/tasks/:id/assign
```

## Mock Runtime

```http
POST /api/mock-runs/start
POST /api/mock-runs/:id/complete
```

`POST /api/mock-runs/start` body:

```json
{
  "task_id": "tsk_competitor",
  "agent_id": "agt_research"
}
```

行为：创建 run，随机生成 2-5 个 tool calls；如果出现高风险 tool call，则生成 approval 并进入 waiting_approval；否则自动完成并生成 evaluation 与 memory candidate。

## Runs

```http
GET /api/runs
GET /api/runs/:id
GET /api/runs/:id/graph
GET /api/runs/:id/evidence-graph
GET /api/runs/export
```

支持 query：

```text
/api/runs?task_id=tsk_competitor
/api/runs?agent_id=agt_research
/api/runs?limit=100&offset=0
/api/runs?limit=100&offset=0&include_page=true
```

For compatibility, `/api/runs`, `/api/tool-calls`, and `/api/audit` still return
legacy arrays by default. When `include_page=true` is supplied, they return a
read-only envelope containing `items`, the named list (`runs`, `tool_calls`, or
`audit_logs`), and `page.{limit,offset,returned,total,has_more}`. The UI uses
bounded `limit` parameters for these large ledger lists so demo and dogfood
workspaces do not fetch unbounded history on first paint.

## Agent Gateway Scoped Readback

For machine-facing agents and remote workers, prefer the scoped Agent Gateway
read endpoints over the local UI/demo list endpoints:

```http
GET /api/agent-gateway/tasks
GET /api/agent-gateway/tasks/:id
GET /api/agent-gateway/runs
GET /api/agent-gateway/runs/:id
GET /api/agent-gateway/runs/:id/graph
GET /api/agent-gateway/runs/:id/evidence-graph
GET /api/agent-gateway/artifacts
GET /api/agent-gateway/approvals
GET /api/agent-gateway/memories
GET /api/agent-gateway/knowledge/search?q=workflow
GET /api/agent-gateway/agent-plans
GET /api/agent-gateway/agent-plans/:plan_id
GET /api/agent-gateway/agent-plans/:plan_id/verify
POST /api/agent-gateway/agent-plans
GET /api/agent-gateway/plan-evidence-manifests
GET /api/agent-gateway/plan-evidence-manifests/:manifest_id
GET /api/agent-gateway/plan-evidence-manifests/:manifest_id/verify
POST /api/agent-gateway/plan-evidence-manifests
GET /api/agent-gateway/review/queue
```

Most scoped readback endpoints require `tasks:read` and are constrained to the
token's workspace plus tasks/runs/artifacts/approvals/memories/review items
visible to the bound agent. Knowledge endpoints use `knowledge:read` /
`knowledge:write`; agent-plan endpoints use `agent_plans:read` /
`agent_plans:write`; plan-evidence manifest endpoints use
`plan_evidence:read` / `plan_evidence:write`. The browser UI may still use the
local list endpoints for the single-machine demo. `GET /api/review/queue`
remains the local UI/demo read path; machine-facing CLI/remote agents should use
`GET /api/agent-gateway/review/queue`. Approval and memory approve/reject
actions remain human/operator actions, not agent-scoped automatic decisions.

## Worker Adapter Readiness

```http
GET /api/workers/adapter-readiness
GET /api/workers/status
GET /api/agent-gateway/host-workers/status
GET /api/agent-gateway/host-workers/fleet
GET /api/agent-gateway/host-workers/adapter-readiness
GET /api/agent-gateway/host-runtime-connectors
GET /api/agent-gateway/host-workers/stuck-tasks
```

Private Host machine reads require the Host API key. Agent enrollment and
short-lived Agent Session tokens cannot inspect Host-wide worker or connector
topology. `host-runtime-connectors` is read-only, performs no runtime refresh,
and omits raw base URLs, binary paths, trust notes, and last-error text.

Human Session reads of the Operator action plan, loop audit, handoff, and
command center use the current ledger/readiness snapshot without running live
Hermes/OpenClaw start checks. Machine-authenticated CLI/API calls retain the
full supervision view. This keeps browser navigation responsive and prevents a
page load from becoming an implicit runtime probe.

`GET /api/workers/adapter-readiness` is a read-only route-selection endpoint
for the browser operator console and uses Human Session authentication in
Private Host mode. The `host-workers/*` mirrors are for the packaged Host CLI
and require the Host machine credential. Agent-bound enrollment/Session tokens
cannot read Host-wide Worker telemetry; they continue to use scoped task/run
and evidence routes. These reads do not pull tasks, execute models, write ledger
rows, or update rejected bound-credential usage timestamps. Each adapter entry
includes:

- `readiness`
- `trust_status`
- `target_resource`
- `recommended_action`
- `capability_manifest`
- `capability_policy_hash`
- `observation_level`
- `risk_floor`
- `commercial_readiness`

The manifest schema is `runtime-capability-manifest-v1`. Mock is currently
`structured_ledger`; Hermes and OpenClaw are deliberately marked
`ledger_summary_only` and `restricted_until_runtime_tool_events` until internal
runtime tool events are ingested or risky external writes are routed through
prepared actions.
Worker-created `agent_worker.<adapter>` tool calls consume the same manifest:
Hermes/OpenClaw record at least a medium risk floor plus observation and
commercial restriction metadata instead of being treated as always low risk.
The live worker prompt also keeps Hermes/OpenClaw in the same
`ledger_summary_only` boundary: the model turn must not call terminal/shell,
browser, filesystem, MIS/API, external tools, or publish/upload/deploy targets.
If those actions are needed, the model returns them as next-step
recommendations for the MIS ledger path to execute and verify.

Runtime-internal events can now be ingested when a runtime or adapter can expose
them:

```http
POST /api/agent-gateway/runtime-events
```

This endpoint requires `runtime_events:write`, a visible `run_id`, and a
workspace/agent binding that matches the run. It records only redacted summaries
and a `raw_payload_hash`; optional `payload` or `metadata` values are hash
inputs only and are not stored as raw runtime logs. Run detail readback includes
the bounded `runtime_events[]` list so an external Hermes/OpenClaw/Dify-style
adapter can prove internal tool progress without bypassing MIS approval and
audit boundaries.

## Knowledge Search

```http
GET  /api/knowledge/search?q=approval&limit=10&refresh=true
POST /api/knowledge/index
GET  /api/agent-gateway/knowledge/search?q=approval&limit=10
POST /api/agent-gateway/knowledge/index
GET  /api/knowledge/context-packet?task_id=<task_id>&adapter=hermes
GET  /api/agent-gateway/knowledge/context-packet?task_id=<task_id>&adapter=hermes
```

The local indexer reads Markdown from the repo root, `docs/`, and `knowledge/`.
It stores document metadata in `knowledge_documents`, splits Markdown into
heading-aware `knowledge_chunks`, and indexes those chunks in SQLite FTS5. If
chunk FTS is unavailable or a row was inserted through a legacy/manual path,
search can still fall back to document-level `knowledge_fts`; if FTS5 is
unavailable entirely, search falls back to a plain SQLite `LIKE` query. This is
still the local-first stage before embeddings or a vector database.

Indexed documents carry `workspace_id`, `project_id`, `access_level`, `scope`,
`source_hash`, and search-time `retrieval_id` metadata. Repo-managed doctrine is
indexed as `workspace_id=global` and `access_level=internal`; future
customer-private imports must use a concrete workspace id. Search results return
redacted snippets and hashes only, never raw document bodies.
The filesystem indexer explicitly excludes generated outputs, caches, runtime
logs, raw customer folders, local databases, env/key material and other
credential-like paths; incremental reindex reports `incremental_noop:true` when
unchanged documents do not rewrite the index.
Search responses include `search_quality` with `fallback_used`,
`fallback_reason`, `searched_fields`, `content_body_searched`, `result_quality`
and a warning when FTS5 falls back to `LIKE`. Agents must treat
`metadata_summary_like` as degraded retrieval, not as authoritative full-text
evidence.
Heading-aware hits return `retrieval_granularity=heading_chunk`, `chunk_id`,
`chunk_heading`, `chunk_heading_path`, and a search-time `retrieval_id`; results
omit raw document bodies and snippets remain redacted.

Agent Gateway search requires `knowledge:read` and is non-mutating: `refresh`
requests are reported as skipped so read scope cannot update the index. Explicit
index refresh uses `POST /api/agent-gateway/knowledge/index` and requires
`knowledge:write`. Bound Agent Gateway tokens can see only `global` knowledge
plus documents whose `workspace_id` matches the token workspace; workspace header
or query spoofing returns `403`.

The evidence packet and context packet have different jobs. The existing
`knowledge/evidence-packet` proves retrieval quality and provenance while
omitting all excerpts. `knowledge/context-packet` adds a bounded transient
model-input layer: at most eight redacted knowledge summaries, at most five
human-approved canonical Memory summaries, and at most 6000 combined
characters. It never scans conversation JSONL, logs, attachments, env files or
raw customer folders. Its response binds every summary to a document/chunk or
approved `memory_id` and a summary hash. Workers may place those summaries in a
single model call, but Tool Call, Evaluation and Audit evidence records only
the packet hash, source IDs, block hashes/counts and omission proof.

## Commander Repo Map

```http
GET /api/commander/repo-map?q=agent%20plan&limit=8&char_budget=4800
```

`GET /api/commander/repo-map` is a read-only localization endpoint for coding
work packages. It scans allowed source/documentation files under the repository
root, excludes generated output, local databases, env files, caches, binaries,
archives, logs, and dependency directories, and returns deterministic ranked
file candidates. Each candidate includes a relative path, score, matched
fields, extracted symbols, short redacted snippets, line count, content hash,
rank reason, and source provenance. The response records `used_chars_estimate`,
`char_budget`, `read_only:true`, `ledger_mutated:false`,
`live_execution_performed:false`, `raw_file_bodies_returned:false`, and
`token_omitted:true`.

Use this endpoint before creating or executing coding work packages when an
agent needs to localize likely files without loading the whole repository into
context. It does not create tasks, approve plans, execute shell commands, call
Hermes/OpenClaw, or bypass Agent Plan, Approval Wall, test, or merge gates.

`GET /api/commander/coding-project-template` returns the local coding project
template for Codex, Hermes, OpenClaw or remote workers. The response is
read-only and links the WorkPackage contract, repo-map localization, worktree
and branch convention, patch artifact capture, verifier commands, required
artifacts, plan-evidence manifest and merge-readiness gate. It intentionally
does not create a worktree, write a patch, mutate the ledger, execute shell
commands, or expose the absolute repo root.

## Commander Work Packages

```http
GET  /api/commander/coding-project-template?q=local+coding+goal
POST /api/commander/work-packages/plan
GET  /api/commander/work-packages?project_id=proj_x&limit=25
GET  /api/commander/lane-packets?project_id=proj_x&limit=25
POST /api/commander/work-packages/:task_id/dispatch
POST /api/commander/work-packages/dispatch-batch
POST /api/commander/work-packages/:task_id/coding-workspace
POST /api/commander/work-packages/:task_id/coding-workspace/cleanup
POST /api/commander/work-packages/:task_id/coding-evidence
```

`POST /api/commander/work-packages/plan` previews by default. With
`confirm_create:true`, it creates normal MIS `tasks` plus runtime/audit evidence
and a `commander_repo_map_localization` artifact for each created package. The
artifact URI uses `repo-map://...`, its summary contains only bounded file
paths and a manifest hash, and the audit metadata records raw-content/snippet
omission proof. It does not store raw source files, raw prompts, model
responses, credentials, or private transcripts.

`GET /api/commander/work-packages` is read-only. It reconstructs package state
from normal MIS tasks, latest runs, evidence counts and the latest
`commander_repo_map_localization` artifact so humans and workers can inspect
the intended file scope before dispatch. `POST .../:task_id/dispatch` remains
the explicit execution boundary; Hermes/OpenClaw dispatch still requires
`confirm_run:true`.

`GET /api/commander/lane-packets` is the machine-facing readback for agents and
workers. It projects each Commander work package into a bounded lane packet with
`lane_id`, `objective`, `owner`, `runtime`, `phase`, `task_id`, optional
`run_id`, `packet_hash`, `blocked_reason`, `next_command`,
`verification_command`, evidence references and safety gates. It is read-only,
does not create tasks/runs, does not call Hermes/OpenClaw, and omits raw
prompts, raw responses, raw source bodies and tokens.

`POST /api/commander/work-packages/:task_id/dispatch` records the lane packet it
consumed before worker execution. The response and audit metadata include a
bounded `commander_lane_packet` summary plus `commander_lane_packet_hash`, so
later run/tool/evaluation/audit evidence can be traced back to the exact
machine-readable Commander packet without storing raw prompts, responses,
source bodies or tokens.

`POST /api/commander/work-packages/dispatch-batch` queues selected planned
packages as async `workflow_jobs` through the same target-task dispatch path.
Mock jobs are CI-safe and write normal run/tool/evaluation/job evidence.
Hermes/OpenClaw batch dispatch fails closed before job creation unless
`confirm_run:true` is present. Queued jobs include a bounded
`commander_lane_packet`, `commander_lane_packet_hash`, and request hash evidence
before the background worker starts, so the queue entry can be traced to the
same machine-readable packet as the completed run evidence. The release gate is
`python3 scripts/commander_work_package_batch_dispatch_smoke.py`.

`POST .../:task_id/coding-workspace` previews by default and returns branch,
worktree path hash, repo-root omission proof and current git status without
creating a worktree. With `confirm_create:true`, it creates an explicit local
git worktree outside the repo and records a `commander_worktree_workspace`
artifact. `POST .../:task_id/coding-workspace/cleanup` is also preview-only
unless `confirm_cleanup:true` is supplied.

`POST .../:task_id/coding-evidence` records the coding package handoff after a
worker run exists. Without `confirm_record:true`, it is a dry-run plan. With
confirmation, it writes summary/hash-only `commander_worktree_workspace`,
`commander_patch_manifest`, `commander_test_log`,
`commander_verifier_report`, and `commander_merge_gate_receipt` artifacts,
plus a rule evaluation, runtime event and audit evidence. If
`collect_from_worktree:true` is supplied, it reads git status/diff metadata
from the prepared worktree, strips raw patch output, stores hashes and bounded
summaries only, and still does not merge or push.

## Agent Plans

```http
POST /api/agent-gateway/agent-plans
GET  /api/agent-gateway/agent-plans?task_id=tsk_123
GET  /api/agent-gateway/agent-plans/:plan_id
GET  /api/agent-gateway/agent-plans/:plan_id/verify
```

`POST /api/agent-gateway/agent-plans` records the required pre-execution plan:

```json
{
  "task_id": "tsk_example",
  "agent_id": "agt_builder",
  "task_understanding": "Implement the Agent Work Method Block.",
  "referenced_specs": ["PROJECT_SPEC.md", "AGENT_WORKFLOW.md"],
  "referenced_memories": ["mem_..."],
  "referenced_bases": ["base_local_memory"],
  "proposed_files_to_change": ["server.py"],
  "risk_level": "medium",
  "approval_required": false,
  "execution_steps": ["index knowledge", "record plan", "run smoke"],
  "verification_plan": "Run focused CLI/API smoke.",
  "rollback_plan": "Remove new routes and schema additions before release."
}
```

`GET /verify` checks that the plan names specs, retrieval/memory context, bases,
execution steps, verification, rollback, risk and file scope. Real `memory_id`
references count as authority only when the memory exists and has
`review_status=approved`; `candidate`, `rejected`, `stale` or missing memory ids
fail the `memory_authority` gate. Knowledge paths may still be used as context,
but they are reported separately from approved memory authority.

`POST /api/agent-gateway/runs/start` is plan-bound: it rejects execution with
`428 agent_plan_required` unless the selected Agent Plan matches the run's
workspace, task, and agent and passes verification. Pass `agent_plan_id` in the
run-start request to make the binding explicit; the run stores `agent_plan_id`
and `plan_hash` for later plan-evidence manifest checks.
For governed live runtimes (`hermes`, `openclaw`, `codex`), run-start also
reads `/api/operator/loop-supervision` before creating the run. If bounded
confirm or no-server-shell safety is not proven, it fails closed with
`428 run_start_loop_supervision_blocked`, writes audit/runtime-event evidence,
and does not create a run. When allowed, the response includes a compact
`loop_supervision_gate` and safe `supervision_hash`; raw prompts, responses,
runtime payloads and tokens remain omitted.

`POST /api/agent-gateway/prepared-actions` creates the durable Approval Wall
pause/resume primitive. The request binds a run, optional tool call, action
type, normalized arguments, target resource, policy version, checkpoint, and
idempotency key into an immutable `action_hash`, then creates a linked pending
approval. It updates the run/task/tool call to `waiting_approval` and omits raw
prompts, raw responses, credentials, and full external payloads.

`POST /api/agent-gateway/tool-calls` also accepts `prepare_action:true`. This
records the tool-call evidence and creates the linked prepared action plus
approval in the same transaction, returning `approval_wall.prepared_action`,
the approval, and a `next_action` command chain for inspect, approve and exact
resume. This is the preferred Agent Gateway path for high/critical external
tool calls.

High/critical tool calls with external side-effect intent are rejected with
`428 high_risk_prepared_action_required` unless `prepare_action:true` is used.
External side-effect intent includes risky upload/publish/write/send/export
tool names, external target resource schemes, and connector/write keywords.
Approving the linked approval does not complete the tool call; it only marks
the prepared action approved. The caller must resume the prepared action exactly
once with a provider side-effect id before completed side-effect evidence is
recorded.

`GET /api/agent-gateway/prepared-actions/:id` verifies that the current stored
prepared action still hashes to the approved `action_hash`. `POST
/api/agent-gateway/prepared-actions/:id/resume` resumes only after the linked
approval is approved, the hash still matches, and `consumed_at` is empty. Resume
records a provider side-effect id, marks the action `consumed`, updates the
linked tool-call evidence, writes runtime/audit evidence, and rejects replay
with `409 prepared_action_already_consumed`.

`POST /api/integrations/dify/upload-text` follows the same exact-resume
contract for live provider writes. Dry-run remains the default. When
`DIFY_ALLOW_REAL_UPLOAD`, `confirm_upload`, API key, dataset id, and document
text are all present but no approved `prepared_action_id` is supplied, MIS
creates a waiting-approval run, tool call, prepared action, and approval, then
returns `dify_external_write_prepared_action_required` without calling Dify.
After the linked approval is approved, the caller repeats the upload request
with `prepared_action_id`; MIS verifies the action hash and normalized upload
arguments before sending text to Dify, then consumes the prepared action with
the returned document id. The API stores hashes, ids, summaries, and audit
evidence only; it does not persist the full document text or API key.

`POST /api/integrations/notion/export-report` and
`POST /api/integrations/notion/export-confirmed` follow the same exact-resume
contract for live Notion writes. Dry-run remains the default. When Notion is
configured and `dry_run:false` plus `confirm_export:true` are supplied without
an approved `prepared_action_id`, MIS creates a waiting-approval run, tool call,
prepared action, and approval, then returns
`notion_external_write_prepared_action_required` without calling Notion. After
approval, repeat the export request with `prepared_action_id`; MIS verifies the
prepared action hash and stored report snapshot hash before creating the Notion
page, then consumes the prepared action with the Notion page id. The snapshot is
written as a local runtime file referenced by hash from the prepared action; it
is the safe report summary only. Credentials, private transcripts, and raw
command bodies are not exported or stored in the MIS ledger.

Fixed live runtime probes use the same Approval Wall path. OpenClaw
`POST /api/integrations/openclaw/probe`, Agnesfallback
`POST /api/integrations/hermes/cli-probe` /
`POST /api/integrations/hermes/chat-completion-probe`, and Hermes default
`POST /api/integrations/hermes/run-task` do not execute a live runtime with
`confirm_run:true` alone; they create a `runtime.fixed_probe` prepared action,
wait for approval, verify the fixed prompt hash, and then consume the prepared
action only on exact resume.

`GET /api/operator/command-center` is the stable read-only command-center BFF
for `/workspace/agents`, CLI operators, Codex, Hermes and OpenClaw supervisors.
`GET /api/command-center/overview` is a compatibility alias that returns the
same canonical read model. It aggregates active projects, blocked runs, pending
approvals, customer deliveries, stale worker references, Commander coding
evidence gates and prioritized next actions into one response. It never starts
workers, creates tasks, writes receipts, creates worktrees, merges, pushes,
approves gates or returns raw prompts, raw model responses, raw source, raw
patches, raw logs, or credentials.
`GET /api/command-center/overview` is a compatibility alias that returns the
same canonical payload with alias metadata.

`GET /api/operator/action-plan` includes a read-only `execution_evidence`
source. It audits recent completed or failed runs for missing plan bindings,
missing/unverified plan-evidence manifests, and missing tool, evaluation,
artifact, or audit evidence. The response summarizes the gap counts and returns
explicit remediation-preview commands; it does not create plans, manifests,
approvals, or audit rows.

`GET /api/operator/intake-checklist` is the read-only pre-run gate for planned
or backlog tasks. It checks assignment, submitted/verified Agent Plan,
knowledge/spec references, base references, and high-risk approval boundaries
before a worker pulls the task. `GET /api/operator/action-plan` embeds this as
the `task_intake` source and reports `task_intake_checked`,
`task_intake_ready`, `task_intake_blocked`, `task_intake_attention`, and
`task_intake_missing_agent_plan`.

`GET /api/operator/loop-launch-packet` is the read-only Agent Work Method
handoff packet for Hermes, OpenClaw, Codex, or remote agents. By default it uses
the lightweight `operator loop-control` read model for next-step control so
agents do not wait on the heavier full handoff graph before starting READ/PLAN
work. Pass `handoff_mode=full` or `full_handoff=true` when deeper
`operator handoff` diagnostics are required. Its RETRIEVE phase includes both
safe knowledge-search metadata and a
`GET /api/commander/repo-map` localization source: sanitized paths, symbols,
content hashes, provenance, ranking proof, omission flags, and the exact
`agentops commander repo-map` command to rerun. The embedded agent-plan draft
uses those repo-map paths as initial `proposed_files_to_change` candidates
when localization succeeds. The response includes `sources.operator_control`
plus a backward-compatible `sources.handoff` alias that names the active control
source (`operator_loop_control` by default, `operator_handoff` in full mode).
The packet does not create plans, run workers, approve gates, create memories,
mutate ledgers, or return raw file bodies.

`GET /api/operator/runtime-doctor` is the lightweight, read-only local runtime
doctor for Hermes, OpenClaw, Codex supervision, and remote Agents. It samples
adapter readiness, worker fleet state, and ledger evidence counts into gates for
local MIS API reachability, runtime availability, `--confirm-run`, prepared
actions for external writes, remote worker freshness, launch-packet
availability, handoff/evidence-chain status, Codex supervision, and redaction.
It deliberately does not run the heavier `operator health` or `operator
handoff` aggregators inline; instead it returns copyable commands for those
checks plus preflight, launch packet, loop audit, evidence report, guarded live
execution, and Action Queue receipts. It requires `tasks:read` for supplied
Agent Gateway tokens/sessions and never starts runtimes, executes tasks, mutates
ledgers/connectors, or exposes tokens/raw prompts/raw responses.

`GET /api/operator/start-check` is the recommended pre-task read model for
Codex, Hermes, OpenClaw, and remote Agents before they accept or advance local
work. It accepts `adapter=mock|hermes|openclaw`, `limit=<n>`, optional
`task_id`, `agent_id`, `q`, `handoff_mode=lightweight|full`,
`full_handoff=true`, and `freshness_hours=<n>`. It composes local readiness,
worker adapter readiness, the worker connection policy, runtime doctor,
live-product ledger proof, a compact Agent Work Method launch brief,
`loop_driver_entry`, `local_run_path`, service-control preview, Agent Plan
boundary, `agent_loop_packet`, `local_loop_admission_packet`, and copyable next
commands into one machine-readable packet.
`loop_driver_entry` exposes copy-only loop-driver preview, `--confirm-loop`,
review queue, and receipt/evidence commands plus a compact RECORD review
snapshot with approval/memory/review counts and raw item summaries/content
omitted. `local_loop_admission_packet` binds Method Block gates to the local
worker-start command, service-control preview, customer-worker dispatch
template, ledger verification, first safe commands, and confirm-required
commands. It requires `tasks:read` for supplied Agent Gateway tokens/sessions
and never starts runtimes, executes server shell, creates tasks, mutates
ledgers/connectors, or exposes tokens/raw prompts/raw responses.

`GET /api/operator/agent-loop-handoff` is the server-side canonical Agent Loop
handoff matrix for Hermes, OpenClaw, Codex, and remote Agents. It accepts
repeatable `adapter=mock|hermes|openclaw`, `limit=<n>`, optional `loop_id`,
`task_id`, `agent_id`, `q`, `handoff_mode=lightweight|full`,
`full_handoff=true`, `freshness_hours=<n>`, and `include_codex=false`. It
aggregates current-code readiness, live Hermes/OpenClaw ledger proof,
per-adapter `start-check` packets, launch briefs, Method Block phase commands,
required gate ids, consumer-specific copyable commands, and a Codex supervisor
block into one read-only response. It requires `tasks:read` for supplied Agent
Gateway tokens/sessions, remains workspace-bound, rolls back auth read
bookkeeping, and never starts runtimes, executes server shell, creates tasks,
mutates ledgers/connectors, approves reviews, or exposes raw prompts, raw
responses, raw content, credentials, or tokens.

`GET /api/operator/loop-supervision` is the read-only pre-confirm supervision
projection that follows the handoff. It accepts the same adapter/task/agent,
query, handoff-mode, and freshness parameters as `agent-loop-handoff`, then
returns per-adapter `can_preview_loop`, `can_confirm_bounded_loop`,
`should_record_before_execute`, review/memory/approval pressure, gate status,
layered `safe_read_commands`, `preview_commands`, and
`confirm_required_commands`. It is the API surface Hermes/OpenClaw/Codex should
read immediately before copying a bounded `operator loop-driver --confirm-loop`
command. It requires `tasks:read`, remains workspace-bound, and never runs
loop-driver, workers, runtimes, approvals, shell commands, or ledger writes.
Each adapter item includes `run_start_admission`, a compact read-only projection
of the Agent Gateway `runs/start` precondition: `would_allow_run_start`,
`run_start_loop_supervision_blocked`, `no_run_created_on_block`, the
`loop_supervision_hash` binding field, safety proof, and recommended next
command.
Confirmed Hermes/OpenClaw customer-worker and installable worker execution
paths consume this projection before live runtime invocation; they write only
compact gate metadata/hash/status to audit/evidence and fail closed when bounded
confirm or no-server-shell safety is not proven.

`GET /api/operator/live-acceptance` is the read-only Hermes/OpenClaw live
customer-worker acceptance freshness projection. It samples recent local worker
runs per adapter, including in-flight `agt_customer_worker_*` attempts before a
delivery artifact exists, and checks tool calls, evaluations, runtime events,
audit logs, artifacts, memory candidates, approvals, and verified
plan-evidence manifests. Each adapter returns `fresh`, `stale`, `missing`,
`latest_failed`, or `latest_incomplete`, with `latest_attempt`,
`latest_passing`, optional `active_attempt`, and a manual
`customer_worker_real_runtime_acceptance.py --confirm-live ... --hermes-max-tokens 512`
command. Active attempts are visible for scheduling and duplicate-run
avoidance, but they do not pass readiness until the run is completed and the
delivery artifact/evidence chain exists. It requires `tasks:read` for supplied
Agent Gateway tokens/sessions and never calls runtimes, starts workers, creates
tasks, mutates ledgers, or exposes tokens/raw prompts/raw responses.

`GET /api/operator/execution-mode` is the read-only dispatch-mode projection
used by UI, CLI operators, and external agents before choosing a worker path.
It accepts `adapter=mock|hermes|openclaw` and optional `confirm_run=true`, then
returns the selected path (`dry_run_or_mock`, `live_confirmation_required`,
`live_confirmed`, or `adapter_route_blocked`), selected adapter readiness,
confirm-run wall, prepared-action wall, pending approval count, active async
workflow job count, live acceptance freshness, and copyable next commands. It
reuses runtime-doctor, adapter-readiness, and live-acceptance evidence, requires
`tasks:read` for supplied Agent Gateway tokens/sessions, and never starts
adapters, creates tasks, writes approvals, or mutates ledgers.

`GET /api/operator/loop-control` is the lightweight, read-only next-step control
projection for real local ledgers. It accepts optional `loop_id=<id>` and
`limit=<n>`, samples bounded counts, recent Action Queue receipt coverage, and
optional `loop://...` readback counts, then returns a copy-only
`work_order.advance_loop.selected_item` with preview/confirm commands for
`agentops operator advance-loop --fast-control`. Unscoped calls select
`operator runtime-doctor` as the first local readiness check; scoped `loop_id`
calls select the next safe loop RECORD/VERIFY action, including a review-queue
step or a reviewable `memory propose --type loop_record` command. It is
receipt-aware: once `advance-loop --fast-control` records a verified receipt for
the unscoped runtime-doctor step, the next unscoped recommendation advances to
handoff, then action-plan, then review-queue when review pressure exists,
instead of looping on the same diagnostic. It remains deliberately cheaper than
full `operator handoff`: it does not call handoff,
action-plan, evidence report, workers, runtimes, or shell commands; it requires
`tasks:read` for Agent Gateway tokens/sessions and never mutates ledgers or
exposes tokens/raw prompts/raw responses.

`GET /api/operator/loop-audit` is the read-only Agent Work Method Block audit.
It turns `READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD`
into seven machine-checkable gates using the existing knowledge index, Agent
Plans, intake checklist, plan-evidence manifests, execution-evidence gaps,
dispatch proofs, human review, memory review, and audit ledger. Optional
`loop_id=<id>` also embeds the Hermes/OpenClaw `loop://...` readback source.
When a `loop_id` has matching loop evidence, the PLAN/RETRIEVE/COMPARE/EXECUTE
and VERIFY gates are scoped to that loop's Agent Plans and manifests so legacy
global gaps remain background context instead of overriding the loop result.
The RECORD gate is also scoped: it passes only after the loop has audit/readback
evidence, no loop-local pending approval or memory candidate, and at least one
approved loop memory record.
For scoped loop audits, the response also includes `loop_record`: safe
`memory_reviews` and `approval_reviews` rows, candidate/approved/pending
counts, exact approve/reject CLI commands, and a safe `audit_trail`/`audit_count`
slice for the loop-local memory and approval review entities. The audit slice
exposes audit ids, actions, entity ids, hashes, and timestamps so
`/workspace/agents` can show both the human review action and the ledger proof
that closes RECORD without exposing raw prompts, responses, metadata, or tokens.
This lets the operator distinguish a globally busy review queue from a specific
loop whose output has already been reviewed into durable memory.
It never creates plans, runs, approvals, memories, or audit rows; it only
returns recommended explicit commands for the gates that are blocked or need
attention.

`GET /api/operator/action-receipts` and
`POST /api/operator/action-receipts` let the human operator record that a queue
recovery action was copied/run and pair it with its follow-up VERIFY command.
Receipts are stored as audit/runtime evidence with command hashes and redacted
command text. They do not execute shell commands, call live runtimes, or persist
raw tokens. `action-plan` includes the `action_receipts` source and receipt
counts in its summary; `loop-audit` includes the same source in RECORD evidence
so the operator can see whether recovery actions were followed by explicit
verification steps. Each receipt-required `action-plan.actions[]` row also
includes `receipt_record_command` for preview-only CLI recording,
`receipt_record_confirm_command` for confirmed append-only recording, and
`receipt_verify_record_command` for recording a verified action/VERIFY pair;
these command strings never execute the recovery or VERIFY commands themselves.
`GET /api/operator/action-receipts` accepts exact read-only filters
`source`, `action_id`, and `action_signature`; the response echoes active
`filters` plus a `filtering` block so agents can prove that a governed action
packet has a current, stale, failed, verified, or missing receipt without
visually scanning recent rows.

`GET /api/agent-gateway/tasks/pull?enforce_intake=true` applies the same gate
at worker pull time: blocked planned/backlog tasks are omitted from `tasks[]`
and returned under `intake.blocked_tasks[]` with failed gate ids and safe next
commands. Local worker loops enable this mode before claiming work.
`POST /api/workers/local/start` and `/restart` also default to this gate for
the daemon agent's pull-visible planned work; a blocked gate returns
`409 worker_intake_blocked` with `task_intake.blocked_tasks[]` and does not
start or restart the daemon.
`POST /api/workers/local/dispatch-once` creates a single UI worker task and
returns top-level `agent_plan_id`, `plan_evidence_manifest_id`,
`plan_evidence_pass`, and an `evidence` readback with intake severity and
ledger counts so the operator can distinguish a freshly proven dispatch from a
blocked backlog task. This fresh one-shot path uses the worker self-plan flow:
it bypasses backlog pull enforcement but must still create and verify an
Agent Plan before `run_start`, then bind the run with a plan-evidence manifest.
For Hermes/OpenClaw external-write wording, dispatch-once now pauses before
starting the local worker subprocess and creates a waiting-approval prepared
action instead.
`GET /api/operator/action-plan` also embeds these verified dispatch/customer
runs as the read-only `dispatch_evidence` source so the command center can keep
showing proofs after the immediate dispatch result card disappears.

`POST /api/operator/execution-evidence/remediation-task` previews or creates a
Commander-compatible remediation package for one run gap. Preview is read-only;
`confirm_create:true` writes one deterministic planned task plus runtime/audit
evidence and returns `already_exists` for repeated calls. Once the package is
dispatched and carries tool/evaluation/artifact/audit plus verified
plan-evidence rows, `GET /api/operator/action-plan` reports the source gap with
`remediation_status=verified` and includes `remediated_evidence_gap_runs` /
`blocked_evidence_gap_runs` in its summary. The same source then recommends the
standard Commander synthesis/review/promotion chain and reports
`evidence_synthesis_ready_runs`, `evidence_synthesis_pending_runs`, and
`evidence_synthesis_promoted_runs`. After promotion, the source gap is still
historical debt until an operator records the final decision.

`POST /api/operator/execution-evidence/close-gap` previews or records that
final decision. `decision` is one of `accepted_remediation`, `waived`, or
`reopen`; preview is read-only, and `confirm_close:true` writes runtime/audit
evidence. `accepted_remediation` fails closed until the remediation synthesis
has been promoted. Action-plan execution-evidence rows may include the latest
gap decision status, but raw notes are omitted.

## Plan Evidence Manifests

```http
POST /api/agent-gateway/plan-evidence-manifests
GET  /api/agent-gateway/plan-evidence-manifests?run_id=run_123
GET  /api/agent-gateway/plan-evidence-manifests/:manifest_id
GET  /api/agent-gateway/plan-evidence-manifests/:manifest_id/verify
```

`POST /api/agent-gateway/plan-evidence-manifests` binds a verified
`agent_plan` to the concrete run evidence that proves the agent followed the
plan:

```json
{
  "plan_id": "plan_123",
  "run_id": "run_123",
  "mismatch_policy": "block",
  "tool_call_ids": ["tc_123"],
  "evaluation_ids": ["eval_123"],
  "artifact_ids": ["art_123"]
}
```

Creation requires `plan_evidence:write` and may persist the verification result.
`GET /verify` requires `plan_evidence:read` and is read-only: it re-computes the
plan/run/task/agent binding and evidence checks without mutating the manifest or
writing audit rows.

Customer delivery approvals fail closed against this gate: approving a customer
delivery approval without a verified manifest returns
`verified_plan_evidence_manifest_required`. The normal worker path creates an
`agent_plan`, records result artifact evidence, and persists a
`plan_evidence_manifest` automatically. The customer-worker workflow reuses that
verified manifest, or creates one from the run ledger, before it generates a
customer delivery approval.

## Tool Calls

```http
GET /api/tool-calls
POST /api/tool-calls/:id/request-approval
```

Supports `limit`, `offset`, `include_page`, `run_id`, and `agent_id` query
parameters. With `include_page=true`, the response envelope uses
`tool_calls`/`items` plus `page` metadata.

## Approvals

```http
GET /api/approvals
POST /api/approvals/:id/approve
POST /api/approvals/:id/reject
```

## Memories

```http
GET /api/memories
GET /api/memories/export
POST /api/memories/:id/approve
POST /api/memories/:id/reject
```

## Evaluations

```http
GET /api/evaluations
POST /api/evaluations/run-rule-check
```

## Dashboard

```http
GET /api/dashboard/metrics
```

Dashboard metrics include baseline MIS counts plus:

- `runtime_health`: OpenClaw, Hermes and Notion status.
- `openclaw_import`: imported OpenClaw agent, cron task, cron run and failed gate counts.
- `agent_performance_summary`: run count, success rate, duration, cost, failure and approval counts.

## Workflows

```http
POST /api/workflows/customer-worker-task
POST /api/workflows/customer-worker-task/submit
POST /api/workflows/hermes-openclaw-loop
GET  /api/workflows/hermes-openclaw-loop?loop_id=loop_123
GET  /api/workflows/customer-task-templates
POST /api/workflows/customer-task-templates/run
POST /api/workflows/customer-task-templates/submit
GET  /api/workflows/jobs
GET  /api/workflows/jobs/stuck
GET  /api/workflows/jobs/:job_id
POST /api/workflows/jobs/:job_id/mark-failed
```

`/customer-worker-task` executes a customer task through the Agent Gateway
worker loop and returns run/artifact/approval/plan-evidence ids. It fails closed
before delivery approval generation when the run cannot produce a verified
`plan_evidence_manifest`. `/customer-worker-task/submit`
queues the same workflow as a `workflow_jobs` row and returns immediately with a
`job_id`, which is the preferred path for real Hermes/OpenClaw work that may
outlive a short browser or CLI request. Job records store status, request hash,
safe summaries, result ids, and safe result JSON; they must not store raw
prompts, raw responses, credentials, tokens, or private transcripts.

`/customer-worker-task/submit` accepts an optional `idempotency_key` with a
maximum length of 512 characters. The Host uses its hash plus workspace and
workflow identity to derive a non-revealing job id; the raw key is removed
before background execution and is never stored or returned. Repeating the
same key and request returns the existing queued/running/completed job without
starting a second Worker. Reusing the key with a different request fails with
`409 idempotency_conflict`. Clients that lose the submit response must retry
with the same key rather than issuing an unkeyed request. The request hash
excludes transport-only `base_url` fields. A same-key replay can launch a
durable `queued` reservation that was persisted before its background thread
started. Every launch must first acquire a short SQLite compare-and-swap lease
on that queued row; an in-process registry supplements the durable claim so
concurrent requests cannot start two threads for the same job. A failed thread
start releases its claim, while an abandoned queued lease can be reclaimed
after its bounded expiry.

This is not a provider-side exactly-once guarantee. A Host crash after a job is
already `running` may leave it for explicit stuck-job review/recovery; MIS does
not silently replay an opaque live Runtime call after restart.

`GET /api/workflows/jobs` is a read-only queue view with optional `status` and
`workflow_type` filters. It returns the current job rows, status/type summaries,
active/stuck counts, and copyable `agentops workflow ...` next actions.
Authenticated human submit requests are bound to the Session workspace;
attempting to select another workspace returns `403`. Human reads of the job
list, stuck list and `GET /api/workflows/jobs/:job_id` are scoped to the Session
workspace and return no cross-workspace job metadata.
Human `mark-failed` and recovery preview/apply operations use the same Session
workspace boundary and return `404` for another workspace's job id.

`tpl_local_coding_project` is the local coding project template exposed through
`GET /api/workflows/customer-task-templates` and executable through
`POST /api/workflows/customer-task-templates/run`. It creates Commander work
packages by default, records task-bound repo-map localization artifacts, and
returns branch/worktree, patch/test/verifier and merge-gate evidence
requirements without running live Hermes/OpenClaw, creating a worktree, storing
raw source, merging or pushing. The next confirmed step is explicit:
`agentops commander coding-workspace --confirm-create` for an isolated local
worktree, then `agentops commander coding-evidence --confirm-record` to write
summary/hash-only worktree, patch, test, verifier and merge-gate evidence back
into MIS.

For confirmed Hermes/OpenClaw tasks that declare `external_write_intent:true`
or match obvious publish/upload/deploy/webhook/external-write wording,
`/customer-worker-task` does not start the live runtime. It creates a
waiting-approval task, run, tool call, prepared action, and approval, then
returns `202 external_write_prepared_action_required` with
`approval_wall`, `approval_id`, `prepared_action_id`, and a precise
`next_action` resume command.

The installable worker loop applies the same preflight rule at the shared
`agentops-worker` execution point. After pull/claim/plan/run-start, but before
`execute_adapter_with_retries`, confirmed Hermes/OpenClaw tasks whose title,
description, acceptance criteria, or target metadata indicate publish/upload/
deploy/webhook/external-write intent create a waiting-approval tool call plus
prepared action and return `external_write_prepared_action_required` without
calling the live adapter. This covers daemon mode, direct `agentops-worker
--once`, and UI dispatch paths.

`/workflows/jobs/stuck` lists queued/running jobs older than a threshold.
`/mark-failed` is an operator recovery action for stale jobs; it marks the job
failed and writes runtime/audit evidence without deleting result history.

`/hermes-openclaw-loop` invokes the supervised Hermes/OpenClaw loop lane through
the Agent Gateway ledger. Dry-run mode is the default; live Hermes/OpenClaw modes
require `confirm_live`. Each parent/child loop lane writes an `agent_plan`,
tool call, evaluation, artifact, audit evidence, and a
`plan_evidence_manifest`. `resume:true` reuses existing gitignored runtime JSONL
rows for a fixed `loop_id`; blocked lanes preserve blocked manifests for
operator review. `GET` is read-only and returns runs, tasks, artifacts,
agent_plans, manifests, audit rows and summary counts for a loop id.

## Audit

```http
GET /api/audit
```

Supports `limit`, `offset`, and `include_page` query parameters. The legacy
array response keeps the existing default cap of 200 audit rows; the paginated
envelope uses `audit_logs`/`items` plus `page` metadata.

## Integrations / OpenClaw

```http
GET  /api/integrations/openclaw/status
POST /api/integrations/openclaw/import
POST /api/integrations/openclaw/probe
```

Import reads local files only:

```text
~/.openclaw/openclaw.json
~/.openclaw/cron/jobs.json
~/.openclaw/cron/runs/*.jsonl
~/.openclaw/subagents/runs.json
```

Deterministic IDs prevent duplicate records on repeated import:

- Agent: `agt_oc_{agentId}`
- Cron task: `tsk_oc_cron_{jobId}`
- Cron run: `run_oc_cron_{jobId}_{sessionId_or_ts}`

Privacy boundary: cron run `summary` raw text is never stored. The database stores a redacted first 200 characters in `runs.output_summary`, plus `summary_hash`, `source_path`, `job_id` and `session_id` style metadata in audit/tool-call metadata.

`POST /api/integrations/openclaw/probe` is manual only and never runs on a schedule. Without `confirm_run:true` it returns a dry-run plan. With `confirm_run:true`, it first creates a `runtime.fixed_probe` prepared action plus approval and does not call OpenClaw. After approval, repeat the request with `prepared_action_id`; MIS verifies the action hash, executes the fixed probe once, records run/evaluation/runtime/audit evidence, and consumes the prepared action.

## Integrations / Hermes

```http
GET  /api/integrations/hermes/status
GET  /api/integrations/hermes/models
POST /api/integrations/hermes/probe
POST /api/integrations/hermes/cli-probe
POST /api/integrations/hermes/chat-completion-probe
POST /api/integrations/hermes/run-task
```

Hermes probe checks local gateway availability on `127.0.0.1:8642`. If the API port is not listening, the endpoint records an `unavailable` health failure as a normal run/evaluation instead of failing the whole MIS service.

Agnesfallback is exposed as a Hermes-compatible runtime connector:

- CLI connector: `rtc_agnesfallback_cli`
- OpenAI-compatible API connector: `rtc_agnesfallback_openai_api`
- Default behavior: dry-run only.
- Real fixed probes require `HERMES_ALLOW_REAL_RUN=true`, request body `{"confirm_run": true}`, and an approved `prepared_action_id` exact resume.
- If live prerequisites are present but `prepared_action_id` is missing, `/cli-probe`, `/chat-completion-probe`, and `/run-task` create a `runtime.fixed_probe` prepared action plus approval and do not call the runtime provider. After approval, repeat the same endpoint with `prepared_action_id`; MIS verifies the action hash, executes the fixed probe once, records run/evaluation/runtime/audit evidence, and consumes the prepared action.
- The CLI probe uses a fixed safe prompt and intentionally excludes `--yolo`.
- `/run-task` supports only a fixed safe Hermes default gateway probe when explicitly confirmed; arbitrary raw task prompts remain disabled.

Environment variables:

```text
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_PROFILE=default
HERMES_RUNTIME_MODE=health_only
HERMES_ALLOW_REAL_RUN=false
HERMES_REQUIRE_CONFIRM_RUN=true
AGNESFALLBACK_BIN=~/.local/bin/agnesfallback
AGNESFALLBACK_GATEWAY_URL=http://127.0.0.1:8643
AGNESFALLBACK_PROFILE=agnesfallback
```

For local acceptance on machines where the Hermes gateway already exposes the Agnesfallback model through `127.0.0.1:8642`, `AGNESFALLBACK_GATEWAY_URL` may point at the same gateway URL. The connector still sends only the fixed probe prompt and stores hashes/summaries, not full prompts or raw responses.

## Private Host Acceptance Receipts

```http
POST /api/host/acceptance-receipts
GET  /api/host/acceptance-receipts/:id
GET  /api/host/acceptance-receipts/:id/download
```

These endpoints are available only in `private_host` deployment mode and
require an authenticated human Owner Session. The POST route additionally
requires valid Origin and CSRF evidence. Agent Gateway machine tokens cannot
substitute for the human Session.

POST accepts exactly one field:

```json
{"run_id": "run_gw_example"}
```

Generation fails closed unless the run is completed and has a passing
evaluation, an associated approved task/run artifact, and a verified plan
evidence manifest. The Host builds an idempotent bounded receipt, computes its
canonical payload SHA-256, writes the authority payload to the dedicated
`private_host_acceptance_receipts` table, creates only a non-sensitive Artifact
summary, and audits generation and download.

The receipt contains IDs, counts, release identity, evaluation result,
`artifact_metadata_sha256`, omission flags, and `payload_sha256`. The artifact
hash covers bounded ledger metadata only; `artifact_file_content_omitted:true`
prevents it from being represented as a source-file hash. Receipt payloads omit
filesystem paths, URL queries, tokens, raw prompts, raw responses, and database
row contents. Non-Owner users may see the generic Artifact metadata but cannot
read or download the authority payload.

## Integrations / Notion

```http
GET /api/integrations/notion/status
GET /api/integrations/notion/export-preview
POST /api/integrations/notion/export-report
POST /api/integrations/notion/preview
POST /api/integrations/notion/dry-run-export
POST /api/integrations/notion/export-confirmed
POST /api/integrations/notion/import-preview
POST /api/integrations/notion/sync-memory-candidates
POST /api/integrations/notion/sync-tasks
```

环境变量：

```text
NOTION_TOKEN=
NOTION_PARENT_PAGE_ID=
NOTION_DATABASE_ID=
NOTION_API_BASE_URL=https://api.notion.com/v1
NOTION_VERSION=2022-06-28
NOTION_WORKSPACE_PRIVATE_EXPORT=false
```

`NOTION_PARENT_PAGE_ID` 和 `NOTION_DATABASE_ID` 二选一即可。

产品化 OAuth / public integration 路径可以设置 `NOTION_WORKSPACE_PRIVATE_EXPORT=true`，在没有 parent/database 时尝试创建 workspace-level private page。Notion 官方限制是：internal integration 通常仍然需要 parent page 或 database；workspace-level private page 只适用于 Notion 允许的 public integration bot / personal access token 场景。

未配置 token，或没有 parent/database 且未开启 workspace private export 时，导出接口只返回 dry-run 预览，不会联网。

POST body：

```json
{
  "dry_run": true,
  "confirm_export": false,
  "title": "AgentOps MIS 项目汇报工作台"
}
```

安全默认：`dry_run` 默认为 `true`。真实导出必须显式传入：

```json
{
  "dry_run": false,
  "confirm_export": true
}
```

`confirm_export:true` 只会进入 Approval Wall；批准后必须用返回的
`prepared_action_id` 再次调用导出接口，MIS 才会按已批准的报告快照创建
Notion 页面。

隐私边界：Notion 导出只包含项目汇报摘要和结构化指标，不导出 credentials、私聊正文、完整 session transcript 或原始命令体。

## Runtime Connectors / Bases / Templates

```http
GET /api/runtime-connectors
GET /api/runtime-events
GET /api/bases
GET /api/connectors
GET /api/external-links
GET /api/sync-events
GET /api/template-packages
GET /api/template-bindings
POST /api/migration/preview
```

`GET /api/runtime-connectors` is now the public read model for runtime
capability manifests and connector trust state. Each row keeps the persisted
`capability_manifest_json` and also returns parsed `capability_manifest`,
`capability_policy_hash`, `observation_level`, `trust_status`,
`require_confirm_run`, `token_omitted:true`, `raw_prompt_omitted:true`, and
`raw_response_omitted:true`. The manifest schema is
`runtime-capability-manifest-v1`; it covers Agent Gateway, OpenClaw, Hermes,
Agnesfallback CLI, and Agnesfallback OpenAI-compatible gateway connectors.

The manifest must declare filesystem, shell, network, Git, secret,
external-write, confirmation, trust-policy and runtime-event ingestion
capabilities. It is read-only: listing connectors does not pull tasks, call
models, run providers, or write ledger rows. Use
`GET /api/workers/adapter-readiness` for route selection and prepared actions
for high-risk live side effects.

The remaining endpoints support the v1.2.1 "external base" story:

- Agent-MIS local bases remain canonical for task, memory, template and audit ledger records.
- Notion is modeled as an external base in dry-run mode by default.
- W&B, Plane, Docmost and Mattermost are represented as planned external bases with capability metadata.
- Template packages describe scenario-specific agent roles, base bindings, memory schema, quality gates and approval policy.
- Migration preview shows what can move between bases, what must stay local, permission changes and rollback steps.

No endpoint imports private Notion content or writes to external systems unless a dedicated confirmed export endpoint is used.

## Demo Scripts

```bash
python3 scripts/demo_seed_openclaw_redacted.py --reset
python3 scripts/demo_acceptance.py --start-server
```

The seed script creates deterministic synthetic data only:

- 10 demo agents
- 50 demo tasks
- 500 demo runs
- 800 demo tool calls
- 200 demo memory candidates
- 2000 demo audit records

The acceptance script verifies local API readiness for dashboard, integrations, runtime connectors, bases, templates, Notion dry-run, Agnesfallback dry-run and migration preview.

## 高风险工具策略

以下工具默认进入审批：

- `shell.exec`
- `github.push`
- `email.send`
- `file.delete`
- `database.write`
- `mcp.invoke`（本 MVP 作为 high risk 示例）
