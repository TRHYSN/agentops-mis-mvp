# Project Context Packet Acceptance

## Purpose

This slice closes the difference between proving that knowledge retrieval ran
and giving an AI worker enough reviewed project context to perform useful work.
It does not ingest Codex/OpenClaw conversation archives or turn raw transcripts
into project authority.

## Product Contract

- `agentops knowledge evidence-packet` remains metadata-only retrieval proof.
- `agentops knowledge context-packet` returns bounded redacted summaries for
  transient model use.
- Context may come from versioned Markdown or an `approved` Memory row.
- Candidate/rejected/stale Memory is not model authority.
- Agent Gateway workspace and task visibility still apply.
- The default packet allows five knowledge summaries, three approved memories,
  480 characters per block and 4000 characters total.
- Tool Call, Evaluation, Audit and Worker result payloads store context IDs,
  approved Memory IDs, packet/block hashes and counts, not summary text.
- Raw task text, source bodies, source refs, prompts, responses, credentials,
  private messages and full transcripts remain omitted.

## Runtime Flow

```text
MIS task
  -> task-aware Knowledge Index search
  -> bounded versioned summaries + approved Memory summaries
  -> transient Hermes/OpenClaw/Codex prompt context
  -> model result
  -> Run / Tool / Evaluation / Audit store hashes and IDs only
```

The bundled `knowledge/shared/agentops_mis_project_context.md` gives a newly
installed Host a concise product and authority baseline. Dynamic continuity
still comes from reviewed Memory rows and task state rather than editing a raw
conversation archive into the bundle.

## Verification

```bash
python3 -m py_compile server.py agentops_mis_cli/agentops.py agentops_mis_cli/worker.py
python3 scripts/worker_knowledge_evidence_consumption_smoke.py
python3 scripts/agent_gateway_knowledge_scope_smoke.py
python3 scripts/knowledge_scope_policy_smoke.py
python3 scripts/knowledge_retrieval_quality_smoke.py
python3 scripts/module_boundary_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The Worker smoke creates an isolated workspace and task, indexes versioned
knowledge, proposes and explicitly approves one project-context Memory, reads a
task-bound context packet, and runs a Mock Worker. It must prove that the Worker
used both bounded knowledge and approved Memory while the unique Memory marker
is absent from Tool, Evaluation, Audit and returned Worker evidence.

## Workspace Memory Authority Amendment

Every Memory row now has an explicit `workspace_id`. Existing task-bound rows
inherit the task workspace through an idempotent schema migration; legacy
taskless rows stay conservatively in `local-demo` until an administrator moves
them through a future explicit workflow. SQLite authority triggers also enforce
the task workspace for direct legacy writers that bypass the Python helper.

Context Packet sharing follows these rules:

- approved `project` and `org` Memory is shared with scoped Agents in the same
  workspace, including a different Agent working on a different task;
- approved `task` Memory remains constrained by task/Agent visibility;
- candidate, rejected, stale and superseded Memory never becomes model context;
- no Memory crosses a workspace boundary;
- another Agent or workspace cannot reuse a `memory_id` to overwrite an
  existing candidate or reviewed Memory.

The isolated Worker acceptance now creates a second Agent, task and short-lived
Session in the same workspace plus a third Agent in another workspace. It proves
approved project/org sharing, candidate exclusion, cross-workspace isolation and
source-ref/prompt/response/transcript/token omission. The migration acceptance
upgrades a database without `memories.workspace_id` twice and verifies the
backfill, index, triggers and idempotent migration row:

```bash
python3 scripts/memory_workspace_authority_migration_smoke.py
python3 scripts/worker_knowledge_evidence_consumption_smoke.py
python3 scripts/human_browser_auth_smoke.py
```

Installed preview.40 predates this amendment. Its earlier OpenClaw run proved
that versioned Knowledge reached the real runtime, but the scoped Session did
not receive approved Memory. A later exact package and fresh real-runtime run
are required before attributing workspace Memory sharing to the installed Host.

Local verification on 2026-07-23 passed all commands above. The retrieval
quality fixture reported Recall@5 `1.0` and MRR `1.0`; the gateway scope smoke
proved that Context Packet summaries stay inside the bound workspace and that
workspace header/query spoofing returns `403`; the secret scan found zero
findings across 816 tracked files.

Mock execution verifies deterministic protocol behavior only. Product-level
Hermes/OpenClaw evidence requires a current exact Host package, an explicitly
authorized task and a real persistent Worker run.

## Real Runtime Dogfood

On 2026-07-23, exact clean source commit `0057efa` was started on loopback with
an isolated SQLite ledger. The explicitly confirmed customer-worker workflow
then ran once through the existing local Agnesfallback Hermes gateway and once
through the existing local OpenClaw runtime. These were real model executions,
not mock adapters or fixed health probes.

| Adapter | Run | Verified plan evidence | Context evidence |
| --- | --- | --- | --- |
| Hermes | `run_gw_bf22d6248d0d` | `pem_080256cb29ea9d8f` | 8 blocks consumed; packet hash recorded; body omitted |
| OpenClaw | `run_gw_3df926a574bb` | `pem_310347c0ca466082` | 8 blocks consumed; packet hash recorded; body omitted |

Each run created one Tool Call and Evaluation, two Artifacts, two reviewable
Memory candidates, one customer-delivery Approval, 15 Runtime Events and 12
Audit rows. Operator Evidence reported retrieval status `ready`, Recall@5
`1.0`, MRR `1.0`, verified Plan Evidence, and
`context_body_not_persisted:true`. Raw prompts, raw responses and credentials
were omitted.

The Agent outputs described an isolated source Host, so the administrator
rejected both generated Memory candidates instead of promoting temporary
service observations into project authority. The customer-delivery approvals
were also rejected because this was an internal acceptance run, not an
approved customer publication. After those decisions both Operator Evidence
reports were `ready` with no failed checks; the Runs correctly became
`blocked`, meaning execution evidence is complete while delivery is forbidden.

This receipt is source-level dogfood. The isolated database remains outside the
repository and does not replace an installed exact-package or second-device
acceptance gate.

## Known Limitations

- Summaries are local FTS/redaction output, not semantic embeddings.
- The current packet is bounded text, not a long-term automatic compaction
  policy for Codex/OpenClaw session archives.
- Old Hosts expose evidence-only retrieval until upgraded.
- Installed preview.40 does not yet include workspace-authoritative Memory
  sharing; source-level acceptance is not an installed-product claim.
- Physical second-device acceptance remains a separate Private Host gate.
