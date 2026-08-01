# Private Host Preview 42 Candidate Acceptance

Date: 2026-07-23

Status: exact local candidate installed and real Hermes/OpenClaw acceptance
passed; physical second-Mac browser acceptance remains open.

## Candidate identity

- Version: `1.6.0-private-host-preview.42`
- Exact packaged source commit:
  `9cd199b65d27718716680c5332ad842ae8228da5`
- Push CI: `30021569634`, passed at the exact commit
- Pull-request CI: `30021573934`, passed at the exact commit
- Source worktree: clean before both candidate builds
- Packaged files: 98
- Build output: private temporary directories only; no release asset is
  committed or published by this acceptance pass

The local candidate checksums were:

| Asset | SHA-256 |
| --- | --- |
| Provenance | `9d48b935acfcecf5588f3b5f67546184ef7d0b186823a3571846ac440fe38f7f` |
| Tar archive | `a3044159310cd2ec1b7276a8054b928dd8ef507059a3b43710bb43f4853908e1` |
| Zip archive | `93f522349990e1f7a7a50faf203cdd0fb8ebce74c79c426645e50f7e1c6a3f06` |
| Bootstrap installer | `75854f364502722eb24d5a7df3c0fc26685bf25acae6d5926e4c6396d16bd812` |

## Why this candidate exists

Preview.41 exposed a real SQLite connection-lifecycle failure under sustained
API polling. Python transaction contexts committed or rolled back but did not
close each connection, so the installed Host eventually exhausted file
descriptors. Current source adds a server-owned `db_session()` lifecycle,
retains direct `db()` for explicitly managed scripts, and covers normal and
exceptional close semantics.

An isolated non-default-port Worker test also found that API-launched Worker
daemons still targeted the historical port `8787`. Current source now gives
priority to a trusted configured base URL and otherwise binds local
start/restart to the canonical request origin. The fix preserves the target
through restart.

Preview.42 also packages the bounded Worker Session release and Run-detail
Project Context Receipt that were committed after preview.41.

## Local package gates

The following gates passed against exact source commit `9cd199b`:

- Python compile, SQLite lifecycle, pragma, reliability and concurrency checks;
- Worker daemon resilience twice on isolated port `18937`, including restart;
- Worker process identity and Human browser-auth smokes;
- Private Host lifecycle, doctor, bundle and release-consumer smokes;
- production UI build;
- secret scan and `git diff --check`;
- clean-machine release-candidate acceptance;
- two candidate builds with byte-identical output.

The production UI build also reported three dependency audit findings, two
high and one critical, plus a large Vite chunk warning. They were not changed
inside this reliability slice and remain explicit preview limitations.

## Real Host upgrade receipt

Before installation, the preview.41 Host and both independently managed Worker
LaunchAgents were explicitly unloaded through confirmed service-control
operations. Readback confirmed that the Host, Hermes Worker and OpenClaw
Worker services were absent.

The preview.42 candidate installer then:

- passed the normal storage preflight with more than 2 GiB available;
- created pre-update backup
  `/Users/wuji/.agentops/host/backups/agentops-mis-20260723T155141441257Z.sqlite`;
- preserved the authority ledger and Owner setup;
- set current version to preview.42 and previous version to preview.41;
- installed the managed production UI;
- reported the exact packaged commit through `agentops host version`.

The backup passed manifest, hash, size, SQLite integrity, schema and
foreign-key verification without printing raw rows. The Host service returned
health `ready`; Human login remained ready. The existing private Tailscale
Serve route returned ready with one matching backend and Funnel disabled.
Relay remained unconfigured and disabled.

Both independent Worker LaunchAgents were then restored. Fleet readback showed
two active service Workers, two execution-capacity Workers, zero pending tasks,
zero stuck tasks and zero stuck workflow jobs. Hermes and OpenClaw were both
idle after acceptance.

## Installed SQLite lifecycle gate

The installed preview.42 Host process began with 35 open file descriptors and
zero idle SQLite handles. A bounded 20-client load completed 2,000 concurrent
requests against the Human-auth status endpoint. Afterwards:

- process file descriptors remained `35 -> 35`;
- idle SQLite handles remained `0 -> 0`;
- Host health remained `ready`.

This directly closes the preview.41 failure mode on the exact installed
preview.42 package. It is not inferred from source-only tests.

## Real Runtime receipt

Two fixed, low-risk, no-external-write tasks were assigned to the persistent
Worker services. Both services pulled, claimed, planned and executed through
the installed Agent Gateway:

| Adapter | Task | Run | Result |
| --- | --- | --- | --- |
| Hermes | `tsk_preview42_hermes_acceptance_20260723T1553Z` | `run_gw_903c688ae46b` | completed |
| OpenClaw | `tsk_preview42_openclaw_acceptance_20260723T1553Z` | `run_gw_f8e666405437` | completed |

Each Run has one Tool Call, one passing Evaluation with score 1.0, one
Artifact, one reviewable Memory candidate, one verified plan-evidence
manifest, eight Runtime Events and eight Audit rows. No Approval was required
for these bounded read-only tasks.

The installed ledger also proves that each Worker consumed a governed Context
Packet before adapter execution:

- context contract `v1`;
- eight bounded context blocks;
- three approved seed Memory IDs;
- safe project Knowledge paths;
- distinct Context Packet hashes;
- query, snippet, source body, context body, transcript, raw prompt, raw
  response and credential omission gates all true.

The Run-detail Project Context Receipt source smoke passed 47 UI/API contract
markers. The aggregate evidence-report endpoint correctly rejected a machine
CLI with `human_auth_required`; a logged-in Human browser is the authority for
that read model. No credential was retrieved or bypassed to manufacture a
browser receipt.

No raw prompt, response, model body, credential, private message, transcript,
Worker log or database row is retained in this record. Candidate Memory remains
non-authoritative until Human review.

## Remaining promotion gates

This acceptance does not publish preview.42 and does not close the long-term
Remote Console goal. The next promotion gate is a physical second-Mac,
browser-only acceptance against the installed preview.42 Host:

1. sign in through the private Console;
2. open both new Run pages and inspect the Project Context Receipt;
3. verify Run, Tool, Evaluation, Runtime Event, Audit and Memory-review
   navigation;
4. sign out and confirm protected reads fail;
5. retain only bounded IDs, counts, hashes and pass/fail evidence.

Ordinary no-Tailscale Relay deployment and commercial multi-workspace hosting
remain separate future product gates.
