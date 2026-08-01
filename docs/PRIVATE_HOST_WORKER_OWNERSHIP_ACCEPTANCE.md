# Private Host Worker Ownership Acceptance

## Scope

Private Host now treats Worker process ownership as an explicit startup gate.
Before it starts Host-owned `mock`, `hermes`, or `openclaw` Workers, it checks
for an already-running local Worker using the same adapter. A conflict fails
closed before the backend port opens.

This is not a process manager takeover. The Host does not stop, unload, or kill
an existing Worker. Operators choose one of two modes:

- Host-owned Workers: `agentops host start --worker <adapter>`;
- independently managed Workers: `agentops host start --no-workers`.

## Safety Contract

- Process discovery uses fixed adapter-specific patterns and invokes `pgrep`
  directly without a shell.
- Output includes only the adapter and numeric PID list. Full process commands,
  environment values, credentials, and tokens are omitted.
- If process discovery is unavailable or fails, Host-owned Worker startup fails
  closed with `worker_ownership_check_unavailable`.
- A conflict returns `worker_ownership_conflict`, does not open the Host port,
  and provides both ownership-mode remediations.
- `--no-workers` bypasses Worker ownership scanning because the Host does not
  create or own a Worker in that mode.
- Existing processes are never terminated automatically.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py scripts/private_host_worker_ownership_smoke.py
python3 scripts/private_host_worker_ownership_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
git diff --check
```

The dedicated smoke uses an isolated Host home, temporary SQLite database,
temporary UI fixture, free loopback port, and a no-op sleeping process whose
command shape matches a Hermes Worker. It proves:

- the duplicate Hermes adapter is detected;
- the fake Worker PID is present while its command is omitted;
- startup returns code 2 and the Host port remains closed;
- `--no-workers` starts the Host while preserving the existing process;
- after the fake Worker exits, a conflict-free Host-owned mock Worker starts
  and stops normally.

The smoke calls no real Hermes/OpenClaw runtime and retains no database,
credential, log, cache, or generated artifact.

## Known Limits

- Detection covers AgentOps MIS Worker command forms. It does not claim to
  discover unrelated third-party processes that happen to call the same model.
- This is a local startup preflight, not a cross-process lease protocol. An
  independently managed Worker started after the check is outside this slice;
  service installers should still use one declared ownership model.
- This gate prevents duplicate local process ownership; remote Worker identity,
  enrollment, and heartbeat conflicts remain enforced by Agent Gateway rules.
- Automatic migration from historical LaunchAgents is intentionally excluded.
  Operators must explicitly unload an old service or select `--no-workers`.
