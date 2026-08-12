# OpenCekura Windows v0 Handoff

Review decision: **AUDIT_COMPLETE — OWNER_REVIEW_REQUIRED**

This document records the reproducible audit of implementation commit
`fd87388451e9646b67e702a7c44fa09cab1d0f14` as of 2026-08-12. That exact commit
is published on the agreed branch and passed every required AgentOps MIS and
OpenCekura Windows/Ubuntu job. The pull request must still be reviewed and
merged, if appropriate, by the Owner; the implementation agent must not merge.

## Candidate identity

```text
Repository: geogejoy107-jpg/agentops-mis-mvp
GitHub Issue: #123
Local checkout: F:/文档/MIS/agentops-mis-mvp
Local branch: codex/open-cekura-windows-v0
Audited remote branch point: origin/codex/open-cekura-windows-v0 at fd87388451e9646b67e702a7c44fa09cab1d0f14
Base branch: origin/main
Base SHA: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Exact audited implementation SHA: fd87388451e9646b67e702a7c44fa09cab1d0f14
Exact governed local acceptance SHA: fd87388451e9646b67e702a7c44fa09cab1d0f14
Ahead / behind origin/main at audited implementation commit: 34 / 0
Dirty state at audited implementation SHA: CLEAN
Windows version: NT 10.0.26200.0
Local Python version: 3.13.5
Local Node version: v22.23.2
Local npm version: 10.9.8
Draft PR: https://github.com/geogejoy107-jpg/agentops-mis-mvp/pull/128
Legacy Draft PR #125: closed as superseded; it was not merged
Exact implementation AgentOps MIS CI: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31576925820 (6/6 success)
Exact implementation OpenCekura CI: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31576925828 (5/5 success)
```

A Git commit cannot contain its own SHA. Therefore this file binds the product
audit to the exact implementation commit above. The later Handoff-only PR-head
SHA and its repeat CI are recorded in the PR Conversation and Notion audit
record after those checks complete. GitHub's PR head SHA is authoritative; no
invented self-referential hash is inserted here.

For any future audit refresh, rerun and record:

```powershell
git fetch origin --prune
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git status --short
git diff --check
```

## Audit status

The prior `BLOCKED_FOR_AUDIT` decision is resolved for the audited
implementation commit. Remote publication, clean dependency installation,
installed import outside the checkout, the Windows/Ubuntu Python 3.10/3.11
matrix, browser acceptance, cross-platform distribution reproducibility, and
the existing MIS workflow all have exact-head GitHub evidence. PR #128 remains
unmerged and requires Owner review.

Because this Handoff update necessarily creates a later documentation-only
head, that final head must pass the same required workflows before the Draft is
marked Ready. Its SHA and run URLs are recorded externally in the PR and Notion
without creating an infinite self-reference loop.

## Delivered architecture

OpenCekura is the code name and Python namespace. **Reliability Lab** is the
product-module name. It is an AgentOps MIS vertical product implementing:

```text
Scenario -> Simulation -> ToolCall observation -> Evaluation -> Failure
         -> Regression -> Release Gate -> Evidence -> Reliability Lab UI
```

The implementation includes versioned domain objects, Scenario v1 YAML input,
deterministic Mock and HTTP adapters, deterministic evaluators, optional LLM
judge metadata, evidence bundles, regression replay, release gating, SQLite
vertical persistence, MIS authority mappings, API routes, the existing Vite UI,
Windows diagnostics, packaging checks, and a cross-platform CI workflow.

This module is intentionally distinct from `incubator/research-lab/`:

```text
Product / navigation: Reliability Lab
Python namespace: open_cekura
Backend namespace: /api/reliability/*
Browser namespace: /mis-api/reliability/*
```

It does not create `research_lab_v2`, a second frontend, or a second auth/RBAC
system. Vite removes `/mis-api` and proxies browser requests to the existing
backend `/api` namespace.

## Packaging and dependency contract

