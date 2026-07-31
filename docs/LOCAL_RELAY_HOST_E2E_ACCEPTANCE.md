# Local Relay + Host End-to-End Acceptance

Status: local topology accepted; public deployment and physical browser receipt
remain open

## Purpose

This slice composes the deployable multi-route Relay daemon with the existing
foreground Host connector service and Host-side TLS/HTTP proxy. It proves the
ordinary Remote Console transport shape without replacing the required public
deployment or second-device acceptance.

The exercised path is:

```text
certificate-verifying TLS client with SNI
  -> agentops-relay browser listener
  -> exact ClientHello SNI route
  -> authenticated TLS + HMAC Host connector
  -> Host-side TLS termination
  -> loopback Host HTTP backend
```

The Relay remains a non-authority L4 transport. The test does not start
`server.py`, open an AgentOps MIS SQLite database, perform a Runtime call, or
retain request/response bodies.

## Verification

Run:

```bash
python3 scripts/local_relay_host_e2e_smoke.py
python3 -m py_compile scripts/local_relay_host_e2e_smoke.py
git diff --check
```

Accepted gates:

- the real `agentops_mis_cli.relay_daemon` foreground process becomes ready;
- the real `agentops_mis_cli.relay_connector_service` establishes its
  authenticated route;
- a certificate-verifying TLS client with SNI reaches the Host HTTP backend
  through the complete local topology;
- TLS terminates on the Host side of the Relay;
- unknown browser SNI fails closed, does not change final Host TLS counters,
  and leaves a freshly observed Relay heartbeat ready;
- daemon and connector stop cleanly and clear readiness;
- child processes receive an allowlisted environment rather than ambient CI
  credentials;
- active/final status and process output omit route-key fragments, encoded key
  material, and application request/response canaries;
- an injected MIS database path is never created;
- the output explicitly declines public Relay, DNS/ACME, and physical
  second-device claims.

Wrong route-key, duplicate route-key, replay epoch, two-Host isolation,
connection-capacity and acceptor-failure gates remain covered by
`scripts/relay_daemon_smoke.py`. Packaging and service-install boundaries remain
covered by `scripts/relay_deploy_contract_smoke.py`.

## Evidence Boundary

This is deterministic loopback evidence. It does **not** prove:

- a public Relay VM or container is deployed;
- firewall rules, DNS, ACME, or certificate renewal work;
- route credentials can be provisioned, rotated, and revoked in production;
- Relay retention, monitoring, backup, upgrade, or rollback operations;
- a fresh second computer can use the Console with only a modern browser;
- a remote browser can observe a fresh real Hermes/OpenClaw run.

Those remain separate deployment and physical acceptance gates in
`LOCAL_HOST_REMOTE_CONSOLE_DELIVERY_PLAN.md`.
