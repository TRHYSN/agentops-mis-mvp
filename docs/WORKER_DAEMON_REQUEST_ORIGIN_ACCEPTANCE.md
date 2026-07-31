# Worker Daemon Request-Origin Acceptance

Status: source fix implemented; exact-head CI and packaged Host verification
remain release gates.

## Failure Found By Isolated Dogfood

An isolated AgentOps MIS server listened on `127.0.0.1:18937` without an
`AGENTOPS_BASE_URL` override. `POST /api/workers/local/start` launched the Mock
Worker with `--base-url http://127.0.0.1:8787`, so the Worker polled a different
server and the assigned task did not complete before the bounded timeout.

This was a real product defect for source servers using `--port` without a
matching environment override. It was independent of the SQLite connection
lifecycle fix.

## Fix

- A trusted `AGENTOPS_BASE_URL` remains the first choice for managed Host
  deployments.
- Without that override, start/restart uses the canonical request origin
  derived by the existing Human Session origin policy.
- User-supplied `base_url` is not used for this local process launch.
- Restart carries the same internal request-origin value into the replacement
  Worker.
- Invalid or non-canonical Host input does not become a Worker target; the
  existing loopback default remains the final local fallback.

## Acceptance

Run an isolated server on a non-default loopback port with
`AGENTOPS_BASE_URL` unset, then execute:

```bash
python3 scripts/worker_daemon_resilience_smoke.py \
  --base-url http://127.0.0.1:18937
```

Acceptance requires the Mock daemon to claim and complete its task, expose a
Run, converge to `processed >= 1`, and stop cleanly. The resulting runtime
directory and SQLite database are temporary local test artifacts and must not
be committed.

## Verified Result

The isolated acceptance passed on 2026-07-23:

```text
server: http://127.0.0.1:18937
task: tsk_worker_daemon_resilience_20260723152818
run: run_gw_5a939f4624b4
processed: 1
iterations: 2
start target: http://127.0.0.1:18937
restart target: http://127.0.0.1:18937
cleanup: Worker stopped; server stopped; temporary DB/runtime removed
```

The test used only the Mock adapter. No Hermes/OpenClaw provider call, external
write, credential, prompt, response, transcript, or committed database was
involved.
