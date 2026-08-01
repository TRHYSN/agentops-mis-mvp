# Local Relay Daemon Acceptance

Date: 2026-07-24

Status: local deployable daemon code accepted; public deployment pending

## Scope

This slice replaces the single-route `LocalFakeRelay` as the only executable
Relay-side transport with a foreground, multi-route `agentops-relay` daemon
boundary. It is a non-authority L4 service: it owns no MIS database, Human
Session, task, run, approval, memory, evaluation, artifact, or Worker state.

It does not close the public deployment gate. DNS, ACME/public certificate
lifecycle, production route provisioning, cloud retention/monitoring, and a
physical stock-browser receipt remain external acceptance work.

## Implemented Boundary

- A bounded raw browser listener inspects only the TLS ClientHello.
- Exact normalized SNI selects one opaque route; there is no wildcard or
  default route.
- Connector traffic requires TLS 1.2 or newer and a unique, route-specific,
  exact 32-byte HMAC key.
- Each route has one current Host control owner and one Host-initiated data
  connection per browser connection.
- Active browser connections are globally capped at 256 and fail closed rather
  than queueing beyond capacity; route count is capped at 256 to remain inside
  the packaged service task budget.
- The consumed ClientHello is forwarded exactly once before opaque
  bidirectional bytes.
- Accepted control epochs are atomically persisted under a cross-process file
  lock before acknowledgement. Removed routes retain epoch tombstones, and a
  daemon restart or later route re-add therefore cannot let an old Host epoch
  reclaim a route.
- A nonblocking instance lock prevents two foreground daemons from owning the
  same persisted route namespace.
- Route keys are loaded only from separate absolute `0600` files. Inline
  credentials are not accepted by the configuration schema.
- Status requires both listener acceptors to remain alive and checks the PID
  plus a fresh heartbeat before reporting ready. It contains route counts,
  connection state, ports, PID, and coarse lifecycle fields only. It omits
  keys, hostnames, HTTP data, prompts, responses, transcripts, application
  payloads, and MIS records.
- SIGTERM closes listeners, controls, pending streams, and active streams, then
  writes `ready:false`.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/relay_tunnel.py \
  agentops_mis_cli/relay_daemon.py \
  scripts/relay_daemon_smoke.py
python3 scripts/local_fake_relay_tunnel_smoke.py
python3 scripts/relay_sni_router_smoke.py
python3 scripts/relay_daemon_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

`relay_daemon_smoke.py` uses loopback and temporary files only. It proves:

- simultaneous exact-SNI isolation for two Host connectors and two keys;
- fail-closed browser capacity and released connection slots;
- exact ClientHello delivery to the selected Host target;
- fail-closed unknown SNI and wrong route key;
- persistent stale-epoch rejection after daemon restart;
- cross-instance epoch preservation, removed-route tombstones and single-daemon
  ownership;
- fresh higher-epoch recovery;
- listener death clearing readiness;
- bounded shutdown during active forwarding, bounded SIGTERM shutdown, and
  payload-free status/output.

No local database is opened and no credential, prompt, response, private
message, transcript, or application body is retained.

## Product Claim Boundary

Allowed:

> The repository packages a locally verified, multi-route, non-authority Relay
> daemon and a credential-free service deployment contract.

Not yet allowed:

- deployed public Relay;
- ordinary browser-only remote Console ready;
- DNS or ACME automation complete;
- public certificate issuance, renewal, or rotation complete;
- route credential provisioning or revocation complete;
- `remote_ready:true`;
- physical second-device acceptance complete;
- transport exactly-once semantics.

The Host ledger and prepared-action layers retain their existing idempotency
contracts; the L4 transport itself does not claim exactly-once delivery.
