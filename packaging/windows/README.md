# AgentOps MIS Windows CLI

This package installs the AgentOps MIS CLI and Worker entry points for a
Windows 10/11 user account. It does not install a Host, register a scheduled
Worker task, start a real runtime, or collect credentials.

## Requirements

- Windows 10 or Windows 11
- 64-bit Python 3.10 or newer, available as `py.exe` or `python.exe`
- PowerShell 5.1 or newer
- A checked-out AgentOps MIS source tree

No Administrator shell is required. The installer builds the repository's
dependency-free wheel locally and invokes pip with `--no-index --no-deps`.

## Install

From the repository root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\install.ps1
```

The default user-level paths are:

```text
%LOCALAPPDATA%\AgentOps MIS\cli
%LOCALAPPDATA%\AgentOps MIS\bin
%LOCALAPPDATA%\AgentOps MIS\config.json
%LOCALAPPDATA%\AgentOps MIS\workers
```

The installer owns only `cli` and `bin`. Config, Worker runtime state, service
templates, and future AgentOps data share the platform data root but are not
read or replaced by installation.

The installer does not change PATH by default. Verify the commands through the
installed bin directory, or explicitly approve a user PATH update:

```powershell
.\packaging\windows\install.ps1 -AddToPath
```

Open a new terminal after the opt-in PATH update, then verify both commands:

```powershell
agentops --help
agentops-worker --help
```

CI and isolated acceptance can override `-InstallRoot` and `-BinDir` without
touching the user PATH.

## Connect to a Host

The installer never asks for or stores a token. A Human can use the Host's
browser Workspace without installing the CLI. An Agent or operator can point
the CLI at a private Host for a single shell:

```powershell
$env:AGENTOPS_BASE_URL = "https://your-private-host.example"
agentops status
```

To persist a scoped enrollment without placing it in PowerShell history, use
the no-echo prompt:

```powershell
agentops login --base-url "https://your-private-host.example" --workspace-id "local-demo" --agent-id "agt_windows_worker" --prompt-api-key
```

Use an enrollment or short-lived session token only through the documented
Agent Gateway flow. Do not place tokens in this repository, installer command
history, launcher files, or screenshots.

## Optional Windows Worker task

The installer deliberately does not register or start Task Scheduler work.
The installed command supports preview-first task management through
`--manager windows-task`:

```powershell
agentops-worker service-template --manager windows-task --adapter mock --base-url "https://your-private-host.example" --workspace-id local-demo --agent-id agt_windows_local --credential-source local_config
agentops-worker service-install --manager windows-task --adapter mock --base-url "https://your-private-host.example" --workspace-id local-demo --agent-id agt_windows_local --credential-source local_config
```

`service-install` remains a dry run until its explicit confirmation flag is
supplied. Real Hermes/OpenClaw adapters retain their separate `--confirm-run`
gate. Review the generated task and credential source before any confirmed OS
mutation.

## Upgrade and rollback behavior

Each package version is installed below `cli\versions`. Reinstalling the same
wheel is idempotent. A wheel with the same version but a different SHA-256 is
rejected instead of replacing a known version. Launchers are switched only
after the new venv and both console entry points pass verification.

## Uninstall

First unload every registered `local.agentops.worker.*` task. Uninstall fails
closed if a managed scheduled Worker remains, preventing an orphan task from
pointing at a removed executable.

```powershell
.\packaging\windows\uninstall.ps1
```

The uninstaller requires the managed ownership marker. It removes the managed
`cli` and `bin` directories while preserving config, Worker runtime state, and
other data below `%LOCALAPPDATA%\AgentOps MIS` by default. Data removal is
deliberately separate:

```powershell
.\packaging\windows\uninstall.ps1 -PurgeData -ConfirmPurgeData
```

## Acceptance

Run from the repository root:

```powershell
python .\scripts\windows_installer_smoke.py
```

On Windows this performs a real isolated install, CLI launch, idempotent
reinstall, and uninstall. On other platforms it performs the deterministic
wheel and static safety checks only and labels that limitation in its JSON.
