# AgentOps MIS Current Project State

> Historical baseline notice: the operational branch, commit, installed/source
> boundary, CI state, and next action below are from 2026-06-22. Read
> [`CURRENT_CONTEXT_SNAPSHOT.md`](./CURRENT_CONTEXT_SNAPSHOT.md) first for the
> current continuation point. This file remains the governance/v1.5 baseline.

> Status date: 2026-06-22
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Development line: `codex/agent-gateway-kb-demo`
> Project-governance integration commit: `5f1706c3afed22156e0bb3dc06ca351f698713d9`
> Last full audit baseline: `8d1827e00629bdca4779794121ca4a31dfa3f1e1`
> Current release status: `READY_TO_MERGE` by `docs/V1_5_MERGE_READINESS_CHECKLIST.md`
> Governance PR: `#6` merged
> Audit PR: `#5` draft; its P0 findings are now incorporated into the hardening overlay.

Always fetch the exact current development HEAD from GitHub before code or architecture work. The integration commit above is a stable baseline, not a claim that the branch will never advance. Exact release evidence must come from `git rev-parse HEAD` and the strict release scripts at runtime, not from a tracked SHA in this file.

## Product Position

AgentOps MIS is a local-first control plane for supervising a small AI workforce across runtimes. Humans create goals, supervise, approve, review delivery, and curate memory. Agents use CLI/API and future MCP paths to pull tasks, execute work, and write structured evidence back to MIS.

It is not an LLM runtime or an agent builder. It governs runtimes such as Codex, Hermes, OpenClaw, LangGraph, CrewAI, Dify, and future adapters.

## Authority Chain

```text
Project Spec / Approved Decision
-> Knowledge Retrieval
-> Agent Plan
-> Task
-> Run
-> Tool Call / Prepared Action
-> Approval
-> Artifact
-> Evaluation
-> Memory Candidate
-> Audit
```

- GitHub is authoritative for code, branch, commit, PR, and test facts.
- AgentOps MIS SQLite/API is authoritative for execution and audit facts.
- Notion MIS Project Ledger and the versioned project files are authoritative for approved project state and decisions.
- Chat history is source material, not canonical state.

## Verified Capability Baseline

The full audit at `8d1827e` verified a credible local-first control-plane candidate with:

- scoped Agent Gateway enrollment and sessions;
- task pull/claim and run/tool/artifact/evaluation/audit writeback;
- installable `agentops` and `agentops-worker` CLIs;
- Mock, Hermes, and OpenClaw adapters;
- commander, review queue, delivery board, and evidence-ledger flows;
- Agent Work Method Block v0;
- Markdown plus SQLite FTS5 Knowledge Index v0;
- Agent Plan API/CLI and verification v0.

Observed post-audit work includes customer local-deployment and SQLite backup/restore tooling, the Commander Work Package Planner, project-governance integration at `5f1706c3`, and the v1.5 hardening pass. The audit blockers are now captured as explicit P0 gates in `docs/V1_5_AGENT_GATEWAY_HARDENING_OBJECTIVE.md` and `docs/V1_5_MERGE_READINESS_CHECKLIST.md`; future commits must re-run those exact-head gates rather than rely on historical pass/fail claims.

## Current Deployment Truth

Appropriate now: loopback local use, course demonstration, controlled dogfood, and single-customer validation with explicit confirmation.

Not yet approved as a canonical claim: hosted SaaS, billing, unattended high-impact actions, universal runtime-internal per-action governance, or enterprise-grade multi-workspace operations beyond the tested local/shared guards.

## Current Milestone

```text
project governance installed
-> audit findings converted into hardening objective
-> Agent Plan / Approval Wall / redaction / workspace / knowledge / SQLite / CI gates implemented or guarded
-> exact-head CI and strict merge-readiness checks green
-> READY_TO_MERGE release-candidate state
-> merge to main through a reviewed release PR
```

Horizontal feature expansion remains secondary until this sequence is complete.

## P0 Findings Incorporated Into Release Gates

The first full audit found these areas. They are no longer only loose notes; each is mapped to hardening objective/checklist evidence and must remain green on the exact current head:

1. Agent Plan approval roles and self-approval prevention.
2. Immutable Plan hash and verified Plan hard-binding to Run and Delivery.
3. Reference provenance and existence checks during Plan verification.
4. Prepared Action, action hash, checkpoint, exact resume, idempotency, and execute-once behavior.
5. Runtime-internal tool visibility and per-action governance.
6. Unified server, worker, and CLI redaction.
7. Fail-closed shared-deployment authentication guards.
8. Exact collaborator and workspace visibility checks.
9. Knowledge workspace/ACL isolation and retrieval provenance.
10. SQLite WAL, busy timeout, short transactions, and concurrency baseline.
11. Automated CI status, secret scanning, SBOM, license, and provenance.

## Project Governance Now Installed

- Notion `MIS Project Control Center`.
- Notion `MIS Project Ledger` with dedicated review and status views.
- Root `AGENTS.md` repository instructions.
- Extended `AGENT_WORKFLOW.md` with Git preflight, deduplication, Project Delta, and handoff rules.
- Versioned `PROJECT_STATE`, `DECISIONS`, `BACKLOG`, `HANDOFF`, and operating rules.
- Repo-local `.agents/skills/project-ledger/SKILL.md`.
- Ready-to-paste ChatGPT Project Instructions template.

## Known Unknowns

- The development branch can continue moving after this state file is written.
- Historical smoke records are not a substitute for checks on the exact current head.
- ChatGPT Project Instructions cannot be edited through the available connector.
- Automatic capture must remain candidate-only until deduplication and approval are enforced.

## Current Release Command

Keep the branch frozen for release-hardening changes, re-run strict exact-head gates after any new commit, and merge through the reviewed PR only while `docs/V1_5_MERGE_READINESS_CHECKLIST.md` remains `READY_TO_MERGE`.
