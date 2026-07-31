# Relay Activation Production Store Opener Acceptance

## Scope

This acceptance covers the private lifecycle-lock-bound opener for the
production Relay activation journal namespace. It is a controller foundation,
not a CLI mutation path.

`agentops-relayctl --root / activate --confirm-activate` remains fail-closed
with `activation_mutation_unavailable` before the opener, scanner, systemd, or
any journal write is reached.

## Lock And Namespace Contract

The private opener:

- anchors an absolute root with no-follow directory opens;
- validates the trusted `var/lib/agentops-relayctl` parent chain and exact
  owner-only `0700` admin directory;
- opens an existing empty, single-link, owner-only `0600` lifecycle lock;
- acquires `LOCK_EX | LOCK_NB` itself and retains that descriptor for the full
  lexical context lifetime;
- yields a private wrapper whose store cannot remain usable after context
  exit, with destructor cleanup as a last-resort abandoned-context guard;
- opens only an already exact `activation/transactions` and
  `activation/receipts` namespace, each as owner-only `0700` directories;
- performs no topology writes: missing, empty, partial, or unknown namespaces
  return `activation_journal_recovery_required`;
- revalidates root, admin, lock, activation, transactions, and receipts
  bindings before and after every store operation and again before unlock;
- closes the journal store before unlocking and closing the lifecycle lock.

A busy lifecycle lock returns `activation_journal_busy` without writing an
activation namespace. Replaced paths, missing or incomplete scaffolds, and
binding changes return `activation_journal_recovery_required`.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_journal.py \
  scripts/relay_activation_production_store_smoke.py
python3 scripts/relay_activation_production_store_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_offline_status_smoke.py
python3 scripts/relay_activation_preview_smoke.py
git diff --check
```

The opener smoke verifies:

- an exact pre-created namespace is preserved and can be reopened;
- an incomplete prepared chain remains visible for future recovery;
- same-process and externally held lock contention;
- zero namespace writes when the lifecycle lock is busy;
- release of the lock and all descriptors on normal and injected failure
  paths;
- release of the lock when an entered context is abandoned and garbage
  collected, while an unentered context acquires nothing;
- rejection of unsafe admin parents, lock modes, hardlinks, symlinks, FIFO,
  directory locks, nonempty locks, unknown entries, and symlink roots;
- zero-write recovery classification when the lifecycle lock is replaced
  after `flock` but before the journal namespace is opened;
- rejection of activation and lock path replacement while a session is open;
- zero-write rejection of missing, wholly empty, and partially populated
  activation scaffolds;
- context-exit binding validation and inability to use a closed session; and
- continued CLI mutation lockout.

Expected summary:

```json
{
  "abandoned_context_released_lock": true,
  "acquisition_lock_race_rejected": true,
  "activation_path_race_rejected": true,
  "busy_opener_zero_write": true,
  "cli_mutation_exposed": false,
  "descriptor_lifecycle_stable": true,
  "exact_namespace_preserved": true,
  "failure_cleanup_released_lock": true,
  "invalid_topology_cases": 11,
  "lifecycle_lock_held": true,
  "lock_path_race_rejected": true,
  "missing_namespace_zero_write": true,
  "partial_namespace_rejected": true,
  "ok": true
}
```

## Remaining Gates

This opener has no caller in `main()`. The private exact-confirmed success
controller now uses it to hold the lifecycle lock across the forward
transaction, but the opener itself performs no systemd action.
Confirmed first-install now creates the exact namespace while holding the same
lifecycle lock, as recorded in
`RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md`. Partial namespace resume,
ownership-safe rollback, crash recovery, completed-history retention, the CLI
boundary, real root-owned Linux systemd acceptance, public Relay, and physical
ordinary-browser acceptance remain open. The private forward controller and
step evidence compiler are recorded in
`RELAY_ACTIVATION_CONTROLLER_SUCCESS_ACCEPTANCE.md` and
`RELAY_ACTIVATION_EVIDENCE_ACCEPTANCE.md`; the guarded read-only recovery
snapshot is recorded in `RELAY_ACTIVATION_RECOVERY_SNAPSHOT_ACCEPTANCE.md`;
the side-effect-free recovery decision contract is recorded in
`RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md`;
the private process boundary is recorded in
`RELAY_SYSTEMD_MUTATION_ADAPTER_ACCEPTANCE.md`, and installer lock hardening is
recorded in `RELAY_OFFLINE_INSTALL_ACCEPTANCE.md`.
