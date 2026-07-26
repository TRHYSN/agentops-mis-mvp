# Relay Linux Systemd Recovery Acceptance

## Scope

This acceptance runs the private one-step recovery executor against a real
systemd manager and the real bound `systemctl` executable on a disposable
GitHub-hosted Ubuntu VM.

It installs one temporary `agentops-mis-relay.service` whose only process is
`/usr/bin/sleep`. It performs no network operation and runs no Relay daemon.
The workflow job is `Relay recovery on real Linux systemd` in
`.github/workflows/ci.yml`.

The same job now includes two real process-death gates. The first is at the
confirmed `daemon_reload` boundary. The second is after the rollback receipt
has been durably published but before the terminal revision is published.
Neither gate simulates an exception: the parent starts an independent child,
waits for a fixed pipe marker, confirms that the child is still alive, sends
`SIGKILL`, and starts a new process to reopen and recover the same durable
fixture journal.

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

1. durable `daemon_reload_requested` intent publication;
2. one real confirmed daemon reload in an independent child;
3. `SIGKILL` after the production mutation adapter has returned, but before
   the executor can obtain or publish the post-mutation observation;
4. an intent-only journal checkpoint after process death;
5. a new process reopening the same journal and receiving exactly
   `resume + record_observation`;
6. strict target-mutation marker count `1` before and after recovery;
7. a next recovery decision of `run_step + enable`, never another
   `daemon_reload`;
8. confirmed forward enable and start;
9. forward verification without a mutation;
10. confirmed rollback stop and disable;
11. exact restored-state rollback verification;
12. rollback receipt and terminal revision; and
13. idempotent `service_state_rolled_back` completion.

Every executor invocation still advances only one confirmed decision. The next
step requires a new stable preview and decision hash.

The mutation marker is an acceptance-only sidecar in the temporary directory.
It counts only the confirmed transaction's target `daemon_reload`; setup and
cleanup reloads are outside that marker. The pipe marker is emitted by the
first post-mutation scanner call, so receiving it proves that the production
mutation adapter returned while observation publication remains unreachable.

The rollback receipt gate begins only after an observed `verify` revision
records `rollback_verified`, with both ownership flags false. The temporary
journal uses the exact production namespace shape and lifecycle-lock opener.
Its checkpoint wrapper delegates to the locked production journal session.
Only after its real `publish_receipt()` method returns does the wrapper emit
`rollback_receipt_published` through the anonymous pipe and block. Therefore
the receipt file publication, file sync, hard-link publication, parent
directory sync, temporary-file unlink, and final directory sync have
completed, while the controller's `_load_after` call and terminal revision
publication remain unreachable.

Before `SIGKILL`, the parent proves that the live checkpoint child still owns
the nonblocking lifecycle lock. After `SIGKILL`, it reacquires and validates
that same lock, proves that exactly one canonical receipt exists, and confirms
that the latest revision is still the observed rollback verification. A new
recovery process then:

1. reacquires the lifecycle lock and reopens the descriptor-bound journal
   namespace;
2. previews exactly
   `terminalize + publish_terminal_revision + terminal + receipt_ready`;
3. recomputes a decision hash for that recovered state;
4. appends exactly one terminal revision without rewriting the receipt or
   invoking a systemd mutation;
5. reopens the journal again and previews exactly
   `complete + none + terminal + journal_complete`; and
6. confirms that completion performs zero writes and leaves systemd inactive
   and disabled.

This gate does not use a timing sleep. Receipt content, paths, and raw systemd
output are never projected into the acceptance result.

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
  "process_death": {
    "checkpoint": "after_daemon_reload_before_observation",
    "child_exit_signal": "SIGKILL",
    "journal_reopened": true,
    "latest_revision": 3,
    "mutation_count": 1,
    "mutation_replayed": false,
    "next_step": "enable",
    "observation_operation": "record_observation",
    "ok": true
  },
  "receipt_process_death": {
    "checkpoint": "after_rollback_receipt_before_terminal",
    "child_exit_signal": "SIGKILL",
    "completion_write_count": 0,
    "decision_recomputed": true,
    "final_state": "service_state_rolled_back",
    "journal_reopened": true,
    "lifecycle_lock_held_at_checkpoint": true,
    "lifecycle_lock_reacquired": true,
    "ok": true,
    "receipt_count": 1,
    "receipt_rewritten": false,
    "receipt_sha256_unchanged": true,
    "systemd_mutation_performed": false,
    "terminal_revision_appended": true,
    "terminal_write_count": 1
  },
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

macOS cannot execute this gate because it has no system-wide systemd manager.
Local macOS verification is limited to compilation and the deterministic
recovery executor/controller smokes. The process-death claim is CI-only until
the existing Ubuntu job reports this exact source revision green.

## Truth Boundary

This is real systemd mutation and observation evidence, but it is not yet the
full production installation acceptance:

- the immutable journal remains in a temporary production-shaped namespace;
  the receipt gate uses the production lifecycle-lock opener, but this is not
  an installed-tree journal;
- non-systemd prerequisite identities use bounded synthetic fixtures;
- the production installed-tree scanner is not run against a provisioned
  service account, Relay binary, configuration, TLS material, or route key;
- process interruption is proven only for the forward `daemon_reload`
  mutation-returned/observation-not-published window and the rollback
  receipt-published/terminal-not-published window, not every intent,
  in-flight mutation, observation, partial publication, receipt, or terminal
  boundary; and
- no CLI, API, browser caller, public Relay, DNS, or physical second-device
  acceptance is enabled by this slice.

The separate production installation, scanner, and journal-opener baseline is
recorded in `RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md`, and
`RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md` combines both baselines with the
packaged Relay process and controller-store reopen boundaries. Actual process
death at the remaining intent, in-flight mutation, observation, partial
publication, and ownership-ambiguous windows remains open. These two gates are
not sufficient to expose the guarded operator CLI.
