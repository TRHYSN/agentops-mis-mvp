# Private Host Human Session Management Acceptance

## Scope

This slice closes the Private Host browser Session revocation gap required by
`LOCAL_HOST_REMOTE_CONSOLE_SPEC.md` and Phase 3 of the delivery plan. An
authenticated Owner can inspect browser Sessions for the current account,
revoke one other Session, or revoke every other Session while preserving the
current browser.

The UI is part of the existing AgentOps MIS Workspace. It is available from the
Topbar account avatar at `/workspace/account`; it is not a second application,
native window, centered auth card, or standalone admin frontend.

## API Contract

```http
GET  /api/human-auth/sessions
POST /api/human-auth/sessions/revoke
```

Both endpoints:

- require a valid human browser Session;
- require the `owner` role;
- remain separate from Agent Gateway machine credentials;
- use the existing same-origin and CSRF policy for state changes;
- return deterministic `hsref_*` references instead of raw Session IDs;
- never return the Session hash, cookie value, token, IP address, or
  User-Agent.

The revoke body accepts exactly one operation:

```json
{"session_ref":"hsref_..."}
```

or:

```json
{"all_other":true}
```

Selecting the current Session returns `409 current_session_requires_logout`.
The current Session must be ended through the normal sign-out path. Revoking an
unknown or another account's safe reference returns `404` without revealing
whether that Session exists elsewhere.

## UI Contract

- `/workspace/account` reuses `AppShell`, Topbar, themes, locale controls, page
  spacing, and the established unframed settings layout.
- The account section shows bounded public account fields only.
- The Session section shows safe reference, status, creation, activity, and
  expiry timestamps.
- A destructive confirmation is required before one-Session or all-other
  revocation.
- The current browser is marked explicitly and exposes sign out instead of an
  indirect revoke button.
- Chinese and English copy are included.

## Security And Audit

- Expired active rows are marked `expired` before a Session list or revoke.
- Session lookup is scoped to the authenticated account and uses a
  constant-time comparison of safe references.
- Owner role, Origin, and CSRF enforcement happen server-side.
- Successful, blocked, invalid, and cross-account revoke attempts create
  bounded `human_auth.sessions_*` audit events after authentication.
- Login and logout audit entities now use `hsref_*` rather than the raw
  internal Session ID.
- Audit metadata explicitly omits credentials, raw Session IDs, Session
  hashes, and token values.
- No real Runtime is called by this feature or its acceptance test.

## Verification

Commands run against the current worktree:

```bash
python3 -m py_compile server.py agentops_mis_core/human_auth.py scripts/human_session_management_smoke.py
python3 scripts/human_session_management_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/private_host_auth_workspace_ui_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

The isolated Session smoke starts a temporary Private Host and database. It
proves anonymous `401`, Viewer `403`, CSRF rejection, current-Session `409`,
single other-Session revoke, all-other revoke with current preservation,
cross-account `404`, expired-Session transition, revoked-cookie denial, safe
response fields, bounded audit actions, and raw Session/credential omission.
It also statically verifies the route, account entry, API bindings,
confirmation step, sign-out action, and existing settings-layout reuse.

The production UI build was also exercised against a separate temporary Host
in a real browser. The browser created the first Owner, a second independent
Session was added, and `/workspace/account` rendered two rows with one current
Session, one revocable other Session, working Topbar navigation, and the normal
Sidebar. Switching to Chinese verified localized headings, counts, statuses,
timestamps, revoke actions, sign out, and privacy boundary copy. The temporary
Host and database were stopped and removed after the exercise.

The smoke is included in the GitHub `Offline safety smokes` job and the release
evidence command manifest.

## Known Limitations

- This version deliberately does not collect IP address or User-Agent, so a
  Session is identified by safe reference and timestamps rather than a named
  physical device.
- The Owner can manage only Sessions for the current account. Account
  invitation, account administration, and enterprise device trust are future
  work.
- The proposed `agentops host session revoke` CLI command is not implemented in
  this slice; the product API and Workspace UI are authoritative.
- Physical second-computer login/revoke/logout evidence is still required for
  final Phase 6 acceptance.
- The installed Private Host must be upgraded to an exact-commit package after
  this slice passes CI; worktree tests alone are not installed-product proof.
