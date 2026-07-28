# Codex to AgentOps MIS Product Bridge Spec

## Document Status

- Status: `Proposed`
- Contract version: `codex_mis_product_bridge_v0`
- Parent baseline inspected: `main` at
  `40db4e22387771cf41e7dfed2490eab283fe0b6f`
- Implementation branch: `codex/codex-mis-product-bridge`
- Change class: runtime readiness, read-only control surface, documentation, and
  offline/live acceptance
- Runtime rule: re-run `git rev-parse HEAD` and the relevant acceptance checks
  before making a current-product claim; the baseline above is evidence, not a
  permanently current release pointer.

This spec is a productization profile over the existing Agent Gateway, Codex
worker, Knowledge Evidence Packet, Agent Plan, PreparedAction, and MIS evidence
ledger. It does not create a second task, approval, identity, or evidence
protocol.

## Product Positioning

AgentOps MIS gives a customer a low-friction way to connect Codex as a governed
execution client: MIS supplies bounded project context and tasks, humans retain
approval authority, and Codex returns linked evidence plus a reviewable Project
Delta.

Codex is an execution client, not an authority system.

## Product Outcome

A customer should be able to:

1. install or open AgentOps MIS;
2. sign in as a human owner;
3. connect an installed Codex runtime without pasting a long-lived credential
   into chat or command history;
4. approve a least-privilege Codex worker identity;
5. assign a task and let Codex receive one bounded Context Packet;
6. observe plan, run, tool, evaluation, artifact, approval, and audit evidence;
7. review a candidate Project Delta; and
8. revoke the Codex connection without deleting historical evidence.

The first product slice is local-first. Shared or remote use additionally
requires an authenticated TLS transport and the existing shared-deployment
security gates.

## Status Vocabulary

- **CURRENT**: present in this product branch and covered by source-backed
  acceptance. The parent baseline above is the comparison point, not the claim
  boundary.
- **GAP**: required for the low-friction product flow but not implemented as a
  complete product contract.
- **PROPOSED**: the smallest compatible addition; it must not be described as
  current until source and acceptance evidence exist.

## Current Implementation Baseline

| Capability | Status | Source-backed boundary |
| --- | --- | --- |
| Installable CLI | CURRENT | `pyproject.toml` exposes `agentops` and `agentops-worker`; installation is from a source checkout with `python3 -m pip install .`. |
| CLI connection config | CURRENT | `agentops login` stores base URL, workspace, agent ID, and an optional API key in owner-only `~/.agentops/config.json`; environment variables take precedence. This is a developer fallback, not the target customer login. |
| Enrollment and session auth | CURRENT | Agent Gateway supports one-time-visible, hash-stored enrollment credentials, approval-gated enrollment, revocation/rotation, and short-lived child sessions. |
| Scoped machine API | CURRENT | Agent registration/heartbeat, task create/read/pull/claim, run start/heartbeat, runtime events, tool calls, artifacts, Agent Plans, plan-evidence manifests, approval requests, PreparedActions, memory proposals, evaluations, and audit writes are available through Agent Gateway routes. |
| Knowledge Evidence Packet | CURRENT | `GET /api/agent-gateway/knowledge/evidence-packet` and `agentops knowledge evidence-packet` return task-aware retrieval IDs, paths, source hashes, metrics, and omission proof without raw snippets or source content. |
| Agent Work Method launch packet | CURRENT | `GET /api/operator/loop-launch-packet` and `agentops operator loop-launch-packet` combine intake, knowledge metadata, repo-map localization, plan draft, control state, and evidence requirements without executing a runtime. |
| Codex read-only worker | CURRENT | `agentops-worker --adapter codex` runs the official non-interactive Codex CLI in an ephemeral, strict, read-only sandbox and records bounded MIS evidence. |
| Codex connector/readiness | CURRENT | `rtc_codex_local` supplies a Codex capability/trust row, while worker adapter readiness exposes `adapters.codex` with safe binary/version attestation, trust, read-only readiness, workspace-write attestation, and raw binary-path omission. |
| Codex workspace-write | CURRENT | A separate high-risk workflow uses a human-approved Agent Plan, exact PreparedAction, attested Codex binary, managed detached Git worktree, allowed paths, independent diff checks, execution lease, and evidence closure. Commit, merge, push, deploy, and publication remain outside that authorization. |
| Codex browser surface | CURRENT | `/admin/codex` is a read-only, fail-closed observation page for Codex workers, runs, approvals, fleet state, and connector evidence. |
| Product MCP bridge | GAP | No Codex-specific MCP enrollment server or supported MCP tool bundle exists. |
| One-click customer connection | GAP | No signed installer, device/browser login, OS-keychain credential handoff, or connection wizard exists. |
| Dedicated Context Packet | GAP | The required ingredients exist, but there is no single `context-packet` API/CLI contract binding task, Git state, reviewed project memory, knowledge evidence, approvals, and output requirements. |
| First-class Project Delta writeback | GAP | Codex can create an artifact, candidate memory, and audit row, but there is no idempotent bridge command/schema that records them as one candidate Project Delta. |
| Connection lifecycle receipt | GAP | Codex connector/readiness exists, but there is no durable product receipt binding enrollment, session, heartbeat, scope set, current-code state, local Codex attestation, trust, and revocation lifecycle into one customer connection object. |

