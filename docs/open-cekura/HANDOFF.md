# OpenCekura Windows v0 Handoff

Status: active implementation; this document is updated with measured evidence at each milestone.

## Immutable start context

```text
Repository: geogejoy107-jpg/agentops-mis-mvp
GitHub Issue: #123
Base branch: main
Base / starting commit: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Working branch: feat/open-cekura-windows-v0
Starting working tree: clean
Operating system: Windows
Canonical Notion spec: 3b96adfd-d920-81cf-9f99-d2990deea005
```

## Authority and architecture

OpenCekura is an AgentOps MIS vertical product. MIS remains authoritative for Task, Plan, Run, ToolCall, Evaluation, Artifact/Evidence, Memory, Approval, and Audit. Reliability-domain rows retain stable `mis_*` mappings. The API reuses `/mis-api`, Human Auth, workspace visibility, session, CSRF, and role checks. The UI is a feature of `ui/start-building-app`, not a second frontend project.

## Pre-implementation audit findings

- The starting branch contained no OpenCekura or Reliability Lab implementation.
- `agentops_mis_core/research_experiments.py` is the closest proven vertical-product integration pattern.
- The executable SQLite schema is `server.py::SCHEMA_SQL` plus migrations; `sql/schema.sql` is reference material.
- `/mis-api/*` already aliases `/api/*`; the Vite API helper already prefixes `/mis-api`.
- Generic Windows CLI and Gateway startup were initially blocked by eager import of POSIX `agentops_mis_cli.host` and `fcntl`.
- Existing CI was Ubuntu-centric and used Bash-specific command blocks; Windows support requires a dedicated cross-platform job rather than a runner-label substitution.
- Node.js 22.23.2 and npm 10.9.8 were installed and verified locally for this development lane.

## Milestone evidence

Milestone results, exact commits, campaign IDs, test counts, evidence verification, gate decisions, UI build output, PR number, and GitHub Actions run are appended only after the corresponding commands have completed. Unmeasured items are stated as not yet evidenced, never as passing.

At document creation:

- Phase 0 fresh-clone equivalent preflight: evidenced.
- Architecture and authority audit: evidenced.
- Product/contracts implementation: in progress.
- Domain through release gate: not yet evidenced.
- API/UI/Windows CI: not yet evidenced.
- Final PR and exact CI run: not yet created.

## Required final acceptance record

The closing revision of this file must include:

- exact final branch and commit;
- clean/dirty working-tree state;
- Python/Node/npm/Git doctor output summary without secret values;
- exact unit and integration commands with results;
- baseline and candidate campaign IDs and reproducible comparison;
- baseline blocker list and candidate PASS decision;
- evidence bundle locations and verification results, including tamper-negative proof;
- regression case IDs and replay result;
- MIS Task/Plan/Run/Evaluation/Artifact/Memory/Approval/Audit mappings;
- API smoke and Reliability Lab UI evidence;
- Ubuntu and Windows GitHub Actions run URL/ID and conclusion;
- PR number/URL targeting `main`;
- known limitations and v0.2 voice scope.

The implementation agent must not merge the PR.
