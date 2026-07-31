# Local L4 Relay Transport Acceptance

Status: 3B transport primitive and loopback composition accepted locally; deployed Relay remains pending

## Scope

This slice makes the browser-only Relay contract executable over temporary
`127.0.0.1` TCP connections without changing
the installed Host, Tailscale Serve, Funnel, router configuration, or any
network interface. It is a dependency-free, in-process loopback protocol test,
not a public endpoint and not a claim that ordinary browser-only onboarding is
complete.

The Relay envelope carries a route reference, connection epoch, monotonic
message ID, request correlation ID, direction, protocol version, and payload
byte count. The trusted connection context separately binds route, direction,
and epoch. Event evidence omits both untrusted references entirely. Application
bytes are forwarded unchanged and are never included in Relay event evidence.
The Relay has no task, run, approval, memory, artifact, user, or audit authority
database.

## Executable Contract

Run:

```bash
python3 scripts/local_l4_relay_transport_smoke.py
python3 scripts/private_host_relay_tls_smoke.py
python3 scripts/local_fake_relay_tunnel_smoke.py
python3 scripts/relay_tunnel_single_owner_smoke.py
python3 scripts/relay_persistent_epoch_smoke.py
python3 scripts/relay_tls_authenticated_tunnel_smoke.py
python3 scripts/relay_host_tls_proxy_smoke.py
python3 scripts/relay_connector_service_smoke.py
python3 scripts/private_host_relay_lifecycle_smoke.py
python3 scripts/local_relay_connector_supervisor_smoke.py
```

The smoke proves:

- exact opaque bytes travel Console-to-Host and Host-to-Console;
- a new connection epoch restarts the message sequence at one;
- duplicate messages and request IDs inside the retained replay window, stale
  epochs, sequence gaps, spoofed connection context, and oversized frames fail
  closed;
- destination write failures become bounded `destination_unavailable` evidence
  without recording socket errors or application bytes; because `sendall` may
  have delivered a prefix, the failed epoch is invalidated and only a fresh
  connection epoch may continue;
- control metadata is capped at 4 KiB and payload frames at 256 KiB;
- reads and writes use a transport-enforced socket deadline and no unbounded
  in-process payload queue;
- replay state is capped at 256 directional streams, request replay history at
  4,096 references, and the bounded in-memory event buffer at 256 metadata
  records; route release recovers replay capacity, refuses active reservations,
  and none contains application bytes or correlatable route/request references;
- only temporary `127.0.0.1` listeners are opened; no durable state is written
  and no Tailscale command is invoked.

The second, separate raw-proxy fixture performs a real TLS handshake through a
byte-forwarding Relay.
The temporary certificate and private key are loaded only by the Host TLS
endpoint. The client verifies the exact Host certificate fingerprint, the Host
and client verify exact binary request/response bytes, and Relay evidence is
restricted to bounded direction/status/byte-count records. This proves the
fixture Relay did not terminate that TLS session or retain application
plaintext, payload hashes, certificate fingerprints, key material, or paths.
It does not yet compose TLS with the framed tunnel primitive or prove an
outbound Host-initiated connector.

`scripts/local_fake_relay_tunnel_smoke.py` then composes the boundary with a
Host-initiated control connection and one Host-initiated data connection per
browser connection. The fake Relay authenticates bounded control frames,
rejects unknown routes, bad MACs, stale epochs, registration replay and data
connection replay. A replacement control epoch cannot claim a pending browser
created by the previous epoch. A Host data connection is marked authenticated
only after a bounded Relay acknowledgement. Unauthenticated connector
handshakes are capped and are actively closed during Relay shutdown. The
browser completes a real TLS handshake with
the Host endpoint through the opaque data connection. Two successive connector
epochs reuse the same Host TLS process and preserve exact binary
request/response bytes and the Host certificate fingerprint. Relay evidence
remains limited to allowlisted status, direction and byte-count fields;
forwarding failure cannot be recorded as successful forwarding.

`scripts/local_relay_connector_supervisor_smoke.py` adds a disabled-by-default,
in-process supervisor around the Host connector. It proves deterministic
bounded backoff, strictly increasing process-lifetime epochs, recovery after a
forced control-connection loss, a second real Host-terminated TLS round trip,
bounded allowlisted status history, and bounded permanent stop. The supervisor
accepts only literal loopback test endpoints and owns no listener, OS service,
network configuration, persistent credential, or Tailscale state.