## Product Boundaries And Non-Goals

The bridge does not:

- make Codex a human user or human approver;
- let the browser pretend it can inspect a Codex binary on another machine;
- give Codex direct SQLite access;
- replace Agent Gateway task/run/evidence APIs;
- make Notion the runtime ledger;
- make MIS authoritative for Git commits, diffs, pull requests, or CI;
- store raw prompts, raw responses, private transcripts, arbitrary source
  files, or raw patches by default;
- authorize commit, merge, push, deploy, publication, or a live Notion write as
  a side effect of workspace-write approval; or
- auto-promote a Project Delta or memory candidate to canonical project state.

## Actors And Trust Boundaries

| Actor | Allowed responsibility | Forbidden authority |
| --- | --- | --- |
| Human owner/admin | Install Host, approve enrollment/scopes, decide high-risk plans/actions, revoke connection, review delivery and Project Delta | Must not infer success without ledger/Git evidence |
| Human approver | Decide the exact approval presented by MIS | Must not approve a changed or expired action hash |
| Codex worker | Read an authorized Context Packet/task, create and verify a plan, execute within its mode, request approval, write bounded evidence, propose Project Delta | Cannot self-approve, promote memory, issue credentials, widen scopes, merge, push, deploy, or publish |
| AgentOps MIS Host | Authenticate, scope, dispatch, supervise, store authority records, build packet, enforce approvals, expose safe readback | Does not become the LLM runtime or Git remote |
| Codex runtime | Produce analysis or a bounded worktree diff | Does not own task, approval, audit, or canonical project state |
| GitHub | Hold code/ref/PR/CI truth | Does not own MIS run or approval state |
| Notion | Hold reviewed project memory and candidate collaboration records | Does not own execution proof or approval-wall state |

## Installation, Login, And Connection Flow

### Current Manual Fallback

The current source-backed operator path remains useful for developers:

```bash
python3 -m pip install .
agentops status
agentops worker preflight --adapter codex
```

An owner creates or approves an enrollment with the current
`agentops enrollment request` / `agentops enrollment issue-approved` flow. The
one-time credential is supplied to the customer machine through a secure
out-of-band handoff, preferably as `AGENTOPS_API_KEY` rather than a command-line
argument. `agentops login` may save it in an owner-only local config file.

The two onboarding shapes must remain distinct:

- Local loopback Codex execution must omit `--use-session`:

  ```bash
  agentops workflow run-task \
    --adapter codex \
    --confirm-run \
    --worker-agent-id <codex_agent_id> \
    --title "<task title>" \
    --description "<task description>"
  ```

- `--use-session` is reserved for remote/enrolled workers:

  ```bash
  agentops workflow run-task \
    --adapter codex \
    --confirm-run \
    --use-session \
    --worker-agent-id <codex_agent_id> \
    --title "<task title>" \
    --description "<task description>"
  ```

