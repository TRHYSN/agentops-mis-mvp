# Private Host Background Service Acceptance

## Scope

This slice adds an optional macOS user-level LaunchAgent for the AgentOps MIS
Host control plane. It is deliberately separate from Worker services and from
the graphical `AgentOps MIS.app` launcher.

The service command is fixed to:

```text
agentops host start --foreground --no-workers
```

It keeps the local Host available after login/restart, but never installs or
starts Hermes/OpenClaw and never authorizes live Agent execution.

## Command Contract

```bash
agentops host service-install
agentops host service-install --confirm-install
agentops host service-check
agentops host stop
agentops host service-control --action load|unload|restart
agentops host service-control --action <action> --confirm-control
agentops host service-remove
agentops host service-remove --confirm-remove
```

- Install, launchctl control and removal are preview-only by default.
- Install writes `~/Library/LaunchAgents/dev.agentops.mis.private-host.plist`
  only after `--confirm-install`; writing does not load the service.
- The same confirmed install creates an empty owner-only `launchd.log` with
  mode `0600`, or tightens an existing owner-owned regular single-link file to
  `0600` without reading or changing its bytes. Dry-run reports the required
  action without mutation.
- A symlink, non-regular file, hardlink, wrong owner or unsafe log directory
  blocks installation. Load and restart are also blocked until the launchd log
  passes the private-file gate; unload remains available for recovery.
- The file is mode `0600` and contains only Host/install paths plus
  `PYTHONPATH`. It contains no machine key, Admin key, setup code, browser
  Session, CSRF value or Runtime credential.
- The service follows the packaged `current` link so a verified update does not
  pin a stale release directory.
- Load is blocked while an independently managed Host process is already
  running. Repeated confirmed load is an idempotent no-op.
- An existing file can be overwritten only when its complete bytes match the
  current managed definition, or the one explicitly supported previous
  definition. A previous definition must be private, credential-free and
  unloaded before an explicitly confirmed overwrite can migrate it. An unknown,
  edited or loaded previous file fails closed.
- The current CLI may issue only `unload` for an exact loaded previous
  definition. It cannot load or restart that definition. This gives upgrades a
  bounded recovery path without executing code from the previous package.
- Removal is blocked while launchd reports the service loaded.
- The product uninstaller refuses to continue while the default service file is
  present, preventing a leftover restart loop after binary removal.
- Raw plist content and launchctl output are omitted from command responses.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py \
  scripts/private_host_background_service_smoke.py
python3 scripts/private_host_background_service_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
python3 scripts/private_host_bundle_smoke.py
python3 scripts/release_evidence_packet_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The isolated smoke uses a temporary Host home, service path and fake launchctl
state. It proves dry-run install, confirmed write, exact host-only arguments,
private plist and launchd-log modes, legacy launchd-log permission repair with
content identity preserved, unsafe symlink refusal, credential omission,
read-only check, dry-run control, confirmed
load/restart/unload, duplicate-load idempotency, loaded-removal blocking,
confirmed removal, exact previous-definition migration, loaded previous-service
blocking and unknown/same-label/credential/path-edited overwrite rejection.

## Preview 39 Log Permission Finding

On 2026-07-22, the installed preview.39 Host exposed a real package gap through
the product's metadata-only commands. `agentops host logs` reported an active
Host log of 51,388,825 bytes. `agentops host log-rotate` then failed closed with
`host_log_inventory_permissions_unsafe` for `launchd.log`. A metadata-only stat
confirmed that the Host log directory was owner-private and `host.log` was
`0600`, but launchd had created its 12,955,332-byte output file as `0644`.
No log content was read.

The source correction makes service installation own this boundary before
launchctl does: it creates or identity-checks the expected log through an
anchored private directory descriptor, applies `0600` through the open file
descriptor, verifies the named inode is unchanged, and only then writes the
managed plist. Existing `0644` files are repairable only when they are regular,
owned by the current user and have one link. The isolated service smoke proves
fresh creation, zero-write preview, byte-preserving repair, symlink refusal,
load blocking before repair and a successful log-rotation preview after repair.
`log-rotate` routes a repair candidate only to the read-only `service-check`;
`service-check` advertises the managed repair command only for that verified
repairable state and a compatible managed service definition.
Unsafe identities or an unknown service definition require manual recovery and
never receive an automatic mutation command. The same boundary now requires an
exact owner-owned `0600` plist in a non-group/world-writable user-owned parent;
fresh install may create its missing private parent.

This is source evidence. Preview.39 remains correctly blocked from rotation;
the release gate stays open until the correction is packaged, installed, the
real metadata-only plan succeeds, and an explicit stopped-Host rotation plus
restart is recorded.

## Upgrade Migration Contract

preview.34 added the `--managed-launch-agent` process-identity gate to the Host
service definition. A real preview.33-to-preview.34 installation preserved the
existing LaunchAgent file, but the preview.34 CLI correctly rejected it as not
the current exact definition. That exposed an upgrade loop: the new CLI would
not overwrite the old managed file, while the old file could still launch the
new package through the stable `current` link.

