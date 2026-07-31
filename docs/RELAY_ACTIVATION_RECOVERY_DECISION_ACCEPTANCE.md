# Relay Activation Recovery Decision Acceptance

## Scope

This acceptance covers the side-effect-free recovery decision compiler in
`agentops_mis_cli.relay_activation_recovery`.

It consumes an already validated journal recovery snapshot, one caller-owned
stable prerequisite/systemd observation, and an explicit requested outcome of
`resume` or `rollback`. It returns one hash-bound bounded decision. It does not
open the production journal, read systemd, publish a revision or receipt, run a
mutation, or expose a CLI command.

## Decision Contract

Every decision binds:

- the exact activation plan and current journal head hashes;
- the latest durable revision number;
- the requested outcome;
- the selected action, operation, and step;
- an optional exact current observation hash;
- an optional durable receipt hash; and
- a canonical `decision_sha256`.

Before planning any nonterminal action, the compiler rebuilds the original
pre-mutation activation plan from the current private prerequisite snapshot.
It restores the journal-bound pre unit/active states and pre enablement
inventory, tries only the parser-valid original reload/result variants, and
requires the exact original `plan_sha256`. This makes config, certificate,
private-key, route-key, service-account, systemctl, release-tree, and trusted
parent drift fail closed without adding those private identities to output.

The bounded actions are:

| Action | Meaning |
| --- | --- |
| `complete` | The exact terminal revision and receipt already agree. |
| `terminalize` | A legal orphan receipt can bind the next terminal revision. |
| `resume` | Retry one unchanged forward step, record a non-ambiguous observation, continue the next fixed step, or publish the success receipt. |
| `inverse` | Run or record one ownership-proven rollback step, verify restored state, or publish the rollback receipt. |
| `blocked` | State drift, missing ownership proof, or an incomplete rollback contract prevents an automatic action. |

An interrupted `enable` or `start` intent is never converted into ownership
solely because the current state resembles the expected result. Without a
durable observed revision, another operator may have produced that state, so
the decision is `blocked` with `ownership_ambiguous`.

Automatic inverse planning requires the current state to reproduce the exact
historical ownership evidence:

- start ownership requires the original post-start `InvocationID` hash; and
- enable ownership requires the original post-enable link inventory hash.

A changed invocation, link inventory, unit identity, release identity, or
journal head remains blocked. No blocked decision is executable.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_recovery.py \
  scripts/relay_activation_recovery_decision_smoke.py
python3 scripts/relay_activation_recovery_decision_smoke.py
python3.11 scripts/relay_activation_recovery_decision_smoke.py
python3 scripts/relay_activation_recovery_snapshot_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_activation_controller_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

Expected summary:

```json
{
  "ambiguous_ownership_blocked": true,
  "decision_hash_deterministic": true,
  "forward_resume_cases": 7,
  "invalid_inputs_rejected": true,
  "network_used": false,
  "ok": true,
  "orphan_receipt_terminalized": true,
  "owned_enablement_drift_blocked": true,
  "ownership_inverse_cases": 2,
  "private_payload_omitted": true,
  "private_prerequisite_drift_blocked": true,
  "rollback_terminal_contract": true,
  "systemd_mutation_performed": false,
  "write_scope": "none"
}
```

The smoke covers prepared recovery, mutation retry, non-owning observation
completion, every forward step, success receipt publication, orphan receipt
terminalization, idempotent completed history, explicit rollback planning,
rollback verification/receipt/terminal completion, refusal to resume after
rollback starts, InvocationID and enablement-inventory drift, ambiguous
ownership, malformed chains, private prerequisite drift, deterministic hashes,
and bounded output.

## Remaining Gates

The private production recovery preview in
`RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md` now holds the lifecycle lock
while it loads the exact snapshot, performs scanner/systemd/scanner stable
observation, reloads the unchanged snapshot, and compiles this decision.
A later exact confirmation must bind the decision hash before any one-step
write or mutation. The private non-systemd writer in
`RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md` now enforces that binding
for observation, success-receipt, terminal-revision, and complete actions.

The private executor in
`RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md` now binds one confirmed
`run_step` to the production locked store, scanner, and mutation adapter; its
stateful execution is fixture-accepted. Operator-facing confirmation, CLI
exposure, and interrupted real Linux systemd acceptance remain unimplemented.
Ambiguous ownership-changing intents remain deliberately blocked rather than
guessed.
