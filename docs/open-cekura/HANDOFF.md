# OpenCekura Windows v0 Handoff

Status: local implementation and acceptance complete; PR and remote CI evidence pending.

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
Implementation commit accepted locally: eb6855b0a51596e7bd79915d6a34cd4ad17b47e7
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

The accepted persistent database contains 2 Tasks, 2 Plans, 20 Runs, 56 Tool
Calls, 160 Evaluations, 20 Artifacts, 3 Approvals, 5 Memories, and 294 Audit
rows. All 20 Reliability runs, all 160 evaluations, and all 20 evidence
manifests have non-null MIS mappings.

Campaign mappings:

- baseline Task `tskoc_01a16cde18e2c0169a8bcd6e`, Plan
  `planoc_5165df2ba6f1f9cdf6cabbef`;
- candidate Task `tskoc_30fa0932ba764d410f47781a`, Plan
  `planoc_fb1bb4380cd9ef03e9734252`.

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
.agentops_runtime/open-cekura-acceptance-eb6855b0/
```

This directory is intentionally ignored local runtime evidence, not source to
commit. Its 20 manifests all record:

```text
git_commit_sha: eb6855b0a51596e7bd79915d6a34cd4ad17b47e7
os: Windows-11-10.0.26200-SP0
python_version: 3.13.5
node_version: v22.23.2
```

Measured campaign results:

- `occampaign_acceptance_baseline_eb6855b0`: 10 runs, 5 explainable
  FailureCases, 5 RegressionCases, task success 90%, **BLOCK**;
- `occampaign_acceptance_candidate_eb6855b0`: 10 runs, no failures, task
  success 100%, **PASS**;
- comparison gate `ocgate_d09acef3b2a4cb9b64fd348b`: **PASS**, mapped to
  MIS Approval `apoc_d09acef3b2a4cb9b64fd348b`.

The baseline gate `ocgate_97edc11198eff27f96dfe1db` maps to Approval
`apoc_97edc11198eff27f96dfe1db` and blocks for facts derived from the run:

- `appointment.mutation_before_confirmation`: `update_booking` before
  confirmation, rule `zero_tolerance.confirmation_before_mutation.v1`;
- `appointment.duplicate_request`: duplicate `update_booking`, rule
  `zero_tolerance.duplicate_mutation.v1`.

The candidate standalone gate `ocgate_8079697d4fb318b3bdf3aaf7` is PASS and
maps to Approval `apoc_8079697d4fb318b3bdf3aaf7`. No campaign ID or version
name is used to hard-code these decisions.

Both persistent campaign trees pass `evidence verify` with 10/10 manifests.
The portable acceptance independently changed a covered transcript, observed
exit code 4 and structured hash issues, restored it, and re-verified it. That
same acceptance deliberately gives baseline/candidate misleading locator IDs;
the observed facts still produce BLOCK/PASS.

RegressionCases and MIS Memory mappings:

- `ocregression_45288c04732eab9c6ba40d60` ->
  `memoc_66639f6e1c5ea8c93280cf2b` (`final_state_match.v1`);
- `ocregression_742d93944bc1c596a5b965ac` ->
  `memoc_a9719f92bd31dcbd6fd9643f` (`task_success.v1`);
- `ocregression_8a26657e848d0316c4bc7692` ->
  `memoc_976d80f5def016b0aaeba5ca` (`duplicate_mutation.v1`);
- `ocregression_d7f66f3a3c1a356dce78f267` ->
  `memoc_f3dfa896e7c5f613f39f32c9`
  (`confirmation_before_mutation.v1`);
- `ocregression_f75319695be7d07ae4bcb422` ->
  `memoc_58f896c8d11cd68d1149134c` (`required_tool_calls.v1`).

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

Commands completed on implementation commit `eb6855b0...`:

```text
python -m pytest open_cekura/tests/unit -q
  179 passed, 2 skipped
python -m pytest open_cekura/tests/integration -q
  138 passed, 1 skipped
python -m pytest open_cekura/tests -q
  317 passed, 3 skipped
(incubator/research-lab) python -m pytest tests -q
  20 passed
python -m ruff check open_cekura scripts/open_cekura_ci_acceptance.py scripts/reliability_lab_ui_smoke.py
  PASS
python scripts/reliability_lab_ui_smoke.py
  PASS, 0 failures, 0 forbidden patterns
npm run build
  PASS, 2299 modules transformed
python scripts/open_cekura_ci_acceptance.py --ui-dist ui/start-building-app/dist --result-path .agentops_runtime/open-cekura-final-acceptance-eb6855b0.json --require-browser
  PASS, including tamper negative and real browser readback
```

The three skips are fail-closed link/reparse-point tests because the current
Windows account cannot create file or directory links. CI Python 3.10/3.11 is
the portability authority; local Python 3.13 results do not replace it.

## Remote delivery truth

The remote PR and GitHub Actions run do not exist at this document revision.
They must be filled with the real PR URL, run ID/URL, exact tested head SHA, and
all four Ubuntu/Windows x Python 3.10/3.11 job conclusions after push. Do not
interpret workflow source or local tests as remote CI evidence.

The implementation agent must not merge the PR; Owner review is the final
merge authority.

## Known limitations

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
