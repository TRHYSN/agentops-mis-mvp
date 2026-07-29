# Private Host Worker Intake Heartbeat Acceptance

## Real Finding

After the real Mini Host upgraded to
`v1.6.0-private-host-preview.37`, the Hermes and OpenClaw Worker LaunchAgents
both loaded with new processes and continued to write fresh, bounded
`task.pull` Runtime Events. The queue contained an assigned task that failed
the Intake Gate, so every poll returned `intake_blocked`.

The Fleet projection nevertheless reported both service Workers as stale. The
last `agent.heartbeat` events predated the maintenance window even though the
processes and authenticated task pulls were current. No Worker log, task body,
raw prompt/response, credential or private origin was read or retained for
this finding.

## Root Cause

`process_one_task` returned early when Intake blocked every pulled task. That
branch ran before the normal no-task heartbeat. Auto-plan success, existing
plan verification, high-risk auto-plan refusal and disabled auto-plan could
therefore all keep a healthy long-running Worker invisible to the Fleet health
projection.

## Corrective Contract

Before any Intake-blocked return, the Worker now attempts an authenticated idle
heartbeat through the same Agent Gateway boundary used by normal idle and
post-run paths.

- The summary is static and contains no task title, description, blocked gate,
  source text or count supplied by the server.
- Unchanged heartbeat requests are limited to once every 60 seconds in the
  Worker process. A status transition or completed run may force an immediate
  update.
- The server always refreshes authenticated token and Agent liveness, but adds
  heartbeat Runtime Event and Audit evidence only on a status transition or
  once every 15 minutes. This separates current Fleet health from historical
  ledger sampling.
- Repeated `task.pull` calls remain live reads, but an unchanged eligible and
  Intake-blocked queue shape adds Runtime/Audit evidence only once every 15
  minutes. A queue-state hash change records immediately; task text and task
  identifiers are not stored in the observation state.
- A rejected or unavailable blocked-path heartbeat is not treated as healthy:
  the failure is returned to the Worker loop, recorded as a Worker error and
  follows the existing bounded error backoff. The error-report heartbeat
  remains best-effort so reporting failure cannot recurse indefinitely.
- Worker state schema v2 adds `last_iteration_at`; the legacy
  `last_heartbeat_at` key remains for shape compatibility but no longer
  relabels every successful loop iteration as a remote heartbeat.
- No run starts, Runtime adapter executes, approval changes or task mutation is
  introduced.
- Fleet liveness is bound to the selected full-scope execution Session. The
  current workspace-scoped observation must carry that exact internal Session
  ID; a missing or mismatched Session observation fails closed as `never_seen`.
  No enrollment or unscoped Runtime Event fallback is used for service-Worker
  liveness.
- Human and Host Fleet reads are workspace-scoped. A heartbeat-only Session
  cannot keep an execution Session fresh, and the same Agent ID in another
  workspace cannot lend liveness or ledger rows to the current workspace.
- Human Fleet hygiene preview/apply, direct task release, Local/Demo Readiness,
  Commander Project Board/Inbox, Review Queue, Customer Delivery Board,
  Operator Action Plan/Command Center/Health and readiness knowledge search use
  the Human Session workspace as authority. Another workspace's task, run,
  enrollment, approval, memory, artifact or aggregate evidence is neither
  returned nor mutated.
- The same Human Session authority covers core Agent/task/run/tool-call/
  approval/memory/Evaluation/artifact lists and details, run graphs and exports,
  Dashboard metrics, knowledge results, and bounded Operator handoff,
  start-check, loop-audit and loop-self-check aggregates. Cross-workspace object
  IDs fail closed as `404`; caller-supplied workspace headers and write bodies
  cannot override the Session workspace. Run graph parent/child/delegation
  traversal is scoped at the query itself, and artifact/approval rows whose
  task and run links disagree on workspace fail closed instead of trusting the
  first non-empty link. A caller cannot reuse another workspace's `task_id` to
  move or overwrite that Task; Agent Gateway Artifact and Approval IDs are
  immutable across Runs and return `409` on a conflicting reuse.
- Human Commander/Operator/workflow/Worker mutations receive a server-bound
  workspace body and header. Commander task dispatch/coding operations,
  synthesis promotion and execution-evidence remediation reject foreign task,
  artifact or run IDs before any ledger or local workspace side effect.
- This is a Private Host workspace boundary, not a claim of complete hosted
  multi-tenancy. The Agent registry remains Host-global; Human-created Agents
  receive an explicit `workspace_agent_memberships` row, while task owner,
  collaborator, run and Agent Gateway enrollment/Session references provide the
  remaining workspace projection. Audit reads authorize exact
  `(entity_type, entity_id)` pairs instead of a type-blind ID union, and can omit
  historical rows that have no authoritative workspace link. A future hosted
  schema still needs immutable first-class `workspace_id` on Audit plus full
  membership lifecycle and RBAC enforcement.
