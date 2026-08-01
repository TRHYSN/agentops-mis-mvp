# Private Host Bundle Acceptance

## Scope

This Phase 4 slice builds an unsigned macOS developer-preview bundle containing
the production UI and private Host runtime. It replaces “download the source
archive and configure a repository” with an integrity-checked installable
archive, but it is not yet a signed/notarized `.pkg` or `.dmg`.

## Build

```bash
cd ui/start-building-app
npm ci
npm run build
cd ../..
python3 scripts/build_private_host_bundle.py \
  --version 1.0.0-preview \
  --output-dir build/private-host
```

Outputs:

- `agentops-mis-private-host-<version>.tar.gz`;
- `agentops-mis-private-host-<version>.zip`;
- `agentops-mis-private-host-<version>.sha256.json`.
- `agentops-mis-private-host-<version>.provenance.json`.
- `install-agentops-mis-private-host.sh` release-consumer bootstrap.

Generated archives and UI `dist` remain untracked.

## Bundle Contract

- Source selection starts from Git-tracked runtime files. The builder refuses
  staged or unstaged changes to tracked files before it creates an output
  directory, so `manifest.json` cannot name `HEAD` while packaging different
  tracked source. Untracked local files are never selected.
- The production UI is copied from the explicit built `dist` directory.
- Runtime packages include CLI, core and runtime adapters plus the Python
  server, knowledge/static/config data and bounded scripts needed by Host mode.
- Each installed version includes the minimal SBOM, third-party notices,
  release provenance and Private Host operator runbook.
- `manifest.json` records product version, exact Git commit, build timestamp,
  platform, Python requirement, file sizes and SHA-256 for every payload and
  installer file. The timestamp is the immutable source commit timestamp, and
  tar/gzip/zip metadata is normalized so two builds from the same commit, UI
  and version are byte-for-byte identical.
- Archive-level SHA-256 values are emitted separately.
- Candidate provenance binds the version and exact clean Git commit to the
  normalized UI tree hash, bundle file count and archive/bootstrap hashes. The
  checksum manifest also covers that provenance document.
- The checksum manifest also covers the published release-consumer bootstrap.
- The release-consumer bootstrap pins one explicit GitHub tag, verifies the
  downloaded tar archive and provenance programmatically, rejects unsafe
  archive members or non-clean provenance, installs the bundle, and verifies
  both installed version and exact source commit. Optional
  `--init --start` remains loopback-only and does not create an Owner or enable
  live Runtimes/Tailscale.
- `.git`, `.env*`, DB/SQLite, token-named files, logs, caches, `node_modules`,
  `__pycache__`, `.agentops_runtime`, sample exports and local artifacts are
  excluded.
- `scripts/private_host_bundle_smoke.py` proves a dirty tracked clone is
  rejected before any release archive is written and repeats the build to
  compare every emitted asset digest.
- Installer rejects missing, modified, undeclared or traversal paths before
  copying payload files.
- Default install path is `~/.local/share/agentops-mis`; the operator CLI shim
  is `~/.local/bin/agentops` and the service/daemon CLI shim is
  `~/.local/bin/agentops-worker`.
- Versioned payloads live below `versions/<version>` and `current` points to the
  active version.
- A verified update stages the new version, preserves the prior version through
  `previous`, and refuses to run while the managed Host PID is alive.
- Install/update and uninstall share the Host lifecycle lock with start/stop;
  lock contention fails closed before backup, version staging, or removal.
  The lock file is opened without following a final symlink and must be a
  regular file.
- Uninstall removes product files and shim but preserves `~/.agentops/host`
  user data unless `AGENTOPS_PURGE_DATA=true` is explicitly set.
- Install and Host initialization create bounded, non-secret ownership markers.
  A non-empty install root without a valid marker is accepted only through the
  legacy migration shape (`current` inside `versions` plus an exact product
  release manifest); arbitrary existing directories are never claimed.
  Uninstall refuses while the managed Host PID is alive, its PID record is
  invalid/unverifiable, or the shared lifecycle lock is held. It requires valid
  product/data ownership markers and rejects dangerous, overlapping, external,
  or symlinked removal roots.
