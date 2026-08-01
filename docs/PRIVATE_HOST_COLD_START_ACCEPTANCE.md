# Private Host Cold Start Acceptance

Status: deterministic gates and fresh live Hermes/OpenClaw cold start passed

## Problem Found by Real Dogfood

The first authenticated Private Host live attempt reached both Hermes and
OpenClaw task intake, but both returned `409 loop_supervision_blocked`. The
fresh Host had no historical closed-loop evidence, so aggregate Local Readiness
was `blocked`; that status then prevented the first run that could create the
missing evidence.

No Runtime was called in this failed attempt. The isolated Host directory and
its credentials were removed after bounded failure evidence was inspected.

## Resolution

`operator_start_check_local_readiness_gate` now distinguishes:

- malformed, missing, or unsafe Local Readiness: hard `blocked`;
- structurally valid read-only Local Readiness whose aggregate product status
  is blocked on an empty ledger: advisory `attention`;
- ready/attention product state: gate `pass`.

This does not weaken current-code matching, adapter readiness, Runtime doctor,
connector trust, explicit `confirm_run`, external-write prepared actions,
Agent Plan, knowledge retrieval, evaluation, audit, or evidence gates.

## Verification

```bash
python3 -m py_compile server.py agentops_mis_core/operator_start_check.py
python3 scripts/operator_start_check_smoke.py
python3 scripts/operator_loop_supervision_smoke.py
git diff --check
```

The deterministic smoke proves a structurally safe empty-ledger state becomes
`attention`, while malformed Local Readiness remains `blocked`. Product-level
acceptance still requires a new isolated Private Host and fresh successful
Hermes/OpenClaw evidence after this fix.

Subsequent real dogfood also found and closed two downstream cold-start issues:

- a Worker machine Session may read only `GET /api/operator/loop-supervision`
  through its existing `tasks:read` scope; anonymous reads still fail and other
  operator browser routes remain human-session protected;
- missing service-manager receipt/readback is advisory for a confirmed one-shot
  customer Worker when `hard_run_start_gate=false`, while an explicit hard gate
  remains blocking.

These paths are covered by `human_browser_auth_smoke.py` and
`worker_loop_supervision_soft_gate_smoke.py` without calling a Runtime.

The subsequent fresh isolated Host run on commit
`b03235652165b804a8dffb83b3cda94a2680f6f9` completed both Hermes and
OpenClaw customer tasks through an Owner Session. The bounded evidence IDs and
counts are recorded in `docs/PRIVATE_HOST_REAL_RUNTIME_CLIENT_ACCEPTANCE.md`.
