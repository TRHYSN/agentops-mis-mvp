# Private Host Human Service Receipt Acceptance

Status: passed on the installed preview.39 Host for the bounded Owner Human
Session receipt/readback gate; source-branch actor spoof resistance and fresh
service identity precedence still require the next exact-package install.

## Scope

This receipt covers the browser-equivalent Human Session boundary for local
Worker service evidence. It proves that an authenticated Owner Session can:

1. establish a cookie session with CSRF protection;
2. run a local, read-only launchd service check for Hermes and OpenClaw;
3. append one Action Receipt and one Control Readback per adapter only after an
   explicit `--confirm-record` flag;
4. read the resulting bounded hashes and counts through the authenticated API;
5. log out and receive HTTP 401 on a subsequent protected read.

It does not execute service-control, invoke either Runtime, read Worker logs or
query the SQLite database directly. Password material is read from macOS
Keychain into process memory and is never accepted on argv, printed or stored.

## Commands

The default invocation is preview-only and must not change the operator ledger:

```bash
python3 scripts/private_host_human_service_receipt_acceptance.py \
  --base-url http://127.0.0.1:18878 \
  --username <owner-username>
```

The explicit recording invocation appends the bounded receipt/readback pair:

```bash
python3 scripts/private_host_human_service_receipt_acceptance.py \
  --base-url http://127.0.0.1:18878 \
  --username <owner-username> \
  --confirm-record
```

Both invocations are loopback-only. The script rejects a non-loopback base URL
and omits credentials, session tokens, raw prompts, responses, logs and database
rows from its output.

## Installed Preview 39 Result

The preview-only pass returned zero receipt and control-readback deltas while
both local service checks reported their plist present, launchd service loaded,
confirmation gate present and relaunch policy enabled.

The explicit pass appended exactly one receipt and one control readback for
each adapter:

| Adapter | Action Receipt | Control Readback | Receipt hash | Readback hash |
| --- | --- | --- | --- | --- |
| Hermes | `oar_9f5d96f32445` | `ocr_34b71a46201c` | `e146824a785be046235a09312667554671dd7ae5b2593a9157497cc769c38d98` | `b96b19de198dd323cfceda0b00f969bc341e257c3633c70c02003dc2df10ab76` |
| OpenClaw | `oar_225da0adaa7f` | `ocr_0d549bc4e084` | `55f8c22e61da98b4bd9f44ef010864ad74513ea1305a3221ea6fa6f12f7e689a` | `6759b609332f553e711be2fcde6c49e628410cd8ed5e027e81969e2a4c5d39d1` |

For both adapters, the recorded status matched the local read-only service
check, the Human actor matched the authenticated Owner context, the control
readback attached to the receipt, logout succeeded and the post-logout
protected read returned HTTP 401. No service-control or Runtime execution was
performed.

## Source-Branch Hardening

The current source branch additionally:

- overwrites caller-provided `actor_id` with the authenticated Human Session
  account for commander/operator/workflow/worker writes;
- prefers a fresh ready service Worker identity over a merely registered local
  service identity in local-readiness projections;
- prevents an unrelated CLI Agent identity from becoming the target of fast
  service-closure commands.

`human_browser_auth_smoke.py`, `local_readiness_smoke.py --isolated-fixture`
and `operator_service_closure_fast_smoke.py` cover these cases. They are source
evidence, not installed preview.39 evidence, until a new exact package is
published and installed.

## Remaining Gate

This Host-local authenticated receipt does not replace the current-package
physical MacBook browser acceptance. The second computer must still open the
published Host Console, authenticate, inspect the same current-package ledger
evidence, log out and prove protected reads fail closed.
