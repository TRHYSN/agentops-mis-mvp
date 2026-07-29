# Worker Session Graceful Release Acceptance

Status: source implementation and deterministic/server-backed verification
complete; installed preview.41 predates this lifecycle correction.

## Product Problem

A bounded `agentops-worker --once` run used the same execution scopes as a
persistent Worker service. After the process exited, its short-lived Session
remained active until TTL expiry, so Fleet correctly observed a stale heartbeat
but incorrectly presented the finished one-shot Agent as a broken service.

## Contract

- A normally exiting Worker publishes a final `disabled` heartbeat.
- When the Worker minted a short-lived Session, it calls
  `POST /api/agent-gateway/session/revoke-self` as its final network action.
- Only the authenticated current Session may revoke itself. Enrollment tokens,
  global keys and anonymous clients cannot select or revoke another Session
  through this route.
- The response exposes only a stable Session reference and omission flags. Raw
  Session IDs and tokens are not returned.
- A revoked Session immediately loses Agent Gateway access.
- Fleet excludes a Session whose Session-bound heartbeat is `disabled`, even if
  a response-loss case leaves that Session active. Another healthy concurrent
  Session for the same Agent remains visible and retains execution capacity.
- Failed Workers preserve the `error` heartbeat; graceful shutdown does not
  overwrite a failure with `disabled`.

## Verification

```bash
python3 -m py_compile server.py agentops_mis_cli/worker.py \
  agentops_mis_core/worker_fleet.py \
  scripts/agent_gateway_session_smoke.py \
  scripts/worker_session_refresh_smoke.py \
  scripts/worker_service_heartbeat_cadence_smoke.py
python3 scripts/worker_service_heartbeat_cadence_smoke.py
python3 scripts/agent_gateway_session_smoke.py --base-url http://127.0.0.1:8787
python3 scripts/worker_session_refresh_smoke.py --base-url http://127.0.0.1:8787
```

The last two checks require the local test server used by the backend CI suite.
They create only bounded fixture Agents, tasks and Sessions in that test
ledger. They do not call Hermes/OpenClaw or inspect credentials.

Local acceptance on 2026-07-23 passed with temporary isolated servers and
SQLite ledgers that were stopped and deleted after verification:

- Agent Gateway Session self-revoke and post-revoke rejection
- two-task Worker Session refresh and graceful final release
- Worker heartbeat cadence, Fleet selection and concurrent Session handling
- blocked-intake heartbeat/Fleet behavior and workspace isolation
- remote Fleet aging and Fleet hygiene
- repository-local Worker package/CLI installation smoke
- Python compilation, module-boundary smoke, secret scan and diff check

This lifecycle slice did not invoke a model runtime. It preserves the separate
real OpenClaw/Hermes governed-memory dogfood evidence already recorded for the
preceding exact commit; it does not relabel deterministic fixture execution as
a new live-model acceptance.

## Release Boundary

The installed preview.41 Host still relies on Session TTL expiry after a
one-shot run. Package and install a later exact commit before attributing
graceful Session release to the installed product.
