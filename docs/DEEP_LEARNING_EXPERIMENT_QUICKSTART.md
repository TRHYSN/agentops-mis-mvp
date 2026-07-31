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
import hashlib
import json

def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

for epoch in range(num_epochs):
    # Normal training code remains unchanged.
    log_metric("validation_accuracy", float(validation_accuracy), step=epoch)

checkpoint_path = normal_training_output_dir / "best-checkpoint.pt"
save_checkpoint(checkpoint_path)  # The model body stays on the training machine.
checkpoint_manifest = {
    "logical_name": "best-checkpoint.pt",
    "size_bytes": checkpoint_path.stat().st_size,
    "sha256": file_sha256(checkpoint_path),
    "selection_rule": "best_validation_accuracy",
}
(artifacts_dir() / "checkpoint-manifest.json").write_text(
    json.dumps(checkpoint_manifest, sort_keys=True),
    encoding="utf-8",
)
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
bundle contains the relative name, byte size and SHA-256 only. Keep large
checkpoints in the normal training output directory: the v1 MIS ingest contract
accepts metadata artifacts up to 50 MiB, so `artifacts_dir()` should contain a
small checkpoint manifest and bounded summaries rather than model bodies.

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

For the manifest-verified Fusion v2 overlay, prepare a machine-local smoke spec
without committing absolute paths:

```bash
python3 scripts/prepare_bwformer_research_lab_smoke.py \
  --project-root /path/to/BWformer1-fusion-v2 \
  --python /path/to/bwformer-cpu/bin/python \
  --output "$RESEARCH_STATE/bwformer-fusion-smoke.json"

agentops experiment validate \
  --spec "$RESEARCH_STATE/bwformer-fusion-smoke.json"
agentops experiment run \
  --spec "$RESEARCH_STATE/bwformer-fusion-smoke.json" \
  --state-dir "$RESEARCH_STATE/bwformer" \
  --confirm-run
```

This adapter verifies every file declared by `MANIFEST.json`, executes only the
Fusion config dry-run and synthetic CPU component smoke, and records bounded
metrics plus a summary hash. It does not open Building3D, start CUDA, or claim
model-quality evidence.