- Both existing CLI shims must match the installer-generated content exactly
  before update or uninstall; modified and symlinked shims fail closed.
- Updates replace both verified CLI shims atomically through same-directory
  temporary files. They never rewrite a live shim inode in place.
- Installer does not use the network or install Hermes, OpenClaw, Tailscale,
  Python or Node.

## Verification

```bash
python3 -m py_compile \
  scripts/build_private_host_bundle.py \
  scripts/private_host_bundle_smoke.py
bash -n packaging/macos/install.sh packaging/macos/uninstall.sh
python3 scripts/private_host_bundle_smoke.py
python3 scripts/private_host_release_consumer_smoke.py
git diff --check
```

The smoke uses temporary build/output/HOME/install/data directories and proves:

- tar.gz and zip forbidden-member/path-traversal scan;
- archive and per-file manifest checksums;
- modified payload rejection before installation;
- offline install and both CLI shim creations;
- direct execution of the installed `agentops-worker` command and Worker
  service-template generation without a repository or module fallback;
- installed `agentops host --help`;
- installed `agentops host init` and `agentops host doctor`;
- installed human-auth capable live Runtime ledger readback client;
- two versioned bundles upgrade and roll back through `current`/`previous`;
- upgrade changes both CLI shim inodes and directly executes the upgraded
  `agentops` and `agentops-worker` commands;
- rollback requires confirmation and creates a verified ledger backup;
- upgrade creates a verified ledger backup before switching binaries;
- Host ledger and user data survive the binary switch;
- production UI presence in the installed current version;
- installed SBOM, third-party notices, provenance and operator runbook;
- uninstall removes product files;
- uninstall preserves a pre-existing user-data sentinel.
- running-Host and invalid-PID uninstall attempts fail closed without removing
  the installed version or CLI shim;
- invalid-PID update, unrelated-root install, and modified operator/Worker shim uninstall
  attempts fail closed while preserving their sentinels;
- lifecycle-lock contention, missing ownership marker, and HOME-root purge
  attempts fail closed without removing product or user data;
- symlinked lifecycle-lock substitution is rejected by both installer and
  uninstaller without modifying the symlink target;
- a clean isolated HOME consumes release-shaped assets without a repository,
  verifies checksum/provenance, initializes and starts the Host, exposes the
  Owner bootstrap action, and stops cleanly.
- bundle-smoke Host instances register before each start and are stopped on
  normal exit, assertion failure, `SIGTERM`, or `SIGINT`; an intentional
  `SIGTERM` acceptance confirmed the new fixture Host exited while unrelated
  processes remained untouched.

No generated archive, DB, credential, log, cache, dependency directory, real
Runtime call or network publication is committed or retained by the smoke.

## 2026-07-15 Live Upgrade Regression

The real preview.29-to-preview.30 upgrade exposed a macOS lifecycle defect that
clean-HOME installation did not reproduce. The installer verified both existing
CLI shims but then rewrote them in place. With independently managed Worker
LaunchAgents already using those executable paths, direct shim execution was
terminated with exit 137 and both service Workers recorded a `-9` exit. The
versioned payload, Host data and pre-update backup remained valid; invoking the
same installed Python module directly also remained healthy.

The Host was recovered through the installed module entry point. Replacing the
two shims with byte-identical files on new inodes restored direct CLI execution,
after which the Hermes and OpenClaw LaunchAgents restarted successfully with
new PIDs. No Runtime task was executed during recovery.

The installer now stages each shim in the destination directory, flushes it,
sets mode `0755`, and atomically replaces the verified old path. The bundle
smoke requires both shim inodes to change across an upgrade and directly runs
the upgraded commands. preview.30 remains immutable with this in-place-upgrade
defect and must not be used for another in-place upgrade.

