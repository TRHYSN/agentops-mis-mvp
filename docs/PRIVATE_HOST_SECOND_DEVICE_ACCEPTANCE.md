# Advanced Tailscale Second-Device Acceptance

Status: advanced Tailscale physical browser workflow partially accepted; preview.36 Host package and its marker/negated-intent fixes are physically accepted; ordinary browser-only Relay protocol pending

This document now covers the advanced private-network fallback only. It no
longer defines the ordinary customer onboarding path and cannot close the
browser-only Console gate in `LOCAL_HOST_REMOTE_CONSOLE_SPEC.md`. Ordinary
acceptance must use a second computer with only a browser through the deployed
Relay, without installing or configuring Tailscale.

This protocol proves that a computer without the AgentOps MIS repository,
Python, Node, Git, Hermes, OpenClaw, or an Agent Gateway token can operate the
Private Host through a browser on the same trusted tailnet. Passing local or CI
smokes does not satisfy this protocol.

## Roles And Boundary

- The Host computer runs the production Host, ledger, knowledge index, Worker,
  and explicitly confirmed Hermes/OpenClaw adapter.
- The Console computer has only Tailscale, a modern browser, and a human MIS
  account.
- Tailscale Serve remains private and must not use Funnel. Use HTTPS port 8443
  when port 443 is already owned by OpenClaw.
- Browser Session credentials and Agent Gateway machine credentials remain
  separate.
- A browser-generated device checklist is non-authoritative. The authoritative
  acceptance receipt is generated and hashed by the Host from ledger evidence.

## Preconditions

On the Host:

1. Install one exact versioned Private Host asset after verifying its published
   SHA-256.
2. Run `agentops host init`, `agentops host start`, `agentops host doctor`, and
   `agentops host status`.
3. Manually start and sign in to Tailscale.
4. Review `agentops host tailscale-preview --https-port 8443`.
5. Apply only with explicit operator confirmation, then restart the Host:

   ```bash
   agentops host tailscale-apply --https-port 8443 --confirm
   agentops host restart
   agentops host console-url
   ```

6. Confirm that an unrelated Serve target, including OpenClaw on port 443, was
   not replaced.

On the Console:

1. Join the same trusted tailnet.
2. Do not clone the repository or install AgentOps MIS, Python, Node, Git, or an
   Agent Runtime.
3. Open the HTTPS Console URL without adding credentials to the URL.

## Three-Step Console Quick Start

1. Install or open Tailscale on the Console computer and join the same trusted
   tailnet as the Host.
2. Open `<private-console-url>/workspace` in a modern browser and sign in with a
   human MIS account. Enter the password manually; never place it in a Codex
   prompt, URL, screenshot, shell command or acceptance receipt.
3. Keep all work in the browser. The Console computer does not need the
   repository, AgentOps MIS, Python, Node, Git, Hermes, OpenClaw or an Agent
   Gateway token.

The Host must remain powered on with AgentOps MIS, Tailscale and at least one
ready Worker running. If the private URL does not open, first confirm the
Console is on the same tailnet; do not enable Funnel or expose the Host through
a router port as a workaround.

## Browser-Only Codex Acceptance Prompt

After the operator has manually signed in, the following prompt can be given
to Codex on the Console computer. Do not include the password or private URL in
the prompt; open the authenticated tab first.

```text
You are the independent second-device acceptance operator for AgentOps MIS.
Use only the already-open, already-authenticated browser tab. Do not inspect or
request passwords, cookies, browser storage, session values, setup codes, API
keys or machine tokens. Do not clone a repository and do not install AgentOps
MIS, Python, Node, Git, Hermes, OpenClaw or another Agent Runtime.

Goal: verify that this dependency-free Console computer can operate the remote
Private Host through the browser while all ledger, knowledge and AI execution
remain on the Host.

Run this bounded flow:
1. Confirm Workspace data is visible only after the existing human login.
2. Open Admin Console > Host Acceptance and refresh readiness.
3. Confirm ledger, knowledge, Worker and adapter states are explicit.
4. Create one low-risk acceptance marker task and retain only its task ID.
5. From Dispatch Desk, submit one explicitly confirmed Hermes or OpenClaw
   customer task and retain only task/run IDs.
6. Tell me when the run has started so I can disconnect this computer from the
   tailnet. After I reconnect, verify the same task and run continued without a
   duplicate.
7. Review the related Evaluation, Audit, approval and memory candidate. Stop
   before any consequential approval decision and ask me to choose approve or
   reject.
8. After my decision, download only an approved ID-addressed artifact and the
   Host acceptance receipt.
9. Log out, then verify protected workspace reads and downloads fail closed.

Return only bounded evidence: release version, Console OS/browser major
version, task_id, run_id, adapter, approval_id, evaluation status, memory_id and
decision, artifact_id/hash, receipt_id/hash, disconnect/reconnect result,
logout-denial result and final pass/fail. Do not quote raw prompts, model
responses, knowledge text, project files, audit bodies, usernames, private
URLs, DNS names, IPs or local filesystem paths. Do not claim success for a step
you did not directly verify.
```

