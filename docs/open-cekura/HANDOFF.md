# OpenCekura Windows v0 Handoff

Status: implementation and remote acceptance complete; PR #125 is open for
Owner review and has not been merged.

## Immutable source context

```text
Repository: geogejoy107-jpg/agentops-mis-mvp
GitHub Issue: #123
Base branch: main
Base / starting commit: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Working branch: feat/open-cekura-windows-v0
Starting working tree: clean
Operating system: Windows 11
Canonical Notion spec: 3b96adfd-d920-81cf-9f99-d2990deea005
Implementation commit accepted locally: 7bd651583fa4553c57ce190d0c4b6617e2a73ddf
Remote acceptance head: 31eef4d1821af0c553e61d638cc1acf30db053e3
Handoff commit: derive with git rev-parse HEAD after this document is committed
```

The handoff commit cannot contain its own SHA. Git and the PR are authoritative
for that runtime-derived value. The implementation SHA above is the exact clean
code commit used to generate the persistent campaigns and local acceptance
evidence below.

## Delivered product line

OpenCekura is an AgentOps MIS vertical product named **Reliability Lab**. It
implements the complete v0 loop:

```text
Scenario -> Simulation -> ToolCall observation -> Evaluation -> Failure
         -> Regression -> Release Gate -> Evidence -> Reliability Lab UI
```

It adds stable, versioned domain objects and YAML Scenario v1 validation;
deterministic Mock and HTTP adapters; eight deterministic evaluators; an
optional fail-closed LLM judge; content-addressed Evidence Bundles; failure to
regression conversion and replay; release-gate comparison; SQLite persistence;
governed CLI and `/mis-api/reliability/*` endpoints; evidence-first Vite views;
Windows doctor/process/path utilities; and an Ubuntu/Windows Python 3.10/3.11
GitHub Actions matrix.

## MIS authority integration

There is no second Task, Run, Approval, or Audit ledger. Reliability rows retain
stable `mis_*` mappings to the existing MIS authority objects:

| Reliability object | MIS authority object |
|---|---|
| Campaign | Task + verified Agent Plan |
| ConversationRun | Run |
| ObservedToolCall | ToolCall |
| EvaluationResult | Evaluation |
| EvidenceManifest | Artifact + plan-evidence manifest |
| ReleaseGateDecision | Approval / Quality Gate |
| RegressionCase | reviewed Memory candidate for future Plan input |

Campaign compare/gate commands reconstruct the exact typed hierarchy, Turn,
ToolCall, Evaluation, Artifact, PlanEvidence, Failure, Regression/Memory,
Approval, Gate-head, and Audit-chain sets before granting new authority. The
SQLite/filesystem boundary uses a tree-hash journal, transactional outbox,
SQLite-instance nonce, Windows case-normalized campaign lock domain, and an OS
process-lifetime lease. Recovery tests cover pre-commit rollback, post-commit
finalization, same-path database replacement, hard-exit stale stages, live
writer exclusion, and transitive historical-baseline closure.

The accepted persistent database contains 2 Tasks, 2 Plans, 20 Runs, 56 Tool
Calls, 160 Evaluations, 20 Artifacts, 18 available plan-evidence manifests,
3 Approvals, 5 Memories, and 297 Audit rows. All 20 Reliability runs, all 160
evaluations, and all 20 evidence manifests have non-null MIS mappings.

Campaign mappings:

- baseline Task `tskoc_4538c05eba6221ef1acad82d`, Plan
  `planoc_87a9febc97d60382deb4aadd`;
- candidate Task `tskoc_266fdeba61d9a998e21ce22d`, Plan
  `planoc_34608746cda05da5b2427a21`.

## Scenario and evaluation coverage

The appointment demo contains ten YAML scenarios: basic success, interruption,
change date mid-flow, ambiguous identity, unavailable slot, duplicate request,
mutation before confirmation, backend timeout, tool succeeded but the agent
claims failure, and agent claims success without mutation.

Every run evaluates `task_success`, `required_tool_calls`,
`forbidden_tool_calls`, `duplicate_mutation`,
`confirmation_before_mutation`, `final_state_match`, `turn_count_limit`, and
`timeout`. Results retain status, score, threshold, reason codes, evidence
references, evaluator metadata, and exact turn/tool/expectation references.

## Persistent campaign evidence

Evidence root:

```text
.agentops_runtime/open-cekura-acceptance-7bd65158/
```

This directory is intentionally ignored local runtime evidence, not source to
commit. Its 20 manifests all record:

```text
git_commit_sha: 7bd651583fa4553c57ce190d0c4b6617e2a73ddf
os: Windows-11-10.0.26200-SP0
python_version: 3.13.5
node_version: v22.23.2
```

Measured campaign results:

