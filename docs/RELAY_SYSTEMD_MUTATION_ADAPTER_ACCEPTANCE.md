# Relay Systemd Mutation Adapter Acceptance

## Scope

This acceptance covers the private, scanner-bound process boundary for the five
systemd mutations reserved by the Relay activation specification:

```text
daemon_reload
enable
start
stop
disable
```

It does not expose those mutations through `agentops-relayctl`, create an
activation transaction, decide ownership, or claim that a real Linux service
was changed.

## Boundary

The adapter:

- accepts only the exact scanner-bound root-owned `systemctl` identity;
- executes that already-open executable through `/proc/self/fd/<fd>`;
- permits only fixed `--system` commands and the exact Relay unit name;
- uses `shell=False`, closed stdin, discarded stdout/stderr, `/` as cwd, a
  fixed minimal environment, closed unrelated FDs, and a new process session;
- applies a bounded timeout and terminates the whole child process group on
  failure;
- revalidates executable path, descriptor, metadata, and content before and
  after the child; and
- maps every failure to `systemd_mutation_failed` without returning paths,
  subprocess output, credentials, or exception text.

The production helper remains private. The private exact-confirmed success
controller now calls it only after a durable journal intent, but
`relay_admin.py` has no controller caller. `--confirm-activate` therefore still returns
`activation_mutation_unavailable`.

## Verification

Run:

```bash
python3 -m py_compile agentops_mis_cli/relay_systemd_mutation.py \
  scripts/relay_systemd_mutation_smoke.py
python3 scripts/relay_systemd_mutation_smoke.py
python3 scripts/relay_activation_preview_smoke.py
git diff --check
```

The smoke uses injected process and binding fixtures. It verifies all five
exact argv forms and subprocess controls, invalid-operation rejection before
binding, post-child identity revalidation, nonzero-exit redaction, bounded
timeout/process-group termination, non-Linux fail-closed behavior, redaction,
binding-factory substitution rejection, and absence of a CLI caller. It
performs no network or systemd mutation.

Expected summary:

```json
{
  "cli_mutation_exposed": false,
  "exact_mutation_commands": 5,
  "invalid_operations_rejected": true,
  "network_used": false,
  "nonzero_exit_redacted": true,
  "non_linux_subprocess_blocked": true,
  "ok": true,
  "post_mutation_binding_revalidated": true,
  "private_canary_omitted": true,
  "substituted_binding_rejected": true,
  "timeout_process_group_terminated": true
}
```

## Remaining Gates

The private success controller now owns plan refresh, lifecycle lock, forward
journal revisions, per-step observations, ownership decisions and terminal
receipt. The private recovery executor now composes this adapter for one
confirmed forward or inverse recovery step, while exact non-systemd rollback
terminalization is also fixture-accepted. Real daemon reload, enable, start,
stop, disable, boot persistence, and interruption tests still require a
disposable Linux systemd host with root authority. The CLI, public Relay, and
physical ordinary-browser acceptance remain open.
