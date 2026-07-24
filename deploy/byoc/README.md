# AgentOps MIS BYOC

This package runs the commercial control plane as Next.js/TypeScript on Node.js
with PostgreSQL 16. It does not start or proxy the Python API and does not use
SQLite as an authority store.

## Prepare

1. Copy `.env.example` to an untracked `.env`.
2. Create private secret files without printing their values:

   ```bash
   install -d -m 700 deploy/byoc/secrets
   umask 077
   openssl rand -hex 32 > deploy/byoc/secrets/postgres-password
   openssl rand -hex 32 > deploy/byoc/secrets/human-session-hmac-key
   chmod 600 deploy/byoc/secrets/postgres-password \
     deploy/byoc/secrets/human-session-hmac-key
   ```

3. For secret settings, keep only file paths in `.env`:

   ```dotenv
   AGENTOPS_POSTGRES_PASSWORD_FILE=./secrets/postgres-password
   AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE=./secrets/human-session-hmac-key
   ```

   Never commit `.env` or the `deploy/byoc/secrets/` directory.

4. Keep `AGENTOPS_BIND_ADDRESS=127.0.0.1` unless TLS is terminated by a trusted
   reverse proxy on the same private deployment boundary.
5. Set `AGENTOPS_ALLOWED_ORIGINS` to the exact HTTPS browser origin.

Do not put a raw PostgreSQL password, Human Session HMAC key, or credentialed
DSN in `.env`. Compose mounts the PostgreSQL secret at
`/run/secrets/postgres_password` for PostgreSQL, the one-shot migrator, and the
control plane. It mounts `/run/secrets/human_session_hmac_key` only for the
control plane; the migrator cannot read the Human Session HMAC key.

## Start

```bash
docker compose --env-file deploy/byoc/.env \
  -f deploy/byoc/compose.yaml \
  up --build --detach
```

The one-shot `migrate` service applies the checksum-pinned PostgreSQL manifest
before the control plane starts. The application then checks the same manifest
and its expected PostgreSQL catalog fingerprint, and fails closed if the schema
is missing or has drifted.

```bash
curl --fail http://127.0.0.1:3001/api/mis/health
```

Do not run `docker compose down --volumes` against a customer environment. The
named PostgreSQL volume is authority data; backup, upgrade, restore, and
rollback procedures must be completed before image promotion.

## Backup And Restore Drill

Create a permission-restricted backup bundle:

```bash
deploy/byoc/backup.sh backups/agentops-before-upgrade.bundle
```

The output is a new `0700` directory containing `database.dump`,
`SHA256SUMS`, and `COMMITTED`. The script reserves the output directory
atomically, builds the dump and checksum in a random private staging directory,
and publishes `COMMITTED` last. A bundle is valid only after that marker exists.
The script never reuses an existing path, so concurrent writers and symlinked
outputs fail closed without replacing another backup.

Restore into a new isolated database and verify it against the migration
manifest and catalog fingerprint embedded in the current image:

```bash
deploy/byoc/restore-drill.sh backups/agentops-before-upgrade.bundle
```

The drill rejects incomplete, symlinked, or checksum-mismatched bundles before
creating a database. It drops its isolated database after verification and
prints success only after the requested cleanup or retention disposition is
confirmed. Restore or verification failures always trigger cleanup even when
`AGENTOPS_RESTORE_KEEP=true`; a cleanup failure is itself a non-zero result.

Set a unique `AGENTOPS_RESTORE_DATABASE` and
`AGENTOPS_RESTORE_KEEP=true` only when an operator intends to retain a fully
verified restore for a separately reviewed promotion. The script refuses to
target the configured production database. `AGENTOPS_RESTORE_KEEP` accepts
only `true` or `false`.
