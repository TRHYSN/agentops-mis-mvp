# AgentOps MIS Current Context Snapshot

> Status date: 2026-08-11
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Local checkout: Windows fresh clone under `F:\文档\MIS`
> Development line: `feat/open-cekura-windows-v0`
> Starting main: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
> Locally accepted implementation: `eb6855b0a51596e7bd79915d6a34cd4ad17b47e7`
> Snapshot commit: derive from `git rev-parse HEAD`
> Review surface: Issue `#123`; PR pending at this document revision
> Release status: local acceptance complete; remote CI and Owner review pending

This is the current compact continuation point. It supersedes operational
branch, commit, CI, and next-action fields in the July 2026 Private Host
snapshot. That history remains available in Git and is not a statement about
the current OpenCekura delivery line.

## Current product line

OpenCekura is an AgentOps MIS vertical product named **Reliability Lab**. It
closes the configured deterministic loop from YAML Scenario through simulation,
ToolCall observation, explainable evaluation, FailureCase, RegressionCase,
release gate, content-addressed Evidence, API, and evidence-first UI.

Reliability Lab reuses the existing MIS Task, verified Agent Plan, Run,
ToolCall, Evaluation, Artifact, Memory, Approval, Audit, auth, workspace,
SQLite, `/mis-api`, and Vite authorities. It does not create a second control
plane or frontend.

## Accepted local evidence

- Ten appointment scenarios produce a natural baseline BLOCK and candidate
  PASS through eight deterministic evaluators and `release_gate.v1`.
- Persistent campaigns on exact implementation SHA `eb6855b0...` contain 20
  Runs, 56 ToolCalls, 160 Evaluations, 20 Evidence manifests, 5 generated and
  replayable regressions, and 3 release-gate decisions.
- Baseline Task/Plan: `tskoc_01a16cde18e2c0169a8bcd6e` /
  `planoc_5165df2ba6f1f9cdf6cabbef`.
- Candidate Task/Plan: `tskoc_30fa0932ba764d410f47781a` /
  `planoc_fb1bb4380cd9ef03e9734252`.
- Local Windows doctor, the 317-test OpenCekura suite, the standalone 20-test
  historical Research Lab suite, UI build/source smoke, API readback, evidence
  tamper-negative, and real Chrome DOM acceptance pass.
- The exact measured record, campaign IDs, gate/Approval IDs, regression/Memory
  mappings, test counts, and limitations are in
  [`../open-cekura/HANDOFF.md`](../open-cekura/HANDOFF.md).

No credential value, private prompt, raw model response, or unredacted customer
payload was copied into this snapshot or release evidence.

## Authority model

```text
Git/GitHub -> code, branch, commit, PR, diff, CI
AgentOps MIS -> Task, Plan, Run, ToolCall, Evaluation, Artifact,
                Memory, Approval, Audit
Versioned docs + reviewed Notion -> approved product contracts and handoff
```

The local filesystem EvidenceManifest is an unsigned integrity check. MIS
Artifact/Approval/Audit rows are the separate authority ledger; neither is
described as production certification.

## Open gate and next action

Push the branch, create the PR to `main`, and record the exact GitHub Actions
run URL/ID, head SHA, and all Ubuntu/Windows x Python 3.10/3.11 conclusions.
Fix any failure and rerun exact-head acceptance. Leave merge to the Owner.

Voice/WebRTC/SIP/LiveKit/Pipecat and real-world telephone campaigns remain v0.2
scope and must not block the v0 release line.

## Project Delta

```yaml
type: ContextSnapshot
title: OpenCekura Windows v0 local acceptance and remote delivery gate
status: ActiveDevelopment
priority: P0
module: Reliability Lab
repository: geogejoy107-jpg/agentops-mis-mvp
branch: feat/open-cekura-windows-v0
implementation_commit: eb6855b0a51596e7bd79915d6a34cd4ad17b47e7
issue: 123
evidence: docs/open-cekura/HANDOFF.md and governed MIS campaign records
next_action: push, open PR, wait for exact-head remote CI, then hand Owner review
```
