# Remote Worker Operations Runbook

This runbook is the v1.5 operator path for using AgentOps MIS as a control
plane while an AI digital employee runs on the same machine or another customer
machine. The browser UI is for humans. The `agentops` and `agentops-worker`
commands are for agent runtimes.

## Safety Defaults

- Do not paste real tokens into the repo, screenshots, GitHub issues, or demo
  scripts.
- Do not commit `.agentops_runtime`, `agentops_mis.db`, `.env`, generated
  service files with real tokens, worker logs, or runtime caches.
- Hermes and OpenClaw live worker daemons require explicit `--confirm-run`.
- `agentops worker preflight` is read-only. It checks readiness but does not
  pull a task, start a run, call a model, or write ledger rows.
- Worker evidence stores summaries, hashes, statuses, IDs, and audit metadata.
  It must not store full prompts, raw model responses, private transcripts, or
  credentials.

## Local Operator Path

Run these from the AgentOps MIS repo:

```bash
python3 -m pip install .
agentops doctor
agentops status
agentops local readiness
agentops worker status
agentops worker preflight --adapter mock
```

`agentops local readiness` is the single-machine closure check. It is read-only
and summarizes Agent Gateway, worker route readiness, memory/knowledge,
approval, task/run/tool/evaluation/audit/artifact evidence, and local runbook
presence. Use it before a demo, after a worker change, or when the local stack
feels out of shape.

For a safe local daemon:

```bash
agentops worker start --adapter mock --poll-interval 5 --max-tasks 0
agentops worker status
agentops worker logs --adapter mock
agentops worker stop --adapter mock
```

For live local Hermes or OpenClaw recording, first run a read-only preflight:

```bash
agentops worker preflight --adapter hermes
agentops worker preflight --adapter openclaw
```

Then start only with explicit confirmation:

```bash
agentops worker start --adapter hermes --confirm-run --poll-interval 5 --max-tasks 0
agentops worker start --adapter openclaw --confirm-run --poll-interval 5 --max-tasks 0
```

Stop live daemons after the recording or task:

```bash
agentops worker stop --adapter hermes
agentops worker stop --adapter openclaw
```

## Remote Machine Enrollment

On the MIS/admin machine, create or request enrollment:

Preview scope risk before issuing any token:

```bash
./scripts/agentops enrollment policy-preview \
  --runtime mock \
  --scopes agents:heartbeat,tasks:read,audit:write
```

For Hermes/OpenClaw or worker write scopes, prefer the approval-gated request
path when the preview returns `approval_recommended=true`.

The browser `/workspace/agents` Worker preset and the backend default worker
enrollment scopes include `agent_plans:read/write`, `plan_evidence:read/write`,
`knowledge:read/write`, `runtime_events:write`, `artifacts:write`, and
`memories:propose` because the installable worker must retrieve task-aware
knowledge evidence, write runtime events, record artifacts, propose memory, and
verify the plan-evidence manifest. `knowledge:write` is intentionally treated as
privileged because it lets an authorized worker refresh the local knowledge
index; remote Hermes/OpenClaw workers should use the approval-gated enrollment
path before receiving that scope set.

```bash
./scripts/agentops enrollment create \
  --agent-id agt_remote_builder \
  --name "Remote Builder" \
  --runtime mock
```

For a human approval flow:

```bash
./scripts/agentops enrollment request \
  --agent-id agt_remote_builder \
  --name "Remote Builder" \
  --runtime mock
```

After approval:

```bash
./scripts/agentops enrollment issue-approved --request-id <request_id>
```

The token is shown once. Send it through a secure channel to the remote machine.
Do not write it into source control.

## Remote Machine Setup

On the remote machine:

```bash
python3 -m pip install .
export AGENTOPS_BASE_URL="http://<mis-host>:8787"
export AGENTOPS_WORKSPACE_ID="local-demo"
export AGENTOPS_AGENT_ID="agt_remote_builder"
export AGENTOPS_API_KEY="<paste one-time token here>"

agentops doctor
agentops status
agentops operator start-check --adapter mock --limit 8
agentops agent heartbeat --status idle --summary "remote worker ready"
agentops worker preflight --adapter mock
agentops-worker --once --adapter mock --use-session --session-ttl-sec 900
```

