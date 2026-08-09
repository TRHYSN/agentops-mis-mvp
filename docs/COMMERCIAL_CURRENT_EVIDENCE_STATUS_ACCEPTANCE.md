# Commercial Current Evidence Status Acceptance

## Scope

This slice implements the first Lane 4 evidence packet generator as a static
read-only smoke. It reports current git, checklist, CI and command-manifest
status without starting a server, reading a database, calling Hermes/OpenClaw,
or mutating any ledger.

## Verification

- `python3 scripts/commercial_current_evidence_status_smoke.py`
- `python3 scripts/commercial_evidence_packet_index_smoke.py`
- `python3 -m py_compile scripts/commercial_current_evidence_status_smoke.py scripts/release_evidence_packet_smoke.py`
- `python3 scripts/release_evidence_packet_smoke.py`
- `python3 scripts/secret_scan_smoke.py`
- `git diff --check`

## Acceptance Checklist

- [x] Current Evidence Status is generated from current tracked docs, workflow
  metadata and git/CI readback.
- [x] No server, DB, ledger, billing provider, cleanup action, hosted service,
  Postgres adapter or live runtime is invoked.
- [x] Output carries exact current `HEAD`, branch, upstream sync, working-tree
  entry count, CI readback, release checklist state and explicit commercial
  limits.
- [x] The smoke fails if CI/release wiring is missing, unsafe commercial claims
  appear, token-like material appears, or commercial packet docs embed a stale
  hard-coded SHA.
- [x] The command is wired into CI and the release evidence command manifest.
- [x] The shared CI readback treats top-level `BYOC Customer Release Acceptance`
  as required exact-SHA promotion evidence. It runs on the commercial
  integration branch push without wiring package-write permission into
  pull-request CI.

## Known Limitations

- This is a current-evidence status packet only. It does not create promotion,
  receipt or rerun packets.
- Strict promotion readiness remains false until the exact branch head has green
  CI, all required BYOC workflow evidence including customer release is green,
  and the working tree is clean.
