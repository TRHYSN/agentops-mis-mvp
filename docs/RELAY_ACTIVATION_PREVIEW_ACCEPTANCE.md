# Relay Read-Only Activation Preview Acceptance

## Scope

This acceptance covers the production read-only systemd adapter in
`agentops_mis_cli.relay_systemd_read`, the preview controller in
`agentops_mis_cli.relay_activation_preview`, and the read-only
`agentops-relayctl --root / activate` command.

```bash
python3 scripts/relay_activation_preview_smoke.py
```

The command observes and projects an activation plan. It cannot reload,
enable, start, stop, or disable a service. The currently parsed
`--confirm-activate` and `--plan-sha256` arguments always fail with the single
bounded `activation_mutation_unavailable` identifier before scanning the host
or starting a subprocess. The raw CLI root gate accepts only a missing
`--root`, one exact `--root /`, or one exact `--root=/`; normalized spellings
such as `/.`, alternate roots, and duplicate root options fail before
the activation scanner or subprocess, while other commands retain their
existing fixture-root semantics.

## Implemented Contract

- accept only the scanner-bound `/usr/bin/systemctl` or `/bin/systemctl`
  identity; no production path, root, resolver, runner, or process override is
  public;
- no-follow open the executable, require its complete metadata identity and
  full content hash to match the scanner snapshot, and revalidate both held FD
  and path before execution;
- execute the opened FD through `/proc/self/fd/<fd>` with no pathname fallback;
- execute exactly:

  ```text
  systemctl --system show agentops-mis-relay.service --no-pager --property=LoadState,UnitFileState,ActiveState,SubState,Result,ExecMainStatus,FragmentPath,NeedDaemonReload,InvocationID,MainPID
  ```

- use `shell=False`, closed stdin, discarded stderr, fixed `/` cwd, a minimal
  `LANG`/`LC_ALL`/`PATH` environment, closed unrelated FDs, and a bounded
  timeout;
- read stdout incrementally with the existing `MAX_SYSTEMD_SHOW_BYTES` cap;
  never use an unbounded `communicate`;
- on any failed read, terminate the new process group, reap the leader before
  probing the group, escalate to group `SIGKILL` when needed, verify the group
  is gone, and close stdout;
- strictly parse the bounded bytes, then revalidate the held FD, path identity,
  and full content hash again before returning the private systemd snapshot;
- run a fresh prerequisite scan, one read-only systemd show, and a second fresh
  prerequisite scan; require the two frozen dataclass snapshots to be exactly
  equal before compiling and projecting the plan;
- return only the existing bounded activation projection. `already_active`
  intentionally has no plan hash under the existing contract.

## Verification

The cross-platform smoke uses only private fake bindings and process runners
for successful preview behavior on macOS and non-systemd CI hosts. It verifies:

- exact command, environment, cwd, descriptor, stdin/stdout/stderr, shell, and
  session settings;
- scanner -> show -> scanner ordering and exact rescan equality;
- bounded happy-path and `already_active` projections;
- timeout, nonzero exit, stdout overflow, parser failure, and one redacted
  outward error identifier;
- pre-child identity mismatch and post-child identity/content race rejection;
- a running child on overflow, an unresponsive child requiring TERM-to-KILL
  escalation, and an exited leader whose helper still holds stdout;
- leader reaping before process-group probing and final no-group verification;
- descriptor closure and no raw output, exception, path, environment, numeric
  identity, hostname, route, or credential material in public output;
- zero product writes, zero network calls, and zero systemd mutation calls;
- non-canonical roots plus confirmation/plan arguments fail before preview or
  subprocess execution.

On Linux CI, the same smoke additionally builds a `FileIdentity` from the
actual root-owned allowlisted systemctl file, verifies no-follow open plus
full-hash revalidation, rejects a wrong content hash, and checks descriptor
closure. macOS skips only this real executable-open subcase.

The smoke is compiled and executed in the Python 3.10 and 3.11 compatibility
matrix and in the deterministic backend job.

## Truth Boundary

This slice proves a read-only activation preview contract. It does not:

- perform confirmed activation or accept a confirmed plan;
- run daemon reload, enable, start, stop, or disable;
- write a transaction marker, receipt, rollback record, or recovery state;
- prove the command against a real Linux systemd host;
- provision an account, config, TLS material, route key, DNS, ACME, firewall,
  or public Relay endpoint;
- prove public Relay reachability or a physical second-browser workflow.

Confirmed activation transaction integrity, rollback/recovery, real Linux
systemd acceptance, public Relay deployment, and physical ordinary-browser
acceptance remain open gates.