- Only an active Session whose scopes match the real local-config Worker
  closed-loop policy is projected as service execution capacity. This includes
  registration, heartbeat, plan/evidence, knowledge, task claim, run,
  Runtime Event, tool, artifact, memory, Evaluation and Audit permissions. A
  read-only or partial Session remains visible as enrollment/session activity
  but cannot claim a runnable Worker lane, and generic Session use is not
  treated as heartbeat.
- Heartbeat sampling state is also keyed by `(workspace_id, agent_id)`, so the
  same Agent ID in two workspaces receives independent bounded Runtime/Audit
  evidence. This 15-minute evidence cadence is not the Fleet freshness
  authority.
- Mixed-offset Session timestamps are normalized to UTC. When multiple active
  full-scope Sessions exist, Fleet first selects the newest fresh execution-ready
  (`idle`/`running`) Session, then a fresh non-ready Session, then a stale
  observed Session, and finally an unobserved Session. A newly minted but
  unobserved Session cannot shadow a healthy Worker; a newer
  `paused`/`error`/`disabled` replica cannot shadow healthy capacity;
  mixed healthy/non-ready replicas retain one deduplicated Worker capacity while
  surfacing degraded Session counts and Fleet `attention`.
- Global `agents.status` is descriptive registration state, not process or
  execution-capacity evidence. A service Worker contributes capacity only when
  its selected Session heartbeat is fresh and reports `idle` or `running`;
  fresh `paused`, `error`, or `disabled` status remains visible but contributes
  zero capacity.

## Verification

```bash
python3 -m py_compile \
  server.py \
  agentops_mis_cli/worker.py \
  agentops_mis_core/worker_fleet.py \
  scripts/worker_intake_auto_plan_smoke.py \
  scripts/worker_intake_heartbeat_fleet_smoke.py \
  scripts/worker_service_heartbeat_cadence_smoke.py
python3 scripts/worker_intake_auto_plan_smoke.py
python3 scripts/worker_intake_heartbeat_fleet_smoke.py
python3 -B scripts/worker_service_heartbeat_cadence_smoke.py
python3 scripts/operator_loop_supervision_consumption_smoke.py
python3 scripts/operator_task_intake_smoke.py --isolated-fixture
python3 -B scripts/human_browser_auth_smoke.py
python3 -B scripts/workspace_isolation_smoke.py --base-url <isolated-loopback-server>
python3 scripts/secret_scan_smoke.py
git diff --check
```

The 2026-07-22 workspace-authority regression adds adversarial collision
coverage. The Human smoke proves that a foreign `task_id` takeover returns
`409`, Audit filtering does not leak a foreign Run audit when a local Task has
the same ID, and both explicit Human-created membership and collaborator-only
Agent projection survive list/detail/performance/Dashboard reads. The dual
workspace Agent Gateway smoke proves conflicting Artifact and Approval IDs
return `409` and the original workspace rows remain unchanged. It also rejects
known-entity and custom-entity Audit writes anchored to the other workspace,
while preserving authorized collaborator Audit emission. Commander plan,
dispatch, batch, synthesis, Project Board and Integration Inbox smokes, plus
Operator receipt, handoff, start-check and loop-self-check smokes, pass against
temporary SQLite/loopback fixtures. These are deterministic authority and
control-plane tests; they do not claim a fresh Hermes/OpenClaw live execution.

The focused smoke covers four early-return paths: automatic plan creation,
existing plan verification, high-risk refusal and explicitly disabled
auto-plan. It also proves that a rejected heartbeat propagates instead of being
reported healthy and that 20 immediate repeated blocked polls produce only one
heartbeat request. The isolated contract additionally mints a real restricted
Session with `tasks:read` but no heartbeat scope; its authenticated pull
succeeds and its heartbeat is rejected with `forbidden`, which propagates to
the Worker loop.

The isolated Fleet smoke uses a real short-lived Agent Gateway session and
temporary SQLite authority. Across 21 blocked iterations it makes two
heartbeat requests, keeps the Fleet state `fresh`, and adds exactly one
heartbeat Runtime Event, one heartbeat Audit row, one sampled task-pull Runtime
Event and one task-pull Audit row. It asserts both the remote-enrollment and
service-worker projections plus execution-capacity counts. A second workspace
with the same Agent ID remains stale while the primary workspace is fresh. The
second due client request refreshes liveness but is coalesced from historical
evidence. No provider or live Runtime executes. A separate 20-request
concurrent case for each of heartbeat and task pull proves the process-local,
DB-only critical sections record exactly one Runtime Event and one Audit row
rather than racing duplicate ledger decisions. The critical sections contain
no network, subprocess or Runtime adapter call.

