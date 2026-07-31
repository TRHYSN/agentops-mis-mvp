# Agent Gateway CLI Spec

## Purpose

The Agent Gateway is the machine-facing layer that lets local and remote AI agents participate in AgentOps MIS without pretending to be human browser users.

Browser UI is for humans. CLI, API, and MCP are for agents.

## Why Browser UI Is For Humans

The browser UI is optimized for judgment, review, and supervision:

- Create or edit high-level goals.
- Inspect task queues and agent performance.
- Review approvals.
- Read reports.
- Inspect audit trails.
- Watch Pixel Office status.

Agents should not depend on browser clicks to do their work. Browser automation is brittle, hard to audit, and easy to confuse with human intent.

## Why CLI/API/MCP Is For Agents

Agents need stable machine contracts:

- Pull a task.
- Claim ownership.
- Start a run.
- Send heartbeat.
- Record tool calls.
- Request approval.
- Submit output summaries.
- Propose memory.
- Submit evaluations.
- Emit audit events.

The Agent Gateway makes these operations explicit, scoped, replayable, and auditable.

## Local Debugging And Secrets Policy

Local debugging is allowed and expected in v1.4, especially for OpenClaw, Hermes, Dify, OpenAI File Search, and remote agents. The rule is: debug locally, never persist raw secrets.

For local debug:

- Use environment variables, local config files, or OS keychain-style storage for API keys.
- Never commit `.env`, local tokens, local SQLite databases, runtime logs with credentials, or raw customer files.
- Never store full prompts, private chats, raw responses, or credentials in `runs`, `tool_calls`, `audit_logs`, or memory.
- Store summaries, hashes, IDs, timestamps, status, duration, cost, and scoped metadata.
- Redact request/response previews before writing them to MIS.
- Use `confirm_run:true` or an equivalent explicit flag for real runtime actions.
- Keep dry-run as the default for demos and cloned repos.

Example local environment names for future use:

```text
AGENTOPS_BASE_URL=http://127.0.0.1:8787
AGENTOPS_WORKSPACE_ID=local_demo
AGENTOPS_AGENT_ID=agt_local_researcher
AGENTOPS_API_KEY=local_dev_only_do_not_commit
```

## CLI Commands

The CLI should be a thin wrapper over the Agent Gateway API. It should return JSON by default so agents can parse it.

Current local MVP implementation:

```bash
./scripts/agentops --help
python3 -m pip install -e .
python3 -m pip install .
agentops --help
agentops doctor
./scripts/agentops login --base-url http://127.0.0.1:8787 --workspace-id local-demo --agent-id agt_local_worker
./scripts/agentops status
./scripts/agentops enrollment create --agent-id agt_remote_builder --name "Remote Builder" --runtime openclaw
```

The implementation lives in `agentops_mis_cli/agentops.py`, with `scripts/agentops` as a repo-local compatibility wrapper. `pyproject.toml` also exposes the same command through the `agentops-mis-cli` Python console script for pip source installs on local or remote agent machines. It uses only Python standard library modules, includes a tiny offline build backend, and reads configuration from environment variables or `~/.agentops/config.json`.

### `agentops login`

Stores a local API key or local token for the current workspace.

```bash
agentops login --base-url http://127.0.0.1:8787 --workspace-id local-demo
```

v1.4 can support environment-variable auth only. Interactive login is optional.

### `agentops status`

Checks Agent Gateway connectivity and safe auth metadata without pulling tasks or starting runs.

```bash
agentops status
```

With a scoped remote-agent token, status returns the auth mode, bound `agent_id`, bound `workspace_id`, allowed scopes, token id, expiry, and heartbeat state. It never returns the raw token or token hash.

### `agentops doctor`

Runs a read-only setup diagnostic for local and remote agent machines.

It reports:

- resolved base URL, workspace, agent id, and config path;
- whether an API key/session token is present without printing it;
- auth source category: flag, env, config, default, or missing;
- Agent Gateway status and token omission proof;
- worker provider status, worker count, running workers, pending tasks, and stuck tasks;
- setup hints for missing token, missing agent id, rejected token, or stuck worker recovery.
- `local_demo_probe`, which checks the local demo default without switching targets, including current-code status, so a stale config can report whether `AGENTOPS_BASE_URL=... agentops status`, `agentops login --base-url ...`, or a process restart would repair the operator/agent CLI path.

```bash
agentops doctor
AGENTOPS_API_KEY=agtok_... AGENTOPS_AGENT_ID=agt_remote_builder agentops doctor
```

`agentops doctor` is also a deployment gate. Local loopback demo diagnostics
return exit code `0`; unsafe shared/production or non-loopback targets without a
Gateway token return exit code `2` while still printing redacted JSON with
`deployment_safety.blocks_unsafe_shared_deployment=true`.

### `agentops local readiness`

Returns the single-workspace local closure check. It is read-only and does not
pull tasks, start workers, or execute Hermes/OpenClaw.

```bash
agentops local readiness
curl -fsS http://127.0.0.1:8787/api/local/readiness | jq .
```

It reports Agent Gateway status, worker fleet health, adapter route selection,
memory/knowledge counts, approval counts, task/run/tool/evaluation/audit/artifact
evidence counts, local runbook/doc presence, UI routes, and recommended next
actions. It is the preferred first check before a local demo or after changing
worker/gateway code.

Use `--require-current-code` before handing work to Hermes/OpenClaw/Codex. The
CLI fails closed when the connected MIS process is older than backend source
files or, with `--expect-head-sha`, when it reports a different git HEAD:

```bash
agentops status --require-current-code
agentops local readiness --require-current-code --expect-head-sha "$(git rev-parse HEAD)"
```

### `agentops knowledge evidence-packet`

Returns a read-only retrieval evidence packet for agents and operators. It uses
the existing Markdown/SQLite FTS knowledge index and reports retrieval IDs,
document/chunk IDs, headings, source hashes, visibility, and baseline
Recall@5/MRR/p95 metrics without returning raw snippets, raw content, raw
prompts, raw responses, or tokens.

`agentops-worker` now consumes this packet before adapter execution. For normal
MIS tasks it asks the server for a task-bound packet with `task_id`, so the
authoritative task-aware query is built in one place. The worker falls back to a
local redacted query only when no `task_id` is available. It folds only compact
packet/query hashes, retrieval IDs, paths, source hashes, metrics, and minimal
task-context omission proof into the Agent Plan, prompt hash input, tool-call
args, evaluation rubric, and audit metadata, and keeps raw query/snippet/content/
task text/prompt/response/token omitted. If the worker token lacks
`knowledge:read`, the worker records an `unavailable` compact status instead of
blocking unrelated task execution.

```bash
agentops knowledge evidence-packet "Agent Gateway CLI commands" --limit 5
agentops knowledge evidence-packet --task-id <task_id> --adapter mock --limit 5
curl -fsS "http://127.0.0.1:8787/api/knowledge/evidence-packet?q=Agent%20Gateway%20CLI&limit=5" | jq .
```

When `--task-id` is provided, MIS builds the same bounded task-aware retrieval
query used by `agentops-worker` from the task title, description, acceptance
criteria, risk and adapter hint, but the response still omits raw task text and
returns only `task_context`, `query_hash`, retrieval IDs, paths, source hashes
and metrics.

The Agent Gateway route requires `knowledge:read`. Packet reads are non-mutating:
they do not refresh the index, pull tasks, start runs, write tool calls, or call
Hermes/OpenClaw.

### `agentops knowledge context-packet`

Returns the bounded project context that an Agent can actually use, rather than
only proof that retrieval happened. It combines redacted summaries from the
versioned Knowledge Index with canonical text from human-approved Memory rows.
Full transcripts, raw document bodies, prompts, responses, source refs and
credentials remain excluded.

```bash
agentops knowledge context-packet \
  --task-id <task_id> \
  --adapter hermes \
  --limit 5 \
  --memory-limit 3 \
  --max-chars 4000
```

`agentops-worker` requests this packet first. New-context servers make bounded
context consumption a Worker quality gate. For compatibility, a Worker can
still read the older evidence-only endpoint from an older Host, but it labels
that path as legacy evidence and cannot claim that the model consumed project
explanations. Context summaries are transient model input; persisted
Tool/Evaluation/Audit rows contain only IDs, hashes, counts and
`context_body_not_persisted:true`.

### `agentops agent register`

Registers or updates an AI digital employee identity.

```bash
agentops agent register \
  --agent-id agt_kb_researcher \
  --name "Knowledge Base Researcher" \
  --role researcher \
  --runtime openclaw \
  --scope tasks:read,runs:write,runtime_events:write,toolcalls:write,artifacts:write,approvals:request
```

Maps to `agents`.

### `agentops enrollment create`

Creates a scoped bearer token for a local or remote agent. The token is shown once; MIS stores only a hash.

```bash
agentops enrollment create \
  --agent-id agt_remote_builder \
  --name "Remote Builder" \
  --runtime openclaw \
  --scopes agents:write,agents:heartbeat,knowledge:read,knowledge:write,agent_plans:read,agent_plans:write,plan_evidence:read,plan_evidence:write,tasks:create,tasks:read,tasks:claim,runs:write,runtime_events:write,toolcalls:write,artifacts:write,memories:propose,evaluations:submit,audit:write \
  --ttl-days 30
```

Optional:

```bash
agentops enrollment create --agent-id agt_local_worker --name "Local Worker" --save-token
```

`--save-token` writes the returned token to the local CLI config for this machine only.

The create response also includes a `next_steps` launch packet for the remote machine:

- environment variables for `AGENTOPS_BASE_URL`, `AGENTOPS_WORKSPACE_ID`, and `AGENTOPS_AGENT_ID`
- a placeholder for `AGENTOPS_API_KEY`, never the raw token embedded in a command
- `agentops status`
- `agentops-worker preflight`
- `agentops worker preflight`
- `agentops agent heartbeat`
- one-shot `agentops-worker --once`
- loop-mode `agentops-worker --max-tasks 0 --continue-on-error`
- launchd/systemd service template generation through `agentops-worker service-template`
- dry-run-by-default service file installation through `agentops-worker service-install`
- read-only service diagnostics through `agentops-worker service-check`
- preview-first launchd/systemd load, unload, and restart through `agentops-worker service-control`
- repo-local fallback commands using `python3 scripts/agent_worker.py ...`

This packet is safe to display in the UI because it omits the token value from command strings.

### `agentops enrollment request`

Creates a human approval request before any remote-agent token is issued. This is the safer customer-facing path for unknown machines or contractors.

```bash
agentops enrollment request \
  --agent-id agt_customer_worker \
  --name "Customer Worker" \
  --runtime mock \
  --scopes agents:heartbeat,knowledge:read,agent_plans:read,agent_plans:write,plan_evidence:read,plan_evidence:write,tasks:create,tasks:read,tasks:claim,runs:write,runtime_events:write,toolcalls:write,artifacts:write,memories:propose,evaluations:submit,audit:write \
  --reason "Customer server worker needs to process assigned MIS tasks"
```

The response includes `request_id`, `approval_id`, `task_id`, and `run_id`, but no token.

### `agentops enrollment issue-approved`

Issues a one-time-visible token only after the linked approval has been approved.

```bash
agentops enrollment issue-approved --approval-id ap_123
```

Maps to `agent_gateway_enrollment_requests`, `approvals`, `tasks`, `runs`, `audit_logs`, and then `agent_gateway_tokens`.

### `agentops enrollment list`

Lists token metadata, status, scopes, expiry, and heartbeat freshness. It never
prints token secrets or raw enrollment token IDs.

```bash
agentops enrollment list
```

Heartbeat freshness uses product-facing lifecycle states:

- `never_seen`: token was issued but the remote worker has not connected yet.
- `fresh`: token is active and the latest heartbeat is inside the timeout window.
- `stale`: token is active but the latest heartbeat is older than the timeout window.
- `revoked`: token is no longer valid and should not be shown as live even if it has old heartbeat data.

List/readback output uses `token_ref` plus `token_id_omitted:true`. The raw
`token_id` is shown only in create/issue/rotate responses for explicit local
management flows; operators can use `--agent-id` for bulk revoke or latest
active-token rotation without retaining raw token IDs.

### `agentops enrollment revoke`

Revokes one token or all active tokens for an agent.

```bash
agentops enrollment revoke --token-id agtok_...
agentops enrollment revoke --agent-id agt_remote_builder
```

The revoke response reports counts plus `token_refs`/`session_refs`; it does not
echo raw enrollment token IDs, raw session IDs, token hashes, or token values.
`agentops status` follows the same readback rule: token/session auth status
returns `token_ref`, `session_ref`, or `parent_token_ref` plus omission flags,
not raw enrollment token IDs or raw session IDs.

### `agentops enrollment rotate`

Revokes an active token and issues a replacement token with the same agent binding and scopes by default. The replacement token is shown once; MIS stores only a hash.

```bash
agentops enrollment rotate --token-id agtok_...
agentops enrollment rotate --agent-id agt_remote_builder --ttl-days 30 --save-token
```

Optional `--scopes` can narrow or replace the previous scope list during rotation.

### `agentops agent heartbeat`

Reports liveness, runtime, current task, and safe status metadata.

```bash
agentops agent heartbeat --agent-id agt_kb_researcher --status running
```

Maps to `agents` and `audit_logs`.

### `agentops task create`

Creates a normal MIS task that local or remote agents can pull through the
Gateway. This is the customer/API-facing path when an external system wants to
assign work without using the browser UI.

```bash
agentops task create \
  --title "Build a knowledge-base Q&A bot" \
  --description "Clean source material, build the KB, run test questions, and submit a delivery report." \
  --owner-agent-id agt_kb_researcher \
  --priority high \
  --risk medium \
  --acceptance "Worker must write run, tool call, evaluation and audit evidence."
```

Maps to `POST /api/agent-gateway/tasks` for CLI/remote-agent use, writes
`tasks`, `runtime_events`, and `audit_logs`, and returns JSON with `operation`,
`outcome`, `task_id`, and the redacted task row. Scoped tokens require
`tasks:create` and cannot create tasks as another agent or workspace. Repeated
calls with the same `task_id` update the same task instead of creating
duplicates.

### `agentops task list`

Lists normal MIS tasks as JSON so a customer script, remote worker, or operator
can inspect the queue without opening the browser UI.

```bash
agentops task list --status planned --owner-agent-id agt_kb_researcher --limit 20
```

Maps to `GET /api/agent-gateway/tasks` with scoped workspace and agent
visibility. It is read-only and returns `token_omitted:true`.

### `agentops task get`

Returns one task plus related runs, approvals, evaluations, memories, artifacts,
and evidence counts.

```bash
agentops task get --task-id tsk_clean_sources
```

This is the machine-facing proof path after a customer task runs: the agent or
remote server can verify that MIS recorded work without relying on a human page.

### `agentops task pull`

Returns available tasks for the agent based on role, scope, and workspace policy.

```bash
agentops task pull --agent-id agt_kb_researcher --limit 5 --enforce-intake
```

