# Agent Plan — OpenCekura Windows v0

> GitHub Issue: `#123`
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Base branch: `main`
> Plan-creation base: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
> Work branch: `feat/open-cekura-windows-v0`
> Risk: `medium`
> Approval required: `false` for the explicitly authorized implementation; PR merge remains owner-controlled
> Owner authorization: user explicitly requested continuous implementation, verification, GitHub/Notion evidence, and an unmerged PR

## Task understanding

Implement the full Windows-supported OpenCekura Reliability Lab v0 vertical slice. The work must begin from GitHub `main`, reuse the existing MIS authority, provide deterministic appointment reliability evidence, support Windows and Ubuntu CI without secrets, integrate the existing Vite workspace, and conclude with a reviewable PR rather than a merge.

## Referenced specs

- `docs/open-cekura/SOURCE_SPEC.zh-CN.md`
- `docs/open-cekura/PRODUCT_SPEC.md`
- `docs/open-cekura/ARCHITECTURE.md`
- `docs/open-cekura/EVALUATION_CONTRACT.md`
- `docs/open-cekura/EVIDENCE_CONTRACT.md`
- `docs/open-cekura/RELEASE_GATE_CONTRACT.md`
- `docs/open-cekura/WINDOWS_DEV_RUNBOOK.md`
- `docs/PUBLIC_CLAIMS_AND_LIMITATIONS.md`
- `docs/project/PROJECT_STATE.md`
- `docs/project/DECISIONS.md`
- `docs/project/BACKLOG.md`
- `docs/project/HANDOFF.md`
- `README.md`
- `AGENTS.md`

## Referenced memories and external facts

- Canonical Notion spec `3b96adfd-d920-81cf-9f99-d2990deea005`.
- GitHub Issue `geogejoy107-jpg/agentops-mis-mvp#123`.
- GitHub `main` is the code/CI authority; Notion records product status and handoff; MIS records governed execution evidence.
- Starting Windows audit found eager POSIX `fcntl` imports blocking the generic CLI and Gateway.

## Proposed files to change

- New `open_cekura/` vertical package and tests.
- New `examples/open-cekura/` appointment demo and scenarios.
- New `docs/open-cekura/` contracts and handoff.
- New `ui/start-building-app/src/features/open-cekura/` feature.
- Minimal existing integrations in `server.py`, generic CLI import routing, Vite app/sidebar, `.gitignore`, and GitHub Actions.
- Dedicated OpenCekura dependency/acceptance files.

The existing Task, Run, Approval, Audit, Human Auth, session, owner, and workspace authority schemas are not replaced.

## Relationship check

- `implements`: GitHub Issue #123 and the canonical OpenCekura Windows spec.
- `extends`: the existing MIS vertical-product pattern demonstrated by Research Lab.
- `supports`: deterministic release evidence, Windows developer adoption, Evaluation/Artifact/Memory/Approval readback, and future v0.2 voice adapters.
- `duplicate_of`: none found in the starting repository.
- `conflicts_with`: any design that creates a second ledger, frontend, auth/session system, or rewrites Private Host/Relay for Windows. Those approaches are excluded.

## Execution steps

1. Freeze local product/contracts and linked Notion implementation records.
2. Use TDD to remove only generic Windows eager-import blockers and establish MIS dogfood evidence.
3. Build strict versioned domain and Scenario v1 contracts.
4. Build deterministic Mock/HTTP simulation and the 10+ appointment suite.
5. Build explainable deterministic evaluation and optional SKIPPED judge.
6. Build atomic tamper-evident run/campaign bundles.
7. Build failures, regression generation/replay, comparison, and release policy.
8. Persist vertical objects with stable MIS authority mappings.
9. Add CLI and authenticated `/mis-api/reliability/*` integration.
10. Add Windows doctor/path/process utilities.
11. Add the lazy read-only Reliability Lab feature to the current Vite workspace.
12. Add Ubuntu/Windows × Python 3.10/3.11 CI and full acceptance.
13. Review, publish, open the PR, monitor/fix CI, record exact evidence, and stop before merge.

Detailed test-first steps and commands are in `docs/superpowers/plans/2026-08-11-open-cekura-windows-v0.md`.

## Verification plan

- All required objects serialize with stable IDs/schema/timestamps/parents.
- Invalid/unsupported Scenario v1 documents fail fast.
- Mock/HTTP adapters satisfy the async contract and deterministic replay.
- Every required evaluator explains failures with turn/tool/expectation/evidence references.
- Missing judge credentials yield SKIPPED; deterministic CI needs no secret.
- Artifact hashes verify and tampering fails.
- FailureCase becomes a complete replayable RegressionCase.
- Gate policy naturally blocks Baseline and passes Candidate.
- Vertical rows have real MIS mappings and create no shadow ledgers.
- API reuses existing auth/visibility and omits secrets.
- UI loads live evidence through existing Vite/MIS surfaces.
- Windows doctor critical checks pass.
- Local unit/integration/CLI/API/UI acceptance passes.
- GitHub Actions Ubuntu/Windows Python 3.10/3.11 and UI build pass.
- HANDOFF records exact final branch, commit, PR, and CI run.

## Rollback plan

Do not merge the PR. Revert feature commits in reverse phase order or close the feature PR. OpenCekura vertical tables/files can be abandoned without deleting or rewriting authoritative MIS ledger history. Any MIS dogfood Task/Plan/Run/Evidence records remain immutable audit evidence and may be marked superseded, not erased. Notion task status is updated with the rollback reason and last verified commit.
