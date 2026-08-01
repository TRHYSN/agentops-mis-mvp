# Local Relay SNI Router Acceptance

Date: 2026-07-18

## Scope

This acceptance covers a dependency-free, loopback-tested L4 ClientHello SNI
router suitable for a future Relay boundary. It is intentionally isolated from
the current Host, connector, server, configuration, database, CI, and installed
state.

The slice:

- reads only the first bounded TLS ClientHello records;
- normalizes one exact ASCII DNS SNI name to lower case and removes one terminal
  dot;
- selects one pre-registered opaque route reference;
- returns the exact consumed TLS preface for forwarding;
- never terminates TLS, parses HTTP, or reads/stores later application bytes;
- has no default route, wildcard route, or route-enumeration API;
- returns only fixed, non-enumerating error codes.

This is not a deployed Relay, listener, proxy, DNS/ACME implementation,
certificate lifecycle, or browser-only Console receipt.

## Files

- `agentops_mis_cli/relay_sni_router.py`
- `scripts/relay_sni_router_smoke.py`
- `docs/LOCAL_RELAY_SNI_ROUTER_ACCEPTANCE.md`

No existing source, CI, configuration, database, or installed file was changed.

## Fixed Limits

| Boundary | Limit | Failure behavior |
|---|---:|---|
| ClientHello wire bytes | 16,384 bytes, including TLS record headers | `client_hello_too_large` |
| ClientHello TLS records | 8 | `client_hello_too_large` |
| Default whole-inspection deadline | 2 seconds | `client_hello_timeout` |
| Configurable deadline range | 0.05 to 10 seconds | invalid construction rejected |
| Route table size | 4,096 exact hostnames | invalid construction rejected |
| Default concurrent inspections | 128 | excess fails immediately with `router_busy` |
| Configurable concurrent range | 1 to 128 | invalid construction rejected |
| DNS hostname | 253 ASCII bytes; labels 1 to 63 bytes | invalid construction/SNI rejected |
| Opaque route reference | 1 to 96 ASCII identifier characters | invalid construction rejected |

Hostnames reject IP literals, wildcards, empty labels, non-ASCII input, and
normalized duplicates. Route references reject path separators and duplicate
ownership. The route map is immutable after construction.

## Privacy And Authority Boundary

The router stores only the minimum routing map: normalized SNI hostname to an
opaque route reference. It stores no MIS database, task/run/memory/artifact
body, Host filesystem path, TLS key, cookie, pairing secret, credential, raw
prompt, or response. It has no event or payload recorder. The returned preface
is ephemeral caller-owned data and is hidden from object representations.

Unknown and malformed traffic receives a fixed code that does not reveal the
requested hostname, registered hostnames, route references, or nearest match.
There is no catch-all behavior.

Backpressure is bounded by a non-queuing inspection semaphore and an absolute
per-connection deadline. Route selection is stateless and deterministic, so a
repeated ClientHello cannot mutate routing authority. Cryptographic replay,
epoch, reconnect, and duplicate-delivery enforcement remain responsibilities of
the existing authenticated Relay tunnel protocol; ClientHello SNI contains no
safe unique replay identity and this slice does not pretend otherwise.

## Local Smoke Evidence

Commands run from the repository root:

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_sni_router.py \
  scripts/relay_sni_router_smoke.py
python3 scripts/relay_sni_router_smoke.py
git diff --check
```

Result:

- Python compilation: pass.
- Real TLS ClientHello generation through `ssl.MemoryBIO`: pass.
- Two exact Host SNI routes select two distinct route references: pass.
- Repeated inspection remains deterministic and non-mutating: pass.
- Unknown SNI: fail closed with `route_not_found`.
- Missing SNI: fail closed with `sni_required`.
- Structurally malformed ClientHello: fail closed with
  `malformed_client_hello`.
- Oversized declared TLS record: rejected before its body is read with
  `client_hello_too_large`.
- Partial ClientHello: bounded by the configured deadline with
  `client_hello_timeout`.
- Exhausted inspection capacity: rejected immediately with `router_busy`.
- Bytes following the complete TLS record remain unread and absent from the
  selected preface/router representation: pass.
- Loopback-only fixture: pass.
- Diff whitespace check: pass.

The smoke emits `"deployed_relay": false` and makes no network or product
deployment claim.

## Known Limitations And Next Integration Boundary

- This parser supports a ClientHello fragmented across up to eight TLS handshake
  records, but intentionally rejects non-handshake records before the complete
  ClientHello.
- Encrypted ClientHello without a usable outer SNI cannot be routed by this
  design and fails closed.
- This slice does not accept sockets, publish DNS, provision certificates,
  authenticate Host tunnels, proxy selected streams, collect operational
  metrics, or manage route registration lifecycle.
- A later integration must bind each opaque route reference to exactly one
  authenticated, current Host tunnel and preserve the existing epoch/replay and
  bounded forwarding controls.
- Deployed Relay and physical second-browser acceptance remain required before
  any remote-ready or production claim.
