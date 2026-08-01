# AgentOps MIS Local Host + Remote Console Product Spec

Status: implementation baseline; browser-only transport amendment accepted
Target: v1.6 local private host
Scope: single-owner or trusted small-team deployment

Transport amendment (2026-07-17): ordinary Console users must not install,
join, or understand Tailscale. The default product path is a browser-only
Console reached through an AgentOps-managed or customer-hosted outbound Relay.
Tailscale Serve remains a supported advanced private-network profile and a
release fallback, but it is no longer the target first-run experience.

## 1. Product Goal

Turn AgentOps MIS into a two-surface local product:

- one trusted computer runs the MIS control plane, SQLite ledger, knowledge
  index, project files, Hermes/OpenClaw adapters, and worker daemons;
- another computer uses only a modern browser, with no VPN client or AgentOps
  installation, to create tasks,
  supervise work, approve actions, review evidence, and download approved
  deliverables.

The remote console is not an Agent runtime and does not receive the host's
model credentials, knowledge corpus, database, raw prompts, or raw responses.

## 2. Current Baseline

The current `v1.0.0-local` release provides a source release and a foreground
one-command local stack. It is suitable for a developer-operated loopback
installation, but it is not yet a consumer installer or remote-console product.

Known baseline constraints:

- `scripts/run_local_stack.py` intentionally rejects a non-loopback backend;
- the beta React UI is run through the Vite development server;
- non-loopback backend mode has a fail-closed API/admin-key gate, but there is
  no complete human browser login/session flow;
- the GitHub release currently has no packaged `.dmg`, `.pkg`, `.zip`, or
  signed installer asset;
- Hermes/OpenClaw installation and model provisioning remain host-side manual
  prerequisites.

These constraints must not be bypassed by simply binding the current developer
stack to `0.0.0.0`.

## 3. Deployment Model

```text
Remote browser console
  -> stable per-Host HTTPS origin
  -> Relay L4/SNI routing (TLS remains opaque)
  <- Host-initiated authenticated tunnel
  -> AgentOps MIS host entrypoint
       -> human session and authorization
       -> Python control plane and Agent Gateway
       -> SQLite authority ledger
       -> Markdown + FTS knowledge index
       -> artifact store
       -> Hermes/OpenClaw worker daemons
```

### Host computer

The host owns and executes:

- workspace, actor, task, run, tool-call, approval, evaluation, memory, and
  audit state;
- local knowledge documents and indexes;
- project working directories and approved artifacts;
- runtime connectors, worker processes, runtime credentials, and secrets;
- backup, restore, retention, and update operations.

### Console computer

The console may:

- sign in and select an authorized workspace;
- create, edit, dispatch, pause, cancel, and review tasks;
- select an available team template or runtime adapter;
- observe workers, jobs, runs, events, evaluations, and health;
- approve or reject prepared actions, memories, and deliverables;
- download explicitly approved artifacts and reports.

The console must not:

- run Hermes/OpenClaw merely by loading the page;
- receive runtime API keys, enrollment tokens, database files, or unrestricted
  filesystem paths;
- read raw prompts, raw responses, private messages, or full transcripts;
- directly mutate the ledger outside authenticated MIS APIs;
- become an alternative authority store.

## 4. Product Modes

### Local workstation mode

- Default mode.
- Binds to loopback only.
- Preserves the current safe mock/live-confirmation behavior.
- Does not require remote access configuration.

### Easy remote console mode

- Recommended product path for an ordinary second computer.
- The Host opens an outbound, mutually authenticated connection to an
  AgentOps-managed or customer-hosted Relay; it never opens an inbound router
  port.
- The Console requires only a browser at a stable per-Host HTTPS origin. It does not
  install Tailscale, AgentOps MIS, Git, Python, Node, Hermes, or OpenClaw.
- First access uses a short-lived, one-time pairing invitation created by an
  authenticated Host Owner and approved by the Host. The invitation is not a
  reusable password or machine credential.
- The Relay routes opaque TLS bytes and bounded connection metadata. It is not
  the task, run, approval, memory, knowledge, artifact, or audit authority.
- Loss of the Relay or browser connection never stops a claimed Host task;
  reconnect resumes observation of the same task and run.

### Advanced private-network mode

- Explicit opt-in through a separate `agentops host` command or equivalent
  launcher profile.
- Serves the production-built React console and APIs from one origin.
- Remains loopback behind a user-managed private-network proxy such as
  Tailscale Serve.
- Requires human authentication before any workspace data is returned.
- Requires explicit confirmation before enabling live Hermes/OpenClaw workers.
- A Host-owned Worker start must first prove that no same-adapter local Worker
  process already exists. A conflict fails closed with adapter and PID evidence
  only; process commands and credentials remain omitted.
