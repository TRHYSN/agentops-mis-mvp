# Private Host + Codex Bridge Integration Acceptance

## Scope

This acceptance records the local integration of:

- Private Host and remote human console from GitHub PR `#104`;
- the Codex governed runtime and Connector UI from GitHub PR `#111`; and
- the installable AgentOps MIS Codex plugin.

The integration was performed in an isolated worktree on
`codex/private-host-codex-bridge-integration`. It does not authorize or claim a
merge to `main`, a GitHub push, or a Notion Canonical update.

## Exact Inputs

- Private Host source: `8a7043d5c06c` (`codex/local-host-remote-console`)
- Codex bridge source: `65821d715e3c` (PR `#111` exact head)
- Codex plugin source: `40fa13a` (local plugin commit)

The resulting integration keeps Private Host human authentication and account
routes while placing Codex under the existing Connector inventory. The legacy
`/admin/codex` route redirects to `/admin/connectors/codex`.

## Integration Fixes

### Host machine connector read

`agentops runtime connectors` now uses:

```http
GET /api/agent-gateway/host-runtime-connectors
```

The route:

- requires the Private Host machine credential;
- rejects bound Agent enrollment and Agent Session tokens;
- performs no runtime refresh or live execution;
- does not mutate the ledger; and
- omits raw base URLs, binary paths, trust notes, and last-error text.

Browser users continue to use `/api/runtime-connectors` with a Human Session.

### Human read performance

Human Session reads of Operator action plan, loop audit, handoff, command
center, and health use current ledger and readiness snapshots without running
live Hermes/OpenClaw start checks. Machine-authenticated CLI/API reads retain
the full supervision view.

The change also prevents repeated action-plan calculation inside the handoff
and loop-audit aggregation path. In the isolated test fixture,
`/api/operator/health` decreased from roughly eight seconds to roughly one
second.

## Verification

Commands used:

```bash
python3 -m py_compile \
  server.py \
  agentops_mis_cli/agentops.py \
  scripts/human_browser_auth_smoke.py \
  scripts/private_host_worker_machine_read_smoke.py

python3 scripts/human_browser_auth_smoke.py
python3 scripts/private_host_worker_machine_read_smoke.py
python3 scripts/operator_health_smoke.py
python3 scripts/operator_handoff_smoke.py
python3 scripts/operator_loop_supervision_smoke.py
python3 scripts/operator_command_center_smoke.py
python3 scripts/codex_plugin_contract_smoke.py
python3 scripts/codex_mis_product_bridge_contract_smoke.py
python3 scripts/module_boundary_smoke.py
python3 scripts/secret_scan_smoke.py

cd ui/start-building-app
npm run build

git diff --check
```

Verified results:

- Human browser authentication and workspace isolation smoke passed.
- Private Host machine-read routes and CLI connector read passed.
- Operator health, handoff, and loop-supervision smokes passed.
- Codex plugin contract passed `89/89` checks.
- Codex product bridge contract passed `156` checks.
- Secret scan reported zero findings across `913` scanned files.
- Vite production build passed before the backend-only integration fixes; the
  existing large-chunk warning remains non-blocking.
- Python compilation and `git diff --check` passed.

## Acceptance Checklist

- [x] Private Host human authentication remains the browser authority.
- [x] Host machine credentials and Human Sessions remain separate.
- [x] Agent enrollment and Agent Session tokens cannot read Host-wide topology.
- [x] Codex is grouped under Connectors, not duplicated in primary navigation.
- [x] Runtime connector CLI reads work in Private Host mode.
- [x] Private connector paths and endpoint details are omitted.
- [x] Human aggregate pages do not implicitly probe live runtimes.
- [x] Machine-authenticated supervision remains available.
- [x] No database, token, `.env`, raw prompt/response, cache, `dist`,
  `node_modules`, or generated export is intended for commit.
- [ ] Run exact-head CI after the branch is pushed.
- [ ] Record a clean exact-head real Codex run in an isolated database.
- [ ] Promote a non-Canonical Notion handoff only after owner authorization.
- [ ] Merge only after owner authorization and exact-head green checks.

## Remaining Boundaries

- This local branch is not yet a GitHub review artifact.
- Notion remains a collaboration and handoff surface, not source-code
  authority.
- Real Hermes/OpenClaw/Codex execution evidence from earlier development is not
  reclassified as an exact-head release receipt.
- No credential issuance, owner bootstrap, password recovery, or remote device
  enrollment behavior is changed by this slice.

## Next Safe Slice

1. Create the local integration commit and verify a clean worktree.
2. With explicit owner authorization, push the exact branch and open a Draft
   PR.
3. Let GitHub Actions verify the pushed exact head.
4. Run one read-only or approval-gated real Codex task against an isolated
   database and record only bounded ledger evidence.
5. With explicit owner authorization, create a non-Canonical Notion Handoff
   that links the exact GitHub review artifact and verification state.