Reads from `tasks`. `--task-id <id>` narrows visibility checks to one task.
`--enforce-intake` excludes tasks blocked by assignment, Agent Plan
verification, knowledge/base-reference, or high-risk approval gates; local
worker loops use this enforced mode by default before claiming work.
Local daemon `start`/`restart` APIs use the same gate for tasks visible to the
daemon agent and fail closed with `worker_intake_blocked` before launching a
process when blocked planned work is present.
The UI/API direct dispatch path, `POST /api/workers/local/dispatch-once`,
creates a fresh single worker task and returns `agent_plan_id`,
`plan_evidence_manifest_id`, `plan_evidence_pass`, and an `evidence` summary
with intake severity plus ledger counts. It explicitly uses the worker
self-plan path for its newly created task: backlog pull enforcement is bypassed,
but `run_start` still requires a verified Agent Plan and the result must bind
to a verified plan-evidence manifest. `agentops operator action-plan` embeds
recent verified dispatch/customer proofs as `dispatch_evidence` so operators can
track them in the command center after the immediate result card is gone.

### `agentops operator loop-audit`

Reads the Agent Work Method Block as seven explicit gates:

```bash
agentops operator loop-audit --limit 20
agentops operator loop-audit --loop-id loop_smoke_api_123 --limit 10
```

This is a read-only operator audit for
`READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD`. It
combines the knowledge index, Agent Plans, task intake, execution-evidence
gaps, dispatch evidence, review/memory state, audit rows, and optional
Hermes/OpenClaw `loop://...` readback. When `--loop-id` matches loop evidence,
the plan/retrieve/compare/execute/verify gates are scoped to that loop's own
Agent Plans and manifests, while global legacy gaps remain background context.
The record gate is scoped too: it passes only when the loop has audit/readback
evidence, no loop-local pending approval or memory candidate, and at least one
approved loop memory record.
The payload includes a `loop_record` section with safe memory/approval review
rows, candidate/approved/pending counts, exact approve/reject commands, and a
safe audit trail for the scoped loop's review entities. This is what the
`/workspace/agents` Loop Audit panel uses to show both the human review action
and the ledger proof that closes RECORD.
The RECORD gate evidence also includes `receipt_failure_memory_*` counters from
the action-plan learning lane, so repeated failed recovery receipts are visible
as memory-review work instead of disappearing after the receipt evaluation
fails.
Operator handoff and launch packets also surface run-level memory review as
first-class RECORD evidence: `required_ledgers` includes `memories` and
`memory_review`, while the evidence-report gate exposes
`memory_reviews`, `memory_review_ready`, `missing_memory_reviews`, and
`pending_memory_reviews` for Hermes/OpenClaw/Codex before delivery closure.
For worker-run observability, the same evidence report also exposes
`worker_runtime_summary`, `worker_runtime_summary_ready`, and
`worker_runtime_summary_missing`. These fields prove that the Agent Worker
recorded a MIS ledger summary of adapter execution with event ids, hashes, and
omission flags; they are not raw runtime traces and must not include full
prompts, full responses, tokens, or private transcript content.
It also includes an `action_package` section: each non-passing gate gets a
copyable `action_command`, a scoped `verify_command`, a preview-only
`receipt_record_command`, and a confirmed `receipt_verify_record_command`.
Hermes, OpenClaw, Codex, or a human operator can use this package as the
repeatable loop work order after each audit pass.
It recommends explicit next commands but does not create runs, approvals,
memories, audit rows, or live adapter work.

### `agentops operator loop-control`

Reads the lightweight loop-control next step for real local ledgers:

```bash
agentops operator loop-control --limit 8
agentops operator loop-control --loop-id loop_smoke_api_123 --limit 8
agentops operator advance-loop --fast-control --limit 8
```

Maps to `GET /api/operator/loop-control`. This is the fast control-plane
read model for Hermes, OpenClaw, Codex, and a human supervisor before they
enter the heavier `handoff`/`loop-audit` diagnostics. It samples bounded ledger
counts, recent Action Queue receipts, optional `loop://...` artifacts, loop
memory-review state, and the shared bounded-runner policy. It does not call
`operator handoff`, `operator action-plan`, or `operator evidence-report`, so it
stays responsive on large local demo ledgers.

When a loop id is provided, it chooses the next safe RECORD/VERIFY action for
that loop: review existing pending memory/approvals, propose a `loop_record`
memory candidate, or inspect blocked evidence. Without a loop id, it selects
the lightweight `runtime-doctor` first check. `advance-loop --fast-control`
uses this same read model, runs at most one allowlisted local command after
`--confirm-advance`, then records the normal Action Queue receipt and
control-readback evidence. Memory approval/rejection, live runtimes, workflow
dispatch, worker lifecycle, and external writes remain denied by policy.
Unscoped control is receipt-aware: a verified runtime-doctor receipt advances
the next recommendation to `operator handoff`, then `operator action-plan`, then
`review queue` when review pressure exists, rather than repeatedly selecting the
same first diagnostic.

### `agentops operator handoff`

Packages the current operator state for handoff between a human operator,
Hermes, OpenClaw, Codex, or a remote Agent:

```bash
agentops operator handoff --limit 12
agentops operator handoff --loop-id loop_smoke_api_123 --limit 10
```

Maps to `GET /api/operator/handoff`. This is read-only and combines the
loop-audit `action_package`, action-plan receipt coverage, recent receipts, and
loop review state into a single handoff payload. It returns `work_order`,
`receipt_state`, `review_state`, source summaries, `loop_health`, an `auth`
boundary (`mode`, scoped flag, required `tasks:read` scope), and safety flags
without executing commands or mutating audit/runtime ledgers. `work_order` also
contains `receipt_failure_memory`, a machine-readable preview/create/review
work order for repeated failed receipt evaluations: preview is read-only, create
requires explicit `--confirm-create`, and approval/rejection remains a separate
review-queue step. The payload also includes `control_summary`, the same
copy-only loop control view consumed by the command center: recommended handoff
step, next command, verify command, receipt command, control mode,
human/receipt requirements, selected gate, policy id, and server-shell omission
proof. The same control view is also summarized in
`loop_health.gates.loop_control`, including the control readback source
(`agentops operator advance-loop --confirm-advance`) and whether a post-receipt
cache refresh is required. `loop_health` is a read-only score/status snapshot derived from method gates, receipt coverage,
receipt evaluation coverage, receipt-failure memory learning, loop RECORD
state, auth, and safety; it carries gate summaries, risks, and the next
recommended action. Failed receipt evaluations block loop health because they
mean a recovery action was recorded but did not pass the operator-quality gate.
Repeated failed receipt evaluations create an attention risk until the
`failure_case` memory candidate is created and reviewed. Local no-token demo reads remain supported when
`AGENTOPS_API_KEY` is unset; supplied Agent Gateway tokens/sessions must be
valid and carry `tasks:read`. Invalid or out-of-range `limit` values are safely
bounded to the supported 1..30 range instead of turning the handoff endpoint
into a 500.

### `agentops operator loop-self-check`

```bash
agentops operator loop-self-check --limit 12
agentops operator loop-self-check --loop-id loop_smoke_api_123 --limit 10
```

Maps to `GET /api/operator/loop-self-check`. This is the pre-advance read-only
check for Hermes, OpenClaw, Codex, or a remote Agent. It reuses the handoff
snapshot and reports gates for the bounded runner policy, local CLI/server-shell
boundary, receipt coverage, receipt evaluations, audit ledger proof, and handoff
health. It also returns the handoff `control_summary` plus a `loop_control`
gate so agents can verify the recommended next command before copying it. It
returns policy decisions such as "memory approve is denied" and the selected
loop action's allowlist decision, but it never executes commands, starts
workers, approves memory, or mutates ledgers.

### `agentops operator health`

Reads a compact commander health snapshot for the current workspace or scoped
loop:

```bash
agentops operator health --limit 12
agentops operator health --loop-id loop_smoke_api_123 --limit 10
```

Maps to `GET /api/operator/health`. This is read-only and aggregates operator
handoff, local readiness, security readiness, worker fleet health, review queue
pressure, the action-plan summary, and the current loop-control recommendation.
It returns a 0..100 score, component
statuses, risks, next actions, source summaries, `auth` boundary proof, and
safety flags. Each risk includes an explicit action command, verify command,
action signature, and receipt helper commands so the same health issue can be
closed with an audited receipt. The shared Operator Action Queue consumes the
backend `operator action-plan` `operator_health` lane instead of synthesizing
frontend-only health actions. When `receipt_failure_memory_candidates` is
non-zero, the action-plan component moves to attention and recommends
`agentops operator receipt-failure-memories --min-failures 2 --limit 8` so the
operator can promote or review the repeated failure before retrying the same
recovery path. The health payload exposes `control_summary`,
`loop_control`, and matching summary fields so the operator health view can
show the same copy-only/human-confirm/receipt-required boundary that handoff
and self-check use. Like
handoff, local no-token demo reads are allowed only when the server has no
configured API key; supplied Agent Gateway tokens/sessions must be valid, carry
`tasks:read`, and remain bound to their workspace.

### `agentops operator runtime-doctor`

Reads the local runtime launch and safety doctor for Hermes, OpenClaw, Codex,
and remote Agents:

```bash
agentops operator runtime-doctor --limit 8
agentops operator runtime-doctor --runtime-base-url http://127.0.0.1:8787 --limit 8
```

Maps to `GET /api/operator/runtime-doctor`. This is a lightweight read-only
first-check, not the heavier full `operator health` aggregate. It samples
adapter readiness, worker fleet state, and ledger evidence counts, then returns
gates for local MIS API reachability, Hermes/OpenClaw availability,
`--confirm-run`, prepared actions for external writes, remote worker freshness,
launch-packet availability, handoff/evidence-chain status, Codex supervision,
and token/raw-content redaction. The payload includes copyable commands for the
full operator health and handoff checks, runtime preflight, launch packet, loop
audit, evidence report, guarded Hermes/OpenClaw execution, and Action Queue
receipt recording. It never starts runtimes, executes tasks, mutates ledgers or
connector rows, or exposes tokens. Supplied Agent Gateway tokens/sessions must
carry `tasks:read` and remain workspace-bound.

### `agentops operator live-acceptance`

Reads Hermes/OpenClaw live customer-worker acceptance freshness from ledger
evidence:

```bash
agentops operator live-acceptance --limit 8
agentops operator live-acceptance --freshness-hours 72 --limit 8
```

Maps to `GET /api/operator/live-acceptance`. The command anchors on recent
`customer_worker_result` delivery artifacts for local Hermes/OpenClaw customer
worker runs and also surfaces active `agt_customer_worker_*` worker attempts
before their artifact exists. It checks the same run for matching adapter
tool-call, evaluation, runtime-event, audit, memory, approval, and verified
plan-evidence-manifest evidence. It returns adapter status as `fresh`, `stale`,
`missing`, `latest_failed`, or `latest_incomplete`, plus `latest_attempt`,
`latest_passing`, `active_attempt`, artifact ids, and manifest ids when
present. Active attempts are visible for operators but never count as passing
until the run is completed and the delivery artifact/evidence chain exists. It
also returns the manual
`customer_worker_real_runtime_acceptance.py --confirm-live ... --hermes-max-tokens 512`
command for each adapter. It never calls Hermes/OpenClaw, creates tasks, starts
workers, mutates ledgers, or exposes raw prompts, raw responses, credentials, or
tokens.

### `agentops operator live-product-readiness`

Returns the operator-facing product-readiness proof from fresh Hermes/OpenClaw
ledger evidence:

```bash
agentops operator live-product-readiness
agentops operator live-product-readiness --require-adapter hermes --require-adapter openclaw
```

This command reads `/api/operator/live-acceptance` plus `/api/local/readiness`
and collapses them into `product_readiness_proof`. It exits `0` only when every
required live adapter has a fresh completed customer-worker run with matching
tool-call, passing evaluation, runtime event, audit log, customer-worker
artifact, memory candidate, delivery approval, and verified plan-evidence
manifest. It exits non-zero with JSON next actions when proof is missing. It is
read-only: no runtime calls, no task creation, no ledger mutation, no raw
prompt/response, and no token output.

### `agentops operator start-check`

Reads the pre-task local loop start check for Hermes, OpenClaw, Codex, or a mock
worker:

```bash
agentops operator start-check --adapter mock --limit 8
agentops operator start-check --adapter hermes --limit 8
agentops operator start-check --adapter openclaw --limit 8
agentops operator start-check --adapter hermes --task-id tsk_123 --agent-id agt_worker
```

This is the recommended first command before an agent accepts or advances local
work. The CLI maps to `GET /api/operator/start-check`, the canonical MIS read
model that composes `/api/local/readiness`, `/api/workers/adapter-readiness`,
`operator runtime-doctor`, live product-readiness evidence,
`operator loop-launch-packet --brief`, and the bounded `operator loop-driver`
entry.
The result exposes gates for local MIS readiness, the worker connection policy,
adapter preflight, runtime doctor, Agent Work Method launch brief, compact
`loop_driver_entry`, `local_run_path`, service-control preview, Agent Plan
boundary, and live ledger product proof. `loop_driver_entry` returns copyable
preview, `--confirm-loop`, review queue, and verify commands plus a compact
RECORD review snapshot with raw item summaries/content omitted. The same
response now includes `agent_loop_packet`, a machine-readable
READ/PLAN/RETRIEVE/COMPARE/PREFLIGHT/EXECUTE/VERIFY/RECORD command packet, so
agents using the API can follow the same phased loop contract as the CLI
`operator loop-driver` without scraping prose. The same response also includes
`local_loop_admission_packet`, which binds those gates to the local worker-start
command, service-control preview, customer-worker dispatch template, ledger
verification, first safe commands, and confirm-required commands. It also
returns copyable next commands for readiness, preflight, runtime doctor, launch
brief, bounded loop-driver preview/confirmation, bounded advance, confirmed live
dispatch, and task/run/evidence readback.

`start-check` does not start Hermes/OpenClaw, execute shell from the server,
create tasks, mutate ledgers, write connector rows, or print raw prompts,
responses, snippets, API keys, worker tokens, or session tokens. Supplied Agent
Gateway tokens/sessions require `tasks:read` and remain workspace-bound. For
Hermes/OpenClaw it may report `status=attention` when adapter binaries,
credentials, or fresh live product proof are missing; that is still useful loop
input rather than a failed CLI call.

### `agentops operator agent-loop-handoff`

Reads the canonical multi-consumer loop handoff matrix for Hermes, OpenClaw,
Codex, or remote Agents:

```bash
agentops operator agent-loop-handoff --limit 8
agentops operator agent-loop-handoff --adapter hermes --limit 8
agentops operator agent-loop-handoff --adapter openclaw --no-codex
```

