# OpenCekura Reliability Lab v0

Review state: **AUDIT_COMPLETE — OWNER_REVIEW_REQUIRED**. The exact audited
implementation commit passed every required Windows, Ubuntu, and existing MIS
job. The Handoff-only head must repeat those checks before the Draft is marked
Ready. This PR must not be merged by the implementation agent.

## What changed

Adds the Windows-supported OpenCekura v0 vertical product, formally presented
in the application as **Reliability Lab**. It closes the deterministic loop from
Scenario through Simulation, ToolCall observation, Evaluation, Failure,
Regression, Release Gate, Evidence, API, SQLite, and the existing MIS UI.

This Draft PR intentionally excludes Voice/WebRTC/SIP work.

## Architecture

The Python namespace is `open_cekura`, the backend namespace is
`/api/reliability/*`, and the browser namespace is
`/mis-api/reliability/*`. Vite performs the existing `/mis-api` to `/api` proxy
translation. The UI remains under `ui/start-building-app`; no second frontend,
login, session, owner, or RBAC implementation was introduced.

OpenCekura is separate from the existing `incubator/research-lab/`. The product
name is Reliability Lab, not Research Lab v2.

## MIS integration

Reliability Lab uses the existing MIS authority ledgers:

- Campaign -> Task and verified Plan;
- ConversationRun -> Run;
- ObservedToolCall -> ToolCall;
- EvaluationResult -> Evaluation;
- EvidenceManifest -> Artifact/Evidence;
- ReleaseGateDecision -> Approval/Quality Gate;
- RegressionCase -> reviewed Memory for future Plan input.

The reliability database contains vertical business objects and stable mapping
IDs, not second Task/Run/Approval/Audit ledgers. Writes and retries use stable
idempotency keys. MIS write failures fail closed or remain `MIS_SYNC_PENDING`;
they cannot be presented as governed success.

## Windows support

Core flows use `pathlib.Path`, `tempfile`, Python sockets, argv-based subprocess
calls, and atomic same-directory writes followed by `os.replace`. They do not
depend on Bash, POSIX process inspection, systemd, LaunchAgent, or POSIX mode
assumptions.

Dependency on PR #116 (`codex/windows-cli-worker-v1`): **NONE for deterministic
v0**. This PR does not duplicate its Windows installer, Task Scheduler,
credential ACL, worker lifecycle, or process containment. Both PRs may touch
packaging, CLI registration, workflows, process utilities, or Windows docs;
rebase and rerun full acceptance after whichever lands first.

## Scenario coverage

The public appointment-agent demo includes ten scenarios: basic success,
interruption, date change mid-flow, ambiguous identity, unavailable slot,
duplicate request, mutation before confirmation, backend timeout, tool success
with an agent failure claim, and an agent success claim without state mutation.

Scenario v1 uses YAML for authoring and fail-fast validation. Evidence hashes
use the parsed canonical JSON representation rather than YAML presentation.

## Evaluation design

Deterministic evaluators cover task success, required ToolCalls, forbidden
ToolCalls, duplicate mutation, confirmation before mutation, final-state match,
turn-count limit, and timeout. Results retain status, score, threshold, reason
codes, evidence refs, metadata, and exact turn/tool/expectation context.

Statuses are `PASS`, `FAIL`, `ERROR`, and `SKIPPED`. Evaluator `ERROR` blocks.
The optional LLM judge records provider/model/prompt/judge versions and
temperature; without credentials it is `SKIPPED`, never fake PASS.

## Evidence design

Every run and campaign has a bounded, exact artifact set. Hashes use canonical
UTF-8 JSON, sorted keys, fixed separators, relative POSIX paths, normalized
Scenario input, and no checkout-specific Windows path or filesystem timestamp.
Path-bearing IDs are lowercase across platforms.

Governed verification reconciles the filesystem Evidence Bundle with MIS
workspace/database authority. Covered-file tampering must identify a hash
mismatch and return a non-zero result. Ordinary campaigns store canonical
`null` replay provenance; replay targets store a typed, hashed mapping and
recursively verify their source campaign.

## Regression loop

FailureCases normalize into RegressionCases containing the original failing
input, expected and observed state, failure reason, source Run, and evaluator.
Replay creates a new ScenarioSuite/Campaign through the normal MIS lifecycle.
Its source-to-target mapping is persisted transactionally in SQLite and
Evidence and uses stable idempotency keys. Source MIS or evidence drift fails
closed; gate/compare operations preserve replay provenance.

## Release Gate

Gate outcomes derive from observed evaluator facts. There is no branch on
`baseline`, `candidate`, version name, or Campaign ID. Comparisons require equal
suite hash, scenario set/schema, evaluator policy, mock backend version, tool
contract, and deterministic seed/mode; mismatches return `INCOMPARABLE` and
block release.

