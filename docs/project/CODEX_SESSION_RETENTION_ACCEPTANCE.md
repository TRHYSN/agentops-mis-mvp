# Codex Session Retention Acceptance

> Status date: 2026-07-23
> Scope: local Codex session JSONL retention only
> Content inspection: prohibited and not performed

## Purpose

Codex session history is a recovery archive, not AgentOps MIS project memory.
This acceptance record allows completed session files to expire after their
durable Project Delta has been captured in versioned project documents,
approved Memory, Git evidence, or the MIS ledger.

## Safety Boundary

- Retain the current active task:
  `019ec0ec-14bf-7243-a145-8b5659f0193a`.
- Do not read JSONL bodies to classify cleanup candidates.
- Do not delete Git repositories, project documents, MIS databases, approved
  Memory, Docker data, OpenClaw state, credentials, or current task state.
- Use date, path, file count, byte size, and active-task identity only.
- Delete in one bounded batch, then verify the active task file still exists.
- If a session is deleted, that conversation can no longer be resumed from its
  raw history. Its durable project facts must already exist elsewhere.

## Pre-Cleanup Inventory

```text
Codex sessions total: 48,306,900 KiB
Selected completed dates: 2026-07-18, 2026-07-19, 2026-07-22, 2026-07-23
Selected JSONL files: 180
Selected allocated size: 41,101,400 KiB
Filesystem available: 1,826,868 KiB
Active task date: 2026-06-13
```

The selected date directories do not contain the active task identity. The
current task file is retained independently and checked after cleanup.

## Durable Context Captured Before Expiry

- [`CURRENT_CONTEXT_SNAPSHOT.md`](./CURRENT_CONTEXT_SNAPSHOT.md) records the
  current branch, committed source, real runtime evidence, installed/source
  boundary, uncommitted reliability work, CI truth, and open gates.
- [`DECISIONS.md`](./DECISIONS.md) records the authority split, Project Delta
  rule, and summarize-then-expire retention decision.
- Git history and AgentOps MIS evidence remain the authorities for code and
  runtime claims.
- No raw conversation content is copied into project memory.

## Post-Cleanup Acceptance

```text
Selected JSONL files remaining: 0
Codex sessions total after cleanup: 7,221,944 KiB
Allocated session-tree space reclaimed: 41,084,956 KiB (about 39.2 GiB)
Filesystem available after cleanup: 40,640,440 KiB (about 38.8 GiB)
Active task retained: yes
Docker data changed: no
OpenClaw state changed: no
MIS database changed: no
Raw session content inspected or copied: no
```

Result: accepted. The active task file remained present and continued updating
after cleanup. The selected completed session dates contain zero JSONL files.
Git code, project documents, governed Memory, and MIS evidence were not part of
the deletion command.
