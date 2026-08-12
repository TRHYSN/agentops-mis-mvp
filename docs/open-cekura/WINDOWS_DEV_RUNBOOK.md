# OpenCekura Windows Development Runbook

Status: v0 execution contract

Shell: PowerShell 7 or Windows PowerShell 5.1

Supported CI Python: 3.10 and 3.11
Supported UI runtime: Node.js 20 or newer compatible LTS

## Fresh clone and immutable preflight

```powershell
git clone https://github.com/geogejoy107-jpg/agentops-mis-mvp.git
Set-Location agentops-mis-mvp
git fetch --all --prune
git checkout main
git pull --ff-only
git branch --show-current
git rev-parse HEAD
git status --short
git checkout -b codex/open-cekura-windows-v0
```

Record repository, branch, exact HEAD, and working-tree state before changing files. Never develop directly on `main` and never use a Mac working directory as a Windows source of truth.

This implementation began from:

```text
Repository: geogejoy107-jpg/agentops-mis-mvp
Base branch: main
Starting HEAD: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Working branch: codex/open-cekura-windows-v0
Starting tree: clean
```

## Toolchain

Verify:

```powershell
python --version
git --version
node --version
npm --version
```

The base MIS package retains `dependencies = []`. Reliability Lab declares its complete third-party runtime contract as the `reliability` install extra (`Pydantic` and `PyYAML`); it must never rely on global site-packages. CI must not require API keys. The optional LLM judge reports `SKIPPED` when no supported key is present.

OpenCekura v0 writes run `EvidenceManifest` schema v3 and campaign evidence
envelope schema v3. The latter always includes `regression_replay.json`:
canonical `null` for an ordinary Campaign or a strict typed mapping envelope for
a replay target. Pre-release local v1/v2 databases and artifact roots are
intentionally not migrated because those versions used different digest
meanings. Archive or remove only the explicitly selected pre-release test
directory, create a fresh database/artifact root, and rerun the campaigns; never
edit an old manifest or its MIS Artifact hash in place.

Campaign publication uses a non-blocking Windows file lease under
`artifacts/.open-cekura-publications/` and a random authority identity stored in
the SQLite database. A hard-exited writer releases the OS lease automatically,
allowing the next command to remove only an unsealed private stage. A sealed
journal is never resolved against a missing database or a newly created
database at the same path; restore the original database or preserve the
journal and campaign tree for operator review. Do not manually remove a slot
while another campaign command is running.

Install fresh-clone dependencies:

```powershell
python -m pip install '.[reliability]'
Set-Location ui/start-building-app
npm ci
Set-Location ../..
```

Prove the custom build backend includes the runtime and public fixtures. This
probe launches the installed interpreter from a temporary directory, executes
both deterministic campaigns, compares them, and verifies candidate evidence:

```powershell
python -m venv .agentops_runtime\venv-package-review
$reviewPython = (Resolve-Path '.agentops_runtime\venv-package-review\Scripts\python.exe').Path
& $reviewPython -m pip install --upgrade pip
& $reviewPython -m pip install '.[reliability]'
& $reviewPython scripts/open_cekura_installed_acceptance.py
Push-Location $env:TEMP
& $reviewPython -c "import open_cekura; print(open_cekura.__file__)"
Pop-Location
```

The printed module path must be in the venv, not this checkout. The wheel and
sdist contract tests additionally require a wheel rebuilt from the generated
sdist to be byte-identical to a direct backend build.

## Doctor

```powershell
python -m open_cekura.windows.doctor
python -m open_cekura.cli.main doctor
```

Critical checks cover Python, Node, npm, Git, repository root, current branch, exact commit, dirty state, write access, SQLite, localhost bind, and UI dependency state. External keys are printed only as `PRESENT` or `MISSING`; values are never read back to the terminal.

## Scenario and deterministic acceptance