- Operators may intentionally keep independently service-managed Workers and
  start the Host with `--no-workers`. The Host never kills or unloads an
  existing Worker automatically.

### Full hosted/public mode

Out of scope for this version. The narrow Relay transport in Easy remote
console mode does not make the Relay an authority database or general SaaS.
Multi-tenant MIS storage, billing, tenant provisioning, enterprise SSO,
anonymous enrollment, hosted model execution, and hosted knowledge storage
require a separate commercial security architecture.

## 5. Network Strategy

### Recommended product path: outbound AgentOps Relay

The Host remains on loopback and establishes an outbound tunnel to a Relay.
Each Host receives a stable, non-secret HTTPS hostname. The Relay routes by SNI
at layer 4 and does not serve the Workspace JavaScript or terminate the Host's
application TLS. TLS terminates at the Host using a Host-generated private key
and a publicly trusted per-Host certificate. Certificate provisioning and
renewal may coordinate through AgentOps DNS/ACME infrastructure, but the Host
private key is never uploaded. A Console user must not need to learn network
topology, copy a tailnet DNS name, or install a network client.

The transport must satisfy all of the following:

- no automatic router port forwarding and no default `0.0.0.0` binding;
- Host identity is bound to the per-Host origin during pairing and every tunnel
  reconnect is mutually authenticated;
- one-time pairing invitations are hashed at rest, expire, are single-use, are
  role-scoped, and can be revoked before use;
- paired Console devices and Human Sessions can be listed and revoked by an
  Owner;
- browser application payloads stay inside Host-terminated TLS; the Relay
  cannot persist or query normal application plaintext;
- Relay logs omit raw prompts, responses, knowledge text, artifact bodies,
  cookies, CSRF values, invitation secrets, and Host filesystem paths;
- backpressure, duplicate delivery, reconnect, and replay are fail-closed and
  preserve the Host ledger as the only authority;
- a Relay outage degrades remote access without corrupting or cancelling Host
  work.

Relay control of routing, DNS, and certificate coordination is an explicit
availability and endpoint-identity trust boundary. A compromised Relay may
deny or misroute traffic, while DNS/CA compromise may impersonate an endpoint;
these residual risks require monitoring, certificate transparency/rotation,
and a customer-hosted advanced profile. They must not be hidden behind an
"opaque Relay" claim.

CI may use a local fake Relay for deterministic protocol tests, but a product
claim requires a deployed HTTPS Relay and a physical browser-only Console
receipt.

The Relay-side routing boundary has dependency-free local acceptance in
`LOCAL_RELAY_SNI_ROUTER_ACCEPTANCE.md` and
`LOCAL_RELAY_DAEMON_ACCEPTANCE.md`. It reads only a bounded TLS ClientHello,
maps one exact normalized SNI hostname to one opaque route reference, preserves
the consumed preface for forwarding, and fails closed for unknown, missing,
malformed, oversized, timed-out, or capacity-exhausted input. The foreground
daemon binds that route to the current route-key-authenticated Host control,
persists accepted epochs before acknowledgement, and forwards opaque bytes
without an HTTP parser or application-payload recorder. It has no wildcard or
default route and it is not an MIS authority. This is deployable local code and
isolation evidence, not a public deployment, DNS/ACME lifecycle, production
credential-provisioning, physical-device, or remote-ready claim.

### Advanced path: Tailscale Serve

The Host may remain on loopback behind Tailscale Serve for customers who prefer
a user-managed private network or require a no-vendor-relay deployment. This
mode keeps Funnel disabled. It is an advanced setup profile, not the ordinary
Console onboarding path, and its client installation cannot satisfy the
browser-only acceptance gate.

### Optional trusted-LAN path

A LAN binding may be added only after the same human authentication, CSRF,
origin checks, rate limits, and fail-closed startup checks pass. The launcher
must require an explicit `--allow-lan` confirmation and display a warning that
LAN is not equivalent to public hosted mode.

### Prohibited default

- no automatic router port forwarding;
- no default `0.0.0.0` binding;
- no unauthenticated remote dashboard;
- no API key embedded in query strings or generated console URLs.

### Owner Relay control contract

Easy remote console activation is a two-stage Owner action over the existing
Human Session boundary:

```http
GET  /api/host/relay
POST /api/host/relay/transitions
POST /api/host/relay/transitions/{transition_ref}/confirm
```

