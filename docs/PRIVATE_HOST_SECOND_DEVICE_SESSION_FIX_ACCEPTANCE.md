# Private Host Second-Device Session Fix Acceptance

## Scope

This slice fixes a false browser sign-out found during the physical second-Mac
acceptance pass. Owner password verification and Session creation succeeded,
but the React client returned to the login gate with "session expired" before
the operator could open Host Acceptance.

## Root Cause

The browser Session was valid. Host audit recorded successful logins and the
new Session rows remained active with updated `last_seen_at` values. Immediately
after login, the Workspace also requested machine-authenticated Agent Gateway
status, enrollment and Session endpoints. Those endpoints correctly returned
their own `401 unauthorized` response because a Human Session is not an Agent
Gateway machine credential.

The shared frontend request wrapper treated every non-auth `401` as a revoked
Human Session. It cleared the Human CSRF state and dispatched the global
sign-out event even when the response did not contain a Human Session error.

## Fix

`ui/start-building-app/src/app/data/liveApi.ts` now expires the browser login
only for these bounded backend errors:

- `human_auth_required`;
- `human_session_invalid`;
- `human_session_expired`.

An unrelated Agent Gateway or connector `401` remains local to that feature and
does not clear the Owner Session. The response is inspected through
`Response.clone()`, so callers can still consume the original body.

## Verification

The following checks passed from exact source commit
`70f5114662e177059796900113ac0e6ea539fba4`:

```text
python3 scripts/private_host_auth_workspace_ui_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/human_session_management_smoke.py
python3 scripts/private_host_console_disconnect_smoke.py
python3 scripts/production_ui_host_smoke.py
python3 scripts/private_host_macos_launcher_smoke.py
python3 scripts/private_host_bundle_smoke.py
python3 scripts/private_host_release_consumer_smoke.py
python3 scripts/secret_scan_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

The Human auth smoke specifically proves that an active Human Session may
receive the independent Agent Gateway `401 unauthorized` and still read a
Human-protected endpoint. Session management still fails closed for a truly
revoked or expired Human Session.

## Installed Candidate

The Host was atomically upgraded from immutable preview.31 to local candidate
`1.6.0-private-host-preview.32` built from the exact commit above. The installer
created a pre-update backup, preserved the Host database and user data, and
kept preview.31 as the rollback version. The Host LaunchAgent returned ready,
private Tailscale HTTPS returned HTTP 200 over HTTP/2, and Funnel remained
disabled. The existing Hermes and OpenClaw service Workers remained loaded.

The installed production JavaScript contains the three bounded Human Session
error markers and the global unauthorized event. No database, credential,
Session value, local bundle, UI `dist`, cache or generated artifact is committed.

## Remaining Physical Receipt

This document does not synthesize the remaining physical-device result. The
second Mac must hard-refresh the private HTTPS Workspace, confirm that the
existing Owner Session no longer falls back to the login gate when Agent
Gateway status returns `401`, and then complete the real Runtime, ledger,
evaluation, audit, disconnect/reconnect and logout-denial acceptance flow.

preview.32 is a locally installed candidate until exact-head CI and the normal
prerelease publication gates are complete.