The repository uses `agentops_mis_cli._build_backend`, not setuptools package
discovery. The audited backend recursively includes `open_cekura`, required MIS
runtime packages, the packaged public contract, and OpenCekura example fixtures.
Wheel/sdist tests cover those contents and distribution reproducibility. A
built base wheel was imported from outside the checkout, demonstrating that
`open_cekura` was loaded from the installed distribution rather than the source
working directory.

The base package keeps `dependencies = []`. Reliability Lab declares its full
runtime dependency contract as:

```toml
[project.optional-dependencies]
reliability = [
  "pydantic>=2.8,<3",
  "PyYAML>=6.0,<7",
]
```

The local installed-runtime probe passed with inherited dependency packages;
the local package index could not perform a new TLS download and TLS validation
was not disabled. The authoritative clean-install proof is therefore the exact
GitHub matrix: all four clean runners installed `.[reliability]`, imported
`open_cekura`, `agentops_mis_runtime`, and `server` from `site-packages` outside
the checkout under `python -I`, and matched the bundled build commit to
`fd87388451e9646b67e702a7c44fa09cab1d0f14`.

## Relationship with Windows PR #116

Dependency on PR #116 (`codex/windows-cli-worker-v1`): **NONE for deterministic
v0**.

Reliability Lab uses `pathlib`, `tempfile`, loopback sockets, atomic same-directory
writes, and argv-based subprocess execution. It does not require or duplicate
PR #116's Windows installer, Task Scheduler manager, credential ACL manager,
worker service lifecycle, or Codex process containment.

Known interaction with #116: both branches may touch Windows documentation,
CLI registration, packaging, workflows, or process utilities. Rebase and rerun
the full acceptance matrix after either PR merges first. If a future change
imports code available only on #116, the dependency declaration must change or
that import must be removed before this PR can target current `main`.

## MIS authority integration

Reliability Lab does not create a second Task, Run, Approval, or Audit ledger.
The vertical tables retain stable mappings to existing MIS authority objects:

| Reliability object | MIS authority object |
|---|---|
| Campaign | Task and verified Plan |
| ConversationRun | Run |
| ObservedToolCall | ToolCall |
| EvaluationResult | Evaluation |
| EvidenceManifest | Artifact and Evidence |
| ReleaseGateDecision | Approval / Quality Gate |
| RegressionCase | reviewed Memory for future Plan input |

The campaign lifecycle writes PENDING/RUNNING authority before simulation,
closes errors fail-closed, and uses stable idempotency keys. A local run can be
marked `MIS_SYNC_PENDING`; it cannot be reported as governed success after an
MIS write failure. Retry paths are tested not to duplicate Task, Plan, Run,
ToolCall, Evaluation, Artifact, Approval, Memory, or Audit authority records.

Governed evidence verification reconciles the filesystem tree with the
workspace/database authority. Public CLI verification therefore requires
`--workspace`, `--db`, and `--artifacts`; a low-level filesystem-only verifier
is not equivalent to the governed CLI result.

## Scenario and evaluation coverage

The appointment demo contains ten deterministic scenarios: basic success,
interruption, date change mid-flow, ambiguous identity, unavailable slot,
duplicate request, mutation before confirmation, backend timeout, tool success
with an agent failure claim, and an agent success claim without state mutation.

Each run evaluates task success, required and forbidden ToolCalls, duplicate
mutation, confirmation before mutation, final-state match, turn-count limit,
and timeout. Evaluator statuses remain distinct:

```text
PASS     sufficient evidence satisfies the contract
FAIL     observed agent behavior violates the contract
ERROR    evaluator execution failed; the release gate blocks
SKIPPED  optional evaluator is unavailable, for example no LLM API key
```

Results retain reason codes, turn/tool/expectation references, evidence refs,
thresholds, scores, and metadata. The optional LLM judge is never required by
CI; missing credentials produce `SKIPPED`, never a fabricated score or PASS.

## Evidence, comparison, gate, and replay contracts

