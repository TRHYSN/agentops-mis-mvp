# Local Service Worker Dogfood Acceptance

Status: preview.33 exact-package upgrade and fresh OpenClaw/Hermes service-Worker tasks accepted locally

## Scope

This acceptance uses the installed Private Host at literal loopback. It repairs
the local Hermes and OpenClaw LaunchAgent credential binding, proves both are
fresh Agent Gateway service Workers, and dispatches one low-risk review task to
each long-running Worker after an exact-package upgrade. It does not use a
repository server, a mock adapter or a direct probe.

No credential, private message, full transcript, raw prompt, raw response,
Worker log, database row dump or local service file is stored in this document.
The existing Tailscale Serve profile was not changed.

## Recovery

The installed services were present but repeatedly exited because their local
credential file was bound to an obsolete loopback Host origin. A dedicated
Host Worker config was created outside the repository with owner-only `0600`
permissions and an exact origin/workspace binding to the installed Host. The
existing general CLI config was not overwritten.

Both `local_stack` service templates were regenerated with:

- `credential_source=local_config`;
- no token in the plist;
- short-lived Agent Gateway sessions;
- `--confirm-run` for Hermes/OpenClaw;
- launchd `KeepAlive` restart policy;
- the installed `agentops-worker` entrypoint and managed package directory.

The services were explicitly restarted. Two obsolete `daemon` services that
targeted a dead development port were unloaded and their plist files retained
locally as disabled backups rather than deleted.

## Fresh Readback

The installed Host then reported:

| Check | Result |
|---|---:|
| active service Workers | 2 |
| execution-capacity Workers | 2 |
| active remote sessions | 2 |
| stale service Workers | 0 |
| unverified process claims | 0 |
| Hermes adapter | ready |
| OpenClaw adapter | ready |

The OpenClaw service Worker automatically claimed and completed:

| Evidence | Reference / count |
|---|---|
| task | `tsk_3547f49a694a` |
| run | `run_gw_89b425c911e4` |
| Agent Plan | `plan_095f331ab1a82576` |
| verified plan-evidence manifest | `pem_8d981e5a7d6c55cb` |
| tool calls | 1 |
| runtime events | 8 |
| evaluations | 1 |
| artifacts | 1 |
| memory candidates | 1 |
| audit events | 8 |

The Hermes service Worker then independently claimed and completed:

| Evidence | Reference / count |
|---|---|
| task | `tsk_d0db920fc1cd` |
| run | `run_gw_b472aac7db01` |
| Agent Plan | `plan_981f8adf805bec31` |
| verified plan-evidence manifest | `pem_88f1ee10747ef435` |
| tool calls | 1 |
| runtime events | 8 |
| evaluations | 1 |
| artifacts | 1 |
| memory candidates | 1 |
| audit events | 8 |

The task and run both reached `completed`; the Worker returned to `idle`. The
bounded output summary identified the correct Host-initiated Relay and Host-only
TLS-key-custody constraints. The task evidence graph is a read model over MIS
ledgers and explicitly omits raw prompt, raw response and token material.

## Preview.33 Exact-Package Closure

Commit `79eb036eddadd76dc5e7e22302e84b4684a25564` passed exact-head GitHub
Actions run `29611604886`, including the production UI build, bundle smoke,
offline safety suite and server-backed suite. A versioned
`1.6.0-private-host-preview.33` candidate from that exact commit passed a clean
HOME install and direct execution of both installed CLI entrypoints.

Before installation, the online Host backup passed manifest, hash, size,
schema, SQLite integrity and foreign-key verification without printing raw
rows or including the secret store. The Host-only LaunchAgent was explicitly
unloaded, the bundle atomically replaced preview.32 while preserving it as the
rollback version, and the Host service returned ready. The two separate Worker
LaunchAgents were then explicitly restarted from the new `current` package.
Fresh fleet readback again showed two active service Workers, two execution
capacity Workers, zero stale service Workers and zero unverified process
claims. Tailscale Serve remained enabled only as the existing advanced profile;
Funnel remained disabled.

The upgraded Hermes Worker automatically claimed and completed:

| Evidence | Reference / count |
|---|---|
| task | `tsk_preview33_hermes_claim_20260717T2041Z` |
| run | `run_gw_1c8cfbc1adbd` |
| Agent Plan | `plan_bf973d9ba83b6da3` |
| verified plan-evidence manifest | `pem_a3a2a513ae5b5309` |
| tool calls | 1 |
| runtime events | 8 |
| evaluations | 1 pass |
| artifacts | 1 |
| memory candidates | 1 |
| audit events | 8 |

The upgraded OpenClaw Worker independently claimed and completed the read-only
replacement task:

| Evidence | Reference / count |
|---|---|
| task | `tsk_preview33_openclaw_claim_readonly_20260717T2043Z` |
| run | `run_gw_f2478047e54d` |
| Agent Plan | `plan_e5812ec0f31af273` |
| verified plan-evidence manifest | `pem_5ae3efef460701c0` |
| tool calls | 1 |
| runtime events | 8 |
| evaluations | 1 pass |
| artifacts | 1 |
| memory candidates | 1 |
| audit events | 8 |

Both bounded summaries now state that the current Worker process is active and
the current Agent Gateway task claim succeeded. Both also state that this fact
does not prove OS service ownership and keep historical service-governance
readback separate. This closes the installed Runtime claim-truth gap without
weakening the existing loop-supervision or approval gates.

## Real Gap Found

The OpenClaw run reported knowledge retrieval quality at attention level with
`Recall@5=0.2` and `MRR=0.2`. Both Runtime summaries also described the service
loop as unverified even though the fresh Fleet readback showed two active
service Workers and zero stale service Workers. The execution and ledger loops
are accepted; the task-bound knowledge and operations-evidence context is not
fresh enough for a production grounding claim. Improve the Relay/Remote
Console corpus and inject current Fleet/service readback into governed task
context before repeating this acceptance.

The Worker adds a bounded current-process execution fact immediately
after a successful Agent Gateway task claim. It tells the Runtime that the
current process is active and the current claim succeeded, while explicitly
refusing to infer launchd/systemd ownership; historical service receipt/readback
remains visible as separate governance evidence. `worker_prompt_profile_smoke.py`
proves that stale historical service fields and a successful current claim can
coexist without exposing prompt, response, token, or service-template material.
The preview.33 upgrade and fresh real Hermes/OpenClaw reruns prove this behavior
in the installed product. Full task-bound Fleet/service readback is still not
injected, so the historical service-governance side can remain `unverified` even
while the current claim is correctly reported.

The first preview.33 OpenClaw task contained terms about deployed infrastructure
and was conservatively classified as a possible external action. It created
run `run_gw_2425533f193e` and stopped at an exact pending Approval Wall instead
of executing. The approval was not bypassed. The separate explicitly read-only
task above completed normally. This proves the safety gate but also exposes a
classification-precision issue for negated or analytical infrastructure text.

## Remaining Boundaries

- The physical MacBook browser Console still lacks a successful authenticated
  `/workspace` receipt for this run.
- The deployed browser-only Relay, Host connector daemon, DNS/certificate
  lifecycle and browser disconnect acceptance remain open.
- Host-local service repair is local machine state; no credential/config/plist
  is committed.
