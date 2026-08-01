# AgentOps MIS UI V2 — Action-first design packet

Status: `Proposed`
Canonical: `false`
Evidence date: `2026-08-01`

This directory records a reviewable UI/UX research and interaction packet for the existing AgentOps MIS application. It does **not** introduce another runtime, state store, or standalone product surface.

## Contents

- `AGENTOPS_MIS_UI_V2_RESEARCH_AND_PLAN.md` — audit, target information architecture, migration concept, work packages, risks, acceptance criteria, and stop conditions.
- `../../agent_plans/UI_V2_ACTION_FIRST_ADOPTION_PLAN_20260801.md` — governed adoption sequence and acceptance gates.

The recovered prototype, screenshots, and prototype-verification record remain separate candidate artifacts. They are intentionally not committed in this bounded documentation-only recovery PR.

## Authority boundary

Any prototype referenced by this packet is a design artifact. It cannot create, approve, mutate, or certify Task, Plan, Run, Tool Call, Approval, Artifact, Evaluation, Memory, or Audit state. Existing MIS APIs and authority semantics remain first-party and unchanged.

## Relationship to the earlier UI V2 line

This packet updates the direction represented by Draft PR #11 (`design/gemini-ui-v2-implementation`, historical head `0fe500d0a21bb02d6d18de3eac864d4b1af7667b`). It does not use that stale development base for new implementation. Implementation should begin from the verified current `main` after WP0 route/API/permission inventory.

It coexists with Spatial/Pixel Office PR #23 and does not claim to implement the commercial Next.js migration in PR #110.