When one local LaunchAgent also appears through its authenticated Gateway
Session, execution capacity is keyed by `(workspace_id, agent_id)` on both
paths and remains one Worker rather than an inflated count of two.
The smoke also proves that a read-only Session can pull safely without becoming
execution capacity, and that two workspaces using the same Agent ID each create
their own first heartbeat sampling record.

The fixed-clock cadence smoke independently exercises the default 60-second
request interval together with the real idle backoff. It checks 250 Fleet
samples across five heartbeat requests, observes a maximum 65-second request
gap against the 90-second service freshness window, and finds zero periodic
stale windows. It also proves that the exact 90-second boundary remains fresh
and that an actually expired heartbeat becomes stale immediately after that
boundary. It starts no service and reads no database or credential.

## Package Boundary

The real finding remains bound to installed preview.37 and preview.37 is not
credited with the correction.

The corrective package is
`v1.6.0-private-host-preview.38` at exact commit
`ee3d36c9ae4f123261893376fff012e36fc8a973`. Candidate, Draft and public assets
were byte-equal, isolated consumers passed, and the real Mini upgraded with
verified manual and automatic pre-update backups.

After both preview.38 Worker LaunchAgents returned, the same Intake-blocked
queue produced authenticated heartbeats without invoking Hermes or OpenClaw.
The first Fleet readback changed from preview.37's zero execution-capacity and
two stale service Workers to status `ready`, two execution-capacity service
Workers and zero stale service Workers. The later real model runs are recorded
separately and are not used to manufacture heartbeat freshness.

Extended observation crossed the 90-second Fleet freshness threshold and found
both service Workers stale again while their processes, task pulls and loop
iterations remained current. The local Host Workers use short-lived Sessions
minted from the Host machine credential, so those Sessions have no parent
enrollment token. Their 60-second same-state heartbeats updated the
workspace-scoped `agent_gateway_heartbeat_observations` row but intentionally
did not add another 15-minute Runtime Event. Preview.38 Fleet projection read
the enrollment timestamp plus historical Runtime Event, but not the current
observation timestamp. Preview.38 therefore receives credit for the
Intake-blocked heartbeat emission fix, not sustained Host-machine Session Fleet
liveness.

The source follow-up keeps the 15-minute `(workspace_id, agent_id)` sampling
row separate from the Session-keyed
`agent_gateway_session_heartbeat_observations` liveness table and accepts only
the row for the selected full-scope execution Session. The 15-minute per-Agent
ledger sampling row is not Fleet freshness authority. The isolated integration
test uses a real unparented Host-machine Session and proves that a coalesced
second heartbeat restores Fleet freshness without adding another Runtime Event
or Audit row. It also proves that a heartbeat-only Session cannot replace or
refresh the execution Session's observation, while a missing observation for
the selected execution Session fails closed as `never_seen`. Another
workspace's Agent/task/run/event/stuck-task rows stay out of the current Worker
read model. The fixed-clock test selects the actually newer Session across
mixed ISO offsets after UTC normalization. Fresh `paused`, `error`, or
`disabled` heartbeats remain observable but contribute zero execution
capacity. This source correction requires a later exact package and real
observation beyond two heartbeat cycles.

The migration fixture starts with the preview.38 per-Agent heartbeat table and
one populated sampling row. Schema initialization preserves that row, creates
an empty Session-keyed liveness table, and does not promote historical evidence
into current capacity. After a preview.38 schema upgrade, Fleet remains
`never_seen` until the selected authenticated execution Session sends its first
heartbeat.

## Post-Acceptance Storage Pressure Finding

After the package and real-runtime acceptance was complete, the Host volume
later fell to roughly 115 MiB free. The backend process and listening socket
remained present, but health requests stopped returning a usable response and
both launchd Workers exited with a nonzero status. This availability failure
temporarily obscured the separate Host-machine Session projection defect.

Only an unreferenced, stopped AgentOps test directory under
`/private/tmp` was removed; no Host database, historical backup, credential,
Runtime evidence or Tailscale configuration was changed. APFS then reclaimed
more than one GiB, Host health returned HTTP 200, and both Workers returned as
two execution-capacity service Workers with zero stale service Workers on the
initial readback. The extended readback then reproduced the 90-second
projection defect while Host health stayed HTTP 200, proving that storage
pressure and heartbeat projection were independent findings.

This recovery does not close the storage-resilience gate. A bounded backup
retention/prune command, Host free-space preflight and bounded Host log rotation
remain required before final RC. Neither the initial Fleet recovery nor the
disk-space recovery is presented as sustained Host acceptance.
