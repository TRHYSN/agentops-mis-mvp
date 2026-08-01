# Private Host Real Runtime Client Acceptance

Status: deterministic auth gates and fresh local Private Host live Runtime acceptance passed

## Purpose

`scripts/customer_worker_real_runtime_acceptance.py` can now exercise the
customer task endpoint through the same human Session and CSRF boundary used by
the browser console. This removes the prior need to bypass Private Host auth for
manual Hermes/OpenClaw acceptance.

## Credential Boundary

The client reads credentials only from operator-selected environment variables:

```text
AGENTOPS_ACCEPTANCE_PASSWORD
AGENTOPS_OWNER_SETUP_CODE
```

The password and one-time setup code are not accepted as CLI arguments and are
not emitted in result JSON. The human browser Session remains separate from the
Agent Gateway machine credential used internally by the Worker.

## Deterministic Verification

```bash
python3 -m py_compile \
  scripts/customer_worker_real_runtime_acceptance.py \
  scripts/private_host_acceptance_client_smoke.py
python3 scripts/private_host_acceptance_client_smoke.py
git diff --check
```

The isolated smoke proves Owner bootstrap, Session cookie persistence,
authenticated task read, CSRF-protected task creation, and machine-token
separation. It does not call a Runtime and is labeled accordingly.

## Explicit Live Command

After an operator initializes and starts an isolated Private Host, export the
one-time setup code and a temporary acceptance password in the current shell,
then run:

```bash
python3 scripts/customer_worker_real_runtime_acceptance.py \
  --base-url http://127.0.0.1:<host-port> \
  --human-auth \
  --confirm-live \
  --adapter hermes \
  --adapter openclaw \
  --request-timeout 900 \
  --hermes-timeout 600 \
  --hermes-max-tokens 512
```

The live claim is accepted only when both adapters produce fresh task, plan,
run, tool, runtime event, evaluation, approval, artifact, memory, audit, and
verified plan-evidence IDs in the isolated Host ledger. Raw prompts, raw
responses, credential values, private messages, and full transcripts remain
excluded from output and committed state.

The authenticated readback uses the same temporary Owner password environment
variable and a separate browser Session:

```bash
python3 scripts/v1_5_live_product_readiness_smoke.py \
  --base-url http://127.0.0.1:<host-port> \
  --human-auth \
  --require-adapter hermes \
  --require-adapter openclaw
```

It does not accept a machine token as a browser credential and refuses to
bootstrap an Owner because this verification step is read-only.

## Fresh Live Result

On commit `b03235652165b804a8dffb83b3cda94a2680f6f9`, a new isolated Private
Host accepted an Owner Session and completed one explicitly confirmed task per
adapter:

| Adapter | Task | Run | Evidence |
|---|---|---|---|
| Hermes | `tsk_customer_worker_task_hermes_hermes_worker_20260712183626_20260712103626589050` | `run_gw_4eadf2ba75bf` | 1 tool call, 1 evaluation, 15 runtime events, 12 audit events, 2 artifacts, 2 memory proposals, 1 approval, 1 verified plan-evidence manifest |
| OpenClaw | `tsk_customer_worker_task_openclaw_openclaw_worker_20260712183736_20260712103736141541` | `run_gw_f7a007436e36` | 1 tool call, 1 evaluation, 15 runtime events, 12 audit events, 2 artifacts, 2 memory proposals, 1 approval, 1 verified plan-evidence manifest |

Both runs finished with `status=completed`; the acceptance client returned
`ok=true`, `human_session_used=true`, and no failures. The first standalone
readback attempt returned `401` because the read-only client did not yet support
Private Host login. That client gap is now covered by
`private_host_acceptance_client_smoke.py` without making Runtime calls.

This proves host-local authenticated dispatch and ledger closure. It does not
yet prove access from a second physical computer, private-network HTTPS, or
browser disconnect/reconnect behavior.

## Exact-Package Preview 39 Persistent Worker Result

The real Host upgraded to published
`v1.6.0-private-host-preview.39` at exact commit
`17801e3bbb20cdaec68e72f3e225ab5492d8f8e2`, preserved its authority ledger,
and restored the independent Hermes/OpenClaw LaunchAgents. Four bounded Fleet
samples spanning 105 seconds remained `ready` with two execution-capacity
Workers and zero stale or unavailable service Workers.

Two low-risk, metadata-only customer tasks then entered the normal queue and
were consumed by the persistent services without a one-shot Worker command:

