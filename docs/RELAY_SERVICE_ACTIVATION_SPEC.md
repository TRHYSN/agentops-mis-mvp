# Relay Service Activation Specification

Status: pure Activation Plan Core v0, strict daemon config parser, read-only
FD-anchored host prerequisite scanner, production read-only systemd adapter and
activate preview CLI, private immutable activation journal core, read-only
installed-status journal validation, lifecycle-lock-owned first-install
namespace initialization, lifecycle-lock-bound private production store
opener and active-transaction scanner capability, canonical activation evidence
compiler, and private scanner-bound systemd mutation process adapter plus
private exact-confirmed success controller
and lifecycle-lock-guarded recovery snapshot plus deterministic recovery
decision compiler plus read-only locked recovery preview implemented and locally
accepted, with exact-confirmed non-systemd recovery writes also accepted; CLI
activation remains planned and unimplemented; private one-step recovery
execution is production-composed and fixture-accepted but lacks real Linux
systemd acceptance; fixture-store rollback verification, receipt, and
terminalization are accepted

## Objective

Turn one already validated Relay installation into an explicitly activated
systemd service without creating accounts, configuration, credentials, TLS
material, routes, DNS, or public infrastructure.

This slice starts only from an `installed_valid` result produced by
`agentops-relayctl status`. It must not treat an installed tree as proof that
the daemon is configured, running, healthy, or reachable.

The current `agentops_mis_cli.relay_activation` module implements strict
systemd-state parsing, private plan-hash compilation and bounded public
projection. `agentops_mis_cli.relay_activation_scan` now creates its private
prerequisite snapshot from one read-only, FD-anchored host observation.
Its ordinary entry point still requires `installed_valid`; its private
same-root capability path can validate the installed tree while a live
lifecycle lock protects an active journal transaction, and binds the raw
journal snapshot hash before and after the scan.
`agentops_mis_cli.relay_systemd_read` reads exactly one bounded systemd `show`
snapshot through the scanner-bound opened executable FD, and
`agentops_mis_cli.relay_activation_preview` requires a second exact
prerequisite scan before compiling the public projection. None of these
modules performs mutation. `agentops_mis_cli.relay_activation_journal` adds
strict canonical hash-chained revisions, ownership transitions, observation
evidence hashes, immutable receipts, no-replace publication, and fail-closed
recovery projection. Its private production opener owns the lifecycle lock for
the entire store lifetime, opens only an already exact journal namespace, and
revalidates namespace bindings around every operation. Missing, empty, or
partial namespaces fail closed without being initialized by the opener. It
does not expose a writer through the CLI. Confirmed first-install separately
binds the desired namespace and observed missing/exact-empty state into its
plan and durable transaction marker before creating the exact empty topology.
The live daemon
separately shares the bounded strict config parser used by the scanner. See
`RELAY_ACTIVATION_PLAN_CORE_ACCEPTANCE.md` and
`RELAY_CONFIG_PARSER_ACCEPTANCE.md`,
`RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md`, and
`RELAY_ACTIVATION_PREVIEW_ACCEPTANCE.md`, and
`RELAY_ACTIVATION_JOURNAL_ACCEPTANCE.md` plus
`RELAY_ACTIVATION_JOURNAL_STATUS_ACCEPTANCE.md` and
`RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md` and
`RELAY_ACTIVATION_PRODUCTION_STORE_ACCEPTANCE.md`.
The private process adapter is recorded in
`RELAY_SYSTEMD_MUTATION_ADAPTER_ACCEPTANCE.md`; it has no CLI caller.
The side-effect-free plan, journal-identity, and per-step evidence binding is
recorded in `RELAY_ACTIVATION_EVIDENCE_ACCEPTANCE.md`; it does not append a
journal revision or call the process adapter.
The private success controller that composes these boundaries is recorded in
`RELAY_ACTIVATION_CONTROLLER_SUCCESS_ACCEPTANCE.md`. It remains absent from the
CLI, uses the locked scanner capability for every production rescan, and maps
every post-prepared failure to retained recovery state.
The read-only exact-chain and optional terminal-receipt recovery input is
recorded in `RELAY_ACTIVATION_RECOVERY_SNAPSHOT_ACCEPTANCE.md`. The pure
hash-bound decision compiler is recorded in
`RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md`. Their lifecycle-lock-bound,
scanner/systemd/scanner stable composition is recorded in
`RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md`; none of these components
executes a recovery action.
The exact-confirmed private writer in
`RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md` can append one confirmed
observation, success receipt, or terminal revision, while every systemd action
remains zero-write and unreachable from the CLI.

## Command Contract

The currently implemented operator flow is read-only:

```bash
agentops-relayctl --root / activate
```