Maps to `GET /api/operator/agent-loop-handoff`. The response combines
current-code readiness, fresh live Hermes/OpenClaw ledger proof, per-adapter
`start-check`, compact launch brief state, Method Block phase commands and gate
ids, copyable preflight/preview/confirm/verify/record commands, and the Codex
supervisor block. The command is read-only and copy-only: it does not run
Hermes/OpenClaw, execute server shell, create tasks, approve gates, mutate
ledgers, or print raw prompts, raw responses, raw content, worker tokens,
session tokens, or API keys. Supplied Agent Gateway tokens/sessions require
`tasks:read` and remain workspace-bound.

### `agentops operator loop-supervision`

Reads the pre-confirm supervision gate after handoff and before bounded loop
execution:

```bash
agentops operator loop-supervision --limit 8
agentops operator loop-supervision --adapter hermes --limit 8
agentops operator loop-supervision --adapter openclaw --no-codex
```

Maps to `GET /api/operator/loop-supervision`. The response reuses the canonical
handoff/start-check data and returns per-adapter `can_preview_loop`,
`can_confirm_bounded_loop`, `should_record_before_execute`, review/memory
pressure, gate status, `safe_read_commands`, `preview_commands`, and
`confirm_required_commands`. It is read-only and does not run loop-driver,
workers, Hermes/OpenClaw, approvals, shell commands, or ledger mutations.
Supplied Agent Gateway tokens/sessions require `tasks:read` and remain
workspace-bound.

Each adapter item also includes `run_start_admission`, a read-only projection of
how Agent Gateway will consume the same gate before `runs/start`. It names the
`POST /api/agent-gateway/runs/start` boundary, whether run creation would be
allowed, the `run_start_loop_supervision_blocked` fail-closed error, the
no-run-on-block rule, the `loop_supervision_hash` binding point, and the next
safe command. `/workspace/agents` renders this projection and, after a local
worker dispatch, the actual `loop_supervision_gate` returned by the worker or
Gateway response.

### `agentops operator execution-mode`

Reads the current dispatch mode before a human or agent starts a customer task:

```bash
agentops operator execution-mode --adapter mock
agentops operator execution-mode --adapter hermes
agentops operator execution-mode --adapter hermes --confirm-run
```

Maps to `GET /api/operator/execution-mode`. The command is read-only and
returns the selected path, adapter readiness/trust state, confirm-run wall,
prepared-action wall, pending approvals, active async jobs, and copyable next
commands. It is the CLI/API counterpart to the `/workspace/agents` execution
mode strip. It does not run Hermes/OpenClaw, create tasks, approve actions,
start daemons, or mutate the ledger.

### `agentops approval prepared-action`

Creates and resumes the durable Approval Wall primitive for exact tool/action
governance:

```bash
agentops approval prepared-action create \
  --run-id run_123 \
  --tool-call-id tc_123 \
  --action-type external.publish \
  --args-json '{"target":"mock://customer/delivery","raw_payload_stored":false}' \
  --target-resource mock://customer/delivery \
  --risk-level critical \
  --checkpoint-json '{"checkpoint":"before_external_publish"}' \
  --idempotency-key publish-run-123

agentops approval inspect --approval-id ap_123
agentops approval approve --approval-id ap_123
agentops approval prepared-action resume \
  --action-id pa_123 \
  --provider-side-effect-id provider-side-effect-123
```

The create command stores normalized arguments, a checkpoint, policy version,
idempotency key and `action_hash`, then creates a linked pending approval.
Approval only authorizes the prepared action; it does not perform the side
effect. Resume checks that approval is approved, the hash still matches and the
action has not been consumed, then records provider side-effect evidence exactly
once. Replay returns `prepared_action_already_consumed`.

For high-risk external side-effect tool calls, agents must create the gate while
recording the tool call. `completed` high/critical tool calls, high/critical
tool calls with a `side_effect_id`, and high/critical tool calls with external
upload/publish/write intent are rejected unless `--prepare-action` is used
first:

```bash
agentops toolcall record \
  --run-id run_123 \
  --tool external.publish \
  --category custom \
  --risk critical \
  --status waiting_approval \
  --args-json '{"target":"mock://customer/delivery","raw_payload_stored":false}' \
  --prepare-action \
  --checkpoint-json '{"checkpoint":"before_external_publish"}' \
  --idempotency-key publish-run-123
```

The response includes `approval_wall.prepared_action`, the linked approval, and
a precise `next_action` command chain for inspect, approve, and exact resume.

Dify connector live uploads follow the same exact-resume rule even though the
operator-facing endpoint is `/api/integrations/dify/upload-text` instead of the
generic tool-call API. `confirm_upload` plus `DIFY_ALLOW_REAL_UPLOAD` only moves
the request to the Approval Wall: MIS creates a waiting-approval run/tool call,
prepared action, and approval without calling Dify. After approval, repeat the
upload request with the returned `prepared_action_id`; MIS verifies the stored
upload hash/args before calling Dify and consumes the action with the Dify
document id.

Notion report export follows the same rule. `dry_run:false` plus
`confirm_export:true` only prepares the exact report export action; MIS does not
call Notion until the linked approval is approved and the export endpoint is
called again with the returned `prepared_action_id`. The prepared action binds
the safe report snapshot hash and consumes exactly once with the Notion page id.

### `agentops task claim`

Claims a task for an agent.

```bash
agentops task claim --task-id tsk_clean_sources --agent-id agt_doc_cleaner
```

Updates `tasks` and writes `audit_logs`.

### `agentops run start`

Starts a run for a claimed task.

```bash
agentops run start --task-id tsk_clean_sources --agent-id agt_doc_cleaner --runtime hermes
```

Maps to `runs`.

### `agentops run heartbeat`

Updates run progress without storing full private output.

```bash
agentops run heartbeat --run-id run_123 --status running --summary "Cleaned 4 source files"
```

Maps to `runs` and optionally `audit_logs`.

### `agentops run list`

Lists runs with optional task, agent, and status filters.

```bash
agentops run list --task-id tsk_clean_sources --limit 5
agentops run list --agent-id agt_kb_researcher --status completed
```

Maps to `GET /api/agent-gateway/runs`. With scoped tokens, only runs attached
to visible tasks, or runs owned by the token's agent, are returned.

### `agentops run get`

Returns one run plus tool calls, approvals, evaluations, artifacts, and evidence
counts.

```bash
agentops run get --run-id run_123
```

### `agentops run graph`

Returns parent/child delegation context for one run.

```bash
agentops run graph --run-id run_123
```

Maps to `GET /api/agent-gateway/runs/:id/graph`.

### `agentops agent-plan`

Records and reads the pre-execution READ -> PLAN -> RETRIEVE -> COMPARE plan.

```bash
agentops agent-plan create \
  --task-id tsk_clean_sources \
  --task-understanding "Clean the uploaded sources before indexing." \
  --referenced-specs PROJECT_SPEC.md,AGENT_WORKFLOW.md \
  --referenced-memories knowledge/shared/common_failures.md \
  --referenced-bases base_local_tasks,base_local_memory \
  --execution-steps READ,PLAN,RETRIEVE,COMPARE,EXECUTE,VERIFY,RECORD \
  --verification-plan "Run a focused smoke test." \
  --rollback-plan "Remove generated evidence if verification fails."

agentops agent-plan verify --plan-id plan_123
```

`--understanding` is accepted as a shorter alias for
`--task-understanding`; launch packets use the alias so token scanners do not
misread the longer flag as a secret-like `sk-...` string.

`agentops agent-plan verify` is read-only. `agentops run start` requires a
submitted/verified plan for the same workspace, task, and agent. Pass the plan
explicitly with `--plan-id`, or rely on the latest verified plan for that task
and agent only when the operator intentionally wants that default binding.

### `agentops plan-evidence`

Binds the verified plan to the actual run evidence after execution.

```bash
agentops plan-evidence create \
  --plan-id plan_123 \
  --run-id run_123 \
  --tool-call-ids tc_123 \
  --evaluation-ids eval_123 \
  --artifact-ids art_123 \
  --mismatch-policy block

agentops plan-evidence verify --manifest-id pem_123
```

`create` requires `plan_evidence:write` and can persist verified/blocked
status. `verify` requires `plan_evidence:read`, re-computes ledger checks, and
does not mutate the manifest or write audit rows.

### `agentops toolcall record`

Records a tool call summary, risk level, duration, status, and redacted metadata.

```bash
agentops toolcall record \
  --run-id run_123 \
  --tool browser.search \
  --status completed \
  --risk low \
  --summary "Checked Dify knowledge-base import docs"
```

Maps to `tool_calls`.

### `agentops runtime-event record`

Records a runtime-internal event summary for a run when Hermes, OpenClaw, Dify,
or another adapter can expose an internal tool/checkpoint event. This is an
evidence-ingestion command only; it does not execute the runtime.

```bash
agentops runtime-event record \
  --run-id run_123 \
  --adapter hermes \
  --event-type runtime.tool_event \
  --status completed \
  --output-summary "Hermes internal browser check completed" \
  --payload-hash sha256_abc123
```

Maps to `runtime_events` and `audit_logs`. The endpoint requires
`runtime_events:write`, verifies the run/workspace/agent binding, stores
redacted summaries, and keeps raw runtime payloads out of the ledger. If
`--payload-json` is supplied, the server hashes its redacted metadata surface and
stores only `raw_payload_hash`.

### `agentops approval request`

Requests human approval for a risky operation.

```bash
agentops approval request \
  --run-id run_123 \
  --task-id tsk_upload_sources \
  --risk high \
  --reason "Upload customer PDFs to Dify knowledge base"
```

Maps to `approvals` and `audit_logs`.

### `agentops approval list`

Lists approval gates visible to the current CLI token without granting the agent
permission to decide them.

```bash
agentops approval list --decision pending --limit 10
```

Maps to `GET /api/agent-gateway/approvals`. Scoped tokens require `tasks:read`
and only receive approvals attached to tasks/runs visible to their bound
workspace and agent. `agentops approval approve/reject` remains a human/operator
decision path. Customer-delivery approvals fail closed until the linked run has
a verified `plan_evidence_manifest`.

### `agentops memory propose`

Creates a reviewable memory candidate.

```bash
agentops memory propose \
  --task-id tsk_eval_retrieval \
  --type decision \
  --text "OpenAI File Search needs source metadata for reliable citations"
```

Maps to `memories`. Supported memory types are `policy`, `sop`, `decision`,
`commitment`, `risk`, `failure_case`, `project_context`,
`customer_preference`, `agent_lesson`, `artifact_summary`, and `loop_record`.
Use `loop_record` for approved Hermes/OpenClaw/Codex loop outcomes such as
`source_ref=loop://...`; it should still pass through candidate review before
the loop-audit `RECORD` gate closes.

### `agentops memory list`

Lists reviewable memories visible to the current CLI token.

```bash
agentops memory list --status candidate --limit 10
```

Maps to `GET /api/agent-gateway/memories`. Scoped tokens require `tasks:read`
and only receive task-linked or agent-owned memory rows visible to their bound
workspace and agent. `agentops memory approve/reject` remains a human/operator
decision path.

### `agentops eval submit`

Submits an evaluation result for a run, artifact, or task.

```bash
agentops eval submit \
  --run-id run_123 \
  --gate citation_grounding \
  --score 86 \
  --pass true \
  --notes "Answers cite the uploaded course notes in 8/10 checks"
```

Maps to `evaluations`.

### `agentops audit emit`

Emits a structured audit event for important state transitions.

```bash
agentops audit emit \
  --action connector.plan_created \
  --entity-type tasks \
  --entity-id tsk_kb_setup \
  --task-id tsk_kb_setup
```

Maps to `audit_logs`.

### `agentops workflow templates`

Lists customer-facing workflow templates in JSON for humans, scripts, or
external agents. This is the machine-readable version of the Pixel Office
template picker.

```bash
agentops workflow templates
```

Maps to `GET /api/workflows/customer-task-templates`. It is read-only and must
not print raw tokens, credentials, private transcripts, or customer documents.

### `agentops workflow run-template`

Runs a selected customer workflow template through the same MIS workflow layer
used by Pixel Office. This is the preferred CLI/API path when a customer or
external agent wants to launch a predefined delivery workflow without operating
the browser UI.

```bash
agentops workflow run-template \
  --template-id tpl_customer_kb_qa_bot \
  --title "Build a formal AI knowledge-base Q&A bot" \
  --description "Clean source docs, prepare the KB workflow, run test questions, and submit a delivery report."
```

For a local coding project, the default template path creates Commander work
packages and coding evidence requirements without running live runtimes:

```bash
agentops workflow run-template \
  --template-id tpl_local_coding_project \
  --title "Implement one AgentOps MIS improvement" \
  --description "Split the coding task, localize files, define worktree/patch/test gates, and prepare merge evidence."
```

Maps to `POST /api/workflows/customer-task-templates/run` and returns
`project_id`, `task_id`, `run_id`, `artifact_id`, pending approval ids, and a
customer report URL when available. The command stores only safe summaries,
hashes, and ledger ids; it must not print or persist raw customer documents,
full prompts, full model responses, tokens, or credentials.

Templates can also be dispatched through the real Agent Worker adapter loop:

```bash
agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter openclaw \
  --confirm-run \
  --request-timeout 420 \
  --title "Optimize the AgentOps MIS customer workspace"
```

Without `--adapter`, the template uses its default safe workflow. With
`--adapter mock|hermes|openclaw`, it maps the template defaults into
`POST /api/workflows/customer-worker-task`, executes through the worker loop,
and returns run/tool/evaluation/audit/artifact/memory/approval evidence. Hermes
and OpenClaw require `--confirm-run`; without confirmation the command creates
a planned task and returns `confirm_run_required_for_live_adapter`.

Long live runs can exceed the default CLI HTTP timeout. Use
`--request-timeout` or `AGENTOPS_REQUEST_TIMEOUT`; the CLI automatically raises
the timeout for confirmed Hermes/OpenClaw template runs.

For customer or remote-agent use, long template runs can be submitted as a
workflow job and polled instead of holding one HTTP request open:

```bash
agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter hermes \
  --confirm-run \
  --async-job

agentops workflow job-status --job-id wfjob_... --wait --timeout 420
```

`--async-job` maps to `POST /api/workflows/customer-task-templates/submit`.
Use `agentops workflow jobs --status queued,running,completed --limit 25` for
the read-only queue view before polling or recovery. It maps to
`GET /api/workflows/jobs`, supports `status` and `workflow_type` filters, and
returns status/type summaries plus next-action commands. `job-status` maps to
`GET /api/workflows/jobs/:job_id`. The job record stores status, request hash,
safe summaries, result ids, and token omission metadata; it must not store raw
prompts, raw documents, credentials, or token values.

### `agentops workflow customer-worker-task`

Dispatches a customer-facing task through the AgentOps worker loop. This is the
CLI/API shape for the product usage model where a customer or external agent
sends work to the MIS, and the AI digital employee uses Agent Gateway/worker to
execute it.

```bash
agentops workflow customer-worker-task \
  --adapter mock \
  --title "Improve the customer workspace" \
  --description "Review task creation, AI execution, approval, evaluation, audit and delivery report flow." \
  --acceptance "Return run, tool, evaluation, audit and artifact evidence."
```

