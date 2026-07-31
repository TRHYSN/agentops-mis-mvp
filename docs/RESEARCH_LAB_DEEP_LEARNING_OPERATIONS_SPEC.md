# Research Lab Deep Learning Operations v1

Status: active implementation spec
Owner: AgentOps MIS Research line
Target: product-level local experiment closure before remote GPU scheduling

## Product outcome

AgentOps MIS must let a human define, run, inspect, govern and reproduce a deep
learning experiment without treating chat history, raw stdout or an external
tracker as the authority system.

The first dogfood target is a generated-data MLP. The intended project target is
the user's BWFormer / 3D-4D research workflow recorded in Notion. No private
dataset or model body is imported by this slice.

## Authority model

- Research Lab owns the frozen Experiment Protocol, Trial matrix, JobAttempt,
  step Metric, generated Artifact hash and scientific deviation decision.
- AgentOps MIS owns people/agent identity, Task/Run lifecycle, Agent Plan,
  Approval, organization-level Evaluation, Audit, reviewed Memory and delivery.
- GitHub owns code/spec/CI facts.
- Notion remains the human research notebook and project collaboration layer.
- MLflow, DVC, Hydra, Slurm/Submitit and Optuna may become adapters; none may
  redefine protocol identity or silently promote an exploratory run to a claim.

## Local v1 workflow

```text
Experiment JSON
  -> dry-run plan
  -> explicit confirmed local execution
  -> deterministic Trials and bounded JobAttempts
  -> metrics.jsonl + actuals.json + hashed Artifacts
  -> Protocol-to-Run deviations
  -> primary-metric threshold and Scientific Claim Gate
  -> local-only, explicit-confirm idempotent MIS evidence adapter
  -> Human Workspace experiment and Run readback
```

## Required experiment evidence

- research question and scientific stage;
- primary metric and optional frozen minimum threshold;
- initialization and training scope;
- code revision and clean/dirty state;
- dataset/model/environment references and versions;
- resolved configuration hash;
- deterministic seed/parameter matrix;
- runtime actuals and deviations;
- Trial/Attempt status, bounded error summary and output hashes;
- step metrics and content-hashed model/report Artifact references;
- claim eligibility with explicit reasons.

## Safety rules

- Real execution is dry-run by default and requires `--confirm-run`.
- The subprocess receives a minimal environment. Sensitive environment keys are
  rejected; credentials must come from a future approved runtime secret provider.
- Research Lab state, checkpoints, metrics, stdout/stderr and SQLite files stay
  outside Git.
- MIS receives summaries, IDs, URIs, hashes, scalar metrics and gate decisions,
  never private datasets, raw model bodies or complete stdout/stderr.
- The standalone executor never opens the MIS SQLite file. It sends a bounded
  evidence bundle through the local Host API; the Host remains the authority.
- Remote uncertainty blocks duplicate GPU retry until operator reconciliation.

## Delivery stages

1. Local Research Lab ledger/orchestrator and real MLP dogfood.
2. Idempotent MIS Task/Run/Artifact/Evaluation/Audit adapter.
3. Human Workspace experiment list/detail and Evaluation Room claim gate.
4. Installed Private Host acceptance and project template for BWFormer.
5. Authorized remote SSH/GPU execution and recovery.

## Acceptance

- Two confirmatory seeds run through a real subprocess.
- Both Trials complete and the final primary metric is at least `0.90`.
- Metrics, actuals and at least one model/report Artifact per Trial are recorded.
- A second identical invocation creates no duplicate completed Trial or Attempt.
- Provenance mismatch or below-threshold metrics close the claim gate.
- MIS shows the corresponding Task, Run, Artifact, Evaluation and Audit evidence.
- UI supports human creation/readback without exposing credentials or raw data.
- Tests, secret scan, diff check and CI pass; generated state is absent from Git.