The first POST accepts only `{"action":"enable"}` or
`{"action":"disable"}` and returns a short-lived, single-use transition ref.
The confirmation POST must repeat the same action. Both POSTs require exact
Origin/Host validation and the Human Session CSRF token. All three routes require
the `owner` role; Agent Gateway, Host machine, admin-key and other bearer
credentials never authorize this browser control surface.

Prepare binds the exact active/prepared Relay config, tunnel secret, CA, Host
certificate/private-key material and relevant Host config to a private digest.
Confirm revalidates the digest, expiry and action before an atomic config
transition. The digest, filesystem paths, hostnames, ports, route, certificate
material and machine credential never enter the HTTP response, audit metadata or
browser storage. A transition is replay-safe and a failed second config write
restores the first write before reporting failure.

This control does not generate certificates, register DNS, provision Relay
credentials or prove a deployed Relay. Enabling is refused while another network
publication profile owns the Host. Runtime status may report a healthy local
connector, but `remote_ready` remains false until a deployed Relay and physical
browser-only receipt exist.

The managed activation and failure-recovery contract is defined in
`PRIVATE_HOST_MANAGED_RESTART_SPEC.md`. The exact managed LaunchAgent parent is
the only automatic restart authority; a manual foreground Host remains manual.
The Owner response must be fully written and flushed before a restart request,
and a broken response must restore the original config without signalling a
process.

## 6. Application Serving Model

Private host mode must use a production UI build, not the Vite development
server. The preferred v1 implementation is same-origin serving:

```text
https://<private-host>/                 React console
https://<private-host>/api/...          MIS API
https://<private-host>/health           redacted health
```

Requirements:

- browser routes use SPA fallback without swallowing `/api/*` errors;
- API calls use relative URLs by default;
- no CORS wildcard is required for the standard console path;
- static files use content hashes and conservative cache headers;
- health endpoints expose no token, secret, raw content, or private path;
- WebSocket or SSE, if added later, uses the same authenticated session.

## 7. Human Authentication and Authorization

The existing Agent Gateway token is a machine credential and must not be reused
as the normal browser credential.

Minimum private-host authentication:

- one bootstrap owner created locally on the host;
- one-time setup code displayed only in the host terminal;
- the managed installer/launcher may hand that code to the literal-loopback
  browser through a fragment that is never sent in HTTP and is immediately
  removed from the address bar; server bootstrap still requires the code;
- password or passkey enrollment after first login;
- server-side, expiring, revocable browser session;
- `HttpOnly`, `Secure`, and `SameSite` cookie when served over HTTPS;
- CSRF protection for state-changing browser requests;
- audit events for login, logout, failed login, session revoke, approval, task
  dispatch, artifact download, and administrative configuration change;
- roles at minimum: `owner`, `operator`, `approver`, and `viewer`;
- workspace authorization enforced server-side, never only hidden in the UI.

Machine workers continue to use scoped enrollment/session tokens bound to
`agent_id`, `workspace_id`, expiry, and permission scopes.

Host-wide Worker telemetry is a separate machine-operator surface. Browser
pages continue to read `/api/workers/*` with a Human Session. The packaged Host
CLI reads `/api/agent-gateway/host-workers/*` with the Host machine credential.
Agent-bound enrollment and Session tokens must fail closed on these Host-wide
routes even when they include `tasks:read`; they can use only scoped Agent
Gateway task/run/evidence routes. Rejected Host telemetry reads must not update
the bound credential's usage timestamp or write ledger/audit rows.

Host and independently service-managed Workers are two explicit ownership
models. `agentops host start --worker <adapter>` owns the selected Worker
processes as children of the Host stack. `agentops host start --no-workers`
owns only the API/UI Host and leaves external Workers untouched. Mixing both
models for the same adapter is rejected before the backend port opens.

## 8. Data and Privacy Boundary

By default, all durable state remains on the host:

- SQLite database;
- knowledge source files and index;
- runtime credentials;
- worker logs;
- project worktrees;
- generated artifacts before explicit download.

The remote browser may cache only static application assets and an opaque
session cookie. Sensitive responses should use `Cache-Control: no-store`.
Artifact download must be an authenticated, audited, ID-based operation; the
browser must never receive arbitrary host paths.

Raw prompts, raw model responses, credentials, private messages, and full
transcripts remain excluded from committed project state and ordinary MIS
ledger records.

## 9. Operator Experience

### Host first run

Target flow:

```bash
curl -fsSL <verified-release-installer> | sh
install-agentops-mis-private-host.sh --tag <tag> --init --start
```

The initializer performs preflight checks, creates local runtime directories
with restrictive permissions, builds or installs the production UI, creates the
owner setup code, prints the local URL, and opens the local Console with a
memory-only setup-code handoff when a graphical macOS session is available.
The browser immediately scrubs the fragment and performs explicit Owner
creation inside the existing Workspace shell. It must not install or enable
Hermes/OpenClaw without a separate explicit action. CLI bootstrap remains a
headless/recovery path.

