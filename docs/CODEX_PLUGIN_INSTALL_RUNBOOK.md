# AgentOps MIS Codex Plugin Runbook

## Purpose

The `agentops-mis` plugin is the Codex-side half of the bidirectional product
bridge:

- AgentOps MIS can dispatch governed tasks to a Codex Worker.
- Codex can use the installed `$agentops-mis` Skill to pull and claim tasks,
  request bounded context, record execution evidence, and return the work to
  the MIS review flow.

The plugin uses the existing AgentOps CLI/API contract. Native AgentOps MIS MCP
tools are not part of this slice.

## Install From This Checkout

Use the Codex CLI bundled with the desktop application:

```bash
CODEX_BIN="/Applications/ChatGPT.app/Contents/Resources/codex"
REPO="/Users/wuji/Documents/MIS/code/agentops-mis-mvp"

"$CODEX_BIN" plugin marketplace add "$REPO"
"$CODEX_BIN" plugin add agentops-mis@agentops-mis
"$CODEX_BIN" plugin list
```

Start a new Codex task after installation so the new Skill is loaded. Invoke it
with:

```text
Use $agentops-mis to check my MIS connection and continue one governed task.
```

## Connect To AgentOps MIS

For local loopback, configure only non-secret defaults:

```bash
agentops login \
  --base-url http://127.0.0.1:8787 \
  --workspace-id local-demo \
  --agent-id <codex_agent_id>

agentops status
```

For an authenticated or remote deployment, provide the API key through the
process environment or a host secret manager. Do not put it in a prompt, Skill,
plugin file, shell history, screenshot, or command argument.

## Windows client path

Install the AgentOps Windows CLI first, then install the same repository plugin
with the Codex CLI:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\install.ps1 -AddToPath

$Repo = (Resolve-Path ".").Path
codex plugin marketplace add "$Repo"
codex plugin add agentops-mis@agentops-mis
codex plugin list
```

Open a new PowerShell and Codex task after installation. The Skill uses
`agentops` from `PATH`; the repository's shell fallback helper is POSIX-only and
is not the Windows execution path.

Use the no-echo login prompt before asking Codex to receive work:

```powershell
agentops login `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  --agent-id "agt_windows_codex_client" `
  --prompt-api-key

agentops status
agentops worker preflight --adapter codex --codex-bin (Get-Command codex.exe).Source
```

The independent Codex Worker requires the native `codex.exe`; it rejects
`.cmd`/`.bat` shims so command-shell quoting cannot change the bounded runtime
arguments. The interactive plugin commands above may still use the ordinary
`codex` command exposed by the installed Codex distribution.

## Governed Codex-Side Loop

The Skill guides Codex through the primitive CLI workflow:

1. pull and claim one task;
2. fetch the bounded Knowledge Evidence Packet and loop launch packet;
3. create and verify an Agent Plan;
4. start and heartbeat the Run;
5. record bounded Runtime Events, Tool Calls, Artifacts, Evaluations, and Audit;
6. request human approval for high-risk actions;
7. never self-approve or write directly to SQLite.

This client loop must not invoke `agentops workflow run-task --adapter codex`.
That command starts a separate Codex Worker and would create nested execution.

## Safe Verification

```bash
python3 scripts/codex_plugin_contract_smoke.py
plugins/agentops-mis/scripts/agentops-mis status
```

The connector UI is:

```text
http://127.0.0.1:19001/admin/connectors/codex
```

It shows the MIS-to-Codex worker path and the Codex-to-MIS plugin path
separately. The page is observational: it does not reveal credentials, launch
the runtime, or approve actions.

## Uninstall

```bash
"$CODEX_BIN" plugin remove agentops-mis
```

Removing the plugin does not delete historical MIS task, Run, evaluation,
artifact, or audit evidence.