Scenario and artifact digests use UTF-8 canonical JSON, sorted keys, fixed
separators, and relative POSIX paths. Digests exclude absolute Windows paths,
filesystem timestamps, YAML presentation differences, and CRLF/LF differences.
Path-bearing IDs are lowercase on every OS so Windows and Linux publish the
same logical identity.

Baseline and candidate are comparable only when suite hash, scenario ID set,
Scenario schema version, evaluator-policy hash, mock backend version,
ToolCall-contract version, and deterministic seed/mode match. Otherwise the
result is `INCOMPARABLE` and the release is blocked.

Every Campaign has an exact artifact set. `regression_replay.json` is canonical
JSON `null` for an ordinary campaign. A replay target contains a typed,
content-addressed mapping from source Failure/Evaluation/Run/MIS authority to
the target Scenario/Run. Strict verification recursively verifies the source
campaign, rejects authority drift, and checks the mapping digest. Gate and
compare operations preserve replay provenance byte-for-byte.

Gate decisions are derived from evaluator facts, never from version names or
campaign IDs. Local browser acceptance deliberately swapped misleading locator
names and still produced the correct result:

```text
Observed baseline behavior campaign ID: occampaign_ci_locator_says_candidate
Observed baseline gate: BLOCK
Observed candidate behavior campaign ID: occampaign_ci_locator_says_baseline
Observed candidate gate: PASS
Comparison gate: PASS
Evidence tamper detection: PASS (covered artifact rejected, then restored)
Browser readback: PASS
```

The exact audited implementation was then run through the governed local CLI
path in a new workspace. These IDs are local audit evidence and are not checked
into Git:

```text
Local acceptance workspace: windows-audit-fd873884
Local acceptance baseline campaign ID: occampaign_windows_audit_baseline_fd873884
Local acceptance candidate campaign ID: occampaign_windows_audit_candidate_fd873884
Local acceptance replay campaign ID: occampaign_6a99776effc7055548a64619
Local acceptance baseline gate: BLOCK (CLI exit 3; 5 RegressionCases)
Local acceptance candidate gate: PASS (CLI exit 0)
Local acceptance replay gate: PASS (CLI exit 0)
Candidate 10-run EvidenceManifest-set SHA-256: ef7d470b3ee3eb1acfe7be4aff91d946edc1127dd5c7ca1a122515719e411ca8
Manifest-set digest contract: SHA256 of the UTF-8 compact JSON array of sorted {path,sha256} rows
Candidate campaign_summary.json SHA-256: 47b23984c03ad2238bbd6520a9df30676ac48cf05c9aa6617ca61758eb5c8f31
Tamper-rejection result: PASS on exact-head Windows and Ubuntu CI; covered transcript was rejected and restored
Regression replay result: PASS; repeated CLI replay returned occampaign_6a99776effc7055548a64619 and run_result.idempotent_replay=true
Wheel SHA-256: 9fe26c34b10f77d143e2aaca1412384a11086a13009a39356934b3c795b66b8c
Sdist SHA-256: 4d1cee27c14946ac91b5f0a8af04484e5965342c80548a3ec1dd02d97af2d980
```

## Local verification record

Exit status was zero unless an expected negative test asserted a documented
non-zero result.

