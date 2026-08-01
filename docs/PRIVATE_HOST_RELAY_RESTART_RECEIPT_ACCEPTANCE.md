# Private Host Relay Restart Receipt Acceptance

Date: 2026-07-18

## Accepted Slice

The private restart receipt can durably coordinate one Relay transition across
the active Relay config and Host config without exposing either file to HTTP,
audit or UI. It binds an action, validated transition ref, persistent monotonic
transaction sequence and ordered state revision.

The receipt is now wired to the Owner confirmation route and exact managed Host
supervisor. A failed HTTP body write restores both originals before any restart;
a failed target runtime restores both originals and relaunches the old Host.

## Proven Boundaries

- receipt, sequence, archive, lock and target files require same-owner regular
  files in mode `0600` under non-symlink directories in mode `0700`;
- both original and target config pairs are size bounded and stored only in the
  private receipt;
- the transition ref is required on every state or config operation and omitted
  from public output;
- transaction sequence persists after receipt finalization and increases for a
  second transition;
- stale transaction sequence and stale state revision fail closed;
- a nonterminal receipt blocks replacement and finalization;
- a terminal receipt may be archived/finalized or explicitly replaced;
- second-file target write failure restores both originals;
- second-file restore failure reapplies both targets, preventing mixed config;
- failed one-time transition consumption after both targets are applied restores
  both originals and records a terminal rollback receipt;
- a response-path restore failure records `rollback_failed` rather than leaving
  an ambiguous nonterminal state;
- public output omits config bytes, refs, paths and digests.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_restart.py \
  scripts/private_host_relay_restart_receipt_smoke.py
python3 scripts/private_host_relay_restart_receipt_smoke.py
git diff --check
```

The smoke passed all permission, symlink, size, replay, ordering, two-file
failure-injection, terminal lifecycle and redaction checks. It uses no network,
subprocess, database, Tailscale or Worker access.

## Remaining Integration

- retain or archive bounded post-restart audit evidence after a successful
  receipt is finalized;
- install and physically validate the integrated source in a later release
  candidate.
