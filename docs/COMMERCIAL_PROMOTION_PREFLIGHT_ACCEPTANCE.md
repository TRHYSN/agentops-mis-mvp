# Commercial Promotion Preflight Acceptance

## Scope

This slice adds a read-only commercial promotion preflight packet. It tells an
operator whether the current branch can be promoted to review from current
tracked evidence, exact-head CI, branch sync, and clean working-tree state.

It is not a commercial readiness claim and does not execute hosted, billing,
cleanup, Postgres, Hermes, OpenClaw, or live-runtime work.

## Command

```bash
python3 scripts/commercial_promotion_preflight_smoke.py
```

Strict local promotion check:

```bash
python3 scripts/commercial_promotion_preflight_smoke.py --require-promotable
```

## Expected Behavior

- The default command passes when wiring and safety checks are valid.
- `promotion_ready` remains `false` while any required workflow is missing,
  pending, failed, or not matched to the exact current HEAD.
- `promotion_ready` is only `true` when the working tree is clean, the branch is
  not behind upstream, all four exact-head workflow lanes are completed
  successfully, and no safety failures are present.
- The required lanes are `AgentOps MIS CI`, its `BYOC Docker Compose Acceptance`
  reusable job, `BYOC Cross-Schema v9 to v11 Acceptance`, and the top-level
  `BYOC Customer Release Acceptance` workflow triggered by the exact commercial
  integration branch push or a later manual rerun.
- `--require-promotable` fails until the same strict conditions are true.

## Evidence Shape

The packet emits `commercial_promotion_preflight` evidence with explicit
`promotion_workflows` and `blocking_reasons`. A clean packet with pending CI is
still useful review input, but it is not promotable until `blocking_reasons` is
empty.

## Safety Boundaries

- No server start.
- No DB read.
- No ledger mutation.
- No billing, cleanup, hosted, Postgres, Hermes, OpenClaw, or live runtime call.
- No `.env`, credentials, raw logs, raw prompts, raw responses, private
  transcripts, or PR #22 contents.
- Current PR #22 facts remain reference-only through the clean-room breakdown;
  the old branch is not copied or merged.

## Verification

```bash
python3 scripts/commercial_promotion_preflight_smoke.py
python3 scripts/commercial_handoff_status_smoke.py
python3 scripts/commercial_current_evidence_status_smoke.py
python3 scripts/commercial_evidence_packet_index_smoke.py
python3 scripts/release_branch_control_smoke.py
python3 -m py_compile scripts/commercial_promotion_preflight_smoke.py scripts/release_evidence_packet_smoke.py
python3 scripts/release_evidence_packet_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

## Known Limits

- This is a promotion preflight only; it does not generate the final promotion
  packet.
- CI may legitimately be unavailable or in progress during branch development;
  that keeps `promotion_ready=false` without making the smoke fail by default.
- Manual live Hermes/OpenClaw product evidence remains a separate explicitly
  confirmed gate.

## Next Slice

Add `commercial_promotion_packet_smoke.py` after this preflight is merged and
exact-head CI is green.
