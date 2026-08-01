# Private Host Restart Persistence Acceptance

Status: passed on isolated local Host state

## Verification

```bash
python3 -m py_compile scripts/private_host_restart_persistence_smoke.py
python3 scripts/private_host_restart_persistence_smoke.py
git diff --check
```

The smoke initializes a temporary Private Host, completes Owner bootstrap,
creates a task through the authenticated browser API, stops and starts the
managed Host process, builds the local Markdown/FTS knowledge index, and then
verifies with the original browser cookie that:

- the server-side human Session remains valid;
- the same task remains in the SQLite authority ledger;
- the knowledge index remains searchable and returns bounded document/chunk
  identifiers after restart without exporting source content;
- no Runtime was called;
- no real user database or credential value was printed or retained.

This proves process-restart persistence. Reboot/background-service persistence
and an active Worker continuing through a browser disconnect remain separate
Release Candidate gates.
