# Commercial Evidence Packet Index

## Purpose

This index is the Lane 4 starting point for the commercial migration clean-room
plan. It defines which operator evidence packets may be generated for promotion
review, where each packet must derive evidence from, and which inputs are
forbidden. It is an index only: no packet in this document claims hosted,
billing, cleanup, Postgres, or commercial deployment readiness.

## Authority Boundary

- Source branch: rebuild from current `origin/main`.
- PR #22 status: reference evidence only; do not merge or copy the branch as a
  whole.
- Authority system: AgentOps MIS first-party ledgers, release gates, CI checks,
  command manifests, and acceptance docs.
- Packet posture: read-only evidence projection until a dedicated generator and
  smoke prove the packet from current source.
- Promotion posture: exact current-head CI is required before any promotion
  packet may claim readiness.

## Forbidden Inputs

Evidence packet generators must not read, store, or commit:

- raw logs
- raw prompts
- raw model responses
- private messages or full transcripts
- credentials, tokens, `.env`, or secret-bearing config
- local SQLite DBs or DB dumps
- `node_modules`, `dist`, caches, or generated export snapshots
- stale packet snapshots copied from PR #22

## Packet Inventory

| Packet | Purpose | Source Of Truth | Current Status | Required Smoke |
| --- | --- | --- | --- | --- |
| Current Evidence Status | Summarize current local MVP gates and known commercial limits. | `docs/V1_5_MERGE_READINESS_CHECKLIST.md`, `docs/RELEASE_EVIDENCE_PACKET.md`, current git/CI readback. | generator smoke added | `commercial_current_evidence_status_smoke.py` |
| Release Evidence Packet | Emit exact SHA, CI state, canonical commands, and release posture. | `scripts/release_evidence_packet_smoke.py`, `.github/workflows/ci.yml`, GitHub Actions readback. | existing generator | `release_evidence_packet_smoke.py` |
| Commercial Handoff Status | Show which clean-room lanes are complete, blocked, or queued. | `docs/COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md`, lane acceptance docs, merged PR history. | generator smoke added | `commercial_handoff_status_smoke.py` |
| Promotion Preflight | Check whether a branch can be promoted to review without unsafe claims. | current `HEAD`, `git status`, branch control smoke, secret scan, release evidence smoke. | generator smoke added | `commercial_promotion_preflight_smoke.py` |
| Promotion Packet | Bundle exact-head evidence after CI and preflight pass. | current source, current-head CI, release evidence, acceptance docs. | generator smoke added | `commercial_promotion_packet_smoke.py` |
| Receipt Plan | Define the human review receipt expected before risky commercial changes. | approval/receipt docs, prepared-action inventory, release freeze protocol. | generator smoke added | `commercial_receipt_plan_smoke.py` |
| Receipt Recording | Record review receipts without executing billing, cleanup, hosted, or live runtime actions. | AgentOps MIS receipt/audit objects and redacted metadata only. | generator smoke added | `commercial_receipt_recording_smoke.py` |
| Rerun Bundle Preview | List deterministic commands needed to reproduce packet evidence on another machine. | release command manifest, CI workflow, acceptance docs. | generator smoke added | `commercial_rerun_bundle_preview_smoke.py` |
| Confirmed Receipt Recording | Explicitly record commercial review receipts into an isolated local ledger. | existing operator receipt CLI/API, temp SQLite DB, acceptance docs. | generator smoke added | `commercial_confirmed_receipt_recording_smoke.py` |
| Prepared Action Receipt Binding | Bind a recorded commercial review receipt to an exact Approval Wall prepared-action hash. | prepared_actions, operator receipt CLI/API, temp SQLite DB, acceptance docs. | generator smoke added | `commercial_receipt_prepared_action_binding_smoke.py` |
| Prepared Action Execution Receipt | Require a consumed prepared action before recording a post-action commercial receipt. | prepared_actions exact resume, operator receipt CLI/API, temp SQLite DB, acceptance docs. | generator smoke added | `commercial_prepared_action_execution_receipt_smoke.py` |