Enrollment responses include this command as `next_steps.start_check` plus
`next_steps.method_gate_contract`. Run it before worker preflight so the remote
worker sees the same Method Block gates as the local operator console:
`read_start_check`, `plan_agent_plan`, `retrieve_knowledge`,
`compare_base_reference`, `preflight_adapter`, `execute_bounded_worker`,
`verify_ledger`, and `record_memory_candidate`. The packet is copy-only and
omits the raw enrollment token.

For a long-running remote loop:

```bash
agentops-worker \
  --adapter mock \
  --use-session \
  --session-ttl-sec 900 \
  --poll-interval 5 \
  --max-tasks 0 \
  --continue-on-error \
  --write-state
```

Long-running services use MIS heartbeat/state and omit per-poll JSONL stdout by
default so idle workers cannot grow an unbounded service log. Add
`--jsonl-log` only for a bounded foreground diagnostic session whose output is
actively collected or rotated.

If the remote machine will execute Hermes or OpenClaw, run preflight first and
then add `--confirm-run` only after the operator confirms that the runtime is
intended to execute live tasks.

## Service Templates

Generate a restartable service template with placeholders only:

```bash
agentops-worker service-template \
  --manager launchd \
  --adapter mock \
  --agent-id agt_remote_builder \
  > ~/Library/LaunchAgents/local.agentops.worker.agt_remote_builder.plist
```

```bash
agentops-worker service-template \
  --manager systemd \
  --adapter mock \
  --agent-id agt_remote_builder \
  > ~/.config/systemd/user/agentops-worker-agt_remote_builder.service
```

Before loading either service, replace `<paste one-time token here>` locally on
the worker machine. Do not commit the generated service file if it contains a
real token.

For a safer product-style install path, first preview the service file target:

```bash
agentops-worker service-install \
  --manager launchd \
  --adapter mock \
  --agent-id agt_remote_builder
```

Then explicitly confirm the file write after reviewing the plan:

```bash
agentops-worker service-install \
  --manager launchd \
  --adapter mock \
  --agent-id agt_remote_builder \
  --confirm-install
```

`service-install` writes only the same placeholder-based template with `0600`
permissions. It does not write a real token, load launchd/systemd, restart a
service, or execute the worker. If a target service file already exists, pass
`--overwrite` only after reviewing the existing file locally.

### Private Host same-Mac Worker service

For a Worker on the same Mac as an installed Private Host, do not paste the
Host machine token into a plist. First create the origin-bound, mode `0600`
local CLI config through the confirmation-gated Host command:

```bash
agentops host configure-cli --confirm
```

Then preview and install a Worker service that references only that file path:

```bash
agentops worker service-install \
  --manager launchd \
  --adapter hermes \
  --agent-id agt_hermes_local_service \
  --credential-source local_config \
  --confirm-run

agentops worker service-install \
  --manager launchd \
  --adapter hermes \
  --agent-id agt_hermes_local_service \
  --credential-source local_config \
  --confirm-run \
  --confirm-install
```

The plist contains `AGENTOPS_WORKER_CREDENTIAL_SOURCE=local_config` and the
absolute `AGENTOPS_CONFIG` path, but no API key. At process start the Worker
opens the config without following symlinks, requires a regular file owned by
the current user with no group/other permissions, and verifies exact Host
origin and workspace binding. It then keeps the parent credential in memory
only long enough to mint a short-lived Worker Session. Missing, unsafe or
mismatched config fails before any Gateway request or Runtime execution.

Repeat with `--adapter openclaw` and a distinct Agent ID for OpenClaw. Both
live adapters still require `--confirm-run` in the installed definition and
`service-control --confirm-control` before launchd is mutated. The Private Host
service remains separate and fixed to `host start --foreground --no-workers`.
This avoids duplicate ownership while allowing launchd to restore Host and
explicitly approved Worker loops independently after login.

Run a read-only service check before loading or troubleshooting a worker
service:

```bash
agentops-worker service-check \
  --manager launchd \
  --adapter mock \
  --agent-id agt_remote_builder
```