| Check | Result |
|---|---|
| Windows Doctor at exact audited SHA | PASS; Python 3.13.5, Node 22.23.2, npm 10.9.8, Git 2.50.1.windows.1, SQLite 3.45.3, clean branch/commit, writable repo, localhost bind, UI dependencies |
| Exact-head local unit suite | 250 passed, 2 skipped |
| Exact-head governed baseline | BLOCK, 10 runs, 5 failures/RegressionCases, duplicate mutation and confirmation-before-mutation blockers |
| Exact-head governed candidate | PASS, 10 runs, 0 failures, strict Evidence authority verification PASS |
| Exact-head governed replay | PASS, 3 replay runs, strict Evidence verification PASS, retry same campaign ID with `run_result.idempotent_replay=true` |
| Exact-head local distribution audit | PASS; wheel/sdist hashes match the GitHub four-platform records |
| `git diff --check` | PASS |
| Local UI install/test/build | `npm ci` PASS; Reliability tests 4 passed; Vite build PASS |
| Exact-head GitHub OpenCekura matrix | 4/4 OS/Python jobs success plus cross-platform reproducibility success |
| Each clean GitHub matrix job | installed probe PASS; 252 unit tests PASS; 232 integration tests PASS; Scenario/Doctor/UI/build/acceptance PASS |
| Exact-head Windows browser acceptance | PASS; misleading IDs still derive baseline BLOCK/candidate PASS; 2 campaigns, 20 runs, 10 candidate API rows, Run Detail readback PASS |
| Exact-head evidence tamper negative | PASS; tamper detected and restored bundle re-verified |
| Exact-head AgentOps MIS CI | 6/6 jobs success, including backend smokes, UI, Python compatibility, and real Linux Relay/systemd paths |

## Exact final acceptance commands

This is the canonical Windows acceptance contract. The exact audited run used
the suffixed Campaign IDs recorded above; future reviewers should use fresh IDs
and record every exit code.

```powershell
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
$reviewRoot = Join-Path $env:TEMP ('open-cekura-review-' + [guid]::NewGuid().ToString('N'))
$reviewOutside = Join-Path $env:TEMP ('open-cekura-outside-' + [guid]::NewGuid().ToString('N'))
$stateRoot = Join-Path $env:TEMP ('open-cekura-state-' + [guid]::NewGuid().ToString('N'))
$openCekuraDb = Join-Path $stateRoot 'reliability.db'
$openCekuraArtifacts = Join-Path $stateRoot 'artifacts'
$workspaceId = 'windows-audit'
$baselineId = 'occampaign_windows_audit_baseline'
$candidateId = 'occampaign_windows_audit_candidate'
New-Item -ItemType Directory -Path $stateRoot | Out-Null

git fetch origin --prune
git status --short
git branch --show-current
git rev-parse HEAD
git diff --check
git diff --stat origin/main...HEAD

python -m venv $reviewRoot
$reviewPython = Join-Path $reviewRoot 'Scripts\python.exe'
& $reviewPython -m pip install --upgrade pip
& $reviewPython -m pip install '.[reliability]' 'pytest>=8,<9'
New-Item -ItemType Directory -Path $reviewOutside | Out-Null
Push-Location $reviewOutside
& $reviewPython -I -c "import open_cekura; print(open_cekura.__file__)"
Pop-Location

& $reviewPython -I -m open_cekura.windows.doctor
& $reviewPython -I -m open_cekura.cli.main doctor
& $reviewPython -I -m open_cekura.cli.main scenario validate examples/open-cekura/scenarios/basic.yaml
& $reviewPython -I -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version baseline --campaign-id $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
& $reviewPython -I -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version candidate --campaign-id $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
& $reviewPython -I -m open_cekura.cli.main campaign compare --baseline $baselineId --candidate $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts

& $reviewPython -I -m open_cekura.cli.main gate evaluate --campaign $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 3) { throw "Expected baseline BLOCK exit 3, got $LASTEXITCODE" }
& $reviewPython -I -m open_cekura.cli.main gate evaluate --campaign $candidateId --baseline $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 0) { throw "Expected candidate PASS exit 0, got $LASTEXITCODE" }

& $reviewPython -I -m open_cekura.cli.main evidence verify --campaign $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts --strict
& $reviewPython -I -m open_cekura.cli.main evidence verify --campaign $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts --strict
$replayJson = & $reviewPython -I -m open_cekura.cli.main regression replay --campaign $baselineId --version candidate --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
$replay = $replayJson | ConvertFrom-Json
$replayId = [string]$replay.replay_campaign_id
& $reviewPython -I -m open_cekura.cli.main evidence verify --campaign $replayId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts --strict
& $reviewPython -I -m open_cekura.cli.main gate evaluate --campaign $replayId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts

& $reviewPython -m pytest open_cekura/tests/unit -q
& $reviewPython -m pytest open_cekura/tests/integration -q
& $reviewPython scripts/open_cekura_installed_acceptance.py --expected-commit ((git rev-parse HEAD).Trim())
& $reviewPython scripts/open_cekura_distribution_audit.py build --output (Join-Path $stateRoot 'dist') --result (Join-Path $stateRoot 'distribution.json')

Push-Location ui/start-building-app
npm ci
npm run test:reliability
npm run build
Pop-Location

& $reviewPython scripts/open_cekura_ci_acceptance.py --ui-dist ui/start-building-app/dist --result-path (Join-Path $stateRoot 'acceptance.json') --require-browser

git status --short
git diff --check
```

