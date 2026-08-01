# Agent Plan — UI V2 action-first adoption

Status: `Proposed`
Canonical: `false`
Evidence date: `2026-08-01`

## Preflight

Repository: `geogejoy107-jpg/agentops-mis-mvp`
Verified base branch: `main`
Verified base commit: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
Recovery docs branch: `docs/ui-v2-action-first-20260801`
Historical UI V2 branch: `design/gemini-ui-v2-implementation`
Historical UI V2 PR/head: Draft PR `#11` @ `0fe500d0a21bb02d6d18de3eac864d4b1af7667b`

This recovery PR is documentation-only. It preserves PR #11 as historical implementation evidence, coexists with Spatial/Pixel Office PR #23, and does not claim to implement or replace the commercial Next.js line in PR #110.

## Goal

Reduce navigation, decision, and evidence-retrieval cost in the human control plane while preserving all backend execution, authentication, approval, worker, runtime, audit, redaction, and database semantics.

## Approved-decision alignment

- D-001: GitHub, MIS Ledger/API, and Notion retain separate authority.
- D-002: candidate Memory remains non-authoritative until reviewed.
- D-003: exact repository/branch/commit preflight is mandatory.
- D-004: Notion receives only the durable Project Delta, not this full packet.
- D-005: GitHub carries execution artifacts; Notion carries the reviewed requirement/handoff.
- D-006: UI work must not weaken current P0 correctness or hardening gates.

## Scope

1. WP0 route, API, permission, and screenshot inventory on the exact branch head.
2. Semantic tokens, shared primitives, and a compatibility-preserving application shell.
3. Action-first Command Center.
4. Unified Work destination and evidence-oriented Task/Run detail.
5. Unified Review queue for Approvals, Evaluations, and Memory candidates.
6. Evidence, Workforce, and Integrations grouping.
7. Optional Pixel Office projection with no canonical state ownership.
8. Responsive, keyboard, legacy-route, and exact-head build verification.

## Out of scope

- database/schema changes;
- backend or runtime changes;
- approval or exact-resume semantic changes;
- automatic Memory promotion;
- destructive actions;
- replacement of the Vite application framework in this work package;
- treating the standalone prototype as production UI.

## Acceptance criteria

- At most six primary operating destinations.
- Pending approval, failed/blocked Run, and stale Worker each reachable from Command Center in one click.
- A Run detail page exposes Goal → Plan → Execution → Approval → Artifact → Evaluation → Handoff with exact branch/commit, plan binding, stop condition, verifier result, and artifact/evaluation readback.
- Prepared Action review shows normalized args, action hash, policy, risk, expiry, side-effect state, and exact-resume status before approval.
- Candidate Memory cannot be visually mistaken for approved Memory.
- Every legacy route has an explicit redirect or retained destination and a test.
- Keyboard-only primary scenarios pass; visible focus and minimum pointer-target rules are met.
- Vite production build, deterministic UI smoke, diff check, and secret scan pass on the exact PR head.
- Final verifier is independent of the implementer.

## Risks

- Current `main` has materially evolved since the historical UI V2 branch.
- Route grouping may hide permission assumptions or expert forensic surfaces.
- Broad home-page changes may duplicate or bypass current BFF/read-model behavior.
- Approval presentation changes could accidentally weaken high-risk action context.
- Pixel Office de-emphasis may be misread as removal.

## Stop conditions

Stop and re-plan when:

- branch HEAD differs from the plan-bound SHA before implementation;
- a route/API/permission contract for a modified surface is unknown;
- UI code begins owning MIS authority state;
- Prepared Action or exact-resume evidence is weakened;
- a legacy route cannot be preserved during staged migration;
- exact-head build/smoke fails;
- implementation and final verification are performed by the same worker.

## Work-package order

`WP0 inventory → WP1 shell/tokens → WP2 Command Center → WP3 Work/Run detail → WP4 Review → WP5 Evidence/Workforce/Integrations → WP6 rollout verification`

## Notion Project Delta

```yaml
type: Requirement
title: Replace fragmented MIS UI with an action-first control plane
status: Proposed
canonical: false
priority: P1
module: Web UI / Information Architecture
repository: geogejoy107-jpg/agentops-mis-mvp
base_branch: main
base_commit: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
updates:
  - historical UI V2 direction in Draft PR #11
  - Pixel Office proposal line
  - P1-05 oversized module decomposition
duplicate_of: null
supersedes: null
conflicts_with: null
next_action: Create the fresh governed branch and complete WP0 only before broad implementation.
```
