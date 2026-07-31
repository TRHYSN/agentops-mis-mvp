# Private Host Auth Workspace UI Acceptance

## Scope

This slice integrates Private Host human authentication into the existing
AgentOps MIS application shell. Authentication is a locked workspace state of
`AppShell`, not a separate page, standalone layout, or second application.

The locked state keeps the product frame recognizable while preventing access
to workspace navigation and authenticated actions. Once the human session is
ready, `AuthGate` returns the existing workspace unchanged.

## Changed Files

- `ui/start-building-app/src/app/components/auth/AuthGate.tsx`
- `ui/start-building-app/src/app/components/shared/WorkspaceSettings.tsx`
- `ui/start-building-app/src/app/components/pages/AccountSecurity.tsx`
- `ui/start-building-app/src/app/context/PreferencesContext.tsx`
- `ui/start-building-app/src/app/components/layout/AppShell.tsx`
- `ui/start-building-app/src/app/components/layout/Sidebar.tsx`
- `ui/start-building-app/src/app/components/layout/Topbar.tsx`
- `ui/start-building-app/src/styles/theme.css`
- `ui/start-building-app/src/app/data/liveApi.ts`
- `agentops_mis_core/human_auth.py`
- `server.py`
- `agentops_mis_cli/host.py`
- `packaging/macos/install-private-host.sh`
- `scripts/private_host_auth_workspace_ui_smoke.py`
- `scripts/private_host_owner_browser_handoff_smoke.py`
- `scripts/human_session_management_smoke.py`
- `scripts/human_password_recovery_smoke.py`
- `docs/PRIVATE_HOST_PASSWORD_RECOVERY_ACCEPTANCE.md`
- `.github/workflows/ci.yml`
- `docs/PRIVATE_HOST_AUTH_WORKSPACE_UI_ACCEPTANCE.md`

## UI Contract

### Visual Integration

- `AuthGate` renders the authentication entry inside `AppShell` with
  `locked={true}` and exposes `human-auth-workspace-gate` for bounded UI
  verification.
- The existing Sidebar, Topbar, theme variables, typography, borders, and
  workspace frame remain visible during connection, Owner bootstrap, login,
  and unavailable states.
- Locked and authenticated routes use the same `p-4 lg:p-5` main-content
  spacing. The auth state is laid out as a normal MIS account-settings page:
  compact page header, contextual left column, and a bounded form column. It is
  not a centered landing-page hero, feature-card wall, or separate product.
- The first-Owner form intentionally has no rounded standalone card shell or
  decorative product pitch. It uses the same borders, field density, content
  width, and action hierarchy as the existing Workspace and Admin surfaces.
- Form density, heading scale, panel radius, and status rows match the existing
  Workspace and Admin surfaces. The first-Owner and sign-in flows retain their
  distinct operational copy without decorative marketing content.
- First-Owner setup, sign-in and the authenticated `/workspace/account` route
  now use the same `WorkspaceSettingsPage` and `WorkspaceSettingsSection`
  components. This is shared product UI, not two visually similar copies.
- `/workspace/account` is visible in the existing Sidebar as `Account and
  access` / `账户与访问`; the Topbar avatar remains a direct shortcut.
- During bootstrap or sign-in, that existing Sidebar entry is the current
  location. Locked rows remain non-links, and the locked footer shows the
  Private Host/account context instead of the demo workspace identity.
- The locked workspace switcher says `Private Host` / `本地主机`. After
  authentication, the Sidebar and Topbar use the actual Human Session
  `workspace_id`, display name, or username; demo identity is only a fallback
  when human authentication is disabled.
- Locked Sidebar items render as disabled non-link rows. They do not navigate
  to workspace or Admin routes before authentication.
- The Topbar communicates the locked state as a quiet inline status, while
  keeping theme and language controls available. Search, workspace switching,
  notifications, and logout are unavailable while locked.

### Visual consistency correction

The final source pass responds to a product review that the first-Owner state
still read like a generated setup screen even though it already reused
`AppShell`. The correction stays inside the existing Workspace implementation:

- the shared settings grid now fills the available Workspace column instead of
  stopping at a fixed 680-pixel content track and leaving an accidental empty
  strip;
- account fields remain bounded to the normal form width, and the submit action
  starts on the same control grid instead of floating against the far edge;
- installer handoff state is screen-reader-only; normal users see the account
  fields, while manual deployment recovery stays under `Advanced setup`;
- the action uses the existing primary product color instead of the bright
  cyan runtime accent;
- the light locked state uses the normal flat enterprise surface rather than a
  decorative page gradient; and
- copy is shorter and task-oriented in both locales, with no feature pitch,
  product tour, or second onboarding design system.

No backend route, credential handoff, Human Session, CSRF, role, Runtime, or
ledger behavior changed in this visual correction.

### Responsive Behavior

- The authentication content uses the existing full-height application shell
  on desktop and mobile viewports.
- When the Sidebar collapses at the mobile breakpoint, the Topbar keeps the
  AgentOps MIS product mark instead of leaving an orphaned workspace selector.
- The main authentication form uses compact label/control rows at narrow
  widths and becomes a two-column settings layout at the existing `lg`
  breakpoint. The normal Sidebar remains visible on desktop and collapses at
  the same mobile breakpoint as the rest of the product.
- Form controls use bounded widths and the shell main area remains scrollable,
  so bootstrap and login content do not require a separate responsive page.

### Bilingual And Theme Behavior

- Connection, unavailable, Owner initialization, login, field, validation,
  action, and local-host boundary copy is available in English and Chinese.
- Owner bootstrap is presented as administrator setup. The one-time
  initialization authority is consumed automatically from the application
  handoff; its manual field lives under `Advanced setup` and is not part of the
  normal customer path.
- Administrator setup and sign-in remain distinct states in both locales.
- Password confirmation is required for administrator setup and password
  recovery, and both new-password
  fields preserve the 12-character minimum.
- The minimum is a length rule, not a composition puzzle: the UI recommends a
  memorable passphrase and does not require arbitrary uppercase, number, or
  symbol combinations.
- Password and confirmation rows provide live bilingual readiness/match status,
  and the create action remains disabled until the setup code, username,
  12-character minimum and matching confirmation are all locally valid.
- Password visibility uses bounded eye-icon buttons with accessible bilingual
  labels. The controls do not change password storage, transport or server-side
  validation, and visibility resets after authentication, logout or Session
  expiry.
- The language control remains usable while locked, so a user can choose a
  locale before bootstrap or login.
- The theme control remains usable while locked and continues to use the
  existing `enterprise`, `ops`, and `workforce` theme modes.
- A fresh browser now starts in the light `enterprise` theme, which matches the
  customer Workspace. Explicitly stored `ops` and `workforce` choices remain
  unchanged.

### Browser-first Owner Setup

- The server continues to require the one-time setup code for the first Owner.
- The managed macOS installer/launcher passes that code to the literal-loopback
  browser in a fragment. Fragments are not included in HTTP requests, and the
  page removes it with `history.replaceState` before the first API request.
- The setup-code field is hidden only when a bounded handoff value was received;
  invalid handoff values are cleared and the manual recovery field returns.
- A same-document `hashchange` is consumed and scrubbed as well as first page
  load, so macOS may safely reuse an already-open Console tab.
- The legacy setup-code API and `agentops host bootstrap-owner --confirm`
  remain available as recovery and headless-host paths.
- The macOS installer prints the local Console URL and opens it after a
  confirmed `--start` installation when a graphical macOS session is
  available. `AGENTOPS_NO_AUTO_OPEN=1` disables that convenience.

### Local Password Recovery

- Sign-in exposes a real `Forgot password` / `忘记密码` action after the Host
  reports an initialized administrator account.
- Starting recovery requires both a literal loopback HTTP Origin and the
  protected local authority handed to the page when the desktop application
  opens the Console. Completing recovery also requires the loopback Origin.
  A browser connected through the private-network URL receives
  `local_recovery_required` and is directed to the computer running AgentOps
  MIS.
