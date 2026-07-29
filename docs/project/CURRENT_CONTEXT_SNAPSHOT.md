# AgentOps MIS Current Context Snapshot

> Status date: 2026-07-23
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Local checkout: `agentops-mis-commercial-handoff-status-mainline`
> Development line: `codex/local-host-remote-console`
> Implementation baseline summarized: `9cd199b65d27718716680c5332ad842ae8228da5`
> Snapshot commit: derive from `git rev-parse HEAD`
> Review surface: Draft PR `#104`
> Release status: active source development; not a release claim

This snapshot is the current compact continuation point. It supersedes the
operational branch, commit, CI, and next-action fields in the older
`PROJECT_STATE.md` and `HANDOFF.md`; those files remain historical governance
baselines until they are fully reconciled.

## Product Position

AgentOps MIS is a local-first control plane that lets humans assign, supervise,
approve, evaluate, and retain evidence for work executed by AI runtimes on the
same machine or through governed remote Workers.

## Authority And Memory Model

```text
Versioned spec / approved decision
-> workspace Knowledge Index + approved Memory
-> Agent Plan
-> Task / Run / Tool Call
-> Approval / Artifact / Evaluation
-> Memory Candidate / Audit
```

- Git and GitHub own code, branch, commit, PR, diff, and CI facts.
- AgentOps MIS owns runtime, approval, evidence, evaluation, and audit facts.
- Versioned project documents and reviewed Notion entries own approved project
  decisions and handoffs.
- Current tasks may use bounded workspace Knowledge plus approved Memory.
- Old Codex conversations are non-authoritative source material. They are not a
  substitute for project context and are eligible for expiry after their
  durable Project Delta has been captured.

No credential, private message, transcript, raw prompt, or raw response was
read or copied to produce this snapshot.

## Verified Committed Source

The current committed line contains these latest product slices:

- `0057efa` and `75857b4`: bounded workspace-scoped context packets from
  versioned Knowledge summaries plus approved Memory, with hash/ID/count
  receipts instead of source bodies.
- `0adbc9f`: workspace-authoritative governed Memory behavior.
- `136779e`: bounded Worker Sessions release their resources cleanly.
- `706af7d`: Run Detail exposes a Project Context Receipt so a human can verify
  which governed context was used without exposing its body.
- `9cd199b`: server-owned SQLite connections close on both success and failure,
  and API-launched local Worker daemons bind to the trusted request origin
  through start and restart.

Real, explicitly confirmed local dogfood produced:

- OpenClaw Run `run_gw_ba8013a9aa17`;
- Hermes Run `run_gw_5387a296a361`;
- eight governed context blocks per Run, including three approved seed Memory
  references;
- compact Run, Tool, Evaluation, Runtime Event, Audit, artifact, approval, and
  candidate-memory evidence with raw input/output omitted.

Hermes candidate `mem_gw_80af5cdbee359296` remains pending Human review. Agent
output therefore has not silently become canonical project knowledge.

## Installed Host Versus Source

The installed Private Host is preview.42 at exact commit
`9cd199b65d27718716680c5332ad842ae8228da5`, matching the current committed
source package. Its verified pre-update backup preserved the preview.41
authority ledger and Owner state.

Installed-package load acceptance completed 2,000 concurrent Human-auth status
requests with 20 clients. Process file descriptors remained `35 -> 35`, idle
SQLite handles remained `0 -> 0`, and Host health stayed ready.

Persistent installed Workers then completed:

- Hermes task `tsk_preview42_hermes_acceptance_20260723T1553Z`, Run
  `run_gw_903c688ae46b`;
- OpenClaw task `tsk_preview42_openclaw_acceptance_20260723T1553Z`, Run
  `run_gw_f8e666405437`.

Each Run has one Tool Call, one passing Evaluation, one Artifact, one candidate
Memory, one verified plan-evidence manifest, eight Runtime Events and eight
Audit rows. Each consumed eight governed context blocks and three approved
Memory IDs with all raw-input/output omission gates true.

## Current Uncommitted Work

The implementation worktree is clean before this evidence-only documentation
update. Candidate archives, extracted staging trees, UI `node_modules` and
`dist` are temporary local build outputs and are not tracked.

## CI Truth

- Exact `9cd199b` push run `30021569634` passed.
- Exact `9cd199b` pull-request run `30021573934` passed.
- The preview.42 candidate was built from that exact clean commit and installed
  locally.
- A later documentation-only evidence commit does not change the packaged
  source identity; it still requires its own lightweight checks and exact CI.

## Storage And Session Retention

- Docker data is outside this cleanup and must not be deleted automatically.
- The current Codex task
  `019ec0ec-14bf-7243-a145-8b5659f0193a` is retained for continuation.
- Selected completed session files may be deleted only after this snapshot and
  the retention acceptance record exist and pass a no-secret/diff check.
- Session cleanup uses file metadata only; raw JSONL bodies are not inspected.
- Deleting an expired session removes chat-resume capability for that session,
  not Git code, approved Memory, MIS ledger evidence, or this snapshot.

See [`CODEX_SESSION_RETENTION_ACCEPTANCE.md`](./CODEX_SESSION_RETENTION_ACCEPTANCE.md).

## Open Gates

1. Complete physical browser-only preview.42 acceptance from the second Mac.
2. Confirm the two new Run pages expose the Human Project Context Receipt and
   Human review links, then sign out and confirm protected reads fail.
3. Decide whether preview.42 should remain a local candidate or become a
   published prerelease after physical acceptance.
4. Keep ordinary no-Tailscale Relay and commercial multi-workspace deployment
   as separate product tracks.

## Next Single Action

Commit and push the preview.42 evidence-only documentation update, remove
temporary local build output, then perform physical second-Mac browser
acceptance without installing AgentOps on the client.

## Project Delta

```yaml
type: ContextSnapshot
title: Current local Host, governed Memory, CI, and reliability truth
status: ActiveDevelopment
priority: P0
module: Project Governance
repository: geogejoy107-jpg/agentops-mis-mvp
branch: codex/local-host-remote-console
commit: 9cd199b65d27718716680c5332ad842ae8228da5
updates: PROJECT_STATE.md and HANDOFF.md operational fields
evidence: Git history, exact CI run identities, bounded MIS runtime receipts, and local verification
next_action: commit and push preview.42 evidence, then perform physical second-Mac browser acceptance
```