The local loopback path uses the Host's local authority boundary. The
remote/enrolled path exchanges its enrollment credential for a short-lived
session before task processing. Adding `--use-session` to the un-enrolled local
loop is an onboarding error, not a stronger security mode.

Limitations of this fallback:

- the customer must understand CLI installation and enrollment;
- a long-lived credential may remain in a local JSON config rather than an OS
  keychain;
- there is no device-code login or browser approval return;
- there is no single connection receipt binding Host, workspace, identity,
  scopes, Codex attestation, and heartbeat; and
- the owner must assemble context/readback commands manually.

### Target Customer Flow

1. **Install Host**: the customer installs AgentOps MIS and signs in to the
   Human Workspace. Host readiness must pass before connection starts.
2. **Install bridge**: the Codex machine installs a signed `agentops` helper or
   uses a bundled helper. It detects Codex locally but does not execute it.
3. **Begin connection**: proposed `agentops connect codex --host <url>` requests
   a short-lived device code. The CLI prints a safe URL/code, not a bearer
   credential.
4. **Human grant**: the browser shows workspace, machine safe reference,
   requested scopes, expiry, runtime mode, and risks. A human approves or
   rejects the enrollment.
5. **Credential handoff**: the bridge receives a one-time enrollment credential
   over the authenticated device flow, stores it in the OS keychain, and
   immediately exchanges it for a short-lived Agent Gateway session.
6. **Preflight**: the local helper attests the Codex binary and performs
   read-only Gateway/worker checks. MIS records only safe hashes, versions,
   statuses, and omission flags.
7. **Connection receipt**: MIS reports `connected` only when enrollment,
   short-lived session, heartbeat, workspace/scope binding, and local Codex
   preflight all pass. Server-only data cannot substitute for local preflight.
8. **Task loop**: Codex receives the task-bound Context Packet, follows
   `READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD`, and
   writes linked evidence.
9. **Review**: the human reviews approvals, diff/evidence summaries, evaluation
   results, and the candidate Project Delta in MIS.
10. **Disconnect**: the owner revokes the enrollment; active child sessions are
    invalidated while historical ledger evidence remains.

**PROPOSED:** `agentops connect codex`, `agentops disconnect codex`, and the
device/browser login flow do not exist at the inspected baseline.

## Authority Boundaries

| Object or claim | Authority | Bridge behavior |
| --- | --- | --- |
| Repository, branch, commit, diff, PR, CI/test result | GitHub and the exact local Git checkout | Context Packet carries verified refs/hashes; MIS stores links and evidence summaries, not a competing Git truth |
| Task, Run, Tool Call, Runtime Event, PreparedAction, Approval, Artifact, Evaluation, Agent Plan, Plan Evidence, Audit | AgentOps MIS | Codex uses scoped API/CLI/MCP tools; no direct DB writes |
| Reviewed decision, requirement, risk, backlog, handoff | Notion Project Ledger plus versioned `docs/project/` after human review | Context Packet may carry reviewed IDs, bounded summaries, status, and source hashes |
| Candidate Project Delta | AgentOps MIS candidate evidence and optionally a Notion `Inbox`/`Proposed` record | Never canonical until human review and source reconciliation |
| Raw chat or model output | No canonical authority | May be ephemeral input; never copied wholesale into project memory |
| Codex answer or worktree diff | Execution output | Must be independently verified and linked to the Run before delivery |

GitHub is authoritative for code, branch, commit, diff, PR, and CI facts.
AgentOps MIS is authoritative for execution, approval, evidence, and audit
facts. Notion is authoritative only for reviewed project-memory records within
its declared field boundary. If these sources conflict, the bridge must stop
and return `authority_conflict`; it must not silently select a convenient
source.

## Context Packet Contract

### Reuse Rule

The Context Packet is an aggregate read model, not a new authority table. It
must be assembled from:

- the workspace-bound Task and acceptance criteria;
- `operator loop-launch-packet` Method Block and control state;
- the task-aware Knowledge Evidence Packet;
- approved Memory/project records and reviewed Notion references when
  available;
- exact local Git repository/branch/HEAD/dirty-state evidence supplied by a
  trusted local bridge process;