Hermes/OpenClaw live execution still requires explicit confirmation:

```bash
agentops workflow customer-worker-task \
  --adapter hermes \
  --confirm-run \
  --hermes-timeout 600 \
  --hermes-max-tokens 512 \
  --title "Optimize AgentOps MIS customer workspace" \
  --description "Use local Hermes to produce product recommendations."
```

For Hermes, `--hermes-timeout` bounds the local gateway request and
`--hermes-max-tokens` caps the OpenAI-compatible completion budget. The default
max-token cap is `HERMES_MAX_TOKENS` or `512`, clamped between `64` and `4096`,
which keeps acceptance runs from drifting into long unbounded summaries.

Confirmed Hermes/OpenClaw tasks that intend to publish, upload, deploy, send,
or otherwise write to an external target must declare the external-write
boundary. In that case the command does not start the live runtime immediately;
it creates a waiting-approval task/run/tool call plus an immutable prepared
action, returns `202 external_write_prepared_action_required`, and gives the
operator an exact approval/resume command.

```bash
agentops workflow customer-worker-task \
  --adapter hermes \
  --confirm-run \
  --external-write-intent \
  --target-resource mock://customer-portal/delivery \
  --external-action-type customer.portal.publish \
  --approval-reason "Publish customer delivery only after human review." \
  --title "Publish the approved customer report" \
  --description "Prepare a customer portal update from the approved MIS report."
```

Maps to `POST /api/workflows/customer-worker-task` and returns `task_id`,
`run_id`, `artifact_id`, `approval_id`, `plan_id`,
`plan_evidence_manifest_id`, `plan_evidence_status`, and evidence counts. The
workflow creates or reuses a verified `plan_evidence_manifest` before generating
the customer delivery approval; if the manifest gate fails, it returns
`verified_plan_evidence_manifest_required` and no delivery approval is created.
For external-write gates, the response instead includes `approval_wall`,
`approval_id`, `prepared_action_id`, `next_action`, and
`live_execution_performed:false`. It must not print raw tokens, full prompts,
full raw model responses,
credentials, or private transcripts.

The same external-write preflight runs inside `agentops-worker` itself. A
daemon or direct `agentops-worker --once --adapter hermes|openclaw --confirm-run`
may process ordinary live tasks, but if the pulled task appears to publish,
upload, deploy, send, webhook, or otherwise write externally, the worker creates
the run-scoped prepared action and exits that iteration before calling Hermes or
OpenClaw.

For real Hermes/OpenClaw work or any customer task that may run longer than a
short HTTP request, submit it as an async workflow job:

```bash
agentops workflow customer-worker-task \
  --adapter openclaw \
  --confirm-run \
  --async-job \
  --title "Optimize AgentOps MIS customer workspace" \
  --description "Use local OpenClaw to produce product recommendations."

agentops workflow job-status --job-id wfjob_... --wait --timeout 420
```

`--async-job` maps to `POST /api/workflows/customer-worker-task/submit`.
Operators can list the queue with
`agentops workflow jobs --status queued,running,completed --limit 25` before
waiting on a specific job. This read-only command returns queue counts, active
job count, stuck job count, and copyable recovery commands.
The job starts quickly, records request hash and safe summaries, and later
stores `result_task_id`, `result_run_id`, `result_artifact_id`, status, and safe
result JSON. This is the preferred product path for customer-visible long
runtime work because the browser and CLI can observe status without pretending
the agent is a human UI user or holding a fragile synchronous request open.

Workflow job recovery commands:

```bash
agentops workflow stuck-jobs --threshold-sec 900 --limit 25
agentops workflow job-mark-failed \
  --job-id wfjob_... \
  --reason "Gateway process died during live run; operator reviewed ledger."
```

`stuck-jobs` maps to `GET /api/workflows/jobs/stuck`. `job-mark-failed` maps to
`POST /api/workflows/jobs/:job_id/mark-failed`. This is an operator recovery
path for jobs left in `queued` or `running` after a server restart, runtime
crash, or gateway disconnect. It does not delete evidence or claim success; it
marks the job failed and writes runtime/audit evidence.

### `agentops workflow hermes-openclaw-loop`

Runs or reads back the supervised Hermes/OpenClaw loop lane. Default mode is
dry-run; live Hermes/OpenClaw calls require `--confirm-live`.

```bash
agentops workflow hermes-openclaw-loop \
  --topic "Review the next AgentOps MIS loop guardrail" \
  --rounds 1

agentops workflow hermes-openclaw-loop \
  --readback \
  --loop-id loop_20260622_review
```

Maps to `POST /api/workflows/hermes-openclaw-loop` and
`GET /api/workflows/hermes-openclaw-loop?loop_id=...`. The workflow runs
`scripts/hermes_openclaw_loop.py --mis-ledger` under bounded timeouts, records
parent/child tasks and runs, and creates per-lane `agent_plan` and
`plan_evidence_manifest` rows. `--resume` reuses existing gitignored loop JSONL
rows for the same `--loop-id`; blocked lanes return nonzero/409 while keeping
blocked manifest evidence for operator readback. It must not print raw prompts,
raw model responses, credentials, or token values.

Smoke coverage: `python3 scripts/hermes_openclaw_loop_smoke.py` checks dry-run,
confirmation gating, resume, CLI/API invocation, readback, verified manifests,
blocked manifests, gitignored runtime files, and token-like leakage.

### `agentops workflow run-task`

Creates a normal MIS task through the scoped Agent Gateway path, executes one
local worker iteration, then returns the task/run ids plus tool/evaluation
evidence. This is the compact CLI path for a customer, script, or external
agent that wants one command instead of manually chaining `task create` and
`agentops-worker --once`.

```bash
agentops workflow run-task \
  --adapter mock \
  --worker-agent-id agt_remote_builder \
  --title "Improve the customer workspace" \
  --description "Review task creation, AI execution, approval, evaluation, audit and delivery report flow."
```

Hermes/OpenClaw live execution still requires explicit confirmation:

```bash
agentops workflow run-task \
  --adapter hermes \
  --confirm-run \
  --worker-agent-id agt_hermes_builder \
  --hermes-timeout 600 \
  --hermes-max-tokens 512 \
  --title "Optimize AgentOps MIS customer workspace" \
  --description "Use local Hermes to produce product recommendations."
```

The command returns JSON with `task_id`, `run_id`, `run_status`,
`task_status`, `evidence`, `readback`, compact `agent_plan`, compact
`plan_evidence`, and `token_omitted:true`. Final task/run evidence is fetched
through `GET /api/agent-gateway/tasks/:id` and
`GET /api/agent-gateway/runs/:id` with `tasks:read`, not through public demo
detail endpoints. The readback also verifies the worker-created Agent Plan and
plan-evidence manifest, exposing `readback.agent_plan_verified`,
`readback.plan_evidence_verified`, `agent_plan.failed_checks`, and
`plan_evidence.evidence_counts`. This makes the one-command worker path prove
READ/PLAN/RETRIEVE/COMPARE/EXECUTE/VERIFY/RECORD closure before another agent
treats the run as done. It must not print raw tokens, full prompts, full raw
model responses, credentials, or private transcripts. Without `--confirm-run`,
Hermes/OpenClaw create a planned task but do not execute the live adapter.

With `--confirm-run`, live Hermes/OpenClaw dispatch first checks the worker
adapter readiness route. If the selected live adapter is `unavailable` or
`blocked`, MIS returns `reason: adapter_not_ready`, creates a blocked recovery
task, records runtime/audit evidence, and does not execute the runtime. This
keeps confirmed customer work from failing later as an opaque worker crash.

### `agentops worker status`

Returns the same safe worker fleet summary used by `/workspace/agents`: worker
count, running workers, pending worker tasks, stuck worker tasks, stuck async
workflow jobs, daemon state, remote enrollment heartbeat state, short-lived
session state, recent worker runs, and recent Gateway events.

```bash
agentops worker status
```

It is read-only and does not print raw tokens. The response also includes
`fleet_health`, a machine-facing operator contract:

- `overall`: `ready`, `attention`, or `blocked`
- `contract`: agents execute through Agent Gateway CLI/API; the browser is an
  operator console only
- `gates`: local daemon, execution capacity, remote heartbeat, session hygiene,
  stuck task, and stuck workflow job checks
- `recommended_actions`: safe next CLI commands such as `agentops worker stuck`,
  `agentops workflow stuck-jobs`, `agentops worker preflight --adapter mock`, or
  `agentops enrollment list`
- `token_omitted:true`

This makes the CLI/API layer usable by external workers and automation scripts,
not only by a human browsing the admin UI.

In Private Host mode this command maps to
`GET /api/agent-gateway/host-workers/status`. It requires the Host machine
credential (`global_api_key`; `local_dev_no_token` remains available only in
loopback developer mode). A bound Agent enrollment or Session token receives
`403 host_machine_credential_required`, because the response includes
Host-wide daemon, process, enrollment, and adapter telemetry. The browser keeps
using `/api/workers/status` with a Human Session.

### `agentops runtime connectors`

Returns the complete runtime connector trust registry and capability manifest
read model. This is broader than `agentops worker readiness`: it includes
Agent Gateway, OpenClaw, Hermes, Agnesfallback CLI and Agnesfallback
OpenAI-compatible gateway connectors, even when a connector is not a normal
worker adapter.

```bash
agentops runtime connectors
```

The command is read-only. It does not pull tasks, claim tasks, start runs,
execute a runtime, call a provider, or write ledger rows. Each connector row
includes parsed `capability_manifest`, `capability_policy_hash`,
`observation_level`, `trust_status`, confirmation policy, and explicit
token/prompt/response omission fields. Capability manifests must declare:

- filesystem
- shell
- network
- Git
- external write
- confirmation
- trust policy
- secrets
- runtime/tool-event ingestion

In Private Host mode the CLI uses
`GET /api/agent-gateway/host-runtime-connectors`. The route requires the Host
machine credential, does not refresh or execute a runtime, and omits raw base
URLs, binary paths, trust notes, and last-error text. The browser continues to
use `GET /api/runtime-connectors` with a Human Session. Bound Agent enrollment
and Session tokens cannot read this Host-wide registry.

Use this command when an operator or remote agent needs to understand what a
runtime is allowed to do before choosing a route or requesting approval.

### `agentops worker readiness`

Returns read-only readiness for all worker adapters in one response. It checks
the AgentOps mock worker, Hermes gateway availability, OpenClaw CLI
availability, and runtime connector trust status. It does not pull tasks,
execute models, write ledger rows, or print raw tokens.

```bash
agentops worker readiness
```

The response includes:

- `summary.ready_adapters`
- `summary.live_ready_adapters`
- `summary.review_required_adapters`
- `summary.blocked_adapters`
- `summary.unavailable_adapters`
- `summary.opaque_runtime_adapters`
- `summary.restricted_capability_adapters`
- `summary.recommended_adapter`
- per-adapter `readiness`: `ready`, `review_required`, `blocked`, or
  `unavailable`
- per-adapter `capability_manifest`, `capability_policy_hash`,
  `observation_level`, `risk_floor`, and `commercial_readiness`
- per-adapter `recommended_action`
- per-adapter `remediation` with `primary_next_action`, missing local checks,
  copy-only preflight/doctor/start/live-run-template/ledger-proof commands,
  and safety proof
- `worker_connection_policy`, a read-only
  `agentops-worker-connection-policy-v1` block covering short-lived session
  refresh, idle/error backoff, adapter retry, daemon error limits, state/log
  fields, and copyable verification commands
- `live_execution_performed:false`

Capability manifests are deliberately conservative. `mock` is
`structured_ledger`; live Hermes/OpenClaw routes are `ledger_summary_only` until
runtime internal tool events can be ingested. For shared/commercial deployment,
external writes from opaque runtimes must be routed through guarded MIS tools
and prepared actions.

The worker loop also uses this manifest when it writes `agent_worker.<adapter>`
tool-call evidence. Mock remains low risk; Hermes/OpenClaw record at least a
medium risk floor and include the observation level, commercial restriction, and
prepared-action requirement in tool-call args, evaluation rubric, and audit
metadata.

Use `agentops worker readiness` when an operator or external agent needs to
choose a route or see what local setup is missing. Use the per-adapter
`remediation.commands` in order: inspect/preflight/doctor commands are read-only;
Hermes/OpenClaw worker starts and live task templates keep `--confirm-run`; the
ledger proof command is `agentops operator live-product-readiness`. Use
`agentops worker preflight --adapter <name>` when debugging one specific adapter.

Remote workers should use the connection policy instead of hard-coding retry
behavior from memory. The v1.5 defaults are `--use-session --session-ttl-sec
900 --session-refresh-margin-sec 60`, `--poll-interval 5`,
`--idle-backoff-max 30`, `--error-backoff-max 30`, `--backoff-factor 2`,
`--continue-on-error`, and `--max-errors 5` for long-running loops. Retryable
adapter failures may retry according to `adapter_retry`; non-retryable safety
gates such as missing `--confirm-run` must not retry.

In Private Host mode the packaged Host CLI maps this command to
`GET /api/agent-gateway/host-workers/adapter-readiness`. It follows the same
Host-machine-only rule as `agentops worker status`. Remote Agent tokens should
consume their scoped task/run/evidence contract rather than Host filesystem,
daemon, or runtime prerequisite telemetry.

### `agentops worker preflight`

Runs read-only Gateway and adapter readiness checks from the main operator CLI.
It does not pull a task, claim work, start a run, execute a runtime, or write
ledger rows.

```bash
agentops worker preflight --adapter mock
agentops worker preflight --adapter hermes
agentops worker preflight --adapter openclaw
```

For Hermes it checks the configured OpenAI-compatible gateway health/models
endpoints. For OpenClaw it checks whether the binary is present and executable,
and attempts a version read only. The result includes
`live_execution_performed:false`.

### `agentops worker start`

Starts a local worker daemon through the MIS supervisor. `mock` can start directly; `hermes` and `openclaw` require explicit `--confirm-run`. `--confirm-run` is not external-write authorization: worker preflight still pauses publish/upload/deploy/webhook tasks behind a prepared action before any live adapter call.

```bash
agentops worker start --adapter mock --poll-interval 5 --max-tasks 0
agentops worker start --adapter hermes --confirm-run --poll-interval 5 --max-tasks 0
```

Maps to `POST /api/workers/local/start`.

### `agentops worker logs`

Returns daemon metadata and a bounded log tail for one adapter.

```bash
agentops worker logs --adapter mock
```

Maps to `GET /api/workers/local/logs`.

### `agentops worker stop`

Stops one local daemon or all local daemons.

```bash
agentops worker stop --adapter mock
agentops worker stop --adapter all
```

Maps to `POST /api/workers/local/stop`.

### `agentops worker stuck`

Lists running worker tasks that exceeded the local recovery threshold. This is an operator view; it does not expose prompts, responses, or tokens.

```bash
agentops worker stuck --threshold-sec 900 --limit 25
```

