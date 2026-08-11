# OpenCekura Windows Development Runbook

Status: v0 execution contract

Shell: PowerShell 7 or Windows PowerShell 5.1

Supported CI Python: 3.10 and 3.11
Supported UI runtime: Node.js 20 or newer compatible LTS

## Fresh clone and immutable preflight

```powershell
git clone https://github.com/geogejoy107-jpg/agentops-mis-mvp.git
Set-Location agentops-mis-mvp
git fetch --all --prune
git checkout main
git pull --ff-only
git branch --show-current
git rev-parse HEAD
git status --short
git checkout -b feat/open-cekura-windows-v0
```

Record repository, branch, exact HEAD, and working-tree state before changing files. Never develop directly on `main` and never use a Mac working directory as a Windows source of truth.

This implementation began from:

```text
Repository: geogejoy107-jpg/agentops-mis-mvp
Base branch: main
Starting HEAD: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
Working branch: feat/open-cekura-windows-v0
Starting tree: clean
```

## Toolchain

Verify:

```powershell
python --version
git --version
node --version
npm --version
```

OpenCekura Python dependencies are installed from the repository's dedicated requirements file; the base MIS package remains dependency-light. CI must not require API keys. The optional LLM judge reports `SKIPPED` when no supported key is present.

## Doctor

```powershell
python -m open_cekura.windows.doctor
python -m open_cekura.cli.main doctor
```

Critical checks cover Python, Node, npm, Git, repository root, current branch, exact commit, dirty state, write access, SQLite, localhost bind, and UI dependency state. External keys are printed only as `PRESENT` or `MISSING`; values are never read back to the terminal.

## Scenario and deterministic acceptance

```powershell
python -m open_cekura.cli.main scenario validate examples/open-cekura/scenarios/basic.yaml
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version baseline
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock --version candidate
python -m open_cekura.cli.main campaign compare --baseline <baseline-id> --candidate <candidate-id>
python -m open_cekura.cli.main gate evaluate --campaign <baseline-id>
python -m open_cekura.cli.main gate evaluate --campaign <candidate-id>
python -m open_cekura.cli.main evidence verify --campaign <baseline-id>
python -m open_cekura.cli.main evidence verify --campaign <candidate-id>
```

Expected semantic outcome:

```text
Baseline: BLOCKED with concrete scenario/evaluator blockers
Candidate: PASS
Evidence verification: PASS before tampering and FAIL after a covered artifact changes
```

IDs may be fixed in CI for locating artifacts, but outcomes must be computed from observed facts.

## Tests

```powershell
python -m pytest open_cekura/tests/unit -q
python -m pytest open_cekura/tests/integration -q
python -m pytest open_cekura/tests -q
```

The integration set includes Scenario → Run → Evaluation → Evidence, baseline/candidate comparison, failure → regression → replay, and API → SQLite. Run the smallest affected test after each fix, then the full OpenCekura suite.

## Backend and UI

Use an isolated database during local acceptance:

```powershell
$env:AGENTOPS_DB_PATH = Join-Path $env:TEMP 'open-cekura-acceptance.db'
python server.py
```

In a second PowerShell window:

```powershell
Set-Location ui/start-building-app
npm ci
npm run build
npm run dev
```

The Vite proxy targets `http://127.0.0.1:8787` by default. Browse to `/workspace/reliability`. Production/deep-link behavior is served by the existing MIS host; no second frontend server is introduced.

## Windows implementation rules

- Use `pathlib.Path`; resolve and validate paths before writes.
- Use `tempfile` for temporary files/directories.
- For durable files, write a temporary file in the destination directory, flush and `fsync` where supported, then `os.replace`.
- Use `subprocess.run([executable, arg1, ...])` with `shell=False`.
- Use Python `socket` for localhost port/bind checks.
- Preserve Unicode and spaces in checkout paths.
- Do not use `/tmp`, `~/.local`, Bash, `sh`, `grep`, `sed`, `awk`, `lsof`, `ps`, systemd, LaunchAgent, or POSIX file-mode assumptions in an OpenCekura core flow.
- Reject path escape, drive-relative ambiguity, UNC/device paths where not explicitly allowed, Windows reserved names, and unexpected reparse-point escape.
- Never print credentials, token values, raw hidden prompts, or unredacted private payloads.

## Existing POSIX Host boundary

The historical Private Host imports `fcntl` and uses POSIX process/service primitives. OpenCekura v0 does not rewrite that service manager. Generic `agentops` and `server.py` paths are made importable on Windows by delaying POSIX-only imports. Invoking a POSIX-only Host operation on Windows must return a clear, fail-closed unsupported-platform error.

## Troubleshooting

### `ModuleNotFoundError: fcntl`

Confirm the generic CLI/Gateway does not import `agentops_mis_cli.host` during startup. A regression here is a Windows compatibility failure; do not install a fake `fcntl` package.

### Node was installed but PowerShell cannot find it

Open a new terminal or refresh `PATH` from the user and machine environment, then rerun `node --version` and `npm --version`. Do not hardcode a user-specific WinGet directory in project code.

### Port already in use

Use the doctor socket check or select another explicit loopback port. Do not depend on `lsof`, `netstat` text parsing, or process-name guessing.

### Evidence verification fails

Treat it as a real integrity failure. Inspect missing/unexpected paths and expected/actual SHA-256 values. Regenerate evidence only from a replayed run; do not edit the manifest to match a manually changed artifact.

## Release handoff

Before PR creation, record exact branch, exact commit, clean/dirty state, acceptance commands and results, artifact campaign IDs, and exact GitHub Actions run. Push the feature branch and open a PR to `main`; do not merge it.
