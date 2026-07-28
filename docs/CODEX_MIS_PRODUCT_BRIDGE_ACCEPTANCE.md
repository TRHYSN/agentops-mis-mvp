# Codex to AgentOps MIS Product Bridge Acceptance

## Scope

This acceptance covers the first customer-visible bridge slice:

- AgentOps MIS remains the task, approval, evidence, and audit authority.
- Codex remains a governed execution runtime.
- Local loopback and remote enrolled-worker commands are kept distinct.
- Operators can inspect live Codex readiness and ledger evidence at
  `/admin/codex`.
- No installer, device-code login, product MCP bridge, or automatic enrollment
  is claimed in this slice.

The implementation is based on `main` at
`40db4e22387771cf41e7dfed2490eab283fe0b6f`. Exact clean-commit runtime
evidence is recorded in the final section after the implementation commit is
created.

## Implemented Slice

- Added `rtc_codex_local` to the runtime capability and connector registries.
- Added safe Codex adapter readiness, including binary/version/official-bundle
  attestation, workspace-write readiness, trust metadata, and raw path
  omission.
- Added separate local and remote launch guidance:
  - local loopback omits `--use-session`;
  - remote enrolled workers use `--use-session`.
- Kept read-only Codex execution separate from approval-gated workspace-write.
- Added `/admin/codex` as a read-only, fail-closed control surface backed by
  live MIS APIs.
- Fixed Codex run-start supervision so unrelated Hermes/OpenClaw readiness does
  not block a ready Codex runtime.
- Fixed external-write intent classification so `modify` does not accidentally
  match the connector name `Dify`; explicit Dify upload intent remains gated.
- Added an offline static contract smoke for product claims and customer-flow
  boundaries.

## Verification

Commands used:

```bash
python3 -m py_compile \
  server.py \
  agentops_mis_cli/worker.py \
  agentops_mis_runtime/capabilities.py \
  agentops_mis_runtime/connectors.py \
  scripts/codex_worker_adapter_smoke.py \
  scripts/codex_mis_product_bridge_contract_smoke.py \
  scripts/module_boundary_smoke.py \
  scripts/run_start_loop_supervision_gate_smoke.py \
  scripts/runtime_capability_manifest_smoke.py \
  scripts/worker_adapter_readiness_smoke.py

python3 scripts/module_boundary_smoke.py
python3 scripts/run_start_loop_supervision_gate_smoke.py
python3 scripts/codex_worker_adapter_smoke.py
python3 scripts/codex_mis_product_bridge_contract_smoke.py
python3 scripts/worker_adapter_readiness_smoke.py \
  --base-url http://127.0.0.1:18787
python3 scripts/runtime_capability_manifest_smoke.py \
  --base-url http://127.0.0.1:18787

cd ui/start-building-app
npm run build

git diff --check
```

Result:

- Python compile passed.
- Module and supervision boundary smokes passed.
- Codex deterministic worker fixture passed with evidence-chain and
  secret-omission checks.
- Product bridge contract smoke passed `156` checks.
- Live readiness and capability-manifest API smokes passed against the isolated
  local server.
- Vite production build passed; the existing large-chunk warning remains
  non-blocking.
- `git diff --check` passed.
- Generated sample exports were restored and were not accepted as source
  changes.

## Browser Acceptance

Observed at `http://127.0.0.1:19003/admin/codex` against the isolated backend:

- the route rendered with the Chinese locale;
- all eight live data sources reported their actual state;
- Codex worker, run, connector, trust, capability, and attestation evidence
  rendered from live APIs;
- local loopback and remote enrolled-worker commands were visibly different;
- the refresh action completed without an error state;
- browser console returned no warnings or errors;
- the page had no horizontal overflow at a `1280px` viewport; and
- missing MCP/one-click-install capability was labeled `not implemented`.

The page does not issue credentials, start a Worker, approve an action, or infer
remote-machine state.

## Development Dogfood Findings

The first isolated real-runtime attempts were intentionally retained as
diagnostic evidence rather than hidden:

1. A local command using `--use-session` failed because an un-enrolled local
   identity cannot mint a child session. Product guidance now omits that flag
   locally and retains it for remote enrollment.
