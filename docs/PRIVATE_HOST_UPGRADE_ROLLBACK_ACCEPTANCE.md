# Private Host Upgrade and Rollback Acceptance

Status: passed with two isolated local bundles

## Contract

- The Host must be stopped before a bundle update.
- Each installed version retains its release manifest and exact Git commit.
- A new version is staged before the `current` symlink changes.
- The prior version remains addressable through `previous`.
- Binary rollback requires explicit confirmation and a verified ledger backup.
- Host data remains outside the installation root and is not switched with the
  binaries.

## Product Commands

```text
agentops host version
agentops host update --check
agentops host rollback [--confirm-rollback]
```

The update check is local and read-only. Installing a newer verified bundle is
still an explicit operator action; no automatic network download is performed.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py scripts/private_host_bundle_smoke.py
python3 scripts/private_host_bundle_smoke.py
git diff --check
```

The smoke builds two real bundles from the current commit, installs version
`0.0.0-smoke`, initializes an isolated Host, writes a durable ledger marker,
and then verifies:

- an update is rejected while the managed Host PID is alive;
- a verified pre-update ledger backup is created before binary switching;
- version `0.0.1-smoke` installs with exact release provenance;
- `current` points to the new version and `previous` to the old version;
- update status is read-only and performs no network request;
- rollback without confirmation has no side effect;
- confirmed rollback creates a verified SQLite backup;
- `current` and `previous` swap back atomically;
- the Host ledger marker and data sentinel survive upgrade and rollback;
- uninstall removes program versions but preserves Host data.

## Known Boundary

This acceptance proves binary switching where both bundles use the same current
schema. A future incompatible schema migration requires an explicit migration
and downgrade contract before cross-schema rollback can be claimed.
