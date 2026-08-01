# Handoff — Research Lab Embedded Template Foundation

> Date: 2026-07-28
> Status: Proposed recovery candidate
> Canonical: false
> Target branch: `template/research-lab-embedded-v0-recovery-20260801`
> Base: verified `main@99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
> Historical source: `template/research-lab-embedded-v0` @ `40db4e22387771cf41e7dfed2490eab283fe0b6f`

## What this slice changes

- Adds a versioned, zero-dependency Research Lab embedded template contract.
- Adds Core MIS authority mappings for research-domain objects.
- Makes same-AppShell navigation and in-app summary projection mandatory.
- Adds a credential-free Building Wireframe Research Lab reference instance.
- Adds deterministic validation and negative tests.

## What this slice does not change

- No Core schema migration.
- No runtime/worker behavior.
- No MLflow/W&B/Jupyter/SSH side effect.
- No Commercial, Private Host, Relay or Spatial OS changes.
- No canonical Project State or Decision change.

## Verification commands

```bash
python3 incubator/research-lab/template/scripts/validate_template_manifest.py \
  --manifest incubator/research-lab/template/manifest.json \
  --instance incubator/research-lab/template/examples/building-wireframe-lab.instance.json

python3 -m unittest \
  incubator/research-lab/template/tests/test_template_manifest.py
```

## Remaining gates

1. Reconcile this contract with the Research Lab runtime and BWFormer smoke already merged through PRs #113–#115.
2. Confirm manifest capability names and proposed Vite routes against current implementation.
3. Implement `MISRepositoryAdapter` for Workspace/Task/Run/Approval/Artifact/Evaluation/Audit reuse.
4. Add embedded Research routes to the current Vite AppShell.
5. Add safe External Base summary projections and return-route context.
6. Run exact-head CI and independent review.

## Next single action

Implement the read-only `ResearchTemplateRegistry` and MIS-backed template install preview without changing Core authority tables.

## Record status

```yaml
GitHub: recovery_draft_pr_pending
Notion: pending_write
Canonical_state_changed: false
```
