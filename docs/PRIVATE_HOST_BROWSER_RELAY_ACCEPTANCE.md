# Private Host Browser-Only Relay Acceptance

Status: local Relay daemon executable; public deployment and physical evidence pending

This is the mandatory ordinary-user second-device protocol. The advanced
Tailscale protocol cannot satisfy it.

`LOCAL_RELAY_DAEMON_ACCEPTANCE.md` proves the multi-route daemon, authenticated
Host tunnel binding, persistent epoch and clean service lifecycle on loopback.
That evidence is necessary but does not satisfy this protocol's public
certificate, deployed endpoint, retention, or physical stock-browser gates.

## Preconditions

Host:

- installed from one exact versioned asset with verified checksum/provenance;
- loopback Host, production Workspace, ledger, knowledge, Human Session, and at
  least one explicitly enabled real Hermes/OpenClaw Worker are ready;
- an Owner has explicitly enabled Remote Console;
- the Host generated its TLS private key locally and received a publicly trusted
  certificate for its stable per-Host hostname;
- the outbound Relay tunnel is authenticated and reports no inbound listener,
  router forwarding, Funnel, or direct `0.0.0.0` Python binding;
- one short-lived, single-use, attempt-limited, non-Owner pairing invitation is
  ready. Only its human-delivered secret may cross devices.

Console:

- a separate physical computer with a stock modern browser;
- no Tailscale/VPN client, AgentOps package, repository, Git, Python, Node,
  Hermes, OpenClaw, Agent Gateway token, or Host project file;
- a clean browser profile for unauthenticated and pairing checks.

Relay:

- deployed at the exact candidate version with a stable public endpoint;
- routes opaque TLS by SNI to the authenticated outbound Host tunnel;
- has bounded routing/connection logs and an explicit retention window;
- does not serve Workspace JavaScript, terminate Host application TLS, or own
  an MIS authority database.

## Pairing And Authentication

1. Open the stable per-Host HTTPS URL from the clean Console browser.
2. Verify a publicly trusted certificate for the expected per-Host hostname.
3. Verify unauthenticated workspace/API reads fail closed.
4. Redeem the invitation once. It may provision a non-Owner account or bind an
   existing account according to the release contract.
5. Verify the invitation secret is absent from the URL, browser history,
   Relay/Host logs, audit metadata, and later API responses.
6. Verify a second redemption, an expired invitation, an over-attempt
   invitation, and a role above the invitation cap all fail closed with
   non-enumerating errors.
7. Complete normal Human authentication. Pairing must not bootstrap an Owner,
   grant approval authority above its cap, or reuse a machine credential.
8. Verify the Host lists only a bounded device reference, role, created/last
   seen timestamps, and status; no device secret or browser fingerprint.

## Customer Task Closed Loop

1. Open **Host Acceptance** and refresh Host, ledger, knowledge, Worker, and
   adapter readiness.
2. Create one low-risk acceptance marker and retain only its task ID.
3. From Dispatch Desk, submit one explicitly confirmed customer-style task to a
   currently ready real Hermes or OpenClaw adapter.
4. Retain bounded task/run/adapter IDs and wait until the exact Run starts.
5. Close the browser or disconnect the Console network without stopping the
   Host, Worker, or Relay.
6. Reconnect and verify the same task/run continues or completed with no
   duplicate Run or repeated prepared action.
7. Review its Runtime Events, Tool Calls, Evaluation, Audit, approval, memory,
   and artifact evidence.
8. Stop before a consequential approval/memory decision unless the signed-in
   role is authorized and the human explicitly chooses the outcome.
9. Download only an approved ID-addressed artifact and the Host authority
   receipt. Verify bounded hashes and download audit.

## Revocation And Failure

1. From an Owner Session, revoke the paired device.
2. Verify every Human Session bound to it is invalidated atomically.
3. Verify its open page, API requests, reconnect, and protected downloads fail.
4. Stop the Relay while a Host task runs. Verify local Host/Worker execution
   continues and reconnect does not duplicate work.
5. Attempt stale/replayed tunnel traffic and duplicate request IDs. Verify they
   fail closed or return the original idempotent result.
6. Rotate the Host certificate/tunnel credential and verify the previous
   credential cannot reconnect.

## Privacy Inspection

Inspect Relay process output and durable storage using a bounded test marker.
The run fails if any of these are present:

- HTTP method/path, Cookie, CSRF value, Session/invitation/device secret;
- raw prompt/response, knowledge text, artifact body, private message or full
  transcript;
- Runtime credential, database content, Host filesystem path, or project file.

Allowed Relay evidence is limited to random routing reference, candidate
version, connection epoch, byte counts, bounded timing/status, and coarse error
class under the documented retention window.

## Pass Criteria

- the Console used only a stock browser and no VPN/network-client setup;
- pairing, normal sign-in, role cap, device binding, expiry, attempt limit,
  replay rejection, revocation, and session cascade passed;
- Host-side TLS and per-Host origin were verified; the Relay had no application
  plaintext or authority state;
- a fresh exact-package real Runtime task completed with the bounded MIS
  evidence chain;
- browser/Relay loss did not stop or duplicate Host work;
- approved download, logout/device-revocation denial, privacy inspection,
  exact-head CI, and release provenance passed.

Record only release/commit, OS/browser major versions, bounded device/task/run/
approval/evaluation/memory/artifact/receipt references, hashes, disconnect and
revocation outcomes, and final pass/fail. Never record private hostnames,
credentials, raw content, project files, or unrestricted paths.
