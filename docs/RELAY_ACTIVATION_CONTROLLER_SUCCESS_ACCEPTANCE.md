# Relay Activation Controller Success Acceptance

## Scope

This acceptance covers the private exact-confirmed success path in
`agentops_mis_cli.relay_activation_controller`.

The production entrypoint opens the exact activation journal while holding the
existing installer lifecycle lock, refreshes the private plan, writes the
prepared revision before any mutation, and composes the scanner, systemd reader,
canonical evidence compiler, journal, and scanner-bound mutation adapter.
All production rescans use a same-root capability from that live lock, so the
durable prepared/intent state does not self-block installed-tree validation;
ordinary status and activation preview still report `recovery_required`.

The controller remains absent from `agentops-relayctl`. This slice does not
unlock `--confirm-activate`, recover an interrupted transaction, automatically
roll back a failed activation, or claim that a real Linux service was changed.

## Success Contract

The controller accepts only one exact 64-hex confirmed plan hash. Under the
held lifecycle lock it requires an exact ready journal store, then performs:

```text
stable prerequisite scan + systemd read + stable rescan
-> exact plan-hash match
-> durable prepared revision
-> daemon_reload intent
-> fixed mutation
-> stable rescan and canonical observation
-> daemon_reload observed revision
-> optional enable intent/mutation/observation
-> optional start intent/mutation/observation
-> verify intent/stable observation
-> immutable active receipt
-> terminal active revision
```

Every step rechecks the complete last observed prerequisite and systemd
snapshot before publishing its intent. Every post-step observation comes from
a new scan/read/scan sequence and the canonical activation evidence compiler.
The locked scan binds the journal tree hash before and after each prerequisite
observation and fails if another revision or receipt appears mid-scan.
Enable ownership is acquired only by the durable enable observation; start
ownership is acquired only by the durable start observation.

The receipt is persisted before its terminal revision so an interruption
cannot expose an unbound terminal journal. A successful response contains only
the plan hash, receipt hash, revision count, bounded state, and controller
schema.

## Failure Contract

An invalid confirmation or stale plan performs no journal or systemd mutation.
After the prepared publication begins, every command failure, state drift,
evidence mismatch, journal failure, or terminal interruption maps to
`activation_recovery_required`.

The controller does not guess whether a timed-out command took effect and does
not erase an incomplete chain or orphan receipt. A second activation attempt
against that store also fails closed until a future recovery command proves and
resumes the exact transaction.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_controller.py \
  scripts/relay_activation_controller_smoke.py
python3 scripts/relay_activation_controller_smoke.py
python3 scripts/relay_activation_evidence_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_activation_production_store_smoke.py
python3 scripts/relay_systemd_mutation_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

The smoke uses an isolated descriptor-anchored journal and injected in-process
scanner, systemd reader, and mutation adapter. It verifies:

- disabled/inactive activation uses three exact mutations and ten revisions;
- enabled/inactive activation skips enable and uses eight revisions;
- completed plan replay is stale and zero-write;
- prerequisite drift before prepared is zero-write;
- a mutation failure retains the exact intent and requires recovery;
- prerequisite drift after prepared retains a one-revision transaction;
- receipt-before-terminal interruption retains recoverable evidence;
- private fixture values and exception text are omitted; and
- the production CLI remains locked with `activation_mutation_unavailable`.

Expected summary:

```json
{
  "cli_mutation_exposed": false,
  "disabled_initial_revision_count": 10,
  "enabled_initial_revision_count": 8,
  "failure_requires_recovery": true,
  "locked_rescan_capability": true,
  "network_used": false,
  "ok": true,
  "private_payload_omitted": true,
  "receipt_before_terminal": true,
  "stale_plan_zero_write": true,
  "systemd_mutation_performed": false
}
```

## Remaining Gates

Before CLI activation can be enabled, the project still needs real-Linux
validation of transaction resume and rollback at every interruption boundary,
plus bounded operator recovery output.
The lifecycle-lock-guarded recovery snapshot now provides the validated chain
and optional legal terminal receipt input. The pure recovery decision compiler
selects a bounded hash-bound action; the private recovery controllers confirm
and write non-systemd actions, and the private executor advances one
scanner-bound `run_step`. None is exposed through the CLI.

Real daemon reload, enable, start, stop, disable, boot persistence, and
interruption testing require a disposable Linux systemd host with root
authority. Public Relay and physical ordinary-browser acceptance remain
separate gates.