```powershell
$openCekuraDb = Join-Path $PWD '.agentops_runtime\open-cekura-runbook\reliability.db'
$openCekuraArtifacts = Join-Path $PWD '.agentops_runtime\open-cekura-runbook\artifacts'
$baselineId = 'occampaign_runbook_baseline'
$candidateId = 'occampaign_runbook_candidate'
$workspaceId = 'local-demo'

python -m open_cekura.cli.main scenario validate examples/open-cekura/scenarios/basic.yaml
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version baseline --campaign-id $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version candidate --campaign-id $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
python -m open_cekura.cli.main campaign compare --baseline $baselineId --candidate $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts

python -m open_cekura.cli.main gate evaluate --campaign $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 3) { throw "Expected baseline gate exit 3, got $LASTEXITCODE" }

python -m open_cekura.cli.main gate evaluate --campaign $candidateId --baseline $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 0) { throw "Expected candidate gate exit 0, got $LASTEXITCODE" }

python -m open_cekura.cli.main evidence verify --campaign $baselineId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
python -m open_cekura.cli.main evidence verify --campaign $candidateId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts

$ordinaryReplayPath = Join-Path (Join-Path $openCekuraArtifacts $candidateId) 'regression_replay.json'
if ([IO.File]::ReadAllText($ordinaryReplayPath) -cne 'null') { throw "Ordinary campaign replay evidence is not canonical null" }

$replayJson = python -m open_cekura.cli.main regression replay --campaign $baselineId --version candidate --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 0) { throw "Regression replay failed" }
$replay = $replayJson | ConvertFrom-Json
if ($replay.run_result.release_gate.decision -ne 'pass') { throw "Expected replay gate PASS" }

$replayId = [string]$replay.replay_campaign_id
$replayRoot = Join-Path $openCekuraArtifacts $replayId
$replayEvidencePath = Join-Path $replayRoot 'regression_replay.json'
$replaySummaryPath = Join-Path $replayRoot 'campaign_summary.json'
$replayEvidence = Get-Content -Raw -LiteralPath $replayEvidencePath | ConvertFrom-Json
$replayPointer = (Get-Content -Raw -LiteralPath $replaySummaryPath | ConvertFrom-Json).summary.regression_replay
if ($replayEvidence.target_campaign_id -ne $replayId) { throw "Replay evidence target mismatch" }
if (@($replayEvidence.mappings).Count -ne $replay.regression_count) { throw "Replay mapping count mismatch" }
if (@($replayPointer.mapping_ids).Count -ne $replay.regression_count) { throw "Replay pointer count mismatch" }
if ([string]::IsNullOrWhiteSpace([string]$replayPointer.mapping_sha256)) { throw "Replay mapping hash is missing" }
python -m open_cekura.cli.main evidence verify --campaign $replayId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts --strict
if ($LASTEXITCODE -ne 0) { throw "Recursive replay evidence verification failed" }

$replayArtifactHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $replayEvidencePath).Hash
python -m open_cekura.cli.main gate evaluate --campaign $replayId --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 0) { throw "Replay gate re-evaluation failed" }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $replayEvidencePath).Hash -ne $replayArtifactHash) { throw "Gate evaluation rewrote replay provenance" }

$replayRetryJson = python -m open_cekura.cli.main regression replay --campaign $baselineId --version candidate --workspace $workspaceId --db $openCekuraDb --artifacts $openCekuraArtifacts
if ($LASTEXITCODE -ne 0) { throw "Regression replay retry failed" }
$replayRetry = $replayRetryJson | ConvertFrom-Json
if (-not $replayRetry.run_result.idempotent_replay) { throw "Regression replay retry was not idempotent" }
```

Expected semantic outcome:

```text
Baseline: BLOCKED with concrete scenario/evaluator blockers
Candidate: PASS
Evidence verification: PASS before tampering and FAIL after a covered artifact changes
```

IDs may be fixed in CI for locating artifacts, but outcomes must be computed from observed facts.

`regression replay` defaults to the candidate mock profile and a stable target
Campaign ID derived from workspace, source Campaign, version, RegressionCases,
and canonical Scenario digests. Use `--campaign-id` only when an explicit target
locator is required. The JSON result reports regression/scenario counts, source
and replay IDs, the source-to-replay Scenario mapping, and the nested normal
campaign result; the target Evidence carries the typed mapping IDs. Repeating
the same command must report an idempotent nested run and must not add Task,
Plan, Run, Artifact, replay-mapping, Approval, or Audit rows. Evidence or MIS
drift, an empty regression set, and Scenario snapshot mismatch return a non-zero
error without creating the target Campaign.

After any compatible `campaign compare` or `gate evaluate` against a replay
target, rerun strict evidence verification and compare the
`regression_replay.json` hash with its pre-command value. Those commands may add
Gate history but must not remove, null, reorder, or rewrite replay provenance.

Exit code 3 from the baseline `gate evaluate` command is the expected successful
acceptance of a BLOCK decision, not an infrastructure failure. Evidence
integrity failure uses exit code 4.

## Tests

```powershell
python -m pytest open_cekura/tests/unit -q
python -m pytest open_cekura/tests/integration -q
python -m pytest open_cekura/tests -q
```

The integration set includes Scenario → Run → Evaluation → Evidence, baseline/candidate comparison, failure → regression → replay, and API → SQLite. Run the smallest affected test after each fix, then the full OpenCekura suite.

