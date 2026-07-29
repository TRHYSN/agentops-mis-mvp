# Relay Config Parser Acceptance

## Scope

This acceptance covers the shared Relay daemon configuration parser in
`agentops_mis_cli.relay_daemon`. The bytes parser validates one bounded,
credential-private JSON document without opening files, starting subprocesses,
using the network or writing state. The existing bounded file loader delegates
to the same parser.

```bash
python3 scripts/relay_config_parser_smoke.py
```

## Implemented Contract

- accept only non-empty `bytes` no larger than 64 KiB;
- accept bounded integer literals only and reject floats and non-standard
  numeric constants;
- reject duplicate JSON object keys recursively, including root, nested
  listener, TLS and route objects;
- require exact root and nested object key sets;
- require schema version to be the actual integer `1`, not a boolean;
- retain strict IP listener and non-boolean port validation;
- require every certificate, key, state and status path to be canonical,
  absolute ASCII with at most 4,096 characters;
- reject tilde expansion, double-root, dot, dot-dot, duplicate separators,
  trailing separators, controls, DEL and non-ASCII path spellings;
- preserve bounded error identifiers without rejected config values or paths;
- make `load_config` delegate to `parse_config_bytes` after its existing
  no-follow, regular-file, ownership, permission and size checks.

## Verification

The smoke uses synthetic configuration only. It verifies:

- four duplicate-key levels;
- sixty invalid path cases across certificate, private key, state, status and
  route-key fields;
- six direct bytes/type/parser-boundary rejections, including oversized
  integer, nesting, float and non-standard numeric inputs, and one
  boolean-schema rejection;
- three rejected listener, hostname and route values with no retained private
  validation exception chain;
- exact compatibility for a valid multi-listener, one-route configuration;
- shared behavior between direct bytes parsing and bounded file loading;
- bounded CLI failure output for duplicate-key and oversized-integer inputs,
  with a private canary omitted and no retained rejected-input exception chain;
- no socket or subprocess behavior and no writes outside a temporary fixture.

The parser smoke runs in the Python 3.10/3.11 compatibility matrix and in the
deterministic backend job. `relay_daemon_smoke.py` remains the separate runtime
regression for listeners, routing, epochs, forwarding and shutdown.

## Truth Boundary

This parser validates configuration syntax and bounded value shape only. It
does not:

- read certificate, private-key or route-key contents during bytes parsing;
- prove file kind, owner, group, mode, inode, link count or parent identity;
- prove that two configured paths reference distinct files;
- create an Activation Plan or bind parsed data to a plan hash;
- inspect or mutate systemd;
- provision accounts, files, credentials, TLS, routes, DNS or ACME;
- prove a deployed Relay or physical browser reachability.

The separately accepted FD-anchored scanner in
`RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md` now creates those host identities for
one read-only observation. Fresh confirmation, systemd state and the mutation
transaction remain pending.
