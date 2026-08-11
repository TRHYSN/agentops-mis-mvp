# OpenCekura Windows v0 Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Apply `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming completion.

**Goal:** Deliver a Windows-supported OpenCekura Reliability Lab vertical slice that closes Scenario → Simulation → ToolCall → Evaluation → Failure → Regression → Release Gate → Evidence → UI, maps every governed object into the existing MIS authority, passes Ubuntu/Windows CI, and opens an unmerged PR to `main`.

**Architecture:** Add a versioned `open_cekura` vertical package, strict YAML contracts, deterministic mock/HTTP simulation, explainable evaluation/gating, atomic evidence, MIS-mapped SQLite projections, existing-server routes, and a lazy feature in the existing Vite workspace. Reuse MIS Task/Plan/Run/ToolCall/Evaluation/Artifact/Memory/Approval/Audit and Human Auth; do not create a shadow ledger or frontend.

**Tech Stack:** Python 3.10/3.11, Pydantic v2, PyYAML, stdlib `asyncio`/`sqlite3`/`hashlib`/`pathlib`/`tempfile`/`socket`/`subprocess`, pytest, existing stdlib MIS server, React 18, React Router 7, Vite 6, GitHub Actions, PowerShell-safe commands.

---

## Baseline and invariant evidence

- Repository: `geogejoy107-jpg/agentops-mis-mvp`
- Issue: `#123`
- Base: `main@99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
- Work branch: `feat/open-cekura-windows-v0`
- Initial tree: clean
- Existing vertical precedent: `agentops_mis_core/research_experiments.py`
- Existing API alias: `/mis-api/*` → `/api/*`
- Initial Windows blocker: eager `agentops_mis_cli.host` import reaches POSIX-only `fcntl`

## Task 1: Freeze product contracts and development evidence

**Files:**

- Create: `docs/open-cekura/SOURCE_SPEC.zh-CN.md`
- Create: `docs/open-cekura/PRODUCT_SPEC.md`
- Create: `docs/open-cekura/ARCHITECTURE.md`
- Create: `docs/open-cekura/WINDOWS_DEV_RUNBOOK.md`
- Create: `docs/open-cekura/EVALUATION_CONTRACT.md`
- Create: `docs/open-cekura/EVIDENCE_CONTRACT.md`
- Create: `docs/open-cekura/RELEASE_GATE_CONTRACT.md`
- Create/update: `docs/open-cekura/HANDOFF.md`
- Create: `docs/agent_plans/2026-08-11-open-cekura-windows-v0.md`

**Execution:** preserve the full 0–29 source specification; document the MIS mapping, Windows constraints, strict schemas, evidence hashing, and natural gate policy. Create linked Notion execution plan/tasks. Record the initial inability to dogfood the local MIS CLI before the Windows import boundary is fixed.

**Verify:**

```powershell
git diff --check
python -c "from pathlib import Path; required=['PRODUCT_SPEC.md','ARCHITECTURE.md','WINDOWS_DEV_RUNBOOK.md','EVALUATION_CONTRACT.md','EVIDENCE_CONTRACT.md','RELEASE_GATE_CONTRACT.md','HANDOFF.md']; assert all((Path('docs/open-cekura') / name).is_file() for name in required)"
```

**Commit:** `docs: add OpenCekura product and Windows contracts`

## Task 2: Remove generic Windows import blockers with TDD

**Files:**

- Create: `open_cekura/tests/unit/test_windows_import_boundary.py`
- Modify: `agentops_mis_cli/cli.py`
- Modify: `server.py`

**RED:** add subprocess tests that make importing `agentops_mis_cli.host` fail deliberately, then prove generic CLI help and `import server` currently trigger it.

```powershell
python -m pytest open_cekura/tests/unit/test_windows_import_boundary.py -q
```

Expected: failures showing eager POSIX imports.

**GREEN:** lazy-load Host modules only for Host-specific commands/functions. Preserve POSIX behavior; return an explicit unsupported-platform error when a Host operation is invoked on Windows. Do not rewrite service/process management.

**Verify:** rerun the focused test, generic CLI help, `python -B -c "import server"`, and relevant existing CLI/server import smokes. Start an isolated loopback server and create a governed MIS Task + Agent Plan if the now-accessible path permits it.

## Task 3: Implement stable domain objects and strict Scenario v1

**Files:**

- Create: `requirements-open-cekura.txt`
- Create: `open_cekura/__init__.py`
- Create: `open_cekura/domain/{__init__.py,enums.py,ids.py,models.py}`
- Create: `open_cekura/scenarios/{__init__.py,schema.py,loader.py}`
- Create: `open_cekura/tests/unit/test_domain_serialization.py`
- Create: `open_cekura/tests/unit/test_scenario_schema.py`
- Create: `open_cekura/tests/fixtures/*.yaml`

**RED:** test all 15 required object types for stable IDs, schema/timestamps/parent IDs and canonical round trips. Test Scenario v1 success plus unknown field, invalid enum, missing field, and unsupported version failures.

**GREEN:** implement the smallest typed Pydantic contracts and fail-fast YAML loader. Never swallow `ValidationError` or replace typed objects with a giant blob.

**Verify:** focused tests, full unit subset, CLI validation of one valid and multiple invalid fixtures.

**Commit:** `feat: add reliability domain and scenario schema`

## Task 4: Implement deterministic simulation and appointment fixtures

**Files:**

- Create: `open_cekura/simulation/{__init__.py,agent_adapter.py,personas.py,runner.py,mock_agent.py,http_agent.py}`
- Create: `examples/open-cekura/appointment-agent/*`
- Create: `examples/open-cekura/scenarios/*.yaml` (10 or more)
- Create: `open_cekura/tests/unit/test_simulation.py`
- Create: `open_cekura/tests/integration/test_deterministic_campaign.py`

**RED:** specify async adapter lifecycle, observation ordering, deterministic repeatability, safe HTTP error/timeout behavior, and the ten appointment scenario classes.

**GREEN:** implement the mock backend/tools, Baseline/Candidate defect profiles, runner, and bounded stdlib HTTP adapter. Defects are behavior/config facts, never campaign-ID conditionals.

**Verify:** run each scenario twice and compare normalized outcomes; inspect transcripts/tool calls/final state; run timeout and invalid-response negative cases.

**Commit:** `feat: add deterministic conversation simulator`

## Task 5: Implement explainable evaluation and optional judge

**Files:**

- Create: `open_cekura/evaluation/{__init__.py,base.py,rules.py,aggregation.py,llm_judge.py}`
- Create: `open_cekura/tests/unit/test_deterministic_evaluators.py`
- Create: `open_cekura/tests/unit/test_llm_judge.py`

**RED:** one or more failing tests for every required evaluator, reason/evidence localization, deterministic error handling, aggregation, and missing-key `skipped` semantics.

**GREEN:** implement `task_success`, required/forbidden calls, duplicate mutation, confirmation before mutation, final-state match, turn limit, timeout, and optional judge provenance.

**Verify:** focused tests plus full campaign inspection. Assert no fake score/fallback/provider secret.

## Task 6: Implement atomic Evidence Bundles and tamper verification

**Files:**

- Create: `open_cekura/evidence/{__init__.py,bundle.py,manifest.py}`
- Create: `open_cekura/tests/unit/test_evidence_hashes.py`
- Create: `open_cekura/tests/integration/test_run_to_evidence.py`
- Modify: `.gitignore`

**RED:** define exact files, canonical hashes, manifest fields, path containment, and a negative test that changes one artifact byte and expects verification failure.

**GREEN:** implement same-directory temp write → flush/fsync → `os.replace`, SHA-256 manifests, campaign files, and offline verification.

**Verify:** generate evidence, verify PASS, copy/tamper one artifact, verify non-zero FAIL, then regenerate from replay.

**Commit:** `feat: add reliability evaluators and evidence bundle`

## Task 7: Implement failures, regressions, replay, and release gates

**Files:**

- Create: `open_cekura/regression/{__init__.py,builder.py,clustering.py}`
- Create: `open_cekura/release_gate/{__init__.py,policy.py,gate.py}`
- Create: `open_cekura/tests/unit/test_release_gate.py`
- Create: `open_cekura/tests/integration/test_regression_replay.py`

**RED:** test automatic FailureCase → normalized RegressionCase fields/replay and each BLOCK/WARN threshold. Test that campaign names/IDs cannot alter a decision.

**GREEN:** derive failures from evaluations, build cases containing original input/expected/observed/reason/source/evaluator, append cases to a suite, replay, compare campaign metrics, and emit explanatory gates.

**Verify:** Baseline blocks for observed zero-tolerance defects; Candidate passes; replay remains deterministic.

**Commit:** `feat: add regression and release gate`

## Task 8: Integrate vertical SQLite storage with MIS authority

**Files:**

- Create: `open_cekura/storage/{__init__.py,repository.py,sqlite_repository.py}`
- Create: `open_cekura/tests/integration/test_sqlite_mis_mapping.py`
- Modify: `server.py` only for vertical schema initialization/lazy route composition as needed

**RED:** create an isolated MIS database, persist a campaign, then assert stable mappings to Task/Plan, Run, ToolCall, Evaluation, Artifact, Memory, Approval/Quality Gate, and Audit. Assert there are no shadow core-ledger tables.

**GREEN:** follow the Research Lab precedent, use existing transactions/visibility, and store vertical objects with explicit `mis_*` columns.

**Verify:** foreign keys, idempotent replay, rollback on mapping error, short transactions, connection closure, API-independent repository readback.

## Task 9: Add the OpenCekura CLI and Reliability API

**Files:**

- Create: `open_cekura/cli/{__init__.py,main.py}`
- Create: `open_cekura/api/{__init__.py,schemas.py,routes.py}`
- Create: `open_cekura/tests/integration/test_cli.py`
- Create: `open_cekura/tests/integration/test_api_sqlite.py`
- Modify: `server.py`

**RED:** specify every required CLI command/exit code and authenticated API list/detail shape, `/mis-api` alias, workspace visibility, bounded queries, missing IDs, and secret omission.

**GREEN:** compose service/repository functions and lazily mount `/api/reliability/*` in the existing Handler. Reuse auth/session/RBAC/CSRF; do not add identity tables.

**Verify:** run all exact commands from the product spec, direct `/api` and aliased `/mis-api` smoke, viewer read/operator write boundaries, and SQLite readback.

**Commit:** `feat: add reliability API`

## Task 10: Add Windows doctor, paths, and bounded process helpers

**Files:**

- Create: `open_cekura/windows/{__init__.py,doctor.py,paths.py,process.py}`
- Create: `open_cekura/tests/unit/test_windows_paths.py`
- Create: `open_cekura/tests/unit/test_windows_process.py`
- Create: `open_cekura/tests/unit/test_windows_doctor.py`

**RED:** test Unicode/space paths, containment, drive/UNC/device/reserved-name rejection where applicable, timeout/process-tree cleanup behavior, socket bind, tool discovery, dirty state, and key redaction.

**GREEN:** implement only OpenCekura's cross-platform utilities. Generic Host/Relay platform expansion stays out of scope.

**Verify:** run doctor on this Windows checkout and assert critical PASS; inspect output for secret-value absence.

## Task 11: Add Reliability Lab to the existing Vite UI

**Files:**

- Create: `ui/start-building-app/src/features/open-cekura/types/index.ts`
- Create: `ui/start-building-app/src/features/open-cekura/api/reliabilityApi.ts`
- Create: `ui/start-building-app/src/features/open-cekura/components/*`
- Create: `ui/start-building-app/src/features/open-cekura/pages/*`
- Create: `ui/start-building-app/src/features/open-cekura/ReliabilityLabRoutes.tsx`
- Modify: `ui/start-building-app/src/app/App.tsx`
- Modify: `ui/start-building-app/src/app/components/layout/Sidebar.tsx`
- Create: `scripts/reliability_lab_ui_smoke.py`

**RED:** static/source smoke asserts all canonical routes, one sidebar entry, `/reliability/...` API paths, encoded IDs, loading/empty/unavailable states, stable three-column test IDs, evidence-chain links, and forbidden secret/raw markers.

**GREEN:** implement the lazy workspace feature, local navigation, aggregate API reads, Overview/lists/details, and responsive Run Detail. Use existing theme/auth/API behavior; no mock fallback or new npm dependency.

**Verify:** smoke, `npm ci`, `npm run build`, then browser/E2E proof that a deterministic campaign appears and the evidence chain is navigable.

**Commit:** `feat(ui): add Reliability Lab views`

## Task 12: Add cross-platform CI and full acceptance

**Files:**

- Create: `.github/workflows/open-cekura.yml`
- Create: `scripts/open_cekura_acceptance.py`
- Modify: existing CI smoke list only where a stable UI/API marker belongs
- Create/update: `docs/open-cekura/HANDOFF.md`
- Update project-delta records only for facts actually changed

**RED:** run the acceptance script before wiring the workflow and record missing gates.

**GREEN:** add an OS/Python matrix for Ubuntu/Windows × 3.10/3.11, Node 20 UI build, deterministic campaigns/comparison/gates/evidence, unit/integration/API checks, and no-secret behavior. Use Python commands instead of Bash-specific blocks.

**Verify locally:**

```powershell
python scripts/open_cekura_acceptance.py
python -m pytest open_cekura/tests -q
Set-Location ui/start-building-app
npm ci
npm run build
Set-Location ../..
git diff --check
git status --short
```

**Commit:** `test: add Windows reliability acceptance`

## Task 13: Close evidence, publish, and monitor CI

**Files:**

- Update: `docs/open-cekura/HANDOFF.md`
- Update: relevant `docs/project/*` Project Delta only if canonical facts changed
- Update: linked Notion plan/tasks with exact commits, commands, PR, and CI

**Execution:** run `superpowers:verification-before-completion`, request a code review, inspect the complete diff, commit the final handoff, push `feat/open-cekura-windows-v0`, and open the PR titled `feat: add Windows-supported OpenCekura Reliability Lab v0` to `main` with every requested body section.

Monitor the exact GitHub Actions run. Fix failures with focused TDD, rerun local acceptance, push, and recheck until required Ubuntu/Windows/UI jobs are green. Record the exact PR and CI run in HANDOFF and Notion. Stop before merge.

**Commit:** `docs: close OpenCekura Windows v0 handoff`
