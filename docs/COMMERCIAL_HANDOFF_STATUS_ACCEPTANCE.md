# Commercial Handoff Status Acceptance

## Scope

This slice implements a read-only handoff packet for the commercial migration
clean-room plan. It summarizes packet status, lane status and the next
recommended generator from current tracked docs, git metadata and CI readback.

It does not start a server, read a database, mutate a ledger, call
Hermes/OpenClaw, execute cleanup, call billing providers, enable hosted service
claims, introduce Postgres requirements, or merge PR #22.

## Verification

- `python3 scripts/commercial_handoff_status_smoke.py`
- `python3 scripts/commercial_current_evidence_status_smoke.py`
- `python3 scripts/commercial_evidence_packet_index_smoke.py`
- `python3 scripts/release_branch_control_smoke.py`
- `python3 -m py_compile scripts/commercial_handoff_status_smoke.py scripts/release_evidence_packet_smoke.py`
- `python3 scripts/secret_scan_smoke.py`
- `python3 scripts/release_evidence_packet_smoke.py`
- `git diff --check`

## Acceptance Checklist

- [x] Handoff status is generated from current tracked docs and git/CI readback.
- [x] The packet reports clean-room lane states without copying PR #22.
- [x] The packet reports evidence-packet states and the next generator.
- [x] The smoke fails when CI/release wiring is missing, unsafe commercial claims
  appear, token-like material appears, or packet docs embed a stale hard-coded
  SHA.
- [x] The command is wired into CI and the release evidence command manifest.

## Known Limitations

- This is handoff status only. Promotion preflight, promotion packet, receipt
  plan, receipt recording and rerun bundle preview remain separate
  generator-smoke guarded packets.
- Strict promotion remains false until the exact source commit passes
  current-head CI, the final real Hermes/OpenClaw acceptance, clean-customer
  installation, retained-data upgrade/rollback, and the external restore gate.
