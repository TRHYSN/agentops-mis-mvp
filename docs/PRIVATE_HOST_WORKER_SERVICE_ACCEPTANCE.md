# Private Host Worker Service Credential Acceptance

## Scope

This slice makes independently service-managed Workers usable on the same Mac
as an installed AgentOps MIS Private Host without putting the Host machine
credential in a launchd plist, process argument or command response.

The ownership model remains explicit:

- the Private Host LaunchAgent starts only `host start --foreground --no-workers`;
- Hermes and OpenClaw use separate Worker LaunchAgents;
- each live Worker definition requires the operator to include `--confirm-run`;
- install and launchctl mutation remain separate confirmation gates.

No browser UI, backend route, database schema or Runtime adapter behavior is
changed by this slice.

## Credential Contract

`agentops host configure-cli --confirm` writes the local Host URL, workspace
and machine credential to `~/.agentops/config.json` with mode `0600` and an
exact `api_key_base_url` binding. A same-Mac Worker service can reference that
file with:

```bash
agentops worker service-install \
  --manager launchd \
  --adapter hermes \
  --agent-id agt_hermes_local_service \
  --credential-source local_config \
  --confirm-run
```

The generated service definition contains the config path and credential
source marker only. Its default working directory follows the managed
`current` package link so a verified upgrade does not pin a removed release or
the shell directory from which install was invoked. On startup, the Worker:

1. rejects a direct API key combined with `local_config`;
2. opens the config with no-follow semantics;
3. requires a regular file owned by the current user with no group/other mode;
4. validates exact Host origin and workspace binding;
5. rejects missing or empty credentials;
6. rejects credentialed transport outside HTTPS or literal loopback HTTP;
7. keeps the parent key in memory and mints a short-lived Worker Session;
8. requests only the fixed Worker scope set and rejects broader overrides such
   as `tasks:create` or `approvals:request`.

Failures return bounded reason codes and omit config contents and credential
values. The service checker recognizes this contract only when the config
reference and `--use-session` exist and `AGENTOPS_API_KEY` is absent.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/worker.py \
  agentops_mis_cli/agentops.py \
  scripts/agentops_worker_local_config_smoke.py
python3 scripts/agentops_worker_local_config_smoke.py
python3 scripts/agentops_worker_service_check_smoke.py
python3 scripts/agentops_worker_service_install_smoke.py
python3 scripts/agentops_worker_service_control_smoke.py
git diff --check
```

The isolated local-config smoke proves:

- a credential-free launchd template with `--use-session`;
- confirmed service installation at mode `0600`;
- service-check recognition of the local config reference;
- a real HTTP Session mint and one Worker polling iteration;
- omission of non-Worker scopes from that short-lived Session;
- fail-closed behavior for unsafe permissions, origin mismatch and symlinks;
- omission of both parent and Session credentials from output.

Existing service check/install/control smokes prove the original direct/local
development behavior and remote placeholder flow did not regress. Hermes and
OpenClaw definitions without `--confirm-run` remain unloadable.
The Private Host bundle also includes this acceptance file and
`REMOTE_WORKER_OPERATIONS_RUNBOOK.md`; its installed-bundle smoke confirms the
same `local_config`, mode `0600`, no-API-key and managed-`current` contract
without loading launchd or running an adapter. The bundle installs a first-class
`agentops-worker` shim alongside `agentops`; clean-HOME acceptance invokes that
consumer command directly, so a repository/module fallback cannot mask a
missing Worker executable.

## Real Private Host Readback

On 2026-07-14, the current source used the installed Private Host's existing
mode `0600` local CLI config without reading or printing its contents. The
following bounded checks passed against `127.0.0.1:18878`:

- Hermes and OpenClaw `service-install` previews both returned `dry_run:true`,
  `wrote:false`, `live_execution_performed:false`, and
  `credential_source:local_config`;
- a mock Worker minted a 60-second Session through the real Agent Gateway,
  registered, heartbeated and completed one empty-status poll with
  `processed:0`;
- the Session contained 17 Worker scopes and omitted `tasks:create` and
  `approvals:request`;
- output reported `token_omitted:true`; no Runtime adapter or customer task ran;
- no service file was written or launchctl state changed.

This is real Host credential/session evidence, not real Hermes/OpenClaw task
completion and not logout/reboot persistence proof.

## Known Limits And Next Gate

- CI uses a bounded fake Gateway and mock adapter. It proves the credential and
  service contract, not a live model completion.
- The Worker reads the local CLI config at process start. Key rotation requires
  a Worker restart so it can mint from the new parent credential.
- Physical logout/reboot restoration still requires a macOS receipt.
- The current installed Host must first be moved from Host-owned child Workers
  to `--no-workers`; duplicate same-adapter ownership must continue to fail.

The next controlled acceptance is to install distinct Hermes and OpenClaw
LaunchAgents on the real Private Host, stop the current Host-owned child
Workers, load the host-only and Worker services with explicit confirmation,
then prove status/restart recovery before running any customer task.
