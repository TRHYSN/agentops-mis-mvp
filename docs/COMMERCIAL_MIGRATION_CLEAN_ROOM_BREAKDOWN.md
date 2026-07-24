# Commercial Migration Clean-Room Breakdown

## Final Product Boundary

The commercial control plane is a first-party AgentOps MIS service with this
production stack:

- Next.js 16 App Router
- TypeScript on Node.js 20 or newer
- PostgreSQL 16
- explicit Hermes and OpenClaw runtime adapters
- Human Session, RBAC, approval, audit, and evidence authority owned by MIS

Python and SQLite remain supported only for Free Local, deterministic test
orchestration, migration verification, and an explicitly selected rollback
path. A production or shared deployment must never proxy a control-plane
mutation to Python or treat SQLite as commercial authority.

## Source Strategy

The historical commercial migration branch represented by PR #22 diverged
before current `main` and is an evidence/reference lane. Do not merge PR #22 directly.
Do not rebase its full history onto current main.

Every production owner is rebuilt as a small commit from current `origin/main`.
The integration sequence is:

1. prove the production boundary fails closed
2. add explicit PostgreSQL schema and migration ownership
3. add one TypeScript route owner at a time
4. prove the route without a Python upstream
5. run real Hermes and OpenClaw acceptance on a frozen source commit
6. promote only after current-head CI, upgrade, rollback, and BYOC gates pass

Old code may inform behavior and negative tests, but old schema assumptions,
receipt shapes, authentication shortcuts, and generated artifacts are not
authoritative.

## Non-Negotiable Rules

- Production writes are Next.js/TypeScript/PostgreSQL only.
- Do not copy generated docs, DB files, caches, `node_modules`, `dist`, `.env`,
  local SQLite files, or secret-bearing configuration from the old branch.
- Unknown production routes return a bounded fail-closed response.
- Free Local Python proxying uses an explicit allowlist and loopback binding.
- Production startup requires a valid PostgreSQL DSN and current schema
  checksum; it cannot silently fall back to Python or SQLite.
- Agent credentials may request Human review but may not approve, reject,
  assign an approver, or forge Human audit attribution.
- Workspace, task, run, agent, plan, manifest, approval, and prepared-action
  bindings are checked at every ownership boundary.
- Raw prompts, responses, transcripts, provider output, credentials, tokens,
  and private messages are never committed as evidence.
- Mock evidence is CI fallback only. Product-readiness claims require real,
  explicitly confirmed Hermes and OpenClaw provider calls.
- Release, handoff, and merge authority remain false until all listed gates are
  current for the exact source commit.

## Migration Lanes

### Lane 0: Runtime Boundary

Own the commercial Next entry point, explicit deployment mode, loopback-only
Free Local startup, and production Python-proxy denial.

Exit gate:

- production build starts through the packaged command
- unknown production reads and writes fail closed
- upstream Python receives zero production requests
- Free Local proxies only allowlisted operations

### Lane 1: PostgreSQL Schema And Startup

Own an explicit current-main baseline, ordered migrations, checksums,
transactional locking, readiness checks, backup, restore, and BYOC bootstrap.

Exit gate:

- fresh PostgreSQL 16 bootstrap passes
- migration reapply is idempotent
- checksum/version drift fails closed
- ambiguous historical data rolls back atomically
- production startup cannot bypass readiness

### Lane 2: Agent Identity And Plans

Own Agent Gateway token/session authentication, workspace and scope binding,
task/run lifecycle, Agent Plan submission/verification, and evidence manifests.

Exit gate:

- token and session values are hash-only at rest
- current plan version, plan hash, verification timestamp, and verification
  result hash bind the run and manifest
- cross-workspace and cross-agent requests fail closed
- no Python process is needed

### Lane 3: Customer Delivery And Human Review

Own customer-delivery approval requests, Human Session/RBAC/CSRF decisions,
replay behavior, and task/read-model transitions.

Exit gate:

- only completed real Hermes/OpenClaw runs with current verified evidence can
  create a pending delivery approval
- one run has at most one customer-delivery approval
- Agent self-approval is impossible
- Human decisions are same-origin, session-bound, workspace-scoped, and audited

### Lane 4: Prepared Actions

Own preparation, Human approval, claim leases, provider execution receipts,
response-loss reconciliation, and terminal no-retry behavior.

Exit gate:

- `action_id`, action hash, plan, approval, task, run, and requester bindings
  are immutable
- concurrent claims have one durable winner
- success, failure, and unknown outcomes are append-only
- an outcome that may have executed cannot be retried automatically
- raw provider output is omitted

