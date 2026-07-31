# Windows Client and Worker Runbook

AgentOps MIS supports Windows 10/11 as both a Human control client and an
Agent Gateway Worker machine. The authority Host can remain on macOS or Linux;
tasks, runs, approvals, evaluations, memories, and audit evidence remain in
that Host ledger.

## Supported topology

| Windows role | Supported path |
| --- | --- |
| Human Workspace | Open the Host HTTPS Workspace in Chrome or Edge |
| Operator CLI | Install `agentops`, connect to the Host, manage tasks and Host workers |
| Agent Worker | Install `agentops-worker`, enroll it, and run mock/Hermes/OpenClaw adapters |
| Persistent Worker | Install a user-level Task Scheduler definition with failure restart |
| Authority Host | Not supported on Windows in this release; use the macOS Private Host or Linux deployment |

Windows does not need a local database. The CLI and Worker use the scoped
Agent Gateway API and write bounded Run, Tool Call, Evaluation, Artifact,
Memory Candidate, and Audit evidence back to the Host.

## Install

Requirements:

- Windows 10 or Windows 11;
- Python 3.10 or newer (`py.exe` or `python.exe`);
- PowerShell 5.1 or newer;
- the downloaded AgentOps MIS release/source folder.

Open PowerShell in the downloaded folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\install.ps1 -AddToPath
```

The install is user-local under `%LOCALAPPDATA%\AgentOps MIS`; it does not need
Administrator access, use the network, start a Worker, or read credentials.
Open a new PowerShell window, then verify:

```powershell
agentops --help
agentops-worker --help
```

## Connect securely

Create a scoped Worker enrollment on the Host. Transfer the one-time token over
a private channel; never put it in Git, screenshots, service XML, or a command
argument. On Windows, store it with the no-echo prompt:

```powershell
agentops login `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  --agent-id "agt_windows_worker" `
  --prompt-api-key
```

The credential file is stored at
`%LOCALAPPDATA%\AgentOps MIS\config.json`. AgentOps applies and verifies a
protected Windows ACL limited to the current user plus Windows SYSTEM and
Administrators before atomically publishing the file. Ordinary users and
inherited access are rejected. If ACL setup fails, the previous config remains
unchanged.

Verify the connection without running a task:

```powershell
agentops status
agentops doctor
agentops worker preflight --adapter mock
```

## Use Windows as a control client

The Human Workspace remains the main operator surface. The Windows CLI can
also submit and inspect governed work, and can control workers that execute on
the Host. For example:

```powershell
agentops worker status
agentops task list
agentops run list
agentops approval list
```

Starting a real Hermes/OpenClaw Worker on the Host still requires the explicit
live-run confirmation used by the existing operator runbook.

## Run a Windows Worker

Run a read-only adapter check first:

```powershell
agentops worker preflight --adapter mock
```

Then process one governed task:

```powershell
agentops-worker --once `
  --adapter mock `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  --agent-id "agt_windows_worker" `
  --credential-source local_config `
  --use-session `
  --write-state
```

For Hermes or OpenClaw, install and verify that runtime on the Windows machine,
run its preflight, and add `--confirm-run`. The confirmation is intentionally
required for every persistent live-adapter definition.

## Install a persistent Worker

Preview the Task Scheduler definition:

```powershell
agentops `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  worker service-install `
  --manager windows-task `
  --adapter mock `
  --agent-id "agt_windows_worker" `
  --credential-source local_config
```

Write the credential-free XML after review:

```powershell
agentops `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  worker service-install `
  --manager windows-task `
  --adapter mock `
  --agent-id "agt_windows_worker" `
  --credential-source local_config `
  --confirm-install
```

Register and start it explicitly:

```powershell
agentops `
  --base-url "https://your-private-host.example" `
  --workspace-id "local-demo" `
  worker service-control `
  --manager windows-task `
  --action load `
  --adapter mock `
  --agent-id "agt_windows_worker" `
  --credential-source local_config `
  --confirm-control
```

The task runs at user logon, ignores duplicate starts, and requests restart
after failure. It references only the protected config path and mints a
short-lived Worker Session; the enrollment token is not copied into XML.

Inspect or remove it:

```powershell
agentops --base-url "https://your-private-host.example" --workspace-id "local-demo" worker service-check --manager windows-task --adapter mock --agent-id "agt_windows_worker"
agentops --base-url "https://your-private-host.example" --workspace-id "local-demo" worker service-control --manager windows-task --action unload --adapter mock --agent-id "agt_windows_worker" --confirm-control
```

## Uninstall

```powershell
.\packaging\windows\uninstall.ps1
```

Unload every registered `local.agentops.worker.*` task with `agentops worker
service-control` before uninstalling the CLI. The uninstaller fails closed
while a managed scheduled Worker remains.

The default uninstall removes only managed CLI files and preserves config,
Worker state, logs, and service templates. Purging data requires both
`-PurgeData` and `-ConfirmPurgeData`.

## Acceptance boundary

The Windows CI gate builds and installs the wheel on `windows-2022`, executes
the installed CLI, runs a complete offline mock Worker protocol through
pull/claim/run/tool/evaluation/audit writeback, writes isolated state, validates
Task Scheduler XML installation, and proves `agentops host` fails closed rather
than importing POSIX Host code.

This CI evidence is an offline fallback, not real-runtime evidence. A customer
readiness claim additionally requires one physical Windows acceptance against
the target Host and a real Hermes or OpenClaw runtime installed on that Windows
machine. The Codex local adapter and a native Windows Authority Host remain
outside this release boundary.