- current Agent Plan, approval, PreparedAction, and evidence requirements; and
- safe connector/runtime capability state.

**PROPOSED:** `GET /api/agent-gateway/context-packet?task_id=<id>` and
`agentops context packet --task-id <id>` are aggregate aliases over those
existing owners. They do not exist at the inspected baseline.

### Minimum Packet Shape

```json
{
  "operation": "codex_mis_context_packet",
  "version": "v0",
  "packet_id": "ctx_<stable-id>",
  "packet_hash": "<sha256>",
  "generated_at": "<iso8601>",
  "expires_at": "<iso8601>",
  "workspace_id": "<bound-workspace>",
  "agent_id": "<bound-codex-agent>",
  "task": {
    "task_id": "<task-id>",
    "task_revision": "<stable-hash>",
    "title": "<bounded-task-title>",
    "description": "<bounded-task-description>",
    "acceptance_criteria": "<bounded-acceptance>",
    "risk_level": "low|medium|high|critical",
    "task_text_persistence": "ephemeral"
  },
  "git": {
    "repository_ref": "<safe-repository-id>",
    "branch": "<verified-branch>",
    "head_sha": "<verified-sha>",
    "dirty": false,
    "source": "local_bridge_preflight"
  },
  "method": {
    "protocol": "READ_PLAN_RETRIEVE_COMPARE_EXECUTE_VERIFY_RECORD",
    "loop_launch_packet_hash": "<sha256>"
  },
  "knowledge": {
    "evidence_packet_hash": "<sha256>",
    "retrieval_ids": [],
    "paths": [],
    "source_hashes": [],
    "raw_content_omitted": true
  },
  "project_memory": {
    "approved_memory_ids": [],
    "reviewed_notion_refs": [],
    "candidate_refs": []
  },
  "governance": {
    "agent_plan_id": null,
    "agent_plan_hash": null,
    "pending_approval_ids": [],
    "prepared_action_id": null,
    "prepared_action_hash": null
  },
  "runtime": {
    "connector_id": "rtc_codex_local",
    "trust_status": "trusted|review_required|blocked",
    "readiness": "ready|review_required|blocked|unavailable",
    "current_code_ok": true,
    "binary_attestation_hash": "<sha256>",
    "raw_binary_path_omitted": true
  },
  "evidence_contract": {
    "required": [
      "run",
      "runtime_event",
      "tool_call",
      "evaluation",
      "artifact",
      "audit",
      "plan_evidence_manifest",
      "project_delta_candidate"
    ]
  },
  "safety": {
    "workspace_bound": true,
    "read_only_packet": true,
    "credential_omitted": true,
    "raw_prompt_omitted": true,
    "raw_response_omitted": true,
    "private_transcript_omitted": true
  }
}
```

### Packet Invariants

- `packet_hash` binds every authority reference and omission flag returned to
  Codex.
- The packet is workspace-, agent-, task-, revision-, and expiry-bound.
- A changed task revision, Git HEAD, scope set, Agent Plan hash, or
  PreparedAction hash invalidates the packet.
- Task text may be delivered only to the assigned scoped worker and remains
  ephemeral; durable evidence stores its hash and bounded summary, not another
  full copy.
- Knowledge rows contain retrieval IDs, approved paths, source hashes, headings,
  and metrics. Raw snippets/content remain omitted by default.
- Notion rows are reviewed references or bounded summaries, never arbitrary
  private page bodies.
- Codex run-start admission depends on the Codex current-code gate and its own
  scope/trust/action checks. It must not depend on Hermes or OpenClaw readiness.
  A blocked unrelated adapter cannot block an otherwise valid Codex start.
- A packet read does not claim a task, create a run, approve an action, execute
  Codex, mutate Git, or write Notion/GitHub.
- Missing or conflicting authority returns a bounded failure; it must not be
  filled with mock data.

## CLI, API, And MCP Tool Surface

### Existing CLI/API Surface To Reuse

