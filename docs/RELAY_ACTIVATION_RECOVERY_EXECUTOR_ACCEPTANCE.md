# Relay Activation Recovery Executor Acceptance

## Scope

This acceptance covers the private one-step recovery executor in
`agentops_mis_cli.relay_activation_recovery_executor`.

The production entrypoint opens the exact activation journal under its
lifecycle lock, obtains a same-root locked scanner capability, repeats the
stable recovery observation, requires the exact confirmed `decision_sha256`,
and advances one `run_step` decision. It remains absent from
`agentops-relayctl`.

## One-Step Contract

Malformed plan, outcome, or confirmation input fails before reading the store.
A canonical but stale confirmation fails after a read-only observation and
before a write or mutation. Blocked and non-`run_step` decisions also have zero
effects.

For a confirmed step the executor:

1. reuses an exact durable intent or appends one new intent revision;
2. rescans and requires the pre-mutation state to remain identical to the
   confirmed observation;
3. runs at most one scanner-bound mutation;
4. performs a second stable scan;
5. rebuilds canonical evidence and appends one observed revision; and
6. returns a bounded `recovery_required` projection so the operator must
   preview and confirm the next decision separately.

The mutation mapping is fixed:

| Recovery step | Bound systemd mutation |
| --- | --- |
| `daemon_reload` | `daemon_reload` |
| `enable` | `enable` |
| `start` | `start` |
| `rollback_stop` | `stop` |
| `rollback_disable` | `disable` |
| forward or rollback `verify` | none |

An interrupted intent is reused only when its exact step and intent ID match.
Ownership changes occur only in the observed revision: enable and start set
their respective ownership flags, rollback stop clears start ownership, and
rollback disable clears both flags. Rollback verification uses the dedicated
restored-state evidence.

Once an intent write or mutation is attempted, every failure returns only
`activation_recovery_required`. The durable intent remains available for the
next stable preview. Raw systemd output, paths, credentials, journal bodies,
prompts, and responses are not returned.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_recovery_executor.py \
  scripts/relay_activation_recovery_executor_smoke.py
python3 scripts/relay_activation_recovery_executor_smoke.py
python3.11 scripts/relay_activation_recovery_executor_smoke.py
python3 scripts/relay_activation_recovery_controller_smoke.py
python3 scripts/relay_activation_recovery_preview_smoke.py
python3 scripts/relay_activation_recovery_decision_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_activation_scan_smoke.py
python3 scripts/relay_systemd_mutation_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

Expected executor summary:

```json
{
  "blocked_zero_effect": true,
  "cli_surface_exposed": false,
  "confirmation_zero_effect": true,
  "intent_reuse_verified": true,
  "mutation_failure_retained": true,
  "network_used": false,
  "ok": true,
  "private_payload_omitted": true,
  "pre_mutation_drift_retained": true,
  "production_lock_composed": true,
  "recovery_steps": 8,
  "systemd_mutation_operations": [
    "daemon_reload",
    "disable",
    "enable",
    "start",
    "stop"
  ],
  "write_scope": "fixture_journal_only"
}
```

The smoke covers a new and interrupted daemon reload, enable, start, forward
verify, rollback stop, rollback disable, rollback verify, invalid and stale
confirmation, blocked and non-run decision rejection, and a mutation failure
after the durable intent. It also changes a prerequisite after intent
publication and proves the executor retains recovery state without invoking
the mutation.

## Truth Boundary

The deterministic executor smoke uses fixture journals and a fake stateful
mutation runner. `RELAY_LINUX_SYSTEMD_RECOVERY_ACCEPTANCE.md` records the
complete forward and rollback sequence against real systemd, and
`RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md` records the exact real-root
installation baseline. `RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md` now
combines those paths with the packaged Relay process and closes/reopens the
production store between every preview and confirmed controller invocation.

No CLI, API, or browser caller is exposed. The next gate injects actual process
death at intent, mutation, observation, receipt, and terminal boundaries. Only
after that evidence may an operator-facing confirmed activation/recovery
command be enabled. Public Relay and physical ordinary-browser acceptance
remain separate.