## Browser Acceptance Flow

Perform the following from the Console browser:

1. Verify that an unauthenticated browser cannot read workspace data.
2. Sign in with a human account and open **Admin Console > Host Acceptance**.
3. Refresh Host readiness and verify ledger, knowledge, Worker, and adapter
   states are explicit (`ready`, `degraded`, or `unavailable`).
4. Create the low-risk acceptance marker task from the page and verify that its
   task ID is readable after refresh.
5. Open Dispatch Desk and submit one explicitly confirmed Hermes or OpenClaw
   customer task. Record only the returned task and run IDs.
6. Close the browser or disconnect the Console from the tailnet while the Host
   Worker continues. Reconnect and verify the same task and run.
7. Review the related Evaluation and Audit entries.
8. Decide the related prepared action or delivery approval with an authorized
   human account.
9. Review the related memory candidate; approve or reject it deliberately.
10. Download only the approved ID-addressed artifact from Task or Run Detail.
11. Generate the Host authority acceptance receipt for the completed run, then
    download its JSON through the authenticated browser.
12. Log out and verify that the receipt and artifact downloads fail closed.

## Pass Criteria

The run passes only when all of these are true:

- the Console required no project or Runtime dependency;
- HTTPS used a private tailnet URL and no Funnel/public route;
- human login, CSRF, Origin, workspace, and role enforcement remained active;
- the marker task and real customer task were created from the Console Session;
- a fresh explicitly confirmed Hermes or OpenClaw run completed on the Host;
- browser or tailnet disconnect did not stop or duplicate the Host run;
- reconnect showed the same task, run, evaluation, audit, memory, approval, and
  artifact evidence;
- approved artifact download succeeded and was audited;
- logout made protected reads/downloads fail;
- the Host receipt hash matched its canonical bounded payload;
- no credential, Session, CSRF value, setup code, raw prompt/response, private
  message, full transcript, database row content, or Host filesystem path was
  captured in the receipt, screenshots, recording, or repository.

## Bounded Evidence Record

Record only:

```text
acceptance date (UTC)
release tag and exact commit
Host operating-system major version
Console operating-system and browser major version
private HTTPS used: true/false
Tailscale Funnel used: false
task_id
run_id
adapter
approval_id
evaluation_id or evaluation status
memory_id and decision
artifact_id and bounded artifact metadata SHA-256
Host acceptance receipt_id and payload SHA-256
disconnect/reconnect passed: true/false
logout denial passed: true/false
final pass/fail and bounded failure reason
```

Do not record the tailnet DNS name, IP address, account email, username,
password, setup code, cookies, tokens, URL query, raw model content, knowledge
text, project files, database path, or unrestricted Host paths.

## Superseded Preview 28

Host-side staging was refreshed on 2026-07-14 with
`v1.6.0-private-host-preview.28` at exact commit
`f627e83aae357ce4733123208a9d41c037803434`:

- GitHub download and published checksum verification passed;
- upgrade created a pre-update ledger backup and preserved user data;
- Host health and the managed preview.28 production UI are ready; Worker
  Status reports two fresh external-service Hermes/OpenClaw Workers and two
  execution-capacity lanes while correctly keeping Host process verification
  separate;
- the published no-repository bootstrap passed release-consumer
  install gates from the public GitHub tag in a fresh temporary HOME without a
  repository on the consumer; this same-Mac receipt is not another-Mac proof;
- installed Agent Gateway CLI configuration is machine-only, confirmation
  gated, origin bound and separate from the browser Session;
- installed Gateway, Hermes and OpenClaw preflight checks passed without live
  execution;
- private Tailscale HTTPS uses port 8443 with one exclusive MIS handler;
- Funnel is disabled and the unrelated port 443 target remains unchanged;
- the private HTTPS Workspace returned HTTP 200;
- the self-validating existing Workspace account UI, installed
  HTML/CSS/JavaScript and exact installed version were verified locally and
  matched the release build; installed browser metadata now uses the AgentOps
  MIS product identity rather than the original Figma starter title;
