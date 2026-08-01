# Operator Service Receipt Signature Acceptance

Date: 2026-07-23

Status: focused source implementation and local regression complete; exact-head
CI is a separate gate.

## Product problem

Local Readiness and the Operator Admission Packet expose the same
`preview_worker_service_control` action. They previously rebuilt its receipt
identity with different namespaces:

- `local_readiness.service_control_preview`;
- `operator_start_check.service_control_preview`.

The command and verification target were identical, but the action ID, source
and SHA-256 signature differed. An operator could therefore record the receipt
recommended by the Admission Packet and still see Local Readiness classify the
same action as stale.

## Contract

The Admission Packet now reuses the canonical identity already projected by
Local Readiness:

- action ID: `local_readiness.service_control_preview.<adapter>`;
- source: `local_readiness.service_control_preview.<adapter>`;
- signature: the exact Local Readiness action signature;
- Control Readback source: the same source plus `.control_readback`.

If a structural caller supplies no projected service step, the fallback
signature uses the same Local Readiness namespace. This remains a preview-only
control action: the server does not execute shell commands, load a service or
run an adapter.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_core/operator_start_check.py \
  scripts/operator_start_check_smoke.py \
  scripts/operator_start_check_api_smoke.py
python3 scripts/operator_start_check_smoke.py
python3 scripts/operator_start_check_api_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

Both focused smokes now compare the Admission Packet receipt/readback commands
against the exact Local Readiness action ID, source and signature. Both smokes
are already part of the backend CI suite.

No service control, Runtime execution, credential read, database migration or
raw prompt/response processing is performed by this acceptance.