```bash
agentops worker service-check \
  --manager systemd \
  --adapter mock \
  --agent-id agt_remote_builder \
  --service-path ~/.config/systemd/user/agentops-worker-agt_remote_builder.service
```

The check inspects the service file and OS service status only. It does not
install, load, unload, restart, or execute the worker. It omits raw service file
content and fails closed if token-like values such as enrollment/session/API
tokens are detected in a generated file. It also reports whether the installed
template exposes the expected OS relaunch policy: launchd `KeepAlive=true` or
systemd `Restart=always` with `RestartSec=5`.
For `local_config`, it additionally proves that the config reference and
`--use-session` are present while `AGENTOPS_API_KEY` is absent; it never prints
the config contents.

Preview OS service control before mutating launchd/systemd:

```bash
agentops-worker service-control \
  --manager launchd \
  --action load \
  --adapter mock \
  --agent-id agt_remote_builder
```

```bash
agentops worker service-control \
  --manager systemd \
  --action restart \
  --adapter mock \
  --agent-id agt_remote_builder \
  --service-path ~/.config/systemd/user/agentops-worker-agt_remote_builder.service
```

`service-control` is preview-only by default. It returns the exact
`launchctl`/`systemctl` commands and service-check evidence without executing
them. To mutate OS service state on the agent machine, re-run with
`--confirm-control`. Load/restart fails closed if the service file contains
token-like values or if a Hermes/OpenClaw service template lacks
`--confirm-run`.

## Operations Loop

1. Human creates or assigns a task in AgentOps MIS.
2. Worker pulls planned tasks through Agent Gateway.
3. Worker claims the task, starts a run, executes the adapter, and writes
   tool-call/evaluation/audit evidence.
4. Human reviews Runs, Tool Calls, Evaluations, Audit, and Approvals.
5. If a worker dies mid-task, run:

```bash
agentops worker stuck
agentops worker release --task-id <task_id> --reason "reviewed stale worker"
```

For routine demo/customer cleanup, use fleet hygiene first. It is read-only by
default and requires an explicit confirmation before it releases stale tasks or
revokes never-seen or heartbeat-stale enrollments:

```bash
agentops worker hygiene
agentops worker hygiene --apply --confirm-cleanup
```

Machine-facing task creation can come from a local script, another server, or an
external agent process. Use the CLI when the caller should not operate the
browser UI:

```bash
agentops task create \
  --title "Build a knowledge-base Q&A bot" \
  --description "Clean source docs, create a KB, run test questions, and submit a delivery report." \
  --owner-agent-id agt_remote_builder \
  --priority high \
  --risk medium \
  --acceptance "Worker must write run, tool call, evaluation and audit evidence."
```

Then the same remote worker can consume it:

```bash
agentops-worker --once --adapter mock --agent-id agt_remote_builder
```

For a one-command local/customer execution, use:

```bash
agentops workflow run-task \
  --adapter mock \
  --worker-agent-id agt_remote_builder \
  --title "Build a knowledge-base Q&A bot" \
  --description "Clean source docs, create a KB, run test questions, and submit a delivery report."
```

This creates the task through Agent Gateway, executes one worker iteration, and
returns `task_id`, `run_id`, status, evidence counts, and a `readback` block
showing final task/run evidence came from scoped Agent Gateway read endpoints.
Hermes/OpenClaw still require explicit `--confirm-run`.

For predefined customer delivery workflows, external agents and operators can
avoid browser UI entirely:

```bash
agentops workflow templates
agentops workflow run-template --template-id tpl_customer_kb_qa_bot
```

This maps to the customer task template API and returns project, task, run,
artifact, approval, and report URL evidence while preserving the same safe
summary/hash storage policy.

To make a template use a real local runtime adapter, pass `--adapter` and
explicit confirmation. This is the agent/operator path; the browser remains for
observation, review, and approval:

```bash
agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter openclaw \
  --confirm-run \
  --request-timeout 420
```

Hermes/OpenClaw template runs can take several minutes. Use
`AGENTOPS_REQUEST_TIMEOUT` or `--request-timeout` so the CLI waits for the
worker to write run/tool/evaluation/audit/artifact evidence.

For remote agents and customer-facing automation, prefer the async job shape for
long live runs:

```bash
agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter hermes \
  --confirm-run \
  --async-job

agentops workflow jobs --status queued,running,completed --limit 25
agentops workflow job-status --job-id wfjob_... --wait --timeout 420
```

The job status response returns the final `run_id`, `task_id`, `artifact_id`,
evidence counts, and `token_omitted:true` when the worker completes. The queue
list is read-only and returns status/type summaries plus stuck-job recovery
commands.

For scoped remote tokens, `agentops task create` maps to
`POST /api/agent-gateway/tasks` and requires `tasks:create`. The Gateway binds
the created task to the token's own `agent_id` and `workspace_id`; attempts to
assign work as another agent or another workspace are rejected with `403`.

## Customer Task API Path

For product dogfooding or customer-facing demos, use the workflow endpoint
instead of manually creating worker tasks:

```bash
curl -fsS -X POST http://127.0.0.1:8787/api/workflows/customer-worker-task \
  -H "Content-Type: application/json" \
  -d '{
    "adapter": "mock",
    "title": "Improve the AgentOps MIS customer workspace",
    "description": "Use the worker loop to produce product recommendations.",
    "acceptance_criteria": "Write run, tool, evaluation, audit and artifact evidence."
  }' | jq .
```

For local live Hermes/OpenClaw dogfood:

```bash
python3 scripts/customer_worker_live_dogfood.py --adapter hermes
python3 scripts/customer_worker_live_dogfood.py --adapter openclaw
```

This path creates a normal MIS task, executes through the worker adapter, and
returns the `run_id`, `artifact_id`, and evidence counts. Hermes/OpenClaw live
execution still requires explicit confirmation inside the workflow call.

After the worker returns, use the customer delivery board to inspect the
customer-facing result without mutating the ledger:

```bash
agentops review queue --limit 12
agentops workflow delivery-board --limit 10
agentops approval list --decision pending --limit 10
agentops approval inspect --approval-id ap_...
agentops approval approve --approval-id ap_...
agentops memory list --status candidate --limit 10
agentops memory approve --memory-id mem_...
curl -fsS http://127.0.0.1:8787/api/workflows/customer-delivery-board?limit=10 | jq .
curl -fsS http://127.0.0.1:8787/api/review/queue?limit=12 | jq .
```

The board links delivery artifacts to task/run evidence, approvals,
evaluations, audit counts, and the next operator action. It is read-only and
does not start live runtime work. Approval decisions can be made in the browser
or through `agentops approval approve/reject`; approved gates clear linked run
approval state, while rejected gates block linked task/run evidence. Memory
candidates can likewise be reviewed in the browser or with
`agentops memory approve/reject`, so local and remote agents can propose
knowledge while humans keep final control.

Use `agentops review queue` for remote/CLI agents; it calls the scoped
`GET /api/agent-gateway/review/queue` path with `tasks:read`. The
`GET /api/review/queue` curl above is the local browser/UI read path for a
single-machine demo.
`agentops approval list` and `agentops memory list` also use scoped Agent
Gateway readback. Remote agents can inspect visible review work, but
approve/reject remains a human/operator action and should not be embedded in a
worker loop.

## Revocation And Rotation

List enrollments and sessions:

```bash
agentops enrollment list
agentops session list
agentops worker readiness
agentops worker status
```

`agentops worker readiness` is the route-selection check. It returns the safe
readiness for `mock`, `hermes`, and `openclaw`, including runtime connector
trust status and a recommended adapter. It never executes live runtime work.
Each adapter also includes a `remediation` block with copy-only operator
commands for preflight, runtime doctor, worker start, live task template, and
ledger verification where relevant. MIS does not execute those commands from
the server; live Hermes/OpenClaw steps still require `--confirm-run` and any
prepared-action approval required by the task.

The same response includes `worker_connection_policy` for remote worker loops:
short-lived session defaults, refresh margin, idle/error backoff caps, adapter
retry semantics, daemon `continue_on_error` / `max_errors`, state/log fields,
and copyable verification commands. Remote agents should read this block before
starting a long-running loop rather than relying on stale local notes.
Confirmed customer-worker dispatch uses the same readiness signal: when a live
Hermes/OpenClaw adapter is unavailable or blocked, MIS returns
`reason: adapter_not_ready` for adapter availability failures or
`runtime_connector_trust_blocked` for trust-policy blocks, writes blocked task
plus audit evidence, and does not execute the runtime. Async customer-worker
submit uses the same gate and writes a failed workflow job instead of queueing
work that cannot run. Template async jobs with a live worker adapter use the
same rule.

