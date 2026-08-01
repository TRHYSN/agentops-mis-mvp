# Relay Activation Journal Read-Only Status Acceptance

## Scope

This acceptance connects the immutable Relay activation journal to the
existing read-only installed-tree status. It does not open a production writer,
acquire the lifecycle lock, run systemd, expose journal bodies, or unlock
confirmed activation.

`agentops-relayctl --root / status` keeps its existing public JSON shapes. The
journal snapshot hash and identity sets are private comparison values and never
appear in stdout.

## Fail-Closed Contract

An installed tree remains `installed_valid` when:

- the activation namespace is absent, preserving compatibility with releases
  installed before the journal exists;
- the exact owner-only `activation/transactions` and `activation/receipts`
  namespace exists and is empty; or
- every transaction is a complete, immutable active or rolled-back chain with
  one exactly bound receipt for the installed release, version, and unit.

Status returns `recovery_required` when the journal is incomplete, malformed,
unknown, over its bounds, linked through a symlink, belongs to another release,
contains an orphan receipt or temporary publication, or changes while the
installed tree is being validated. It takes a private journal snapshot before
and after installed-tree validation and requires exact equality. The final
activation path is also rebound to the descriptor used for inspection.

The status path performs no writes, network access, subprocess execution,
systemd calls, configuration reads, or credential reads.

A completed `active` journal is immutable historical evidence, not a live
service-health claim. This command still validates only the installed tree and
journal consistency.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_journal.py \
  agentops_mis_cli/relay_admin.py \
  scripts/relay_offline_status_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_offline_status_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

The offline status smoke covers source and installed-wheel entrypoints when the
local Python can install the generated wheel. Its journal matrix includes:

- absent, empty, complete active, and complete rollback histories;
- prepared chains, orphan receipts, temporary files, tampered revisions,
  unknown names, unsafe modes, missing directories, and symlinked directories;
- a complete journal bound to the wrong release;
- mutation between the two namespace snapshots; and
- replacement of the activation path after the final journal inspection;
- repeated in-process status calls without descriptor growth or public-output
  shape drift.

Expected source-run summary:

```json
{
  "cases": 42,
  "journal_fd_leak_free": true,
  "journal_path_swap_rejected": true,
  "journal_read_only_cases": 14,
  "journal_snapshot_race_rejected": true,
  "ok": true,
  "schema_id": "agentops.relay.offline-status.v0"
}
```

## Remaining Gates

This is read-only recovery detection, not recovery automation. Partial
namespace resume, confirmed-controller use of the private systemd mutation
adapter, interruption recovery, retention across multiple installed release
histories, real Linux systemd tests, public Relay, and physical
ordinary-browser acceptance remain open. Confirmed first-install
initialization, the private strict open-only production opener, and the private
mutation process boundary are recorded in
`RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md` and
`RELAY_ACTIVATION_PRODUCTION_STORE_ACCEPTANCE.md` and
`RELAY_SYSTEMD_MUTATION_ADAPTER_ACCEPTANCE.md`.