2. A server started before later source edits failed the current-code
   supervision gate. Restarting the latest source restored the correct
   fail-closed behavior.
3. The word `modify` matched the substring `dify`, creating a false
   PreparedAction. Intent matching now uses ASCII word boundaries, while an
   explicit Dify upload still requires governance.
4. Run `run_gw_ab10f818299d` failed closed after Codex emitted a protocol error.
   The bridge did not relax that gate.
5. Retry run `run_gw_7a1f7d291af8` completed through the normal Agent Gateway
   worker path and produced:
   - verified Agent Plan `plan_959ad3f840f67aea`;
   - plan-evidence manifest `pem_339b04bbba5dd6ab`;
   - Runtime Event `rte_33816f6723d5`;
   - one Tool Call and one Evaluation;
   - linked Artifact, Audit, and candidate Memory
     `mem_gw_3cbf64e83bebed39`; and
   - bounded output summaries with raw prompt, raw response, and credentials
     omitted.

These run IDs use an isolated temporary database and a dirty development
checkout. They prove the integration path during development, but they are not
the clean release receipt.

## Acceptance Checklist

- [x] MIS is the authority for tasks, approvals, evidence, and audit.
- [x] Codex is represented as a governed runtime connector.
- [x] Codex readiness is available without returning the binary path.
- [x] Read-only and workspace-write modes remain separate.
- [x] Workspace-write remains plan-, approval-, lease-, and worktree-gated.
- [x] Local loopback onboarding does not require a child session.
- [x] Remote onboarding guidance retains short-lived session use.
- [x] The browser surface uses live APIs and fails closed.
- [x] Real Codex execution reached Run, Runtime Event, Tool Call, Evaluation,
  Artifact, Audit, Plan Evidence, and candidate Memory during development.
- [x] No DB, token, `.env`, raw prompt/response, cache, `dist`, or
  `node_modules` is part of the intended change.
- [ ] Record a real run against the exact clean implementation commit.

## Known Product Gaps

- No signed customer installer or one-click connection wizard.
- No browser/device-code enrollment and no OS-keychain handoff.
- No Codex-specific product MCP server or supported MCP tool bundle.
- No single first-class Context Packet API that binds task, Git state,
  reviewed project memory, knowledge evidence, plan, and approvals.
- No idempotent first-class Project Delta object joining candidate artifact,
  memory, audit, Git evidence, and human review.
- No first-class customer deliverable object for a longer structured answer.
  The current ledger intentionally keeps only a bounded output summary, so a
  long answer can be truncated even when the governed Run succeeds.
- The control page is observational; task dispatch and approval remain in
  their existing MIS surfaces.

## Exact Clean-Commit Runtime Receipt

The real read-only Codex task ran against exact clean implementation commit
`fbcfea1ab9dbda63d5e2bef1edbb6c8b55f8cb2f`.

- Run: `run_gw_c38225e9b545` (`completed`)
- Task: `tsk_b51e6f88762b` (`completed`)
- Agent: `agt_codex_bridge_exact_fbcfea1`
- Agent Plan: `plan_c558d330c8d5c4de` (`verified`)
- Plan Evidence Manifest: `pem_1ddd66d1fe1958bb` (`verified`)
- Worker Runtime Event: `rte_58b6abba73a3`
- Candidate Memory:
  `mem_gw_agt_codex_bridge_exact_fbcfea1_tsk_b51e6f88762b_a029e6bd9d13`
- Evidence closure: one Tool Call, one Evaluation, one Artifact, one Audit
  record, one Runtime Event, one candidate Memory, and zero approvals because
  the task remained read-only.

Runtime observation recorded four valid Codex JSONL events
(`thread.started`, `turn.started`, `item.completed`, and `turn.completed`) with
zero protocol errors and zero prohibited tool events. The runtime was
ephemeral, strict-configured, read-only, and had web search, apps, browser,
computer use, shell, plugins, goals, image generation, and multi-agent
capabilities disabled. Raw events, raw prompt, raw response, credentials, and
tokens were omitted.

The task requested three risk/action pairs. The bounded ledger summary was
truncated during the third pair, which is why the structured customer
deliverable remains a known product gap rather than being presented as solved.