`scripts/relay_persistent_epoch_smoke.py` adds the crash-safe epoch primitive
needed by that supervisor. The allocator writes the next epoch before network
use under an exclusive file lock, atomically replaces a `0600` schema-bound
state file inside a `0700` directory, fsyncs both file and directory, and
rejects corruption, connector mismatch, broad permissions and symlinks. The
state stores only a hash-derived connector reference and the last epoch; it
omits endpoint, route, key material and filesystem paths. A restarted allocator
and 16 concurrent allocators cannot reuse an epoch. The supervisor integration
uses this allocator for reconnects and reports only that crash persistence is
enabled, never the state path or connector identity. Corrupt or mismatched
state stops before network connection with only the bounded
`epoch_allocation_failed` status code.

`scripts/relay_tls_authenticated_tunnel_smoke.py` adds the production tunnel
authentication shape without deploying an endpoint. Plain connector transport
remains restricted to literal loopback. A non-loopback-capable connector must
receive a caller-owned TLS context with certificate and hostname verification.
The loopback fixture proves an untrusted Relay certificate fails before HMAC
registration, a trusted TLS connection still rejects the wrong Host HMAC key,
and both control and per-browser data connections use Relay-authenticated TLS.
Inside that outer tunnel, the browser verifies a different Host application
certificate and completes an exact binary round trip with TLS terminating at
the Host endpoint. Relay evidence remains payload-, key-, certificate-, path-,
hostname- and port-free.

`scripts/relay_tunnel_single_owner_smoke.py` makes the data-stream concurrency
boundary deterministic. Both directions now share one nonblocking owner pump
with a 256 KiB buffer bound per direction and a resettable five-second inactivity
deadline. The same thread performs every read, write, half-close and final close
for each stream; a stop event is observed on a 100 ms poll bound before an
external fallback close is permitted. The fixture proves exact full-duplex bytes,
truthful bounded metadata, bounded stop and zero cross-thread stream access. The
authenticated nested-TLS fixture additionally passes repeated round trips on
this pump. A TLS stream is never subjected to `SHUT_RDWR`; successful shutdown
queues `close_notify` from the owning thread, avoiding the Linux/OpenSSL
`SSLEOFError` race caused by concurrent read/write/shutdown ownership.

`scripts/relay_host_tls_proxy_smoke.py` adds the Host-owned application TLS
boundary as a reusable loopback-only component. It binds only literal
`127.0.0.1`, requires TLS 1.2 or newer and an exact expected SNI hostname, and
forwards accepted connections only to a literal loopback Host HTTP port. A
real HTTP request reaches the backend through this proxy, wrong SNI fails during
the TLS handshake, and a bounded TLS-aware byte pump sends the server
`close_notify` without applying unsafe plain-socket half-close semantics to the
TLS stream. Fixed failure-stage counters distinguish handshake, backend-connect
and forwarding failures without exception text. Bounded status omits hostname,
port, certificate path and application bytes, and stop owns only its listener
and accepted connections, including sockets that have not completed
ClientHello. An unexpected accept-loop exit clears readiness and becomes a
bounded failure instead of leaving a false-ready proxy.

`scripts/relay_connector_service_smoke.py` runs the connector as a real
foreground subprocess suitable for later Host/LaunchAgent ownership. Its
strict `0600` config/secret inputs and atomic `0600` status/epoch outputs live
under `0700` directories. Disabled configuration exits without reading a
secret or attempting a connection; invalid secrets fail before network use.
The enabled service now owns the loopback Host TLS proxy as well as the Relay
connector. It establishes nested TLS, forwards real HTTP to the loopback Host,
rejects wrong browser SNI, survives a forced Relay restart with a higher
persisted epoch, completes a second browser-to-Host round trip, and stops both
layers cleanly on `SIGTERM`, including an in-flight browser connection that has
not sent ClientHello. Status and process output omit endpoint, route,
key, certificate path, hostname, port and filesystem paths. The schema-1
configuration shape now makes the Host HTTP port, Host TLS listener,
certificate/key paths and expected Host hostname explicit; the unchanged
schema preserves secret and epoch compatibility, while an old
external-TLS-target shape fails with an explicit upgrade-required boundary
rather than being silently reinterpreted.

TLS inputs must be regular owner-owned files in an exact `0700` owner
directory; the private key must be `0600`, and the Host certificate must
contain the exact configured DNS SAN. A nonblocking process lock prevents a
second service from overwriting the active instance's status. A pre-existing
protected epoch file continues with a strictly higher value under the new Host
TLS composition.