Local acceptance intentionally swapped misleading locator names and still
derived the correct outcome:

```text
occampaign_ci_locator_says_candidate -> baseline behavior -> BLOCK
occampaign_ci_locator_says_baseline -> candidate behavior -> PASS
comparison -> PASS
```

Governed local acceptance on the exact audited implementation
`fd87388451e9646b67e702a7c44fa09cab1d0f14` produced:

```text
occampaign_windows_audit_baseline_fd873884 -> BLOCK, 5 RegressionCases
occampaign_windows_audit_candidate_fd873884 -> PASS
occampaign_6a99776effc7055548a64619 -> replay PASS and idempotent retry
candidate 10-run EvidenceManifest-set SHA-256 -> ef7d470b3ee3eb1acfe7be4aff91d946edc1127dd5c7ca1a122515719e411ca8
```

## UI

Adds Reliability Lab navigation and evidence-first views for Overview, Agents,
Scenario Suites, Campaigns, Run Detail, Failures, Regression Suite, and Release
Gates. Run Detail renders transcript, ToolCalls, evaluator results, evidence
hashes, MIS mappings, and the Gate result. It reads existing reliability API
data; this is not a static chat demo.

## Tests

Verified at the exact audited implementation:

```text
Local Windows Doctor: PASS
Local OpenCekura unit suite: 250 passed, 2 skipped
Local governed baseline/candidate/replay: BLOCK/PASS/PASS
Local strict Evidence verification and replay idempotency: PASS
git diff --check: PASS
npm ci: PASS
Reliability UI tests: 4 passed
Vite build: PASS
Four clean GitHub OS/Python jobs: 4/4 success
Each GitHub job: installed import PASS, 252 unit tests, 232 integration tests
Windows real-browser acceptance: PASS on Python 3.10 and 3.11
Evidence tamper/rejection/restore: PASS
Cross-platform wheel/sdist reproducibility: PASS
Existing AgentOps MIS CI: 6/6 success
```

The local package index could not perform a new TLS dependency download, and TLS
validation was not disabled. The authoritative dependency proof is the clean
GitHub matrix, where `pip install '.[reliability]'` and the isolated
outside-checkout import succeeded on all four runners.

## Windows CI

`.github/workflows/open-cekura-windows.yml` defines a required matrix:

```text
ubuntu-latest / Python 3.10
ubuntu-latest / Python 3.11
windows-latest / Python 3.10
windows-latest / Python 3.11
```

Each job installs `.[reliability]`, runs an isolated installed-distribution
probe outside the checkout, and binds its bundled commit to the immutable
selected checkout SHA (PR head for `pull_request`, `github.sha` for `push`). It
then runs Doctor, unit/integration tests, Scenario validation,
deterministic/tamper acceptance, `npm ci`, Reliability UI tests, and the Vite
build without secrets. The workflow recomputes the downloaded wheel/sdist
hashes before comparing all four matrix records across Windows and Ubuntu.

Exact audited head:
`fd87388451e9646b67e702a7c44fa09cab1d0f14`.

- OpenCekura Windows and Ubuntu run
  `31576925828`: 5/5 jobs success, including the four matrix jobs and
  cross-platform distribution reproducibility.
- AgentOps MIS CI run `31576925820`: 6/6 jobs success, including backend
  deterministic smokes, UI build, Python 3.10/3.11 runtime compatibility, and
  both real Linux Relay/systemd paths.

## Known limitations

- The repository currently uses a proprietary local MVP license; this PR does
  not silently relicense it or claim to be an official Cekura open-source
  release.
- Mock fixture results are not proof of production reliability or certification.
- The HTTP adapter exists, while the v0 CLI emphasizes deterministic Mock runs.
- LLM judging is optional and is not CI authority.
- The v0 UI is evidence-first; a visual Scenario editor is future work.
- Evidence manifests are integrity records, not signed remote attestations.
- Voice, WebRTC, SIP, LiveKit, Pipecat, and real-world audio campaigns are out
  of v0 scope.
- Rebase and rerun after PR #116 if it merges first.
- `npm ci` reports five existing dependency findings (four high, one critical),
  a Recharts 2.x deprecation, and the existing Vite chunk-size warning.

## Exact acceptance commands

The canonical Windows command block and expected exit codes are in
`docs/open-cekura/HANDOFF.md` and `docs/open-cekura/WINDOWS_DEV_RUNBOOK.md`.
The audited candidate ran this governed path; future reviewers can repeat it
(the HANDOFF contains the additional outside-checkout import and
distribution-reproducibility probes):

