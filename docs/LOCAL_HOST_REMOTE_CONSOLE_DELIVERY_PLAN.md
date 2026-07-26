# Local Host + Remote Console Delivery Plan

This plan implements `docs/LOCAL_HOST_REMOTE_CONSOLE_SPEC.md` as small,
verifiable slices. It preserves loopback local mode while adding a separate,
fail-closed private-host mode.

The 2026-07-17 transport amendment makes an outbound Relay plus browser pairing
the ordinary Console path. Tailscale Serve remains an advanced fallback and
must not be counted as proof that a non-technical Console user has a zero-install
experience.

## Delivery Rules

- Keep `main` usable after every slice.
- Prefer one independently testable behavior per commit or PR.
- Do not expose the existing Vite development server as the product endpoint.
- Do not weaken live-runtime confirmation or Agent Gateway scope checks.
- Do not read or commit credentials, private messages, full transcripts, raw
  prompts/responses, databases, `.env`, caches, `node_modules`, `dist`, worker
  logs, or generated sample export drift.
- Use real Hermes/OpenClaw evidence for real-runtime acceptance when explicitly
  authorized and available; mock is only the offline/CI fallback and must be
  labeled.
- Continue another safe implementation, verification, docs, or packaging lane
  while builds, CI, live runtimes, or delegated checks are running.

## Lane Board

| Lane | Responsibility | Initial output |
|---|---|---|
| A. Host runtime | Production UI serving, lifecycle, health | same-origin host process |
| B. Human access | Login, session, CSRF, roles, audit | fail-closed browser auth |
| C. Console UX | Pairing, connection/setup/readiness and remote-safe UI | browser-only Relay path |
| G. Relay transport | Outbound Host tunnel, routing, replay safety and privacy | local fake Relay then deployed HTTPS Relay |
| D. Packaging | Bundle, checksums, installer, upgrade/rollback | versioned release asset |
| E. Runtime dogfood | Hermes/OpenClaw host execution and evidence | fresh real task receipt |
| F. Verification | clean-machine, remote-device, security, backup | acceptance packet |

Lanes A and B define the Host security boundary. C and G define the ordinary
remote critical path and can advance against stable API contracts; D can
prepare reproducible packaging in parallel; E and F integrate each usable
slice as it lands.

## Phase 0: Baseline and Contract Freeze

Deliverables:

- record exact branch, commit, CI state, dirty/generated drift, and release
  assets;
- add API/route inventory for the human console and Agent Gateway;
- classify routes as public health, human-session, machine-token, or local-only;
- add a threat-boundary checklist for private host mode;
- freeze the browser/Relay/Host envelope, pairing, replay, reconnect, and
  metadata-retention contracts;
- preserve current loopback one-command acceptance as a regression gate.

Verification:

```bash
git diff --check
python3 scripts/run_local_stack_smoke.py
python3 scripts/v1_5_local_product_acceptance.py --base-url http://127.0.0.1:8787
```

Exit gate: current local mode is green and the new mode has an agreed route and
credential contract.

## Phase 1: Production Host Entrypoint

Deliverables:

- add a production UI build step and same-origin static serving;
- add SPA fallback that excludes `/api/*` and health endpoints;
- add `agentops host init/start/stop/status/doctor` minimum lifecycle;
- keep runtime directories outside the repository with restrictive permissions;
- retain loopback binding by default;
- print redacted local and private-network connection information;
- add host process health and current-code/version evidence.

Verification:

- production UI build passes;
- host starts without `node_modules` at runtime;
- `/`, formal workspace routes, `/api/*`, and health routes resolve correctly;
- Ctrl-C and service stop terminate only owned processes;
- current `run_local_stack.py` behavior remains unchanged.

Exit gate: one host process can serve the production console and API locally.

## Phase 2: Human Session Security

Deliverables:

- add local bootstrap-owner initialization;
- make setup-code-authorized browser bootstrap the primary graphical path by
  handing the code from the managed installer/launcher through an immediately
  scrubbed fragment, while keeping CLI bootstrap for headless/recovery use;
