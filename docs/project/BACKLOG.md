# AgentOps MIS Prioritized Backlog

> Commercial migration note (2026-07-24): the v1.5 backlog below remains the
> Free Local baseline. Commercial / BYOC migration priority is the lane order
> and Definition of Done in
> `docs/COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md`; it does not target
> Python/SQLite as the production stack.

> Status date: 2026-06-22
> Development line: `codex/agent-gateway-kb-demo`
> Current release status: `READY_TO_MERGE`
> Exact release SHA source: `git rev-parse HEAD` plus strict release scripts.

## Status Vocabulary

```text
Next | Verify Current Head | Ready | In Progress | Blocked | Done | Keep Green | Superseded
```

## P0 — Release and Authority Correctness

| ID | Work item | Status | Acceptance evidence |
|---|---|---|---|
| P0-00 | Re-audit the exact latest development head | Done | `docs/V1_5_MERGE_READINESS_CHECKLIST.md` is `READY_TO_MERGE`; strict gates read the current `git rev-parse HEAD` at runtime |
| P0-01 | Separate Agent Plan authoring from approval | Keep Green | Guarded by `scripts/agent_plan_integrity_smoke.py` and `scripts/approval_semantics_boundary_smoke.py` |
| P0-02 | Hard-bind a verified immutable Agent Plan to Run and Delivery | Keep Green | Guarded by `scripts/run_start_plan_gate_smoke.py`, `scripts/operator_evidence_report_smoke.py`, and release checklist plan-evidence gates |
| P0-03 | Validate plan reference provenance and visibility | Keep Green | Guarded by `scripts/agent_plan_integrity_smoke.py`, `scripts/knowledge_scope_policy_smoke.py`, and `scripts/agent_gateway_knowledge_scope_smoke.py` |
| P0-04 | Close Prepared Action approval and exact resume | Keep Green | Guarded by prepared-action and external connector/runtime inventory smokes listed in `docs/V1_5_AGENT_GATEWAY_HARDENING_OBJECTIVE.md` |
| P0-05 | Unify redaction and shared-deployment authentication guards | Keep Green | Guarded by redaction, secret scan, doctor, and shared-mode local-write guard smokes |
| P0-06 | Replace approximate collaborator and workspace visibility checks | Keep Green | Guarded by workspace isolation and scoped reviewable-list smokes |
| P0-07 | Add Knowledge workspace/ACL isolation and retrieval provenance | Keep Green | Guarded by knowledge scope, retrieval quality, and search policy smokes |
| P0-08 | Establish SQLite concurrency baseline | Keep Green | Guarded by `scripts/sqlite_pragmas_smoke.py` and `scripts/sqlite_concurrency_smoke.py` |
| P0-09 | Establish CI, secret scan, SBOM, license, and release evidence | Keep Green | Required PR checks `Backend deterministic smokes` and `UI build` must be green on the exact current HEAD; strict release evidence gates remain required after any new commit |
| P0-10 | Install durable project governance in repository and Notion | Done | `AGENTS.md`, project state/decision/backlog/handoff files, Project Ledger views, repo-local skill, and governance PR #6 exist |
| P0-11 | Install the prepared ChatGPT Project Instructions | Blocked | Project owner pastes the versioned template into ChatGPT Project Instructions and confirms a new project chat follows it |

## P1 — Productization After P0

