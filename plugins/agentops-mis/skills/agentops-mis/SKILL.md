---
name: agentops-mis
description: Connect Codex bidirectionally with AgentOps MIS. Use when a user asks Codex to check the MIS connection, receive or claim governed tasks, load bounded project context, follow Agent Plan and approval gates, inspect Run evidence, or record verified outcomes back to AgentOps MIS.
---

# AgentOps MIS

Use AgentOps MIS as the authority for tasks, plans, approvals, runs, evidence,
and audit. Codex is the execution client. It must not become a human approver,
credential store, or canonical project-memory authority.

## Bidirectional Contract

```text
MIS -> Codex
scoped task + bounded context + plan/approval state

Codex -> MIS
heartbeat + run/tool/evaluation/artifact/audit summaries
```

Git remains authoritative for repository, commit, diff, PR, and CI facts.
AgentOps MIS remains authoritative for execution and governance facts. Reviewed
project records remain authoritative only in their declared project-memory
system.

## Resolve The CLI

Prefer `agentops` from `PATH`. Inside the AgentOps MIS repository,
`./scripts/agentops` is the supported fallback. The plugin also ships
`../../scripts/agentops-mis`, which resolves either form without printing
credentials.

Never pass an API key as a command-line argument. Use the existing owner-only
CLI config or `AGENTOPS_API_KEY` supplied outside the conversation.

## Preflight

Run these read-only checks before claiming a connection:

```bash
agentops status
agentops doctor
agentops worker preflight --adapter codex
agentops runtime connectors
```

Treat any failed authentication, workspace mismatch, blocked connector,
unavailable Codex runtime, or stale supervision state as a blocker. Do not
replace missing live state with mock data.

## Choose The Execution Shape

### Current Codex Is The MIS Client

When this Codex task itself is receiving work from MIS, use the primitive
Agent Gateway loop below. Do **not** call:

```bash
agentops workflow run-task --adapter codex
```

That command launches another Codex Worker and would create a nested execution
instead of recording the current Codex task.

### MIS Launches A Separate Codex Worker

Use `agentops workflow run-task --adapter codex --confirm-run` only when the
human explicitly asks MIS to start a separate bounded Codex Worker. Local
loopback execution omits `--use-session`; remote enrolled Workers use a scoped
enrollment and short-lived session.

## Governed Client Loop

1. Verify the active Agent identity and workspace with `agentops status`.
2. Pull one scoped task with `agentops task pull --enforce-intake`.
3. Claim the exact task; never substitute another task after claim.
4. Request bounded retrieval evidence:

   ```bash
   agentops knowledge evidence-packet --task-id <task_id> --adapter codex
   agentops operator loop-launch-packet \
     --task-id <task_id> --agent-id <agent_id>
   ```

5. Create and verify an Agent Plan bound to the task, referenced specs,
   proposed files, risk, verification, and rollback.
6. If the verified plan requires approval, stop and surface the pending
   approval in MIS. An Agent must never approve its own plan or PreparedAction.
7. Start the Run with the exact verified `plan_id`.
8. Execute only the approved scope in the current Codex task.
9. Record bounded Runtime Event, Tool Call, Artifact, Evaluation, and Audit
   summaries. Store hashes and stable IDs; omit raw prompts, raw responses,
   credentials, private transcripts, and unreviewed customer data.
10. Complete or fail the Run explicitly. Propose Memory only as a candidate for
    human review.

Read [CLI_MAP.md](references/CLI_MAP.md) before constructing write commands.

## Approval Wall

External writes, publication, deployment, account changes, destructive
operations, high-risk tools, and workspace-write must use an exact
PreparedAction and human decision. Approval applies only to the bound action
hash and checkpoint; changed or expired actions require a new approval.

Never call `agentops agent-plan approve`, `agentops approval decide`, or an
equivalent human/admin endpoint while acting as the Agent.

## Failure Rules

- Record failed or blocked Runs instead of hiding them.
- Do not retry a denied, changed, or expired action.
- Do not claim success from a CLI exit code alone; read back Run and evidence.
- Do not promote candidate Memory or Project Delta to canonical state.
- Do not read local SQLite directly. Use Agent Gateway CLI/API.
- Do not ingest `.env`, credential stores, private messages, or full
  transcripts.

## Useful Readback

```bash
agentops task get --task-id <task_id>
agentops run get --run-id <run_id>
agentops run evidence-graph --run-id <run_id>
agentops agent-plan verify --plan-id <plan_id>
agentops runtime connectors
```

Return task/run/plan/evidence IDs and concise verified outcomes to the human.
Do not return raw credentials or raw model payloads.
