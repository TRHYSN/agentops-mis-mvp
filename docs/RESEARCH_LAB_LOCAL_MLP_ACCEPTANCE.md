# Research Lab Local MLP Acceptance

Date: 2026-07-31
Branch: `codex/deep-learning-experiment-ops-v1`
Scope: local execution plus bounded MIS adapter and Human Workspace readback

## Result

The Research Lab incubator now executes a real dependency-free MLP rather than
only validating experiment specifications.

## Real run

- Model: standard-library `2 -> 8 -> 2` tanh/softmax MLP.
- Data: generated XOR classification; no download and no raw samples persisted.
- Stage: `confirmatory`.
- Seeds: `17`, `29`.
- Experiment: `exp_3f610efdcc6d731c`.
- Protocol hash: `3f610efdcc6d731c92354bdcadf9a99137eaa3d9f51b7bbd69f113f9e6968e36`.
- Trials: 2 completed.
- JobAttempts: 2 completed.
- Final validation accuracy: `1.0`, `1.0`.
- Metrics: 44 step records.
- Artifacts: 4 content-hashed model/summary references.
- Open deviations: 0.
- Scientific Claim Gate: eligible.

The run used a temporary state directory outside the repository. Its SQLite DB,
metrics, logs and model files are not committed.

## Failure retained during dogfood

The first confirmed attempt failed because the example requested `python` on a
host that exposes `python3`. Research Lab recorded two failed Attempts with a
bounded `command not found` summary and did not claim success. The spec was
corrected to `python3`; a fresh exact run then passed. Failed state was not
rewritten into successful evidence.

## Verification

```text
python3 -m unittest discover -s tests -v
python3 -m compileall -q research_lab examples tests
git diff --check
python3.11 -m venv <temporary-directory>
<temporary-directory>/bin/python -m pip install --no-deps .
<temporary-directory>/bin/research-lab validate-spec --spec examples/tiny_mlp_experiment.json
```

- 19 deterministic tests pass.
- Default `run-local` reports `dry_run:true` and performs no execution.
- `--confirm-run` creates the real ledger/run evidence.
- Default `sync-mis` reports `dry_run:true`; confirmed sync is local-Host only.
- The MIS bundle omits local state paths, raw stdout/stderr and artifact bodies.
- A clean Python 3.11 environment built and installed wheel `agentops-research-lab-0.4.0`.
- Re-running a completed protocol is idempotent.
- Re-running a failed Trial creates Attempt 2 and preserves the failed Attempt 1.
- A primary metric below its frozen threshold makes Claim Gate fail.
- Sensitive environment keys are rejected.

## MIS closure

- Bounded evidence sync into MIS Task/Run/Metric/Artifact/Evaluation/Audit passes.
- Human Workspace experiment list/detail and Claim Gate readback are implemented.
- Private Host `1.6.0-research-lab-local.1` is installed from commit `39f8ca0`;
  its repository-independent CLI completed the real MLP and idempotent MIS sync.

## Remaining product gates

- Run an authorized BWFormer or synthetic remote GPU Trial before claiming SSH/GPU readiness.