- one Hermes and one OpenClaw Worker are running, and same-adapter duplicate
  Host ownership now fails closed without terminating an external Worker.

Fresh Hermes run `run_gw_242eac97293e` and OpenClaw run
`run_gw_23bb6ba9f13e` are `waiting_approval`. This is expected: machine
credentials staged the work but could not approve their own Agent Plans. No
Runtime execution is claimed for these runs until a human Owner approves them.

Owner bootstrap is still required. No physical second-device browser login,
task dispatch, approval, evaluation/audit/memory review, artifact download,
disconnect/reconnect or logout-denial receipt is attached yet. Automated
browser runtimes outside the Host tailnet are not accepted as a substitute.
No tailnet DNS name, IP address, account identifier, credential or setup code is
recorded in this document.

## Current Preview 31

Host-side staging was refreshed on 2026-07-15 with
`v1.6.0-private-host-preview.31` at exact commit
`fed1b2410d6725a217c9727dba570db62cc46963`:

- candidate and public clean-HOME install/start/status/stop passed without a
  repository; candidate, Draft and public assets were byte-equal;
- exact-commit push CI `29391744378` and pull-request CI `29391746311` passed;
- the Release was published through the manual prerelease path; the Private
  Host Preview Release workflow did not run;
- the real Host upgraded from preview.30 with explicit Host LaunchAgent
  unload, install and load steps, created a pre-update backup, preserved user
  data and reported `previous_version=1.6.0-private-host-preview.30`;
- the existing first Owner remained present and login-ready;
- the independently managed Hermes and OpenClaw services survived installation
  and were explicitly restarted from preview.31;
- one real OpenClaw task and one real Hermes task completed with bounded ledger
  evidence and verified plan-evidence manifests.

This same-Mac evidence does not close the physical second-computer protocol.
No physical second-device login, real network disconnect, physical logout,
reboot or another-Mac installation is claimed. A fresh Owner Human Session over
the private HTTPS route rejected the two new low-value memory candidates and
two conservative prepared-action false positives, then logged out. That closes
the Host-side Owner review gate only; it is not a physical Console receipt. No
external evidence is synthesized from local, CI, or prior-preview receipts.

## Preview 36 Host Staging

`v1.6.0-private-host-preview.36` was published from exact commit
`a5c7d559cfce5157b10401e34204a6b6a405a554`. Exact-head push and pull-request
Backend/UI CI passed. The five candidate assets were reproduced byte-for-byte,
matched their Draft and public downloads, and passed isolated no-repository
candidate, Draft and public network install/start/status/stop checks.

The real Mini Host then upgraded from preview.35 after a fresh verified ledger
backup and explicit Host/Worker service unload. The public installer preserved
Host data, created another verified pre-update backup and bound the installed
version to the exact preview.36 commit. The Host returned to `ready`, both
launchd-managed Hermes/OpenClaw Worker processes returned, the private transport
remained ready and Funnel remained disabled. No Tailscale configuration,
credential, private origin or database content was copied into the repository.

This package includes the blank-owner marker normalization and the shared
negation-aware external-write classifier found by the preview.35 MacBook flow.
Their deterministic local gates pass. A fresh Host-local real OpenClaw job
`wfjob_83cb57da8242e855501f3780` completed run `run_gw_ed42f579d487` using the
previously misclassified negated read-only wording and created zero
external-write PreparedActions. Its delivery approval remains pending. The
physical retest below independently verifies both fixes through the current
package. The earlier preview.35 browser receipt remains valid historical
evidence for disconnect/reconnect, deliberate review and approved downloads;
it is not silently relabeled as preview.36 acceptance.

## Preview 36 Physical MacBook Retest

The physical MacBook dedicated Console authenticated to the installed
`v1.6.0-private-host-preview.36` Host at exact commit
`a5c7d559cfce5157b10401e34204a6b6a405a554` through private Tailscale HTTPS.
The Console still had no AgentOps repository, Python, Node, Hermes, OpenClaw or
machine token, and Funnel remained disabled.

From **Admin Console > Host Acceptance**, the browser created marker task
`tsk_570cb03937f6`. Same-origin authenticated readback found one related runtime
event and two audit rows, with zero runs, tool calls or evaluations. The task
remained low risk, zero budget and unassigned, so the fixed browser action did
not invoke a Runtime or external connector.

