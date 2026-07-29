# Private Host + Codex Bridge Integration Acceptance

## Scope

This acceptance records the local integration of:

- Private Host and remote human console from GitHub PR `#104`;
- the Codex governed runtime and Connector UI from GitHub PR `#111`; and
- the installable AgentOps MIS Codex plugin.

The integration was performed in an isolated worktree on
`codex/private-host-codex-bridge-integration`. It does not authorize or claim a
merge to `main` or a Notion Canonical update.

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

### External-write intent word boundaries

Exact-head dogfood found that the ordinary English word `modifying` contained
the connector name `dify` as a substring and was therefore classified as an
external write. ASCII connector and action terms now require word boundaries,
while common action inflections such as `uploading`, `sending`, and `sent`
remain fail-closed. Chinese action terms retain substring matching.

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
python3 scripts/customer_worker_external_write_gate_smoke.py
python3 scripts/worker_external_write_preflight_gate_smoke.py
python3 scripts/codex_worker_adapter_smoke.py
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

## Real Codex Dogfood

A real read-only Codex task completed against clean integration commit
`d617c0af18c93fad2335c16dd0316a3359340b18` using an isolated SQLite database
and loopback server:

- Run: `run_gw_93b57d77dbf9` (`completed`)
- Task: `tsk_b892f4a1916c` (`completed`)
- Agent Plan: `plan_69b29e9d5df1e0da` (verified)
- Plan Evidence Manifest: `pem_51219f5290b0d717` (verified)
- Worker Runtime Event: `rte_361ef0adbb94`
- Evidence: one Tool Call, one Evaluation, one Artifact, bounded Audit records,
  and one candidate Memory
- Runtime protocol: four valid Codex JSONL events, zero parse errors, and zero
  prohibited events
- Runtime boundary: ephemeral strict read-only sandbox, web and interactive
  tools disabled, raw prompt/response/events and credentials omitted

The first wording attempt stopped before model execution and created an
Approval Wall record because of the `modifying`/`dify` substring collision.
The approval was not bypassed or self-approved. The second unambiguous
read-only task completed, and the classifier regression is covered by the
follow-up word-boundary tests above.

The temporary server was stopped, generated sample-export drift was restored,
and the temporary database was removed after bounded evidence readback.

## Packaged Host Acceptance

### Preview 43 fail-closed receipt

The first packaged candidate was built from exact commit
`724f89da746bf45fe16b312a91193efe76c4c68d` as
`1.6.0-private-host-preview.43`. Its archive, manifest, clean-machine consumer,
upgrade, rollback, secret, Host, and Worker gates passed. The existing Host
ledger and Owner setup were preserved through a verified backup and atomic
upgrade.

The first installed Codex task failed closed:

- Task: `tsk_c568bdbb8937`
- Run: `run_gw_373d973a14bf`
- failure: `CodexProtocolViolation`
- failed Plan Evidence Manifest: `pem_6e113c06a583df30`

The failed Run, Tool Call, Evaluation, Artifact, Runtime Event, and Audit were
retained. No success was manufactured. Diagnosis showed that the packaged
directory is intentionally not a Git worktree, while Codex CLI rejects
non-Git working directories unless explicitly allowed.

### Packaged-runtime compatibility fix

Commit `694137a0f6dcab33dac147f79749f6ea1efe52f2` adds
`--skip-git-repo-check` only to the governed Codex read-only command. The
existing safety boundary remains:

- ephemeral strict configuration;
- read-only sandbox;
- web, apps, browser, computer use, shell, unified exec, plugins, goals,
  image generation, hooks, and multi-agent disabled;
- prompt delivered over stdin;
- AgentOps credentials excluded from the Codex child;
- raw prompt, response, and event bodies omitted;
- prohibited tool events fail the Run.

The same fix distinguishes recovered transport errors from incomplete runtime
protocols. A Run may pass after transient reconnect events only when it still
has exactly one thread start, turn start, turn completion, and read-only agent
message, the process exits successfully, and no prohibited event appears.
Recovered error-event counts remain in runtime evidence. Deterministic fixtures
cover the recovered and malformed paths.

A real Codex invocation in a temporary non-Git directory then passed with:

- `protocol_valid=true`;
- four recovered TLS reconnect events;
- one final agent message;
- zero prohibited events; and
- raw prompt, response, and token omission gates true.

