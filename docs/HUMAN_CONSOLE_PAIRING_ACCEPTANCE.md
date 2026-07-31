# Human Console Pairing Acceptance

Status: local backend and Workspace UI slice implemented; deployed Relay acceptance pending

## Scope

This slice implements transport-neutral human pairing before the Relay exists.
It reduces second-device setup to an Owner-created one-time invitation while
keeping Human Sessions separate from Agent Gateway machine credentials.

Implemented backend contract:

- `POST /api/human-auth/pairing-invitations` (Owner + CSRF)
- `GET /api/human-auth/pairing-invitations` (Owner)
- `POST /api/human-auth/pairing-invitations/:ref/revoke` (Owner + CSRF)
- `POST /api/human-auth/pair` (public redemption at an allowed Origin)
- `GET /api/human-auth/devices` (Owner)
- `POST /api/human-auth/devices/:ref/revoke` (Owner + CSRF)

Implemented Workspace flow:

- **账户与访问 / Account and access** lets an Owner create a role/expiry/label
  invitation, copy its one-time fragment link, review bounded invitation/device
  rows, and revoke either one;
- `AuthGate` consumes `#pair=<secret>`, removes the fragment from the address
  bar immediately, keeps the secret only in React component memory, and reuses
  the existing locked Workspace form to create the invited member and device;
- pairing copy and controls are bilingual, compact and remain inside the
  existing AppShell/Workspace settings layout;
- frontend API failures retain only a bounded error code and never include raw
  response bodies or the pairing secret.

## Security Contract

- Invitations are SHA-256 hashed at rest, expire after 10 minutes by default,
  are single-use, allow five failed profile attempts, and can grant only
  `viewer`, `operator`, or `approver`; `owner` is forbidden.
- Only the creation response contains the ephemeral invitation secret. Lists,
  later responses, audits, process output, and safe references omit it.
- Redemption provisions a non-Owner account, creates a device with a separate
  hash-at-rest secret, and issues a Human Session bound to that device.
- A paired account cannot sign in from a browser that does not possess an
  active device Cookie. Device revocation atomically revokes its active Human
  Sessions.
- Session and device Cookies are separate `HttpOnly`, `SameSite=Strict`
  credentials. Their `Secure` policy follows the request-scoped Host transport
  rule so literal loopback HTTP remains usable while remote HTTPS stays Secure.
- Owner inventory returns only deterministic safe references, labels, roles,
  statuses and bounded timestamps. It omits raw IDs, hashes, secrets, Cookies,
  browser fingerprints, private URLs and Host paths.
- Pairing does not bootstrap an Owner, reset a password, approve an action, or
  reuse Agent enrollment/session tokens.

## Verification

```bash
python3 -m py_compile server.py agentops_mis_core/human_auth.py \
  scripts/human_console_pairing_smoke.py
python3 scripts/human_console_pairing_smoke.py
python3 scripts/human_console_pairing_ui_smoke.py
cd ui/start-building-app && npm run build
```

The isolated HTTP smoke passed with:

- Owner/CSRF enforcement and Owner-role rejection;
- one-time operator redemption and dual Session/device Cookies;
- replay, expiry, explicit revoke and attempt-lock rejection;
- paired-account denial from an unpaired browser;
- Owner-only bounded device inventory;
- device-to-Session cascade revocation;
- complete bounded pairing/device audit actions;
- no secret, raw device ID/hash or Cookie in audit/process output;
- no live Runtime invocation and no user database access.
- static UI checks prove immediate fragment scrubbing, memory-only secret
  handling, no local/session storage, no DOM rendering of the secret, Owner
  controls, bilingual copy and bounded API errors;
- the production React build passes.

Headed browser verification against an isolated temporary Host also passed the
actual Workspace flow: bootstrap Owner, create invitation, copy fragment link,
confirm immediate fragment scrubbing, redeem as Operator, return as Owner, and
read back one redeemed invitation plus one active paired device. The test used
fixture credentials and an isolated SQLite database only. A second tab shares
the same browser Cookie jar, so this is UI evidence rather than a substitute for
the required physical second-computer acceptance.

## Remaining Gates

- add proxy-header hardening before internet exposure; source-independent
  login/pairing throttling is closed in
  `docs/PRIVATE_HOST_AUTH_THROTTLE_ACCEPTANCE.md`, and bounded JSON request
  framing is closed in `docs/PRIVATE_HOST_REQUEST_HARDENING_ACCEPTANCE.md`;
- add cryptographic Host identity, per-Host TLS/SNI, Relay tunnel and deployed
  browser-only acceptance;
- verify a physical second computer, disconnect/reconnect, real Runtime task,
  approval/memory/artifact flow and device revocation at one exact release.
- the existing global AppShell keeps the full sidebar below tablet width; the
  paired-device flow is verified for laptop/desktop browsers, while a separate
  responsive-shell slice is still needed before claiming phone usability.

This local pairing receipt does not claim a deployed Relay or physical
browser-only Console.
