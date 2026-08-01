# Browser-Only Remote Console Transport Decision

Status: accepted product direction; implementation pending

Date: 2026-07-17

## Decision

AgentOps MIS will not make Tailscale installation, tailnet membership, DNS
names, or Serve configuration part of ordinary Console onboarding.

The default product experience is:

1. Install AgentOps MIS once on the trusted Host.
2. An Owner selects **Enable remote Console** and creates a short-lived pairing
   invitation.
3. A second computer opens a stable HTTPS Console URL in a browser, pairs, and
   signs in.
4. Ledger, knowledge, projects, credentials, Workers, Hermes, and OpenClaw
   remain on the Host.

Tailscale Serve remains an advanced, user-managed private-network profile with
Funnel disabled. It is useful for technical or high-privacy deployments but is
not accepted as evidence of the ordinary zero-install experience.

## Why

Requiring software and account setup on both computers makes the Console feel
like infrastructure administration rather than a product. A Console user
should understand tasks, approvals, runs, evaluations, memory, audit, and
deliverables, not VPN topology.

Across the public internet, a browser-only second device necessarily needs a
publicly reachable rendezvous and routing component. The product therefore
absorbs that complexity into a narrow Relay instead of moving it to the user.

## Authority Boundary

The Relay is transport, not MIS authority.

- The Host remains authoritative for users, roles, tasks, runs, approvals,
  evaluations, memories, audit, knowledge, and artifacts.
- The Host initiates the connection; no inbound router port or default
  non-loopback Python binding is opened.
- Pairing binds a Console device, Host identity, workspace, and maximum role.
- Each Host has a stable non-secret hostname. The Relay routes by SNI at layer
  4; it does not serve Workspace JavaScript or terminate application TLS.
- TLS terminates at the Host with a Host-generated private key and publicly
  trusted per-Host certificate. The private key never leaves the Host.
- Relay storage is limited to bounded connection/provisioning metadata with an
  explicit retention policy.
- Raw prompts, model responses, knowledge content, artifact bodies, cookies,
  CSRF values, invitation secrets, runtime credentials, and Host paths are
  excluded from Relay logs and durable storage.
- Normal browser requests stay inside Host-terminated TLS so the Relay cannot
  query their content.
- Every state mutation is still authorized and audited by the Host.

The Relay operator necessarily controls routing and coordinates DNS/certificate
issuance. Relay compromise may deny or misroute service, and DNS/CA compromise
may impersonate an endpoint. Certificate rotation, transparency monitoring,
abuse controls, and a customer-hosted advanced profile mitigate but do not
erase those residual risks.

## Failure Model

- Relay unavailable: remote Console is unavailable; local Host and Workers
  continue.
- Browser disconnect: the claimed task continues; reconnect observes the same
  task/run without creating a duplicate.
- Replayed frame: rejected by connection epoch, monotonically checked message
  identity, and idempotency key.
- Revoked device: active sessions are invalidated and tunnel requests from that
  device fail closed.
- Lost pairing invitation: it expires and can be revoked; it is never a
  standing password or Agent Gateway token.

## Delivery Boundary

CI may exercise a local fake Relay, but product acceptance requires all of:

- a deployed L4 Relay at a stable per-Host Console origin with Host-side TLS;
- a physical second computer with only a browser;
- one-time pairing and later device revocation;
- a fresh real Hermes or OpenClaw task controlled from that browser;
- disconnect/reconnect without task cancellation or duplicate Run;
- approval, evaluation, memory, audit, approved artifact, and logout evidence;
- inspection showing the Relay did not persist prohibited content.

A public quick tunnel, Tailscale Funnel, LAN HTTP binding, or local browser
smoke cannot substitute for this acceptance.

## Required External Infrastructure

The first deployed slice needs a domain/TLS origin and a small server or
approved managed runtime for the Relay. Until that exists, pairing and transport
contracts can be implemented and verified locally, but browser-anywhere
product readiness must remain explicitly unproven.
