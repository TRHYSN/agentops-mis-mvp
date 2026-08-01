# BWFormer Research Lab Local Acceptance

Date: 2026-07-31
Branch: `codex/bwformer-research-lab-smoke-v1`
Scope: manifest-verified BWFormer Fusion v2 CPU smoke and bounded MIS evidence sync

## Result

AgentOps Research Lab executed the user's real local BWFormer Fusion v2 overlay
through a governed Trial. This is no longer the built-in XOR example:

- Project: `~/Documents/bw/BWformer1-fusion-v2`
- Overlay version: `2.0.0`
- Manifest files verified: `45`
- Config: `configs/fusion_24g_safe.yaml`
- Runtime: isolated Python 3.11, PyTorch 2.13.0, NumPy 2.4.6,
  SciPy 1.17.1 and PyYAML 6.0.3
- Building3D opened: no
- CUDA/full model executed: no
- Credentials or private data required: no

The adapter executed the real overlay's config dry-run and deterministic Torch
CPU component smoke. It did not copy or modify the BWFormer project.

## Research Lab evidence

- Experiment: `exp_92270eea5aaa6d3d`
- Protocol hash:
  `92270eea5aaa6d3dd259bc71007bdf08103a1f48556b63c15026e7061a6c097f`
- Provenance hash:
  `2fab29146fbb317018cb974e5f20a81e7c28fe08ef47d8f285a3915ea49726cb`
- Trial: `trl_d360890818c73d69`
- Attempt: `att_73964838fc714529`
- Status: completed
- Metrics: 6
- Artifacts: 1 bounded summary
- Open protocol deviations: 0

The Scientific Claim Gate is intentionally ineligible. A `smoke` stage proves
that the declared code/config/runtime checks executed; it cannot support a
model-quality or paper claim.

## MIS authority readback

Confirmed local sync created:

- Task: `tsk_research_180eba9eff9a72b68e70`
- Run: `run_research_657a2d88894455c4c1cb`
- Evaluation: `eval_research_52132e7950a3cd2af509`
- Artifact: `art_research_8a80dfa46711257a1a7e`

The Run is `completed` with runtime type `research_lab_local`. The Evaluation is
a Research Claim Gate and reports `fail` because exploratory smoke evidence is
not claim-eligible, not because the process failed.

First sync returned:

```text
created 13
unchanged 2
updated 0
HTTP 201
```

Exact replay returned:

```text
created 0
unchanged 15
updated 0
HTTP 200
idempotent_replay true
```

## Privacy and artifact boundary

- The local spec and Research Lab SQLite state live outside Git.
- The synchronized protocol replaces absolute command and workdir values with
  local-path omission markers.
- A serialized bundle check confirmed `/Users/wuji` was absent.
- Raw stdout/stderr, source files, data samples and artifact bodies were not
  synchronized.
- The only artifact is a 1,154-byte summary; MIS stores its logical URI, byte
  size and SHA-256.
- Large checkpoints remain on the training machine. Research Lab should receive
  a small checkpoint manifest, not the model body.

## Commands exercised

```text
python3.11 scripts/prepare_bwformer_research_lab_smoke.py ...
agentops experiment validate ...
agentops experiment run ...                       # dry-run
agentops experiment run ... --confirm-run
agentops experiment show ...
agentops experiment sync ...                      # dry-run
agentops experiment sync ... --confirm-sync
agentops experiment sync ... --confirm-sync       # exact replay
agentops task get --task-id tsk_research_180eba9eff9a72b68e70
agentops run get --run-id run_research_657a2d88894455c4c1cb
agentops artifact list --run-id run_research_657a2d88894455c4c1cb
```

## Deterministic verification

- Research Lab unit suite: 19 passed.
- BWFormer adapter fixture contract: passed on Python 3.11 and 3.14; explicitly
  reports `fixture_only:true`.
- Root `agentops experiment` delegation and secret-boundary smoke: passed.
- Python compile for server, CLI, core, scripts and Research Lab: passed.
- Research Lab 0.4.1 wheel build: passed and includes
  `research_lab/bwformer_adapter.py`.
- `git diff --check`: passed.

## Remaining gates

This acceptance does not claim full BWFormer training readiness. The machine has
the Fusion v2 overlay but not a complete local `BWformer1` clone, Building3D
data or NVIDIA CUDA. The next product slice requires:

1. Install Fusion v2 into a clean, versioned BWformer1 clone.
2. Add bounded metric/actual/checkpoint-manifest hooks to the Fusion training
   path.
3. Run the two-epoch `fusion_24g_safe` protocol on an authorized 24 GB CUDA
   machine.
4. Verify disconnect reconciliation and exact replay before any longer
   experiment.
5. Only then create pilot/ablation protocols; smoke evidence must not be
   promoted into a scientific claim.
