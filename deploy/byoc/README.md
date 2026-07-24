# AgentOps MIS BYOC

This package runs the commercial control plane as Next.js/TypeScript on Node.js
with PostgreSQL 16. It does not start or proxy the Python API and does not use
SQLite as an authority store.

## Prepare

1. Copy `.env.example` to an untracked `.env`.
2. Replace every placeholder with a generated secret.
3. Keep `AGENTOPS_BIND_ADDRESS=127.0.0.1` unless TLS is terminated by a trusted
   reverse proxy on the same private deployment boundary.
4. Set `AGENTOPS_ALLOWED_ORIGINS` to the exact HTTPS browser origin.

Use a URL-safe PostgreSQL password because Compose constructs the internal DSN
without writing another secret file.

## Start

```bash
docker compose --env-file deploy/byoc/.env \
  -f deploy/byoc/compose.yaml \
  up --build --detach
```

The one-shot `migrate` service applies the checksum-pinned PostgreSQL manifest
before the control plane starts. The application then checks the same manifest
again and fails closed if the schema is missing or has drifted.

```bash
curl --fail http://127.0.0.1:3001/api/mis/health
```

Do not run `docker compose down --volumes` against a customer environment. The
named PostgreSQL volume is authority data; backup, upgrade, restore, and
rollback procedures must be completed before image promotion.

## Backup And Restore Drill

Create a permission-restricted custom-format backup plus SHA-256 sidecar:

```bash
deploy/byoc/backup.sh backups/agentops-before-upgrade.dump
```

Restore into a new isolated database and verify it against the migration
manifest embedded in the current image:

```bash
deploy/byoc/restore-drill.sh backups/agentops-before-upgrade.dump
```

The drill drops its temporary database after verification. Set a unique
`AGENTOPS_RESTORE_DATABASE` and `AGENTOPS_RESTORE_KEEP=true` only when an
operator intends to retain the verified restore for a separately reviewed
promotion. The script refuses to target the configured production database.
