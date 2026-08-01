# Private Host Preview 39 Candidate Acceptance

Date: 2026-07-22

Status: public prerelease, real-Host upgrade, sustained Worker and real Runtime gates passed; physical Human gates remain open

## Candidate identity

- Version: `1.6.0-private-host-preview.39`
- Exact source commit: `17801e3bbb20cdaec68e72f3e225ab5492d8f8e2`
- Push CI: `29927328480`, passed at the exact commit
- Pull-request CI: `29927329540`, passed at the exact commit
- Source worktree: clean before both candidate builds
- Build output: private temporary directories only; no artifact is committed

## Why this candidate exists

Before preview.39 promotion, the installed real Host remained preview.38 while
the persistent Hermes and OpenClaw Workers ran the current source. Both
launchd processes stayed alive and iterated without Worker errors, but
preview.38 predates the scoped Session heartbeat observation schema used by the
current Fleet read model. The result was a real version mismatch: Runtime
execution worked, while Fleet eventually projected both services as stale.

Current source has the additive
`agent_gateway_session_heartbeat_observations` schema and the exact-Session,
workspace-scoped Fleet projection. The isolated cadence and server-backed
heartbeat smokes passed with no periodic stale window. This candidate packages
that already-tested correction; it does not replace the correction with an
Agent-status or unscoped Runtime-event fallback.

## Local release gates

The following gates passed against the exact candidate source:

- production Vite build;
- secret scan, license provenance and public-claims gate;
- Host backup/restore and Owner acceptance-receipt smokes;
- Private Host bundle smoke, including two-version upgrade and rollback;
- no-repository release-consumer install/start/status/stop;
- worker Session heartbeat cadence and server-backed Fleet integration;
- two independent `preview.39` asset builds with byte-identical directories.

The exact candidate contains 97 packaged files and five release assets. It is
an unsigned macOS developer preview; Hermes, OpenClaw and Tailscale are not
installed automatically.

## Published prerelease receipt

The exact tag and five assets are public at
`https://github.com/geogejoy107-jpg/agentops-mis-mvp/releases/tag/v1.6.0-private-host-preview.39`.
The tag resolves to the exact source commit above. Candidate, repeat-candidate,
Draft-download and public-download files were byte-identical. The published
checksums are:

| Asset | SHA-256 |
| --- | --- |
| Provenance | `10ce325f46b232be18923b838176921bfb2acff32ef04d5f56fa5d489ffa708f` |
| Checksum manifest | `4df97caf263b2db7c90507f91268373ba59050b7621e48cf0735bf6fc1a4bead` |
| Tar archive | `dec94e888004cb694a571b11e155311501a2458726aabaa48d34ab9371b4d9ef` |
| Zip archive | `ea4692c7918f85e0e51087cc23e201141cf465e6a2f501fc2eceb3a0e7bc04c6` |
| Bootstrap installer | `75854f364502722eb24d5a7df3c0fc26685bf25acae5926e4c6396d16bd812` |

A separate temporary HOME then installed from the public GitHub release, with
no repository or test release directory available. It started a healthy Host
on an isolated loopback port, reported version preview.39 and the exact source
commit, and stopped cleanly. This closes publication and public-network
consumer gates only; it does not claim that the real authority Host was
upgraded or that a Runtime executed.

## Exact isolated upgrade receipt

The public preview.38 assets were downloaded from the existing GitHub release
and installed into an isolated HOME on a separate loopback port. Before the
upgrade, the temporary ledger had these bounded counts:

| Table | Rows |
| --- | ---: |
| agents | 5 |
| tasks | 10 |
| runs | 30 |
| memories | 10 |
| evaluations | 12 |
| audit_logs | 57 |

Preview.38 did not contain the scoped Session heartbeat table. With the Host
stopped, the preview.39 candidate installer then:

- passed the real 2 GiB storage floor without a test capacity override;
- created and verified a pre-update SQLite backup;
- preserved the six bounded table counts exactly;
- set `current=preview.39` and `previous=preview.38`;
- started a healthy loopback-only Host;
- added the scoped Session heartbeat table;
- reported the exact candidate commit through `agentops host version`.

The pre-update backup passed hash, manifest, SQLite integrity, schema and
foreign-key verification. The candidate was then stopped. Rollback first
returned the required dry-run refusal, exact confirmation created a verified
pre-rollback backup, switched back to preview.38, and restarted a healthy Host
at the exact preview.38 commit. The temporary Host was stopped after readback.
No real Host, Runtime service, credential, Tailscale rule or authority ledger
was changed by this exercise.

## Real Host upgrade receipt