```powershell
$ErrorActionPreference = 'Stop'
$reviewTag = [guid]::NewGuid().ToString('N')
$reviewRoot = Join-Path $env:TEMP ('open-cekura-pr-review-' + $reviewTag)
$db = Join-Path $reviewRoot 'reliability.db'
$artifacts = Join-Path $reviewRoot 'artifacts'
$workspace = 'pr-review'
$baseline = 'occampaign_pr_review_baseline'
$candidate = 'occampaign_pr_review_candidate'
New-Item -ItemType Directory -Path $reviewRoot | Out-Null

python -m pip install '.[reliability]' 'pytest>=8,<9'
python scripts/open_cekura_installed_acceptance.py --expected-commit ((git rev-parse HEAD).Trim())
python -I -m open_cekura.windows.doctor
python -m pytest open_cekura/tests/unit -q
python -m pytest open_cekura/tests/integration -q
python -m open_cekura.cli.main scenario validate examples/open-cekura/scenarios/basic.yaml
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version baseline --campaign-id $baseline --workspace $workspace --db $db --artifacts $artifacts
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version candidate --campaign-id $candidate --workspace $workspace --db $db --artifacts $artifacts
python -m open_cekura.cli.main campaign compare --baseline $baseline --candidate $candidate --workspace $workspace --db $db --artifacts $artifacts
python -m open_cekura.cli.main gate evaluate --campaign $baseline --workspace $workspace --db $db --artifacts $artifacts
if ($LASTEXITCODE -ne 3) { throw "Expected baseline BLOCK exit 3, got $LASTEXITCODE" }
python -m open_cekura.cli.main gate evaluate --campaign $candidate --baseline $baseline --workspace $workspace --db $db --artifacts $artifacts
if ($LASTEXITCODE -ne 0) { throw "Expected candidate PASS exit 0, got $LASTEXITCODE" }
python -m open_cekura.cli.main evidence verify --campaign $baseline --workspace $workspace --db $db --artifacts $artifacts --strict
python -m open_cekura.cli.main evidence verify --campaign $candidate --workspace $workspace --db $db --artifacts $artifacts --strict
$replayJson = python -m open_cekura.cli.main regression replay --campaign $baseline --version candidate --workspace $workspace --db $db --artifacts $artifacts
$replay = $replayJson | ConvertFrom-Json
$replayId = [string]$replay.replay_campaign_id
python -m open_cekura.cli.main evidence verify --campaign $replayId --workspace $workspace --db $db --artifacts $artifacts --strict
python -m open_cekura.cli.main gate evaluate --campaign $replayId --workspace $workspace --db $db --artifacts $artifacts
Push-Location ui/start-building-app
npm ci
npm run test:reliability
npm run build
Pop-Location
python scripts/open_cekura_ci_acceptance.py --ui-dist ui/start-building-app/dist --result-path (Join-Path $reviewRoot 'open-cekura-ci-acceptance.json') --require-browser
git diff --check
```

The full handoff command block also runs explicit baseline/candidate campaigns,
compare, both gate outcomes, governed strict Evidence verification, regression
replay, replay gate evaluation, and installed distribution audit.

## Evidence

```text
Base SHA: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Exact audited implementation SHA: fd87388451e9646b67e702a7c44fa09cab1d0f14
Exact governed local acceptance SHA: fd87388451e9646b67e702a7c44fa09cab1d0f14
Exact Draft PR: https://github.com/geogejoy107-jpg/agentops-mis-mvp/pull/128
Exact OpenCekura GitHub Actions run: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31576925828
Exact AgentOps MIS CI run: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31576925820
Local baseline Campaign ID: occampaign_windows_audit_baseline_fd873884
Local candidate Campaign ID: occampaign_windows_audit_candidate_fd873884
Local replay Campaign ID: occampaign_6a99776effc7055548a64619
Local EvidenceManifest-set SHA-256: ef7d470b3ee3eb1acfe7be4aff91d946edc1127dd5c7ca1a122515719e411ca8
Local campaign_summary.json SHA-256: 47b23984c03ad2238bbd6520a9df30676ac48cf05c9aa6617ca61758eb5c8f31
Wheel SHA-256: 9fe26c34b10f77d143e2aaca1412384a11086a13009a39356934b3c795b66b8c
Sdist SHA-256: 4d1cee27c14946ac91b5f0a8af04484e5965342c80548a3ec1dd02d97af2d980
Tamper rejection: PASS and restored on exact-head CI
Regression replay: PASS and idempotent retry
```

The implementation audit is complete. After the Handoff-only PR head repeats
the required workflows, record that exact SHA/run evidence in the PR
Conversation and Notion, then mark Ready for Owner review. Do not merge.

## Remaining v0.2 scope

Pipecat and LiveKit adapters, audio-file pipeline, ASR/TTS observation,
interruption/dead-air/turn-latency/clipping metrics, SIP/telephone adapters, and
real-world voice campaigns.