The corrective source slice recognizes exactly one previous byte definition:
the same Host-only, credential-free service without
`--managed-launch-agent`. Recognition is not enough to mutate it. Migration
also requires mode `0600`, a regular non-symlink file, the exact current Host
home/install/current-link/log paths, launchd reporting the service unloaded,
`--overwrite`, and `--confirm-install`. The replacement remains atomic and the
post-write check must match the current exact definition.

If that exact previous definition is still loaded, the current CLI permits one
operation: an explicitly confirmed `service-control --action unload`. It still
rejects load and restart, leaves the plist bytes unchanged during unload, and
requires the separate confirmed overwrite before the service can be loaded
again.

The migration gate does not accept structurally similar plist files. A changed
command, extra environment value, credential marker, path outside the managed
installation contract, unsafe mode, symlink or loaded previous service remains
blocked. Raw plist content is still omitted from command output.

## Local Service Loaded Receipt

**Superseded Preview 28.**

On 2026-07-14, preview.19 first rendered the installation plan in dry-run mode,
then wrote the default managed LaunchAgent only after the explicit
`--confirm-install` gate. During the preview.28 upgrade, the existing Host-only
service was explicitly unloaded before installation and loaded again afterward.
Current readback proves:

- the file is a safe regular plist with mode `0600`;
- its bytes exactly match the packaged managed definition;
- no credential-like material is present;
- the service command remains Host-only and starts no Worker;
- launchd reports the Host-only service loaded.

The loaded service serves the installed preview.28 Workspace on loopback and
continues to start with `--no-workers`. The independently service-managed Hermes
and OpenClaw Workers recovered fresh heartbeat state after the Host restart;
the Host service did not start, stop, or claim their processes. No Runtime task
was dispatched during the upgrade or app-open receipt. This loaded receipt is
not logout/reboot proof.

On 2026-07-15 the installed preview.28 service was exercised once more through
the consumer CLI. The unconfirmed command returned only the planned
`launchctl kickstart -k` action and reported `service_mutated:false`. The same
command with `--confirm-control` replaced the Host process, then the loopback
health check returned ready and `agentops host version` still identified exact
release commit `f627e83`. Tailscale Serve remained ready on private HTTPS with
Funnel disabled. The control receipt contained no Worker action and reported
`live_workers_started:false`; readback of the actual independent Hermes and
OpenClaw LaunchAgent units showed both still running. This proves an explicit
installed-service restart without implicit Runtime or Worker ownership. It
still does not substitute for a physical logout/reboot receipt.

## Current Preview 29

On 2026-07-15, the real Host upgraded from preview.28 to
`v1.6.0-private-host-preview.29` at exact commit
`574c735541d95b70180254235a385ff764f8c45c`. The Host LaunchAgent was
explicitly unloaded, preview.29 was installed, and the Host LaunchAgent was
loaded again. Upgrade readback reported `previous_version=.28`.

The preview.29 Host service loaded successfully and returned ready through the
installed consumer CLI. The managed plist remained Host-only, followed the
packaged `current` link and started with `--no-workers`. The independent Hermes
and OpenClaw Worker PIDs were preserved across the Host service upgrade; no
Worker was installed, started, stopped or claimed by the Host LaunchAgent. No
model task was dispatched or executed. Readback identified exact release commit
`574c735`.

Opening the installed app afterward reused the loaded Host and both Worker
processes. This is same-login service-control and process-reuse evidence only;
real physical logout/reboot persistence remains open.

preview.29 was published through the manual prerelease path; the Private Host
Preview Release workflow did not run.

## Current Preview 31

On 2026-07-15, the real Host upgraded from preview.30 to public prerelease
`v1.6.0-private-host-preview.31` at exact commit
`fed1b2410d6725a217c9727dba570db62cc46963`. The Host-only LaunchAgent was
explicitly unloaded before installation and loaded again afterward. The Host
returned ready on loopback and private Tailscale HTTPS, while Funnel remained
disabled.

The installer atomically replaced both CLI shim inodes and the commands ran
immediately after installation. The independently managed Hermes and OpenClaw
services stayed running through the install without exit 137 or `-9`; explicit
service restarts then created PIDs `38056` and `38080`, both with fresh Agent
Gateway heartbeats and no recorded exit. The Host LaunchAgent still contains
no Worker command or credential material.

This is same-login service-control, upgrade and Worker-continuity evidence. It
still does not substitute for a physical logout/reboot receipt.

## Known Limits

- This is an unsigned macOS preview, not a signed/notarized installer service.
- CI validates the launchd contract with an isolated fake launchctl. Physical
  logout/reboot persistence still requires a clean macOS account or VM receipt.
- The service keeps only the MIS Host control plane alive. Hermes/OpenClaw
  Worker services remain independent and require their own explicit live-run
  confirmation. Same-Mac Worker services can use the credential-free
  `local_config` reference described in
  `docs/PRIVATE_HOST_WORKER_SERVICE_ACCEPTANCE.md`; this does not change the
  Host-only service command.
- Browser UI controls for service installation are not included in this slice;
  the existing `.app` remains the normal low-friction way to initialize, start
  safely and open the same React Workspace.

## Next Gate

On a clean macOS account, install the exact prerelease, confirm the service,
load it only after stopping any independently managed Host, log out/reboot, and
record that the same Workspace becomes reachable without any live Worker
starting implicitly.
