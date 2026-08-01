# Private Host Request Hardening Acceptance

Status: bounded JSON request slice implemented; auth throttling and forwarding-header policy implemented separately

## Closed In This Slice

The Python Host now rejects unsafe JSON framing before authentication or route
handling:

- default request-body limit is 256 KiB;
- `AGENTOPS_MAX_JSON_BODY_BYTES` may set a value from 1 KiB to 1 MiB;
- oversized bodies return `413 request_body_too_large` without reading or
  reflecting the body;
- invalid/negative `Content-Length`, incomplete bodies, malformed UTF-8/JSON,
  non-object JSON and chunked transfer encoding return bounded `400` errors;
- every framing error declares `body_omitted:true` and does not include request
  content in the response or process output;
- POST and PATCH share the same parser and error contract.

The Host deliberately does not accept chunked request bodies in this local
product. Browser and CLI JSON requests have a known bounded size, and rejecting
ambiguous framing keeps the future Relay boundary simple.

## Verification

```bash
python3 -m py_compile server.py scripts/bounded_json_request_smoke.py
python3 scripts/bounded_json_request_smoke.py
python3 scripts/human_browser_auth_smoke.py
python3 scripts/human_console_pairing_smoke.py
git diff --check
```

The isolated smoke uses a temporary database and a 1 KiB test limit. It verifies
oversized, malformed, non-object, invalid-length, incomplete and chunked cases.
It does not call a Runtime or use a real credential.

## Still Blocking Internet Relay Exposure

- source-independent login and pairing throttling is implemented separately in
  `docs/PRIVATE_HOST_AUTH_THROTTLE_ACCEPTANCE.md`;
- direct Host/Origin and fail-closed forwarding-header policy are implemented
  separately in `docs/PRIVATE_HOST_FORWARDED_HEADER_POLICY_ACCEPTANCE.md`;
- SNI enforcement at the future Host TLS/Relay boundary;
- deployed Host-terminated TLS Relay and physical second-computer acceptance.

This receipt does not authorize a non-loopback bind, public quick tunnel,
Tailscale Funnel or production Relay exposure.
