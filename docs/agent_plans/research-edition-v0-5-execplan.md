# Research Edition v0.5 Living ExecPlan

Status: Proposed
Canonical: false
Plan ID: `RE-V05-PLAN-001`
Base: `origin/main@99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
Branch: `codex/research-v05-reconcile`
Worktree: `agentops-mis-research-v05-reconcile`
Updated: 2026-08-09

MIS binding: Task `tsk_re_v05_l00_99ce51d`; Agent Plan `plan_336c6f572cdcd906`; Run `run_gw_e5fcd972a353`; verified MIS plan hash `e2a9bdd8339e10e16bd169a4f1ff32d61fc8aa2e5a092875e8f057a2e7cb0d6f`.

## Authority and intent

The user authorized isolated reconciliation, planning, local candidate implementation, tests, documentation, and evidence work by saying “开始” with the Research Edition execution package attached. This starts candidate work; it does not promote the package, this plan, or its output to canonical product state.

Git/GitHub remains authoritative for source, SHA, PR, CI, and release state. MIS remains authoritative for Task, Agent Plan, Run, Approval, Artifact, Evaluation, Memory Review, and Audit state. Reviewed project ledgers remain authoritative for accepted project decisions. The attached package and this plan are candidate inputs until separately reviewed.

## Goal

Evolve the existing Research Lab 0.4.1 into an evidence-first Research Edition candidate that can use a pinned openJiuwen agent-core runtime without transferring MIS authority, and that can eventually supervise durable local and authorized remote research jobs across agent-session and worker restarts.

## Non-goals

- Do not create a second MIS authority ledger.
- Do not replace the existing Research Lab or silently flatten all research objects into generic MIS objects.
- Do not fork all of openJiuwen or make JiuwenSwarm the control plane.
- Do not claim durable recovery, SSH/GPU execution, scientific validity, or merge readiness without the corresponding gate evidence.
- Do not merge, release, promote memory, change production state, use credentials, or run GPU workloads under this plan.

## Loaded inputs

| Input | SHA-256 / exact ref | Authority |
|---|---|---|
| Research Edition ZIP | `1caa0d0de411faf524229d70a435eef23c70e2d80daa557fb5547eb0ba4a82e1` | Candidate |
| `PROJECT_GOAL.yaml` | `cc4513d1…` | Candidate |
| Product and architecture spec | `d86c7d88…` | Candidate |
| Candidate context | `1ddd26ce…` | Candidate |
| Repository `AGENTS.md` | `4971e440db4c57b49bc22a39395384bd833777f0869eefc55bf92d906b05ddf5` | Instruction |
| `docs/project/DECISIONS.md` | `b127e8cffad70f6717bd59ca24b050897615a88a4e292b684c8ccb15fbc668dd` | Reviewed ledger, stale operational snapshot |
| `PROJECT_SPEC.md` | `5ff9fbf7b9b91b50c4ba3a617882320e08fe192e2aab6df6415c3fb69b78e239` | Project contract, priority snapshot stale |
| `AGENT_WORKFLOW.md` | `8e28013ebb394cadc1851c6d99354efc6fb8dacc0880cd67c82d57ef10d70ccc` | Workflow contract |

## Gate 0 reconciliation

| Concern | Status | Verified result |
|---|---|---|
| Exact main | PASS | `99ce51d`; current-head GitHub CI passed. |
| Existing checkout | PASS_WITH_CONDITIONS | Clean, but 294 commits behind and 3 commits ahead; never use it as the implementation base. |
| Existing Research Lab | PASS | 0.4.1 has local Trial/Attempt execution, bounded evidence ingest, read-only API/UI, and BWFormer CPU smoke. |
| Durable jobs | FAIL | Local execution waits in the originating process; no durable handle, heartbeat, restart reconcile, or exactly-once resume. |
| SSH/GPU | NOT_RUN | OpenSSH execution truthfully returns `remote_unknown`; no authorized NVIDIA environment is available. |
| Native research domain | PASS_WITH_CONDITIONS | Experiment/Trial/Attempt/Metric exist; Contract, Checkpoint, Claim, EvidenceEdge, RuntimeSnapshot, independent review, and invalidation do not. |
| openJiuwen | FAIL | Registry says `PILOT / NOT_INTEGRATED`; no dependency, import, or adapter exists on main. |
| Embedded template PR #118 | PASS_WITH_CONDITIONS | Useful candidate design, but conflicts with current Experiment→Task mapping and its new test is not discovered by CI. |
| Adaptive planning candidate | UNKNOWN | No source, branch, or implementation was found. |
| Canonical priority | PASS_WITH_CONDITIONS | D-006 remains accepted; this is isolated candidate work authorized by the user, not canonical promotion. |
| MIS dogfood | PASS_WITH_CONDITIONS | Task/Plan/Run write scopes are available; Project/Goal and Commander planner routes require a Human Session and cannot be fabricated. |

Gate 0 result: `PASS_WITH_CONDITIONS`. The exact gaps are explained; unknowns remain explicit.

## Frozen architecture decisions

1. MIS is the only authority for tasks, runs, approvals, evidence, memory review, and audit.
2. Embedded MIS evolves through an additive hybrid research domain. Existing Experiment/Trial/Attempt rows remain compatible; new first-class rows are added only where independent lifecycle and queryability require them.
3. `ResearchProject` is not invented while Core MIS has no native Project/Goal table. Candidate Project/Goal/Requirement references use a narrow, bounded authority reference until a real Core object exists.
4. A Research Contract has a stable identity plus immutable versions. Rejected review creates a terminal `rejected` version; revision creates a new version and hash.
5. JobAttempt cancellation uses `cancel_pending → cancelled`. Ambiguous provider results enter reconciliation and never count as successful cancellation or trigger blind resubmission. Research spelling `cancelled` maps explicitly to core Task spelling `canceled`.
6. Existing client `claim_eligible` is advisory. MIS must recompute readiness; acceptance additionally requires an independent reviewer bound to the exact evidence-set hash.
7. The first openJiuwen spike uses agent-core commit `bf0a3eb2c70fcbae404403530519ca02e7fc4692` in a managed Python 3.11 subprocess with a narrow JSONL or authenticated-loopback protocol. No root dependency or JiuwenSwarm integration is authorized by this decision.
8. openJiuwen checkpoints are runtime-private, trusted-storage inputs. They do not replace Research Checkpoint artifacts or MIS recovery evidence.

## Milestones and owned boundaries

| Lane | Milestone | Owner boundary | Entry gate | Exit evidence |
|---|---|---|---|---|
| L00 | Reconciliation and planning | `docs/agent_plans`, board, Gate 0 receipt | Exact main | Plan hash and MIS readback |
| L01 | MIS dogfood bootstrap | Existing CLI/API only; no second ledger | L00 | Task/Plan/Run IDs and hashes |
| L02 | openJiuwen compatibility spike | New isolated incubator package/docs/tests; no domain schema | L01 | Pin, lock, license, minimal agent, event/permission/cancel/resume tests |
| L03 | Native research domain | Pure domain, repository, additive migration, contract tests | L01 | State/hash/migration/concurrency tests |
| L04 | Durable local executor | Process handle, heartbeat, log cursor, reconcile; consumes L03 repository | L03 | Conversation-exit and worker-restart integration receipts |
| L05 | Evidence pipeline | v1/v2 ingest, checkpoints, metric snapshots, claims, invalidation | L03 | Negative tests and independent-review gate |
| L06 | Event-driven runtime | openJiuwen event translation and context packets | L02 + L03 + L05 | Runtime snapshot/recovery and bounded budgets |
| L07 | Embedded UI | Existing AppShell/routes; no new authority | L03 + L04 + L05 | UI build, API readback, screenshots |
| L08 | SSH/GPU | Existing connector/profile boundary; no credentials in Git/MIS | L04 + approval | Authorized UAT or explicit pending receipt |
| L09 | BWFormer proof | Existing adapter plus verified contracts | L05 + L08 as applicable | Bounded research receipt; claims limited to evidence |
| L10 | Independent acceptance | Read-only verifier paths | All candidate gates | Exact-head CI and independent review artifacts |

Only one integration owner may edit `server.py` at a time. L03 owns research schema extraction during its lease; L05 receives `research_experiments.py` only after that lease ends. UI, runtime, and executor lanes do not edit domain schema.

## Gate sequence

- G0 Current State: `PASS_WITH_CONDITIONS`.
- G1 Plan: `PASS`; Task and 100/100 verified Agent Plan were read back with exact hashes.
- G2 Dogfood: `PASS_WITH_CONDITIONS`; a plan-bound Run was read back. Commander Project/Goal remains unavailable to this agent because that route requires a Human Session, so no Project/Goal ID was fabricated.
- G3 openJiuwen Spike through G12 Final: `NOT_RUN`.

`feature_write_allowed=true` only for separately owned Wave 1 candidate branches after each receives its own Task/Plan/Run binding. It does not authorize implementation on this reconciliation branch, merge, release, dependency installation, GPU, credentials, or external side effects.

## Wave 1

Wave 1 contains two non-overlapping candidate slices after G2:

1. L02 compatibility harness: dependency manifest, license/NOTICE boundary, narrow runtime protocol, fake worker contract tests, and an optional real agent-core execution only after its network/install action is separately reviewed.
2. L03 domain foundation: pure state/hash contracts and additive migration design with legacy preflight. It must not yet change API/UI or launch jobs.

## Verification

At minimum for every branch:

- touched unit/contract tests;
- `git diff --check`;
- secret and generated-runtime-file checks;
- existing Research Lab tests and three exact-main smokes;
- UI build when UI paths change;
- independent verifier who did not implement the slice;
- exact changed-path audit against the lane lease.

## Rollback and stop conditions

Rollback candidate source with isolated commits or worktree removal. Additive evidence tables are never destructively down-migrated after writes; behavior rolls back behind a feature flag while evidence remains readable.

Stop and request direction for: main moving before branch integration; unexplained dirty files; duplicate active implementation; authority moving outside MIS; unpinned or incompatible upstream dependencies; required credentials/GPU/external writes; approval-required action; failed legacy migration preflight; or a claim whose evidence cannot be independently verified.

## Change log

- 2026-08-09: Gate 0 synthesized from four read-only reports. Architecture decisions frozen for candidate planning. No canonical state changed.
- 2026-08-09: G1/G2 bound to live MIS. Two earlier Agent Plan submissions remain as audit history: one exposed date-path over-redaction and one exposed installed-repository reference resolution. Replacement plan `plan_336c6f572cdcd906` passed all checks with quality 100/100.
