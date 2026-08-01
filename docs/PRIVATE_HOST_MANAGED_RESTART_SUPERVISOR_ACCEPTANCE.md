# Private Host Managed Restart Supervisor Acceptance

Date: 2026-07-18

## Accepted Slice

The exact managed LaunchAgent foreground Host can now supervise its owned
`run_local_stack.py` child and accept one bounded internal replacement request.
The parent stops only that child, reloads the current private Host config and
secrets from disk, waits for the inherited readiness signal plus loopback health,
and writes a new private service-instance record.

The Owner Relay confirmation route now invokes this primitive only after its
`202` response body flushes. The request is bound to the private receipt action,
transition ref, transaction sequence and expected revision. Replacement runtime
failure restores both configs and relaunches the old Host. Crash reconciliation
is now performed before a newly launched supervisor loads config or starts its
first stack.

The supervisor validates the kernel-reported Unix peer PID as the exact direct
`server.py` child of its current stack and holds the Host lifecycle lock for the
whole replacement transaction. Unrelated same-UID socket clients and concurrent
Host lifecycle commands cannot consume the receipt or create a second stack.

## Authority And Isolation

- The LaunchAgent file must match the exact generated Host-only template.
- The exact service label must be loaded and the supervisor parent must be
  launchd.
- The hidden managed flag is accepted only with `--foreground --no-workers`.
- The backend requester must be the exact direct `server.py` child of the current
  stack child, verified again from the accepted socket peer PID.
- The private service-instance record binds supervisor PID, stack child PID,
  exact label and exact template hash.
- The Unix socket and instance record are mode `0600`; their directory is a
  same-owner, non-symlink directory in exact mode `0700`.
- A manual foreground Host has no automatic restart authority.
- No shell, Tailscale command, Worker command or unrelated PID operation is
  used.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/host.py \
  scripts/private_host_managed_restart_supervisor_smoke.py
python3 scripts/private_host_managed_restart_supervisor_smoke.py
python3 scripts/private_host_background_service_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
python3 scripts/private_host_relay_lifecycle_smoke.py
git diff --check
```

Results:

- exact service gate: pass;
- manual foreground rejection: pass;
- unrelated same-user requester rejection: pass;
- unsafe/symlink/wrong-mode run directory rejection: pass;
- one request creates one replacement child: pass;
- stubborn owned child receives bounded terminate then kill: pass;
- replacement reloads changed allowed-origin/cookie configuration: pass;
- service-instance and public result redaction: pass;
- existing LaunchAgent, Host and Relay lifecycle regressions: pass.

All tests use a temporary Host home and fake child/service boundaries. The
installed Host, Tailscale profile, Hermes/OpenClaw services and local database
were not changed.

## Remaining Integration

- prove the bounded post-restart audit projection from the exact installed
  release candidate; deterministic coverage is recorded in
  `PRIVATE_HOST_RESTART_AUDIT_RETENTION_ACCEPTANCE.md`;
- install the new exact service template into a versioned Host release candidate;
- run physical browser-only acceptance against a deployed Relay.
