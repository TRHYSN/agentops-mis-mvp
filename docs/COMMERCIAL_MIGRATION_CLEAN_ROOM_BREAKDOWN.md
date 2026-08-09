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

As of 2026-07-31:

- Lane 0 is implemented. Production and shared Vite builds resolve only to the
  Next.js `/api/mis` control plane and fail at build time if Python proxy mode,
  `/mis-api`, credential-bearing URLs, or insecure remote HTTP are selected.
  Free Local retains the explicit loopback Python compatibility path.
- Lane 1 has a checksum-pinned thirteen-migration manifest, schema contract v11,
  transactional TypeScript runner/readiness ownership, exact catalog
  fingerprinting, and real PostgreSQL 16 bootstrap and contract coverage.
  Production uses distinct migrator, restricted runtime, and entitlement-admin
  identities plus a derived passwordless `NOLOGIN` owner for all application
  `SECURITY DEFINER` functions and bounded API wrappers. Runtime startup rejects
  schema owners, over-privileged DSNs, function-owner attribute or membership
  drift, any non-allowlisted database object ownership, schema `CREATE`, and
  wrapper ownership drift.
- Lane 2 has direct TypeScript/PostgreSQL owners for Agent identity, sessions,
  task claim, Agent Plans, runs, and governed evidence. The commercial
  TypeScript Worker uses those HTTP owners and has no Python, SQLite, or direct
  database dependency.
- Lane 3 owns customer-delivery request creation and Human Session approval
  decisions, including workspace, CSRF, immutable replay, and sealed evidence
  checks.
- Lane 4 owns PreparedAction creation, approval binding, execution leases,
  terminal receipts, and reconciliation gates.
- The frozen-source acceptance harness requires complete real Hermes and real
  OpenClaw Human review flows against one source fingerprint. Passing receipts
  require the TypeScript Worker, no Python Worker or Python API,
  `provider_call_performed=true`, and `dry_run=false`.
- Lane 5 now has direct Human and Agent task, run, artifact, and evidence-graph
  read owners. Commercial Vite builds use the Next `/api/mis` transport and
  Human Session CSRF authority for review and enrollment mutations; Free Local
  retains its explicit compatibility transport.
- Lane 6 has direct Human enrollment create/list/revoke/rotate, approval-gated
  request/decision/issue, session lifecycle, one-time hash-only credentials,
  and PostgreSQL workspace entitlement/quota ownership. Enrollment,
  child-session, and run-start writes fail closed with committed denial audit
  evidence. A distinct entitlement-admin CLI applies policy with Human
  authentication plus CSRF, a short-lived single-use database challenge,
  revision guards, a workspace advisory lock, and append-only audit; the
  long-running runtime cannot plan/apply or directly mutate entitlements, and
  the admin role cannot issue challenges or access application relations.
  PostgreSQL rechecks live database-role and Human authority at claim time.
  Cost reservation, heartbeat, terminal settlement, historical
  UTC billing, active-run upgrade preflight, and exact `NUMERIC(18,6)`
  projections are contract-covered.
- Lane 7 has a hardened BYOC package and executable promotion path: separate migrator/runtime/admin
  secrets, a non-root Node.js runtime, PostgreSQL 16 Compose topology, one-shot
  migration and entitlement administration, direct TypeScript/PostgreSQL
  readiness, atomic custom-format backup publication, stable isolated restore,
  cleanup-on-failure behavior, catalog fingerprint verification, and
  supply-chain gates. The top-level `BYOC Customer Release Acceptance` runs for
  exact commercial integration branch pushes, publishes two exact-source
  immutable OCI images, and transfers a
  checksum-manifested source-free customer bundle to a separate no-checkout
  runner. It contains no Dockerfile, application source, package-manager input,
  Git metadata, or credentials. Using only that bundle plus immutable image
  digests, the consumer executes packaged install, committed backup, isolated
  restore, retained-volume same-schema apply, and backup-authoritative rollback.
  A separate workflow covers historical v9-to-v11 forward migration with
  backup-authoritative rollback.
  Each promotion candidate must still pass all three BYOC evidence lanes,
  exact-head CI, and the manual same-SHA dual-runtime acceptance.

Database function ownership is now separated from migration authority. Before
pending migrations execute, the transaction grants the migrator temporary
membership in the derived function-owner role so existing owner-bound functions
remain upgradeable. It revokes that handoff after migration, transfers all
application `SECURITY DEFINER` functions and bounded API wrappers during
provisioning, then revokes schema `CREATE` and role membership again before
commit. Re-provisioning is idempotent, and PostgreSQL 16 contracts prove LOGIN,
membership, and unexpected object-ownership drift fail closed and recover only
after the catalog boundary is restored.

Historical receipts never transfer to a later commit. Every final candidate
must rerun exact-head CI, all three real BYOC workflows, strict promotion evidence,
and same-SHA dual-runtime acceptance before release, handoff, or merge authority
can be asserted.

Final closure still requires final same-SHA dual-runtime acceptance and
exact-head CI, plus Lane 7 clean-customer image installation, retained-data
upgrade/rollback, and an external restore drill before promotion.

## Definition Of Done

The migration is complete only when every production mutation and required read
has a TypeScript/PostgreSQL owner, all Python production fallbacks are removed
or explicitly disabled, customer installation and rollback are proven, and the
same frozen commit passes both real Hermes and OpenClaw closed-loop acceptance.
