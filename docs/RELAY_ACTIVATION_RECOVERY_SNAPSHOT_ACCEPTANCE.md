# Relay Activation Recovery Snapshot Acceptance

## Scope

This acceptance covers the private read-only recovery snapshot in
`agentops_mis_cli.relay_activation_journal`.

While the production lifecycle lock is held, it loads one exact plan's highest
contiguous validated revision chain and at most one receipt that can bind the
next or existing terminal revision. It does not read systemd, choose resume or
rollback, append a revision, publish a receipt, or expose a CLI command.

## Snapshot Contract

The immutable snapshot contains only parsed bounded journal objects:

```text
ActivationJournalRecoverySnapshot
  revisions: tuple[ActivationJournalRevision, ...]
  receipt: ActivationJournalReceipt | None
```

Loading fails closed unless:

- the requested plan directory has one contiguous authenticated chain;
- every receipt filename, canonical body, and content hash is valid;
- no more than one receipt belongs to the requested plan;
- a terminal chain has its exact terminal-bound receipt; and
- an orphan receipt can form a valid next terminal revision with the current
  chain under the full sequence and terminal-binding validators.

Receipts for other completed plans are parsed and validated but do not
contaminate the requested plan snapshot. A premature receipt, mismatched
identity, ownership mismatch, malformed file, duplicate matching receipt,
broken chain, or changing descriptor binding returns only
`activation_journal_recovery_required`.

The locked production wrapper revalidates root, admin directory, lifecycle
lock, activation namespace, transaction directory, and receipt directory
before and after the read. A snapshot cannot escape a closed lock context.

## Verification

Run:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_activation_journal.py \
  scripts/relay_activation_recovery_snapshot_smoke.py
python3 scripts/relay_activation_recovery_snapshot_smoke.py
python3 scripts/relay_activation_journal_smoke.py
python3 scripts/relay_activation_production_store_smoke.py
python3 scripts/relay_activation_controller_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

The smoke verifies receipt-free in-progress state, a valid orphan receipt,
completed terminal state, coexistence with another completed plan, premature
receipt rejection, lifecycle-lock guard behavior, descriptor stability,
private-output omission, zero network use, and zero systemd mutation.

Expected summary:

```json
{
  "completed_terminal_identified": true,
  "descriptor_stable": true,
  "foreign_receipts_ignored": true,
  "invalid_orphan_rejected": true,
  "locked_store_guarded": true,
  "network_used": false,
  "ok": true,
  "orphan_receipt_identified": true,
  "private_payload_omitted": true,
  "systemd_mutation_performed": false,
  "write_scope": "fixture_journal_only"
}
```

## Remaining Gates

The pure compiler in `RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md` now
combines this snapshot with a caller-owned stable prerequisite/systemd
observation and produces a deterministic, hash-bound complete, terminalize,
resume, inverse, or blocked decision.

Production lifecycle-lock-bound stable scanning and decision projection are now
composed in `RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md`. One confirmed
scanner-bound recovery step is production-composed and fixture-accepted in
`RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md`; operator confirmation, CLI
exposure, automatic multi-step recovery, and real interrupted Linux systemd
acceptance remain unimplemented.