Only a missing `--root`, one exact `--root /`, or one exact `--root=/` is
accepted. A normalized spelling such as `/.`, an alternate root, or duplicate
root options return `host_root_required` before a scan or subprocess. This raw
activation-only check runs after side-effect-free argument parsing so root
values that happen to equal `activate` do not change `status`, `inspect`, or
`install` semantics.

The following confirmed CLI flow remains reserved for the rollback and recovery
slice and is not implemented:

```bash
agentops-relayctl \
  --root / \
  activate \
  --confirm-activate \
  --plan-sha256 <64-hex>
```

The current CLI parses `--confirm-activate` and `--plan-sha256` only to reject
either one with `activation_mutation_unavailable` before scanning or starting
a subprocess. The private success controller already requires the exact plan
hash but is intentionally unreachable until interruption recovery is complete.
Recovery remains a future separate command that must bind to a retained
transaction hash:

```bash
agentops-relayctl \
  --root / \
  activation-recover \
  --confirm-recovery \
  --transaction-sha256 <64-hex>
```

The normal activation path must run `daemon-reload`, `enable`, and `start` as
separate, receipted steps. It must not use `enable --now`.

Confirmed activation and recovery require the canonical host root `/`.
Tests may inject an in-process fake read-only systemctl runner through private
helpers; no adapter is exposed by the production CLI, and the production CLI
has no systemctl-path, root, resolver, scanner, or runner override.

## Plan Contract

The preview may expose only bounded identifiers, booleans, state labels, and
hashes:

```json
{
  "operation_id": "activate",
  "schema_id": "agentops.relay.activation-plan.v0",
  "ok": true,
  "state": "plan_ready",
  "plan_sha256": "<64-hex>",
  "release_id": "<version>-<commit12>",
  "version_id": "<version>",
  "unit_id": "agentops-mis-relay.service",
  "systemd": {
    "load_state": "loaded",
    "unit_file_state": "disabled",
    "active_state": "inactive",
    "sub_state": "dead"
  },
  "prerequisites": {
    "service_account": "ready",
    "config": "ready",
    "tls_material": "ready",
    "route_keys": "ready"
  },
  "requested": {
    "daemon_reload": true,
    "enable": true,
    "start": true
  }
}
```

It must not emit paths, user or group IDs, config values, certificate
identifiers, hostnames, routes, command output, service logs, environment
values, or exception text.

The public response exposes only `plan_sha256`, but that hash binds a private
canonical payload containing:

- the host root device, inode, owner, group, and mode;
- release ID plus installed release-tree and unit hashes;
- config, certificate, private-key, and route-key file identity, mode,
  ownership, size, and content hashes;
- the exact numeric service UID and GID plus required group membership;
- the canonical systemctl executable identity and content hash;
- allowlisted systemd properties, including load, unit-file, active, sub,
  result, fragment identity, invocation identity, and daemon-reload-needed
  state;
- the exact private enablement-link inventory for this unit.

Replacing one valid prerequisite with another valid prerequisite must therefore
invalidate the old plan. Private component hashes and numeric identities never
appear in output or terminal receipts.

## Preconditions

Activation fails closed unless all of these are already true:

- the exact installed Relay tree passes read-only status validation;
- the process has the required root authority;
- the `agentops-relay` account and group already exist;
- the packaged unit is the exact installed unit;
- live config is a safe regular non-symlink file with an accepted schema;
- referenced certificate, private key, and route-key files are safe regular
  non-symlink files with accepted ownership and modes;
- config, certificate, key, state, and status paths are opened through anchored
  trusted parent-directory descriptors; every existing component is
  non-symlink, identity-bound, and not group- or world-writable;
- config and certificate files are readable by the service account without
  being group- or world-writable;
- private key and route-key files are owned by the exact service UID, are mode
  `0600`, and are reachable through trusted, traversable parent directories;
- configured writable state and status paths are confined respectively to
  `/var/lib/agentops-mis-relay` and `/run/agentops-mis-relay`; no alternate,
  traversal, or symlinked writable location is accepted;
- account, group, file, and parent identities are bound to the plan rather than
  inferred only from readable labels;
- systemd reports a bounded, understood state for the exact unit;
- no activation transaction or install recovery marker is unresolved.

This slice validates prerequisite metadata and configuration semantics
internally. It never returns secret or route material.

## State Machine

- `plan_ready`: current state can be activated after exact confirmation.
- `already_active`: the exact unit is enabled and active; no mutation occurs.
- `active`: confirmed activation completed and post-state was verified.
- `service_state_rolled_back`: a failure occurred and activation-owned enable
  and start mutations were reversed and verified; the receipted daemon reload
  is explicitly not described as reversible.
- `recovery_required`: an interrupted transaction or unverified rollback
  remains.
- `invalid`: installed tree, prerequisite, unit, or observed systemd state is
  unsafe.
- `stale_plan`: the confirmed plan no longer matches current filesystem,
  prerequisite, or systemd state.