In Private Host mode this maps to
`GET /api/agent-gateway/host-workers/stuck-tasks`. The browser operator view
remains `/api/workers/stuck-tasks` behind Human Session. The CLI route is
Host-machine-only because stuck-task recovery is an installation-wide operator
view, not an Agent-scoped task feed.

### `agentops worker release`

Releases a stuck running task back to `planned` and blocks any linked running run with `WorkerTaskReleased`.

```bash
agentops worker release --task-id tsk_... --reason "operator recovery"
```

Maps to `POST /api/workers/tasks/release` and writes runtime/audit evidence.

### `agentops session create`

Mints a short-lived session token from a scoped enrollment token. The session inherits the bound `agent_id`, `workspace_id`, and a scope subset. It is shown once; MIS stores only a hash.

```bash
agentops session create \
  --ttl-sec 900 \
  --scopes agents:heartbeat,tasks:read,runs:write,runtime_events:write,toolcalls:write,evaluations:submit,audit:write
```

Session tokens cannot mint more sessions. They are for worker loops, not for browser users.

### `agentops session list`

Lists short-lived session metadata without secrets.

```bash
agentops session list
```

Maps to `GET /api/agent-gateway/sessions`. The response omits `session_hash` and raw token values.
It also omits raw session IDs and parent token IDs: operators get
`session_ref`, `parent_token_ref`, `session_id_omitted:true`, and
`parent_token_id_omitted:true` for readback correlation.

### `agentops session revoke`

Revokes one short-lived session or all active sessions for one agent.

```bash
agentops session revoke --session-id agtsess_...
agentops session revoke --agent-id agt_remote_builder
```

The raw `session_id` is only shown at session creation time for local
management flows; list/readback outputs are refs-only. Operators can use
`--agent-id` for bulk active-session revocation without retaining raw IDs.

Maps to `POST /api/agent-gateway/session/revoke` and writes runtime/audit evidence.
The public response reports safe `session_refs` only and includes
`session_id_omitted:true`.

### Worker-owned graceful Session release

`agentops-worker` calls `POST /api/agent-gateway/session/revoke-self` after a
normally bounded run publishes its final `disabled` heartbeat. The route does
not accept a caller-selected Session ID: only the currently authenticated
short-lived Session can revoke itself. Enrollment tokens, local/global machine
credentials and anonymous requests cannot use this route. The response exposes
only a stable `session_ref` plus omission flags and writes runtime/audit
evidence. This is an internal Worker lifecycle action, not a Human operator CLI
command.

## API Endpoint Proposal

All endpoints are under the existing local API server.

```http
GET  /api/agent-gateway/enrollments
GET  /api/agent-gateway/sessions
GET  /api/agent-gateway/status
GET  /api/agent-gateway/host-workers/status
GET  /api/agent-gateway/host-workers/fleet
GET  /api/agent-gateway/host-workers/adapter-readiness
GET  /api/agent-gateway/host-runtime-connectors
GET  /api/agent-gateway/host-workers/stuck-tasks
POST /api/agent-gateway/enrollment/create
POST /api/agent-gateway/enrollment/request
POST /api/agent-gateway/enrollment/issue-approved
POST /api/agent-gateway/enrollment/revoke
POST /api/agent-gateway/enrollment/rotate
POST /api/agent-gateway/session/create
POST /api/agent-gateway/session/revoke
POST /api/agent-gateway/session/revoke-self
POST /api/agent-gateway/register
POST /api/agent-gateway/heartbeat
GET  /api/agent-gateway/tasks
GET  /api/agent-gateway/tasks/pull
GET  /api/agent-gateway/tasks/:id
POST /api/agent-gateway/tasks/:id/claim
GET  /api/agent-gateway/runs
GET  /api/agent-gateway/runs/:id
GET  /api/agent-gateway/runs/:id/graph
POST /api/agent-gateway/runs/start
POST /api/agent-gateway/runs/:id/heartbeat
POST /api/agent-gateway/tool-calls
GET  /api/agent-gateway/artifacts
POST /api/agent-gateway/artifacts
POST /api/agent-gateway/approvals/request
POST /api/agent-gateway/memories/propose
POST /api/agent-gateway/evaluations/submit
POST /api/agent-gateway/audit
```

### Common Request Fields

Every write endpoint should accept or infer:

```json
{
  "workspace_id": "local_demo",
  "agent_id": "agt_kb_researcher",
  "runtime_type": "openclaw",
  "request_id": "optional_idempotency_key"
}
```

### `GET /api/agent-gateway/status`

Returns safe gateway readiness and auth metadata for local or remote agents.

Reads:

- `agent_gateway_tokens`
- `agents`

Never returns:

- raw token value
- token hash
- raw enrollment token ID
- raw short-lived session ID
- raw parent token ID
- full prompts or raw outputs

When authenticated by a token or short-lived session, `auth` uses safe refs
(`token_ref`, `session_ref`, `parent_token_ref`) and explicit omission flags
(`token_id_omitted`, `session_id_omitted`, `parent_token_id_omitted`) so
operators can correlate status without exposing raw credential identifiers.

### `POST /api/agent-gateway/register`

Creates or updates an agent identity.

Writes:

- `agents`
- `audit_logs`

### `POST /api/agent-gateway/heartbeat`

Updates liveness and current work state.

Writes:

- `agents`
- `audit_logs` for meaningful state changes only

### `GET /api/agent-gateway/tasks/pull`

Returns tasks matching agent scope, role, and status.
When `enforce_intake=true`, blocked planned/backlog tasks are omitted from
`tasks[]` and reported under `intake.blocked_tasks[]` with failed gate ids,
next action text, and the safe CLI command to run before worker execution.

Reads:

- `tasks`
- `agents`
- `agent_plans` when intake enforcement is enabled
- future policy tables

### `POST /api/agent-gateway/tasks/:id/claim`

Assigns a task to the caller when allowed.

Writes:

- `tasks`
- `audit_logs`

### `POST /api/agent-gateway/runs/start`

Creates a run for an agent and task. This endpoint fails closed with
`428 agent_plan_required` unless the request is bound to a submitted Agent Plan
that verifies against the Agent Work Method Block. The plan must match
`workspace_id`, `task_id`, `agent_id`, and stored `plan_hash`.

Example:

```json
{
  "task_id": "tsk_example",
  "agent_id": "agt_builder",
  "runtime_type": "mock",
  "agent_plan_id": "plan_123",
  "input_summary": "Plan-bound run start."
}
```

The created run stores `agent_plan_id` and `plan_hash`; the response includes
an `agent_plan.verification_pass` summary so operators can audit the execution
boundary before later binding a plan-evidence manifest.
For `runtime_type` `hermes`, `openclaw`, or `codex`, `runs/start` also consumes
the read-only loop-supervision gate before a run row is created. If the gate is
blocked, unavailable, preview-only, or cannot prove bounded confirm plus
no-server-shell safety, the endpoint returns
`428 run_start_loop_supervision_blocked`, writes task-scoped audit/runtime-event
evidence, and reports `live_execution_performed:false`. If the gate allows
run-start, the response includes `loop_supervision_gate` and
`agent_plan.loop_supervision_hash`; these are safe hashes/statuses, not raw
runtime transcripts or credentials.
For `hermes` and `openclaw`, `runs/start` also consumes
`agent_work_packet_decision_v1` before run creation. `stop`, `blocked`,
`missing`, server-shell, server-may-execute or live-execution decision evidence
returns `428 run_start_work_packet_decision_blocked` before any run row exists.
Allowed decisions are still recorded as governance evidence through
`work_packet_decision_gate` and `agent_plan.work_packet_decision_hash`.
If `run_id` already exists, `runs/start` is idempotent only for the same
`workspace_id`, `task_id`, `agent_id`, `agent_plan_id`, and `plan_hash`.
Attempts to rebind an existing run to another Agent Plan or hash fail with
`409 run_start_rebind_forbidden`.

Writes:

- `runs`
- `audit_logs`
- `runtime_events`
- `agent_plans` verification metadata when the selected plan is verified

### `POST /api/agent-gateway/runs/:id/heartbeat`

Updates run status, duration, safe output summary, and progress metadata.

Writes:

- `runs`
- `audit_logs` for important transitions

### `POST /api/agent-gateway/tool-calls`

Records tool-call evidence.

Writes:

- `tool_calls`
- `approvals` when policy requires approval
- `audit_logs`

### `POST /api/agent-gateway/artifacts`

Records a delivery artifact summary without storing raw customer content.

Writes:

- `artifacts`
- `runtime_events`
- `audit_logs`

### `agentops artifact record`

Records a customer-readable artifact reference after a run, while keeping the full artifact body in the customer-approved system of record.

```bash
agentops artifact record \
  --run-id run_123 \
  --title "Knowledge-base bot delivery summary" \
  --type customer_delivery_report \
  --uri agentops://kb-bot-demo/project/delivery-summary \
  --summary "Delivered task plan, connector decision, QA rubric and pending external upload approval."
```

Maps to `artifacts`, `runtime_events`, and `audit_logs`.

### `agentops artifact list`

Lists safe artifact summaries without fetching raw customer files or full report
bodies.

```bash
agentops artifact list --task-id tsk_clean_sources --limit 10
agentops artifact list --run-id run_123 --type customer_worker_result
```

Maps to `GET /api/agent-gateway/artifacts` with scoped task/run visibility and
returns `token_omitted:true`.

### `agentops review queue`

Returns the combined human review queue as JSON: pending approvals, candidate
memories, evaluation case candidates, failed evaluation-case benchmark runs,
Commander synthesis actions, and customer delivery items that need human
attention.

```bash
agentops review queue --limit 12
```

Maps to `GET /api/agent-gateway/review/queue`. Scoped tokens require
`tasks:read`; results are constrained by the token's bound workspace and agent
visibility rules before the requested `limit` is applied, so unrelated global
review pressure cannot push a remote worker's own visible item out of the
queue. The payload exposes `gateway_scope.scope_before_limit:true` and
`summary.scoped_totals_before_limit:true` for bound tokens; this is covered by
`scripts/agent_gateway_review_queue_smoke.py`, which creates hidden noise in a
different workspace and still requires the scoped item to survive `limit=1`.
The local browser UI can still use `GET /api/review/queue`, but remote workers
and machine-facing CLI flows should use the Agent Gateway path and must omit raw
token/session values.

Failed benchmark runs are read-only risk items, not approval objects. Operators
should inspect the linked run/task, preview a remediation task, and only then
confirm creation of a normal MIS work item that is also readable as a
Commander work package. Acknowledged or waived failed benchmark runs remain in
the evidence ledger but no longer block the review/action queues.

```bash
agentops eval case-runs --pass-fail fail --review-status open
agentops eval remediate-case-run --case-run-id ecr_123
agentops eval remediate-case-run --case-run-id ecr_123 --confirm-create
agentops commander packages --project-id proj_evalcase_remediation_x --limit 5
agentops commander dispatch-package --task-id tsk_evalcase_fix_x --adapter mock
agentops eval review-case-run \
  --case-run-id ecr_123 \
  --status acknowledged \
  --note "Investigated and accepted for this local run."
```

`remediate-case-run` maps to
`POST /api/evaluation-case-runs/:case_run_id/remediation-task`. It previews by
default and creates a planned task only with `--confirm-create`; it never calls
Hermes/OpenClaw or changes code by itself. Confirmed remediation tasks use the
Commander work-package description contract, so they can be read with
`agentops commander packages`, dispatched with `agentops commander
dispatch-package`, batched, and synthesized through the normal Commander loop.
Reruns still require the explicit `agentops eval run-cases` path.

### `agentops operator command-center`

Returns the stable operator command-center BFF as JSON.

```bash
agentops command-center overview --limit 12
agentops operator command-center --limit 12
agentops operator command-center --project-id proj_local_coding --limit 8
```

Maps to `GET /api/operator/command-center`. `agentops command-center overview`
is a product-facing alias for the same canonical operator BFF. This is the
read-only supervisor view for humans, Codex, Hermes, OpenClaw and remote
agents: projects, blocked runs, pending approvals, customer deliveries, stale
worker references, Commander coding evidence gates, and prioritized next
actions in one response. It reports safety flags including `read_only:true`,
`ledger_mutated:false`, `worktree_created:false`,
`live_execution_performed:false`, raw prompt/response omission, raw source/patch
omission, and `token_omitted:true`.

### `agentops operator action-plan`

Returns the prioritized operator command-center plan as JSON. It merges the
review queue, customer delivery board, worker fleet status, adapter readiness,
commander inbox, task-intake gates, execution-evidence gaps, remediation loop,
and a non-recursive `operator_health` recovery source into safe next CLI/UI
actions.

```bash
agentops operator action-plan --limit 12
```

Maps to `GET /api/operator/action-plan`. This is read-only: it must not start a
worker, approve a gate, upload data, or call Hermes/OpenClaw. It returns
`top_commands`, `actions[]`, source statuses, safety flags, and
`token_omitted:true` so a local admin, remote operator, or future commander
agent can decide which explicit command to run next. `execution_evidence` is a
read-only audit source for the loop contract: it inspects recent completed or
failed runs for missing `agent_plan_id` / `plan_hash`, missing or unverified
`plan_evidence_manifests`, and missing tool/evaluation/artifact/audit rows. It
reports summary counters such as `evidence_gap_runs`, `missing_plan_runs`,
`missing_plan_evidence_manifests`, and
`unverified_plan_evidence_manifests`, then suggests
`agentops operator remediate-evidence-gap --run-id <run_id>` without mutating
the ledger. `task_intake` is the pre-run source for planned/backlog tasks: it
checks assignment, verified Agent Plan, knowledge/spec references, base
references, and high-risk approval boundaries before the task is pulled.
`operator_health` actions mirror health components that can be checked without
calling full health recursively, such as local readiness, security readiness,
worker fleet health, or human-review pressure, and verify through
`agentops operator health --limit 20`. `receipt_failure_memory` is the learning
source for repeated failed Action Queue receipt evaluations: it stays
preview-first, proposes a memory candidate only after repeated failures, and
routes confirmed candidates into the normal review queue instead of making them
authoritative automatically. Each
receipt-required `actions[]` row also includes `receipt_record_command`
(preview-only), `receipt_record_confirm_command` (append a recorded receipt),
and `receipt_verify_record_command` (append a verified action/VERIFY receipt)
so a human, Hermes, OpenClaw, or Codex operator can close the RECORD gate
through the same audited CLI path.

### `agentops commander repo-map`

Returns deterministic file/symbol candidates for a coding work package without
mutating the ledger or executing local commands.

```bash
agentops commander repo-map "Agent Plan approval gate" --limit 8 --char-budget 4800
agentops commander repo-map --query "prepared action runtime probe" --limit 6
```

Maps to `GET /api/commander/repo-map`. The command returns ranked relative
paths, extracted symbols, short redacted snippets, content hashes, source
provenance, rank reasons, and token-budget proof. It excludes local databases,
env/key files, generated outputs, dependency folders, archives, binaries, logs,
and caches, and reports `read_only:true`, `ledger_mutated:false`,
`live_execution_performed:false`, `raw_file_bodies_returned:false`, and
`token_omitted:true`.