- The application handoff is consumed and scrubbed before requests, remains
  component-memory-only, and is never rendered in the normal sign-in UI. A
  loopback-looking request without it cannot start recovery.
- The local browser then receives one memory-only, single-use recovery authority
  with a ten-minute lifetime. SQLite stores only its SHA-256 hash; audit rows
  contain only a bounded challenge reference and omission flags.
- A successful reset changes the scrypt password, revokes every older active
  browser Session, consumes the challenge, and creates one fresh Session for
  the local browser. Replaying the challenge fails closed.
- There is deliberately no fake email link or cloud reset promise. This
  Private Host release has no account email service; physical access to the
  Host is the recovery authority. Passkeys, MFA, and delegated recovery remain
  later product work.

### Loopback And Private HTTPS Session Cookies

- A Host may serve both the literal-loopback Console over HTTP and the remote
  Console through private Tailscale HTTPS. Cookie transport is therefore
  selected from the validated request Origin/Host rather than one global
  publication flag.
- `http://127.0.0.1`, `http://localhost`, and loopback IPv6 receive an
  `HttpOnly; SameSite=Strict` Session cookie without `Secure`, allowing the
  local browser to return it over literal loopback.
- Private HTTPS requests retain `Secure; HttpOnly; SameSite=Strict`. Non-
  loopback HTTP origins do not receive the loopback exception and remain
  fail-closed.
- `human_browser_auth_smoke.py` runs with secure-cookie mode enabled and proves
  both sides of the split: authenticated loopback reads/writes/logout work,
  while a private HTTPS Origin receives a `Secure` cookie. Password recovery,
  Origin, CSRF, workspace and machine-token separation remain unchanged.

## Safety Boundary

- Human browser authentication remains separate from Agent machine
  authentication.
- No-code Owner bootstrap remains rejected even from loopback. A local process
  cannot become Owner merely by reaching the Host port.
- `agentops host open-console` reads the protected code locally, passes the
  handoff to macOS Launch Services over stdin rather than argv, and omits the
  code and full handoff URL from command output.
- Owner creation still uses `BEGIN IMMEDIATE`; concurrent first-owner requests
  produce one Owner Session and one fail-closed conflict.
- Terminal bootstrap failures clear the in-memory handoff. Only locally
  correctable username/password-strength errors retain it for the next submit.
- Locked navigation preserves the existing Workspace information architecture
  so first-run setup does not look like a second product. Its items are inert
  and expose no authenticated route or workspace data; Account and access is
  the current location, and the locked Topbar does not expose logout.
- The smoke is static and read-only. It does not start a Host, mutate a ledger,
  inspect or print credentials, read a database, or invoke Hermes, OpenClaw, or
  another runtime.
- No credential, setup code value, cookie, Session value, CSRF value, database,
  private message, full transcript, raw prompt, or raw response is recorded by
  this acceptance document or its smoke output.
- Backend Session, CSRF, Origin, role, and workspace enforcement remain the
  authoritative security boundary; the locked UI is defense in depth and user
  guidance, not a replacement for server enforcement.
- Password recovery never records a submitted password or raw recovery
  authority in audit metadata, logs, or the database. The response returns the
  authority only to the initiating loopback browser and marks it ephemeral.

## Verification

Commands run against the current worktree on 2026-07-14:

