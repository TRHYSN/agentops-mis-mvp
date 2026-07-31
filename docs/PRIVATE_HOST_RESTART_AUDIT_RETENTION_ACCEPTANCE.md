# Private Host Restart Audit Retention Acceptance

Status: deterministic local acceptance passed; exact-package and deployed Relay evidence remain open

## Product Slice

The Host restart supervisor now retains the final outcome of an Owner-confirmed
Relay transition after the HTTP process has been replaced. A terminal
`healthy`, `rolled_back`, or `rollback_failed` transition writes one owner-only
outbox event containing only:

- action and terminal state;
- transaction sequence and receipt revision;
- the existing bounded transition reference.

The event contains no Relay or Host configuration, filesystem path,
certificate, credential, secret, token, digest, prompt, response or Runtime
content.

The MIS server consumes events only when its SQLite path is the exact database
inside the same Private Host home. It commits a system audit row before
acknowledging the private event. A repeated event is recognized by its bounded
transition reference and terminal action, acknowledged, and not inserted a
second time. This makes a crash between SQLite commit and outbox deletion safe
to retry.

An outbox write failure does not terminate a healthy replacement or restored
Host. The terminal receipt remains the durable source, the event is marked
pending, and the next bounded server tick recreates it. A `healthy` event is not
committed while its same-sequence receipt still exists; if finalization fails,
a higher-revision rollback outcome atomically replaces it. SQLite contention is
limited to 50 milliseconds and returns a private retryable pending state rather
than failing an ordinary API request. Outbox locks are also non-blocking on the
API path, as are receipt/sequence reads. Lifecycle outbox writes now use the
same bounded non-blocking behavior while receipt state remains durable; core
receipt transitions continue to use strict identity and state validation.

A retryable terminal-receipt finalize failure keeps the validated replacement
Host alive and leaves the receipt plus outbox evidence for startup recovery or
a later operation. Receipt identity or state corruption still fails closed and
terminates the untrusted replacement. Malformed event files are moved
atomically into an owner-only quarantine and no longer prevent later valid
events from being consumed. Terminal replacement reads its exact transaction
sequence directly instead of depending on the first page of the queue.

## Verification

```bash
python3 -m py_compile \
  server.py \
  agentops_mis_cli/host.py \
  agentops_mis_cli/relay_restart.py \
  scripts/private_host_restart_audit_retention_smoke.py
python3 scripts/private_host_restart_audit_retention_smoke.py
python3 scripts/private_host_restart_start_reconciliation_smoke.py
python3 scripts/private_host_relay_managed_restart_rollback_smoke.py
python3 scripts/private_host_post_response_action_smoke.py
git diff --check
```

The focused smoke proves owner-only event storage, database-to-Host binding,
exactly-once ledger projection, retry acknowledgement, bounded metadata and
terminal success/rollback outcomes. It also injects outbox write failure,
unfinalized success, success-to-rollback replacement, SQLite write contention
outbox lock contention, retryable finalize failure, receipt corruption,
malformed-event quarantine, a terminal event beyond the first 64 queue entries
and queue capacity exhaustion. Existing restart and response-path smokes remain
the source of truth for process replacement, health validation and config-pair
rollback.

## Boundaries

- This is deterministic local evidence, not a deployed Relay receipt.
- The current installed preview.33 is unchanged.
- No Host, Worker, Hermes, OpenClaw or Tailscale process is started or stopped
  by this smoke.
- `rollback_failed` remains fail closed and requires an explicit operator
  repair workflow before Host startup; this slice records the outcome but does
  not add that repair UI.
- A versioned clean-install candidate and physical MacBook browser-only run are
  still required before release claims.
