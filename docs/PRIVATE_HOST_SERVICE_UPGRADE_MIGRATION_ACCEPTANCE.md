# Private Host Service Upgrade Migration Acceptance

## Problem

The preview.34 package installed successfully and the Host data remained
healthy, but its CLI could not replace the preview.33 LaunchAgent definition.
preview.34 introduced the private `--managed-launch-agent` process-identity
gate, so the preserved preview.33 plist was no longer byte-equal to the current
template. Falling back to the previous CLI restored service availability, but
that is a recovery procedure rather than a product-grade upgrade path.

## Corrective Contract

The current source supports one bounded legacy migration:

- current exact managed definitions retain their existing behavior;
- the only accepted legacy bytes are the immediately previous Host-only
  definition without `--managed-launch-agent`;
- the legacy file must be owned by the current user with exact mode `0600`, be
  a regular file rather than a symlink, live in a current-user-owned directory
  that is not group/other writable, contain no credential-like material and use
  the exact managed paths;
- launchd must report the legacy service unloaded;
- when the exact legacy service is loaded, the current CLI permits only an
  explicitly confirmed unload; load and restart remain blocked;
- mutation still requires both `--overwrite` and `--confirm-install`;
- replacement uses the existing atomic writer and must pass the current exact
  post-write service check.

The source continues to reject arbitrary same-label definitions, changed
commands, added environment values, credential-bearing files, path edits,
unsafe file modes/directories, symlinks, unverified launchctl state and loaded
legacy services.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py \
  scripts/private_host_background_service_smoke.py