`ok` is true only for `plan_ready`, `already_active`, and `active`.

## Transaction And Rollback

Before the first systemd mutation, the controller must revalidate the installed
tree and prerequisites, reread systemd state, recompute the plan, and atomically
write a private bounded transaction marker. The marker records only:

- schema, unit, release, and version identifiers;
- plan hash;
- pre-enable and pre-active state labels;
- per-step intent and observed-result identifiers;
- whether this activation owns an enable or start mutation.

Activation state is namespaced under:

```text
/var/lib/agentops-relayctl/activation/transactions/<plan-sha256>/
  revision-<monotonic-revision>.json
/var/lib/agentops-relayctl/activation/receipts/<receipt-sha256>.json
```

The read-only status implementation accepts only that exact bounded namespace,
requires complete one-to-one terminal revision and receipt bindings for the
installed release, and returns `recovery_required` for incomplete or changing
state. It must not reuse the install `transaction.json`.
Terminal receipts are immutable, bounded, credential-free, and retained under
an explicit count policy. The existing `lifecycle.lock` is held from the final
plan refresh through terminal receipt persistence, rollback, or durable
`recovery_required` state.

Every transaction revision is a canonical hash-chained record written through
a new `O_EXCL` temporary file, file fsync, and no-replace publication to its
immutable revision name. Publication uses `renameat2(RENAME_NOREPLACE)` or a
same-filesystem hard-link equivalent that fails when the final name already
exists; ordinary overwriting rename is forbidden. The parent directory is
fsynced before the temporary file is removed and fsynced again. The previous
valid revision remains until the new revision is durable. Recovery accepts only
the highest contiguous valid hash-chained revision; a gap, fork, malformed
revision, existing final name, or ambiguous temporary file remains
`recovery_required`. Terminal receipts use the same no-replace publication,
file fsync, and parent-directory fsync. No transaction or receipt is modified
in place.

The implemented private success controller holds the production journal
lifecycle lock, requires an exact ready store, refreshes and matches the
confirmed plan, writes the prepared revision before the first mutation, and
persists each intent before running its fixed adapter command. It rescans before
and after every step, writes the immutable receipt before the terminal revision,
and returns only bounded hashes and state. Every post-prepared failure retains
`recovery_required`; this slice does not guess or roll back.

The implemented private journal core additionally binds the initial
enabled/disabled and active/inactive labels, hashes of the initial enablement
inventory and installed unit identity, and one step-specific observation hash
for every observed revision. The implemented side-effect-free evidence
compiler derives that exact journal identity from a refreshed confirmed plan
and defines canonical post-step hashes: enable ownership binds the exact
post-enable link inventory, while start ownership binds the exact post-start
InvocationID plus unit identity. It also defines bounded daemon-reload,
verification, rollback-stop, and rollback-disable observations. Raw systemd
output and private canonical payloads are not returned or journaled.

Confirmed execution order:

1. fsync the `daemon_reload` intent, run it, observe systemd, and fsync the
   observed result
2. when previously disabled, fsync the `enable` intent, run it, observe
   systemd, and fsync the observed result
3. when previously inactive, fsync the `start` intent, run it, observe systemd,
   and fsync the observed result
4. verify loaded, enabled, active, running, and successful result states
5. write and fsync an immutable terminal receipt
6. publish a no-replace terminal transaction revision and retain its immutable
   revision chain as completed history

Recovery treats an intent without a durable observed result as ambiguous. It
must compare the bound pre-state, current state, unit identity, and step
ownership before choosing an inverse action. If ownership cannot be proven, it
retains the transaction and returns `recovery_required`; it never guesses
whether a timed-out command took effect or reverses later operator state.
The implemented private recovery snapshot supplies the exact validated chain
and at most one receipt that can legally bind its current or next terminal
revision. It rejects premature or mismatched receipts and remains read-only;
the pure recovery decision compiler can combine that input with a caller-owned
stable observation to choose complete, terminalize, one forward resume, one
ownership-proven inverse, or blocked. Production lock-bound stable observation,
decision confirmation, and non-systemd production-store writes are composed
and fixture-executed.
That writer records a dedicated restored-state rollback verification, publishes
the exact rollback receipt, and terminalizes idempotently. Production-store
composition and one scanner-bound recovery mutation are implemented in the
private executor, with a fresh decision confirmation required for every next
step. Real Linux interruption acceptance remains future work. An interrupted
enable/start intent without a durable observation is blocked as
ownership-ambiguous even when the current state resembles its postcondition.
Every nonterminal decision must also reproduce the exact original plan hash
from the current private prerequisite identities and the journal-bound
pre-systemd state, so config/key/account/systemctl/release drift cannot pass
through a step-only observation hash.

