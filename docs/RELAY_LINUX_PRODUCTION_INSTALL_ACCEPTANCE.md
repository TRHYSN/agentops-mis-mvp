# Relay Linux Production Install Acceptance

## Scope

This acceptance closes the gap between relocated filesystem fixtures and the
real Linux root used by Relay activation. On a disposable GitHub-hosted Ubuntu
VM it:

1. creates the dedicated `agentops-relay` system account;
2. builds and installs the exact current-commit offline Relay bundle with the
   production preview/confirmation installer;
3. provisions bounded synthetic configuration, TLS, route-key, state, and
   runtime fixtures with production ownership and modes;
4. runs the public production prerequisite scanner against `/`;
5. opens the production activation journal while holding its exact lifecycle
   lock;
6. runs the locked production scanner with the capability issued by that
   store; and
7. verifies that the scanner performs no journal mutation before removing the
   complete disposable installation and account.

The workflow job is `Relay production install scan on real Linux`.
The exact current-commit bundle is built as the unprivileged CI user. Only its
absolute path and SHA-256 are passed into the root acceptance process, which
re-verifies the bundle before planning or writing the installation.

## Safety Guard

The script refuses to run unless:

- `AGENTOPS_RELAY_LINUX_PRODUCTION_ACCEPTANCE=1` is explicit;
- the platform is Linux with a live systemd manager;
- effective UID is root;
- the service user and group do not exist; and
- every fixed Relay install, admin, runtime, config, unit, launcher, and
  enablement path is absent.

GitHub's hosted Ubuntu image intentionally makes `/opt` and
`/usr/local/bin` writable for tool installation. The production installer
correctly rejects those parent modes. This disposable acceptance therefore
requires the separate explicit
`AGENTOPS_RELAY_LINUX_PRODUCTION_HARDEN_INSTALL_PARENTS=1` opt-in, clears only
group/other write bits on those two root-owned directory identities, and
restores their exact original modes during every cleanup path. The installer
itself remains fail-closed and unchanged.

The acceptance does not start the Relay daemon, invoke `systemctl`, open a
listener, access the network, or ingest a customer credential. Its TLS and
route material are synthetic test fixtures on an ephemeral VM. Output is
limited to fixed stage identifiers and booleans.

Cleanup removes only the fixed paths that had to be absent at preflight. The
launcher target and packaged unit bytes must still match before they are
unlinked. A replaced or unexpected path makes cleanup fail closed.

## Verification

The Linux-only command is:

```bash
sudo env \
  AGENTOPS_RELAY_LINUX_PRODUCTION_ACCEPTANCE=1 \
  AGENTOPS_RELAY_LINUX_PRODUCTION_HARDEN_INSTALL_PARENTS=1 \
  AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE=/absolute/path/to/bundle.tar.gz \
  AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE_SHA256=<sha256> \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/relay_linux_production_install_acceptance.py
```

Expected bounded result:

```json
{
  "account_provisioned": true,
  "cleanup_ok": true,
  "installed_tree": true,
  "install_parent_modes_restored": true,
  "journal_unchanged": true,
  "network_used": false,
  "ok": true,
  "operation": "relay_linux_production_install_acceptance",
  "production_scanner": true,
  "production_store": true,
  "stage": "complete",
  "systemd_mutated": false
}
```

The ordinary scanner and lifecycle-lock-bound scanner must return the same
full prerequisite snapshot. The store must remain `ready` with zero completed
transactions and an unchanged snapshot hash.

## Truth Boundary

This is real-root installation, account/ownership, production scanner, and
production journal-opener evidence. It does not claim:

- service activation or a running Relay process;
- interruption recovery between intent, mutation, observation, receipt, and
  terminal publication;
- firewall, DNS, ACME, public TLS, credential provisioning, or route-key
  rotation;
- a public Relay endpoint or physical ordinary-browser acceptance; or
- that the guarded operator activation CLI is ready for exposure.

The existing real-systemd recovery acceptance proves the mutation adapter
separately. `RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md` now combines the
production tree, journal, packaged Relay process, and real systemd with
controller-store reopen boundaries. The next gate injects actual process death
inside intent/mutation/observation and receipt/terminal windows. Only that
interruption gate can unlock the guarded operator CLI.
