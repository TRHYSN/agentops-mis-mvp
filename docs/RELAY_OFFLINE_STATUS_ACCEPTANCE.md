# Relay Offline Status Acceptance

## Scope

This acceptance covers the read-only `agentops-relayctl status` contract for
the installed Linux Relay tree. It operates only on a disposable absolute
`--root` and emits bounded JSON on stdout.

```bash
python3 scripts/relay_offline_status_smoke.py
```

The smoke builds a formal release bundle from the current source build backend,
performs a confirmed first install into a temporary root, and invokes status
through both the live source controller and, when the local Python has a usable
pip install path, the generated `agentops-relayctl` wheel entrypoint. During
pre-commit development this fixture deliberately uses current source bytes;
the final mainline acceptance reruns after those bytes are committed so the
bundle commit identifier is authoritative.

## Status Contract

Every diagnosed response is JSON on stdout with:

```text
schema_id: agentops.relay.offline-status.v0
operation_id: status
state_id: absent | installed_valid | recovery_required | invalid
ok: boolean
```

`absent` returns exit `0`; `installed_valid` returns exit `0`;
`recovery_required` and `invalid` return exit `1`. Expected failures do not
write diagnostics to stderr and do not expose paths, raw configuration,
archive bodies, credentials, or exception text.

For an installed tree, the response reports only safe identifiers, counts,
relationships, RECORD integrity, and provenance fields marked
`provenance_only`. The recorded archive, manifest, and wheel hashes are not
reverified because no bundle is supplied.

## Verification Matrix

The smoke verifies:

- empty roots and safe empty/lifecycle-lock scaffolding return `absent`;
- a formal bundle plus confirmed install returns `installed_valid`;
- transaction, transaction-temporary, release-staging, and unit-temporary
  markers return `recovery_required`;
- absent, empty, complete active, and complete rolled-back activation journal
  histories are accepted without changing the public valid-status shape;
- incomplete, malformed, unknown, wrong-release, or concurrently replaced
  activation journal namespaces return `recovery_required`;
- missing, extra, and tampered release files return `invalid`;
- malformed, noncanonical, or identity/count-inconsistent `release.json`
  returns `invalid`;
- current, controller, stable launcher, and installed unit mismatches return
  `invalid`;
- a same-target symlink replacement between metadata validation and `readlink`
  returns `invalid`;
- release symlink/native modules and wrong modes return `invalid`, with wrong
  ownership exercised when the runner has permission to change ownership;
- missing, extra, digest-mismatched, and size-mismatched RECORD rows return
  `invalid`;
- unsafe parent chains, a symlink supplied as `--root`, and a post-scan root
  path swap return `invalid` without redirecting reads or writes;
- protected `/etc/agentops-mis-relay` and `/var/lib/agentops-mis-relay`
  canaries retain inode, bytes, mtime, mode, UID, and GID;
- a child-process guard blocks network, subprocess, write, rename, unlink,
  chmod, chown, symlink, service-call behavior, write-capable `os.open`
  flags, writable `builtins.open`/`io.open` modes, and pathlib mutation
  methods during status;
- every tested root is tree-digested immediately before and after each status
  invocation, in addition to the protected canary snapshots;
- all source and installed-entrypoint output is checked for path, config,
  credential, traceback, and exception leakage;
- the repository status remains unchanged except for the pre-existing
  controller/spec changes and these two assigned files.

## Truth Boundary

`installed_valid` means only that the installed release tree is internally
consistent: the anchored root, exact release layout, ownership/modes, release
metadata, links, launcher, unit, exact package module set, entry points, and
RECORD coverage all agree.

This acceptance does **not** claim:

- service or systemd state, daemon health, process liveness, or port health;
- live configuration validity or configuration readability by the service;
- TLS, route-key, credential, runtime-state, or filesystem-state validity;
- bundle re-verification or source/provenance authenticity;
- upgrade availability, upgrade safety, rollback, recovery automation, or
  uninstall behavior;
- account provisioning, systemd activation, public DNS, ACME, firewall,
  public endpoints, or internet reachability.

Status is a truthful installed-tree readback, not a service or deployment
doctor.

The activation-journal extension is recorded separately in
`RELAY_ACTIVATION_JOURNAL_STATUS_ACCEPTANCE.md`.
