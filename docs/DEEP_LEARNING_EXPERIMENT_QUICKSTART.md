# Deep Learning Experiment Quickstart

This is the local-first path for using AgentOps MIS with an existing training
repository. Training stays on the experiment machine. AgentOps MIS receives
only governed metadata, scalar metrics, evidence hashes and review decisions.

## 1. Install the Research Lab CLI

```bash
cd incubator/research-lab
python3.11 -m pip install --user .
research-lab --help
```

For repository development, `PYTHONPATH=incubator/research-lab python3.11 -m
research_lab.cli ...` provides the same commands without installing globally.

## 2. Add three evidence hooks

The training entry point must run through `research-lab run-local`. Add only the
following bounded outputs; do not send data samples or model bodies to MIS.

```python
from research_lab.runtime import artifacts_dir, log_metric, record_actuals

for epoch in range(num_epochs):
    # Normal training code remains unchanged.
    log_metric("validation_accuracy", float(validation_accuracy), step=epoch)

checkpoint_path = artifacts_dir() / "best-checkpoint.pt"
save_checkpoint(checkpoint_path)
record_actuals(
    initialization_mode="from_checkpoint",
    training_scope="declared_ablation",
    dataset_version="dataset-version-id",
    model_architecture="model-architecture-id",
    checkpoint_selection_rule="best_validation_accuracy",
    metric_code_hash="sha256:<metric-code-digest>",
)
```

Research Lab hashes files written under `artifacts_dir()`. The MIS evidence
bundle contains the relative name, byte size and SHA-256 only.

## 3. Freeze an experiment JSON

Start from `incubator/research-lab/examples/tiny_mlp_experiment.json` and replace:

- `command` with the repository's real Python training entry point;
- `matrix` with the declared seeds or ablation parameters;
- `protocol` with the research question, primary metric and frozen conditions;
- `provenance` with Git revision, dataset/model/environment references and the
  resolved configuration;
- `integrity.minimum_primary_metric` with the claim threshold.

Keep state outside Git:

```bash
export RESEARCH_STATE="$HOME/Library/Application Support/AgentOps MIS/research-lab"
mkdir -p "$RESEARCH_STATE"
research-lab validate-spec --spec experiment.json
research-lab run-local --spec experiment.json --state-dir "$RESEARCH_STATE"
research-lab run-local --spec experiment.json --state-dir "$RESEARCH_STATE" --confirm-run
```

The first `run-local` is a dry-run plan. Real training occurs only with
`--confirm-run`.

## 4. Publish bounded evidence to local MIS

```bash
research-lab sync-mis \
  --experiment-id <experiment_id> \
  --state-dir "$RESEARCH_STATE"

research-lab sync-mis \
  --experiment-id <experiment_id> \
  --state-dir "$RESEARCH_STATE" \
  --confirm-sync
```

The first command is a dry-run. Confirmed v1 sync accepts only a local HTTP Host.
If machine authentication is enabled, the CLI reads `AGENTOPS_API_KEY` from the
process environment and never prints or stores it.

## 5. Review in AgentOps MIS

Use the Human Workspace Experiments page to inspect Experiment, Trial, Metric,
Artifact hash and Claim Gate state. Use the Governance Console for the linked
Task, Run, Evaluation and Audit evidence. Human approval remains required before
publishing a checkpoint, report, paper claim or external dataset result.

## BWFormer first slice

For the current BWFormer work, begin with one bounded smoke or ablation protocol:

- one code revision and resolved config;
- an existing nonprivate dataset version reference;
- two declared seeds only if resources permit;
- peak memory, training loss and validation metric as scalar evidence;
- checkpoint and summary hashes, not checkpoint bodies;
- a 24 GB memory constraint recorded in protocol/actuals;
- no automatic remote retry after an uncertain GPU disconnect.

OOM profiling, baseline repair and geometry ablations should be separate
Experiments. Do not mix exploratory parameter search with a confirmatory Claim
Gate.