### Console first run

Target flow:

1. On the Host, open the literal-loopback Console and establish the first Owner.
2. On the Host, choose **Enable remote Console** and create a one-time pairing
   invitation for a non-Owner role. Pairing may provision that invited account
   or bind an existing account, but it never bootstraps an Owner or reuses an
   Agent Gateway credential.
3. On the second computer, open the stable HTTPS Console URL in a browser. No
   Tailscale or other VPN installation is part of this flow.
4. Complete pairing and sign in; consequential approval remains a human action.
5. See host, ledger, knowledge index, and worker readiness.
6. Dispatch a safe task or explicitly confirm a live runtime task.

No Git, Python, Node, repository clone, or Agent runtime is required on the
console computer.

### Host lifecycle commands

Proposed commands:

```text
agentops host init
agentops host start
agentops host stop
agentops host restart
agentops host status
agentops host logs
agentops host doctor
agentops host backup
agentops host update --check
agentops host console-url
agentops host session revoke
```

Commands that install services, change network exposure, enable live workers,
restore data, or update binaries remain preview-first and confirmation-gated.

## 10. Runtime and Knowledge Behavior

- Hermes/OpenClaw execute only on the host or an explicitly enrolled remote
  worker machine, never in the browser console.
- A console task is written to MIS first, then claimed through Agent Gateway.
- Every product claim about real execution requires run, runtime-event,
  evaluation, artifact, plan-evidence, and audit records.
- Live runtime remains opt-in and visibly labeled `live`, `dry-run`, `mock`,
  `waiting approval`, or `unavailable`.
- Knowledge retrieval runs on the host and returns bounded evidence identifiers
  and citations; ordinary console views do not return the entire corpus.
- Approved memories enter the host authority store only after the configured
  human/evaluation gate.

## 11. Packaging and Update Contract

The first distributable may be an unsigned developer preview, but its status
must be explicit. A release is not considered packaged merely because GitHub
automatically provides source archives.

Minimum release assets:

- versioned host bundle or installer for macOS;
- managed user-level macOS launcher for opening the existing browser Console;
- SHA-256 checksum file;
- software bill of materials and third-party notices;
- install, uninstall, backup, restore, and upgrade instructions;
- clean-machine acceptance evidence;
- release provenance tied to an exact Git commit;
- no DB, `.env`, token, cache, `node_modules`, `dist`, or local runtime logs.

Updates must preserve the database by default, run schema preflight, create a
verified backup before migration, and support rollback to the previous binary
and schema-compatible state.

## 12. Functional Acceptance

The private-host slice is accepted only when all items pass:

1. A clean host can install from a versioned release asset without cloning the
   repository manually.
2. `agentops host start` starts the production UI, API, ledger, knowledge index,
   and selected workers or reports an actionable failure.
3. A second computer with no project dependencies or private-network client can
   open the stable HTTPS Console URL, pair, and authenticate using only a
   browser.
4. Unauthenticated API and UI data requests fail closed.
5. The remote console can create a task, observe claim/execution, approve a
   prepared action, review evaluation/audit evidence, and download an approved
   artifact.
6. Hermes or OpenClaw can execute one explicitly confirmed task on the host and
   write the complete bounded evidence chain to MIS.
7. Disconnecting the console does not stop the host worker or lose the task.
8. Restarting the host preserves ledger and knowledge state.
9. Backup and restore pass against an isolated test database.
10. No credential, database, raw prompt/response, private transcript, generated
    cache, or dependency directory appears in the release or Git diff.

## 13. Non-Goals

- automatic Hermes/OpenClaw/model installation in the first slice;
- full hosted MIS/SaaS authority storage or direct inbound Host exposure;
- multi-tenant billing and customer provisioning;
- complete enterprise RBAC or external identity provider integration;
- moving the knowledge corpus or model execution to the console computer;
- Notion/Dify live synchronization;
- mobile-native or Electron/Tauri desktop clients;
- a second native desktop UI; the macOS `.app` is only a managed launcher for
  the browser Console;
- universal interception of every internal runtime tool action.

## 14. Future Expansion

After private-host acceptance:

- optional guided Hermes/OpenClaw installation and health repair;
- signed/notarized macOS installer and background service;
- Windows/Linux host installers;
- PWA installation for the console;
- richer enterprise invitation policy and fleet-wide device administration;
- secret manager integration;
- multiple workspaces with stronger isolation;
- customer-hosted and vendor-hosted deployment profiles.