| Adapter | Task | Run | Bounded evidence |
| --- | --- | --- | --- |
| Hermes | `tsk_preview39_hermes_retention_policy_20260722T1450Z` | `run_gw_0dfd8981a340` | 1 Tool Call, pass Evaluation, 1 Artifact, 1 Memory, 8 Runtime Events, verified Plan and manifest |
| OpenClaw | `tsk_preview39_openclaw_retention_review_20260722T1450Z` | `run_gw_a46813718b06` | 1 Tool Call, pass Evaluation, 1 Artifact, 1 Memory, 8 Runtime Events, verified Plan and manifest |

Both runs completed with no error. Their Agent Plans scored 100 and their
plan-evidence manifests had no failed checks. Each task had zero Approval and
zero Prepared Action rows because it explicitly prohibited deletion, session
body access and external writes. No raw prompt, response, credential, private
message, transcript, Worker log or database row is retained here.

This closes current-package Host-local persistent Runtime execution. It does
not replace the physical MacBook current-package browser acceptance.

## Exact-Package Preview 39 Human Session Receipt

After the persistent Runtime result, the installed preview.39 Host passed the
separate Owner Human Session service-control-preview receipt/readback gate. The
preview pass made zero operator-ledger writes. The explicit pass appended one
Action Receipt and one Control Readback for each adapter after a local read-only
launchd service check. Both checks found the service present and loaded; the
recorded actor matched the authenticated Owner context, logout succeeded and a
post-logout protected read returned HTTP 401.

No service-control or Runtime invocation was performed by this gate. Exact
bounded receipt/readback IDs and hashes are in
`PRIVATE_HOST_HUMAN_SERVICE_RECEIPT_ACCEPTANCE.md`. Source-branch actor spoof
resistance and fresh service identity precedence remain pending installation in
the next exact package and are not attributed to preview.39.

## Exact-Package Preview 10 Approval-Wall Staging

On exact commit `d7c2ec3a49347ed6899aff3c3406f922a7690279`, the installed
`v1.6.0-private-host-preview.10` Host passed Hermes and OpenClaw Gateway/adapter
preflight and explicitly started both live Worker lanes. Two fresh
customer-style tasks were then dispatched through the installed Agent Gateway
CLI:

| Adapter | Task | Run | Current state |
|---|---|---|---|
| Hermes | `tsk_preview10_hermes_readiness_20260713` | `run_gw_242eac97293e` | `waiting_approval` |
| OpenClaw | `tsk_preview10_openclaw_readiness_20260713` | `run_gw_23bb6ba9f13e` | `waiting_approval` |

Each run has a verified Agent Plan plus one approval request and one bounded
tool record, but no evaluation, artifact, or verified plan-evidence manifest
yet. The Worker processed zero Runtime tasks: the machine credential could not
approve its own plan, so neither Hermes nor OpenClaw was invoked. This is a
successful approval-boundary check, not completed live-Runtime evidence.

The current-package live gate remains open until a locally created human Owner
approves the plans and the resumed exact actions complete with bounded
run/runtime-event/evaluation/artifact/memory/audit evidence.

The Host subsequently upgraded to exact-package preview.11 with verified
pre-update backups. Both run IDs and their `waiting_approval` states persisted;
no model execution is inferred from the upgrade.

## Exact-Package Preview 3 Result

On exact commit `642471f571d9943f9c4c217b3912e32f6728dfce`, the installed
`v1.6.0-private-host-preview.3` package ran with an isolated AgentOps install,
Host data directory and SQLite ledger while preserving the authorized local
Runtime environment. Both adapters completed a new explicitly confirmed
customer task:

| Adapter | Run | Approval | Authority receipt | Bounded evidence |
|---|---|---|---|---|
| Hermes | `run_gw_0c16ac907390` | `ap_customer_worker_delivery_run_gw_0c16ac907390` | `phr_429aeebd31ea54e9ed7817f2` | 1 tool call, 1 evaluation, 15 runtime events, 12 audit events, 2 artifacts, 2 memory proposals and 1 verified plan-evidence manifest |
| OpenClaw | `run_gw_c0dabe5b3e3b` | `ap_customer_worker_delivery_run_gw_c0dabe5b3e3b` | `phr_ced9731c645f5becbec2f158` | 1 tool call, 1 evaluation, 15 runtime events, 12 audit events, 2 artifacts, 2 memory proposals and 1 verified plan-evidence manifest |

The live client returned `ok=true` with no failures. Each delivery initially
remained pending and receipt generation failed closed until a human Owner
approved it. A second Owner Session then read and downloaded each receipt; the
downloaded canonical payload hashes matched the stored authority hashes. No
credential, Cookie, raw prompt, raw response, private message, transcript or
database file was captured in this acceptance record.

This closes the host-local exact-package Runtime and authority-receipt gate. It
does not close the physical second-device HTTPS or disconnect/reconnect gates.