## Generator Rules

1. Generate packets from current source at runtime. Do not commit stale rendered
   packet output as the source of truth.
2. Include exact `git rev-parse HEAD` and upstream/dirty-state readback where a
   packet makes branch or promotion claims.
3. Require current-head green CI before any packet says a branch is ready to
   promote or release.
4. Store hashes, paths, counts, and safe summaries only. Omit raw logs,
   prompts, responses, secrets, DB contents, and full transcripts.
5. Keep commercial claims explicit and negative until the dedicated gate exists:
   no billing provider call, no destructive cleanup, no hosted readiness, no
   Postgres dependency for local MVP, and no live runtime execution.
6. Add one packet generator at a time, with a focused smoke, release-evidence
   manifest entry, CI wiring, and an acceptance note.

## Next Implementation Slice

Use `python3 scripts/commercial_current_evidence_status_smoke.py` as the first
generator. It reads only current tracked docs and command manifests, emits a
redacted JSON summary, and fails if it finds unsafe readiness claims or stale
generated packet output. The next generator should be
`commercial_handoff_status_smoke.py`.

Use `python3 scripts/commercial_handoff_status_smoke.py` as the handoff packet.
It reads the packet index, clean-room breakdown and acceptance docs, then emits
safe lane and packet status without running live systems. The next generator
should be `commercial_promotion_preflight_smoke.py`.

Use `python3 scripts/commercial_promotion_preflight_smoke.py` as the promotion
preflight packet. It checks clean working-tree state, upstream sync, exact-head
CI, exact-SHA BYOC Compose/cross-schema/customer-release evidence,
branch-control, secret-scan and release-evidence command wiring without running
live systems. The customer-release lane is a top-level exact-branch push
workflow with manual reruns available after it reaches the default branch, so
package-write is never granted to ordinary PR CI. The next generator should be
`commercial_promotion_packet_smoke.py`.

Use `python3 scripts/commercial_promotion_packet_smoke.py` as the promotion
packet. It bundles current-source evidence references after preflight while
keeping commercial readiness claims negative until later gates exist. The next
generator should be `commercial_receipt_plan_smoke.py`.

Use `python3 scripts/commercial_receipt_plan_smoke.py` as the receipt plan
packet. It defines the human review receipt and prepared-action requirements for
risky commercial changes without recording receipts or executing actions. The
next generator should be `commercial_receipt_recording_smoke.py`.

Use `python3 scripts/commercial_receipt_recording_smoke.py` as the receipt
recording packet. It materializes preview-only receipt requests, normalized
action arguments and hashes without mutating ledgers or executing commercial
actions. The next generator should be `commercial_rerun_bundle_preview_smoke.py`.

Use `python3 scripts/commercial_rerun_bundle_preview_smoke.py` as the rerun
bundle preview. It lists deterministic reproduction commands for the commercial
evidence packet chain without executing the bundle, mutating receipts or
running live systems.

Use `python3 scripts/commercial_confirmed_receipt_recording_smoke.py` as the
confirmed receipt recording packet. It starts an isolated local MIS server with
a temporary SQLite database, previews a receipt without writing, then records
the five commercial review receipts through the existing operator receipt
CLI/API while keeping action execution and live commercial side effects false.

Use `python3 scripts/commercial_receipt_prepared_action_binding_smoke.py` as the
prepared-action receipt binding packet. It starts an isolated local MIS server,
creates one high-risk prepared action, proves receipt preview is dry-run, proves
missing prepared actions fail closed, and then records one receipt bound to the
exact prepared action hash without executing the action.

Use `python3 scripts/commercial_prepared_action_execution_receipt_smoke.py` as
the prepared-action execution receipt packet. It starts an isolated local MIS
server, proves pre-consumption receipts fail closed, approves and resumes one
prepared action through the existing exact-resume path, and records a receipt
requiring `consumed` status with provider side-effect readback while keeping
commercial/live execution false.