- implement one-time setup code and password/passkey-ready account storage;
- use password hashing from a proven library, not custom cryptography;
- implement expiring/revocable server-side browser sessions;
- add non-enumerating login/pairing errors, attempt limits, cooldowns, bounded
  request bodies, exact Host/Origin validation, and proxy-header rejection;
- add CSRF and allowed-origin checks for state-changing browser requests;
- enforce owner/operator/approver/viewer permissions server-side;
- audit authentication, dispatch, approval, download, and admin actions;
- ensure Agent Gateway machine tokens remain a separate credential class.
- separate Host machine Worker telemetry from both Human Session routes and
  Agent-bound enrollment/Session tokens; rejected reads remain non-mutating.

Verification:

- unauthenticated reads and writes fail closed;
- expired, revoked, cross-workspace, and wrong-role sessions fail;
- CSRF and disallowed-origin smoke tests pass;
- no secret or session value appears in logs, URLs, health, or audit metadata;
- Host CLI Worker status/fleet/readiness/stuck reads succeed with the Host
  machine credential, while Agent-bound tokens fail closed without usage-state
  or ledger mutation;
- loopback developer mode has an explicit, tested compatibility policy.
- no-code Owner bootstrap remains rejected, browser handoff values do not enter
  argv/output/audit/HTTP requests, and concurrent first-owner requests create
  exactly one Owner Session.

Exit gate: a remote browser cannot read workspace data without a valid human
session and cannot perform actions outside its role.

## Phase 3: Browser-Only Remote Console

Deliverables:

- add one-time, expiring, single-use Owner-created Console pairing invitations;
- add paired-device inventory, role binding, last-seen evidence, revocation,
  and immediate Session invalidation;
- add a transport-neutral Host tunnel client, Host-generated TLS identity, and
  deterministic local L4 fake Relay for protocol tests;
- compose that fake Relay with a Host-initiated control connection, one
  Host-initiated data connection per browser session, and real Host-terminated
  TLS before any deployed endpoint work;
- add a deployed HTTPS Relay profile with a stable Console URL, Host-initiated
  connection, per-Host SNI routing, Host-side TLS termination, bounded
  connection metadata, and no authority data store;
- add replay protection, idempotent request correlation, reconnect/resume,
  backpressure, and Relay-unavailable behavior;
- add connection/setup page with host version, workspace, ledger, knowledge,
  worker, and adapter readiness;
- make normal API calls same-origin and remove hard-coded loopback assumptions
  from production UI paths;
- display `mock`, `dry-run`, `live`, `waiting approval`, and `unavailable`
  consistently;
- add session/device revocation and logout;
- ensure reconnecting the browser resumes observation without restarting work.
- retain Tailscale Serve as a separately labeled advanced private-network
  profile with Funnel disabled; do not show it in ordinary onboarding.

Verification:

- second-computer browser acceptance over the deployed Relay using only a
  modern browser;
- no Tailscale, VPN, Python, Node, Git, repository, Hermes, OpenClaw, or
  AgentOps package is installed on the Console computer;
- pairing codes are single-use, expire, are role-scoped, are omitted from
  logs/audit/URLs, and cannot be replayed;
- revoking a paired device invalidates its Human Sessions and blocks reconnect;
- Relay process/storage inspection finds no application plaintext, raw
  prompt/response, knowledge
  body, artifact body, cookie, CSRF value, invitation secret, credential, or
  Host path;
- task dispatch, run observation, approval, evaluation, audit, memory review,
  and approved artifact download work remotely;
- browser disconnect/reconnect does not interrupt the worker.

Exit gate: the second computer is a useful browser-only control console and the
operator never configures or learns a private network.

Implementation slices:

1. `3A Pairing`: schema, local Owner creation, one-time redemption, role scope,
   device/session revocation, audit, and UI.
