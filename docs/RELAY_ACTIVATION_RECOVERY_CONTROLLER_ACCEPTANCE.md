# Relay Activation Recovery Controller Acceptance

## Scope

This acceptance covers the private exact-confirmed non-systemd recovery writer
in `agentops_mis_cli.relay_activation_recovery_controller`.

It consumes an explicit `decision_sha256`, repeats the complete stable recovery
observation against the same store, and performs at most one of:

- append one already-observed journal revision;
- publish one forward success receipt;
- publish one verified rollback receipt;
- append one terminal revision for an existing exact receipt; or
- return an already-complete terminal state without writing.

Its private production entrypoint opens the exact store under the lifecycle
lock, obtains the same-root locked scanner capability, and uses the production
read-only systemd adapter. It has no mutation adapter, CLI, API, or browser
caller.

## Confirmation And Write Contract

Malformed confirmation hashes fail before reading the store. A canonical but
stale hash fails after a read-only preview and before any write. A blocked
decision and every `run_step` decision also fail with zero writes.

The snapshot loader is not injectable independently from the store. The same
store that supplies the two stable journal snapshots receives the selected
write, preventing a confirm-one/write-another split.

For each supported action:

- `record_observation` rebuilds the canonical observation from the confirmed
  prerequisite/systemd snapshot, requires its hash to match the decision, and
  appends exactly one `observed` revision;
- `publish_success_receipt` requires a forward verified observation and
  publishes exactly one `activation_succeeded` receipt;
- rollback `record_observation` uses the dedicated restored-state evidence,
  requires both ownership flags to be clear, and appends exactly one
  `rollback_verified` revision;
- `publish_rollback_receipt` requires that exact observed rollback verification
  and publishes exactly one `rollback_succeeded` receipt bound to
  `service_state_rolled_back`;
- `publish_terminal_revision` binds an existing exact orphan receipt in exactly
  one terminal revision; and
- `complete` validates and projects the existing terminal state with no write.

A write-tracking wrapper switches the error boundary only immediately before
calling `publish_revision` or `publish_receipt`. A failure after that point
returns only `activation_recovery_required`; write-before-validation failures
retain their bounded zero-write error.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_recovery_controller.py \
  agentops_mis_cli/relay_activation_recovery_preview.py \
  scripts/relay_activation_recovery_controller_smoke.py
python3 scripts/relay_activation_recovery_controller_smoke.py
python3.11 scripts/relay_activation_recovery_controller_smoke.py
python3 scripts/relay_activation_recovery_preview_smoke.py
python3 scripts/relay_activation_recovery_decision_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_activation_scan_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

Expected summary:

```json
{
  "blocked_action_zero_write": true,
  "cli_surface_exposed": false,
  "complete_zero_write": true,
  "confirmation_zero_write": true,
  "network_used": false,
  "observation_one_write": true,
  "ok": true,
  "post_write_failure_retained": true,
  "private_payload_omitted": true,
  "production_lock_composed": true,
  "production_one_write": true,
  "receipt_one_write": true,
  "rollback_observation_one_write": true,
  "rollback_receipt_one_write": true,
  "rollback_terminal_contract": true,
  "systemd_action_zero_write": true,
  "systemd_mutation_performed": false,
  "terminal_one_write": true,
  "write_scope": "fixture_journal_only"
}
```

The smoke exercises invalid and stale confirmation, blocked rollback, an
unsupported forward mutation, observation completion after an interrupted
daemon reload, success-receipt publication, orphan-receipt terminalization,
dedicated rollback verification, rollback-receipt publication, rollback
terminalization, idempotent complete for both terminal states, and a fault
injected after a durable revision write. It also replaces the production
opener, locked scanner, and systemd reader with bounded fixtures and proves one
store/capability pair receives exactly one write.

## Truth Boundary

This slice does not execute `daemon-reload`, `enable`, `start`, `stop`, or
`disable` or expose a recovery command. It composes the production recovery
store and completes the rollback verification/receipt/terminal contract
against the injected exact store. The separate private executor in
`RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md` now binds the same confirmed
decision to exactly one scanner-bound systemd step. Real Linux interruption
tests remain required before any CLI is enabled.
