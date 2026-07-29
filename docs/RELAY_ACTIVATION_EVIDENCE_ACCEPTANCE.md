# Relay Activation Evidence Acceptance

## Scope

This acceptance covers the private, side-effect-free compiler that binds one
exact refreshed activation plan to its immutable journal identity and compiles
bounded ownership evidence after each reserved activation or rollback step.
It also defines a dedicated final rollback verification observation that proves
the exact pre-activation service state and enablement inventory were restored.

It does not expose a CLI writer, call systemd, append journal revisions, decide
which step runs next, publish a receipt, or implement crash recovery.

## Identity Contract

`build_activation_journal_identity` accepts the private validated prerequisite
and systemd snapshots plus the exact confirmed plan hash. It recompiles the
plan and rejects stale, invalid, already-active, mismatched release, or
mismatched version input.

The returned journal identity binds:

- the exact confirmed activation plan hash;
- release and version IDs;
- pre-activation unit-file and active states;
- a canonical hash of the pre-activation enablement-link inventory; and
- a canonical hash of the installed unit file identity.

The compiler validates the result against the immutable activation-journal
schema before returning it. Raw paths, file metadata, link targets, numeric
ownership, Invocation IDs, configuration, credentials, and source snapshots
are not part of its public return type.

## Step Evidence Contract

The compiler accepts only these fixed step IDs:

| Step | Required observed state | Bound evidence |
|---|---|---|
| `daemon_reload` | original enabled/active states and inventory, reload no longer needed | unit identity and bounded state |
| `enable` | originally disabled, now enabled, active state unchanged | unit identity and exact changed link-inventory hash |
| `start` | originally inactive, now enabled and active | unit identity and exact Invocation ID |
| `verify` | enabled and active | unit identity, link-inventory hash, and bounded systemd health |
| `rollback_stop` | originally inactive, now enabled and inactive | unit identity and bounded state |
| `rollback_disable` | originally disabled, now disabled with original active state and inventory restored | unit identity and exact restored link-inventory hash |
| rollback `verify` | both ownership flags cleared, original unit/active states and inventory restored, reload not needed | unit identity, restored inventory hash, and bounded systemd health |

Every current snapshot is revalidated through the Activation Plan Core. A
changed release, version, unit identity, unsafe state, pending daemon reload,
wrong ownership transition, unknown step, or tampered journal identity returns
only `activation_evidence_invalid`.

The result contains only:

```json
{
  "step_id": "start",
  "observation_id": "start_observed",
  "observation_sha256": "<64-hex>"
}
```

The observation hash is suitable for a later journal revision; the compiler
does not return its private canonical payload.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_evidence.py \
  scripts/relay_activation_evidence_smoke.py
python3 scripts/relay_activation_evidence_smoke.py
python3 scripts/relay_activation_plan_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

The deterministic smoke verifies all six step observations plus the dedicated
rollback verification observation, exact plan binding,
unit and enablement ownership changes, Invocation ID changes, stale and invalid
input rejection, bounded errors, private-payload omission, and zero network or
systemd mutation.

Expected summary:

```json
{
  "deterministic_plan_bound_identity": true,
  "invalid_inputs_redacted": true,
  "network_used": false,
  "observation_count": 7,
  "ok": true,
  "ownership_identity_changes_detected": true,
  "private_payload_omitted": true,
  "systemd_mutation_performed": false
}
```

## Remaining Gates

The private exact-confirmed success controller now holds the lifecycle lock,
refreshes and matches the plan, opens the production journal, appends the
prepared revision before mutation, invokes one private mutation at a time,
rescans and compiles each observation, publishes one immutable receipt, and
appends terminal state. It is not exposed through the CLI.

The exact-confirmed non-systemd recovery writer now records the dedicated
rollback verification, publishes one `rollback_succeeded` receipt, appends the
`service_state_rolled_back` terminal revision, and then completes idempotently
in fixture journals. Scanner-bound recovery mutations and a production
recovery opener remain required before the CLI can use that controller.

Real daemon reload, enable, start, stop, disable, reboot persistence, and crash
recovery require a disposable Linux systemd host with root authority. Public
Relay and physical ordinary-browser acceptance remain separate gates.