- `occampaign_acceptance_locator_alpha_7bd65158`: 10 runs, 5 explainable
  FailureCases, 5 RegressionCases, task success 90%, **BLOCK**;
- `occampaign_acceptance_locator_beta_7bd65158`: 10 runs, no failures, task
  success 100%, **PASS**;
- comparison gate `ocgate_1ad3b64bc7a4226be7373b6c`: **PASS**, mapped to
  MIS Approval `apoc_1ad3b64bc7a4226be7373b6c`.

The baseline gate `ocgate_27c8850cab97292dca28d3ce` maps to Approval
`apoc_27c8850cab97292dca28d3ce` and blocks for facts derived from the run:

- `appointment.mutation_before_confirmation`: `update_booking` before
  confirmation, rule `zero_tolerance.confirmation_before_mutation.v1`;
- `appointment.duplicate_request`: duplicate `update_booking`, rule
  `zero_tolerance.duplicate_mutation.v1`.

The candidate standalone gate `ocgate_40675da0261dae52790c1a12` is PASS and
maps to Approval `apoc_40675da0261dae52790c1a12`. No campaign ID or version
name is used to hard-code these decisions.

Both persistent campaign trees pass `evidence verify` with 10/10 manifests.
The portable acceptance independently changed a covered transcript, observed
exit code 4 and structured hash issues, restored it, and re-verified it. That
same acceptance deliberately gives baseline/candidate misleading locator IDs;
the observed facts still produce BLOCK/PASS.

RegressionCases and MIS Memory mappings:

- `ocregression_abea6c26794fd35dc4cbfc99` ->
  `memoc_559a5e065e28a4b000224815` (`final_state_match.v1`);
- `ocregression_8dc512e9b2bbd8f5425a57ad` ->
  `memoc_f525fa4c78fe7d04463f4d18` (`task_success.v1`);
- `ocregression_fbd81d8b0bf65d6379d1e001` ->
  `memoc_e9d0857658a992ddb0265510` (`duplicate_mutation.v1`);
- `ocregression_4a29ca1106805f9326bcb377` ->
  `memoc_e147c993f2e1d3b2f6a714cc`
  (`confirmation_before_mutation.v1`);
- `ocregression_71b45e3c76bd563b56be523b` ->
  `memoc_e1ba81665589d49a9d06db50` (`required_tool_calls.v1`).

The exact replay/drift rejection test passed: `1 passed`.

## Windows and UI acceptance

Both doctor entry points passed all critical checks on the accepted
implementation commit: Python 3.13.5, Node 22.23.2, npm 10.9.8, Git
2.50.1.windows.1, repository root, feature branch, commit, clean tree, write
access, SQLite 3.45.3, localhost bind, and UI dependencies. The optional
`OPENAI_API_KEY` was reported only as `MISSING`; no value was printed.

The real-browser acceptance used the installed `chrome.exe`, performed no
browser download or install, and returned:

```text
ok=true
browser_e2e=pass
campaign_count=2
run_count=20
candidate_api_rows=10
run_detail_read_back=true
```

It read the Overview BLOCK/PASS state and a Run Detail transcript, ToolCall,
eight evaluators, EvidenceManifest hashes, MIS Run reference, and release gate
from the built Vite application. The feature lives at
`/workspace/reliability` in `ui/start-building-app`; no second frontend or auth
system exists.

## Local verification record

Commands completed on implementation commit `7bd65158...`:

```text
python -m pytest open_cekura/tests/unit -q
  196 passed, 2 skipped
python -m pytest open_cekura/tests/integration -q
  180 passed, 1 skipped
complete unit + integration coverage
  376 passed, 3 skipped
python -m pytest open_cekura/tests/unit/test_evidence_hashes.py open_cekura/tests/integration/test_campaign_publication_recovery.py -q
  61 passed, 1 skipped
(incubator/research-lab) python -m pytest tests -q
  20 passed
python -m ruff check open_cekura scripts/open_cekura_ci_acceptance.py scripts/reliability_lab_ui_smoke.py
  PASS
python scripts/reliability_lab_ui_smoke.py
  PASS, 0 failures, 0 forbidden patterns
npm run build
  PASS, 2299 modules transformed
(ui/start-building-app) npm run test:reliability
  4 passed
python scripts/open_cekura_ci_acceptance.py --ui-dist ui/start-building-app/dist --result-path .agentops_runtime/open-cekura-final-acceptance-7bd65158.json --require-browser
  PASS, including tamper negative and real browser readback
```

The three skips are fail-closed link/reparse-point tests because the current
Windows account cannot create file or directory links. CI Python 3.10/3.11 is
the portability authority; local Python 3.13 results do not replace it.

Post-acceptance integration and CI fixes leading to remote-tested head
`31eef4d1...` were also verified:

