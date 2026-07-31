# Commercial Agent Gateway Core Owner Status

Status date: 2026-07-31

## Owned Production Boundary

The following `/api/mis` routes have specific Next.js route files and direct
TypeScript/PostgreSQL owners:

- `POST /agent-gateway/register`
- `POST /agent-gateway/session/create`
- `GET /agent-gateway/sessions`
- `POST /agent-gateway/session/revoke`
- `POST /agent-gateway/heartbeat`
- `GET /agent-gateway/status`
- `GET /agent-gateway/enrollments`
- `POST /agent-gateway/enrollment/create`
- `POST /agent-gateway/enrollment/request`
- `POST /agent-gateway/enrollment/issue-approved`
- `POST /agent-gateway/enrollment/revoke`
- `POST /agent-gateway/enrollment/rotate`
- `GET /agent-gateway/tasks/pull`
- `GET /agent-gateway/tasks/:taskId`
- `POST /agent-gateway/tasks/:taskId/claim`
- `POST /agent-gateway/agent-plans`
- `GET /agent-gateway/agent-plans/:planId/verify`
- `POST /agent-gateway/runs/start`
- `GET /agent-gateway/runs`
- `GET /agent-gateway/runs/:runId`
- `GET /agent-gateway/runs/:runId/graph`
- `POST /agent-gateway/runs/:runId/heartbeat`
- `POST /agent-gateway/tool-calls`
- `POST /agent-gateway/evaluations/submit`
- `POST /agent-gateway/artifacts`
- `GET /agent-gateway/artifacts`
- `POST /agent-gateway/plan-evidence-manifests`
- `POST /agent-gateway/runtime-events`
- `POST /agent-gateway/audit`
- `POST /agent-gateway/memories/propose`
- `POST /agent-gateway/knowledge/index`
- `GET /agent-gateway/knowledge/evidence-packet`
- `GET /agent-gateway/knowledge/retrieval-evidence-packet`
- `POST /agent-gateway/approvals/request`
- `POST /agent-gateway/prepared-actions`
- `GET|POST /agent-gateway/prepared-actions/:actionId/*`

Production execution uses the shared Agent Gateway token/session authority,
PostgreSQL transactions, row and advisory locks, workspace/agent/task/run
bindings, bounded JSON bodies, immutable replay checks, and the append-only
audit/runtime ledgers. These owners do not start or proxy to Python.

Python proxy compatibility is limited to an explicit
`AGENTOPS_DEPLOYMENT_MODE=free_local` plus
`AGENTOPS_CONTROL_PLANE_MODE=proxy` selection. It is not a production fallback.

## Plan And Receipt Binding

Agent Plan hashes use the current Python canonical JSON byte contract,
including `plan_version`. Verification persists `verified_at` and
`verification_result_hash`. Non-mock run start requires the current persisted
verification and writes both `runs.agent_plan_id` and `runs.plan_hash`.

Evidence writes recheck that run binding. Plan evidence manifests bind the
current plan hash and verification-result hash and reject caller-supplied stale
hashes. Artifact content is omitted; a lowercase SHA-256 digest is stored.
Sensitive JSON keys and token-like values are redacted before ledger storage.

## Acceptance

`agent-gateway-core-postgres-contract.ts` creates a disposable PostgreSQL
schema, applies the current migration runner, and covers:

- bearer token and child session authentication
- cross-workspace and cross-agent denial
- pull and two-agent claim concurrency
- Agent Plan create, canonical verification, and hash drift
- run-to-plan binding and cross-agent heartbeat denial
- tool, evaluation, artifact, and manifest immutable replay
- stale manifest plan-hash rejection
- raw prompt, raw response, and token omission
- zero requests to a configured Python upstream observer

`agent-gateway-core-production-boundary.ts` statically verifies the specific
route ownership, bounded bodies, explicit Free Local proxy switch, and absence
of Python process/proxy calls in production owners.

The schema contract is now `agentops_commercial_postgres_v11` with thirteen
checksum-pinned migrations. Workspace entitlement evaluation is serialized by
a transaction-scoped workspace advisory lock. New enrollment, child-session,
and run-start writes fail closed on missing, inactive, suspended, expired,
disabled, or exhausted entitlement and commit a bounded denial audit. Existing
idempotent writes replay before quota evaluation, enrollment rotation is
quota-neutral, and revocation remains available while entitlement is suspended.
Remote enrollment requests are authenticated as the requesting Agent with the
`approvals:request` scope. They create no credential and cannot create or
rewrite Agent identity. Only `workspace-admin` or `owner` Human Sessions can
decide and issue an approved request; issuance rechecks current entitlement,
returns the raw token once, and stores only its SHA-256 hash.

