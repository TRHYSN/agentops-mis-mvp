# Relay Activation Plan Core Acceptance

## Scope

This acceptance covers the pure `agentops_mis_cli.relay_activation` planning
module. The module accepts preconstructed, private identity snapshots and
bounded `systemctl show` bytes, then returns either a deterministic private
plan hash with a redacted public projection or a fail-closed state.

```bash
python3 scripts/relay_activation_plan_smoke.py
```

No production `agentops-relayctl activate` command exists in this slice.

## Implemented Contract

- strict parsing of exactly ten allowlisted systemd properties;
- rejection of missing, duplicate, unknown, oversized and malformed output;
- validation of loaded/enabled state, active/substate, result, exit status,
  fragment path, reload state, invocation identity and main PID combinations;
- immutable snapshot types for root, file, symlink, service-account,
  enablement-link and systemd state;
- explicit regular-file, directory and symlink kind binding, single-link
  checks, service readability, root ownership and exact
  `multi-user.target.wants` inventory validation;
- deterministic `plan_sha256` binding private file identities and hashes,
  numeric service identity and groups, release/unit identity, trusted parent
  chains, state/runtime directories, systemctl identity, enablement-link
  inventory, systemd state and requested actions;
- `plan_ready`, `already_active`, `recovery_required` and `invalid` states;
- an exact public projection containing only bounded state labels, safe release
  identifiers, requested-action booleans and the executable plan hash;
- no executable plan hash for `already_active`, `recovery_required` or
  `invalid`;
- compiled-origin and projection-binding checks reject hand-constructed or
  subsequently modified success plans;
- active services with `NeedDaemonReload=yes` fail closed because automatic
  restart is outside the current contract.

## Verification

The smoke uses synthetic values only. It:

- verifies disabled/inactive, enabled/inactive, disabled/active and
  enabled/active state handling;
- rejects fifteen malformed systemd cases, twenty-one unsafe prerequisite
  snapshots and forged systemd/plan dataclass projections;
- changes each private bound identity independently and requires a unique plan
  hash;
- checks exact public JSON keys and scans output for private paths, process
  identity and input canaries;
- blocks file opening, subprocess and socket behavior while the core runs;
- writes no files and reads no host configuration, certificate, private key,
  route key, service state or credential.

The module and smoke run in the Python 3.10/3.11 compatibility matrix and the
smoke also runs in the deterministic backend job.

## Truth Boundary

This remains a pure planning primitive. The separately accepted
`relay_activation_scan` module can now create its private snapshot from one
read-only host observation, but this core does **not** itself prove that an
arbitrary caller-provided snapshot came from that scanner or remains current.
It does not implement:

- a read-only systemctl adapter;
- an `activate` or `activation-recover` CLI command;
- confirmation, lifecycle locking, transaction revisions or receipts;
- daemon reload, enable, start, stop, disable, rollback or recovery;
- Linux account, group, config, certificate or route-key provisioning;
- a real Linux VM/systemd acceptance;
- public Relay deployment, firewall, DNS, ACME or browser reachability.

The live daemon now shares the separately accepted strict bytes parser in
`RELAY_CONFIG_PARSER_ACCEPTANCE.md`. That parser does not inspect host identity
or bind its result to an activation plan.

See `RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md` for the host-observation boundary.
The next slice must combine a fresh scanner result with the read-only systemd
adapter before exposing a confirmable preview. It must not treat a
caller-constructed snapshot as authority.