Use this before drafting an Agent Plan or handing a coding task to another
agent so the work package includes scoped file candidates instead of vague
repo-wide instructions. It is not an approval, merge gate, runtime adapter, or
substitute for tests; any actual code change still has to pass the Agent Plan,
Approval Wall where relevant, verification, and release gates.

### `agentops commander coding-template`

Returns the local coding project template contract without creating tasks,
worktrees, patches, runs or live-runtime calls.

```bash
agentops commander coding-template \
  --query "P1-02 local coding project template" \
  --project-id proj_local_coding \
  --task-id tsk_local_coding_example
```

Maps to `GET /api/commander/coding-project-template`. The response links the
Commander WorkPackage contract, repo-map localization, branch/worktree naming,
patch capture, verifier commands, required artifacts, plan-evidence manifest
and strict merge-readiness gate. It reports `read_only:true`,
`ledger_mutated:false`, `worktree_created:false`, `patch_created:false`,
`repo_root_omitted:true`, and `token_omitted:true`.

### `agentops commander coding-workspace`

Previews or creates the isolated git worktree for one Commander coding package.

```bash
agentops commander coding-workspace --task-id tsk_local_coding_example
agentops commander coding-workspace --task-id tsk_local_coding_example --confirm-create
agentops commander coding-workspace-cleanup --task-id tsk_local_coding_example --confirm-cleanup
```

Maps to `POST /api/commander/work-packages/:task_id/coding-workspace` and
`POST /api/commander/work-packages/:task_id/coding-workspace/cleanup`.
Preview mode returns the branch, worktree path hash, repo-status summary and
safety proof without mutating the ledger or filesystem. `--confirm-create`
creates the worktree outside the repository and records a
`commander_worktree_workspace` artifact. Cleanup removes the worktree and, by
default, deletes the temporary branch. Use `--branch` when an operator needs a
custom branch name, and pass the same value to `coding-evidence` and cleanup.

### `agentops commander coding-evidence`

Records the patch/test/verifier/merge-gate evidence for a dispatched Commander
coding package.

```bash
agentops commander coding-evidence \
  --task-id tsk_local_coding_example \
  --run-id run_local_coding_example \
  --collect-from-worktree \
  --confirm-record
```

Maps to `POST /api/commander/work-packages/:task_id/coding-evidence`. It is a
dry run unless `--confirm-record` is present. With `--collect-from-worktree`,
the command collects bounded git status/diff metadata and syntax-check results
from the prepared worktree, strips raw patch/log output, and writes
summary/hash-only `commander_worktree_workspace`, `commander_patch_manifest`,
`commander_test_log`, `commander_verifier_report`, and
`commander_merge_gate_receipt` artifacts plus a rule evaluation and audit row.
It does not merge, push, or execute a live runtime.

```bash
agentops operator action-receipts --limit 12
agentops operator action-receipts --limit 8 --plan-limit 20
agentops operator action-receipts \
  --source local_harness_proof.governed_launch \
  --action-id local_harness_proof:openclaw \
  --action-signature <signature> \
  --limit 20
```

Maps to `GET /api/operator/action-receipts` plus read-only action-plan receipt
coverage. This command does not record receipts and must not mutate ledger
rows. It returns recent receipt rows, `receipt_coverage` with
required/verified/stale/missing counts, `action_plan_status`, top commands, and
safety flags. Use it when a CLI operator needs the same Action Queue receipt
health that `/workspace/agents` shows before deciding which explicit recovery
command to run. The optional `--source`, `--action-id`, and
`--action-signature` filters are for machine readback of an exact governed
action packet; they remain read-only and are echoed in the response `filters`
and `filtering` blocks.

```bash
agentops operator record-action-receipt \
  --action-command "agentops operator action-plan --limit 20" \
  --verify-command "agentops operator loop-audit --limit 20" \
  --status verified

agentops operator record-action-receipt \
  --action-command "agentops operator action-plan --limit 20" \
  --verify-command "agentops operator loop-audit --limit 20" \
  --status verified \
  --confirm-record
```

Maps to `POST /api/operator/action-receipts` only when `--confirm-record` is
present. Without confirmation it returns
`operator_action_receipt_cli_preview`, hashes the supplied action/verify
commands, and does not mutate the ledger. With confirmation it appends
`operator.action_queue_receipt` runtime/audit evidence, still never executing
`action_command` or `verify_command`. Confirmed `verified` and `failed`
receipts also write an `operator_action_evaluations` rule score and an
`operator.action_queue_evaluation` audit row, so recovery work has both RECORD
and VERIFY-quality evidence. `operator action-plan`, `operator loop-audit`, and
`operator health` consume this evaluation signal: failed receipt evaluations
surface as a blocked `receipt_evaluation` recovery action, and coverage
summaries include evaluated/pass/fail/missing counts. Valid receipt statuses
are `recorded`, `verified`, `failed`, and `skipped`.

```bash
agentops operator receipt-failure-memories --min-failures 2 --limit 8

agentops operator propose-receipt-failure-memory \
  --action-hash <action_hash> \
  --min-failures 2

agentops operator propose-receipt-failure-memory \
  --action-hash <action_hash> \
  --min-failures 2 \
  --confirm-create
```

Maps to `GET /api/operator/receipt-failure-memories` and
`POST /api/operator/receipt-failure-memories/propose`. The read command is
ledger read-only and groups failed receipt evaluations by `action_hash`. The
proposal command is also read-only by default: it returns a deterministic
`failure_case` memory draft, source refs, receipt/evaluation evidence ids, and
safety flags. `--confirm-create` writes a `memories` row with
`review_status:candidate` plus runtime evidence, then the item must be approved
or rejected through `agentops review queue` / `agentops memory approve|reject`.
This prevents failed recovery paths from silently becoming authoritative
project memory. `/workspace/agents` exposes the same preview/confirm path in
the Operator Handoff receipt-failure-memory card: preview stays read-only,
while create requires explicit confirmation and refreshes the review queue.

```bash
agentops operator intake-checklist --limit 12
```

Maps to `GET /api/operator/intake-checklist`. This is read-only and reports
`ready_for_intake`, `blocked_for_intake`, `attention_for_intake`,
`missing_agent_plan`, `missing_knowledge_retrieval`, and
`missing_base_reference` without starting workers or writing ledger rows. Ready
rows point to `agentops task pull --enforce-intake` so worker execution uses the
same gate decision as the operator console.

```bash
agentops operator start-check --adapter mock --limit 8
agentops operator start-check --adapter hermes --limit 8
agentops operator start-check --adapter openclaw --limit 8
```

This is the pre-task local loop start check for Codex, Hermes, OpenClaw, or a
remote operator. It reads `GET /api/operator/start-check` and stays read-only:
it aggregates `agentops local readiness`, `agentops worker readiness`,
`operator runtime-doctor`, `operator loop-launch-packet --brief`, worker
connection policy, local run path, service-control preview, and live
product-readiness readback for the selected adapter. It does not start workers,
call runtimes, execute shell commands, mutate ledgers, or expose tokens/raw
prompts/raw responses. Use it immediately before handing a task to a local or
remote agent so the agent sees the current copyable
boot/readiness/preflight/launch/verify commands in one payload.

The response includes two machine-facing gate packets:

- `agent_loop_packet`: phase commands and Method Block gates for READ, PLAN,
  RETRIEVE, COMPARE, PREFLIGHT, EXECUTE, VERIFY, and RECORD.
- `local_loop_admission_packet`: a compact admission readback that binds those
  gates to worker start, service-control preview, customer-worker dispatch, and
  ledger verification commands while preserving `server_executes_shell:false`.

Agent Gateway enrollment `next_steps` mirrors this for remote machines with
`start_check`, `loop_launch_brief`, and `method_gate_contract`, so a newly
enrolled worker can run start-check before preflight without relying on the
browser UI.

```bash
agentops operator loop-launch-packet --task-id tsk_123 --agent-id agt_worker --limit 8
agentops operator loop-launch-packet --task-id tsk_123 --agent-id agt_worker --brief --adapter hermes
agentops operator loop-launch-packet --task-id tsk_123 --agent-id agt_worker --brief --adapter openclaw
agentops operator loop-launch-packet --task-id tsk_123 --agent-id agt_worker --handoff-mode full
```

Maps to `GET /api/operator/loop-launch-packet`. This is a read-only Agent Work
Method launch packet for Hermes, OpenClaw, Codex, or a remote Agent. It combines
the intake checklist, safe knowledge-search metadata, repo-map localization,
operator control state, and a complete agent-plan draft into one machine-readable
`READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD` sequence.
Default control uses lightweight `operator loop-control`; `--handoff-mode full`
or `--full-handoff` switches the packet to deeper `operator handoff`
diagnostics. The payload exposes `sources.operator_control` and a
backward-compatible `sources.handoff` alias so older agents still find the
control source while newer agents can branch on `summary.handoff_mode`.
On large local ledgers, full handoff may be slow; prefer the default lightweight
mode for normal loops, or pair full mode with a low `--limit` / higher
`--request-timeout` for deliberate diagnostics.
CLI `--brief` keeps the server/API contract unchanged but emits a compact
copy-only `operator_loop_launch_brief` for live local agents. The brief includes
the adapter preflight command, the current next/verify/receipt commands, compact
execution-chain rows, confirmation/prepared-action guidance for Hermes/OpenClaw,
an explicit `agentops workflow run-task --adapter ... --confirm-run` live command
template, task/run/manifest readback commands, a compact `local_run_path` from
`agentops local readiness` with the service-control preview step, and the same
read-only/token-omission safety proof without printing the full launch packet.
The local run path remains copy-only: it exposes boot/readiness/worker/service/
dispatch/ledger/live-acceptance commands for the operator or agent shell, while
`server_executes_shell=false` stays part of the safety proof.
It emits commands for loop self-check, knowledge search, commander repo-map
localization, plan creation/verification, intake comparison, enforced task
pull, loop verification, plan-evidence binding, evidence reporting, Action
Queue receipt readback, and review queue drain. The RETRIEVE phase includes
`agentops commander repo-map`
and `sources.repo_map` with sanitized paths, symbols, hashes, provenance,
ranking proof, and raw-content/snippet omission flags; the agent-plan draft
uses those paths as initial proposed files when candidates are found. The
payload includes an `evaluation_contract` with minimum exit
criteria and required ledgers, plus an `audit_contract` with tamper-chain,
recording, bounded-runner, and raw-content omission requirements. It also
includes an `execution_chain` that spells out self-check, bounded advance
preview/confirm, verification, plan-evidence binding, receipt readback, and
final loop audit steps with mutating/confirmation markers. Each step also
returns current `step_status`, `blocked_reason` or `ready_reason`,
`next_safe_command`, and `receipt_state` with token omission proof, so agents
can distinguish a read-only copy step from a confirmation-gated or
receipt-gated step. The packet also includes `control_summary`, a copy-only
operator control view that selects the recommended step, next command, verify
command, receipt command, control mode, human/receipt requirements, step counts,
and server-shell omission proof. It does not create plans, run workers, approve
gates, create memories, or mutate ledgers.

```bash
agentops operator advance-loop --loop-id loop_123 --limit 10
agentops operator advance-loop --fast-control --limit 8
agentops operator advance-loop --loop-id loop_123 --limit 10 --confirm-advance
agentops operator advance-loop-policy
agentops operator loop-control --limit 8
agentops operator loop-bootstrap --adapter hermes --limit 8
agentops operator loop-bootstrap --adapter openclaw --limit 8 --run-service-check
agentops operator loop-supervision --adapter hermes --adapter openclaw --limit 8 --work-packet
agentops operator loop-supervision --adapter hermes --adapter openclaw --limit 8 --decision
curl 'http://127.0.0.1:8787/api/operator/loop-supervision?adapter=hermes&adapter=openclaw&limit=8&work_packet=1'
curl 'http://127.0.0.1:8787/api/operator/loop-supervision?adapter=hermes&adapter=openclaw&limit=8&decision=1'
agentops operator loop-driver --adapter hermes --max-steps 3
agentops operator loop-driver --adapter openclaw --max-steps 3 --confirm-loop --auto-service-closure
```