`enrollment-approval-gated-postgres-contract.ts` covers Agent self-request,
Human and cross-workspace rejection, admin-only decision and issue, concurrent
single-winner transitions, task/run/Agent/config drift, entitlement denial
evidence, one-time credential delivery, replay omission, and a database-wide
raw-token scan.

Entitlement administration uses a workspace-scoped TypeScript route to exchange
an authenticated, CSRF-bound Human Session for a database-backed challenge.
The challenge is bound to the canonical request, stores only the token SHA-256,
expires within 90 seconds, consumes the Human Session, and can be claimed once.
The runtime can only issue; the separate entitlement-admin role can only plan
or apply. PostgreSQL rechecks both database-role attributes and the operator's
current membership and credential at claim time, so role elevation or Human
authority revocation after issue fails closed.

Run start now requires a positive `estimated_cost_usd` and assigns
`started_at` from the PostgreSQL control-plane transaction. The same
transaction reserves concurrent-run, monthly-run, and monthly-cost capacity.
Running heartbeats cannot reduce cost or exceed the reservation and atomically
renew the bounded concurrency lease. Lease expiry does not erase monthly run or
estimated-cost usage, and an expired run can renew or settle without being
counted as a new monthly run. Gateway heartbeat, approval rejection,
PreparedAction success/failure/timeout, and enrollment management decisions all
close cost state in their owning transaction. Reservation estimate, observed,
and settled amounts plus the `runs.cost_usd` compatibility projection are
`NUMERIC(18,6)`. Terminal settlement advances both observed and settled cost to
the same authoritative receipt amount.

The v10 cost-authority migration refuses to begin while a non-enrollment
execution run is still `running` or `waiting_approval`. Operators must drain or
reconcile those runs before retrying the migration; the failed preflight leaves
the previous schema intact. Historical billing months are derived by parsing
stored timestamps and converting them to UTC before truncating to month.
Timestamps with an explicit offset retain that instant; legacy timestamps
without an offset are interpreted as UTC, independent of the PostgreSQL session
time zone.

The complete Human review acceptance also covers the first-party Human Session
owners for login, logout, current session, approval list/detail/decision,
candidate Memory review, and operator loop supervision.

Role-boundary provisioning derives a dedicated, passwordless `NOLOGIN`
function-owner identity from the application and runtime API schema names.
Each provisioning pass explicitly clears any password verifier on that role.
Before applying pending migrations, the same transaction validates or creates
the restricted owner and grants the migrator temporary membership, so a later
`CREATE OR REPLACE` can update functions already owned by that identity.
Membership and schema `CREATE` are removed after migration and again after the
bounded API wrappers and application `SECURITY DEFINER` ownership are rebuilt.
Runtime and entitlement-admin readiness verify the owner's restricted
attributes, zero memberships, schema privileges, a database-wide ownership
closed set that permits only the expected functions, and each wrapper's exact
body, fixed search path, owner, and executable grantee.
Re-provisioning also removes stale non-owner `EXECUTE` ACLs from application
`SECURITY DEFINER` functions, and readiness rejects any later ACL drift.
The runtime API schema is a closed eight-function set; an extra function,
owner, signature, or executable grantee fails both runtime and admin readiness.

Frozen commit `72a1b9f` passed the full real-runtime acceptance separately with
Hermes and OpenClaw against the same source fingerprint. Both runs used the
TypeScript Worker and PostgreSQL 16, performed a real provider call with
`dry_run=false`, and started neither the Python Worker nor Python API.

## Still Open

This candidate now includes direct TypeScript/PostgreSQL ownership and contract
coverage for approval-gated enrollment, workspace entitlement administration,
restricted database identities, exact cost projection, and BYOC packaging plus
backup/restore behavior. Release authority still requires:

- remaining browser dashboard, agent, connector, and deployment workflows
- a real external BYOC deployment, upgrade, rollback, and restore drill
- a clean exact-head Hermes plus OpenClaw acceptance receipt
- exact-head GitHub Actions and supply-chain evidence

Commits after `72a1b9f` require a new same-SHA Hermes/OpenClaw run before any
release claim. This status therefore records route ownership and prior frozen
runtime proof, not current-head commercial release authority.