The repository also contains a standalone historical Research Lab package. Its
tests are run from its package root rather than by adding unrelated paths to
the root import environment:

```powershell
Push-Location incubator/research-lab
python -m pytest tests -q
Pop-Location
```

## Backend and UI

Use an isolated database during local acceptance:

```powershell
$env:AGENTOPS_DB_PATH = Join-Path $env:TEMP 'open-cekura-acceptance.db'
python server.py
```

In a second PowerShell window:

```powershell
Set-Location ui/start-building-app
npm ci
npm run build
npm run dev
```

The Vite proxy targets `http://127.0.0.1:8787` by default. Browse to `/workspace/reliability`. Production/deep-link behavior is served by the existing MIS host; no second frontend server is introduced.

Run the same portable UI/API/campaign acceptance used by CI. On Windows,
`--require-browser` fails closed unless a preinstalled Chrome or Edge renders
the actual Reliability Lab DOM; the script never downloads a browser.

```powershell
python scripts/reliability_lab_ui_smoke.py
Set-Location ui/start-building-app
npm run build
Set-Location ../..
python scripts/open_cekura_ci_acceptance.py `
  --ui-dist ui/start-building-app/dist `
  --result-path .agentops_runtime/open-cekura-final-acceptance.json `
  --require-browser
```

The result must contain `ok=true`, `browser_e2e=pass`, the selected installed
browser name, two campaigns, twenty runs, ten candidate API rows, and
`run_detail_read_back=true` for transcript, tool, evaluator, manifest, MIS Run,
and gate evidence.

## Windows implementation rules

- Use `pathlib.Path`; resolve and validate paths before writes.
- Use `tempfile` for temporary files/directories.
- For durable files, write a temporary file in the destination directory, flush and `fsync` where supported, then `os.replace`.
- Serialize hashed JSON as UTF-8 canonical JSON with sorted keys and fixed
  separators; do not add a BOM or translate newlines.
- Serialize logical evidence paths relative to the campaign root with POSIX `/`
  separators even on Windows; never hash an absolute checkout path or a `\`
  alias.
- Use `subprocess.run([executable, arg1, ...])` with `shell=False`.
- Use Python `socket` for localhost port/bind checks.
- Preserve Unicode and spaces in checkout paths.
- Do not use `/tmp`, `~/.local`, Bash, `sh`, `grep`, `sed`, `awk`, `lsof`, `ps`, systemd, LaunchAgent, or POSIX file-mode assumptions in an OpenCekura core flow.
- Reject path escape, drive-relative ambiguity, UNC/device paths where not explicitly allowed, Windows reserved names, and unexpected reparse-point escape.
- Never print credentials, token values, raw hidden prompts, or unredacted private payloads.

## Existing POSIX Host boundary

The historical Private Host imports `fcntl` and uses POSIX process/service primitives. OpenCekura v0 does not rewrite that service manager. Generic `agentops` and `server.py` paths are made importable on Windows by delaying POSIX-only imports. Invoking a POSIX-only Host operation on Windows must return a clear, fail-closed unsupported-platform error.

## Relationship with Windows PR #116

Dependency on PR #116 (`codex/windows-cli-worker-v1`): **NONE for deterministic
v0**. Reliability Lab uses only `pathlib`, `tempfile`, loopback sockets, and
argv-based subprocesses. It does not require or duplicate the Windows
installer, Task Scheduler manager, credential ACL manager, worker lifecycle,
or Codex process containment from #116. Both changes may touch CLI registration,
packaging, workflows, process utilities, or Windows documentation; rebase and
rerun the complete acceptance matrix after whichever PR lands first.

## Troubleshooting

### `ModuleNotFoundError: fcntl`

Confirm the generic CLI/Gateway does not import `agentops_mis_cli.host` during startup. A regression here is a Windows compatibility failure; do not install a fake `fcntl` package.

### Node was installed but PowerShell cannot find it

Open a new terminal or refresh `PATH` from the user and machine environment, then rerun `node --version` and `npm --version`. Do not hardcode a user-specific WinGet directory in project code.

### Port already in use

Use the doctor socket check or select another explicit loopback port. Do not depend on `lsof`, `netstat` text parsing, or process-name guessing.

### Evidence verification fails

Treat it as a real integrity failure. Inspect missing/unexpected paths and expected/actual SHA-256 values. Regenerate evidence only from a replayed run; do not edit the manifest to match a manually changed artifact.

## Release handoff

Before PR creation, record exact branch, exact commit, clean/dirty state, acceptance commands and results, artifact campaign IDs, and exact GitHub Actions run. Push the feature branch and open a PR to `main`; do not merge it.
