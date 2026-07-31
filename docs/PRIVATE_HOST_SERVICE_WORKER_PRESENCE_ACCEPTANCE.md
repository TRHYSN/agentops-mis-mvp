# Private Host Service Worker Presence Acceptance

## Scope

This slice closes a read-model gap for independently service-managed AgentOps
Workers. A Worker may be running under launchd or systemd, use a short-lived
Agent Gateway session, and heartbeat correctly without being a child process of
the AgentOps Host. The existing console previously reported zero running
Workers in that state.

No task was dispatched and no Runtime/model execution was performed by this
slice.

## Product Contract

- `running_workers` remains the count of directly observed local daemon or
  running Agent process references. Existing process semantics are preserved.
- `active_service_workers` counts distinct Agent Gateway session holders with a
  fresh `agent.heartbeat` ledger event.
- `execution_capacity_workers` is the de-duplicated union of directly observed
  running Workers and fresh service Workers.
- A heartbeat-confirmed service Worker is shown as `external_service`; it does
  not claim that the Host inspected launchd/systemd or verified a PID.
- The server remains read-only with respect to OS service control. It does not
  execute `launchctl`, `systemctl`, or shell commands while building status.
- Raw session IDs, credentials, prompts, and responses are omitted.

The service heartbeat freshness window is 90 seconds. The default Worker loop
heartbeats during task polling and has a 30-second maximum idle backoff, so this
allows three maximum idle intervals before reporting a stale service presence.

## UI Integration

The existing React Worker Console now uses `execution_capacity_workers` for its
primary capacity metric and `active_service_workers` for the service Worker
metric. Fresh service Workers render in the existing daemon/control area with:

- Agent name and safe Agent ID;
- adapter;
- heartbeat state;
- active session count; and
- a service Worker badge.

This is not a separate frontend and does not add a new navigation surface.

## Verification

Run from the repository root:

```bash
python3 -m py_compile server.py agentops_mis_core/worker_fleet.py \
  scripts/module_boundary_smoke.py scripts/worker_console_ui_smoke.py
python3 scripts/module_boundary_smoke.py
python3 scripts/private_host_service_worker_presence_smoke.py
python3 scripts/worker_console_ui_smoke.py
python3 scripts/agentops_worker_status_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

The deterministic module fixture covers a service-only Worker with:

- no local daemon claim;
- one active short-lived Agent Gateway session;
- one fresh heartbeat;
- `running_workers = 0`;
- `active_service_workers = 1`; and
- `execution_capacity_workers = 1`.

The same isolated integration smoke is wired into the GitHub Actions Backend
deterministic lane and the canonical release-evidence command list.

## Real Host Readback

After installing a package containing this slice, the real Host acceptance is:

```bash
agentops worker status | jq '{
  running_workers,
  active_service_workers,
  execution_capacity_workers,
  service_workers
}'

agentops worker fleet | jq '{
  summary,
  service_lanes: [.lanes[] | select(.lane_type == "gateway_service_worker")]
}'
```

For the current two-service Hermes/OpenClaw Host, expected readback is two fresh
service Workers and two execution-capacity paths while `running_workers` may
remain zero because those processes are not Host child daemons.

The 2026-07-14 readback used a temporary SQLite backup and the source read model;
it did not query task content or execute a Runtime. It returned:

```text
status=running
running_workers=0
active_service_workers=2
execution_capacity_workers=2
Hermes heartbeat=fresh session=active process_state_verified=false
OpenClaw heartbeat=fresh session=active process_state_verified=false
```

## Known Limitation

Heartbeat evidence proves recent Agent Gateway activity, not operating-system
service ownership. OS-level loaded/running proof remains the responsibility of
the local `agentops-worker service-check` command and cannot be inferred by the
browser server without weakening the no-server-shell boundary.
