# Private Host Restart Recovery Acceptance

Date: 2026-07-18

## Result

Private Host startup now reconciles an interrupted Relay restart receipt before
loading configuration and launching the stack. The recovery context is private,
identity-bound and limited to action, state, transaction sequence, revision and
transition ref; it never exposes config bytes or paths to HTTP, CLI output,
audit or UI.

## Recovery Policy

| Durable state | Startup action |
|---|---|
| `config_applied` | restore both originals before launch; validate the original runtime |
| `response_flushed` | launch the exact target pair; resume validation |
| `restart_requested` | launch the exact target pair; resume validation |
| `validating_new_host` | launch the exact target pair; repeat the health gate |
| `manual_restart_required` | launch the exact target pair; validate, mark healthy and finalize |
| `healthy` | revalidate the target runtime and finish an interrupted finalize |
| `restoring_config` | restore both originals before launch; validate rollback |
| `rolled_back` | ensure both originals and continue normal startup |
| `rollback_failed` | block startup for explicit operator repair |

An unhealthy recovered target is stopped, both original configs are restored,
and the next launch validates the original runtime. It never starts an original
stack concurrently with a target stack. If the original runtime also fails, the
receipt becomes `rollback_failed` and further blind startup is blocked.

Duplicate start checks run before any receipt or config mutation. Recovery
completion is exception-safe: a receipt transition/finalize failure performs a
bounded target-stack stop, escalates from process-group TERM to KILL for a
background stack, removes the PID record only after confirmed exit, and records
`rollback_failed` when termination cannot be proven.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/host.py \
  agentops_mis_cli/relay_restart.py \
  scripts/private_host_relay_restart_recovery_smoke.py \
  scripts/private_host_restart_start_reconciliation_smoke.py
python3 scripts/private_host_relay_restart_receipt_smoke.py
python3 scripts/private_host_relay_restart_recovery_smoke.py
python3 scripts/private_host_restart_start_reconciliation_smoke.py
python3 scripts/private_host_managed_restart_supervisor_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
git diff --check
```

The deterministic checks use only temporary private Host homes. They cover exact
state/direction gates, stale sequence/ref/revision rejection, target and original
pair idempotence, manual restart finalization, failed target startup, failed
original validation, duplicate-start non-mutation, receipt-write exception
cleanup, bounded process-group termination, permissions and redaction. No installed Host, database,
Tailscale profile, Worker or network endpoint is changed.

## Remaining Release Gates

- run this source from a versioned clean-install release candidate;
- run the bounded post-restart audit projection from that exact installed
  candidate; deterministic coverage is recorded in
  `PRIVATE_HOST_RESTART_AUDIT_RETENTION_ACCEPTANCE.md`;
- deploy and bind authenticated Relay/DNS/certificate infrastructure;
- complete physical MacBook browser-only and fresh Hermes/OpenClaw acceptance.

This acceptance does not claim a deployed Relay or `remote_ready` state.
