# Relayctl Offline Install Acceptance

## Scope

This slice adds a fail-closed, dependency-free offline install transaction for
the existing `agentops.relay.release-bundle.v1` artifact. It is deliberately
limited to inspection and first installation:

```bash
agentops-relayctl \
  --root / \
  inspect \
  --bundle /absolute/path/to/agentops-mis-relay-<version>-<commit>.tar.gz \
  --expect-sha256 <sha256>

agentops-relayctl \
  --root / \
  install \
  --bundle /absolute/path/to/agentops-mis-relay-<version>-<commit>.tar.gz \
  --expect-sha256 <sha256>
```

`install` is always a dry-run unless both `--confirm-install` and the exact
`--plan-sha256` returned by the current dry-run are supplied. The plan binds the
verified archive, release identity, target root device/inode/mode/ownership
fingerprint, current install state, wheel, unit, launcher, and manifest hashes.
It contains no timestamp, so
the same inputs and unchanged target state produce the same plan hash.

## Bundle Verification

The controller implements the runtime bundle v1 reader inside
`agentops_mis_cli`; it does not import repository `scripts`.
The packaged wheel exposes the same controller as `agentops-relayctl`; direct
`python3 -m agentops_mis_cli.relay_admin` remains an equivalent recovery entry.

Before planning an install it requires:

- an absolute, regular bundle path and an exact caller-supplied SHA-256;
- one canonical archive root matching the manifest version;
- the exact v1 member and directory whitelist;
- canonical manifest and `SHA256SUMS` data;
- exact manifest size/hash and checksum coverage;
- normalized regular-file/directory tar metadata;
- no duplicate, absolute, traversal, non-ASCII, link, device, or other special
  archive members;
- bounded archive size, member count, per-member size, and expanded size;
- one safe pure-Python wheel whose package members are top-level `.py` files;
- exact distribution name/version, `py3-none-any`, purelib metadata, parsed
  console-script map, and complete RECORD hash/size coverage;
- the exact versioned wheel filename and exact release-schema-v1 Python module
  set, including every declared console entry point and its dependencies;
- no duplicate, traversal, encrypted, link, device, out-of-package, oversized,
  or excessive wheel members.

The verified wheel is unpacked directly with bounded ZIP reads. The
implementation never calls `pip` or imports the release code while installing.

## Install Layout

The first confirmed install publishes:

```text
/opt/agentops-mis-relay/
  releases/<version>-<commit12>/
    bin/agentops-relay
    private/site-packages/
    release.json
    systemd/agentops-mis-relay.service
  current -> releases/<version>-<commit12>
  controller -> releases/<version>-<commit12>

/usr/local/bin/agentops-relay
  -> ../../../opt/agentops-mis-relay/current/bin/agentops-relay

/etc/systemd/system/agentops-mis-relay.service
/var/lib/agentops-relayctl/lifecycle.lock
/var/lib/agentops-relayctl/activation/
  receipts/
  transactions/
```

Release files are staged under the release filesystem, fsynced, and renamed
into place. The unit is published without overwrite, and each symlink is
created atomically only after the release and unit exist. Confirmed publication
opens the approved root with `O_NOFOLLOW`, verifies its device/inode
fingerprint, and performs all filesystem operations from that held directory
descriptor. Replacing the original root path therefore cannot redirect writes;
a mismatch before completion becomes `plan_stale` and rolls back exact
artifacts. A private transaction
marker and lifecycle lock prevent concurrent controllers from treating a
partial transaction as complete. A caught publish failure removes only exact
artifacts owned by that plan; an unresolvable interrupted state fails closed as
`recovery_required`, including a crash that leaves only the pre-publication
transaction temporary file.

The exact install plan also binds activation namespace schema, desired entries,
mode, and observed `missing` or `exact_empty` state. The canonical transaction
marker is durable before namespace creation starts. Fresh install creates the
empty topology through held-directory-FD operations; an exact empty topology
is preserved. Partial, unknown, or preinstall history states fail closed. See
`RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md`.

