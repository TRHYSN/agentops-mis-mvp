# Relay Linux Production Systemd Acceptance

## Scope

This acceptance combines the previously separate real-root installation and
real-systemd evidence on one disposable GitHub-hosted Ubuntu VM. It:

1. installs the exact current-commit offline Relay bundle into the production
   Linux paths;
2. creates the dedicated service account and production activation namespace;
3. provisions an ephemeral self-signed certificate, synthetic route key, and
   loopback-only Relay configuration with production ownership and modes;
4. opens the real production journal under its lifecycle lock and publishes
   the hash-bound prepared revision;
5. closes and reopens the production store between every recovery preview and
   confirmed controller invocation;
6. runs the packaged `agentops-relay` process through the packaged systemd
   unit and verifies its bounded status command reports ready when invoked
   under the dedicated `agentops-relay` service identity;
7. performs the real daemon-reload, enable, start, verify, rollback-stop,
   rollback-disable, and rollback-verify sequence;
8. if systemd reaches a valid stopped state just after the one-step executor
   returns recovery-required, reopens the journal and records the already
   completed stop observation without replaying the mutation;
9. publishes the rollback receipt and terminal revision into the production
   journal; and
10. verifies one completed transaction, restored inactive/disabled systemd
   state, stopped Relay status, and complete cleanup.

The workflow job is
`Relay production install and systemd recovery on real Linux`.

## Safety Guard

The script refuses to run unless:

- `AGENTOPS_RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE=1` is explicit;
- the platform is Linux with a live systemd manager;
- effective UID is root;
- the service account and every fixed Relay production path are absent;
- the fixed loopback acceptance ports are available; and
- a root-owned, non-writable `/usr/bin/openssl` or `/bin/openssl` executable is
  available for ephemeral TLS generation.

The exact release bundle is built without root and passed by absolute path plus
SHA-256. The root process re-verifies it before the production installer writes
anything. GitHub-hosted Ubuntu's writable `/opt` and `/usr/local/bin` modes are
handled only under the separate explicit parent-hardening opt-in, with exact
mode restoration on cleanup. The installer remains unchanged and fail-closed.

The Relay listens only on `127.0.0.1` using fixed high test ports. It has no
Host connector, customer credential, public route, DNS, ACME, or external
network dependency. Certificate and route material exist only on the
disposable VM and are never emitted. Output is limited to fixed identifiers,
step names, counts, and booleans.

The low-level daemon `status` command runs through the root-owned,
non-writable `/usr/sbin/runuser` executable as `agentops-relay`. This preserves
the runtime status file's service-owned `0600` boundary; root retains systemd
orchestration and uses the separate administrator status surface instead of
weakening daemon-file ownership checks.

The packaged unit sets `RuntimeDirectoryPreserve=yes`. The service-owned
`0700` runtime directory and `0600` stopped-status leaf therefore remain
available while the controller reopens its journal and completes rollback
observation. Cleanup still removes the disposable runtime tree. Preview does
not retry generic scanner failures: path, owner, mode, configuration, journal,
plan, systemd, and unknown failures remain fail-closed.

Cleanup first stops and disables the service, then removes only identities
owned by the exact installation acceptance, restores parent modes, removes the
service account, reloads systemd, and verifies all fixed paths are absent.

## Controller Reopen Contract

The initial prepared revision is committed in one production-store scope. Each
later preview closes that scope before a newly opened store re-observes the
same plan and executes exactly one confirmed decision. This proves that the
durable journal, not a retained store object, carries progress across
independent controller invocations. All invocations still run in one test
process; actual process death remains outside this slice.

The test intentionally switches from forward recovery to rollback before
publishing the success receipt. The real Relay must have reached ready state,
but authority is not terminalized as active. Rollback then owns the stop,
disable, restored-state verification, receipt, and terminal publication.

## Verification

The Linux-only command is:

```bash
sudo env \
  AGENTOPS_RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE=1 \
  AGENTOPS_RELAY_LINUX_PRODUCTION_HARDEN_INSTALL_PARENTS=1 \
  AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE=/absolute/path/to/bundle.tar.gz \
  AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE_SHA256=<sha256> \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/relay_linux_production_systemd_acceptance.py
```

Expected bounded result:

```json
{
  "account_provisioned": true,
  "cleanup_ok": true,
  "external_network_used": false,
  "final_state": "service_state_rolled_back",
  "forward_steps": [
    "daemon_reload",
    "enable",
    "start",
    "verify"
  ],
  "installed_tree": true,
  "loopback_only": true,
  "ok": true,
  "operation": "relay_linux_production_systemd_acceptance",
  "production_journal": true,
  "production_store_reopen_boundaries": 22,
  "real_relay_process_started": true,
  "real_systemd": true,
  "recovered_late_observation_steps": [],
  "rollback_steps": [
    "rollback_stop",
    "rollback_disable",
    "verify"
  ],
  "stage": "complete"
}
```

`production_store_reopen_boundaries` is a bounded count of production-store
closes and reopens. `initial_reload_required` records that the acceptance
deliberately changes only the installed unit mtime after a setup reload so the
journaled daemon-reload step is exercised without changing packaged unit
bytes. A run may report `rollback_stop` in
`recovered_late_observation_steps` and a correspondingly larger reopen count
when systemd's stopped state becomes observable just after the mutation call.
That path requires the next reopened preview to return exactly
`inverse + record_observation + rollback_stop` for the retained intent. Any
other decision fails before a mutation call, so the stop action cannot be
replayed by the acceptance.

## Truth Boundary

This proves the production installed tree, production journal, packaged Relay
daemon, bound systemd adapter, and normal controller-reopen recovery together.
It does not yet prove process-death handling inside a one-step critical window.
In particular, it does not kill the controller:

- after intent publication but before the systemd mutation;
- after mutation but before observation publication;
- after receipt publication but before terminal publication; or
- during partial activation-namespace creation.

It also does not expose an operator activation CLI/API/browser action and does
not prove public Relay infrastructure or a physical ordinary-browser client.
Those claims remain blocked until dedicated interruption injection passes.