`v1.6.0-private-host-preview.31` is the corrective prerelease at exact commit
`fed1b2410d6725a217c9727dba570db62cc46963`. Push CI `29391744378` and
pull-request CI `29391746311` passed at that commit. Candidate, Draft and
public Release assets were byte-equal; clean-HOME download, install, both CLI
entry points, Host init/start/status/stop and a preview.30-to-preview.31
isolated upgrade all passed. The Private Host Preview Release workflow was not
available on the default branch, so the prerelease used the documented manual
Draft-to-public path.

The real installed Host then unloaded only its Host LaunchAgent, installed the
public preview.31 tar archive and loaded the service again. The installer
created a verified pre-update backup, preserved user data and reported
`previous_version=1.6.0-private-host-preview.30`. Both CLI shim inodes changed,
both commands executed immediately, and the independently managed Hermes and
OpenClaw Worker processes survived the install without exit 137 or `-9`.
After explicit Worker restarts, both new processes reported fresh heartbeats
from the preview.31 `current` directory. No credential, database, raw prompt,
raw response, Worker log or generated release asset is tracked in Git.

## Preview.31 Installed Runtime Dogfood

Two new tasks were submitted through the installed preview.31 CLI and consumed
by the independently managed service Workers rather than a one-shot helper or
mock adapter:

- OpenClaw task `tsk_preview31_openclaw_readonly_20260715T055005Z` completed as
  run `run_gw_612fb884979c` in 13,173 ms. It produced one tool call, evaluation,
  artifact and reviewable memory candidate. Plan-evidence manifest
  `pem_d873db245a315031` is `verified`.
- Hermes task `tsk_preview31_hermes_readonly_20260715T055005Z` completed as run
  `run_gw_9767258929f0` in 28,261 ms with the same bounded evidence classes.
  Plan-evidence manifest `pem_691181d2ac0d35ea` is `verified`.

Both rule evaluations passed, both runs ended without an error type, and both
Workers remained loaded with fresh heartbeats. Their bounded output summaries
also correctly surfaced stale service-control readback and weak task-specific
knowledge retrieval as operator attention instead of claiming that every
release gate had passed. A Keychain-backed Owner Human Session later rejected
both low-value generic memory candidates. The ledger records
`mem_gw_643cafa6c4cdd588` and `mem_gw_ffb731901b9426c1` as `rejected`, with
bounded audit rows `aud_aaef199cbeb8` and `aud_4d06bc9c043d`. The Session then
logged out; no password, Cookie, CSRF value or raw memory content was emitted.

An earlier pair of task descriptions included the negated phrase “do not
perform external writes.” preview.31 conservatively matched the keyword and
created prepared-action approvals before invoking either Runtime. Those rows
were rejected by the same Owner Session, moving the two non-executed tasks to
`blocked`. Approval audit rows `aud_3ff892cc911d` and `aud_df91003af40f`
preserve the decisions without raw task or Runtime content. Post-release source now evaluates
each keyword occurrence with bounded English/Chinese clause negation, while a
later positive action in the same task still triggers the Approval Wall. The
full external-write preflight smoke passes, but this follow-up source fix is
not retroactively attributed to the immutable preview.31 package.

## Known Limitations

- Apple Developer ID signing, notarization, Gatekeeper validation and `.pkg` or
  `.dmg` UX are not complete.
- Target Mac must already provide Python 3.10 or newer.
- Hermes/OpenClaw remain explicit Host-side prerequisites for real execution.
  Tailscale is only an advanced private-network profile and is not required by
  the ordinary browser-only Relay Console.
- `.github/workflows/private-host-release.yml` manually publishes an existing
  exact-commit tag as a prerelease after rebuilding and re-running bundle and
  consumer smokes. It then downloads the published bootstrap and performs a
  clean-HOME install/init/start/status/stop gate against the real Release.
- Binary upgrade/rollback is covered for two bundles using the same schema;
  incompatible future schema changes still require a dedicated downgrade path.
- A real second Mac download/install/private-console acceptance remains pending.

## Next Slice

Complete the same download/install receipt on another physical Mac, then add
Apple signing/notarization only after the unsigned preview workflow is stable.