From the normal AI Employees customer-dispatch UI, the same browser selected
OpenClaw, checked the explicit live confirmation and submitted job
`wfjob_9940b1e6ea15`. It completed task
`tsk_customer_worker_task_7606dfeb537fe9f9` and run
`run_gw_c8d2ad1aa845` on the Host. Bounded readback found one tool call, one
passing evaluation, 16 runtime events, 11 audit rows, two artifacts, two memory
candidates, one delivery approval and verified plan manifest
`pem_094a19932cdcc50e`. The negated read-only wording class produced zero
external-write PreparedActions. Delivery approval
`ap_customer_worker_delivery_run_gw_c8d2ad1aa845` remains pending; no product
claim treats Runtime completion as a human delivery decision.

The MacBook rendered `/admin/runs/run_gw_c8d2ad1aa845` with the run and its
Evaluation/Audit entry points visible. UI logout completed, and an immediate
protected Dashboard request returned HTTP 401. No raw prompt/response,
credential, Session/CSRF value, private origin, browser storage, Worker log,
private message, transcript or database content was recorded.

## Preview 35 MacBook Client Staging

During the `v1.6.0-private-host-preview.35` release from exact commit
`6424ec144013517b21438cd7e528c6db106a0a5e` and the real Host upgrade, a
physical MacBook served as an advanced Tailscale Console client while the Mac
mini retained the ledger, knowledge, Host and Runtime authority. The MacBook
required no project checkout, Python, Node, Hermes or OpenClaw.

Before maintenance, the MacBook reached the private HTTPS Workspace with HTTP
200 and the dedicated Console application opened a Chrome window titled
`AgentOps MIS`. A separate temporary no-account Chrome profile rendered the
real 1280 x 800 login Workspace, including the existing navigation and account
form, rather than a blank page. That temporary process was stopped without
reading or modifying the user's normal Chrome profile.

The same physical MacBook then exercised the unauthenticated boundary through
the private HTTPS origin. Dashboard metrics, approvals, audit logs and memories
all returned HTTP 401. An unauthenticated task create also returned HTTP 401,
and the bounded Host task count remained 25 before and after the request. No
response body, cookie, Session value, credential or private origin is retained
in this acceptance record. This proves physical-client fail-closed behavior;
it does not prove an authenticated Console workflow.

The Console became unavailable only during the explicit Host service
maintenance window. After preview.35 installation, exact legacy migration and
Host reload, the same MacBook again received HTTP 200. It remained reachable
while a fresh OpenClaw Worker run executed on the Host. Tailscale Serve remained
the transport and Funnel stayed disabled.

That initial network/render receipt was extended on 2026-07-18 UTC by the
authenticated browser workflow below. The advanced Tailscale client now proves
human login, real OpenClaw dispatch, browser disconnect/reconnect, deliberate
delivery and memory review, Evaluation/Audit readback, approved artifact and
Host receipt download, and logout denial. The preview.36 section above closes
the later exact-package marker and negated-intent retest. It is still not the
full ordinary protocol pass: the deployed Relay, Host reboot and another-Mac
clean installation gates remain open. No private URL, DNS name, IP address,
credential, cookie, raw model content or screenshot artifact is committed
here.

## Preview 35 Authenticated MacBook Evidence

This receipt used the installed
`v1.6.0-private-host-preview.35` package at exact commit
`6424ec144013517b21438cd7e528c6db106a0a5e`. The Host was macOS major 26 and
the physical Console was macOS 26.5 with Chrome major 150. The Console used the
dedicated AgentOps MIS browser profile, used no AgentOps project checkout or
Agent Runtime dependency for the workflow, reached the Host through private
Tailscale HTTPS, and
never enabled Funnel. Host status after the flow remained ready with Human
login available; the ordinary Relay remained unconfigured and undeployed.

### Real Run And Human Decisions

The MacBook Owner Session submitted customer job
`wfjob_ec747fe27ab2`, which created task
`tsk_customer_worker_task_651b111ba9c71b15` and exactly one OpenClaw run
`run_gw_edfe2753846f`. After the run entered `running`, the dedicated Console
browser was closed. The Host and both independently managed Runtime Workers
remained ready, and the job completed while the Console was disconnected.
Reopening the Console and signing in showed the same completed job, task and
run; bounded ledger readback found one workflow-job row and one run row, so no
duplicate was created.

The completed evidence chain contained passing evaluation
`eval_gw_run_gw_edfe2753846f_rule` with score `1.0`, verified plan manifest
`pem_825774cd8cac45dc`, artifact
`art_customer_worker_task_run_gw_edfe2753846f`, and low-risk delivery approval
`ap_customer_worker_delivery_run_gw_edfe2753846f`. The Owner approved only that
delivery. Memory candidates `mem_customer_worker_task_run_gw_edfe2753846f`
and `mem_gw_1f3c3b069c3bafdb` were deliberately rejected as generic or
transient rather than promoted into project memory. The matching Evaluation and
Audit entries were then visible from the MacBook UI.

