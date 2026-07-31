# Windows CLI Installer Acceptance

## Product boundary

This slice installs `agentops` and `agentops-worker` for a non-admin Windows
user. AgentOps MIS authority remains on the configured Host. The installer
does not deploy a Windows Host, a Windows service, Hermes, OpenClaw, or any
model runtime.

## Acceptance command

```powershell
python .\scripts\windows_installer_smoke.py
```

## Required result on Windows

- A deterministic offline wheel is built twice with identical SHA-256.
- Installation succeeds in an isolated user-level directory.
- `agentops --help` and `agentops-worker --help` both exit successfully.
- Exact reinstall is idempotent.
- User PATH remains unchanged unless `-AddToPath` is explicitly supplied.
- Uninstall removes only managed CLI files.
- Uninstall preserves config/runtime data below
  `%LOCALAPPDATA%\AgentOps MIS` by default.
- Installer and output omit credentials, tokens, and database contents.

## Local cross-platform result

macOS/Linux can verify deterministic packaging, required PowerShell guards,
managed file ownership, secret omission, and the Windows test contract. It
must report `actual_windows_execution: false`; that result alone is not a
claim that Windows process execution passed.

## Known limitations

- No MSI/MSIX code signing or Start Menu application is included.
- The installer does not register a scheduled Worker task. The separately
  integrated Worker supports preview-first `--manager windows-task` lifecycle.
- This installer consumes a local source checkout; a signed release downloader
  remains future release engineering work.
- Real Hermes/OpenClaw execution on Windows requires separate runtime adapters
  and is outside this disjoint packaging slice.
