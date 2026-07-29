# Relay Activation Namespace Install Acceptance

## Scope

This acceptance covers initialization of the production activation journal
namespace by the confirmed offline first-install transaction. It does not
enable service activation or expose a journal writer through the CLI.

The strict production opener remains open-only. The installer prepares the
namespace while it already owns the same validated lifecycle lock and admin
directory FD.

## Plan And Transaction Contract

The install plan hash binds:

- namespace schema `agentops.relay.activation-namespace.v0`;
- desired entries `activation/receipts` and `activation/transactions`;
- exact directory mode `0700`; and
- observed preinstall state `missing` or `exact_empty`.

The existing canonical `transaction.json` is published and fsynced before the
first namespace directory is created. It binds the install plan hash, archive
and release identity, namespace schema, and observed namespace state.

An interrupted partial initialization retains that transaction marker and
returns `recovery_required`. A later preview detects the marker and does not
silently complete or delete the partial namespace.

## Namespace State Machine

Preinstall behavior is:

- missing admin state or exact admin state without `activation`: plan as
  `missing`;
- exact empty `activation/{receipts,transactions}`: plan as `exact_empty` and
  preserve directory identities;
- partial topology, unknown admin entries, symlinks, wrong metadata, or
  nonempty journal history before first install: `recovery_required` with no
  repair.

During confirmed install:

1. validate the exact plan again under the lifecycle lock;
2. publish the canonical install transaction marker;
3. create directories only with `dir_fd`-relative `mkdir`/open operations;
4. enforce owner/group, mode, type, link count, and path/FD identity;
5. fsync each child, `activation`, and the held admin directory;
6. retain immutable directory identities through install commit; and
7. remove the transaction marker only after namespace, install tree, root, and
   lock bindings are revalidated.

The completed empty namespace is durable infrastructure. If a later install
publication step rolls back, the exact empty namespace remains and a new plan
records `exact_empty`; install artifacts and the transaction marker are still
rolled back.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_admin.py \
  scripts/relay_activation_namespace_install_smoke.py
python3 scripts/relay_activation_namespace_install_smoke.py
python3 scripts/relay_offline_install_smoke.py
python3 scripts/relay_offline_status_smoke.py
python3 scripts/relay_activation_production_store_smoke.py
git diff --check
```

The namespace smoke verifies:

- a fresh confirmed install creates the exact empty namespace;
- the production open-only journal context can immediately inspect it;
- an existing exact empty namespace is idempotent and keeps its inode
  identities;
- missing versus exact-empty state changes the exact install plan hash;
- partial, unknown, preinstall history, symlink, and FIFO states fail closed
  without writes or external canary changes;
- an injected child-creation failure retains the canonical transaction marker,
  partial evidence, descriptor hygiene, and restart recovery state;
- replacement of `activation` after its directory FD is opened is rejected by
  the final identity binding and retained for recovery; and
- repository status remains unchanged.

Expected summary:

```json
{
  "activation_path_race_rejected": true,
  "exact_namespace_idempotent": true,
  "failure_marker_retained": true,
  "fresh_namespace_initialized": true,
  "ok": true,
  "plan_state_bound": true,
  "production_opener_ready": true,
  "rejected_preinstall_states": 5,
  "repository_status_unchanged": true
}
```

## Remaining Gates

This is first-install namespace initialization, not activation recovery. Safe
resume or rollback of a marker-owned partial namespace, full root-to-admin
`openat` parent-chain anchoring, transaction operations entirely through the
held admin FD, confirmed-controller use of the private systemd mutation
adapter, real Linux interruption tests, public Relay, and physical
ordinary-browser acceptance remain open.