`open_cekura_ci_acceptance.py` performs the bounded tamper/rejection/restore
probe in a temporary workspace. Do not manually alter persistent review
evidence. The acceptance record must report `tamper_detected=true`,
`restored_after_tamper=true`, `browser_e2e=pass`, two campaigns, twenty runs,
ten candidate API rows, and `run_detail_read_back=true`.

The CI-equivalent Linux commands use `python` from the runner environment and
omit `--require-browser`; the workflow itself is the authoritative source.

## Remote delivery checklist

- [x] Commit the audited implementation and record its exact SHA above.
- [x] Confirm the working tree is clean at the audited implementation SHA.
- [x] Push `codex/open-cekura-windows-v0` to `origin`.
- [x] Open Draft PR #128 to `main` with title
      `feat: add Windows-supported OpenCekura Reliability Lab v0`.
- [x] Confirm `/api/reliability/*` direct backend integration tests and
      `/mis-api/reliability/*` UI requests.
- [x] Confirm the four Windows/Ubuntu Python matrix jobs install the declared
      extra and import from outside the checkout.
- [x] Confirm package, Scenario, deterministic campaigns, compare, gate,
      evidence/tamper, replay, MIS/SQLite, UI tests, and UI build are green.
- [x] Record exact implementation GitHub Actions run URLs, IDs, head SHA, and
      every job result.
- [x] Backfill local acceptance campaign/replay IDs and EvidenceManifest-set SHA-256.
- [x] Establish the external closeout rule: record the Handoff-only PR head and
      repeat CI in the PR Conversation and Notion after they complete.
- [x] Enforce the transition rule: mark Ready only after that repeat CI passes;
      never merge from the implementation session.

## Known limitations

- The repository currently declares a proprietary local MVP license. This PR
  does not silently relicense it and must not be advertised as an officially
  open-source Cekura product.
- Deterministic mock campaigns prove fixture/policy behavior, not production
  reliability, certification, or live-traffic performance.
- The HTTP adapter is implemented and tested, but the v0 campaign CLI centers
  on deterministic Mock execution.
- The optional LLM judge is not CI authority; without a configured key it is
  `SKIPPED`.
- Reliability Lab v0 is evidence-first. A visual Scenario editor is not part of
  this release.
- Evidence manifests detect covered-file changes but are not cryptographically
  signed attestations. MIS Artifact/Approval/Audit records provide the separate
  authority ledger.
- Voice, WebRTC, SIP, LiveKit, Pipecat, audio, ASR/TTS, dead-air, clipping, and
  real telephone campaigns are v0.2 scope.
- `npm ci` currently reports five dependency findings (four high, one critical),
  the existing Recharts 2.x deprecation, and a Vite chunk-size warning. They are
  project debt; no unsafe automated dependency upgrade belongs in this audit pass.

## Remaining v0.2 scope

After v0 is reviewed and merged by the Owner: Pipecat and LiveKit adapters,
audio-file pipelines, ASR/TTS observation, interruption/dead-air/turn-latency
and clipping metrics, SIP/telephone adapters, and real-world voice campaigns.
