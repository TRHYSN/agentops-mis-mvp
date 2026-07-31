# Worker Explicit Result Failure Acceptance

Date: 2026-07-23

## Scope

This slice closes a Worker daemon state-reporting gap for bounded adapter results that
return `ok:false` without raising an exception. It does not change adapter execution,
task intake, approval policy, or ledger authority.

## Product Behavior

- An explicit failed result is reported as `failed`, including when
  `processed:false`.
- Explicit failures increment total and consecutive error counters.
- Explicit failures do not increment the idle counter.
- The state file keeps only a bounded, redacted error type and message.
- A continuing daemon uses error backoff and honors `--max-errors`.
- A daemon without `--continue-on-error` stops after the explicit failure.
- A later healthy idle result clears stale consecutive-error state.
- A detached or failed stdout pipe cannot overturn the Worker exit decision after
  the state and ledger work are complete.

## Verification

Run:

```bash
python3 -m py_compile agentops_mis_cli/worker.py scripts/worker_service_output_resilience_smoke.py
python3 scripts/worker_service_output_resilience_smoke.py
python3 scripts/worker_daemon_resilience_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The focused smoke creates its state file only inside a temporary directory. No local
database, credential, prompt, response, transcript, or token is persisted or committed.

## Boundary

This is source-level deterministic evidence. It does not replace the already recorded
real Hermes/OpenClaw installed-Host run evidence, and it makes no new remote Relay or
physical second-device claim.