| Purpose | CLI | API |
| --- | --- | --- |
| Safe connection status | `agentops status` | `GET /api/agent-gateway/status` |
| Codex host preflight | `agentops worker preflight --adapter codex` | Local CLI attestation plus `adapters.codex` in worker adapter readiness |
| Task read/pull/claim | `agentops task get|pull|claim` | `GET /api/agent-gateway/tasks/:id`, `GET /api/agent-gateway/tasks/pull`, `POST /api/agent-gateway/tasks/:id/claim` |
| Knowledge evidence | `agentops knowledge evidence-packet` | `GET /api/agent-gateway/knowledge/evidence-packet` |
| Method launch packet | `agentops operator loop-launch-packet` | `GET /api/operator/loop-launch-packet` |
| Agent Plan | `agentops agent-plan create|get|verify` | `/api/agent-gateway/agent-plans` read/write/verify routes |
| Run lifecycle | `agentops run start|heartbeat|get` | `POST /api/agent-gateway/runs/start`, `POST /api/agent-gateway/runs/:id/heartbeat`, `GET /api/agent-gateway/runs/:id` |
| Evidence write | `agentops runtime-event record`, `toolcall record`, `artifact record`, `eval submit`, `audit emit` | Existing Agent Gateway runtime-event, tool-call, artifact, evaluation, and audit routes |
| Human gate request | `agentops approval request` | `POST /api/agent-gateway/approvals/request` |
| Exact action state | `agentops approval prepared-action get|resume` | Existing Agent Gateway PreparedAction read/resume routes |
| Candidate memory | `agentops memory propose` | `POST /api/agent-gateway/memories/propose` |
| Evidence closure | `agentops plan-evidence create|verify` | Existing plan-evidence manifest routes |
| Governed Codex task | `agentops workflow run-task --adapter codex` | Existing task plus worker orchestration |
| Governed Codex write | `agentops workflow codex-workspace-write` | Existing Gateway owners orchestrated by CLI/worker |

### Proposed Product CLI

```text
agentops connect codex
agentops connection status --runtime codex
agentops context packet --task-id <id>
agentops project-delta propose --run-id <id> --from-file <bounded-json>
agentops disconnect codex
```

These commands must call existing authority owners. They must not open the MIS
database or create a second local ledger.

### Proposed MCP Tools

The MCP server runs on the Codex/customer machine and is a typed adapter over
the same HTTPS Agent Gateway contracts:

```text
agentops.status
agentops.context.get
agentops.task.get
agentops.task.pull
agentops.task.claim
agentops.plan.create
agentops.plan.verify
agentops.run.start
agentops.run.heartbeat
agentops.runtime_event.record
agentops.toolcall.record
agentops.artifact.record
agentops.approval.request
agentops.approval.status
agentops.prepared_action.get
agentops.prepared_action.resume
agentops.evaluation.submit
agentops.memory.propose
agentops.audit.emit
agentops.plan_evidence.create
agentops.plan_evidence.verify
agentops.project_delta.propose
```

MCP is not implemented at the inspected baseline. Each tool must have a
bounded JSON schema, request idempotency key where mutating, explicit
workspace/task/run binding, safe error shape, and redacted response. The MCP
server must not expose generic SQL, filesystem, shell, browser, Git push, Notion
write, or unrestricted HTTP tools.

The Codex MCP profile must not expose:

- enrollment create/issue/rotate;
- scope grants;
- Agent Plan approve/reject;
- approval approve/reject;
- memory approve/reject;
- Git commit/merge/push/deploy;
- Notion live write; or
- any generic command that bypasses PreparedAction and human review.

## Permission Scopes

The current valid Agent Gateway scope vocabulary remains authoritative. A
recommended Codex worker profile is:

