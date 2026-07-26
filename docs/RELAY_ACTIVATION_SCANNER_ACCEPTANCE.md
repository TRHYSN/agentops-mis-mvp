# Relay Activation Prerequisite Scanner Acceptance

## Scope

This acceptance covers the read-only, FD-anchored host prerequisite scanner in
`agentops_mis_cli.relay_activation_scan`. It converts one exact live Relay
installation plus its already-provisioned service inputs into the private
`ActivationPrerequisiteSnapshot` consumed by the pure Activation Plan Core.

```bash
python3 scripts/relay_activation_scan_smoke.py
```

The production entry point always scans `/`. It exposes no root, account
resolver, command path, fixture or mutation override, and this slice adds no
`agentops-relayctl activate` command.

An additional private entry point accepts only a capability issued by a live
`_LockedActivationJournalStore` for the same root. It exists so an exact
confirmed activation or recovery transaction can continue validating the
installed tree after its durable `prepared` revision makes ordinary status
correctly return `recovery_required`.

## Implemented Contract

- hold one no-follow host-root descriptor for the entire scan and revalidate
  both the held descriptor and root namespace identity before returning;
- require effective UID `0` before any production host access and again before
  returning the private snapshot;
- traverse every release, unit, config, TLS, route-key, state, runtime,
  systemctl and enablement path with descriptor-relative, no-follow opens;
- bound held descriptors, directory entries, release files, release bytes,
  config bytes, TLS material, route-key bytes and path length;
- require the exact installed tree to pass anchored status validation both
  before and after observation while all captured descriptors remain held;
- keep ordinary status fail-closed for an active journal transaction, while a
  live same-root lifecycle-lock capability permits only the installed-tree
  portion of status validation;
- bind the capability to one root and one raw journal tree snapshot hash before
  and after scanning, rejecting a forged subtype, closed session, another root,
  or any journal revision/receipt drift during the scan;
- compute a live release-tree hash over canonical paths, content hashes and
  numeric filesystem identities rather than reusing provenance metadata;
- resolve and revalidate the exact `agentops-relay` UID, primary GID and
  supplementary groups without exposing numeric identities publicly;
- delegate configuration syntax to the shared strict bytes parser, then confine
  certificate, key, route-key, state and status paths to their accepted roots;
- require safe regular-file kind, ownership, mode and single-link identity for
  unit, config, TLS material, route keys and systemctl;
- decode route keys exactly as the daemon does, require 32 decoded bytes and
  reject reused key material;
- require service-owned `0700` state/runtime directories and service-traversable
  trusted parent chains;
- validate both absence and safe existing `0600` state/status leaves on every
  anchored scan, while excluding their expected runtime creation/replacement
  from the stable parent-chain hash;
- bind the exact optional `multi-user.target.wants` symlink identity and target,
  including an explicitly revalidated absent state;
- return only an internal private snapshot suitable for
  `compile_activation_plan`.

## Verification

The smoke builds a synthetic installed Relay tree with the real offline build
backend, runs the scanner under write, network, subprocess and unanchored-open
guards, and verifies that the resulting snapshot compiles into the expected
activation plan.

It also rejects twenty-four unsafe or raced fixtures, including:

- a non-root production call before any host path is opened;
- symlinked, hard-linked, wrongly-modeled and duplicate sensitive material;
- untrusted or non-traversable parents and unsafe state/status leaves;
- invalid account identity and account changes during the scan;
- path escapes, nested writable paths, hidden state names and duplicate config
  keys;
- reused or oversized route keys;
- root replacement, release/unit changes after initial status validation,
  absent-link creation, same-target enablement-link replacement and in-place
  file mutation.

The smoke also publishes a durable prepared revision and proves that the
ordinary scanner rejects it while the live same-root capability succeeds. It
then rejects forged capability subtypes, capability reuse after close,
cross-root reuse, and a valid journal intent appended during the scan. It checks
bounded error output, omitted exception chains, descriptor cleanup, an exact
wheel-module inventory and the absence of a scanner CLI. It runs in the Python
3.10/3.11 compatibility matrix and in the deterministic backend job.

## Truth Boundary

This scanner proves only one read-only host observation. The locked variant
also proves that the lifecycle lock, root binding, and journal snapshot remained
unchanged for that observation. It does not:

- call `systemctl`, parse live systemd state or expose a preview command;
- expose the lifecycle capability outside private activation/recovery code;
- write transaction markers or mutate daemon reload, enablement or service
  state;
- provision accounts, configuration, TLS material, route keys, DNS or ACME;
- prove a real Linux systemd activation, public Relay deployment or physical
  browser reachability.

The returned snapshot contains private hashes and numeric identities and must
remain inside the plan compiler. A later confirmed transaction must rescan and
recompile the exact plan immediately before its first mutation.