`agentops worker status` is the operator's fleet summary. In addition to local
daemon state, it summarizes remote worker enrollments, heartbeat states
(`never_seen`, `fresh`, `stale`), active short-lived sessions, and stuck tasks.
Use `agentops worker fleet` when the operator needs a normalized lane table
across local daemons, remote workers, and registered worker agents. Both commands
omit raw token/session identifiers and return only safe refs for machine
diagnostics.

The same response includes `fleet_health`, which is the machine-facing health
gate for agent operators and scripts:

- `overall`: `ready`, `attention`, or `blocked`
- `gates`: stuck worker tasks, stuck workflow jobs, execution capacity, remote
  heartbeats, session hygiene, and local daemon visibility
- `recommended_actions`: concrete next CLI commands, for example
  `agentops worker stuck`, `agentops workflow stuck-jobs`,
  `agentops worker preflight --adapter mock`, or `agentops enrollment list`

`agentops worker hygiene` wraps the most common fleet recovery actions in a safe
operator flow. The `GET`/default CLI path only reports stuck worker tasks,
active enrollments that never heartbeated after the age threshold, and active
enrollments whose last heartbeat is older than the same threshold. The confirmed
apply path releases those tasks back to `planned`, blocks any linked running
runs, revokes stale enrollment tokens, cascades active child sessions, and writes
runtime/audit evidence. It never executes live Hermes/OpenClaw work.

Use this before asking a remote OpenClaw/Hermes/Dify-style worker to execute
work. The browser UI should confirm what happened; the worker should still pull,
claim, run, and write evidence through CLI/API.

Before using the same server beyond a local demo machine, run:

```bash
agentops security production-readiness
```

Default `local_dev_no_token` mode is intentionally allowed for classroom/demo
recording and local self-use, but it is not production-ready. Shared or hosted
deployment must configure authenticated Agent Gateway access and an admin key
for enrollment management. The check is read-only: it does not start workers,
create tokens, call Hermes/OpenClaw, or write ledger rows.
`agentops doctor` is the CLI deployment gate for workers and operators: unsafe
shared/production targets without a Gateway token return exit code `2`, while
still printing redacted JSON diagnostics.

Server startup also enforces this boundary. Binding to `0.0.0.0`, `::`, or any
non-loopback address requires explicit non-loopback opt-in plus Gateway and
admin keys; production/shared mode requires the same keys even on loopback. An
unsafe startup exits before the HTTP server binds.

Revoke one session:

```bash
agentops session revoke --session-id <session_id>
```

Revoke or rotate an enrollment token:

```bash
agentops enrollment revoke --agent-id agt_remote_builder
agentops enrollment rotate --agent-id agt_remote_builder
```

Revoking an enrollment also invalidates active child sessions.
Public revoke output returns counts plus safe token/session refs only; raw
enrollment token ids, session ids, token hashes, and token values stay out of
CLI/API output.
Gateway status readback uses the same boundary: token/session-authenticated
`agentops status` and `GET /api/agent-gateway/status` return safe
`token_ref`, `session_ref`, and `parent_token_ref` values plus omission flags,
never raw enrollment token ids or raw short-lived session ids.
Session inventory follows that readback boundary too: `agentops session list`
and `GET /api/agent-gateway/sessions` expose safe `session_ref` and
`parent_token_ref` values only. Keep the one-time creation `session_id` locally
if a specific-session revoke is required, or use `agentops session revoke
--agent-id <agent_id>` for bulk active-session cleanup.
Enrollment inventory follows the same pattern: `agentops enrollment list` and
`GET /api/agent-gateway/enrollments` expose safe `token_ref` values only. Keep
one-time creation/rotation token IDs locally if exact-token rotation is needed,
or use `agentops enrollment rotate --agent-id <agent_id>` and
`agentops enrollment revoke --agent-id <agent_id>` for managed cleanup.