2. `3B Transport contract`: Host connector interface, per-Host TLS/SNI identity,
   request IDs, replay and idempotency rules, local L4 fake Relay, reconnect tests.
   The bounded frame/replay primitive and a separate raw-proxy Host-only TLS
   fixture are executable in `LOCAL_L4_RELAY_TRANSPORT_ACCEPTANCE.md`. The
   Host-initiated TLS-over-tunnel composition and a disabled-by-default,
   in-process reconnect supervisor are also executable. A protected atomic
   epoch allocator now prevents connector epoch reuse across process crashes.
   An outer certificate-verifying Relay TLS channel plus inner Host-terminated
   application TLS now proves the production mutual-authentication shape
   locally. A strict disabled-by-default foreground connector process now owns
   reconnect/status/epoch lifecycle, loopback Host TLS termination, exact Host
   SNI rejection, HTTP forwarding and clean signal shutdown. Host
   initialization now creates an exact private disabled config, and Host
   status/doctor expose a bounded fail-closed connector projection without
   reading secrets. The source Host stack now starts exactly one connector child
   only for an explicitly enabled, valid private configuration; disabled or
   unconfigured state starts none, and stop/restart own its cleanup without
   changing Tailscale. Its readiness handshake now requires the first durable
   connector epoch, the lifecycle smoke exercises the exact managed instance
   lock, and real child environment inspection proves Host authority values are
   excluded. A read-only `agentops host relay-preflight` now validates an
   operator-provisioned private config, tunnel secret, CA, Host certificate,
   private-key pairing and exact backend port without enabling Relay, binding a
   socket or using the network. Host status now recognizes an enabled runtime
   only when the private PID record matches the current Host stack, exactly one
   direct managed connector child exists, and a post-start private status proves
   Host TLS plus a durable epoch; remote readiness remains false without a
   deployed Relay. Owner enable/disable now uses an Owner-only, same-origin,
   CSRF-protected prepare/confirm transition with private material binding,
   transactional rollback and bounded audit output. A managed source Host now
   returns a flushed `202`, submits one receipt-bound request to its exact
   LaunchAgent parent, validates the replacement stack and Relay runtime, and
   restores both configs plus the old stack when the target health gate fails.
   A manual foreground Host remains explicit `manual_restart_required`.
   A dependency-free, loopback-tested Relay-side ClientHello SNI selector now
   proves exact two-Host route isolation, bounded parsing/backpressure, opaque
   application bytes, and fail-closed unknown/malformed input. A foreground
   multi-route Relay daemon now binds those opaque route refs to authenticated
   current Host tunnels, persists accepted epoch tombstones under a
   cross-process lock before acknowledgement, enforces one daemon per state
   namespace, forwards the consumed ClientHello exactly once, exposes
   listener-backed bounded readiness, and stops cleanly on SIGTERM. Public
   deployment, DNS/ACME and certificate
   lifecycle, production credential provisioning, installation into the
   current preview, and physical browser-only evidence remain open.
   `PRIVATE_HOST_MANAGED_RESTART_SPEC.md` freezes the safe restart state machine,
   exact-service authority, response-flush ordering, health gate, rollback, and
   Tailscale/Worker isolation. A deterministic response hook now proves that a
   restart request cannot run before the accepted JSON body flushes and is not
   called after a broken response. The exact managed LaunchAgent parent now has
   an internal, caller-parent-bound restart supervisor that reloads current Host
   config for every bounded stack replacement; manual foreground mode has no
   automatic authority. The private restart receipt stores both Relay and
   Host original/target config pairs, binds one transition ref plus a persistent
   monotonic transaction sequence, enforces ordered revisions, restores or
   reapplies both files transactionally, and supports terminal archive/reuse.
   Owner route, receipt, parent request, replacement health and runtime-failure
   rollback are now integrated and locally accepted. Host startup also
   reconciles every receipt state before loading config: unflushed changes roll
   back, flushed/manual changes resume target validation, interrupted rollback
   resumes the original health gate, and `rollback_failed` blocks blind startup.
   Terminal outcomes now enter an owner-only bounded outbox and project exactly
   once into the same Host's MIS audit ledger. Installation and audit proof from
   a versioned candidate, authenticated SNI route binding, certificate
   lifecycle, credential provisioning and the deployed Relay remain open.
