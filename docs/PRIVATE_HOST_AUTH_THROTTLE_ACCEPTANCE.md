# Private Host Authentication Throttle Acceptance

Status: isolated product acceptance implemented; internet Relay exposure remains blocked by separate proxy and transport gates

## Product Contract

Human Login and one-time Pairing use persistent, source-independent throttling.
The policy does not use or persist an IP address, forwarding header, User-Agent,
raw username or raw pairing secret as throttle identity.

- Login has a per-username-hash bucket at 8 failures and an endpoint-global
  bucket at 100 failures in a five-minute window.
- Pairing has a per-secret-hash bucket at 8 failures and an endpoint-global
  bucket at 60 failures. Rotating unknown invitation strings cannot bypass the
  endpoint gate.
- Every active block returns bounded `429 too_many_attempts` JSON with a numeric
  `retry_after_seconds`. The HTTP `Retry-After` value equals the JSON value and
  is bounded by the five-minute block interval, allowing one second for integer
  rounding.
- A successful Login or Pairing clears only that subject bucket. Endpoint-global
  failure history remains intact.
- Active blocks survive a Host process restart because SQLite stores only a
  hashed bucket key, bounded scope, count and timestamps.
- A later authentication failure prunes buckets whose last update is older than
  one day.
- Throttled requests write `human_auth.login_throttled` or
  `human_auth.pairing_throttled` audit actions without raw authentication or
  source input.

## Isolated Smoke

Run from the repository root:

```bash
python3 -m py_compile scripts/human_auth_throttle_smoke.py
python3 scripts/human_auth_throttle_smoke.py
```

The smoke starts a loopback-only temporary Host on an ephemeral port and points
it at a temporary SQLite database. It never contacts the installed Host or a
persistent product database, and `HERMES_ALLOW_REAL_RUN=false` prevents real
Hermes/OpenClaw execution.

The black-box HTTP assertions cover:

1. Seven failed Login attempts followed by a success, proving the subject bucket
   is deleted while the global count remains seven.
2. Login subject blocking at eight failures while rotating User-Agent,
   `X-Forwarded-For` and `Forwarded` values, plus rejection of a correct password
   during the active block.
3. Process restart against the same fixture database, proving the active Login
   block persists.
4. A synthetic bucket older than one day, proving the next failed auth prunes it.
5. Login endpoint-global blocking at 100 failures across fresh usernames and
   varied source headers, including a fresh subject after the threshold.
6. Failed then successful Pairing with one valid invitation, proving subject-only
   reset and preservation of Pairing global history.
7. Pairing subject blocking at eight failures and endpoint-global blocking at 60
   failures across rotated unknown secrets and varied source headers, including
   rejection of a valid invitation while the global block is active.
8. Exact `Retry-After`/`retry_after_seconds` agreement, bounded response keys and
   size, hashed `hatb_<sha256>` bucket keys, the exact bounded table schema,
   bounded audit actions, stale-row absence and no raw fixture/source values in
   throttle rows, throttle audit metadata or captured Host output.

The script prints one bounded JSON receipt and exits nonzero on any failed
assertion. It does not print fixture credentials, generated invitation secrets,
raw bucket rows, raw audit metadata or Host logs.

## Companion Regression

After the isolated smoke passes, the broader non-runtime regressions remain:

```bash
python3 scripts/human_browser_auth_smoke.py
python3 scripts/human_console_pairing_smoke.py
python3 scripts/secret_scan_smoke.py
```

These companion checks are not substitutes for the isolated throttle receipt;
they cover surrounding Human Session, Pairing and repository-secret boundaries.

## Remaining Internet Gates

- direct Host/Origin and forwarding-header policy are implemented separately in
  `docs/PRIVATE_HOST_FORWARDED_HEADER_POLICY_ACCEPTANCE.md`; Host-side TLS/SNI
  enforcement remains a Relay gate;
- deployed outbound Relay with application TLS terminating on the Host;
- physical second-computer task, disconnect and revoke acceptance at one exact
  release.

This receipt does not authorize a non-loopback bind, public quick tunnel,
Tailscale Funnel or production Relay exposure.