On failure, the controller may stop only a service it started and disable only
a unit it enabled. It must never undo pre-existing operator state.
`daemon-reload` is receipted but is not described as reversible. Failed inverse
actions retain the marker and return `recovery_required`; rollback success
requires post-rollback state verification.

Start ownership requires the exact post-start systemd `InvocationID` and unit
identity to remain unchanged. A later operator restart changes the invocation
and blocks automatic stop. Enable ownership requires exact before/after
inventories of the unit's enablement symlinks. Automatic disable is permitted
only when the current inventory exactly matches the recorded post-enable
inventory; any new, missing, replaced, or operator-created link blocks inverse
action. Post-disable verification must recover the exact pre-enable inventory.

A durable terminal receipt makes its matching transaction directory completed,
not active. Status and future activation scans require a one-to-one hash
binding between the terminal revision and receipt. Normal activation does not
delete revision files. A separate bounded retention operation may later remove
an entire completed transaction and receipt under the lifecycle lock; partial
or unknown retention state remains `recovery_required`.

## Systemctl Boundary

The implementation must:

- resolve systemctl only from a fixed platform allowlist, canonicalize and
  verify its trusted parent chain, and bind its regular-file device, inode,
  owner, group, mode, size, and content hash to the plan;
- use `shell=False`, closed stdin, a fixed working directory, a minimal
  allowlisted environment, and a bounded timeout;
- read `show` output incrementally with a hard byte cap and terminate the child
  on overflow; send mutation stdout and stderr to `DEVNULL`;
- parse only allowlisted state fields and discard raw output;
- never invoke `sudo`, `pkexec`, a shell, `journalctl`, package managers, user
  management, network clients, or the Relay daemon directly.

The current read-only slice permits exactly one operation:

```text
systemctl --system show agentops-mis-relay.service --no-pager \
  --property=LoadState,UnitFileState,ActiveState,SubState,Result,ExecMainStatus,FragmentPath,NeedDaemonReload,InvocationID,MainPID
```

These mutation operations are implemented only as a private scanner-bound
process adapter. The private success controller calls them after durable intent,
and the private recovery executor maps one confirmed decision to one durable
step. They remain unreachable from the CLI until real Linux interruption
acceptance is complete:

```text
systemctl --system daemon-reload
systemctl --system enable agentops-mis-relay.service
systemctl --system start agentops-mis-relay.service
systemctl --system stop agentops-mis-relay.service
systemctl --system disable agentops-mis-relay.service
```

The parser accepts exactly one value for every requested property and rejects
duplicates or unknown output. `LoadState` must be `loaded`; `UnitFileState` is
limited to `enabled` or `disabled`; active units require
`ActiveState=active`, `SubState=running`, a nonzero `MainPID`, and a nonempty
canonical `InvocationID`; inactive units require `ActiveState=inactive`,
`SubState=dead`, and `MainPID=0`. `Result` must be empty or `success`. Active
units require `ExecMainStatus=0`; inactive units accept zero, or exactly 15
only when `Result=success`, because the fixed service uses systemd's default
`SIGTERM` stop and systemd may retain that signal as `ExecMainStatus`.
`FragmentPath` must resolve to the exact installed unit, and
`NeedDaemonReload` is limited to `yes` or `no`. These raw properties and paths
remain private plan inputs and are not copied to public JSON.

## Verification Split

Disposable fixtures can verify:

- deterministic plan and transaction hashes;
- exact confirmation and stale-plan rejection;
- prerequisite and systemd-state parsing;
- command and environment allowlists;
- idempotency for enabled/active combinations;
- failure injection at every step;
- activation-owned rollback;
- process termination after durable intent, after a command succeeds but before
  observation, and after observation but before the next marker update;
- identity-bound interruption and recovery behavior;
- output redaction and zero network access.

These mutation tests call the controller through injected fake systemctl and
filesystem-effects adapters rooted in a disposable directory. They do not call
the production CLI with a custom `--root`, and they do not weaken the
canonical-host-root gate.

A real Linux VM with systemd and root authority is required to verify:

- real daemon reload, enable, start, stop, and disable behavior;
- service account and group access;
- `ExecStartPre` configuration validation;
- `StateDirectory` and `RuntimeDirectory` creation and permissions, including
  preservation of the runtime directory across a controlled stop until
  rollback observation and explicit cleanup complete;
- active/running state, boot persistence, and restart behavior.

Neither fixture nor VM activation is evidence of firewall, DNS, ACME, public
TLS, public reachability, or physical browser acceptance.

## Out Of Scope

- service account or group creation;
- ownership provisioning;
- config, TLS, route-key, or credential generation;
- secret rotation or revocation;
- firewall, DNS, ACME, or public networking;
- release upgrade, release rollback, uninstall, or purge;
- application-level health beyond the bounded systemd and existing daemon
  checks;
- automatic restart of an already active service.
