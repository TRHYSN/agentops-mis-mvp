# Relay Linux Systemd Recovery Acceptance

## Scope

This acceptance runs the private one-step recovery executor against a real
systemd manager and the real bound `systemctl` executable on a disposable
GitHub-hosted Ubuntu VM.

It installs one temporary `agentops-mis-relay.service` whose only process is
`/usr/bin/sleep`. It performs no network operation and runs no Relay daemon.
The workflow job is `Relay recovery on real Linux systemd` in
`.github/workflows/ci.yml`.

## Safety Guard

The script refuses to run unless:

- `AGENTOPS_RELAY_LINUX_SYSTEMD_ACCEPTANCE=1` is explicit;
- the platform is Linux with `/run/systemd/system`;
- effective UID is root; and
- both the target unit and enablement link were absent before the test.

The unit is created with `O_EXCL` and contains a fixed no-network payload. The
cleanup path verifies that the unit bytes remain test-owned before it stops,
disables, unlinks, reloads systemd, and clears failed state. Cleanup runs after
both success and failure. A pre-existing or replaced unit is never deleted.

## Real Execution Contract

The smoke binds the real root-owned `systemctl` file identity, reads live state
through `read_systemd_show`, and invokes the production
`_run_bound_systemd_mutation` adapter from the confirmed recovery executor.
The parser accepts either an empty invocation ID or the strict retained
32-hex invocation ID that systemd may report after a previously active unit
returns to `inactive`. It also accepts `ExecMainStatus=15`, which the fixed
unit's successful default `SIGTERM` stop may retain, but only while the unit is
inactive, its `Result` is `success`, and its `MainPID` is zero.
Active units still require `ExecMainStatus=0`; failed results remain invalid.

It exercises:

1. real daemon reload;
2. confirmed forward enable;
3. confirmed forward start;
4. forward verification without a mutation;
5. confirmed rollback stop;
6. confirmed rollback disable;
7. exact restored-state rollback verification;
8. rollback receipt and terminal revision; and
9. idempotent `service_state_rolled_back` completion.

Every executor invocation still advances only one confirmed decision. The next
step requires a new stable preview and decision hash.

## Verification

The Linux-only command is:

```bash
sudo env \
  AGENTOPS_RELAY_LINUX_SYSTEMD_ACCEPTANCE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/relay_linux_systemd_recovery_acceptance.py
```

Do not run it on a host where `agentops-mis-relay.service` already exists.

Expected bounded result:

```json
{
  "cleanup_ok": true,
  "final_state": "service_state_rolled_back",
  "forward_steps": [
    "daemon_reload",
    "enable",
    "start",
    "verify"
  ],
  "journal_scope": "temporary_fixture",
  "linux_systemd": true,
  "network_used": false,
  "ok": true,
  "operation": "relay_linux_systemd_recovery_acceptance",
  "rollback_steps": [
    "rollback_stop",
    "rollback_disable",
    "verify"
  ],
  "stage": "complete",
  "systemctl_bound": true
}
```

`initial_reload_required` records whether the VM reported a stale unit after
the intentional unit-file change. The setup still performs one real bound
daemon reload even if that flag is false.

## Truth Boundary

This is real systemd mutation and observation evidence, but it is not yet the
full production installation acceptance:

- the immutable journal uses a temporary fixture store;
- non-systemd prerequisite identities use bounded synthetic fixtures;
- the production installed-tree scanner is not run against a provisioned
  service account, Relay binary, configuration, TLS material, or route key;
- process interruption is not injected between every intent, mutation,
  observation, receipt, and terminal boundary; and
- no CLI, API, browser caller, public Relay, DNS, or physical second-device
  acceptance is enabled by this slice.

The separate production installation, scanner, and journal-opener baseline is
recorded in `RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md`, and
`RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md` combines both baselines with the
packaged Relay process and controller-store reopen boundaries. Actual process
death inside intent/mutation/observation and receipt/terminal windows remains
the next gate. Only then may the guarded operator CLI be exposed.
