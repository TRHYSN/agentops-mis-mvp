# Private Host macOS Launcher Acceptance

## Scope

This slice adds a managed, user-level `AgentOps MIS.app` entry point to the
unsigned Private Host preview. It opens the existing browser Workspace; it is
not an Electron, Tauri, or second native UI.

Default install path:

```text
~/Applications/AgentOps MIS.app
```

CLI installation and recovery remain available at `~/.local/bin/agentops`.

## Product Flow

On double-click, the launcher:

1. validates its managed config, application marker, pinned Python runtime,
   current release directory, CLI shim, and Host path;
2. serializes concurrent launches with a private user-level lock;
3. initializes a completely new Host on `127.0.0.1:18878` when both config and
   secrets are absent;
4. fails closed when only one of config or secrets exists;
5. starts a stopped Host with `agentops host start --no-workers`;
6. leaves an already-running Host and its Worker set unchanged;
7. calls `agentops host open-console` so first-Owner pairing continues through
   the existing scrubbed browser handoff.

The launcher does not accept a password, token, model key, setup code, Runtime
selection, network exposure option, or Worker confirmation.

## Workspace UI Continuity

- The `.app` opens the production build of the existing AgentOps MIS React
  Workspace. It does not render or install a second launcher UI.
- First-Owner setup and later sign-in reuse the existing `AppShell`, Sidebar,
  Topbar, theme control, language control, spacing, and design tokens.
- The locked state is a compact `Account and access` settings form inside the
  Workspace content area. It does not use a standalone hero, feature pitch,
  host-status card wall, or separate product navigation.
- The locked Sidebar shows only the current account-setup step instead of a
  wall of disabled product routes. The locked Topbar keeps the Workspace
  identity, theme and language controls while omitting the inactive search and
  workspace-switch controls.
- The form keeps only the setup/login fields, submission action, and a bounded
  local-host connection note. Host and Agent operational detail remains in the
  normal Workspace after authentication.
- At narrow widths the existing Sidebar collapses and each field becomes a
  label-over-input row; the desktop settings grid remains unchanged for the
  authenticated product surface.

## Lifecycle Boundary

- `packaging/macos/install.sh` creates or atomically replaces only an
  ownership-marked `AgentOps MIS.app` below the selected HOME application
  directory.
- A foreign, symlinked, or unmarked application is never overwritten.
- `packaging/macos/uninstall.sh` removes the application only after verifying
  the same ownership marker.
- Uninstall preserves `~/.agentops/host` unless the existing explicit and
  ownership-checked purge mode is selected.
- `AGENTOPS_NO_APP_INSTALL=1` keeps the headless/CLI-only installation path.
- The preview remains unsigned and unnotarized.

## Runtime And Credential Safety

- Finder launch does not depend on its reduced PATH. Both the app executable
  and managed CLI module use the absolute Python interpreter validated during
  installation.
- First-run `host init` output is captured and discarded because it includes
  one-time Owner setup material.
- Error dialogs contain only fixed product messages. Raw subprocess output is
  never shown or persisted.
- New Host startup always includes `--no-workers`.
- Hermes/OpenClaw installation, enablement, confirmation, and model
  provisioning remain separate explicit operations.

## Automated Verification

Canonical commands:

```bash
python3 -m py_compile packaging/macos/launcher.py scripts/private_host_macos_launcher_smoke.py
python3 scripts/private_host_macos_launcher_smoke.py
python3 scripts/private_host_auth_workspace_ui_smoke.py
python3 scripts/private_host_bundle_smoke.py
python3 scripts/private_host_release_consumer_smoke.py
python3 scripts/release_evidence_packet_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

The focused launcher smoke uses an isolated HOME and a fixture CLI module. It
does not call a real Runtime or inspect a real Host database. It verifies:

- clean-HOME install and first-run initialization;
- Finder-style restricted PATH with pinned Python;
- `--no-workers` startup;
- repeat launch plus ten concurrent launches without duplicate init/start;
- setup material omission from app and process output;
- foreign application rejection;
- modified ownership-marker uninstall rejection;
- partial Host state rejection;
- launcher removal with Host data preserved.

Local acceptance on 2026-07-14 passed the React production build (2,282
modules), the auth Workspace contract, launcher smoke, full bundle lifecycle,
clean release-consumer install, release evidence packet, Python compilation,
secret scan, and `git diff --check`. Browser review covered both the existing
dark operations theme and light enterprise theme at desktop width. The latest
focused review also covered the light enterprise first-Owner state at
1280x720 and 390x844 with zero browser console errors and no horizontal
overflow. The real Host now serves preview.28 from the versioned installation;
source assets were not copied over the running installation in place.

The `v1.6.0-private-host-preview.28` package upgrade preserved the same launcher
boundary and existing Workspace. Its public bundle and clean-HOME launcher
smokes passed, and installed browser readback returned the Chinese Owner form
without horizontal overflow. The process-reuse receipt below was physically
re-run against the installed preview.28 package.

## Installed App Launch Receipt

**Superseded Preview 28.**

On 2026-07-14, preview.19 first established the installed-app process reuse
receipt. After the versioned `v1.6.0-private-host-preview.28` upgrade, the
launcher was invoked again through the real macOS application boundary:

```bash
open ~/Applications/AgentOps\ MIS.app
```

Google Chrome opened `http://127.0.0.1:18878/workspace` and the preview.28
production Workspace returned HTTP 200. The launcher delivered the protected
one-time setup authority to the already integrated account page: the address
bar contained no fragment, the manual setup-code input was absent, and the page
showed `已安全接收`. The create action remained disabled and no form
was submitted.

The Host PID and both independent service Worker PIDs remained unchanged across
the preview.28 app open. The AgentOps Worker read model reported two fresh
service Workers, two execution-capacity lanes and zero recent completed runs in
this launch window. The installed HTML, CSS and JavaScript came from the
versioned preview.28 release directory. This proves that opening the installed
app reuses the existing Host and service Worker set instead of restarting or
duplicating them; `live_execution_performed:false` remains the launcher safety
contract. No Owner was created, no Runtime task was dispatched, and no
credential or setup-code value was read or recorded.

## Current Preview 29

On 2026-07-15, the launcher from
`v1.6.0-private-host-preview.29` at exact commit
`574c735541d95b70180254235a385ff764f8c45c` was invoked through the installed
macOS application boundary after the versioned upgrade.

The Host PID and both independent service Worker PIDs remained unchanged across
the preview.29 app open, and no duplicate Host or Worker was started. The
installed HTML, CSS and JavaScript were byte-equal to the exact preview.29
build. No model task was dispatched or executed, so
`live_execution_performed:false` remains true.

This receipt did not perform an installed-state browser address-bar, fragment,
or page-visual inspection: `browser_visual_readback_performed:false`. The
preview.28 browser/fragment observations above remain superseded historical
evidence and are not attributed to preview.29. No credential, setup authority,
account identifier, raw browser state, prompt or response was recorded.

preview.29 was published through the manual prerelease path; the Private Host
Preview Release workflow did not run.

## Current Preview 31 App Receipt

`v1.6.0-private-host-preview.31` is installed at exact commit
`fed1b2410d6725a217c9727dba570db62cc46963`, and clean-HOME bundle/consumer
smokes passed for its packaged macOS launcher and both CLI entry points. After
the workstation was unlocked, the installed app was opened through:

```bash
open ~/Applications/AgentOps\ MIS.app
```

Across the preview.31 app open, Host PID `37995`, Hermes Worker PID `38056`
and OpenClaw Worker PID `38080` remained unchanged. The bounded Run list count
remained 36 before and after. No Runtime task was created or executed by the
launcher, and no duplicate Host or Worker appeared.

This is a process-reuse receipt only:
`browser_visual_readback_performed:false`. It does not attribute a new visual,
fragment, account or credential observation to preview.31, and it does not
replace physical another-Mac or logout/reboot acceptance.

## Remaining External Gate

The installed-app `open` receipt covers only same-Mac process reuse. A separate clean Mac still must
download and install the published asset, launch the app
from that installation, and complete the no-repository consumer acceptance
before final RC. Physical logout/reboot and exact-package approved Runtime
execution also remain open. Signing/notarization remain future work and must
not be claimed by this preview.
