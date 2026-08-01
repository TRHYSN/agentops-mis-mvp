# Run Project Context Receipt Acceptance

Status: source implementation, exact-head CI, preview.42 installation and real
Hermes/OpenClaw ledger acceptance complete; physical second-Mac browser
acceptance remains open.

## Product Purpose

Project Knowledge and approved Memory already feed the transient Agent Worker
Context Packet. The Run detail page now makes that use visible to a Human
operator instead of showing only a generic Memory count.

The read-only Project Context Receipt shows:

- whether Worker knowledge retrieval is ready;
- consumed Worker tool-call count;
- Context Packet block count and hash;
- safe project Knowledge paths;
- exact approved Memory IDs used by the run;
- resulting Run Memory ID, type, review status and source reference;
- pending Memory-review count and a link to Human Memory review;
- omission proof for queries, snippets, raw content, context bodies, full
  transcripts, raw prompts, raw responses and tokens.

## Authority And Privacy Boundary

- The UI reads `GET /api/operator/evidence-report?run_id=<run_id>&limit=1`.
- The report is derived from existing MIS tool-call, Memory, Run and Audit
  evidence. It does not create or approve Memory and does not mutate the
  ledger.
- Knowledge summaries and approved Memory bodies remain transient Worker
  context. The receipt persists and displays only bounded IDs, hashes, counts,
  paths and omission booleans.
- Candidate Memory remains non-authoritative until a Human approves or rejects
  it in the Memory Library.
- A non-Worker run is labeled not applicable instead of being presented as
  proof that project context was consumed.

## Verification

```bash
python3 scripts/run_detail_evidence_ui_smoke.py
python3 scripts/worker_knowledge_evidence_consumption_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The React production build remains an exact-head CI requirement. Local package
or npm installation should not be attempted on a Host that is below the
documented storage floor.

## Installed Package Receipt

The installed `v1.6.0-private-host-preview.42` at exact commit
`9cd199b65d27718716680c5332ad842ae8228da5` contains this Run-detail panel.
Persistent Hermes Run `run_gw_903c688ae46b` and OpenClaw Run
`run_gw_f8e666405437` each consumed eight governed context blocks and three
approved Memory IDs. Their omission gates and context-aware Evaluations passed.

The Human-only aggregate evidence-report endpoint correctly rejects machine
CLI access. Final physical browser readback from the second Mac is tracked as a
separate acceptance gate; it must not be replaced by machine authentication.
