# AgentOps MIS Project Governance

This directory is the versioned entry point for project continuity. Read it before making code, architecture, planning, or priority decisions.

## Required Reading Order

1. [`CURRENT_CONTEXT_SNAPSHOT.md`](./CURRENT_CONTEXT_SNAPSHOT.md) — current verified branch, source/install boundary, runtime evidence, CI truth, and next action.
2. [`DECISIONS.md`](./DECISIONS.md) — accepted cross-cutting decisions and their consequences.
3. [`BACKLOG.md`](./BACKLOG.md) — prioritized work, status, and acceptance evidence.
4. [`PROJECT_STATE.md`](./PROJECT_STATE.md) — historical governance and v1.5 baseline; current operational fields are superseded by the snapshot.
5. [`HANDOFF.md`](./HANDOFF.md) — historical 2026-06-22 handoff; use the snapshot for the live continuation point.
6. [`PROJECT_OPERATING_RULES.md`](./PROJECT_OPERATING_RULES.md) — authority, state machine, Project Delta, preflight, and review rules.
7. [`CODEX_SESSION_RETENTION_ACCEPTANCE.md`](./CODEX_SESSION_RETENTION_ACCEPTANCE.md) — summarize-then-expire safety boundary for local Codex history.
8. [`CHATGPT_PROJECT_INSTRUCTIONS.md`](./CHATGPT_PROJECT_INSTRUCTIONS.md) — ready-to-paste Project Instructions for ChatGPT.
9. [`../../AGENTS.md`](../../AGENTS.md) — repository-wide instructions.
10. [`../../.agents/skills/project-ledger/SKILL.md`](../../.agents/skills/project-ledger/SKILL.md) — reusable project-ledger workflow.

Then read:

- [`../../PROJECT_SPEC.md`](../../PROJECT_SPEC.md)
- [`../../AGENT_WORKFLOW.md`](../../AGENT_WORKFLOW.md)
- [`../../BASE_INDEX.md`](../../BASE_INDEX.md)
- task-specific specs, code, tests, and current GitHub evidence

## External Project Ledger

- [MIS Project Control Center](https://app.notion.com/p/3866adfdd920816096a0ef9bd4a58801)
- [MIS Project Ledger](https://app.notion.com/p/24467ea0d1764e40957cdcc1ca55db53)

Notion stores reviewed project decisions, risks, candidate deltas, and handoffs. GitHub remains authoritative for code and version-control facts; AgentOps MIS remains authoritative for runtime and audit facts.

## Fast Preflight

```text
Repository:
Branch:
Commit:
Current milestone:
Current objective:
Relevant approved decisions:
Open P0/P1 items:
Risks / unknowns:
```

Do not infer missing Git context from conversation memory.

## Fast End-of-Work Check

```text
Branch:
Commit:
Changed:
Not changed:
Verification:
Open risks:
Next action:
Project Delta:
```

Only update canonical state when reviewed decisions or implementation evidence changed.
