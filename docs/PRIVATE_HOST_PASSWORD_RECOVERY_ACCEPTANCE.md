# Private Host Password Recovery Acceptance

Status: **PASS (isolated product smoke)**
Date: 2026-07-15
Scope: loopback-only recovery for the first Private Host Owner

## Product Boundary

Password recovery is a host-local administrative operation, not a cloud account
service. The application accepts an unauthenticated recovery request only when
its HTTP Origin names a literal loopback address and the request carries the
protected local authority handed to the browser by the AgentOps MIS desktop
application. A normal private-network or public browser Origin cannot receive
or redeem recovery authority.

The successful flow is:

1. The desktop application opens the loopback Console and hands it protected
   local authority through an immediately scrubbed, memory-only URL fragment.
2. The loopback Console uses that authority to request a one-time recovery
   challenge.
3. The server returns the raw authority only to that local response and stores a
   hash in SQLite.
4. The Owner confirms the existing username and supplies a new password.
5. The server changes the password, consumes the challenge, revokes every prior
   active browser session, and issues one new HttpOnly session.
6. Audit rows retain bounded status and safe references, never the authority,
   password, setup code, or raw session value.

There is deliberately no email or hosted-cloud reset path in this release. The
Private Host does not depend on a vendor identity service, and the product does
not collect an email address for recovery. Losing both local host access and the
Owner password therefore remains an operational recovery problem, not something
a remote support operator can override.

## Threat Model

### Protected assets

- Owner password verifier and browser sessions
- One-time recovery authority
- Private Host ledger and Workspace access
- Recovery audit evidence without reusable secrets

### Trust boundary

Physical or OS-level control of the Host is the intended recovery authority for
this MVP. The browser request must carry an `http://` Origin with a literal
loopback IP. The Tailscale/private HTTPS Console and normal LAN or internet web
Origins fail with `local_recovery_required` before a challenge is issued or
redeemed. Origin is defense in depth; the protected desktop-application handoff
remains mandatory even when a caller supplies a loopback-looking Origin.

### Defended abuse paths

- A normal remote browser Origin cannot start recovery and obtain an authority.
- A valid authority cannot be redeemed from a normal remote browser Origin.
- A forged loopback Origin without the protected application handoff cannot
  start recovery.
- Guessing the Owner username returns the same generic invalid-authority error.
- A short password does not consume the otherwise valid challenge.
- A successful recovery invalidates prior authenticated browser sessions.
- A consumed challenge cannot be replayed.
- SQLite and audit rows contain hashes or bounded references rather than raw
  credentials, challenge authority, or browser cookies.

### Accepted MVP risks

- Malware or another user with control of the local OS is inside the recovery
  trust boundary.
- The same local OS user can read the protected Host secret and is inside the
  recovery trust boundary; stronger separation requires local IPC or an OS
  credential broker.
- There is no passkey, MFA, hardware-backed recovery key, email recovery, or
  support-assisted cloud recovery.
- Recovery is currently scoped to the first active Owner on a single Private
  Host; multi-owner recovery policy is not defined.
- Starting a new local challenge invalidates an older active challenge. Local
  rate limiting and recovery-attempt lockout are future hardening work.
- The browser must keep the returned authority ephemeral; it must not persist it
  to local storage, logs, analytics, or URL state.

## Password Policy

The MVP requires at least **12 characters** and applies no uppercase, digit,
symbol, or rotation composition rule. This is a usability/security compromise
for a single-factor local Owner account: users can choose a memorable long
passphrase instead of satisfying arbitrary complexity patterns.

Twelve characters is not a permanent identity architecture. A later release
should prefer passkeys and add optional MFA. Once phishing-resistant sign-in and
a tested recovery-key lifecycle exist, the product can revisit password UX
without weakening the current single-factor boundary.

## Automated Acceptance

Command:

```bash
python3 scripts/human_password_recovery_smoke.py
```

Result on 2026-07-15: **PASS**

The smoke starts `server.py` on a free loopback port with a temporary SQLite
database and fixture-only environment. It does not read the installed Host DB,
start a Runtime, or retain generated state.

Verified checks:

- [x] Owner bootstrap and a second pre-recovery session succeed.
- [x] A remote browser Origin header at recovery start is rejected with HTTP 403.
- [x] A loopback-looking request without application handoff authority is
  rejected with HTTP 403.
- [x] Loopback recovery start returns a single-use local authority.
- [x] A remote browser Origin header cannot redeem a valid authority.
- [x] A password shorter than 12 characters is rejected.
- [x] A wrong username receives the generic invalid-authority response.
- [x] Correct recovery revokes both old sessions and issues an HttpOnly session.
- [x] Both old browser sessions lose Workspace access.
- [x] The consumed challenge cannot be replayed.
- [x] The old password fails and the new password signs in successfully.
- [x] The challenge row is `used` and stores only a hash.
- [x] Recovery audit includes blocked, started, failed, and completed actions.
- [x] Account/session/challenge/audit rows contain none of the raw fixture
  passwords, setup code, authority, or browser cookie values.

Observed bounded evidence:

```text
recovery audit rows: 8
challenge rows: 1 used
session ledger: 2 revoked, 2 active
real user database used: false
real runtime called: false
```

## Browser Acceptance

The production Vite bundle was served by a temporary Private Host and exercised
in a fresh headed browser. The application handoff disappeared from the address
bar before interaction, the initialized Host rendered the compact sign-in state,
and `忘记密码` opened the recovery state without showing any internal authority.
A test reset completed and returned to the existing Workspace. No installed
database, real credential, or Runtime was used, and the fresh corrected bundle
reported no console errors.

## Follow-up Gates

1. Add passkey enrollment and sign-in, with a recovery-key lifecycle.
2. Add optional MFA before relaxing any single-factor password policy.
3. Add bounded local attempt throttling without creating a permanent lockout.
4. Replace the same-user protected-file handoff with local IPC or an OS
   credential broker before claiming hostile-local-user isolation.
5. Define multi-owner recovery approval and workspace isolation semantics.
6. Keep browser tests proving the authority remains memory-only and is cleared
   on completion, navigation, timeout, or error.