python3 scripts/private_host_background_service_smoke.py
python3 scripts/private_host_lifecycle_smoke.py
python3 scripts/private_host_managed_restart_supervisor_smoke.py
python3 scripts/private_host_relay_managed_restart_rollback_smoke.py
git diff --check
```

The background-service smoke uses a temporary Host home, service path and fake
launchctl. It proves an unloaded exact legacy definition migrates to the
current template, while a loaded exact legacy definition and every edited
variant remain unchanged and fail closed.

## Preview 35 Release And Real Upgrade

`v1.6.0-private-host-preview.35` was published from exact commit
`6424ec144013517b21438cd7e528c6db106a0a5e`. Both exact-head backend jobs and
both production UI jobs passed. The five candidate assets were reproducible,
matched their Draft downloads byte-for-byte, and passed an isolated
no-repository install/start/status/stop receipt before publication.

The real preview.34 Host ledger was backed up and verified before maintenance.
The independent Hermes and OpenClaw services were explicitly unloaded first.
The preview.35 release payload then exercised the new unload-only path against
the loaded exact previous service definition. launchd reported the service
unloaded and the Host stopped; no plist or Tailscale mutation occurred during
that action.

The public bootstrap downloaded and verified preview.35, preserved user data,
created another pre-update backup and reported preview.34 as the previous
version. The installed preview.35 CLI first returned a successful dry-run
legacy migration plan. The separate confirmed overwrite atomically replaced
the previous plist, and readback proved the current exact definition, unloaded
state and no credential material. A confirmed load returned the Host to ready,
the private Tailscale route remained ready with Funnel disabled, and both
independent Worker services were reloaded with fresh idle heartbeats.

This closes the specific service-upgrade migration defect without modifying
the immutable preview.34 release. It does not claim signed/notarized packaging,
physical logout/reboot persistence or unattended automatic upgrades.

## Preview 36 Release And Real Upgrade

`v1.6.0-private-host-preview.36` was published from exact commit
`a5c7d559cfce5157b10401e34204a6b6a405a554`. Push CI `29671655369` and
pull-request CI `29671656879` passed both Backend deterministic smokes and the
production UI build. Two candidate builds were byte-equal; all five candidate,
Draft and public assets matched. Isolated candidate, Draft and public network
consumers each completed no-repository install/start/status/stop with exact
version and commit readback.

The real preview.35 Host ledger received a fresh verified backup before the
maintenance window. Both launchd-managed Worker services were explicitly
unloaded and their processes stopped. The Host `bootout` command returned zero,
and independent launchd/process checks confirmed the Host was unloaded while
Tailscale remained present. The public bootstrap then installed preview.36,
preserved Host data, created a verified pre-update backup and recorded
preview.35 as the previous version. Installed provenance and the `current`
symlink both bind to preview.36 and its exact commit.

The new CLI passed Host service preflight, loaded the Host successfully and
returned health `ready` from the preview.36 UI/API. The private transport
remained ready with Funnel disabled. Hermes and OpenClaw Worker service checks
passed, both LaunchAgents loaded, both worker processes returned, and bounded
authenticated status reported two execution-capacity lanes with fleet status
`ready`. No Worker logs, model bodies, credential, private origin or database
content were retained.

The maintenance window also exposed a narrower convergence defect in the old
preview.35 CLI: its immediate post-`bootout` state read briefly remained
`loaded`, so it returned `ok:false` even though the command succeeded and the
service was independently confirmed unloaded. This is not treated as a passed
CLI receipt. Follow-up source commit `a88b5fa` adds four bounded 100ms state
reads and a deterministic stale-then-converged regression, while a
never-converging launchd state continues to fail closed. The fix still requires
a later package before it becomes installed release behavior.

## Preview 37 Release And Real Upgrade

`v1.6.0-private-host-preview.37` was published from exact commit
`6a87e048b7a8e40f5d33c50983c7d0c482804ffc`. Push CI `29675492028` and
pull-request CI `29675493139` passed Backend deterministic smokes and the
production UI build. Two candidate builds were byte-equal. Five Candidate,
Draft and public assets were compared in separate directories, and isolated
Candidate, Draft and public-network consumers each completed no-repository
install/start/status/stop with exact provenance readback.

The real preview.36 ledger received a fresh verified backup before maintenance.
Both Worker LaunchAgents were explicitly unloaded and independently confirmed
absent. The preview.36 Host `bootout` returned zero but its immediate readback
again returned `ok:false`; independent launchd, process and port checks proved
the Host was unloaded. This remains a failed preview.36 CLI receipt and is the
behavior preview.37 was built to correct.

The first public-installer attempt stopped before changing `current` because
the automatic pre-update backup could not complete on a nearly full data
volume. The verified manual backup remained valid, preview.36 remained current,
and no Host or Worker restarted. Only the failed partial backup, a separately
verified byte-identical debug duplicate and stopped `/tmp` consumer homes from
this maintenance window were removed. No historical backup or authority
ledger was deleted. The retry retained the backup gate, created and verified a
new pre-update backup, preserved Host data, recorded preview.36 as the previous
version and bound `current` to preview.37 at the exact release commit.

The installed preview.37 CLI then completed a real service roundtrip. Initial
load converged in one read. Confirmed unload returned `ok:true`, required two
bounded reads to observe `loaded:false`, and independent process/port checks
agreed. The following load converged in one read and returned the Host to
health `ready`. Tailscale remained running, Serve stayed configured and Funnel
remained disabled. No Worker was implicitly controlled by the Host service.

Both independent Worker LaunchAgents were reloaded and wrote fresh bounded
`task.pull` events. That readback exposed a separate source defect: an
Intake-blocked poll returned before `agent.heartbeat`, so Fleet health remained
stale despite live processes and current pulls. The correction and package
boundary are recorded in
`PRIVATE_HOST_WORKER_INTAKE_HEARTBEAT_ACCEPTANCE.md`; it requires a later
package and is not attributed to preview.37. No model task ran during the
maintenance window.

## Preview 38 Release And Real Upgrade

`v1.6.0-private-host-preview.38` was published from exact commit
`ee3d36c9ae4f123261893376fff012e36fc8a973`. Push CI `29677587281` and
pull-request CI `29677588369` passed Backend deterministic smokes and the
production UI build. Two candidate builds were byte-equal. Five Candidate,
Draft and public assets matched in separate directories, and isolated
Candidate, Draft and public-network consumers each completed no-repository
install/start/status/stop with exact provenance readback. The release used the
documented manual Draft-to-public path because the release workflow is not on
the repository default branch.

Only stopped AgentOps test directories under `/private/tmp` were removed before
maintenance; no historical backup or authority ledger was deleted. A fresh
preview.37 backup passed hash, schema, integrity and foreign-key verification.
Both Worker LaunchAgents and the Host LaunchAgent were explicitly unloaded and
independently observed absent. The public installer retained its automatic
backup gate, created another verified pre-update backup, preserved Host data,
recorded preview.37 as the previous version and bound `current` to preview.38
at the exact release commit.

The preview.38 Host LaunchAgent loaded successfully. An immediate status read
occurred before process readiness, but independent launchd and port checks
observed the active process and the next bounded status read returned health
`ready`; no crash or retry was hidden. Human login remained ready, Tailscale
Serve stayed configured and Funnel remained disabled.

Both independently managed Worker LaunchAgents then returned with active
processes. Against the same Intake-blocked queue that made preview.37 appear
stale, preview.38 reported Fleet status `ready`, two execution-capacity service
Workers and zero stale service Workers without invoking a model. A subsequent
Owner-authenticated workflow explicitly invoked both real adapters and
completed Hermes run `run_gw_c835b4dab9a9` plus OpenClaw run
`run_gw_be0e8275670f`, each with passing Evaluation and verified plan-evidence
closure. No raw model content, credential, private origin, Worker log, private
message, transcript or database content was retained.

That first Fleet result was not sustained acceptance. A later readback beyond
the 90-second freshness threshold reported both service Workers stale while
their launchd processes and loop iterations remained current. The Host-machine
Sessions have no parent enrollment token; same-state heartbeats update the
workspace-scoped observation on every request while historical Runtime/Audit
evidence remains sampled at 15 minutes. Preview.38 projected the historical
event but not the current observation. The source correction now binds that
workspace-scoped observation to the exact selected full-scope execution
Session, keeps per-Agent ledger sampling separate from per-Session liveness,
makes missing/mismatched selected-Session evidence fail closed, prevents a
heartbeat-only Session from replacing or refreshing execution liveness,
removes enrollment/unscoped Runtime Event freshness fallback, scopes Human and
Host Fleet reads, treats global Agent status as non-authoritative, selects the
newest fresh execution-ready Session before concurrent non-ready or stale
Sessions, preserves one deduplicated capacity while reporting mixed-Session
degradation, and normalizes mixed-offset Session timestamps to UTC. Human Fleet hygiene, task
release, Local/Demo Readiness, Commander read models, Review Queue, Customer
Delivery Board and Operator Action Plan/Command Center/Health are also bound
to the authenticated Human Session workspace. Core Agent/task/run/tool/
approval/memory/Evaluation/artifact reads, graphs/exports, Dashboard metrics,
knowledge results and bounded Operator handoff aggregates use the same authority;
cross-workspace details and writes fail closed, including Task-ID rebinding and
Agent Gateway Artifact/Approval ID collisions. The Agent registry remains
Host-global, with explicit membership for Human-created Agents and authoritative
owner/collaborator/run/Gateway projections. Audit tenancy authorizes exact
`(entity_type, entity_id)` pairs and remains fail-closed until a later hosted
schema adds first-class immutable workspace ownership to every Audit row.
Deterministic integration coverage
passes, but the correction requires a later exact package and real multi-cycle
readback. Preview.38 is not credited with sustained service-Worker freshness.

## Preview 39 Release And Real Upgrade

`v1.6.0-private-host-preview.39` was published from exact commit
`17801e3bbb20cdaec68e72f3e225ab5492d8f8e2`. Push CI `29927328480` and
pull-request CI `29927329540` passed. Two candidate directories were
byte-identical, all five Draft downloads matched, and a public-network consumer
completed no-repository install/start/version/status/stop with exact provenance
readback.

Before the real upgrade, the independent Hermes and OpenClaw Worker
LaunchAgents were explicitly unloaded. Stopping the Host process alone caused
its `KeepAlive=true` LaunchAgent to relaunch it, so the product Host
service-control boundary was then used to unload the authoritative service;
independent launchd and process checks confirmed all three services absent.
This distinction prevents a direct process stop from being misreported as a
maintenance boundary.

The public installer passed the real storage floor without a test override,
created and verified a pre-update backup, preserved the authority ledger and
Owner state, recorded preview.38 as the previous version and bound `current`
to preview.39. Installed provenance resolves to the exact release commit. The
real schema now contains the additive scoped Session heartbeat-observation
table packaged by preview.39.

The one-shot installer Host was stopped and the existing Host-only LaunchAgent
was loaded again. It converged in one read and returned health `ready`; Human
login remained ready, the managed UI resolved to preview.39, the existing
private Tailscale Serve target remained exclusive on port 8443 and Funnel
remained disabled. Both independent Worker LaunchAgents then loaded with active
processes. The initial bounded Fleet read reported two execution-capacity
Workers, zero stale Workers and a fresh execution-ready Session for each real
adapter. Four bounded samples spanning 105 seconds remained `ready` with two
execution-capacity Workers, zero stale Workers and zero unavailable Workers.
Both selected Sessions remained `fresh_ready` with execution scope present.
This closes the preview.38 post-90-second Fleet regression on the installed
package.

Two normal MIS tasks were then assigned to the persistent service identities;
no one-shot Worker command was used. Hermes completed
`run_gw_0dfd8981a340` and OpenClaw completed `run_gw_a46813718b06` after the
services independently pulled their tasks. Each run had one Tool Call, one
passing Evaluation, one Artifact, one Memory candidate, eight Runtime Events,
a quality-100 verified Agent Plan and a verified plan-evidence manifest. Both
had zero Approval and zero Prepared Action rows because the metadata-only
retention review prohibited deletion and external writes. This closes the
installed-package persistent Runtime gate without turning a service-control
receipt into model-execution evidence.

No raw model content, credential, private origin, Worker log, private message,
transcript or database row is retained in this receipt.