`scripts/private_host_relay_config_smoke.py` adds the first Host CLI ownership
boundary without starting the connector. New Host initialization creates only
the exact disabled schema-1 Relay config in a `0700` directory with a `0600`
file. Existing Hosts with no Relay config remain implicitly disabled. `agentops
host status` projects only bounded configuration state; it never reads Relay
secrets or trusts a stale service status file, and never returns Relay endpoints,
routes, certificate paths or configuration values. `agentops host doctor`
passes absent/disabled Relay state and fails closed for broad permissions,
malformed state or any enabled connector that is not yet Host-managed. An
enabled config remains `enabled_unmanaged`; a separately running connector or
stale status file cannot create Host-level readiness.

`scripts/private_host_relay_lifecycle_smoke.py` adds actual source-level Host
process ownership without enabling the safe default. `agentops host start` gives
the managed stack all four private Relay paths. An absent or exact disabled
configuration starts no connector process, makes no network attempt and writes
no service status. An explicitly enabled configuration is validated before
startup, must point back to the exact managed loopback backend port, and starts
one connector child only after the backend is ready. The stack requires a fresh
private status plus a one-byte readiness signal inherited from that exact child,
sent only after Host TLS, epoch and supervisor initialization. The connector
receives a minimal environment without Host API, admin, Owner or Human Session
credentials. Invalid configuration fails Host startup closed. Host
restart reaps the old connector and starts a new child; Host stop reaps the
owned tree within an 8-second stack bound while preserving an unrelated
process. A separately started, connected connector holding the exact managed
instance lock is neither adopted nor terminated; it blocks Host startup without
rewriting its status. The Host accepts backend health only after the connector
has allocated its first durable epoch and the complete stack sends its own
inherited readiness signal. The lifecycle smoke observes the real child process
environment rather than only testing the projection helper.
`agentops host relay-preflight` separately validates pre-provisioned private
material and the exact Host backend port while leaving the active config at the
disabled default; it does not bind a socket, contact Relay or change Tailscale.
The Host runtime projection additionally requires a verified Host process
identity, one direct managed connector child and a private post-start status;
it exposes local runtime health while keeping `remote_ready` false because this
fixture is not a deployed Relay.
The fixture observes no
Tailscale invocation and uses an unavailable loopback fake Relay, so it proves
lifecycle/backoff ownership rather than a deployed remote endpoint.

`scripts/relay_registration_publication_race_smoke.py` deterministically pauses
the Relay immediately after writing the registration acknowledgement. It proves
the same lock still blocks browser route lookup until control publication is
complete, then releases the barrier and completes an exact round trip. This
closes the Linux CI scheduling window where the Host could report connected
before the Relay route became usable.

## Boundaries

The frame smoke itself sends TLS-looking bytes and does not prove TLS; the
separate fixture proves only its own Host-side termination boundary. Unilateral
TLS half-close is explicitly not claimed because the stdlib `SSLSocket`
shutdown path used by this fixture would discard TLS wrapper state.

The composed connector remains a loopback fake Relay with a single route,
short test deadlines and an in-memory temporary tunnel key. It does not prove a
deployed service, internet routing, long-lived browser sessions, TLS half-close
or transport exactly-once delivery.

The source Host stack now owns the foreground service when a strict private
configuration is explicitly enabled; disabled and legacy-unconfigured Hosts do
not start it. Preview.42 packages this lifecycle slice, while its real installed
Host remains safely unconfigured and starts no Relay connector. Host
status/doctor still conservatively refuse to turn a service status file into
remote readiness. The service owns local Host TLS termination, but it does not
generate, renew or rotate the supplied certificate and key. Its
epoch can now be crash-persistent when the protected allocator is supplied,
while backoff/status state remains process-local. This proves the reconnect and
epoch identity behavior needed by the Host-owned connector, not the deployed
Relay itself.

This slice validates exact Host SNI at TLS termination but does not yet
implement Relay-side SNI parsing/routing, production Host-generated
certificates, DNS/ACME coordination, a deployed Relay daemon,
multi-Host routing, certificate rotation, or a stock-browser public endpoint.
It also does not claim crash-proof exactly-once byte delivery; product-level
idempotency remains at the Host ledger/action layer.
Those remain 3C work. The current Tailscale Serve path remains available as the
advanced private-network profile and is intentionally untouched.

## Next Slice

Add an exact-definition, rollback-aware managed Host restart worker after the
now-implemented Owner confirmation-gated enable/disable transition. Then add
private certificate provisioning, SNI/multi-Host routing and deploy the same
authority-free boundary behind a stable domain for 3C physical browser
acceptance.