3. `3C Relay delivery`:
   - local deployable multi-route daemon, credential-free service contract,
     bounded status and deterministic loopback acceptance: implemented;
   - complete local daemon-to-connector-to-Host TLS/HTTP topology, including
     exact SNI rejection, payload-free status and no MIS database authority:
     implemented and recorded in `LOCAL_RELAY_HOST_E2E_ACCEPTANCE.md`;
   - deterministic offline release bundle with wheel, systemd/config contracts,
     manifest, checksums and exact Git provenance: implemented and recorded in
     `RELAY_RELEASE_BUNDLE_ACCEPTANCE.md`;
   - fail-closed offline first-install transaction with exact plan confirmation,
     immutable releases, stable launch links, protected runtime state, and a
     strict nonblocking lifecycle lock: implemented and recorded in
     `RELAY_OFFLINE_INSTALL_ACCEPTANCE.md`;
   - anchored read-only installed-tree status with absent, valid, recovery and
     invalid states, exact layout/RECORD checks and zero service-readiness
     claims: implemented and recorded in `RELAY_OFFLINE_STATUS_ACCEPTANCE.md`;
   - credential-free systemd activation state machine and crash/rollback
     boundary: specified in `RELAY_SERVICE_ACTIVATION_SPEC.md`; its pure
     plan core, shared strict daemon config parser and read-only FD-anchored
     host scanner plus read-only systemd adapter and activate preview are
     implemented and recorded in
     `RELAY_ACTIVATION_PLAN_CORE_ACCEPTANCE.md`,
     `RELAY_CONFIG_PARSER_ACCEPTANCE.md`,
     `RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md`, and
     `RELAY_ACTIVATION_PREVIEW_ACCEPTANCE.md`; the private immutable journal
     core is implemented in `RELAY_ACTIVATION_JOURNAL_ACCEPTANCE.md`; read-only
     installed status now validates that exact namespace as recorded in
     `RELAY_ACTIVATION_JOURNAL_STATUS_ACCEPTANCE.md`; the private
     lifecycle-lock-bound, strict open-only production opener is implemented
     in `RELAY_ACTIVATION_PRODUCTION_STORE_ACCEPTANCE.md`; confirmed
     first-install now owns exact namespace initialization as recorded in
     `RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md`, while partial
     namespace resume remains unimplemented; the side-effect-free exact
     plan-to-journal and six-step observation compiler is implemented in
     `RELAY_ACTIVATION_EVIDENCE_ACCEPTANCE.md`, and the private scanner-bound
     systemd mutation process adapter is implemented; the private
     exact-confirmed success controller now composes the lock, refreshed plan,
     prepared/intent/observed revisions, fixed mutations, receipt and terminal
     revision as recorded in
     `RELAY_ACTIVATION_CONTROLLER_SUCCESS_ACCEPTANCE.md`; its production
     rescans now use a same-root live-lock capability so active transaction
     status cannot self-block the controller, but it remains unreachable from
     the CLI; the lifecycle-lock-guarded exact-chain and
     orphan-receipt recovery snapshot is implemented in
     `RELAY_ACTIVATION_RECOVERY_SNAPSHOT_ACCEPTANCE.md`; the pure hash-bound
     decision compiler is implemented in
     `RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md`; the lifecycle-lock-bound
     stable-read composition is implemented in
     `RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md`; exact-confirmed
     non-systemd forward and rollback observation/receipt/terminal writes are
     implemented in
     `RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md`; private scanner-bound
     one-step recovery execution and production-store composition are
     fixture-accepted in
     `RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md`; the full forward and
     rollback sequence now passes against real Linux systemd as recorded in
     `RELAY_LINUX_SYSTEMD_RECOVERY_ACCEPTANCE.md`; exact offline installation,
     account ownership, the production scanner and the production store now
     pass separately on a real Linux root as recorded in
     `RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md`; the production installed
     tree, production journal, packaged Relay process and real systemd now run
     together across controller-store reopen boundaries as recorded in
     `RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md`; process-death injection
     inside the intent/mutation/observation and receipt/terminal windows plus
     partial namespace resume remain unimplemented;
   - upgrade, rollback, uninstall, the operator activation surface and public
     infrastructure acceptance: pending;
   - public endpoint, DNS/ACME provisioning, stable per-Host Console origin,
     production credential lifecycle, retention/monitoring, deployment and
     rollback receipt: pending external infrastructure acceptance.