```bash
python3 scripts/private_host_auth_workspace_ui_smoke.py
python3 scripts/private_host_owner_browser_handoff_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/human_password_recovery_smoke.py
python3 scripts/private_host_owner_bootstrap_cli_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

The static smoke passed all current checks and returned JSON with `ok: true`, no
failures, and exit code 0. It verifies locked-shell reuse, the authentication
gate marker, the unframed Workspace settings layout, shared content
spacing and shared setup/account components, bilingual administrator
setup/login/recovery copy, password confirmation, live readiness/match
guidance, accessible
visibility controls and minimum length, the visible Account navigation
entry, locked Account current-location treatment, demo-identity omission,
compact responsive form rows, mobile product identity, the full existing
Workspace navigation rendered as inert non-link items while locked, persistent
theme/language controls, logout omission while locked, and the
initialization-authority browser handoff projection.

The browser-handoff integration smoke passed against a temporary Host and
database. It proved that no-code bootstrap is rejected, setup-code bootstrap
creates the Owner, audit metadata omits credentials, the CLI uses `osascript -`
stdin rather than argv, and the frontend scrubs and bounds the fragment. The
existing browser-auth and CLI bootstrap smokes also passed.

The Vite production build passed with 2,282 modules transformed. Vite reported
the existing large-chunk advisory for the main bundle; this is a performance
follow-up and did not fail this scoped acceptance. The final `git diff --check`
also passed.

The final visual-correction source was also rendered through a Vite proxy to
the real loopback Host at desktop width and at `390x844`. The installer-style
handoff used a synthetic bounded fragment only to exercise the presentation;
the address bar was immediately scrubbed, no form was submitted, and no real
setup code, account credential, database value, Runtime task, raw prompt, or
raw response was read. The enterprise and operations themes both rendered
without horizontal overflow or browser errors beyond the development-only
React DevTools notice. Browser screenshots remain ignored local evidence and
are not release assets.

### 2026-07-15 Recovery UX Follow-up

A fresh production build was exercised against a new temporary Private Host
and SQLite database. The browser consumed and scrubbed the desktop-application
handoff, created an administrator through the existing Workspace shell, entered
the Workspace, signed out, and exposed the normal two-field sign-in form plus a
`忘记密码` action. Reopening that login tab through the application handoff
entered the local recovery state and a completed password reset returned to the
Workspace while revoking prior Sessions.

The rendered recovery surface contained only username, new password,
confirmation, reset, and back actions. It explained the 12-character length
rule without composition requirements and the old-Session revocation effect;
it did not render the initialization authority. A browser-valid escaped
username `pattern` replaced the previous unescaped hyphen form discovered by
this exercise. A fresh browser loaded the corrected production asset and
reported no console errors; the remaining password-form message was a browser
verbose accessibility advisory, not an application error.

An isolated production build was visually exercised at narrow and desktop
viewports in Chinese and English across `ops`, `workforce`, and `enterprise`
themes. A temporary browser completed Owner bootstrap, entered Workspace,
logged out, and signed in again without reading or changing the installed Host
database.

The final production build also exercised installer-style same-document
handoff against a fresh temporary Host: the Console first exposed its manual
initialization field only under `Advanced setup`, then received the fragment on
the already-mounted page, scrubbed the address bar, hid that advanced field,
created the Owner, and entered the existing Workspace. No installed
Host database or real credential was used.

## Security Review Closure

The final review found no Critical or High issue and no path for a process that
can merely reach the loopback port to create an Owner without the setup code.
It identified one medium issue in the initial implementation: a reused browser
tab could receive a same-document fragment after `AuthGate` had mounted. The
final implementation consumes and removes `hashchange` handoffs and the browser
exercise above covers that path. It also clears the in-memory handoff after
terminal request errors while retaining it only for locally correctable
username or password-strength input. A late handoff received after bootstrap
also gets scrubbed without being retained in login or ready state.

The same-Unix-user boundary remains explicit: a malicious process running as
the Host account may read files owned by that account despite `0600`. This is a
host trust assumption, not a browser bootstrap authority granted by the port.

## Known Limitations

- Owner bootstrap still depends on the protected one-time setup code. The
  graphical path removes manual copying; it does not weaken that authority.
- The real installed Private Host serves preview.21 from exact commit
  `c29addf2fb1155e6046432007c7d6282ac6d1754`. Installed UI bytes match the
  release build; Owner completion remains a human action.
- Owner bootstrap/login and the real Hermes or OpenClaw runtime closure still
  require user input. No unattended automation or machine credential may
  substitute for those human actions.
- This acceptance does not claim a physical second-device browser pass,
  authenticated artifact download, disconnect/reconnect proof, logout-denial
  receipt, or completed Owner-approved real-runtime loop.
