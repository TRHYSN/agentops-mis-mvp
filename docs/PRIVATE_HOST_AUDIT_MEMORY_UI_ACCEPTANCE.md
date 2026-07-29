# Private Host Audit and Memory UI Acceptance

## Scope

This slice removes the remaining `mockData` dependency from the formal Audit
Center and Memory Library routes used by the Private Host browser console. It
does not change backend routes, `liveApi`, CI, runtime execution, or external
connectors.

Routes:

- `/admin/audit`
- `/workspace/memory`

## Implemented Contract

### Audit Center

- Reads bounded audit records through the existing `loadAudit` API wrapper.
- Remains read-only and preserves `user`, `agent`, and `system` actor filters.
- Provides explicit loading, error, empty, and refresh states.
- Does not claim that the tamper chain is verified from list data alone.
- Explains in Chinese and English that raw prompts, responses, and credentials
  are outside the ordinary audit view.

### Memory Library

- Reads the live memory ledger through `loadMemories`.
- Approves or rejects candidate memories through `decideMemory`.
- Updates the reviewed row in place after a successful decision.
- Provides per-memory busy state, action error feedback, page loading state,
  empty state, and manual refresh.
- Preserves scope/status filters and includes bilingual labels for memory scope,
  status, known memory types, metadata, and review actions.
- Does not load agents, tasks, raw conversations, or transcripts to decorate the
  review view; only bounded IDs already present in the memory record are shown.

## Verification

```bash
python3 -m py_compile scripts/private_host_audit_memory_ui_smoke.py
python3 scripts/private_host_audit_memory_ui_smoke.py
cd ui/start-building-app && npm run build
git diff --check -- \
  ui/start-building-app/src/app/components/pages/AuditCenter.tsx \
  ui/start-building-app/src/app/components/pages/MemoryLibrary.tsx \
  scripts/private_host_audit_memory_ui_smoke.py \
  docs/PRIVATE_HOST_AUDIT_MEMORY_UI_ACCEPTANCE.md
```

Expected static-smoke evidence:

- both pages have no `mockData` import or read;
- Audit uses `loadAudit`, actor filtering, and no mutation API;
- Memory uses `loadMemories` and `decideMemory` for both decisions;
- Memory exposes busy, loading, error, refresh, and bilingual review states;
- no token-like material is embedded in either page;
- no live runtime or external service is called by the smoke.

## Verification Result

Verified on 2026-07-12 against the current worktree:

- `python3 scripts/private_host_audit_memory_ui_smoke.py`: passed, 26 checks;
- `python3 scripts/human_browser_auth_smoke.py`: passed against a temporary
  database with no real runtime call;
- `npm run build`: passed, 2,279 modules transformed;
- scoped `git diff --check`: passed.

Vite continues to report the existing large-chunk advisory for the main bundle.
This is a performance follow-up and did not fail this scoped UI acceptance.

## Limitations

- The static smoke proves source wiring and safety boundaries; it does not
  replace a headed browser test against an authenticated Private Host.
- Backend human role enforcement remains authoritative. This slice does not add
  role-aware button hiding or account management UI.
- Audit pagination and server-side filtering remain future work; the existing
  bounded `loadAudit` request is filtered client-side.
- Memory decisions still depend on the existing approval-role, CSRF, Origin,
  and Session enforcement in the backend.
