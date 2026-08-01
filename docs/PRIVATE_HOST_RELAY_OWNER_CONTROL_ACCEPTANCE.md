# Private Host Relay Owner Control Acceptance

Date: 2026-07-18

## Product Slice

The existing Account and access Workspace page now exposes a bounded Remote
Console control for an authenticated Owner. The Owner first prepares an exact
`enable` or `disable` transition, then confirms the short-lived in-memory
transition reference. Confirmation executes the exact prepared config change
once and reports that a Host service restart is required.

This is a local configuration-control slice. It does not provision or deploy a
Relay, generate certificates, restart the installed Host, publish a remote URL,
or claim browser-only remote readiness.

## HTTP Contract

```http
GET  /api/host/relay
POST /api/host/relay/transitions
POST /api/host/relay/transitions/{transition_ref}/confirm
```

- All routes require a Human Session with the `owner` role in `private_host`
  mode.
- Both POST routes require exact Origin validation and the Human Session CSRF
  token.
- Agent Gateway API keys, Host machine credentials and anonymous callers do not
  authorize these routes.
- Request bodies accept only `{"action":"enable"}` or
  `{"action":"disable"}`.
- Responses and audit metadata omit config paths, hostnames, ports, routes,
  certificate material, private digests and machine credentials.

## Verification

```bash
python3 -m py_compile \
  server.py \
  agentops_mis_cli/host.py \
  agentops_mis_cli/relay_control.py \
  scripts/private_host_relay_control_core_smoke.py \
  scripts/private_host_relay_cli_control_smoke.py \
  scripts/private_host_relay_owner_control_smoke.py
python3 scripts/private_host_relay_control_core_smoke.py
python3 scripts/private_host_relay_cli_control_smoke.py
python3 scripts/private_host_relay_owner_control_smoke.py
cd ui/start-building-app && npm run build
```

The deterministic core smoke proves private `0700`/`0600` material, prepare
without mutation, non-executing confirmation, single-use execution, bounded
TTL, action/ref/material mismatch rejection, replay rejection, exact
enable/disable config projection, Tailscale field preservation, transactional
rollback after an injected second-write failure, bounded public errors and zero
runtime network/subprocess use.

The server-backed smoke uses an isolated Host home and temporary SQLite ledger.
It proves anonymous, non-Owner, machine-bearer, missing-CSRF and wrong-Origin
denial; Owner prepare and confirm; replay rejection; config preservation; and
bounded prepare/execute/failure audit events. It does not contact Tailscale or a
Relay.

## Known Limitations

- This document records the original non-restarting Owner-control slice. The
  current branch now completes confirmed transitions through the managed Host
  restart and terminal receipt path documented in
  `PRIVATE_HOST_MANAGED_RESTART_ACCEPTANCE.md`.
- A bounded Relay-side exact-SNI router now exists in the current branch, but no
  deployed Relay, DNS, public certificate lifecycle or production credential
  provisioning exists yet.
- `remote_ready` must remain false until deployed-Relay and physical
  browser-only acceptance are both complete.
- Preview.42 now packages this Owner-control and managed-restart path. Its real
  installed Host remains safely unconfigured and disabled, so it is not
  deployed-Relay or remote-readiness evidence.

## Next Slice

Deploy the non-authority Relay, DNS and public certificate lifecycle, then bind
the managed restart receipt to exact-head physical browser-only acceptance.