4. `3D Physical acceptance`: fresh browser-only device, real Hermes/OpenClaw
   run, disconnect/reconnect, approval, memory, artifact and logout receipt.

## Phase 4: Packaging and Lifecycle

Deliverables:

- build a versioned macOS host bundle/installer;
- install a managed user-level `AgentOps MIS.app` launcher that opens the same
  browser Workspace and never starts live workers implicitly;
- include production UI assets and Python application without local project
  data;
- include the disabled-by-default Relay connector and make **Enable remote
  Console** an explicit Owner action; do not require a separately installed
  network client;
- provide checksums, provenance, SBOM, third-party notices, uninstall, backup,
  restore, and upgrade instructions;
- add preview-first background service installation;
- reject duplicate same-adapter Worker ownership before Host startup while
  preserving an explicit `--no-workers` external-owner mode;
- implement update check, pre-migration backup, migration verification, and
  rollback instructions;
- publish actual GitHub Release assets rather than relying only on automatic
  source archives.

Verification:

- clean macOS account or VM install;
- install path contains no source-machine DB, token, config, cache, log, or
  dependency dump;
- service survives logout/reboot when explicitly installed;
- duplicate Worker ownership fails closed without opening the Host port or
  terminating the existing process;
- upgrade preserves ledger/knowledge and rollback is exercised;
- checksum and exact-commit provenance validation pass.

Exit gate: another host computer can install and operate the private-host
product without a manual repository setup.

## Phase 5: Real Runtime Closure

Deliverables:

- provide host-only Hermes/OpenClaw prerequisite checks and guided remediation;
- do not auto-install or enable runtimes in this phase;
- expose adapter readiness without exposing runtime credentials;
- execute one fresh, explicitly confirmed customer-style task through Hermes;
- execute one fresh, explicitly confirmed customer-style task through OpenClaw;
- verify task, plan, run, runtime events, tool calls, evaluations, artifacts,
  approvals where required, memories where proposed, and audit evidence;
- show the same evidence from the remote console.

Verification:

- real runtime health is distinguishable from mock readiness;
- failed or unavailable runtime produces bounded failure evidence rather than a
  false success;
- raw prompt/response and credentials are absent from ledger, logs, artifacts,
  screenshots, and commits;
- live mode cannot start without explicit operator confirmation.

Exit gate: the product demonstrates real host-side AI work controlled from the
second computer.

## Phase 6: Release Candidate Acceptance

Run the complete acceptance matrix:

- local workstation regression;
- private host startup and shutdown;
- human auth/session/role/CSRF/origin tests;
- browser-only Relay second-device workflow;
- Tailscale advanced-mode regression with Funnel disabled;
- worker persistence during console disconnect;
- real Hermes and OpenClaw receipts when authorized;
- backup, restore, migration, rollback, and clean uninstall;
- UI build and core Python smoke suites;
- `git diff --check` and artifact/secret scan;
- exact-head CI and release provenance.

Create an acceptance document containing commands, results, limitations, exact
commit, release URL, checksums, and fresh evidence IDs. Do not cite stale demo
run IDs as current proof.

## Definition of Done

The goal is complete only when:

1. The host installs from a real versioned asset and starts through a product
   command rather than repository-specific manual steps.
2. A second computer requires only a modern browser; ordinary onboarding does
   not install or explain Tailscale, a VPN client, or development dependencies.
3. Human authentication and role enforcement protect all workspace data and
   state-changing actions.
4. Ledger, knowledge, project files, secrets, and Agent execution remain on the
   host by default.
5. A remote operator completes the customer task closed loop and sees bounded
   evidence from a fresh real Hermes/OpenClaw run.
6. Local mode remains safe and usable.
7. Clean-machine, backup/restore, disconnect/reconnect, security, and release
   acceptance pass at the exact release commit.

## Deferred Work

- automatic Hermes/OpenClaw/model installation;
- full hosted MIS authority storage, SaaS multi-tenancy, and billing;
- enterprise SSO and complete enterprise RBAC;
- mobile/native desktop console;
- Notion/Dify live synchronization;
- billing, licensing, and customer provisioning.

These items may be planned in parallel but cannot be used to delay or dilute
the private local-host product closure.