## Exact-Package Preview 4 Async Disconnect Result

On exact commit `1b8f2b9469105ce826e551b5e83fd9d5f0656bff`, the installed
`v1.6.0-private-host-preview.4` package completed fresh explicitly confirmed
Hermes and OpenClaw customer jobs after each first human Session client was
discarded:

| Adapter | Job | Run | Approval | Authority receipt |
|---|---|---|---|---|
| Hermes | `wfjob_ab33425f1f5b3ec6ae4de5ff` | `run_gw_7c88b0db4d2a` | `ap_customer_worker_delivery_run_gw_7c88b0db4d2a` | `phr_d6441f356098629861e67931` |
| OpenClaw | `wfjob_c8a51117c3db4c3adaddf98d` | `run_gw_b66254b6e070` | `ap_customer_worker_delivery_run_gw_b66254b6e070` | `phr_5f09a657d97a469ccb46b922` |

Each same-key replay returned `202`, anonymous job read returned `401`, a fresh
Owner Session observed completion after the disconnect timestamp, and ledger
readback found exactly one matching workflow job and one task run. Both
deliveries passed plan-evidence verification, evaluation and human approval;
their downloaded canonical receipt hashes matched Host authority. No Session
secret, setup code, credential, raw prompt, raw response, transcript or database
content was retained.

This closes the exact-package Host-local async Session-disconnect gate. Physical
second-device private HTTPS, physical tailnet disconnect/reconnect, and a clean
install on another Mac remain external gates.

## Exact-Package Preview 35 Result

The installed `v1.6.0-private-host-preview.35` package at exact commit
`6424ec144013517b21438cd7e528c6db106a0a5e` restored both independently managed
Worker services after the Host service migration. Each Worker wrote a fresh
idle heartbeat after receiving a new process ID.

An unauthenticated call to the customer workflow returned `401` and created no
task, proving that the Private Host did not accept a machine caller as a human
browser Session. A later Owner-authenticated Hermes request created and ran a
fresh real task before the waiting client was interrupted:

| Adapter | Task | Run | Result |
|---|---|---|---|
| Hermes | `tsk_customer_worker_task_hermes_hermes_worker_20260718140610_20260718060610225788` | `run_gw_7ac27edaf52c` | Runtime completed; delivery remains at Human Approval Wall |

The Hermes ledger contains 1 tool call, 1 evaluation, 15 runtime events, 8
bounded audit entries, 2 artifacts, 2 memory candidates and 1 verified
plan-evidence manifest. Delivery approval
`ap_customer_worker_delivery_run_gw_7ac27edaf52c` remains `pending`; no machine
credential or interrupted browser client approved it.

A separate normal Agent Gateway task-create call then assigned a low-risk,
read-only customer review task to the persistent OpenClaw Worker. The Worker
claimed it automatically and completed a fresh real run:

| Adapter | Task | Run | Result |
|---|---|---|---|
| OpenClaw | `tsk_preview35_openclaw_product_review_20260718T0611Z` | `run_gw_45eac4968e30` | Completed |

The OpenClaw ledger contains 1 tool call, 1 evaluation, 13 runtime events, 7
bounded audit entries, 1 artifact, 1 memory candidate and 1 verified
plan-evidence manifest. Its bounded output summary is present and capped at 200
characters. No raw prompt, raw response, credential, private message, full
transcript or database content was retained in this acceptance record.

This proves both authenticated human dispatch to an approval stop and direct
API/Gateway dispatch to a persistent real Worker on the installed package. The
Hermes delivery still requires an explicit human decision before an approved
artifact/receipt claim can be made.

## Exact-Package Preview 36 Negated Read-Only Result

The installed `v1.6.0-private-host-preview.36` package at exact commit
`a5c7d559cfce5157b10401e34204a6b6a405a554` completed a fresh, explicitly
confirmed OpenClaw customer job after the real preview.35-to-preview.36 Host
upgrade:

| Adapter | Job | Task | Run | Result |
|---|---|---|---|---|
| OpenClaw | `wfjob_83cb57da8242e855501f3780` | `tsk_customer_worker_task_openclaw_preview36_20260719t052502z_20260719052502088849` | `run_gw_ed42f579d487` | Completed with no external-write PreparedAction |

The task deliberately prohibited publication, external connectors, messages,
external APIs and file writes while requesting a Host-local review. This is the
wording class that preview.35 misclassified. preview.36 produced one tool call,
one passing evaluation, 15 runtime events, 12 bounded audit rows, artifact and
memory evidence, plus verified plan-evidence manifest
`pem_e1b9275c986daf4b`. Bounded approval readback found zero PreparedActions.
The normal delivery approval
`ap_customer_worker_delivery_run_gw_ed42f579d487` remains `pending`; no human
delivery decision is implied by successful Runtime execution.