```text
python -m pytest open_cekura/tests/unit -q
  197 passed, 2 skipped
clean WSL clone: python3 scripts/private_host_bundle_smoke.py
  PASS, including installed Host startup and upgrade/rollback
Linux-equivalent server + scripts/worker_session_refresh_smoke.py
  PASS, 2 runs, 3 distinct sessions, refresh count 2, final revoke true
python -m open_cekura.windows.doctor
python -m open_cekura.cli.main doctor
  both local entry points PASS on clean c366bf04; remote Windows jobs PASS on 31eef4d1
```

## Remote delivery truth

Canonical pull request:

```text
PR: https://github.com/geogejoy107-jpg/agentops-mis-mvp/pull/125
Base: main
Head: feat/open-cekura-windows-v0
Remote-tested head: 31eef4d1821af0c553e61d638cc1acf30db053e3
State at acceptance: OPEN, MERGEABLE, merge state CLEAN, not merged
```

PR #124 was the original fork PR. It was closed without merge after GitHub
required repository-admin approval before fork workflows could run; #125 uses
the exact upstream branch and is the only canonical review surface.

OpenCekura Windows and Ubuntu PR run:

```text
Run: 31538272116
URL: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31538272116
Head: 31eef4d1821af0c553e61d638cc1acf30db053e3
Conclusion: SUCCESS
ubuntu-latest / Python 3.10: SUCCESS, job 93934520758
ubuntu-latest / Python 3.11: SUCCESS, job 93934520919
windows-latest / Python 3.10: SUCCESS, job 93934520815
windows-latest / Python 3.11: SUCCESS, job 93934520831
```

Every matrix job installed minimal dependencies, ran unit and integration
tests, validated Scenario v1, tested the real TypeScript gate-head selector,
built the existing MIS UI, and uploaded bounded evidence. Both Ubuntu jobs ran
portable acceptance; both Windows jobs passed Windows Doctor and real Chrome
browser acceptance. The same head's independent push run `31538266896` also
finished SUCCESS in all four matrix jobs.

AgentOps MIS CI PR run:

```text
Run: 31538272115
URL: https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/31538272115
Head: 31eef4d1821af0c553e61d638cc1acf30db053e3
Conclusion: SUCCESS (6/6 jobs)
Backend deterministic smokes: SUCCESS, job 93934584077
UI build + Private Host bundle smoke: SUCCESS, job 93934584218
Runtime compatibility / Python 3.10: SUCCESS, job 93934584102
Runtime compatibility / Python 3.11: SUCCESS, job 93934584032
Relay recovery on real Linux systemd: SUCCESS, job 93934583989
Relay production install/recovery: SUCCESS, job 93934584082
```

No workflow required repository secrets, an external model, or a downloaded
browser. The earlier Windows code-page failure, omitted Reliability runtime in
the Private Host bundle, and a two-second session-refresh smoke race were each
root-caused, regression-tested, fixed, and re-run before the successful head
above was accepted.

The implementation agent must not merge the PR; Owner review is the final
merge authority.

## Known limitations

- The product direction is open-source, but this repository currently carries
  a proprietary local MVP license. Choosing and applying an open-source license
  is an explicit Owner decision; this PR does not silently relicense the wider
  repository and must not be advertised as open-source licensed before that
  decision.
- The public campaign CLI supports the deterministic Mock adapter in v0. The
  HTTP adapter is implemented and tested but is not yet exposed by campaign
  CLI selection.
- The LLM judge is optional. With no configured key it is `SKIPPED`; CI never
  depends on a model, never invents a score, and never converts evaluator
  errors into PASS.
- Reliability Lab v0 is evidence-first and read-only. A visual Scenario editor
  is future work.
- Voice, WebRTC, SIP, LiveKit, Pipecat, audio/ASR/TTS metrics, and real phone
  traffic are v0.2 scope.
- Deterministic mock campaigns prove behavior only under their checked-in
  fixtures and policies; they are not proof of production reliability or
  certification.
- Windows v0 does not rewrite the existing POSIX Private Host service manager.
- A filesystem manifest is an unsigned offline integrity check. It detects
  covered artifact changes but cannot resist an attacker rewriting the entire
  artifact tree, manifest, and local anchor together. MIS Artifact, Approval,
  and Audit records provide the separate authority ledger.
- Existing UI dependency debt remains: npm reports five audit findings, the
  Recharts 2.x deprecation warning, and the pre-existing large main-chunk
  warning. No unsafe automatic dependency rewrite was performed in this PR.

## Remaining v0.2 scope

After v0 is reviewed and merged: Pipecat and LiveKit adapters, audio-file
pipeline, ASR/TTS observation, interruption/dead-air/turn-latency/audio-clipping
metrics, SIP/telephone adapters, and real-world voice campaigns.
