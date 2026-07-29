# Audit Chain Concurrency Acceptance

Status: deterministic local acceptance passed

## Contract

Every audit append uses a singleton SQLite chain-head row inside one INSERT
statement. The statement reads the current predecessor, computes the new
tamper-chain hash through a deterministic SQLite function, inserts the audit
row and advances the chain head through an AFTER INSERT trigger. SQLite writer
serialization therefore covers the complete append without an explicit or
long-lived application transaction.

Existing databases initialize the chain head from the last stored audit row.
Deterministic `audit_id` replay keeps `INSERT OR IGNORE` semantics: an ignored
duplicate does not run the trigger or advance the head.

## Verification

```bash
python3 scripts/audit_chain_concurrency_smoke.py
python3 scripts/sqlite_long_transaction_audit_smoke.py
python3 scripts/sqlite_concurrency_smoke.py
python3 scripts/migration_rollback_smoke.py
```

The concurrency smoke starts two independent processes with separate SQLite
connections. They append 80 unique rows plus the same deterministic replay row,
then reconstruct the chain from `genesis`. Acceptance requires all 81 rows to
form one unique path, the singleton head to equal its terminal hash, and the
replayed ID to appear exactly once.

## Boundaries

- The chain is tamper-evident evidence, not an external signature or remote
  transparency log.
- Audit metadata remains bounded and must not contain credentials, raw prompts,
  raw responses, private messages or full transcripts.
- Release acceptance still requires exact-head CI and a versioned package.
