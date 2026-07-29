# Relay Activation Recovery Preview Acceptance

## Scope

This acceptance covers the private, read-only recovery preview controller in
`agentops_mis_cli.relay_activation_recovery_preview`.

The production entrypoint opens the exact activation journal under its
lifecycle lock, obtains a same-root scan capability, loads one exact recovery
snapshot, performs scanner/systemd/scanner stable observation, reloads the
snapshot, and compiles one bounded hash-bound recovery decision. It remains
absent from `agentops-relayctl`.

## Stable Read Contract

One preview is accepted only when:

- `plan_sha256` is canonical and the requested outcome is exactly `resume` or
  `rollback`;
- both journal loads return the same validated immutable chain and optional
  receipt;
- both prerequisite scans return the same exact snapshot;
- every scan uses the capability issued by the same live lifecycle lock;
- the systemd read is bound to the scanner-observed `systemctl` identity; and
- the deterministic recovery compiler returns a valid bounded projection.

The locked scanner independently hashes the raw journal tree before and after
each scan. The controller also compares the parsed journal snapshot across the
entire scanner/systemd/scanner sequence. A revision or receipt added anywhere
inside the preview therefore fails closed as `activation_recovery_required`.

The output contains only action, operation, step, reason, journal/receipt/
observation hashes, requested outcome, and `decision_sha256`. It never includes
configuration, certificate, key, route, path, prompt, response, credential, or
raw journal content.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_recovery_preview.py \
  scripts/relay_activation_recovery_preview_smoke.py
python3 scripts/relay_activation_recovery_preview_smoke.py
python3.11 scripts/relay_activation_recovery_preview_smoke.py
python3 scripts/relay_activation_scan_smoke.py
python3 scripts/relay_activation_recovery_decision_smoke.py
python3 scripts/relay_activation_recovery_snapshot_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

Expected summary:

```json
{
  "cli_surface_exposed": false,
  "decision_hash_deterministic": true,
  "invalid_input_zero_read": true,
  "journal_drift_rejected": true,
  "lifecycle_lock_composed": true,
  "network_used": false,
  "ok": true,
  "prerequisite_drift_rejected": true,
  "private_payload_omitted": true,
  "systemd_failure_bounded": true,
  "systemd_mutation_performed": false,
  "write_scope": "fixture_journal_only"
}
```

The smoke also injects a valid journal revision during the systemd read, changes
one prerequisite between scans, checks bounded systemd failure mapping, proves
invalid input reaches no host callback, and statically rejects mutation or CLI
composition.

## Truth Boundary

This slice selects no action and changes no host state. It does not:

- publish a revision or receipt;
- run daemon reload, enable, start, stop, or disable;
- expose recovery through CLI, API, or browser UI;
- confirm a decision hash;
- execute or terminalize one recovery step; or
- claim real interrupted Linux systemd acceptance.

The private writer in
`RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md` now reloads this same
journal head and binds `decision_sha256` for one non-systemd observation,
forward or rollback receipt, terminal, or complete action. The rollback
terminal contract is fixture-accepted. The private executor now composes one
confirmed scanner-bound recovery step with the production store; real Linux
interruption acceptance and an operator-facing confirmation surface remain the
next gates.
