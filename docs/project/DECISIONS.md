# AgentOps MIS Decision Log

> Canonical decisions only. New ideas belong in the Notion Ledger as `Inbox` or `Proposed` until reviewed.

## Decision Index

| ID | Decision | Status | Date |
|---|---|---|---|
| D-001 | Split authority across GitHub, AgentOps MIS, and Notion | Accepted | 2026-06-21 |
| D-002 | Candidate memory cannot become authority automatically | Accepted | 2026-06-21 |
| D-003 | Repository, branch, and commit verification is mandatory | Accepted | 2026-06-21 |
| D-004 | Preserve Project Delta, not whole responses | Accepted | 2026-06-21 |
| D-005 | Use Notion for knowledge/decisions and GitHub for execution; no Asana yet | Accepted | 2026-06-21 |
| D-006 | Freeze horizontal expansion until hardening, CI, and v1.5 RC | Accepted | 2026-06-21 |
| D-007 | Capture durable Project Delta before expiring raw Codex sessions | Accepted | 2026-07-23 |

## D-001 — Authority Split

**Decision**

- GitHub owns code, branch, commit, PR, diff, and test facts.
- AgentOps MIS SQLite/API owns run, tool, approval, artifact, evaluation, memory-review, and audit facts.
- Notion Project Ledger plus versioned project files own approved project decisions, risks, requirements, backlog, and handoff.
- Chat history is source material only.

**Rationale**

A single memory layer cannot provide complete, queryable, versioned, and independently verifiable project truth.

**Consequences**

When sources disagree, reconcile them explicitly. Do not silently turn an old conversation into a current code claim.

## D-002 — Candidate Memory Is Not Authority

**Decision**

Agent output, chat ideas, and automatically captured lessons enter `Inbox` or `Proposed`. Only reviewed `Approved` or evidence-backed `Implemented` items may become canonical.

**Rationale**

Automatic capture without review creates duplicated ideas, stale priorities, and unsafe project drift.

**Consequences**

Any future automation may create candidate entries but must not directly update canonical project state.

## D-003 — Mandatory Git Preflight

**Decision**

Before code, architecture, planning, or priority work, verify exact repository, branch, and commit. If any value is unknown, mark it `Unknown` and stop implementation assumptions.

**Rationale**

The development line has advanced independently of `main`, and a prior audit baseline became stale while work was still in progress.

**Consequences**

Every handoff and technical answer must identify the verified Git context it relies on.

## D-004 — Project Delta Instead of Transcript Dump

**Decision**

Save only durable changes relative to existing project state. Classify each item and relate it with `duplicate_of`, `updates`, `supersedes`, or `conflicts_with`.

**Rationale**

Copying full answers into a knowledge system creates a second unsearchable conversation archive rather than a usable project ledger.

**Consequences**

If a work cycle changes no canonical fact, state `No canonical project-state change`.

## D-005 — Notion + GitHub, No Asana Yet

**Decision**

Use Notion for requirements, decisions, architectural reasoning, risks, and snapshots. Use GitHub Issues/PRs for executable technical work, acceptance, and delivery evidence. Do not add Asana while these two systems are sufficient.

**Rationale**

A third task authority would increase synchronization cost and ambiguity without solving a current constraint.

**Review trigger**

Reconsider Asana only when multi-person scheduling, non-technical ownership, dependencies, and deadline management exceed GitHub/Notion capability.

## D-006 — Hardening Before Horizontal Expansion

**Decision**

The current order is:

```text
project governance
-> current-head verification
-> execution and permission correctness
-> CI, security, concurrency, and performance gates
-> v1.5 RC
-> reviewed merge
```

**Rationale**

The product already has substantial breadth. Reliability and authority-chain correctness now create more value than another horizontal feature.

**Consequences**

New feature proposals remain `Proposed` unless they close a current release gate or are explicitly reprioritized by the project owner.

## D-007 — Summarize Then Expire Raw Codex Sessions

**Decision**

Completed Codex session history may be deleted only after its durable Project
Delta is captured in versioned project documents, reviewed Memory, Git
evidence, or the AgentOps MIS ledger. The current active task and an explicitly
chosen recovery window must remain. Session cleanup uses metadata only and must
not inspect or copy raw JSONL bodies.

**Rationale**

Raw session archives consume substantial local storage but are not an authority
for current project state. Keeping every transcript duplicates knowledge,
weakens privacy boundaries, and does not improve runtime retrieval.

**Consequences**

- Deleting a session removes the ability to resume that raw conversation.
- Git code, approved Memory, MIS evidence, and project snapshots remain intact.
- A retention acceptance record must name the retained active task, aggregate
  deletion scope, and post-cleanup verification without storing conversation
  content.