### Lane 5: Read Models And Supervision

Own workspace task/run detail, run graph, tool/evaluation/artifact/audit reads,
operator supervision, Memory review, and evidence packets.

Exit gate:

- every read is workspace-authoritative
- machine credentials cannot impersonate Human actors
- browser workflows have complete production-owned replacements
- retired Python routes have parity evidence

### Lane 6: Enrollment And Entitlements

Own enrollment request/approval, one-time token issue, session lifecycle,
commercial entitlements, quotas, and policy decisions.

Exit gate:

- issued tokens are shown once and stored hash-only
- concurrent issue has one winner
- entitlement denial is fail-closed and audited
- safe Human rejection remains available during entitlement failure

### Lane 7: Deployment And Promotion

Own container/BYOC packaging, configuration, upgrade, backup/restore, rollback,
observability, retention, release evidence, and promotion.

Exit gate:

- a clean customer environment installs without repository-local state
- upgrade and rollback preserve authority data
- current-head GitHub CI and supply-chain receipts pass
- frozen-source real Hermes and OpenClaw acceptance passes
- no release claim depends on an old branch or mock runtime

## Current Integration State

As of 2026-07-24:

- Lane 0 is implemented. Production and shared Vite builds resolve only to the
  Next.js `/api/mis` control plane and fail at build time if Python proxy mode,
  `/mis-api`, credential-bearing URLs, or insecure remote HTTP are selected.
  Free Local retains the explicit loopback Python compatibility path.
- Lane 1 has a checksum-pinned ten-migration manifest, schema contract v9,
  transactional TypeScript runner/readiness ownership, and real PostgreSQL 16
  bootstrap and contract coverage.
- Lane 2 has direct TypeScript/PostgreSQL owners for Agent identity, sessions,
  task claim, Agent Plans, runs, and governed evidence. The commercial
  TypeScript Worker uses those HTTP owners and has no Python, SQLite, or direct
  database dependency.
- Lane 3 owns customer-delivery request creation and Human Session approval
  decisions, including workspace, CSRF, immutable replay, and sealed evidence
  checks.
- Lane 4 owns PreparedAction creation, approval binding, execution leases,
  terminal receipts, and reconciliation gates.
- Frozen commit `d3b9e73` passed the complete real Hermes and real OpenClaw
  Human review flow against the same source fingerprint. Both receipts reported
  the TypeScript Worker started, no Python Worker or Python API started,
  `provider_call_performed=true`, and `dry_run=false`.
- Lane 5 now has direct Human and Agent task, run, artifact, and evidence-graph
  read owners. Commercial Vite builds use the Next `/api/mis` transport and
  Human Session CSRF authority for review and enrollment mutations; Free Local
  retains its explicit compatibility transport.
- Lane 6 remains partial. Direct Human enrollment create/list/revoke/rotate,
  session list/revoke, one-time hash-only credentials, and PostgreSQL workspace
  entitlement/quota evaluation are owned. New enrollment, child-session, and
  run-start writes fail closed with committed denial audit evidence. A trusted
  local TypeScript/PostgreSQL operator CLI now plans and applies entitlement
  configuration with Human credential verification, revision guards, and
  append-only audit. Approval-gated enrollment request/decision/issue and final
  cost-reservation policy are still open.
- Lane 7 has an initial BYOC package: a non-root Node.js image definition,
  PostgreSQL 16 Compose topology, one-shot migration dependency, direct
  TypeScript/PostgreSQL readiness, and custom-format backup/isolated restore
  drill scripts. Atomic backup publication, behavior-tested restore cleanup,
  complete schema-invariant verification, actual clean-customer image
  installation, retained-data upgrade/rollback, supply-chain receipts, and
  final promotion remain open.

Commits after `d3b9e73` are not covered by that frozen-source runtime receipt.
Release, handoff, and merge authority remain false until the remaining read,
enrollment, entitlement, deployment, and promotion gates pass and the final
source commit is rerun through both real runtimes.

The next slices are the remaining Lane 6 approval-gated enrollment and hard
cost-reservation policy owners, followed by Lane 7 clean-customer image
installation, retained-data upgrade/rollback, and final same-SHA dual-runtime
promotion.

## Definition Of Done

The migration is complete only when every production mutation and required read
has a TypeScript/PostgreSQL owner, all Python production fallbacks are removed
or explicitly disabled, customer installation and rollback are proven, and the
same frozen commit passes both real Hermes and OpenClaw closed-loop acceptance.
