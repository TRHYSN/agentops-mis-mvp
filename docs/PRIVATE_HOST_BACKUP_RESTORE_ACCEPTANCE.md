# Private Host Backup and Restore Acceptance

Status: passed on isolated local state
Scope: private Host SQLite authority ledger

## Product Commands

```text
agentops host backup
agentops host backup-verify [--backup PATH]
agentops host restore --backup PATH --confirm-restore
```

`backup` uses the SQLite online backup API, writes a SHA-256 manifest, and may
run while the Host is serving requests. `restore` requires the managed Host to
be stopped and explicit confirmation. An existing ledger receives a separate
pre-restore safety copy before replacement.

## Verification

```bash
python3 -m py_compile \
  agentops_mis_cli/host.py \
  scripts/agentops_local_backup.py \
  scripts/private_host_backup_restore_smoke.py
python3 scripts/private_host_backup_restore_smoke.py
git diff --check
```

Result:

- backup SQLite integrity: `ok`;
- backup manifest hash: verified;
- missing manifest and tampered SQLite file: rejected;
- backup and manifest permissions: private;
- unconfirmed restore: no write;
- restore while managed Host PID is alive: rejected;
- confirmed restore: ledger marker recovered;
- pre-restore safety copy: created;
- staged database integrity and foreign keys: verified before atomic replacement;
- restored human/Agent sessions and enrollment tokens: revoked by default;
- independent Host secrets file: unchanged by hash;
- hash-only Session/Token ledger state: explicitly included and treated as
  sensitive backup data;
- credential values and raw ledger rows: absent from command output;
- real user database: not used.

## Installed Preview 31 Pre-Update Backup Receipt

On 2026-07-15 the public
`v1.6.0-private-host-preview.31` installer created a verified backup before
switching the real Host from preview.30. The installed preview.31 CLI then
verified that exact backup at
`~/.agentops/host/backups/agentops-mis-20260715T054425269391Z.sqlite`.
Its SHA-256 is
`4a7e6df29d81f33ae0353be48ba4f3bae6dd565948093604052c75010560520e`.

Manifest, file hash, size, schema, SQLite integrity and foreign-key checks all
passed. The command was read-only, raw ledger rows were not printed, and token
values and the independent Host secret store were omitted. The installer
reported `previous_version=1.6.0-private-host-preview.30` and preserved user
data before changing the active binary.

This receipt proves the preview.31 upgrade rollback point, not a restore of the
user's live ledger and not a backup of the real Hermes/OpenClaw tasks completed
after the upgrade. Confirmed restore and access-revocation behavior remain
covered by isolated acceptance state.

## Superseded Preview 29 Backup Receipt

On 2026-07-15 the installed
`v1.6.0-private-host-preview.29` consumer CLI created and verified an online
backup while the real Host remained ready. The exact release commit was
`574c735541d95b70180254235a385ff764f8c45c`, and the bounded installed-backup
receipt hash was
`c8bb0335d0602bec5c3588cd9a7ee013fc71d697ee440c645310508cfc2a3031`.

Manifest, file hash, size, schema, SQLite integrity and foreign-key checks all
passed. The secret store was excluded, authentication state remained hash-only,
and raw ledger rows, token values and credentials were omitted. Verification
was read-only and the Host continued serving the same loopback and private
HTTPS Workspace.

This is installed preview.29 backup evidence, not a restore of the user's live
ledger. Confirmed restore, pre-restore safety-copy and access-revocation
behavior remain covered only by the isolated acceptance. The Release used the
manual prerelease path; the Private Host Preview Release workflow did not run.

## Superseded Preview 28 Backup Receipt

On 2026-07-15 the installed preview.28 consumer CLI created an online backup
while the real Host remained ready, then immediately verified the selected
backup through `agentops host backup-verify`. Manifest, file hash, size, schema,
SQLite integrity and foreign-key checks all passed. The receipt reported that
the secret store was excluded, authentication state was hash-only, and no raw
ledger rows or token values were printed. The verification was read-only and
the Host continued to serve the same loopback and private-HTTPS Workspace.

This is real current-package backup evidence, not a restore of the user's live
ledger. Confirmed restore, pre-restore safety copy and access revocation remain
covered by the isolated acceptance below so verification does not replace the
operator's current state.

## Boundaries

- Credentials, logs, PID state, raw prompts/responses, and Runtime files are not
  part of this backup.
- The command backs up the SQLite authority ledger. External mutable project
  directories or future Host-managed Markdown source directories require a
  separate directory backup contract before whole-host disaster recovery can
  be claimed.
- Upgrade migration and rollback across two product versions remain a Release
  Candidate acceptance item.
