# Private Host Forwarded-Header Policy Acceptance

Status: isolated policy acceptance implemented; deployed Relay and physical
second-computer acceptance remain separate gates

## Policy Contract

The Python Private Host does not use untrusted proxy metadata to decide Human
Auth Origin, browser-cookie security, or canonical request origin. The ignored
request headers are:

- `Forwarded`;
- `X-Forwarded-For`;
- `X-Forwarded-Host`;
- `X-Forwarded-Port`;
- `X-Forwarded-Proto`;
- `X-Real-IP`.

These headers are ignored, not rejected. A request carrying them continues
through the ordinary Human Auth and route checks. An allowed browser `Origin`
still passes, and an untrusted browser `Origin` still returns
`403 origin_validation_failed`; no forwarding value can convert one result into
the other.

Canonical request origin uses the direct HTTP `Host` only when it exactly
matches a configured `http`/`https` allowed Origin, or when it names literal
loopback. Forwarded host, port, and protocol values cannot rehabilitate an
unknown direct Host. Literal loopback canonicalizes to `http`, while an exact
configured HTTPS Origin retains its configured `https` scheme.

Cookie security keeps the advanced Tailscale behavior:

- a configured HTTPS tailnet Origin receives a `Secure` Human Session cookie,
  even when forwarding headers claim loopback, HTTP, or port 80;
- a literal HTTP loopback Origin receives a non-`Secure` Human Session cookie,
  even when `X-Forwarded-Proto` or `Forwarded` claims HTTPS and port 443.

Raw forwarding values are request metadata only. They are not throttle
identity, authority, canonical origin, durable product state, audit metadata,
or Host log content.

Private Host also fails closed when the Origin allowlist is empty. An invalid
`AGENTOPS_COOKIE_SECURE` value falls back to the secure Private Host default
instead of silently disabling `Secure` cookies. Local development keeps its
explicit loopback behavior.

## Isolated Smoke

Run from the repository root:

```bash
python3 -m py_compile scripts/private_host_forwarded_header_policy_smoke.py
python3 scripts/private_host_forwarded_header_policy_smoke.py
git diff --check -- scripts/private_host_forwarded_header_policy_smoke.py docs/PRIVATE_HOST_FORWARDED_HEADER_POLICY_ACCEPTANCE.md
```

The smoke starts two source-tree Hosts on ephemeral loopback ports. Each Host
uses its own temporary HOME and SQLite database. It disables real Hermes and
OpenClaw execution and never contacts an installed Host.

The two scenarios are:

1. A configured `https://...ts.net:8443` Origin and matching direct Host, with
   forwarding values that claim HTTP, loopback, and port 80.
2. A literal `http://127.0.0.1:<ephemeral-port>` Origin, with forwarding values
   that claim HTTPS, an attacker host, and port 443.

For each scenario, the smoke sends every ignored header separately and all six
together. It verifies:

- six allowed-Origin logins succeed and six attacker-Origin logins fail with
  `origin_validation_failed`;
- Human Auth status and every login retain the scenario's expected cookie
  security decision;
- canonical-origin helpers and the server request-base wrapper return the same
  direct configured/loopback Origin in all seven header cases;
- a non-allowed direct Host remains non-canonical even when forwarding metadata
  claims the allowed host and HTTPS;
- bootstrap requests carrying all forwarding headers are accepted, proving the
  policy is ignore-and-continue rather than header rejection;
- unique raw marker values are absent from the SQLite database bytes, Human
  Auth audit rows, and captured process stdout/stderr.
- an empty Private Host Origin allowlist returns a bounded configuration error,
  and a malformed cookie-security setting remains secure.

The script prints one bounded JSON receipt without raw marker values, fixture
credentials, database rows, or Host logs. Any failed assertion makes it exit
nonzero.

## Tailscale Invariants

This slice does not change or execute any Tailscale command. Existing advanced
user-managed Tailscale Serve setup, status, disable, and cleanup commands remain
unchanged. Tailscale Serve remains an advanced private-network fallback and is
not the ordinary browser-only Relay path. The Host does not need to trust
Tailscale-injected forwarding headers to preserve HTTPS Origin and Secure-cookie
semantics.

The smoke does not enable Serve, Funnel, a non-loopback Python bind, router
forwarding, or a public tunnel. It is not deployed Relay evidence and does not
authorize internet exposure.

## Remaining Gates

- Host/SNI enforcement at the deployed outbound Relay and Host TLS boundary;
- deployed Relay privacy/retention evidence;
- exact-release physical second-computer pairing, task, disconnect, reconnect,
  logout, and device-revocation acceptance.