| ID | Work item | Status | Acceptance evidence |
|---|---|---|---|
| P1-01 | Aider-style repository map and task localization | Done | `GET /api/commander/repo-map`, `agentops commander repo-map`, `operator loop-launch-packet`, Commander work-package `commander_repo_map_localization` artifacts, and CI-backed `commander_repo_map` / `commander_work_package_plan` / `commander_work_package_dispatch` release commands provide deterministic file/symbol candidates, task-bound localization artifacts, dispatch-time restoration, provenance, redaction, and merge-gate evidence |
| P1-02 | Local Coding Project Template | Done | `GET /api/commander/coding-project-template`, `agentops commander coding-template`, `tpl_local_coding_project`, `agentops commander coding-workspace`, `agentops commander coding-evidence`, `scripts/commander_coding_project_template_smoke.py`, `scripts/commander_coding_workspace_smoke.py`, and `scripts/local_coding_project_template_smoke.py` now expose the WorkPackage/worktree/patch/tests/verifier/merge-gate contract, create Commander packages with repo-map localization artifacts, execute an isolated worktree loop, write summary/hash-only coding evidence back to MIS, and clean branch/worktree residue |
| P1-03 | Command Center BFF | Done | `GET /api/operator/command-center`, `agentops operator command-center`, and CI-backed `scripts/operator_command_center_smoke.py` provide one read-only operator BFF for projects, blocked runs, approvals, deliveries, stale workers, Commander coding evidence gates, and prioritized next actions; Commander writes clear read-model cache so the command center reflects newly dispatched/recorded evidence immediately |
| P1-04 | Runtime Capability Manifest | Done | `GET /api/runtime-connectors`, `agentops runtime connectors`, `GET /api/workers/adapter-readiness`, and CI-backed `scripts/runtime_capability_manifest_smoke.py` prove Agent Gateway, OpenClaw, Hermes and Agnesfallback connectors expose filesystem, shell, network, Git, secret, external-write, confirmation, trust-policy, observation, risk-floor and governance manifests without live execution or token/raw prompt/raw response leakage |
| P1-05 | Split oversized horizontal modules | In Progress | Slices 1-46 extract runtime connector capability policy, registry row/upsert helpers, refresh row projection, trust state read/update helpers, read-model cache behavior, Approval Wall prepared-action hash/public/readback, resume-gate mismatch, waiting-response, route blocked/error, route access/error, prepare-response, prepared-action decision projection, high-risk tool-call required response, risky tool registry, external side-effect intent detection, metadata-only false-positive guard, resume-success, provider result reconciliation and next-action helpers, Agent Plan approval-decision/create-status/bound-approval-forbidden/transition-error/run-start gate/rebind/success response projections, run-start binding comparison, plus contract/hash/path-scope/verification-result/pending-approval/approval-run helpers, Agent Gateway run-heartbeat update projection, Evaluation Case public projections, worker fleet status/fleet-view aggregation plus hygiene and remote-fleet public token/session-omitting projections, workflow-job public/list/stuck/recovery response projections, Commander work-package readback aggregation, Commander project-board gate aggregation, Operator command-center gap/project/stale-ref/status aggregation, Operator evidence-report memory/status/summary projection, Operator start-check gate/local-run-path/launch-brief/loop-driver-entry/acceptance-packet/agent-loop-packet/local-loop-admission projections, Operator loop-control summary/gate projections, and Operator receipt/evaluation/control-readback public projections into `agentops_mis_runtime/capabilities.py`, `agentops_mis_runtime/connectors.py`, `agentops_mis_runtime/trust.py`, `agentops_mis_core/read_model_cache.py`, `agentops_mis_core/approval_wall.py`, `agentops_mis_core/agent_plans.py`, `agentops_mis_core/gateway_runs.py`, `agentops_mis_core/evaluation_cases.py`, `agentops_mis_core/worker_fleet.py`, `agentops_mis_core/workflow_jobs.py`, `agentops_mis_core/commander_work_packages.py`, `agentops_mis_core/operator_command_center.py`, `agentops_mis_core/operator_evidence.py`, `agentops_mis_core/operator_start_check.py`, `agentops_mis_core/operator_loop_control.py`, and `agentops_mis_core/operator_receipts.py`; server keeps HTTP routes, health probing, SQLite upsert orchestration, runtime/audit writes, auth checks and route identity inputs, SQLite producers, local daemon/process discovery, runtime adapter readiness, workflow-job reads/writes/list filters/count producers/status transitions/threshold-limit normalization/status validation/stuck-job recovery routing, tool-call row construction/upsert, risk default application, provider-specific prepared-action creation, provider calls and side-effect id construction, Approval Wall exact-once resume invocation/writes, route-level hash-mismatch audit writes, Agent Plan DB-backed memory/base checks, run-start plan lookup/matching/approval/hash checks, rebind-blocked audit writes, run-start row upserts/status-code selection, heartbeat redaction/parsing/default selection and run/task/agent writes, stable approval run ids/timestamps, verification persistence, status transitions and approval row insertion, evaluation-case candidate/run SQLite reads/writes/execution/review/remediation, scoped auth/bound-token detection, fleet hygiene mutations, Commander planning/dispatch/synthesis writes, human review queue reads, operator receipt/evaluation/control-readback SQLite reads/writes and final response assembly, operator loop-control SQLite/ladders/final response assembly, final operator evidence report/start-check response assembly, and remote enrollment launch-step assembly. Guarded by `scripts/module_boundary_smoke.py` plus runtime capability/readiness/trust, read-model cache, prepared-action Approval Wall, runtime probe/Dify/Notion/customer-worker prepared-action gates, generic external side-effect and connector/runtime inventory gates, workflow jobs list/poll and stuck recovery smokes, Agent Plan integrity, Agent Gateway scoped-read/workspace-isolation smokes, evaluation case candidate and module-boundary smokes, worker status/fleet/hygiene, Commander project-board, Operator command-center, Operator evidence-report, Operator start-check, remote enrollment launch packet, Operator loop-control, Operator action receipt/readback, and Commander work-package smokes; continue with small strangler slices only |
| P1-06 | Knowledge chunking, retrieval evaluation, Repo Map, then hybrid search | In Progress | P1-06a adds a read-only Knowledge Retrieval Evidence Packet through `GET /api/knowledge/evidence-packet`, `GET /api/agent-gateway/knowledge/evidence-packet`, `GET /api/agent-gateway/knowledge/retrieval-evidence-packet`, `agentops knowledge evidence-packet`, `/api/local/readiness`, and `operator loop-launch-packet`; P1-06b makes `agentops-worker` consume a compact packet before adapter execution, include packet/query hashes, retrieval ids, paths, source hashes and metrics in Agent Plan references plus tool/evaluation/audit metadata, and keep raw query/snippet/content/prompt/response/token omitted; P1-06c makes missing/unavailable worker knowledge retrieval fail the worker evaluation/plan-evidence quality gate while still recording adapter run/tool/audit evidence, lets authorized workers safely build the local knowledge index before retrying packet retrieval, defaults worker enrollment scopes to `knowledge:read`/`knowledge:write`, and surfaces worker retrieval readiness as an Operator Evidence Report gate with ready/missing/unavailable summary counts; guarded by `scripts/knowledge_retrieval_evidence_packet_smoke.py`, `scripts/worker_knowledge_evidence_consumption_smoke.py`, `scripts/operator_evidence_report_smoke.py`, `scripts/knowledge_retrieval_quality_smoke.py`, `scripts/agent_gateway_knowledge_scope_smoke.py`, and `scripts/operator_loop_launch_packet_smoke.py`; vectors remain optional |

## P2 — Differentiation

- Research Lab Template and experiment/evidence workflow.
- GPU, server, dataset, model, experiment, and paper objects.
- JiuwenSwarm, LangGraph, CrewAI, and other runtime adapters.
- Agent/Skill/Template marketplace, hiring, evaluation, and billing.
- SaaS, BYOC, private deployment, and enterprise audit.

## Priority Change Rule

A new idea does not change priority merely because it is newly discussed. Any priority change must name:

1. the decision or evidence that changed;
2. the item it displaces;
3. the consequence of delaying the displaced item;
4. the owner who approved the change.

## Completion Rule

A backlog item is `Done` only when acceptance evidence exists on the exact relevant branch and commit. A `Keep Green` item is implemented but remains a release invariant: any new commit must keep its guard scripts and required CI checks passing.