```text
agents:heartbeat
knowledge:read
agent_plans:read
agent_plans:write
plan_evidence:read
plan_evidence:write
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

`read-only Codex` means read-only with respect to the customer repository and
external systems. It still needs narrow MIS ledger-write scopes to record
evidence.

Optional scopes require a separate reason:

- `tasks:create`: only when Codex is allowed to propose/create MIS tasks;
- `knowledge:write`: only for a trusted index-maintenance role; normal Context
  Packet reads need `knowledge:read`;
- `agents:write`: bootstrap/admin use, not a normal Codex worker.

There is intentionally no `approvals:approve` Agent Gateway scope. Codex may
request and poll an approval but cannot decide it.

Workspace-write does not become safe through a reusable broad scope. The
current exact authorization remains: verified high-risk Agent Plan, human
decision, PreparedAction hash/checkpoint, allowed paths, attested runtime,
explicit confirmation, execution lease, independent verification, and
consume-once closure. A future Codex-specific scope may be an additional
coarse gate, never a replacement for that exact action authorization.

## Approval And Execution Policy

Human approval is mandatory for:

- Codex enrollment when the machine, runtime, or requested scopes are not
  already covered by an approved policy;
- high/critical Agent Plans;
- workspace-write;
- external network writes;
- Git commit, merge, push, release, deploy, or publication;
- live Notion write/sync;
- connector credential or trust-policy changes;
- destructive file operations; and
- promotion of candidate memory or Project Delta into canonical project state.

Codex can request approval and inspect status. It cannot approve its own plan
or action.

Workspace-write follows:

```text
verified Agent Plan
-> exact PreparedAction + action hash + checkpoint
-> human approval
-> exact hash/revision recheck
-> consume-once execution lease
-> managed worktree execution
-> independent verification
-> linked evidence closure
-> human merge/publish decision outside this authorization
```

If any bound value changes, the action expires or fails closed. A status edit
is not a substitute for execution evidence.

For Codex, run-start admission is runtime-specific:

```text
Codex current-code gate
-> Codex scope/trust/plan/action checks
-> Codex run start
```

Hermes/OpenClaw adapter availability is not an input to the Codex decision.
Their state may remain visible as independent operational context, but it
cannot cause Codex run creation to fail.

## Project Delta Writeback Contract

### Boundary

A Project Delta is the durable change relative to existing project state, not
a transcript or complete model answer. It is always candidate-first when
proposed by Codex.

Minimum fields:

```json
{
  "delta_type": "Decision|Proposal|Requirement|Task|Risk|Evidence|Question|Handoff",
  "status": "Proposed",
  "title": "<bounded-title>",
  "summary": "<bounded-delta-only-summary>",
  "repository": "<repository-ref>",
  "branch": "<verified-branch>",
  "commit": "<verified-commit-or-null>",
  "duplicate_of": null,
  "updates": null,
  "supersedes": null,
  "conflicts_with": null,
  "evidence_refs": ["run:<id>", "artifact:<id>", "evaluation:<id>"],
  "owner": "<human-or-team>",
  "next_action": "<bounded-next-action>"
}
```

### Minimal Reuse Implementation

**PROPOSED:** `agentops project-delta propose` /
`agentops.project_delta.propose` performs one idempotent bridge operation over
existing owners:

1. validate the structured fields and exact Git/MIS references;
2. record a hash-backed `project_delta_candidate` artifact summary on the Run;
3. call `memories/propose` with `memory_type=project_context`,
   `review_status=candidate`, the bounded delta summary, and evidence source
   refs;
4. emit an audit event linking task, run, artifact, memory, packet, and Delta
   hashes; and
5. optionally prepare a separate, human-approved Notion write. No Notion write
   occurs inside the proposal operation.

The bridge should derive stable idempotency identifiers from workspace, task,
run, Delta hash, and target. Partial completion is reported with the created
IDs and a retry-safe next step; it is never presented as canonical completion.
MIS stores bounded summaries, hashes, and refs. A fuller reviewed Delta may
live in a Git-tracked project document or a Notion candidate record.

Promotion remains a human action:

- Git code facts are verified against GitHub;
- MIS execution claims are verified against linked ledger evidence;
- Notion project-memory fields are checked for stale/conflicting state; and
- only then may a reviewer mark the Delta `Approved` or `Implemented`.

## Data Minimization And Secret Handling

### May Persist In MIS

- workspace/agent/task/run/tool/approval/evaluation/artifact/audit IDs;
- credential hashes and safe credential references, never raw credentials;
- packet, task revision, plan, action, source, diff, and artifact hashes;
- bounded redacted summaries;
- Git repository reference, branch, commit SHA, changed-path list, and test
  result summary;
- retrieval IDs, approved paths, headings, source hashes, and metrics;
- scope names, status, timestamps, duration, usage counts, and safe errors; and
- omission flags proving which raw fields were not stored.

### Ephemeral Only

- the one-time enrollment credential before keychain storage/exchange;
- bounded task title, description, and acceptance delivered to the assigned
  worker;
- the constructed Codex prompt;
- Codex JSONL events and raw model response;
- source files read by Codex in its authorized sandbox; and
- the raw workspace diff before independent hash/path verification.

### Must Not Persist Or Be Exported By Default

- raw API keys, enrollment credentials, session credentials, or connector
  secrets;
- credentials in argv, chat, audit summaries, screenshots, or Project Delta;
- full private chats or transcripts;
- raw prompts or raw responses;
- arbitrary Notion page bodies or customer documents;
- raw patch/source content in MIS; or
- unredacted subprocess output.

The target product stores long-lived client credentials in the OS keychain.
The current owner-only JSON config remains a developer fallback and must be
shown as such.

## Control Panel Acceptance

### Current Surface

`/admin/codex` is CURRENT and read-only. It independently reads current Agents,
Runs, Tool Calls, Approvals, Worker Status, Worker Fleet, and Connector sources.
Unavailable sources fail closed rather than being replaced with mock data. The
page reads only `adapters.codex` for Codex readiness and does not substitute
mock, Hermes, or OpenClaw state. Product MCP, one-click installation, and the
connection lifecycle remain explicitly unavailable/not implemented.

### Target Connection Panel

The product panel is accepted only when it:

1. exposes `not_connected`, `pending_human_approval`, `connected`, `stale`,
   `revoked`, and `error` states;
2. starts a device/browser connection request without showing a bearer
   credential;
3. shows the bound workspace, safe machine/agent reference, requested/granted
   scopes, credential expiry, session expiry, and revocation control;
4. displays the dedicated `adapters.codex` readiness/trust/attestation result,
   keeps raw binary path omitted, and distinguishes read-only readiness from
   workspace-write attestation;
5. reports `connected` only after enrollment, session, heartbeat, workspace
   binding, scope binding, and local Codex attestation pass;
6. previews Context Packet metadata/hash/expiry without raw prompt, response,
   transcript, secret, or arbitrary source content;
7. links current task, Agent Plan, pending approvals, Run, Tool Calls,
   Evaluations, Artifacts, Audit, Plan Evidence, and Project Delta candidate;
8. makes read-only execution the default and presents workspace-write as a
   separate high-risk human-approved workflow;
9. supports revoke/disconnect and proves child-session invalidation; and
10. never lets the Codex worker approve its own plan, action, memory, or
    Project Delta.

A green HTTP response alone is not connection readiness. Missing Codex
attestation, stale heartbeat, scope mismatch, blocked `rtc_codex_local` trust,
expired session, or authority conflict must produce `attention`/`blocked`, not
`ready`. Hermes/OpenClaw availability is independent and must not be substituted
for, or added as a prerequisite to, Codex readiness.

## Failure And Recovery Contract

- Enrollment denial creates no credential.
- Lost one-time credential requires rotation/re-enrollment, not retrieval from
  MIS.
- Revocation invalidates child sessions and blocks new writes.
- A stale worker can reconnect with a valid enrollment and mint a new
  short-lived session.
- A local loopback worker retries through the local authority path without
  `--use-session`; a remote/enrolled worker refreshes its short-lived session.
- Context Packet expiry or revision mismatch requires a fresh packet before
  run start/resume.
- A failed read-only Codex run records bounded failure evidence and cannot mark
  the task completed.
- A failed workspace-write removes the managed worktree according to the
  existing rollback contract and cannot reuse the old approval.
- Partial Project Delta writeback reports exact created refs and supports an
  idempotent retry; it does not self-promote.
- GitHub/Notion/MIS disagreement blocks canonical promotion and creates a
  review item.

## Phased Delivery Slices

### Slice 0 - Contract Freeze (this lane)

- Add this spec.
- Add an offline static smoke that verifies source-backed CURRENT claims,
  authority boundaries, explicit GAP/PROPOSED labels, scope rules, and data
  minimization.
- No server, UI, runtime, network, credential, or database change.

### Slice 1 - Connection CLI

- Add `agentops connect codex`, connection status, and disconnect.
- Reuse enrollment request/approval/issue/session/revoke owners.
- Add device/browser approval and OS-keychain storage.
- Produce a hash-only connection receipt and deterministic offline fixture.

### Slice 2 - Context Packet Aggregate

- Add one workspace/task-bound read endpoint and CLI command.
- Compose existing Task, loop-launch, Knowledge Evidence Packet, reviewed
  project-memory refs, Git preflight, approval, and evidence contracts.
- Add expiry/revision/hash checks and a no-ledger-mutation acceptance.

### Slice 3 - MCP Adapter

- Publish a typed MCP server whose tools map one-to-one to the allowlisted
  Agent Gateway owners in this spec.
- Exclude all human decision, credential issuance, generic shell/SQL, Git
  publish, and Notion write tools.
- Add workspace/scope/idempotency/redaction contract tests.

### Slice 4 - Candidate Project Delta

- Add the bridge-level candidate schema and idempotent artifact + candidate
  memory + audit orchestration.
- Add duplicate/update/supersede/conflict fields.
- Keep Notion write separate and PreparedAction-gated.

### Slice 5 - Product Control Panel

- Extend the existing `/admin/codex` truth surface with guided connection,
  grant/revoke, preflight receipt, Context Packet preview, and linked evidence
  readback.
- Keep host/runtime execution outside browser process authority.

### Slice 6 - Packaging And Remote Hardening

- Signed installer/update/uninstall path.
- TLS/shared deployment gate, device expiration, keychain portability policy,
  connection recovery, support bundle, and customer deployment acceptance.
- Run product acceptance with a real installed Codex runtime; deterministic
  fixtures remain CI evidence only.

## Acceptance Contract

The bridge may be called product-ready for the bounded local slice only when:

- source checkout is no longer required for the customer install path;
- a human can connect/revoke Codex without copying a long-lived credential into
  chat or shell history;
- the connection is workspace/scoped/session-bound and visible in the control
  panel;
- local loopback execution omits `--use-session`, while remote/enrolled
  execution uses a short-lived session;
- Codex receives a task-bound, hash/expiry/revision-bound Context Packet;
- Codex run-start admission proves the Codex current-code gate and remains
  independent of Hermes/OpenClaw readiness;
- read-only Codex is the default and completes a real task through the normal
  Worker/Gateway path;
- workspace-write remains separately plan/PreparedAction/human approved and
  independently verified;
- Run, Runtime Event, Tool Call, Evaluation, Artifact, Audit, Plan Evidence,
  approval state, and candidate Project Delta are linked and readable;
- Project Delta remains candidate-only until human review;
- GitHub, MIS, Notion, and Codex authority boundaries remain intact;
- revocation blocks subsequent scoped writes;
- no raw credential, prompt, response, private transcript, arbitrary Notion
  body, raw patch, local DB, or generated cache enters committed or exported
  evidence; and
- real-runtime acceptance is labeled separately from deterministic fixture
  acceptance.

## Static Verification

```bash
python3 scripts/codex_mis_product_bridge_contract_smoke.py
python3 -m py_compile scripts/codex_mis_product_bridge_contract_smoke.py
```

The static smoke is intentionally offline. It reads only an allowlisted set of
tracked source/spec files and does not inspect environment variables,
credentials, local databases, runtime logs, generated artifacts, or private
transcripts.

## Project Delta

```yaml
type: Proposal
title: Productize the existing Codex worker as a low-friction MIS bridge
status: Proposed
module: Codex Product Bridge
summary: Reuse Agent Gateway, Context/Knowledge evidence, approval, and ledger owners behind a guided connection, MCP adapter, and candidate Project Delta flow.
repository: geogejoy107-jpg/agentops-mis-mvp
branch: main
commit: runtime-derived by git rev-parse HEAD
updates: existing Codex read-only and workspace-write acceptance contracts
owner: project owner
next_action: implement Slice 1 only after review of this contract
```