Reads `GET /api/operator/handoff` and selects the first non-passing loop action
whose `agentops ...` command is allowed by the local bounded-runner policy.
`--fast-control` reads `GET /api/operator/loop-control` instead of the full
handoff packet, so local agents can advance one bounded read-only diagnostic or
record step without waiting on the heavier aggregate read models.
Without `--confirm-advance` it is preview-only. With confirmation it executes
exactly one local allowlisted action, runs the paired verify command, and records
an Action Queue receipt as `verified` or `failed`. Confirmed runs also return
`control_readback`: the pre-action handoff control summary plus post-receipt
handoff and loop-self-check control summaries fetched with `refresh_cache=true`,
including cache-bypass proof. The same readback is also written as an append-only
`operator.action_queue_control_readback` audit row attached to the Action Queue
receipt, so `/api/operator/action-receipts` and the Operator Command Center can
show before/after/self-check control proof with the readback tamper hash. This
receipt read model also reports control-readback coverage
(`required`/`attached`/`missing`/coverage/status), and operator health exposes a
`control_readbacks` gate so missing post-advance readbacks become an explicit
attention risk. This
lets agents see that the next recommendation has moved after receipt/evaluation
instead of reading stale handoff state. The
policy scope allows safe local actions such as `knowledge index`, `memory
propose --type loop_record`, and read-only `operator remediate-evidence-gap
--run-id ...` previews after the run-level evidence-report work order is
receipted. It refuses memory
approval/rejection, approval decisions, worker lifecycle, workflow dispatch,
live/confirm flags, remediation `--confirm-create`, close-gap, external uploads,
and other commands that require an explicit human or dedicated confirmation path.
`advance-loop-policy` is read-only and returns the current policy id/version,
allowlisted commands, denied namespaces/actions/flags, and sample decisions so
operators and UI can show exactly why a command can or cannot be auto-advanced.
`operator loop-control` is read-only and returns the lightweight copy-only
selected item, preview command, confirm command, bounded evidence counts, and
policy id for fast local loop steering.
`operator loop-bootstrap` is the local service and loop startup packet for
Hermes/OpenClaw. It reads start-check and loop-supervision, then orders
current-code verification, service-install preview/confirm, service-check,
service-closure record, optional service-control load confirmation, and
`loop-driver --confirm-loop --auto-service-closure`. It is read-only by default;
`--run-service-check` performs only local worker service-check and still does
not mutate ledgers, load services, execute server shell, or run live adapters.
Use `--fast` when Hermes/OpenClaw need the copy-only startup commands before a
large local ledger can finish heavy start-check or loop-supervision reads. The
fast packet is intentionally not a pass: it marks deep verification required,
keeps confirm-loop blocked until current-code and supervision readback pass, and
preserves the same no-ledger/no-live/no-server-shell boundary.
If the selected local MIS is stale or missing a required loop endpoint,
`loop-bootstrap` returns a structured blocked packet with
`error_type=stale_server_or_missing_endpoint`, current-code/restart/retry
commands, token omission proof, and no ledger or service side effects.
If a required local endpoint exceeds the CLI request timeout, the command
returns `error_type=local_mis_endpoint_timeout` with a fast fallback packet and
longer-timeout retry command instead of a Python traceback.
`GET /api/operator/loop-bootstrap` exposes the same read-only startup packet to
the local MIS and AI Employees console. The API never performs service-check or
service-control itself; operators copy the returned CLI commands and run them
locally when the packet says confirmation is required.
`GET /api/operator/loop-bootstrap?fast=1` exposes the same lightweight startup
commands without reading heavy start-check or loop-supervision endpoints.
`operator loop-supervision --work-packet` and
`GET /api/operator/loop-supervision?work_packet=1` expose the compact
`agent_work_packet_bundle_v1` for HTTP-only Hermes/OpenClaw/Codex agents. That
bundle contains adapter-scoped `agent_work_packet_v1` entries, phase commands,
packet hashes, primary next actions, service-closure receipt/readback gates,
and the same no-server-shell/no-live/no-token safety proofs as the full
supervision read model. Machine callers should use this compact bundle instead
of scraping the larger supervision payload.
`operator loop-supervision --decision` and HTTP `decision=1` expose a still
smaller `agent_work_packet_decision_v1` projection for local Hermes/OpenClaw
callers that only need the next consumption decision. The decision layer
classifies each adapter packet as `plan_first`, `service_closure_first`,
`record_research_consumption_first`, `record_first`, `review_first`,
`preview_first`, `confirm_ready`, `safe_read_or_preview`, `blocked`, or `stop`,
then repeats the command, verify command, confirm/receipt requirements, and
server-may-execute=false safety proof.
`operator loop-driver --confirm-loop` consumes that decision before confirmed
bounded advance and before each bounded step. It may continue governance-first
decisions through the local allowlisted `advance-loop` path, but fails closed
for `stop`, `blocked`, missing decisions, server-shell evidence, or live
execution evidence before running any bounded advance.
`operator service-closure --fast --run-service-check --confirm-record` is the
standalone local RECORD command for service-managed loop readback when the
packet asks for it. Unlike `--decision`, it writes receipt/readback evidence to
the local ledger after explicit `--confirm-record`; verify with `operator
action-receipts` and then re-read the compact work packet before copying a
bounded loop-driver command.
`operator handoff` exposes the same `work_order.advance_loop` preview/confirm
commands, and `/workspace/agents` renders copy buttons for those local CLI
commands without letting the browser or server execute shell.
`operator loop-driver` is the local CLI loop wrapper for Hermes/OpenClaw/Codex:
preview mode reads `operator start-check` into an `acceptance_gate`, then reads
the compact launch brief and policy without writing ledgers. It also returns
`agent_loop_packet`, a machine-readable READ/PLAN/RETRIEVE/COMPARE/PREFLIGHT/
EXECUTE/VERIFY/RECORD command packet with acceptance, preflight, confirm-loop,
verify, receipt, audit, and review commands for Hermes/OpenClaw/Codex;
preview mode also reads `agentops worker readiness` into an
`adapter_readiness` gate with the selected adapter's trust/readiness state,
checks, `agentops worker preflight --adapter ...` command, and live-dispatch
blockers. It also returns `record_review_snapshot`, a compact read-only RECORD
view from the review queue with pending approval, memory candidate, returned
item, and next review command counts; item summaries and raw content are
omitted. Confirmed mode runs up to five
`advance-loop --fast-control --confirm-advance` steps, re-reading the
start-check acceptance gate, adapter readiness, and the launch brief before each
step. If `can_confirm_bounded_loop` is false or `server_executes_shell` is not
false, it stops before advance. Otherwise it relies on the existing allowlist,
receipts, and control-readback path, then refreshes the same
`record_review_snapshot` after each step. Confirmed output includes initial and
final `agent_loop_packet` readbacks, so a caller can see which phase and
readback command should run next without reconstructing state from prose. The
readiness, packet, and review gates are read-only evidence, not authorization:
they do not create a new server shell execution surface and still refuse
live/workflow/approval/external-write commands through the bounded-runner
policy.

```bash
agentops operator remediate-evidence-gap --run-id run_123
agentops operator remediate-evidence-gap --run-id run_123 --confirm-create
```

The remediation command previews a Commander-compatible work package by
default. `--confirm-create` writes a deterministic planned task, runtime event,
and audit row; repeated confirmed calls return `already_exists`. The resulting
task can be dispatched with `agentops commander dispatch-package --task-id ...
--adapter mock`. After mock dispatch writes tool/evaluation/artifact/audit and
verified plan-evidence rows, the `execution_evidence` gap remains visible as
legacy source-run debt but reports `remediation_status=verified`,
increments `remediated_evidence_gap_runs`, and drops from blocked severity to a
ready review item. The next action then follows the standard Commander chain:
stable `commander synthesize`, `approval inspect`, and
`commander promote-synthesis --mode both --confirm-promote`. The action-plan
summary reports that chain with `evidence_synthesis_ready_runs`,
`evidence_synthesis_pending_runs`, and `evidence_synthesis_promoted_runs`.

`operator handoff` also emits a stage-level remediation work order for each
non-ready evidence run. `work_order.evidence_report.remediation_chain.items[]`
contains `workflow_steps` for `preview`, `create_task`, `dispatch_package`,
`plan_evidence`, `synthesize`, and `close_gap`, plus `next_workflow_step`.
Only `preview` is marked `auto_advance_allowed=true`; mutating stages are
explicit, receipt-gated, and carry their command plus confirmation boundary so
agents can hand off the chain without silently running create/dispatch/close
steps. Each command-bearing stage also carries an independent `action_id`,
`action_signature`, `receipt_state`, `receipt_record_command`, and
`receipt_verify_record_command`; the handoff `loop_health` gate reports
workflow ready/blocked steps plus required/verified/missing workflow receipts.
Stage rows also expose `blocked_reason`, `ready_reason`, `prerequisite_step`,
`next_safe_command`, `next_safe_command_kind`, and `receipt_next_command` so
Hermes/OpenClaw/Codex can tell whether the next move is read-only, verification,
or explicit mutating work that only gets copied into the local operator path.
`operator loop-self-check` mirrors the same `evidence_remediation_workflow`
gate before bounded advance, and `/workspace/agents` renders the first few
remediation workflow rows without executing commands.
`operator action-plan` also projects the current actionable remediation
workflow stage into the shared Action Queue with source
`evidence_remediation_workflow:<step>`. These rows reuse the handoff remediation
receipt source, expose `workflow_step_id`, `next_safe_command_kind`,
`mutating`, `confirm_required`, and `prerequisite_step` in `evidence`, and
return an `evidence_remediation_workflow` read model with action, mutating,
confirm-required, missing-receipt, and verified-receipt counts. The UI consumes
those backend rows directly, so Hermes/OpenClaw/Codex see one ordered queue
instead of a separate handoff table and a separate action-plan table.
After promotion, action-plan recommends an explicit operator closure:

```bash
agentops operator close-evidence-gap --run-id run_123 --decision accepted_remediation
agentops operator close-evidence-gap --run-id run_123 --decision accepted_remediation --confirm-close
agentops operator close-evidence-gap --run-id run_123 --decision waived --note "legacy imported run" --confirm-close
agentops operator close-evidence-gap --run-id run_123 --decision reopen --confirm-close
```

The close command is preview-only by default. Confirmed calls write
runtime/audit evidence; `accepted_remediation` is allowed only after the
remediation synthesis has been promoted, while `waived` requires a note. The
operator action-plan can surface the latest gap decision in the action evidence
without storing raw notes.
Failed-benchmark remediation is a first-class
`remediation_loop` source: once a failed evaluation case becomes a Commander
work package, the action plan reports remediation package counts,
ready-for-review counts, pending synthesis reviews, promoted delivery/memory
counts, and read-only next commands for `/workspace/agents`.

### `agentops eval propose-case`

Previews or creates a review-gated evaluation case candidate from existing
ledger evidence. Omit `--confirm-create` for a read-only preview.

```bash
agentops eval propose-case --evaluation-id eval_123
agentops eval propose-case --run-id run_123 --case-type regression --confirm-create
agentops eval cases --status candidate --limit 20
agentops eval approve-case --case-id evalcase_123
agentops eval run-cases --case-id evalcase_123
agentops eval run-cases --case-id evalcase_123 --confirm-run
agentops eval run-cases --task-id tsk_123 --confirm-run
agentops eval case-runs --case-id evalcase_123 --limit 5
agentops eval case-runs --task-id tsk_123 --limit 10
agentops eval remediate-case-run --case-run-id ecr_123
agentops eval remediate-case-run --case-run-id ecr_123 --confirm-create
agentops commander packages --project-id proj_evalcase_remediation_x --limit 5
agentops commander dispatch-package --task-id tsk_evalcase_fix_ecr_123 --adapter mock
agentops commander synthesize --project-id proj_evalcase_remediation_x --status ready_for_review --confirm-create
agentops approval inspect --approval-id ap_cmd_synthesis_x
agentops approval approve --approval-id ap_cmd_synthesis_x
agentops commander promote-synthesis --artifact-id art_cmd_synthesis_x --approval-id ap_cmd_synthesis_x --mode both --confirm-promote
agentops operator action-plan --limit 20
agentops workflow delivery-board --limit 12
```

Maps to `POST /api/evaluation-cases/propose`, `GET /api/evaluation-cases`,
`GET /api/evaluation-case-runs`,
`POST /api/evaluation-cases/:case_id/approve|reject`, and
`POST /api/evaluation-cases/run`. Failed case-run remediation maps to
`POST /api/evaluation-case-runs/:case_run_id/remediation-task`.

Writes only after explicit confirmation or review:

- `evaluation_case_candidates`
- `evaluation_case_runs`
- `runs`
- `evaluations`
- `artifacts`
- `runtime_events`
- `audit_logs`

Raw prompts, raw responses, credentials, and full private transcripts remain
omitted; candidates carry bounded summaries, source refs, confidence, rubric,
and review status. Case execution is local-only by default: `run-cases`
previews without mutation unless `--confirm-run` is provided, and the v1 runner
uses `rule` or `llm_mock` checks rather than calling Hermes/OpenClaw live.
`case-runs` is read-only evidence readback for the local benchmark ledger and
returns bounded summaries plus run/evaluation/artifact ids.
`remediate-case-run` converts a failed benchmark into a planned task candidate:
preview returns a deterministic Commander-compatible task draft, and
`--confirm-create` writes a normal task plus runtime/audit evidence. Existing
deterministic remediation task ids are returned as `already_exists` instead of
silently updating work.
After dispatch and synthesis, the remediation project uses the same Commander
approval/promotion lifecycle as ordinary work packages. The operator action
plan reports pending remediation synthesis reviews, approved-but-not-promoted
synthesis reports, promoted remediation memory candidates, and promoted
remediation delivery artifacts without running workers, approving gates, or
mutating the ledger.

Customer worker tasks automatically run task-bound approved evaluation cases
after a worker completes, then include `evaluation_case_runs` in the returned
evidence summary. This makes the worker loop act like a small local CI gate:
normal task execution still happens through Agent Gateway, while reusable
regression/golden cases are executed locally and safely as ledger evidence.

### `POST /api/agent-gateway/approvals/request`

Creates a human approval request.

Writes:

- `approvals`
- `audit_logs`

### `POST /api/agent-gateway/memories/propose`

Creates a candidate memory, never an auto-approved memory.

Writes:

- `memories`
- `audit_logs`

### `POST /api/agent-gateway/evaluations/submit`

Submits quality-gate output.

Writes:

- `evaluations`
- `audit_logs`

### `POST /api/agent-gateway/audit`

Emits a direct audit event for state transitions that do not fit another endpoint.
Known ledger entities derive their workspace/task/run authority from the stored
object. Custom entity types must include an authorized `task_id` or `run_id`;
the Gateway rejects unbound or cross-workspace audit writes.

Writes:

- `audit_logs`

## Required Auth Model

### v1.4 Local Auth / v1.5 Scoped Tokens

v1.4 used a simple local token or API key. v1.5.2 adds per-agent scoped bearer tokens for local and remote workers.

Required fields:

- `workspace_id`
- `agent_id`
- API key or local token
- permission scope

Supported auth headers:

```http
Authorization: Bearer <local_token>
X-AgentOps-Agent-Id: agt_kb_researcher
X-AgentOps-Workspace-Id: local_demo
```

Scopes should be explicit:

```text
agents:write
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
approvals:request
memories:propose
evaluations:submit
audit:write
```

Current implementation:

- `AGENTOPS_API_KEY` remains a global local development key.
- `POST /api/agent-gateway/enrollment/create` issues an agent-bound bearer token.
- MIS stores only a token hash.
- The token can act only as its bound `agent_id`.
- The token can act only in its bound `workspace_id`.
- Token-auth requests cannot override `agent_id` or `workspace_id` through request body, query string, or headers.
- Token-auth task creation is bound to the token's own `agent_id` and `workspace_id`; a remote agent cannot create work as another agent or another workspace.
- Gateway endpoints check required scopes.
- Valid scoped tokens that lack an endpoint scope return `403 forbidden`, not `401 unauthorized`.
- Enrollment launch packets now recommend minting a short-lived session before worker execution:
  - `agentops session create --ttl-sec 900 --save-session`
  - `agentops-worker ... --use-session --session-ttl-sec 900`
- `agentops-worker --use-session` now refreshes short-lived sessions during loop mode before expiry, using the parent enrollment token only in process memory and writing only session metadata to state/output. `scripts/agent_worker.py` remains a repo-local compatibility wrapper.
- Task claim is guarded for multi-worker use:
  - public pool tasks may be visible to multiple agents before claim,
  - first claim moves the task to `running` and binds `owner_agent_id`,
  - repeat claim by the same agent is idempotent,
  - claim or run start by another agent is rejected with `403` or `409`.
- Task pull, task claim, run start, run heartbeat, tool call, approval, memory, evaluation, and audit write paths enforce the run/task workspace boundary.
- Worker recovery is operator-visible:
  - `GET /api/workers/stuck-tasks` lists stale running worker tasks,
  - `POST /api/workers/tasks/release` returns a stuck task to `planned`,
  - linked running runs are marked `blocked` with `WorkerTaskReleased`.
