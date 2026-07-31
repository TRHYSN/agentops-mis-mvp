# AgentOps Research Lab — Standalone v0.4.1

A path-isolated, local-first prototype for **asynchronous parallel experiments, remote SSH execution, frozen provenance, and scientific-integrity gates**.

The module is independently installable. It owns its standalone SQLite WAL ledger, workspaces, tests, reports, and event export, while leaving AgentOps MIS workspace, approval, delivery, reviewed-memory, and audit authority untouched.

## Capability baseline

### v0.3 execution foundation

- Immutable Experiment Protocol and SHA-256 identity.
- Explicit stages: `smoke`, `pilot`, `search`, `confirmatory`, `ablation`, `robustness`, and `reproduction`.
- Parameter-matrix expansion into scientific Trials.
- Bounded asynchronous execution and per-server concurrency caps.
- Strict separation of scientific `Trial` and retryable infrastructure `JobAttempt`.
- Local executor plus a guarded OpenSSH executor adapter.
- Deterministic allowlisted staging and fail-closed remote uncertainty.
- Runtime actuals, Protocol-to-Run deviations, Scientific Claim Gate, reports, read-only site, and redacted event export.

### v0.3.1 provenance integrity

Research Lab now adapts selected MLflow, DVC, and Hydra ideas into first-party protocol evidence:

```text
code revision
+ dataset references
+ model/checkpoint references
+ environment reference
+ fully resolved configuration
= provenance_hash
```

For `confirmatory`, `robustness`, and `reproduction` by default:

- the code revision must be declared and clean;
- at least one dataset reference is required;
- a fully resolved configuration is required;
- checkpoint-based initialization requires a model reference;
- runtime actuals must echo provenance, resolved-config, and code-revision evidence;
- any mismatch closes the claim gate even when the process exits successfully.

External tools remain adapters. MLflow may later track runs/models/datasets; DVC may version data/models; Hydra may compose configs; Slurm/Submitit may schedule jobs; Optuna may suggest search Trials. None of them owns Research Lab protocol approval, Trial identity, deviation review, or claim eligibility.

### v0.4 local execution ledger

- SQLite WAL ledger for Experiment, Trial, JobAttempt, Metric, Artifact and ProtocolDeviation.
- Dry-run by default; a real subprocess requires explicit `--confirm-run`.
- Deterministic Experiment/Trial identities and idempotent readback of completed Trials.
- Bounded Trial concurrency, timeout and retry through `LocalExecutor`.
- Hash-only stdout/stderr evidence plus content-hashed artifact references.
- Scientific Claim Gate checks provenance, deviations, seed coverage, primary-metric presence and the optional frozen minimum metric threshold.
- A dependency-free `2 -> 8 -> 2` MLP example that trains on generated XOR data without downloads or persisted raw samples.

## Install

```bash
python -m venv .venv
. .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .
```

Python 3.11 or newer is required. Real SSH execution also requires a system OpenSSH client and approved key/agent-based authentication.

## Validate locally

```bash
research-lab inventory --workdir .
research-lab validate-spec --spec examples/confirmatory_experiment.json
research-lab validate-spec --spec examples/ssh_experiment.json --servers examples/servers.example.json
```

Plan and execute the local MLP experiment:

```bash
STATE_DIR="$(mktemp -d)"
research-lab run-local --spec examples/tiny_mlp_experiment.json --state-dir "$STATE_DIR"
research-lab run-local --spec examples/tiny_mlp_experiment.json --state-dir "$STATE_DIR" --confirm-run
research-lab show --experiment-id <experiment_id> --state-dir "$STATE_DIR"
research-lab sync-mis --experiment-id <experiment_id> --state-dir "$STATE_DIR"
research-lab sync-mis --experiment-id <experiment_id> --state-dir "$STATE_DIR" --confirm-sync
```

The first command is a no-execution plan. The confirmed command writes its
standalone ledger and generated artifacts only under `STATE_DIR`; do not point
that directory at the Git repository. `sync-mis` is also dry-run by default.
The confirmed sync supports only a local HTTP AgentOps MIS Host and publishes a
bounded evidence bundle: protocol/Trial identities, scalar metrics, hashes,
deviation summaries and the Claim Gate. State paths, logs, artifact bodies,
credentials and raw model output are omitted.

## BWFormer Fusion v2 smoke

AgentOps MIS includes a bounded adapter for the manifest-packaged Fusion v2
overlay. Generate its machine-local spec from the repository root:

```bash
python3 scripts/prepare_bwformer_research_lab_smoke.py \
  --project-root /path/to/BWformer1-fusion-v2 \
  --python /path/to/bwformer-cpu/bin/python \
  --output /path/outside/git/bwformer-smoke.json
```

The adapter verifies the overlay manifest, runs its config dry-run and
synthetic CPU component smoke, and emits only scalar metrics, actual conditions
and a small hash summary. It neither reads Building3D nor runs the full CUDA
model. Large checkpoints must remain in the training repository; place only a
small checkpoint manifest under `artifacts_dir()`.

## Provenance example

```json
{
  "provenance": {
    "code": {
      "repository": "https://github.com/example/project",
      "revision": "0123456789abcdef",
      "dirty": false
    },
    "datasets": [
      {
        "name": "train-data",
        "uri": "dvc://datasets/train",
        "version": "dvc-rev-001",
        "digest": "sha256:0123456789abcdef"
      }
    ],
    "models": [],
    "environment": {
      "name": "python-env",
      "uri": "file://environment.lock",
      "version": "v1",
      "digest": "sha256:fedcba9876543210"
    },
    "resolved_config": {
      "optimizer": "adamw",
      "learning_rate": 0.0001,
      "epochs": 100
    }
  }
}
```

References are metadata-only and reject unstable or secret-bearing values. Research Lab injects the frozen provenance/config/code hashes into the runtime evidence path.

## SSH execution

```bash
cp examples/servers.example.json servers.local.json
research-lab server-list --servers servers.local.json
research-lab server-probe --servers servers.local.json --profile lab-gpu-01
research-lab validate-spec --spec examples/ssh_experiment.json --servers servers.local.json
```

A server profile stores non-secret connection/capability metadata and optional local file references. Changing the frozen server profile blocks old queued work until a new Protocol version is submitted.

## Verification

```bash
python -m compileall -q research_lab examples tests
python -m unittest discover -s tests -v
```

This uploaded slice has 19 deterministic tests. They cover the local CLI,
read-only server profile inspection, deterministic matrix expansion, frozen
provenance, unsafe reference rejection, strict-stage provenance requirements,
checkpoint initialization, local execution/idempotency, retained failed Attempts, metric thresholds and
the real dependency-free MLP example. A real authorized SSH/GPU target is still
required for remote infrastructure dogfood.

## Repository strategy

```text
agentops-mis-mvp
└── incubator/research-lab/   # standalone package; no direct MIS database import
```

- GitHub PR: `#32`
- Target: `codex/osbi-v1-1-mainline`
- Branch: `codex/research-lab-ssh-mainline`
- Current source slice: runnable CLI smoke plus protocol/provenance contracts

## Next lanes

1. Idempotent AgentOps MIS evidence adapter and Human Workspace experiment readback.
2. Real SSH/GPU dogfood, cancellation, detached polling, and orphan reconciliation.
3. Optional MLflow tracker adapter with external IDs only.
4. DVC/Hydra import helpers for provenance capture.
5. Slurm/Submitit executor with scheduler job and array identities.
6. Optuna ask/tell search controller; winners must be resubmitted as confirmatory protocols.
7. Paper Claim Ledger and Paper-to-Protocol / Run-to-Claim consistency.
