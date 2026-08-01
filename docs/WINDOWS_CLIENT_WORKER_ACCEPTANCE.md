# Windows Client and Worker Acceptance

## Scope

Windows is a supported AgentOps MIS client and Worker platform. A Windows
machine can use the operator CLI, connect to an existing AgentOps MIS Host,
run a governed Worker, and keep that Worker registered through Windows Task
Scheduler. The authoritative Host remains a macOS or Linux deployment in this
release.

## Acceptance layers

### 1. Cross-platform source and regression checks

Run from the repository root:

```text
python3.11 -m py_compile agentops_mis_cli/*.py agentops_mis_core/*.py scripts/*.py
python3.11 scripts/agentops_cli_install_smoke.py
python3.11 scripts/agentops_worker_package_smoke.py
python3.11 scripts/agentops_worker_local_config_smoke.py
python3.11 scripts/agentops_worker_service_install_smoke.py
python3.11 scripts/agentops_worker_service_check_smoke.py
python3.11 scripts/agentops_worker_service_control_smoke.py
python3.11 scripts/windows_installer_smoke.py
python3.11 scripts/windows_config_atomic_write_smoke.py
python3.11 scripts/windows_worker_service_security_smoke.py
git diff --check
```

These checks cover package installation, existing launchd/systemd behavior,
atomic credential/config publication, Windows Task XML validation, forged
action rejection, confirmation gates, and failure-safe service publication.
The Windows installer smoke explicitly labels non-Windows execution and is not
used as proof that Windows process execution passed.

### 2. Real runtime and MIS ledger closure

The same Worker/Gateway protocol completed locally with both authorized real
runtimes:

- Hermes: `run_gw_59f2c78ea541`
- OpenClaw: `run_gw_44916a0aae34`
- Codex official ChatGPT bundle: `run_gw_b3572673c358`

All three executions entered the MIS Run Ledger and produced runtime, evaluation,
audit, plan-evidence, and bounded output-summary records. Credentials, raw
prompts, raw responses, and full transcripts were omitted. These runs prove
the real adapter-to-ledger closure; they do not substitute for Windows OS
acceptance because the processes ran on macOS.

### 3. Windows CI contract

The `Windows CLI and Worker` GitHub Actions workflow is the required Windows
OS gate. On `windows-2022` it must:

- build and install the dependency-free wheel;
- execute `agentops` and `agentops-worker` from the isolated installation;
- run a complete one-shot Worker protocol against the bounded fake Gateway;
- compile and execute a native Windows `.exe` Codex fixture through stdin/JSONL and verify the
  Codex task-to-ledger evidence chain;
- enforce one shared process/stdin/stdout deadline and terminate inherited child
  processes even when the Codex launcher exits first;
- reject explicit `.cmd`/`.bat` service bindings without falling back to a
  different runtime discovered from `PATH` or `CODEX_BIN`;
- bind the exact Codex launcher into a credential-free Task Scheduler action
  and verify service definition plus runtime readiness;
- verify config and service XML DACLs;
- register, run, stop, and delete a real Task Scheduler task;
- prove Windows Host commands fail closed;
- run atomic-write and forged-service security smokes;
- run the complete user-local PowerShell install/reinstall/uninstall lifecycle.

Windows OS acceptance evidence:

- Commit: `27c6b7eb6c40250f1d7921eb98e5cb551a3f50d6`
- Workflow runs: [Windows CLI and Worker Acceptance #30683239307](https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/30683239307)
  and [independent duplicate #30683237648](https://github.com/geogejoy107-jpg/agentops-mis-mvp/actions/runs/30683237648)
- Result: passed on `windows-2022`, including the installed CLI/Worker flow,
  both `agentops` and `agentops-worker` service-management entry points, the
  native Codex process-tree/deadline and shim-rejection gates, real Task
  Scheduler lifecycle, atomic ACL failure checks, and the complete PowerShell
  install/reinstall/uninstall lifecycle.

## Physical Windows acceptance

Before claiming a specific customer Windows machine ready for live work, run
the commands in `docs/WINDOWS_CLIENT_WORKER_RUNBOOK.md` on that machine and
capture:

- installer success and `agentops status`;
- `agentops worker preflight` against the private Host using the saved config;
- one confirmed Hermes or OpenClaw task when that runtime is installed there;
- the resulting Task, Run, Evaluation, and Audit identifiers in MIS;
- `windows-task` service check after logoff/login or reboot.

Physical Windows plus the selected real Hermes/OpenClaw/Codex runtime remains a
customer-machine acceptance item until that evidence is captured. Windows
CLI/Worker support does not require installing every runtime; a Worker only
needs the adapter runtime it will execute on that machine.

The deterministic Windows Codex fixture is CI evidence for process transport,
bounded JSONL, Task Scheduler binding, and MIS ledger closure. Product evidence
for a specific Windows Codex installation still requires one physical Windows
run using the installed official Codex CLI. The cross-platform read-only path
does not authorize Windows Codex workspace-write.

## Known limitations

- Windows does not run the authoritative AgentOps MIS Host in this release.
- The user-local installer is not yet a signed MSI/MSIX package.
- There is no Start Menu desktop shell; Human Workspace remains browser based.
- Runtime installation and model licensing stay under the runtime owner's
  control; AgentOps MIS does not silently install Hermes, OpenClaw, or Codex.
- Codex workspace-write remains macOS-attested and fail-closed on Windows;
  Windows supports the governed read-only Worker and Codex-to-MIS plugin path.
