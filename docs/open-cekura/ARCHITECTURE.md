# OpenCekura Reliability Lab v0 Architecture

Status: frozen implementation boundary
Repository baseline: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`

## Architectural decision

OpenCekura is a vertical package in the AgentOps MIS monorepo. It owns reliability-domain models and projections while reusing the authoritative MIS ledgers and the existing Vite workspace.

```text
YAML Scenario Suite
        │
        ▼
open_cekura simulation ──► AgentAdapter (Mock or HTTP)
        │                         │
        │                         └─ observed replies/tool calls/timing
        ▼
deterministic evaluation ──► failure/regression ──► release gate
        │
        ├─► OpenCekura vertical SQLite rows (domain/query projection)
        ├─► MIS Task/Plan/Run/ToolCall/Evaluation/Artifact/Memory/Approval/Audit
        └─► atomic Evidence Bundle + manifest hashes
                                      │
                                      ▼
                       /mis-api/reliability/*
                                      │
                                      ▼
                  existing Vite Reliability Lab feature
```

## Existing components reused

- `server.py` remains the stdlib HTTP Gateway and executable SQLite schema authority.
- `server.db()`, `db_session()`, and `sqlite_atomic_write()` supply foreign keys, busy timeout, WAL, synchronous policy, connection lifecycle, and short transactional writes.
- Core MIS tables remain `tasks`, `agent_plans`, `runs`, `tool_calls`, `approvals`, `memories`, `evaluations`, `artifacts`, `audit_logs`, and `plan_evidence_manifests`.
- The Research Lab vertical integration in `agentops_mis_core/research_experiments.py` is the structural precedent: vertical tables retain MIS IDs while core ledgers remain authoritative.
- `Handler.api_path` already maps `/mis-api/*` to `/api/*`.
- Existing Human Auth, workspace visibility, role checks, CSRF, Agent Gateway scopes, and Audit chaining are reused without a second login/session/owner model.
- `ui/start-building-app` remains the only frontend project. Its `apiJson` helper already prefixes `/mis-api`; Reliability clients pass `/reliability/...` paths.

## Package boundaries

```text
open_cekura/domain        versioned domain objects, enums, stable IDs
open_cekura/scenarios     strict Scenario v1 validation and loading
open_cekura/simulation    adapter contracts, mock/HTTP execution, personas
open_cekura/evaluation    deterministic rules, aggregation, optional judge
open_cekura/regression    failure normalization, clustering, replay cases
open_cekura/release_gate  comparison metrics and gate policy
open_cekura/evidence      atomic bundles, manifests, SHA-256 verification
open_cekura/storage       repository protocol and MIS-mapped SQLite adapter
open_cekura/api           reliability read/write service functions and schemas
open_cekura/cli           Windows-safe command tree
open_cekura/windows       doctor, path, socket, and bounded process helpers
```

Dependency direction is toward domain contracts. Domain and scenario modules do not import `server.py`. The CLI composes repositories and services. The server lazily imports the reliability route adapter so a base AgentOps installation can still start when optional OpenCekura validation dependencies are absent.

## Persistence design

Vertical tables represent agents, versions, suites, scenarios, campaigns, runs, turns, observed tool calls, evaluations, failures, clusters, regressions, release gates, and evidence manifests. Each table uses stable IDs, schema versions, timestamps, and explicit parent foreign keys. MIS-backed objects additionally store their `mis_*` mapping.

Writing a campaign follows this order within bounded transactions:

1. create or resolve the authoritative MIS Task and Agent Plan;
2. create the vertical campaign mapped to them;
3. create each MIS Run and vertical ConversationRun;
4. persist turns and map every observed call/evaluation/artifact to its MIS record;
5. propose failures/regressions as candidate MIS Memory;
6. persist the explanatory release decision and its MIS Quality Gate/Approval mapping;
7. emit Audit/evidence-manifest links.

Failure to create an authoritative mapping is recorded explicitly. It is not hidden by fabricating an ID. Test repositories may use an isolated SQLite file but must initialize the same MIS schema plus the vertical schema.

Gate recomputation treats filesystem bundles as evidence, never as authority.
Before creating a new MIS Approval, the campaign service reconstructs the typed
campaign hierarchy and reconciles exact Turn, ToolCall, Run, Evaluation,
Failure, Regression/Memory, Manifest, Artifact, PlanEvidence, Gate, Approval,
Audit-chain, and current-Gate-head sets with both vertical projection and core
MIS. Any missing, extra, stale, or conflicting fact aborts the transaction.
Campaign evidence schema v2 preserves create-once gate snapshots under
`gates/<gate_id>/`; a hash-linked index selects the current root aliases without
deleting prior decisions.

SQLite and the filesystem are joined by a recoverable publication protocol, not
by an impossible cross-resource atomic transaction. A complete verified
campaign generation is staged under a same-volume private slot, sealed by a
durable tree-hash journal, and linked to a publication outbox row inside the MIS
transaction. Same-volume directory swaps expose the generation. Recovery uses
the committed outbox as authority to restore the old tree or finish and reverify
the new tree before cleanup. The journal is bound to a persistent random
SQLite-instance identity rather than its pathname. A filesystem-normalized
campaign slot and a process-lifetime OS file lease serialize the exact final
namespace across workspaces and Windows case aliases. A dead writer's unsealed
stage is reclaimable only after that lease can be acquired; a live writer and a
sealed journal with missing or replaced SQLite authority both fail closed.

## Stable IDs and serialization

IDs use a type prefix plus a deterministic digest for replay-derived objects. New user-level entities may use a generated stable ID supplied once and persisted. Re-running the same campaign request with an explicit idempotency key must not create duplicate ledger objects.

All serialized timestamps are UTC ISO-8601 with a `Z` suffix. JSON is UTF-8, sorted where hashing requires canonical bytes, and never relies on Python object `repr`. Hash inputs and algorithm versions are part of the contract.

## API integration

Reliability endpoints are added to the existing `Handler` route tree and auth boundary:

- `/api/reliability/overview`
- `/api/reliability/agents`
- `/api/reliability/scenario-suites`
- `/api/reliability/campaigns`
- `/api/reliability/campaigns/<id>`
- `/api/reliability/runs/<id>`
- `/api/reliability/failures`
- `/api/reliability/regressions`
- `/api/reliability/release-gates`

The browser addresses the equivalent `/mis-api/reliability/*` paths. List endpoints are bounded and filterable. Run Detail is one aggregate read model containing context, trace, evaluations, failures, regressions, gate impact, evidence references, and stable MIS mappings. It does not expose credentials, raw hidden prompts, session/token values, or unredacted private payloads.

## UI integration

`App.tsx` mounts one lazy `/workspace/reliability/*` feature. `Sidebar.tsx` adds one `Reliability Lab` item under the workspace group. Feature-local routes provide Overview, Agents, Scenario Suites, Campaigns, Campaign Detail, Run Detail, Failures, Regressions, and Release Gates.

Run Detail uses a responsive three-column evidence view:

- left: run/agent/campaign/scenario context and turn timeline;
- center: user/agent transcript and selected trace events;
- right: tool calls, evaluations, failures, gate impact, and evidence hashes;
- top: PASS/FAIL, turns, latency, tool-call count, and evaluator count.

The feature uses existing theme tokens, Human Auth behavior, CSRF-aware API helper, Lucide icons, and shared status components. It adds no frontend project and no implicit mock-data fallback.

## Windows boundary

All new code uses `pathlib.Path`, `tempfile`, Python sockets, argv-list subprocess calls, and same-directory atomic replacement. No core flow depends on Bash, POSIX process commands, `/tmp`, `~/.local`, chmod semantics, systemd, or LaunchAgent.

The existing POSIX Private Host remains outside the v0 rewrite scope. On Windows, generic CLI and Gateway imports must not eagerly import `agentops_mis_cli.host` or its `fcntl`-based dependencies. POSIX-only commands stay fail-closed with an explicit unsupported-platform error when invoked.

## Failure semantics

- Scenario contract violations fail before a run is created.
- Adapter errors and deterministic evaluator errors are evidence-bearing failures, never PASS.
- A missing LLM key yields `SKIPPED`, never a fake score or hidden provider fallback.
- Hash verification fails on any changed, missing, unexpected, or mismatched artifact covered by the manifest.
- Release gates are derived from evaluation and comparison facts, never from a campaign name or ID.
- Partial MIS integration is reported as a mapping failure; it does not create a shadow ledger.
