# Research Lab to AgentOps MIS Acceptance

Date: 2026-07-31
Branch: `codex/deep-learning-experiment-ops-v1`
Source baseline: `2a3280b` (`origin/main`)

## Product slice

The local Research Lab executor and AgentOps MIS authority ledger now form one
real, bounded experiment workflow:

```text
frozen experiment JSON
  -> explicit local execution
  -> standalone Experiment / Trial / Attempt ledger
  -> scalar metrics + content-hashed artifacts + Claim Gate
  -> explicit local MIS evidence sync
  -> MIS Task / Run / Evaluation / Artifact / Audit
  -> Human Workspace experiment readback
```

The MIS server does not execute the training shell command and does not receive
the Research Lab state directory, raw stdout/stderr, artifact/model bodies,
credentials, raw prompts or raw responses.

## Real local evidence

- Experiment: `exp_3f610efdcc6d731c`
- Protocol hash: `3f610efdcc6d731c92354bdcadf9a99137eaa3d9f51b7bbd69f113f9e6968e36`
- Trials: 2 completed, seeds 17 and 29
- Attempts: 2 completed
- Metrics: 44
- Final validation accuracy: 1.0 for both Trials
- Research artifacts: 4 content-addressed model/summary references
- Claim Gate: eligible
- MIS Task: `tsk_research_9bdcdfef261b5aa79ad7`
- MIS Runs: `run_research_e4a2bbd3213db6a3588b`, `run_research_d650a6a7a75d08f2634d`
- MIS Evaluation: `eval_research_d8e188ed4800c9d682f9`
- MIS Audit records for the evidence hash: 1

The final installed acceptance used AgentOps MIS Private Host
`1.6.0-research-lab-local.1`, packaged from commit `39f8ca0`, and a Research Lab
state directory under `~/.agentops/research-lab`. The installer created a
verified pre-update ledger backup, preserved Owner state, kept
`1.6.0-private-host-preview.44` as the rollback target and restarted the
host-only launchd service. No database or Research Lab state is committed.

## Idempotency and failure behavior

- First confirmed sync returned HTTP 201 and created the authority objects.
- The same evidence replayed through `agentops experiment sync --confirm-sync`
  returned HTTP 200 with `idempotent_replay:true`.
- Replay reported 59 unchanged objects, zero created and zero updated.
- Unknown/raw fields and nested raw prompt fields return HTTP 400 without ledger
  mutation.
- Experiment/Trial/Attempt identity conflicts return HTTP 409 through an atomic
  rollback boundary.
- A failed local Trial keeps Attempt 1. A successful exact rerun creates Attempt
  2 and marks superseded old deviations without deleting them.

## Interfaces

```text
POST /api/research/experiments/ingest
GET  /api/research/experiments
GET  /api/research/experiments/:id

agentops experiment validate
agentops experiment run
agentops experiment show
agentops experiment sync
```

Human Workspace routes:

```text
/workspace/experiments
/workspace/experiments/:id
```

The UI has complete English/Chinese labels and no mock fallback.

## Installed product acceptance

- `agentops host version` reports packaged commit `39f8ca0` and previous version
  `1.6.0-private-host-preview.44`.
- `agentops host status` and `agentops host doctor` report a ready loopback Host,
  ready Owner login and private Tailscale URL with Funnel disabled.
- The installed, repository-independent `agentops experiment` command validates,
  plans, executes, shows and syncs the generated-data MLP.
- Installed first sync created 59 authority objects; exact replay returned 59
  unchanged objects and no duplicate writes.
- Machine-facing readback returned the completed Task, both completed Runs, four
  content-addressed Artifacts and the passing Claim Gate Evaluation.
- `/workspace/experiments` is present in the installed production UI and remains
  protected by Human login.

## Verification

```text
(cd incubator/research-lab && python3 -m unittest discover -s tests -v)
python3 scripts/research_experiment_api_smoke.py
python3 scripts/agentops_experiment_cli_smoke.py
python3 scripts/private_host_bundle_smoke.py
python3 scripts/relay_activation_namespace_install_smoke.py
python3 scripts/relay_offline_install_smoke.py
python3 -m py_compile server.py agentops_mis_core/research_experiments.py
(cd ui/start-building-app && npm run build)
git diff --check
```

- 19 Research Lab tests pass.
- Isolated API ingest/list/detail and idempotency smoke passes.
- Root AgentOps CLI delegation and secret boundary smoke passes.
- Private Host packaging includes the Research Lab module, example and quickstart;
  an isolated installed CLI validates the protocol without repository access.
- Relay exact-wheel allowlisting includes the two new Research Lab modules; both
  offline install and activation namespace regressions pass.
- A clean Python 3.11 environment installs `agentops-research-lab-0.4.0` and
  validates the real MLP protocol.
- The production UI build passes. Browser acceptance shows two completed Trials,
  four projected final metrics, four Artifact hashes, one passing Evaluation and
  links from each Trial to its authority Run.

## Remaining limits

- Local subprocess execution is complete; remote SSH/GPU execution still needs
  an authorized machine, disconnect reconciliation and no-duplicate retry proof.
- The first BWFormer run still needs a project-owned training entry point to add
  the three bounded evidence hooks.
- Publishing a checkpoint/report or externally visible research claim remains a
  Human Approval action; this slice records local evidence only.
