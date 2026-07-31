# Hermes / Agnesfallback Connector Spec

## Goal

Show that AgentOps MIS can manage a real local runtime adapter shape while staying safe enough for classroom demo.

## Status API

`GET /api/integrations/hermes/status` returns:

- Hermes profile, gateway URL, API listening state and runtime mode.
- Agnesfallback CLI path, binary existence, gateway URL and API listening state.
- Whether real runs are enabled and whether confirmation is required.

## Probe API

- `POST /api/integrations/hermes/probe`: records Hermes health as available or unavailable.
- `GET /api/integrations/hermes/models`: tries local `/v1/models`; unavailable is recorded as a normal connector event.
- `POST /api/integrations/hermes/cli-probe`: dry-run plan unless explicitly confirmed.
- `POST /api/integrations/hermes/chat-completion-probe`: dry-run plan unless explicitly confirmed.
- `POST /api/integrations/hermes/run-task`: dry-run plan by default; with live mode and `confirm_run:true`, runs only a fixed Hermes default gateway probe and writes run/evaluation/audit evidence.

## Fixed Prompts

The fixed probes use constant, non-sensitive prompts:

- CLI: reply with `AGNESFALLBACK_OK`.
- API: reply with `HERMES_AGNES_API_OK`.
- Hermes default gateway: reply with `HERMES_DEFAULT_RUN_OK`.

The stored ledger keeps prompt hashes and redacted short output summaries only.

## v1.5 Governed Worker Path

The v1.5 Agent Gateway worker can execute a bounded MIS task through an
OpenAI-compatible Hermes profile after the human explicitly passes
`--confirm-run`. This path is separate from the fixed connector probes: it
creates and claims a normal MIS task, starts a run, calls the local runtime,
and records tool-call, evaluation, audit, artifact, memory-candidate, Agent
Plan, and plan-evidence rows. Full prompts, raw responses, credentials, and
transcripts remain outside the ledger.

### Persistent local Worker

For unattended operation, the Worker is installed as a user LaunchAgent with
the canonical label derived from its MIS agent identity:

```text
local.agentops.worker.agt_worker_daemon_hermes
```

The service must persist an explicit credential-free
`HERMES_GATEWAY_URL=http://127.0.0.1:8643` when Agnesfallback is the intended
profile. `agentops worker service-install --hermes-gateway-url ...` accepts only
an HTTP(S) base URL with a hostname and no credentials, path, query, fragment,
or whitespace. Invalid input fails before writing or loading the service and is
never echoed in the response. The service obtains its MIS credential from the
private local config; the plist contains no API key.

The long-running Worker pulls a bounded 50-candidate window so an intake-blocked
queue head cannot starve a later assigned task. The server remains authoritative
for workspace, Agent Plan, approval, and intake filtering; this window changes
candidate discovery only, not authorization.

Service-control receipts and control readbacks require an authenticated human
browser session. A machine/global API key may run and report Worker tasks but
cannot approve or attest the operating-system action that launched the Worker.

The latest local Agnesfallback acceptance is recorded in
[`HERMES_AGNESFALLBACK_MIS_DOGFOOD_2026_07_22.md`](HERMES_AGNESFALLBACK_MIS_DOGFOOD_2026_07_22.md).

## Non-Goals

- No default `--yolo`.
- No arbitrary prompt execution through the v1.2.1/v1.4 probe endpoints; only fixed probes are enabled there. The v1.5 worker accepts bounded MIS task summaries under its separate confirmation and ledger policy.
- No arbitrary cron ingestion or connector-triggered execution. The governed
  Agent Gateway Worker may poll continuously after an operator explicitly
  installs it with real-run confirmation.
- No remote deployment or public network binding.