One earlier job, `wfjob_b2321f7faf73`, exposed a usability defect: negated
phrases such as prohibiting publication or external connectors were
conservatively interpreted as external-write intent. Prepared action
`ap_prepared_action_b673fa1a19408a4b` was not approved and no consequential
action was executed. Current source centralizes external-write intent in a
negation-aware, fail-closed classifier and covers the observed wording plus
mixed real-write instructions. The fix is not present in preview.35; the
preview.36 physical retest above closes it. The failed preview.35 job is not
counted as the successful customer run below.

### Downloads, Receipt And Logout

After approval, the browser downloaded the ID-addressed artifact. Its Host
authority receipt was `phr_c2ea51dd3d37a09055e20889`, with bounded evidence:

```text
adapter: openclaw
artifact_id: art_customer_worker_task_run_gw_edfe2753846f
artifact_metadata_sha256: 0d26751afa89b8ff85041ff81dff9301ac33e9fbc53d7764d2c88fc01ec2b38b
downloaded_artifact_sha256: 236b8753da671cfd8a1bfcd88c83154977cc45c7d18c44d443de8de2a880cbf9
receipt_id: phr_c2ea51dd3d37a09055e20889
receipt_payload_sha256: ffce1b92ec7872f45b9d74f246ddc0738a46b77c54bff84265655500ec664bba
downloaded_receipt_sha256: 4d3476361f7f18fdb23f4ea1e2477c30afe076e75c59cd879378c3129dd35869
disconnect/reconnect passed: true
logout denial passed: true
```

The artifact body and receipt JSON were not read into this record. After UI
logout, the same physical browser received HTTP 401 for dashboard metrics, the
artifact download and the authority-receipt download.

### Remaining Acceptance Gap

The Host Acceptance page's low-risk marker creation returned HTTP 500 on the
installed preview.35 because an intentionally blank `owner_agent_id` reached a
foreign-key write as an empty string. Source commit
`70bae606c577191041778a92e3480138f3b67795` normalizes it to SQL `NULL`,
preserves an explicit zero budget, and adds an authenticated Owner/CSRF smoke
that proves the marker creates only task, runtime-event and audit evidence with
no run, tool call, evaluation or real Runtime invocation. The deterministic
smoke passes, preview.36 packages the fix, and marker `tsk_570cb03937f6` now
closes the exact-package physical retest.

Therefore the authenticated advanced-Tailscale workflow is accepted across two
package-bound receipts: preview.36 closes the current-package marker, real task,
Run-page and logout rows; preview.35 remains the historical evidence for
disconnect/reconnect, deliberate review and approved downloads. The overall
second-device protocol remains partial. Ordinary customer acceptance still
requires a browser-only deployed Relay with no Tailscale client, Host
logout/reboot recovery and another clean Mac install.

## Preview 38 Authenticated MacBook Review

The physical MacBook dedicated Console authenticated to installed
`v1.6.0-private-host-preview.38` at exact commit
`ee3d36c9ae4f123261893376fff012e36fc8a973` through the preserved advanced
Tailscale HTTPS profile. The temporary Chrome profile used
`--no-proxy-server` and a loopback-only SSH/CDP tunnel; normal Chrome, network
settings and the `codex-resume` terminal were not touched.

The Owner Session rendered current Hermes run `run_gw_c835b4dab9a9` and
OpenClaw run `run_gw_be0e8275670f`. Both pages exposed the expected Evaluation,
Runtime, Approval and Audit entry points. The browser then opened the Approval
Inbox and approved exactly the two new low-risk deliveries by their full Task
IDs; each matching card lost its approval action and displayed the approved
state.

The OpenClaw Task page exposed two approved Artifact download controls. One
download completed in a private temporary directory. Only its size and hash
were inspected: 811 bytes and SHA-256
`3dbe03f31d9c42ffb15f53f18b9b85e010d0d85d7370b89174a788f903e9f6b9`.
The file body and name were omitted. UI logout restored the login surface, and
the same browser's next protected Dashboard request returned HTTP 401. The
temporary profile, download directory and tunnel were removed.

This advances the authenticated physical receipt to the current package. It
does not repeat the preview.35 browser-disconnect timing proof, install
AgentOps or a Runtime on the MacBook, or close the ordinary no-Tailscale Relay,
deployed-Relay interruption, Host reboot and another-Mac gates. No credential,
Cookie, Session/CSRF value, private origin, raw prompt/response, task body,
Worker log, private message, transcript or database content was retained.