The installer creates the lifecycle lock with `O_EXCL`, or opens an already
safe lock without changing it. The lock must be an empty, owner/group-matched,
single-link regular file with exact mode `0600`; symlinks, FIFOs, directories,
hardlinks, nonempty files, and mode drift fail before other install directories
are created. The installer holds a no-follow FD for the exact owner-only admin
directory; lock stat/open/unlink operations are relative to that FD. Both the
admin path/FD and lock path/FD must keep the same identity before and after
nonblocking `flock`. Lock contention and acquisition faults fail closed as
`lifecycle_lock_failed`, and every failure path closes both descriptors. The
bindings are checked again before publication commits and before unlock.

The same fully verified release is an idempotent no-op. A different installed
version fails closed as `upgrade_required`; upgrade and rollback are intentionally
future commands. A different commit under the same version fails closed rather
than being treated as equivalent.

## Protected State

The installer does not read, create, replace, chmod, or delete either of these
Relay runtime material paths:

```text
/etc/agentops-mis-relay
/var/lib/agentops-mis-relay
```

It does not generate configuration, route keys, TLS material, credentials, or
an MIS database. The smoke places independent config and epoch canaries in
those paths and requires their inode, bytes, and nanosecond mtime to remain
unchanged, together with mode, UID, and GID, through dry-run, rejected
confirmation, confirmed install, repeated no-op, and rejected
different-version attempts.

## Output Contract

Successful output contains only operation/schema/release/version identifiers,
hashes, counts, and booleans. Expected failures contain a bounded `error_id`
and, for a different installed version, the `future_operation_id` `upgrade`.
Paths, archive bodies, environment values, credentials, configuration,
application payloads, and Python exception text are not emitted.

## Verification

Run:

```bash
PYTHONPYCACHEPREFIX="$(mktemp -d)" \
  python3 -m py_compile \
  agentops_mis_cli/relay_admin.py \
  scripts/relay_offline_install_smoke.py
python3 scripts/relay_offline_install_smoke.py
git diff --check -- \
  agentops_mis_cli/relay_admin.py \
  scripts/relay_offline_install_smoke.py \
  docs/RELAY_OFFLINE_INSTALL_ACCEPTANCE.md
```

The smoke:

1. builds the real current-HEAD bundle v1 in a clean local clone;
2. blocks network calls, `os.system`, and every installer subprocess;
3. runs all install operations against private temporary `--root` sandboxes;
4. verifies archive SHA, manifest, checksums, member whitelist, and wheel safety;
5. rejects tampering, duplicate/traversal/link/device/oversized tar members,
   traversal/link/native/missing/extra wheel members, false entry points,
   RECORD mismatch, and deeply nested malformed metadata;
6. proves dry-run stability and zero writes before exact confirmation;
7. verifies private wheel extraction, release/current/controller/bin/unit
   layout, modes, and removal of staging files;
8. proves same-release no-op and different-version fail-closed behavior;
9. binds confirmation to the target root device and inode, and rejects a
   symlink supplied as `--root`; it also swaps the root after confirmation and
   proves the replacement receives no writes;
10. rejects a symlinked install parent without changing the external target;
11. turns retained transaction and pre-publication temporary markers into
   `recovery_required`;
12. injects a mid-publish link failure and proves exact install artifacts are
   rolled back while the safe empty namespace remains durable and is bound
   into the next plan;
13. verifies protected config/epoch inode, content, mtime, mode, UID, and GID;
14. scans output for environment and bundle-body canaries; and
15. rejects six unsafe existing lock forms without repairing or changing them,
    rejects lock contention and post-`flock` path replacement, and injects
    `fchmod`/`flock` failures while checking descriptor cleanup;
16. rejects admin-directory replacement both during lock acquisition and after
    the lock is held;
17. proves unsafe admin/lock preflight leaves all other install state
    untouched; and
18. requires repository status to remain unchanged.

## Boundaries

This acceptance is not evidence of:

- Linux account creation or ownership provisioning;
- `systemctl`, daemon reload, enable, start, restart, or boot recovery;
- a real Linux VM, firewall, public endpoint, DNS, ACME, or stock browser;
- route-key/TLS provisioning, rotation, or revocation;
- upgrade, rollback, uninstall, purge, or crash-recovery automation;
- complete root-to-admin `openat` parent-chain anchoring against a same-owner
  hostile filesystem; and
- transaction creation, commit unlink, and rollback performed entirely through
  the held admin directory FD.

Those remain separate lifecycle and public-network gates. The v0 installer
does not invoke or simulate them.
