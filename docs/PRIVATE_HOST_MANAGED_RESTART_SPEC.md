# Private Host Managed Restart Spec

Date: 2026-07-18

## Purpose

An Owner-confirmed Relay enable or disable transition must become active without
requiring a terminal command when the Host is running under the exact managed
LaunchAgent. The HTTP response must finish before the current backend stops,
and a failed new Host startup must restore both Relay and Host configuration
byte-for-byte before the previous Host is relaunched.

This slice does not deploy a Relay, publish DNS, provision certificates, change
Tailscale, start or stop Workers, or make `remote_ready` true.

## Authority Boundary

Only the existing Human Session Owner route may prepare and confirm a Relay
transition. The HTTP server may request a restart only after the transition has
been confirmed, its exact private material has been revalidated, the ledger
audit has committed, and the full response has been flushed.

The Host parent process is the only process allowed to stop and relaunch its
owned `run_local_stack.py` child. The backend never invokes a shell, never sends
a generic PID signal, and never calls `launchctl kickstart` itself.

Automatic restart is available only when all of these are true:

- the default LaunchAgent file is a same-UID regular non-symlink file in mode
  `0600`;
- its bytes match the exact generated Host-only template;
- the exact `dev.agentops.mis.private-host` service is loaded;
- the current private service-instance record binds the supervisor PID, stack
  child PID, service label, and template hash;
- the service command contains `--foreground --no-workers` and no Worker or
  live-run authority.

A manually started foreground Host does not gain automatic signal authority. It
returns `manual_restart_required` and remains available until the operator
restarts it explicitly.

## Durable State Machine

```text
prepared
  -> confirmed
  -> config_applied
  -> response_flushed
  -> restart_requested
  -> validating_new_host
       -> healthy
       -> restoring_config
            -> rolled_back
            -> rollback_failed
```

Manual foreground mode ends at `manual_restart_required` after the response is
flushed. Every transition is monotonic and idempotent. A stale generation,
replayed request, invalid state order, mismatched service instance, or changed
config material fails closed without signalling a process.

The private restart receipt is owner-only, bounded, atomically written and
fsynced. It may contain original and target configuration bytes needed for
recovery. Those bytes, private paths, material digests, certificates, tunnel
secrets, cookies, CSRF values, and credentials never enter HTTP, audit, logs, or
browser storage.

## Response Contract

The confirmation request returns an acceptance result, not a false claim that
the new Host is already healthy:

```json
{
  "ok": true,
  "state": "restart_scheduled",
  "config_applied": true,
  "restart_mode": "managed_launchagent",
  "restart_required": true,
  "restart_pending": true,
  "rollback_armed": true,
  "remote_ready": false,
  "status_url": "/api/host/relay",
  "tailscale_changed": false,
  "workers_affected": false,
  "sensitive_values_omitted": true
}
```

The server must commit the bounded audit, send `202 Accepted`, force
`Connection: close`, write the complete body, flush the response, mark the
receipt `response_flushed`, and only then submit the private restart request. If
the body write or flush fails, it must not request a restart and must restore
the original configuration immediately.

## New Host Health Gate

The supervised replacement is healthy only when:

- the stack readiness descriptor signals within a bounded timeout;
- loopback `/health` succeeds;
- a fresh private stack child identity replaces the old child identity;
- enabling has exactly one directly owned Relay connector with Host TLS ready
  and a durable connector epoch;
- disabling has a disabled Relay config and no directly owned Relay connector.

If any check fails, the supervisor stops only its replacement stack child,
restores both original configuration files, and launches the old configuration
once. A healthy old configuration records `rolled_back`; otherwise the private
receipt remains `rollback_failed` and the Host fails closed.

## Crash And Isolation Rules

- No durable receipt means no config mutation.
- A crash between config writes restores both original files on reconciliation.
- A crash after response flush but before restart leaves a recoverable pending
  receipt; it cannot silently report success.
- A new supervisor reconciles an unfinished receipt before ordinary startup.
- One generation causes at most one owned stack replacement.
- Tailscale configuration and processes are never read, written, or signalled.
- Hermes/OpenClaw service processes are outside the Host child tree and remain
  untouched.
- SQLite, Human Sessions, tasks, runs, knowledge, memories, artifacts, and audit
  rows survive both successful restart and rollback.

## Acceptance Matrix

| Case | Required proof |
|---|---|
| Managed success | response flush precedes one parent-owned restart; new Host passes health gate |
| Manual foreground | returns `manual_restart_required`; zero process signals |
| Broken response | no restart request; both configs restored |
| Exact-service mismatch | no restart request and bounded failure |
| New stack failure | originals restored byte-for-byte; old Host healthy |
| Replay/concurrency | stale or duplicate generation causes no second restart |
| Crash points | no mixed config and no duplicate replacement |
| Isolation | Tailscale call log empty; Hermes/OpenClaw PIDs unchanged |
| Redaction | HTTP/audit contain no private bytes, paths, digests, certificates, or credentials |

Deterministic tests use temporary Host homes, fake child processes, fake
service-state readers, and loopback health fixtures. They must never restart the
installed Host or mutate the installed Relay/Tailscale/Worker state.