- `POST /api/agent-gateway/enrollment/revoke` revokes tokens.
- `POST /api/agent-gateway/enrollment/rotate` revokes an active token and returns a one-time replacement token.
- `POST /api/agent-gateway/enrollment/request` creates task/run/approval/request ledger rows without issuing a token.
- `POST /api/agent-gateway/enrollment/issue-approved` issues the token only after the linked approval is approved.
- `POST /api/agent-gateway/session/create` mints a short-lived session token from a valid enrollment token or local API key.
- Session tokens inherit agent/workspace bindings and a subset of parent scopes.
- Session tokens cannot mint replacement sessions.
- Session-token auth rechecks the parent enrollment on every request. If the parent token is revoked, expired, missing, or no longer matches the session binding, the session fails closed.
- `GET /api/agent-gateway/sessions` lists session metadata with safe refs only; it omits `session_hash`, raw token values, raw session ids, and raw parent token ids.
- `POST /api/agent-gateway/session/revoke` revokes one session or all active sessions for an agent.
- `POST /api/agent-gateway/session/revoke-self` lets only the currently authenticated active Session release itself without accepting a caller-selected Session ID.
- Revoking an enrollment token also revokes active child sessions.
- `GET /api/agent-gateway/enrollments` reports heartbeat freshness with safe `token_ref` values and omits raw token ids.
- Revoked tokens report `heartbeat_state=revoked`, not `fresh` or `stale`.

Current endpoint scope map:

| Endpoint | Required scope |
| --- | --- |
| `POST /api/agent-gateway/enrollment/request` | local request path; no token issued |
| `POST /api/agent-gateway/enrollment/issue-approved` | admin/local approval authority |
| `POST /api/agent-gateway/session/create` | valid enrollment token or local API key |
| `GET /api/agent-gateway/sessions` | admin/local authority |
| `POST /api/agent-gateway/session/revoke` | admin/local authority |
| `POST /api/agent-gateway/session/revoke-self` | current active short-lived Session; exact self only |
| `GET /api/agent-gateway/host-workers/status` | Host machine credential only; `tasks:read` contract |
| `GET /api/agent-gateway/host-workers/fleet` | Host machine credential only; `tasks:read` contract |
| `GET /api/agent-gateway/host-workers/adapter-readiness` | Host machine credential only; `tasks:read` contract |
| `GET /api/agent-gateway/host-workers/stuck-tasks` | Host machine credential only; `tasks:read` contract |
| `POST /api/agent-gateway/register` | `agents:write` |
| `POST /api/agent-gateway/heartbeat` | `agents:heartbeat` |
| `POST /api/agent-gateway/tasks` | `tasks:create` |
| `GET /api/agent-gateway/tasks` | `tasks:read` |
| `GET /api/agent-gateway/tasks/pull` | `tasks:read` |
| `GET /api/agent-gateway/tasks/:id` | `tasks:read` |
| `POST /api/agent-gateway/tasks/:id/claim` | `tasks:claim` |
| `GET /api/agent-gateway/runs` | `tasks:read` |
| `GET /api/agent-gateway/runs/:id` | `tasks:read` |
| `GET /api/agent-gateway/runs/:id/graph` | `tasks:read` |
| `POST /api/agent-gateway/runs/start` | `runs:write` |
| `POST /api/agent-gateway/runs/:id/heartbeat` | `runs:write` |
| `POST /api/agent-gateway/runtime-events` | `runtime_events:write` |
| `POST /api/agent-gateway/tool-calls` | `toolcalls:write` |
| `GET /api/agent-gateway/artifacts` | `tasks:read` |
| `POST /api/agent-gateway/artifacts` | `artifacts:write` |
| `GET /api/agent-gateway/knowledge/search` | `knowledge:read` |
| `GET /api/agent-gateway/knowledge/evidence-packet` | `knowledge:read` |
| `GET /api/agent-gateway/knowledge/context-packet` | `knowledge:read` |
| `GET /api/agent-gateway/knowledge/retrieval-evidence-packet` | `knowledge:read` |
| `POST /api/agent-gateway/knowledge/index` | `knowledge:write` |
| `GET /api/agent-gateway/agent-plans` | `agent_plans:read` |
| `GET /api/agent-gateway/agent-plans/:plan_id` | `agent_plans:read` |
| `GET /api/agent-gateway/agent-plans/:plan_id/verify` | `agent_plans:read` |
| `POST /api/agent-gateway/agent-plans` | `agent_plans:write` |
| `GET /api/agent-gateway/plan-evidence-manifests` | `plan_evidence:read` |
| `GET /api/agent-gateway/plan-evidence-manifests/:manifest_id` | `plan_evidence:read` |
| `GET /api/agent-gateway/plan-evidence-manifests/:manifest_id/verify` | `plan_evidence:read` |
| `POST /api/agent-gateway/plan-evidence-manifests` | `plan_evidence:write` |
| `GET /api/agent-gateway/approvals` | `tasks:read` |
| `POST /api/agent-gateway/approvals/request` | `approvals:request` |
| `GET /api/agent-gateway/memories` | `tasks:read` |
| `POST /api/agent-gateway/memories/propose` | `memories:propose` |
| `POST /api/agent-gateway/evaluations/submit` | `evaluations:submit` |

Bound Agent Gateway read paths use exact collaborator membership rather than
substring matching. Task pull/list and task-linked run, artifact, approval and
memory lists compare `agent_id` with parsed collaborator arrays via
`agentops_json_array_contains`, so an agent like `agt_x` cannot read rows whose
only collaborator is `agt_x_extra`.

### `agentops operator intake-checklist`

Shows the read-only pre-intake gates for planned/backlog tasks before any worker
starts execution:

```bash
agentops operator intake-checklist --limit 20
```

The checklist verifies assignment, submitted Agent Plan, verified Agent Plan,
knowledge/spec retrieval, base references, and high-risk approval boundaries.
It also feeds `agentops operator action-plan`, so customer tasks that are not
ready for agent execution become explicit operator actions instead of silently
remaining in the queue.
| `POST /api/agent-gateway/audit` | `audit:write` |

### Future Auth

Future product versions should add:

- Workspace-level RBAC.
- Connector-specific scoped credentials.
- Hosted customer enrollment policy UI.
- mTLS or signed heartbeats for server-side agents.
- Trust registry for external runtimes.

## Data Mapping To Existing Tables

| Gateway object | Existing table | Notes |
| --- | --- | --- |
| Agent identity | `agents` | Register, role, runtime, status, tool scope, budget. |
| Task assignment | `tasks` | Pull, claim, status changes, ownership. |
| Run lifecycle | `runs` | Start, heartbeat, completion, failure, output summary. |
| Tool evidence | `tool_calls` | Tool name, risk, duration, status, redacted metadata. |
| Human gate | `approvals` | Required for high-risk side effects. |
| Memory candidate | `memories` | Candidate only; approval is separate. |
| Quality result | `evaluations` | Gate name, score, pass/fail, notes. |
| Audit event | `audit_logs` | Append-only operational evidence. |

## v1.4 Implementation Plan

### Step 1: Docs Only

Create and review the product usage model and Agent Gateway CLI/API spec. No backend or UI change is required for this step.

### Step 2: Minimal CLI Wrapper

Add a small `agentops` CLI wrapper that can:

- Read `AGENTOPS_BASE_URL`.
- Read `AGENTOPS_API_KEY`.
- Send JSON requests.
- Print JSON responses.
- Avoid writing secrets to logs.

The first implementation can be a Python script under `scripts/` or a small installable package.

Status: implemented as `agentops_mis_cli/agentops.py`, `scripts/agentops`, and the editable Python package entry in `pyproject.toml` / `agentops_mis_cli`.

### Step 3: Backend Endpoints

Add minimal local endpoints under `/api/agent-gateway/*`.

The first backend pass should support:

- Register.
- Heartbeat.
- Task pull.
- Task claim.
- Run start.
- Run heartbeat.
- Tool-call record.
- Approval request.
- Evaluation submit.
- Audit emit.

Status: implemented in the local Python server for v1.4 MVP.

### Step 4: Connect OpenClaw / Hermes Adapters

Add adapters that translate runtime activity into Agent Gateway calls:

- OpenClaw agent or cron jobs call `task pull`, `run start`, `toolcall record`, and `eval submit`.
- Hermes or Agnesfallback local probes call `run start`, `run heartbeat`, and `audit emit`.
- Dify / OpenAI File Search workflows can be represented as external runtimes or connectors, with uploads and writes gated by prepared actions before provider side effects.

### Step 5: Remote Agent Support

Current v1.5.2 minimal support:

- Remote agent gets a scoped token.
- Remote agent registers with workspace and runtime metadata.
- Remote agent heartbeats periodically.
- Remote agent pulls scoped tasks.
- MIS can revoke the agent token.
- Raw files and credentials stay outside the ledger unless explicitly approved and redacted.

Verification helper:

```bash
python3 scripts/remote_agent_token_worker_smoke.py
python3 scripts/workspace_isolation_smoke.py
python3 scripts/enrollment_health_state_smoke.py
python3 scripts/agentops_pip_install_smoke.py
python3 scripts/agentops_doctor_smoke.py
python3 scripts/agentops_worker_status_smoke.py
python3 scripts/agentops_worker_daemon_cli_smoke.py
python3 scripts/agentops_status_smoke.py
python3 scripts/enrollment_launch_steps_smoke.py
python3 scripts/remote_launch_packet_worker_smoke.py
python3 scripts/agent_gateway_scope_matrix_smoke.py
python3 scripts/agent_gateway_special_char_scope_smoke.py
python3 scripts/agent_gateway_session_smoke.py
python3 scripts/enrollment_approval_workflow_smoke.py
python3 scripts/task_claim_conflict_smoke.py
python3 scripts/worker_stuck_recovery_smoke.py
python3 scripts/worker_session_refresh_smoke.py
python3 scripts/worker_adapter_retry_smoke.py
python3 scripts/agentops_worker_package_smoke.py
python3 scripts/agentops_customer_worker_cli_smoke.py
python3 scripts/agentops_task_create_cli_smoke.py
python3 scripts/agent_gateway_task_create_scope_smoke.py
python3 scripts/agentops_cli_inspect_smoke.py
```

This helper creates a scoped token, creates a normal MIS task for that agent, runs `scripts/agent_worker.py --once` with the token, verifies run/tool/eval evidence, and revokes the token by default. It does not print the raw token.
The workspace isolation helper creates workspace A/B tasks and a workspace B run, verifies that the workspace A token cannot pull, claim, start, heartbeat, record tool calls, request approvals, submit evaluations, emit run-scoped audit, or write artifacts against workspace B work, and verifies that normal workspace A execution still succeeds.
The enrollment health helper verifies `never_seen -> fresh -> stale -> revoked` without printing the raw token.
The CLI status helper verifies `agentops status` reports safe token-bound metadata, updates to `fresh` after heartbeat, and rejects revoked tokens without leaking the raw token.
The CLI doctor helper verifies `agentops doctor` works in local no-token mode and scoped env-token mode, checks Gateway/worker status, confirms unsafe shared/production no-token mode exits `2`, and confirms the raw token is omitted from output.
The CLI worker-status helper verifies `agentops worker status` returns the worker fleet/daemon summary without token leakage.
The CLI worker-preflight helper verifies `agentops worker preflight` returns read-only Gateway/adapter readiness JSON with `live_execution_performed=false`.
The CLI worker-daemon helper verifies `agentops worker start/status/logs/stop` can manage a mock daemon without leaking secrets.
The live-confirm-gate helper verifies `agentops worker start --adapter hermes|openclaw` fails closed without `--confirm-run`.
The customer-worker CLI helper verifies `agentops workflow customer-worker-task` creates a real mock worker run with run/tool/evaluation/audit/artifact evidence and keeps Hermes live execution gated by confirmation.
The task-create CLI helper verifies `agentops task create` can create a normal customer/API task, then a worker can pull it and write run/tool/evaluation evidence without leaking token-like values.
The task-create scope helper verifies scoped tokens need `tasks:create`, cannot create tasks as another agent, and cannot cross workspace boundaries.
The CLI inspect helper verifies `agentops task get/list`, `agentops run get/list/graph`, and `agentops artifact list` can retrieve a completed customer worker task's ledger evidence without browser use or token-like leakage.
The launch-steps helper verifies create/rotate responses include safe remote-worker commands, a short-lived session command, `--use-session`, and do not embed the raw token in those commands.
The remote launch-packet helper uses the returned environment shape to run a real worker through `--use-session` and verify run/tool/evaluation ledger evidence.
The scope-matrix helper verifies an observer token can heartbeat/pull/audit but receives `403 forbidden` for claim/run/tool/artifact writes.
The special-character scope helper runs against an isolated SQLite database and verifies scoped access for workspace, agent, and task ids containing spaces, `+`, `%`, quotes, commas, and URL-encoded slashes. It covers URL-decoded task/run/approval path ids, exact collaborator matching, workspace-header spoof rejection, scoped ledger list isolation, scoped review queue visibility, and token omission.
The session helper verifies an enrollment token can mint a narrowed short-lived session, sessions can be listed without hash leakage, one session can be revoked directly, a non-expired session can status/heartbeat/pull tasks, cannot mint another session, a separate one-second session expires closed, and parent enrollment revocation cascades to active child sessions.
The enrollment-approval helper verifies request-before-token behavior: request returns no token, premature issue is rejected, approval unlocks token issue, and the issued token can heartbeat.
The task-claim helper verifies two agents can initially see the same public pool task, the first claim wins, same-agent repeat claim is idempotent, and a second worker cannot claim or start the already claimed task.
The stuck-recovery helper verifies a stale running worker task is listed, released back to `planned`, and the linked running run is blocked with `WorkerTaskReleased`.
The worker package helper installs the Python source package into a temporary venv, verifies `agentops-worker --help`, then runs a one-shot no-task worker loop against a local stub Agent Gateway. It proves the installable worker can register, pull, heartbeat, write state outside the repo, and omit token values.
It also verifies `agentops-worker service-template --manager launchd|systemd` renders restartable service files with only a token placeholder, not a raw token.
It verifies `agentops-worker service-install --manager launchd|systemd` defaults to dry-run and only writes a placeholder service file when explicitly confirmed.
It verifies `agentops-worker service-check --manager launchd|systemd` is read-only, omits raw service content, detects token-like values, and reports the expected relaunch policy (`KeepAlive=true` for launchd, `Restart=always` plus `RestartSec=5` for systemd).
It verifies `agentops-worker service-control --manager launchd|systemd` previews load/unload/restart commands by default, requires explicit confirmation before OS mutation, and fails closed for token-like service files or live adapter templates missing `--confirm-run`.
It verifies `agentops-worker preflight` can perform read-only Gateway/adapter readiness checks without executing tasks, writing ledger rows, or printing token values.
The session-refresh helper verifies a loop worker using `--use-session` refreshes short-lived sessions before expiry and still completes multiple tasks with run/tool/evaluation evidence.
The adapter-retry helper verifies retryable adapter failures can succeed after retry, while non-retryable safety gates such as missing `--confirm-run` stop after one attempt and still write failed run/tool/evaluation evidence.

Remaining future work:

- Hosted customer enrollment policy UI.
- Hosted multi-tenant isolation and full RBAC.
- Reconnection/backoff policy is exposed through
  `agentops worker readiness` -> `worker_connection_policy`.
- mTLS or signed heartbeats for server-side agents.

## Non-Goals For v1.4

- No SaaS multi-tenant billing.
- No public OAuth marketplace.
- No automatic secret ingestion.
- No browser-click automation as the primary agent integration.
- No default real external writes.
- No storage of full private transcripts or raw customer files in the ledger.