The existing preview.38 Host and the independently managed Hermes/OpenClaw
Worker LaunchAgents were explicitly unloaded before maintenance. Because the
Host LaunchAgent has `KeepAlive=true`, stopping only its child process caused a
correct relaunch; the operator then used the product `host service-control`
boundary to unload the service itself and independently confirmed that all
three services were absent before installation.

The public bootstrap asset installed preview.39 into the normal Host home and
started the loopback Host. One transient GitHub release-CDN TLS error was
retried inside the bounded download flow; the final verified install returned
success. The real storage preflight reported approximately 2.84 GB free,
required approximately 2.37 GB including planned writes, and used no test
capacity override. The installer:

- created pre-update backup
  `agentops-mis-20260722T144504908254Z.sqlite`;
- preserved the authority ledger and existing Owner state;
- set `current=preview.39` and `previous=preview.38`;
- reported exact commit `17801e3bbb20cdaec68e72f3e225ab5492d8f8e2`;
- served the managed production UI from the preview.39 package;
- added `agent_gateway_session_heartbeat_observations` to the real schema.

The backup passed manifest, hash, size, SQLite integrity, schema and
foreign-key checks in read-only verification. No raw rows were printed. The
Human login boundary remained ready. The existing private Tailscale Serve
profile still had one exclusive backend on HTTPS port 8443, matched the Host
target and kept Funnel disabled; the private DNS name and origin are omitted
from this record.

The one-shot installer process was then stopped and the existing Host-only
LaunchAgent was loaded again. It converged in one read and returned health
`ready`. Both independent Worker LaunchAgents loaded with active processes.
The first bounded Fleet read reported `overall=ready`, two execution-capacity
Workers, zero stale service Workers, and one fresh execution-ready Session for
each of Hermes and OpenClaw. Four bounded samples spanning 105 seconds then
remained `ready` with two execution-capacity Workers, zero stale Workers and
zero unavailable Workers. Both selected Sessions remained `fresh_ready` with
execution scope present. This closes the exact failure window observed on
preview.38 without invoking either model; new model tasks remain a separate
gate below.

## Persistent Worker Runtime receipt

Two normal MIS tasks were created through `agentops task create` and assigned
to the independently managed service identities. No one-shot Worker command
was used. Both services pulled, claimed, planned and executed their assigned
task through the Agent Gateway:

| Adapter | Task | Run | Result |
| --- | --- | --- | --- |
| Hermes | `tsk_preview39_hermes_retention_policy_20260722T1450Z` | `run_gw_0dfd8981a340` | completed |
| OpenClaw | `tsk_preview39_openclaw_retention_review_20260722T1450Z` | `run_gw_a46813718b06` | completed |

The useful customer task was a metadata-only review of local AI-session
retention boundaries after the storage incident. It supplied aggregate sizes,
prohibited reading session bodies, deletion and external writes, and required
bounded advice. Each run produced one Tool Call, one passing Evaluation with
score 1.0, one Artifact, one reviewable Memory candidate, eight Runtime Events
and four Task-entity Audit rows. Each Agent Plan passed with quality score 100,
and each plan-evidence manifest verified one Tool Call, Evaluation, Artifact
and Audit reference with no failed check. Both tasks had zero Approval and zero
Prepared Action rows, as expected for low-risk read-only work.

No raw prompt, response, model body, credential, private message, transcript,
Worker log or database row is retained in this receipt.

## Authenticated Human Session receipt

The installed preview.39 Host subsequently passed the bounded Owner Human
Session service-control-preview receipt/readback gate. A preview-only pass made
zero operator-ledger writes. An explicit `--confirm-record` pass then appended
exactly one Action Receipt and one Control Readback for each of Hermes and
OpenClaw after local read-only launchd service checks. Both services were
present and loaded, the recorded actors matched the authenticated Owner
context, logout succeeded and a subsequent protected read returned HTTP 401.
No service-control or Runtime execution occurred. Exact bounded IDs and hashes
are recorded in `PRIVATE_HOST_HUMAN_SERVICE_RECEIPT_ACCEPTANCE.md`.

The source branch also adds fail-closed Human actor binding, fresh ready service
identity precedence and unrelated-CLI-Agent rejection for fast service closure.
Those corrections are covered by isolated regressions but are not claimed as
installed preview.39 behavior; they require the next exact-package install.

## Remaining promotion gate

This document does not yet claim physical-client current-package acceptance.
Promotion still requires repeating the browser acceptance from the physical
MacBook against the current package.

Raw prompts, responses, credentials, private messages, transcripts, Worker
logs and database rows are outside this acceptance record.
