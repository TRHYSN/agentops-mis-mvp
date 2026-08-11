# Public Claims And Limitations

This document is the release evidence boundary for public-facing claims about the
local AgentOps MIS MVP. It keeps demos, docs and release packets aligned with
tested behavior.

## Allowed Claims

AgentOps MIS may be described as:

- a local-first AI workforce/MIS control plane, also phrased as a
  local-first control plane;
- a local MVP for classroom, founder, and internal operator demonstration;
- loopback local use, controlled dogfood, and single-customer validation with
  explicit operator confirmation;
- a first-party authority ledger for tasks, runs, Agent Plans, approvals,
  prepared actions, memory candidates, evaluations, artifacts and audit logs;
- an Agent Gateway and worker loop that can dispatch mock/local work and record
  safe Hermes/OpenClaw protected-runtime evidence;
- a system that stores structured state, short summaries, hashes, stable IDs,
  review statuses and evidence counts;
- a Pixel Office operating map implemented with first-party React/CSS geometry,
  not copied third-party art assets;
- a release candidate only after exact HEAD, CI, clean-machine install/build,
  migration/rollback, license/provenance, safe-closure, and protected runtime
  evidence all pass.
- OpenCekura Reliability Lab as a local, deterministic, configured simulation,
  evaluation, regression, release-gate, and evidence workflow for supported
  Mock campaigns. Its lower-level HTTP adapter may be described as implemented
  and tested, but not as exposed by the v0 campaign CLI/evidence workflow.

## OpenCekura / Reliability Lab Boundary

OpenCekura is an AgentOps MIS vertical product with an open-source product
intent. It may be described as local agent reliability testing under checked-in
scenarios, deterministic evaluators, and explicit policies. Its Evidence
Bundles prove what those local fixtures observed; they do not prove production
reliability. The current repository license is proprietary, so the present
code must not be called open-source licensed or redistributed unless the Owner
first selects and applies an open-source license.

OpenCekura must not be described as:

- the official open-source edition of Cekura or as containing private Cekura
  code, private APIs, branding, or UI;
- a production reliability certification or a guarantee for untested agents,
  models, backends, traffic, or infrastructure;
- a completed real-time Voice, WebRTC, SIP, LiveKit, Pipecat, or telephone
  reliability product in v0;
- evidence that deterministic mock behavior generalizes to real customer
  traffic.

Any public result must name the scenario suite, agent/config version,
evaluator and gate policy versions, exact source commit, environment, and
whether the evidence came from mock fixtures, HTTP execution, or a future live
adapter.

## Required Qualifiers

Public or demo copy must keep these qualifiers when relevant:

- Current release status is local MVP / NOT_READY until the checklist is advanced
  by exact HEAD evidence.
- Hosted SaaS, billing, production multi-tenant fleet management, marketplace,
  and commercial distribution are future directions, not current release claims;
  AgentOps MIS is not yet a hosted SaaS platform.
- Hermes/OpenClaw live execution is protected/manual: preflight is read-only,
  live dispatch requires explicit confirmation, and fixed probes use prepared
  actions before any provider call.
- Dify, Notion and other external connectors are adapter or prepared-action
  paths; do not claim broad live sync or bidirectional sync unless a dedicated
  release gate proves it.
- Star-Office-UI and other pixel-office references are reference-only; current
  product visuals are first-party CSS/React and any public/commercial art pack
  needs its own license/provenance evidence.
- The product does not store raw credentials, private prompts, raw model
  responses, customer document bodies, private transcripts, local databases or
  unsafe runtime logs in release evidence.

## Disallowed Claims

Do not claim these items.

### Must Not Be Claimed Yet

- Production hosted SaaS readiness;
- Complete multi-tenant tenant isolation;
- Enterprise-grade RBAC;
- hosted/SaaS, billing, production multi-tenant, or commercial readiness;
- reliable unattended production Hermes/OpenClaw fleet management;
- Dify live dataset sync, Dify live sync, Notion bidirectional sync, or external
  provider writes without the matching prepared-action/release gate;
- universal runtime per-action governance;
- exact tool-action resume from a generic approval;
- Star-Office or other third-party pixel art is owned by AgentOps MIS or safe for
  commercial product use;
- raw prompts, raw model outputs, credentials, customer bodies, local SQLite
  databases, or private transcripts are included in public release evidence.

## Verification

Release Packet Rule: if a public claim is not covered by a release-packet gate,
it must be marked future, protected/manual, local-only, or excluded.

Run:

```bash
python3 scripts/public_claims_release_gate_smoke.py
```

This gate complements `scripts/license_provenance_smoke.py`,
`scripts/customer_delivery_boundary_smoke.py`,
`scripts/protected_live_runtime_ids_smoke.py`, and the secret scan. It does not
replace final legal, security, or production-readiness review.