### Preview 44 installed receipt

The fixed commit was packaged and installed as
`1.6.0-private-host-preview.44`. Readback reports:

- packaged commit: `694137a0f6dcab33dac147f79749f6ea1efe52f2`;
- previous version: `1.6.0-private-host-preview.43`;
- Host health: ready;
- Owner login: ready;
- local Console: `http://127.0.0.1:18878/workspace`;
- private Console: Tailscale Serve HTTPS on port `8443`;
- Funnel: disabled;
- four restored Hermes/OpenClaw service Workers, all fresh and idle;
- Codex, OpenClaw, and Agent Gateway connectors available through the
  Host-machine redacted connector view.

Both local and private Workspace URLs returned HTTP 200. Protected dashboard
data returned HTTP 401 without a Human Session. `agentops host open-console`
successfully prepared and opened a bounded local-authority browser handoff
without printing authority material.

The installed Host then completed a real governed Codex task:

- Agent: `agt_codex_installed_preview44`
- Task: `tsk_8b0bcb877072` (`completed`)
- Run: `run_gw_3ef64d0355ff` (`completed`)
- Agent Plan: `plan_6d88310543c8191d` (verified)
- Plan Evidence Manifest: `pem_ce100acfc7e6d8b8` (verified)
- Worker Runtime Event: `rte_ea2957a98bff`
- Audit: `aud_520293fd76d4`
- Evidence: one Tool Call, one passing Evaluation, one Artifact, one candidate
  Memory, and zero Approvals
- Session: short-lived and revoked after the one-task run
- Runtime protocol: four recovered TLS reconnect events, one final agent
  message, zero parse errors, zero prohibited events, and `protocol_valid=true`

This is installed-package evidence against the preserved real Host ledger, not
an isolated CI fixture.

## Acceptance Checklist

- [x] Private Host human authentication remains the browser authority.
- [x] Host machine credentials and Human Sessions remain separate.
- [x] Agent enrollment and Agent Session tokens cannot read Host-wide topology.
- [x] Codex is grouped under Connectors, not duplicated in primary navigation.
- [x] Runtime connector CLI reads work in Private Host mode.
- [x] Private connector paths and endpoint details are omitted.
- [x] Human aggregate pages do not implicitly probe live runtimes.
- [x] Machine-authenticated supervision remains available.
- [x] A real Codex read-only task completed through the integrated Gateway.
- [x] Agent Plan and Plan Evidence were verified for the real run.
- [x] External-write connector names do not match inside ordinary ASCII words.
- [x] Packaged Codex read-only execution works outside a Git worktree.
- [x] Recovered transport errors remain visible without converting a complete
  successful protocol into a false failure.
- [x] `preview.44` is installed locally with `preview.43` retained for rollback.
- [x] Host health, Owner login, Tailscale Serve, and four service Workers
  recovered after the atomic upgrade.
- [x] A real Codex task completed through the installed package and produced a
  verified Plan Evidence Manifest.
- [x] No database, token, `.env`, raw prompt/response, cache, `dist`,
  `node_modules`, or generated export is intended for commit.
- [x] Push the integration branch and open Draft PR `#112`.
- [x] Create a non-Canonical Notion Handoff after owner authorization.
- [ ] Run exact-head CI after the final acceptance update is pushed.
- [ ] Merge only after owner authorization and exact-head green checks.

## Remaining Boundaries

- PR `#112` remains a Draft until its final exact-head checks pass.
- Notion remains a collaboration and handoff surface, not source-code
  authority.
- The latest installed receipt covers Codex. Existing Hermes/OpenClaw service
  Workers are healthy, but their earlier Runs are not reclassified as
  preview.44 execution evidence.
- No credential issuance, owner bootstrap, password recovery, or remote device
  enrollment behavior is changed by this slice.
- Physical second-Mac browser acceptance remains a separate device gate.

## Next Safe Slice

1. Push this final acceptance update and let GitHub Actions verify its exact
   head.
2. Update the non-Canonical Notion Handoff with the installed preview.44
   receipt and final CI state.
3. Mark PR `#112` ready only after all exact-head checks pass.
4. Merge only through the reviewed GitHub path; do not bypass branch review.