For a local supervised worker daemon, restart is a first-class operator action:

```bash
agentops worker restart --adapter mock
agentops worker restart --adapter openclaw --confirm-run
```

Hermes/OpenClaw restart fails closed without `--confirm-run`, so a recovery
click cannot silently stop/start a live adapter.

## Acceptance Checks

Use these lightweight checks before a demo or customer handoff:

```bash
python3 -m py_compile server.py agentops_mis_cli/*.py agentops_mis_core/*.py agentops_mis_runtime/*.py scripts/*.py
python3 scripts/agentops_worker_preflight_smoke.py
python3 scripts/worker_adapter_readiness_smoke.py
python3 scripts/customer_worker_adapter_not_ready_smoke.py
python3 scripts/customer_worker_async_adapter_not_ready_smoke.py
python3 scripts/customer_delivery_board_smoke.py
python3 scripts/agentops_approval_cli_smoke.py
python3 scripts/agentops_memory_cli_smoke.py
python3 scripts/template_worker_async_adapter_not_ready_smoke.py
python3 scripts/worker_live_confirm_gate_smoke.py
python3 scripts/remote_launch_packet_worker_smoke.py
python3 scripts/agent_gateway_task_create_scope_smoke.py
python3 scripts/agentops_workflow_run_task_smoke.py
python3 scripts/worker_remote_fleet_status_smoke.py
python3 scripts/worker_fleet_hygiene_smoke.py
python3 scripts/agentops_worker_service_control_smoke.py
python3 scripts/enrollment_policy_preview_smoke.py
python3 scripts/agentops_worker_restart_smoke.py
python3 scripts/agentops_local_backup_smoke.py
python3 scripts/demo_acceptance.py
git diff --check
```

The expected proof is:

- `agentops worker preflight` returns JSON and `live_execution_performed=false`.
- `agentops worker readiness` returns all adapter routes, includes
  `summary.recommended_adapter` plus `worker_connection_policy`, and still reports
  `live_execution_performed=false`.
- Confirmed customer worker dispatch returns `adapter_not_ready` before live
  execution when a selected Hermes/OpenClaw adapter is unavailable.
- Async confirmed customer worker submit also rejects before queueing and records
  a failed workflow job when the selected live route cannot run.
- Async template worker submit follows the same live-route gate.
- `agentops worker service-install` defaults to dry-run and only writes a
  placeholder template when `--confirm-install` is present.
- `agentops worker service-check` returns JSON, omits raw service content,
  verifies the launchd/systemd relaunch policy, and detects token-like values
  without printing them.
- `agentops worker service-control` is preview-only by default, refuses unsafe
  token-like service files, and refuses Hermes/OpenClaw load/restart when the
  service template lacks `--confirm-run`.
- Hermes/OpenClaw daemon starts without `--confirm-run` fail closed.
- Remote launch packet commands can create ledger evidence through Agent Gateway.
- `agentops worker status` reports remote worker heartbeat/session health without
  leaking token/session identifiers and includes `fleet_health.gates` plus
  `fleet_health.recommended_actions`.
- `agentops worker fleet` reports normalized fleet lanes with health,
  heartbeat/session state, safe refs, and next actions, without executing work.
- `agentops worker hygiene` is read-only by default, requires
  `--apply --confirm-cleanup` for cleanup, and records recovery evidence.
- `agentops worker restart` can restart a local supervised daemon and still
  fails closed for Hermes/OpenClaw unless `--confirm-run` is present.
- `agentops enrollment policy-preview` is read-only, classifies observer /
  worker / privileged / invalid scope sets, recommends direct create vs
  approval-gated request, and omits token/session/secret-like strings.
- `scripts/agentops_local_backup.py` can create, verify, and explicitly restore
  a local SQLite backup without printing rows or token material.
- `agentops security production-readiness` reports the local-dev vs production
  security boundary and marks no-token local mode as non-production without
  breaking safe local demos.
- Scoped task creation requires `tasks:create` and rejects agent/workspace impersonation.
- `agentops workflow run-task` creates a task, executes one worker iteration, and returns evidence.
- Demo acceptance remains safe and reproducible.
