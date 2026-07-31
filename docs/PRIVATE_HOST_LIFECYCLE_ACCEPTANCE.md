# Private Host Lifecycle Acceptance

## Scope

This slice turns the production UI and human-auth foundations into a managed
repo-local product command:

```text
agentops host init
agentops host start
agentops host open-console
agentops host bootstrap-owner --username <name> --confirm  # headless/recovery
agentops host configure-cli --confirm
agentops host status
agentops host doctor
agentops host logs
agentops host restart
agentops host stop
agentops host console-url
agentops host tailscale-preview
agentops host service-install
agentops host service-check
agentops host service-control --action load|unload|restart
agentops host service-remove
```

It does not publish the Host, install Hermes/OpenClaw, or claim a packaged
customer release.

## Implemented Contract

- Host state lives outside the repository under `~/.agentops/host` by default.
- Initialization/start maintains a private, non-secret Host-data ownership
  marker. Init, start, stop, restart, restore, and rollback serialize through
  the same lifecycle file lock used by install/uninstall, closing data and
  PID-record races.
- Initialization refuses to claim a non-empty, unmarked data root. Existing
  preview data is migrated only when the private-host config schema, exact
  managed database path, required secret fields, data/run layout, and product
  source identity are present. A marker created for migration is removed when
  that first startup fails.
- PID records bind the process ID to its process-group ID and a hash of the OS
  process start/command identity. Stop refuses to signal a live PID when that
  identity cannot be reproduced, preventing stale PID reuse from terminating
  an unrelated process or process group.
- The shared lock is opened without following the final symlink and is checked
  as a regular file before use.
- `AGENTOPS_HOST_HOME` supports isolated tests and controlled installations.
- Config, secret, log and PID files are created with private permissions.
- Initial API/Admin keys and Owner setup code use cryptographic randomness.
- The Owner setup code is displayed by first init and never reprinted by a
  repeated init.
- The first Owner can be created from the Host terminal without copying the
  setup code or placing a password in argv or the environment. The CLI accepts
  an interactive hidden password with confirmation, or one stdin line only for
  controlled automation, and sends credentials only to a literal loopback Host.
  Its loopback transport ignores environment proxies and rejects redirects.
  Long-option abbreviation is disabled, and an attempted `--password` option
  fails without echoing the supplied value.
- Concurrent bootstrap requests serialize at the SQLite write boundary: one
  creates the Owner and every loser fails closed with
  `owner_already_initialized`.
- Host startup uses the production same-origin UI and does not run Vite.
- A prepackaged `--ui-dist` can run without `node_modules`.
- Background start owns one process group; stop targets only that group.
- Real-Worker stop receives a 20-second graceful window; restart never starts a
  duplicate Host after stop failure. Background restart returns one JSON result
  while foreground restart preserves the live stream.
- Default Worker is mock; Hermes/OpenClaw still require
  `--confirm-live-workers`.
- Network publication remains `disabled` after init/start.
- Loopback HTTP uses an `HttpOnly`, `SameSite=Strict` session cookie without the
  HTTPS-only `Secure` attribute, so local Owner login is usable.
- Tailscale inspection is read-only and `tailscale-preview` only prints a
  port-scoped Serve command plus the matching `off` command; it never executes either and
  never enables Funnel.
- Existing Serve backends on the selected HTTPS port are summarized without
  returning raw configuration. A target owned by another local service blocks
  apply; selecting another explicit HTTPS port permits safe coexistence.
- Funnel, Services-wrapped config, mixed handlers, DNS drift, stopped Host state,
  and unknown/non-exclusive ownership all fail closed.
- `tailscale-apply` and `tailscale-revoke` remain side-effect free without
  `--confirm`; confirmed apply/reset update the private trusted-Origin config
  and require a Host restart. Apply enables `Secure` cookies for tailnet HTTPS;
  revoke returns to the loopback cookie policy.
- Revoke refuses changes when MIS cannot prove ownership of the selected port
  and disables only that port with the exact Tailscale `off` form. It never
  resets unrelated Serve handlers.
- Host status and doctor expose a bounded `human_access` state and required
  Owner-bootstrap action without account or credential values. The state moves
  from `bootstrap_required` to `ready` only after first-Owner creation.
- Host status, doctor, logs and console URL responses omit credential values.
- `console-url` reports the private HTTPS URL ready only when Tailscale is
  running, Host health is ready, trusted Origin matches current Tailscale DNS,
  and the verified Serve target is exclusively owned by MIS without Funnel.
- A managed production UI follows the packaged `current` release across update
  and rollback. Explicit custom UI paths remain unchanged.
- Worker and UI-helper subprocesses receive purpose-specific allowlisted
  environments. Unknown custom variables and Owner setup, browser-session,
  CSRF and human-admin credentials are omitted; workers retain only the exact
  Agent Gateway and Hermes/OpenClaw fields they require, while npm/Vite build
  configuration does not cross into workers.
