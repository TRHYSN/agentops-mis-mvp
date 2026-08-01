# Private Host Worker Machine Read Acceptance

## Product Slice

The packaged Host CLI can read Worker status, fleet, adapter readiness, and
stuck-task telemetry after Human Authentication is enabled. It does not reuse a
browser Session and does not grant Agent-bound credentials installation-wide
visibility.

## Route Contract

| Consumer | Route family | Credential |
| --- | --- | --- |
| Browser Workspace | `/api/workers/*` | Human Session |
| Packaged Host CLI | `/api/agent-gateway/host-workers/*` | Host machine credential |
| Remote Agent/Worker | scoped `/api/agent-gateway/tasks`, runs and evidence | bound enrollment/Session token |

The Host read routes are:

- `GET /api/agent-gateway/host-workers/status`
- `GET /api/agent-gateway/host-workers/fleet`
- `GET /api/agent-gateway/host-workers/adapter-readiness`
- `GET /api/agent-gateway/host-workers/stuck-tasks`

All four return `auth.host_machine_only:true`, `safety.read_only:true`,
`safety.ledger_mutated:false`, and `token_omitted:true`. Agent-bound enrollment
and Session tokens receive `403 host_machine_credential_required`, including
when they have `tasks:read`.

## Verification

```bash
python3 -m py_compile server.py agentops_mis_cli/agentops.py scripts/private_host_worker_machine_read_smoke.py
python3 scripts/private_host_worker_machine_read_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/agentops_worker_status_smoke.py
python3 scripts/agentops_worker_fleet_smoke.py
python3 scripts/run_local_stack_smoke.py
python3 scripts/worker_process_identity_smoke.py
python3 scripts/worker_console_ui_smoke.py
python3 scripts/real_runtime_ui_confirm_smoke.py
python3 scripts/release_evidence_packet_smoke.py
(cd ui/start-building-app && npm run build)
git diff --check
```

The isolated Private Host smoke proves:

- a Host machine key remains rejected by the browser `/api/workers/status`
  route with `401 human_auth_required`;
- the four new machine routes return `200` to the Host credential;
- `agentops worker status|fleet|readiness|stuck` use those routes;
- bound Agent tokens and short-lived Sessions receive the expected `403`;
- missing or incorrect Host machine credentials fail closed; the normal Private
  Host startup guard refuses to listen without `AGENTOPS_API_KEY`, and the route
  retains `503 host_machine_credential_not_configured` as defense in depth;
- ledger table counts and bound credential `last_used_at` values do not change;
- no fixture credential appears in response, CLI, server, or smoke output;
- no real Hermes/OpenClaw execution occurs.

## Known Limits

- This surface is intentionally Host-wide and is not a remote Agent fleet API.
- Worker logs and all Worker write/recovery commands remain on their existing
  Human/Admin control paths.
- `agentops doctor` and loop-driver scoped readiness keep their existing
  contracts; they are not silently upgraded to Host-wide visibility here.

## Host Stack Process Normalization

The Private Host starts Hermes/OpenClaw Workers as children of the managed
local stack. Those children now publish bounded process identity in their local
state (`pid`, adapter, agent id, management mode, poll interval and explicit
confirmation state). Credentials, prompts, responses and log content remain
omitted. Worker status and Fleet read that state when daemon-API metadata is not
present, so a live Host child is no longer shown as a stopped daemon.

`management_mode:host_stack` is also a control boundary. The browser console
shows the process as running but disables daemon start/restart/stop controls for
that adapter. The backend independently returns `409 worker_managed_by_host`
for restart or stop instead of killing a child and causing the Host supervisor
to tear down the full stack. The operator uses the Host lifecycle for those
processes; daemon-API and standalone Worker behavior remain unchanged.

The isolated `run_local_stack_smoke.py` starts a real mock Worker process and
proves all of the following without a real model call:

- Worker status exposes a live PID with `management_mode:host_stack`;
- Fleet reports one running local daemon and one Host-managed Worker;
- the process and its matching Agent row count as one running Worker;
- stop and restart through the Worker endpoints fail closed with `409`;
- the Host stack remains alive after both rejected child-control requests;
- no user CLI config or credential material is written or printed.

This normalization is installed in `v1.6.0-private-host-preview.23` from exact
commit `24d0791bbd1848c275e72b9c7baafcb6c28be1ce`. The real Host reports one
Hermes plus one OpenClaw child as `host_stack`, two running local daemons, two
Host-managed Workers, zero API-managed daemons and `running_workers:2`.

## Process Identity Hardening

preview.24 binds every new Worker state record to a
non-reversible process identity made from the OS process start record, command
record and process group. Only the SHA-256 digest and process-group number are
stored; the command, environment, prompt, response and credentials are not.

Status recomputes that identity before accepting a state-file PID as a running
Worker. A stale, replaced or tampered claim becomes `identity_unverified`,
`running:false`, and `control_allowed:false`. The backend refuses start, stop
and restart against the live unverified PID. A Host ownership claim remains
authoritative even when its identity is unverified, so the console cannot kill
the child or start a duplicate outside the Host lifecycle.

`worker_process_identity_smoke.py` starts a temporary local stack with a real
mock Worker process, verifies its initial identity, tampers only the temporary
state hash, and proves that status fails closed, stop/restart return `409`, and
both the Worker and Host survive. It also proves that `running_workers` drops to
zero and Fleet reports one warning-level unverified claim instead of counting
the stale Agent row as capacity. `run_local_stack_smoke.py` now also requires
`process_identity_verified:true` in its normal path, and CI runs both smokes.

The existing React Worker Console and AI Employees pages consume this state;
they disable unsafe controls and show a bilingual process-verification badge.
No alternate frontend or new visual shell was introduced. This hardening is
published and installed in `v1.6.0-private-host-preview.24` from exact commit
`d52415f7d838c584faa61204fe27fafb4c622324`.

## Current Result

Validated locally on 2026-07-14:

- Private Host machine-read smoke: pass, including four CLI commands, Human
  Session isolation, Agent token/Session `403`, missing-key startup failure,
  unchanged ledger counts, and unchanged rejected-credential usage timestamps.
- Agent Gateway Session, scoped-read, and CLI doctor regressions: pass on an
  isolated SQLite database and loopback server.
- Existing Worker status/fleet, Human Auth, account Workspace UI, bundle,
  release-consumer, release-evidence, Python compile, secret scan, and diff
  checks: pass.
- Draft/public asset equality, two no-repository clean-HOME installs and the
  real preview.23-to-preview.24 Host upgrade: pass.
- Installed Hermes/OpenClaw process visibility, ownership and deduplicated
  capacity readback: pass; both adapters are ready and sleeping for work.
- Process-identity tamper/PID-reuse guard: isolated real-process smoke,
  local-stack smoke, Worker Console static contract and production UI build
  pass; exact-head CI, public Release, clean installation and real Host upgrade
  validation also pass. The real Host reports two verified identities and zero
  unverified process claims.
- API-managed daemon regression: pass on an isolated server with explicit
  loopback base URL; the daemon processed one mock task through plan, claim,
  run, runtime event, tool call, evaluation, artifact, memory proposal, audit
  and evidence-manifest writes, then stopped normally. No Hermes/OpenClaw model
  was invoked by this regression.
- Live Hermes/OpenClaw execution: not called by this read-only slice.
