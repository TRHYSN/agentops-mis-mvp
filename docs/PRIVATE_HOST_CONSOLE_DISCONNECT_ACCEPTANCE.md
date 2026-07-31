# Private Host Console Disconnect Acceptance

## Scope

This slice proves that the private Host and a mock Agent Worker do not depend
on a live human browser connection. It does not call Hermes, OpenClaw, Dify,
Notion, or any other external runtime or service.

## Scenario

`scripts/private_host_console_disconnect_smoke.py` uses a temporary SQLite
database, a free loopback port, ephemeral in-memory fixture credentials, and a
private-host server process. The smoke:

1. bootstraps an Owner browser session;
2. creates a mock Worker identity and a planned task through authenticated
   human APIs with CSRF protection;
3. logs out, clears, and discards the first browser client;
4. confirms an unauthenticated client cannot read the task;
5. starts an independent mock Worker process after the browser is gone;
6. forces one retryable mock attempt before success to produce controlled
   background progress;
7. verifies that the original Host PID remains alive and unchanged;
8. signs in through a fresh browser client and reads the completed task, run,
   tool-call, evaluation, and runtime-event evidence.

The smoke does not claim OS service persistence, process survival after a Host
restart, or real-runtime completion. Those remain separate acceptance gates.

## Real Runtime Async Mode

The packaged manual-live client also supports an explicit async disconnect
path:

```bash
python3 scripts/customer_worker_real_runtime_acceptance.py \
  --base-url http://127.0.0.1:<host-port> \
  --human-auth \
  --confirm-live \
  --async-disconnect \
  --adapter hermes \
  --adapter openclaw
```

For each adapter the client submits one `202` workflow job with a fresh
`idempotency_key`, repeats the same request once to prove it resolves to the
same job, discards the first browser client without logging out, confirms an
anonymous read returns `401`, waits with no browser attached, then signs in
through a distinct Owner Session and polls the original job. It fails unless
the job completes with one matching request hash, the same task/run linkage,
passing evaluation and verified plan evidence. The replay must still return
`202`, and the recorded `completed_at` must be later than the moment the first
client was discarded; a job that completed before disconnect does not pass.
The key, Session cookies, CSRF,
credentials, raw prompts and raw responses are omitted from output and ledger
metadata.

The deterministic Private Host client smoke covers the state machine without
calling a Runtime. It also proves same-key/different-request conflict handling,
concurrent same-key single-job/single-run behavior, recovery of a persisted
queued reservation, transport-alias-stable request hashing, and human Session
workspace scoping for reads and mutations. Product-level
real Runtime evidence still requires the command above against an exact
installed release package.

## Verification

```bash
python3 -m py_compile scripts/private_host_console_disconnect_smoke.py
python3 scripts/private_host_console_disconnect_smoke.py
git diff --check -- scripts/private_host_console_disconnect_smoke.py \
  docs/PRIVATE_HOST_CONSOLE_DISCONNECT_ACCEPTANCE.md
```

Expected result:

- `ok: true`;
- `isolated_private_host: true`;
- `temporary_database: true`;
- `real_runtime_called: false`;
- `browser_connection_required_for_worker: false`;
- the first Session is revoked and anonymous readback returns `401`;
- the Host PID remains stable while the Worker runs;
- the mock Worker reports two attempts and a completed run;
- a fresh Session reads the completed task/run evidence;
- no fixture credential value appears in Host or Worker output.

## Deterministic Observed Result

Verified on 2026-07-12 against branch `codex/local-host-remote-console`:

- smoke exit code: `0`;
- `ok: true` with no failures;
- disconnected anonymous read: `401`;
- Host PID stable: `true`;
- mock Worker attempts: `2`;
- task and run status: `completed`;
- evidence: 1 tool call, 1 passing evaluation, and 8 runtime events;
- fresh reconnect Session: `true`;
- real Runtime calls: `false`.

All state was created below a temporary directory and removed when the smoke
finished. The temporary task/run identifiers are intentionally not retained as
release evidence.

## Exact-Package Real Runtime Result

Verified on 2026-07-12 against installed prerelease
`v1.6.0-private-host-preview.4`, exact commit
`1b8f2b9469105ce826e551b5e83fd9d5f0656bff`:

| Adapter | Workflow job | Run | Host authority receipt |
|---|---|---|---|
| Hermes | `wfjob_ab33425f1f5b3ec6ae4de5ff` | `run_gw_7c88b0db4d2a` | `phr_d6441f356098629861e67931` |
| OpenClaw | `wfjob_c8a51117c3db4c3adaddf98d` | `run_gw_b66254b6e070` | `phr_5f09a657d97a469ccb46b922` |

For both adapters the idempotent replay returned `202`, anonymous read after
discarding the first client returned `401`, a fresh distinct Owner Session
reconnected, and `completed_at` was later than the recorded disconnect time.
Each request produced exactly one matching workflow job and one task run, then
passed evaluation, plan-evidence, human approval, artifact and bounded audit
checks. Session cookies, CSRF values, credentials, raw prompts and raw responses
were omitted from the acceptance output.

This is real Host-local Session disconnect evidence. It is not evidence of a
physical second computer losing its tailnet or browser connection. That gate
requires a separate device and remains open.

## Product Meaning

The browser is an authenticated operator surface, not the execution owner.
Tasks are durable in the MIS ledger, and Workers use the separate Agent
Gateway machine path. Closing or revoking a browser Session therefore removes
human access without canceling Host-side work. Reconnection creates a new
human Session and resumes observation of the same ledger state.

## Limitations

- The Worker uses the deterministic mock adapter as an offline/CI fallback.
- The Worker is a bounded subprocess started by the smoke, not a launchd or
  systemd daemon.
- Browser network loss is represented by explicit logout plus disposal of the
  cookie jar; abrupt TCP loss is not separately instrumented.
- Real Hermes/OpenClaw completion after Host-local Session disposal is proven
  on preview.4; physical browser/tailnet disconnect on a second device remains
  open.