- Local machine CLI configuration is explicit and confirmation-gated. The Host
  first authenticates its machine key against the literal-loopback Gateway,
  then atomically writes a `0600` config below `~/.agentops`; it never reuses a
  browser Session or prints the key. Saved keys are bound to their configured
  base URL, and credentialed Worker traffic rejects environment proxies,
  redirects, non-HTTPS remote targets and reflected credential output.
- Owner bootstrap and local machine CLI configuration require a live managed
  Host PID before sending any credential to loopback; an unrelated process on
  the configured port cannot satisfy the command with health JSON alone.
- Foreground Host mode writes the same managed PID contract while running and
  removes only its own matching record on exit, so credentialed setup commands
  remain available without weakening process ownership checks.
- The optional macOS Host LaunchAgent is preview-first and host-only. Its exact
  managed definition follows the packaged `current` link, uses `--no-workers`,
  stores no credential material, writes mode `0600`, and requires separate
  confirmation for install, launchctl mutation, and removal. Unknown same-name
  files and removal while loaded fail closed.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py agentops_mis_cli/cli.py \
  scripts/run_local_stack.py scripts/private_host_lifecycle_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
python3 scripts/private_host_background_service_smoke.py
python3 scripts/private_host_owner_bootstrap_cli_smoke.py
python3 scripts/host_cli_credential_binding_smoke.py
python3 scripts/private_host_bundle_smoke.py
python3 scripts/run_local_stack_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/production_ui_host_smoke.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The lifecycle smoke used a temporary Host Home, temporary production UI,
temporary SQLite database and a free loopback port. It verified:

- private config and secret permissions;
- one-time setup-code output;
- repeat-init fail-closed behavior without secret output;
- unrelated non-empty Host-data root rejection with its sentinel preserved;
- blocked init writes no marker or config, then initializes normally after the
  lifecycle lock is released;
- a failed legacy migration startup rolls back its newly created data marker;
- an interrupted/failed first initialization also rolls back its newly created
  marker, config, secrets, and empty managed directories; the same Host home
  can then be initialized successfully without manual cleanup;
- background start and ready health;
- blocked start waits for the lifecycle lock without writing a PID, then starts
  normally after the lock is released;
- a stale/reused PID record is rejected and its unrelated process remains
  running;
- zero Worker mode for deterministic CI;
- publication disabled by default;
- running status readback;
- explicit Owner-bootstrap-required status before creation and login-ready
  status after creation;
- real Owner bootstrap and authenticated task read through a loopback CookieJar;
- installed browser-first Owner creation inside the existing Workspace shell,
  with `agentops host bootstrap-owner` retained for headless/recovery use;
- confirmation, non-TTY, empty-password, password-mismatch and non-loopback
  fail-closed behavior without a bootstrap POST;
- environment-proxy bypass, redirect rejection, long-option abbreviation
  rejection and safe forbidden-password-argv output;
- concurrent bootstrap yielding exactly one Owner and one `409` loser;
- setup/password/session omission after init and human-control secret removal
  from Worker and UI-helper environments;
- stale local CLI context replacement, safe config-path/symlink rejection,
  base-URL credential binding, proxy/redirect blocking and reflected-key
  redaction;
- `HttpOnly`/`SameSite` local cookie behavior without a false HTTPS-only flag;
- Tailscale preview-only/Funnel-disabled behavior;
- an existing non-MIS Serve target blocks apply and revoke without their
  distinct destructive acknowledgements;
- private console readiness changes to true only after the verified Serve
  target points at MIS and returns to false after revoke;
- Secure cookie policy enabled on confirmed Tailscale apply and disabled after
  confirmed revoke;
- process-group stop and temporary-state cleanup.
- Owner-bootstrap smoke Host instances and foreground process groups are
  registered before launch and cleaned on normal exit, exceptions, `SIGTERM`,
  or `SIGINT`; an intentional `SIGTERM` acceptance stopped only the newly
  created fixture Host.
- stopped-Host, DNS-drift, Funnel, Services and mixed-handler fail-closed cases;
- single-result background restart and foreground-stream restart contracts;
- actual version-marker HTTP readback after bundle upgrade and rollback;
- custom production UI path preservation.

No real Runtime, external connector, Tailscale configuration, user database,
prompt, response, private message or transcript was used.

## Known Limitations

- The available macOS Host bundle remains unsigned and unnotarized.
- Tailscale Serve remains explicit and operator-confirmed; it is never enabled
  by init/start.
- Trusted Origin validation is implemented; richer trusted-proxy identity and
  forwarded-header handling remain pending.
- One-time pairing invitations, password recovery, Session revocation and paired
  device revocation now exist in the Workspace UI. General account
  administration, post-enrollment role editing and enterprise device trust
  remain pending.
- `host logs` returns safe metadata and the local path, not live redacted tail.
- Second-computer acceptance and real Hermes/OpenClaw remote-control evidence
  remain mandatory before product completion.

## Next Slice

Run `agentops host bootstrap-owner` interactively on the current physical Host,
then complete the browser workflow from the already reachable second tailnet
device with no repository dependencies. Repeat clean installation on another
physical Mac before declaring the final RC.