The run used a Host-local Owner Session and real OpenClaw Worker. By itself it
was not a physical MacBook receipt and did not close the preview.36 marker or
negated-intent client retest; the next section closes those two rows. Ordinary
Relay, reboot and another-Mac gates remain separate. Raw
prompt, raw response, credential, private origin, Worker log, private message,
full transcript and database content were omitted.

## Exact-Package Preview 36 Physical MacBook Result

The physical MacBook dedicated Console then authenticated to the same installed
`v1.6.0-private-host-preview.36` Host at exact commit
`a5c7d559cfce5157b10401e34204a6b6a405a554` and used the normal AI Employees
dispatch UI. The browser selected OpenClaw, explicitly confirmed live execution and
submitted customer job `wfjob_9940b1e6ea15`. The Host completed task
`tsk_customer_worker_task_7606dfeb537fe9f9` and real run
`run_gw_c8d2ad1aa845`.

The bounded evidence chain contains one tool call, one passing evaluation, 16
runtime events, 11 audit rows, two artifacts, two memory candidates, one
delivery approval and verified plan-evidence manifest
`pem_094a19932cdcc50e`. The task used the negated read-only wording class from
the preview.35 defect and created zero external-write PreparedActions. Delivery
approval `ap_customer_worker_delivery_run_gw_c8d2ad1aa845` remains `pending`;
successful Runtime execution does not imply human acceptance of the delivery.

The MacBook rendered `/admin/runs/run_gw_c8d2ad1aa845` with its Evaluation and
Audit entry points, then logged out through the UI. The next protected Dashboard
request returned HTTP 401. This closes the preview.36 advanced-client marker and
negated-intent physical retest, but does not close ordinary no-Tailscale Relay,
deployed-Relay interruption, Host reboot or another-Mac installation. No raw
prompt/response, credential, Session/CSRF value, private origin, browser
storage, Worker log, private message, transcript or database content was
retained.

## Exact-Package Preview 38 Host-Local Result

The installed `v1.6.0-private-host-preview.38` package at exact commit
`ee3d36c9ae4f123261893376fff012e36fc8a973` completed two fresh,
Owner-authenticated and explicitly confirmed customer tasks. Both asked the
real Runtime to review the AgentOps MIS Workspace and return three actionable
Chinese improvements while prohibiting file writes and external services.

| Adapter | Task | Run | Plan evidence | Result |
|---|---|---|---|---|
| Hermes | `tsk_customer_worker_task_hermes_hermes_worker_20260719152548_20260719072548673410` | `run_gw_c835b4dab9a9` | `pem_da01eaeea65a518a` | Completed |
| OpenClaw | `tsk_customer_worker_task_openclaw_openclaw_worker_20260719152623_20260719072623508530` | `run_gw_be0e8275670f` | `pem_16c802e45dcbd6d9` | Completed |

Each run wrote one tool call, one passing Evaluation, 15 Runtime Events, 12
bounded Audit rows, two Artifacts, two reviewable Memory candidates, one
delivery Approval and one passing plan-evidence manifest. The client returned
`ok=true`, `human_session_used=true` and no failures for both adapters. Delivery
approval IDs `ap_customer_worker_delivery_run_gw_c835b4dab9a9` and
`ap_customer_worker_delivery_run_gw_be0e8275670f` were created; this receipt
does not claim that a human accepted either delivery.

This closes the current-package Host-local real-Runtime repeat required after
the Worker heartbeat package. It does not close the later-discovered sustained
Fleet projection gap for Host-machine Sessions; that source correction still
requires a later exact package. The physical MacBook dedicated Console then
authenticated as Owner, rendered both new Run pages with Evaluation, Runtime,
Approval and Audit entry points, and approved exactly these two deliveries by
Task ID. It downloaded one approved Artifact to a temporary directory without
reading its body; the file was 811 bytes with SHA-256
`3dbe03f31d9c42ffb15f53f18b9b85e010d0d85d7370b89174a788f903e9f6b9`.
After UI logout, the same browser's protected Dashboard request returned HTTP
401. The temporary browser profile, download and SSH/CDP tunnel were removed
without touching normal Chrome.

This is the current-package authenticated physical-browser review receipt. It
does not repeat the preview.35 disconnect/reconnect flow and does not close the
ordinary no-Tailscale Relay, Host reboot or another-Mac gates. No raw prompt,
raw response, credential, private origin, Worker log, private message, full
transcript or database content was retained.
