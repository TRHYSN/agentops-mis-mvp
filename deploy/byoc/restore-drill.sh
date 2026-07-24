#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: deploy/byoc/restore-drill.sh BACKUP.dump" >&2
  exit 2
fi

backup=$1
checksum_file="${backup}.sha256"
if [ ! -s "$backup" ] || [ ! -s "$checksum_file" ]; then
  printf '%s\n' "restore_backup_or_checksum_missing" >&2
  exit 1
fi

expected=$(awk 'NR==1 {print $1}' "$checksum_file")
actual=$(shasum -a 256 "$backup" | awk '{print $1}')
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
  printf '%s\n' "restore_checksum_mismatch" >&2
  exit 1
fi

compose_file=${AGENTOPS_BYOC_COMPOSE_FILE:-deploy/byoc/compose.yaml}
env_file=${AGENTOPS_BYOC_ENV_FILE:-deploy/byoc/.env}
restore_database=${AGENTOPS_RESTORE_DATABASE:-agentops_restore_$(date -u +%Y%m%d%H%M%S)}
case "$restore_database" in
  *[!A-Za-z0-9_]*|"") printf '%s\n' "restore_database_invalid" >&2; exit 2 ;;
esac

production_database=$(
  docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    sh -ceu 'printf "%s" "$POSTGRES_DB"'
)
if [ "$restore_database" = "$production_database" ]; then
  printf '%s\n' "restore_database_must_not_be_production" >&2
  exit 1
fi

created=false
cleanup() {
  if [ "$created" = true ] && [ "${AGENTOPS_RESTORE_KEEP:-false}" != "true" ]; then
    docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
      sh -ceu 'dropdb --username "$POSTGRES_USER" --if-exists "$1"' sh "$restore_database" \
      >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT HUP INT TERM

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'createdb --username "$POSTGRES_USER" "$1"' sh "$restore_database"
created=true

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'pg_restore --username "$POSTGRES_USER" --dbname "$1" --no-owner --no-privileges --exit-on-error' sh "$restore_database" \
  < "$backup"

docker compose --env-file "$env_file" -f "$compose_file" run --rm \
  migrate sh -ceu '
    base=${AGENTOPS_POSTGRES_DSN%/*}
    AGENTOPS_POSTGRES_DSN="${base}/$1"
    export AGENTOPS_POSTGRES_DSN
    npm run check:postgres-schema
  ' sh "$restore_database" >/dev/null

kept=false
if [ "${AGENTOPS_RESTORE_KEEP:-false}" = "true" ]; then
  kept=true
fi
printf '{"ok":true,"contract":"agentops_byoc_restore_drill_v1","checksum_verified":true,"schema_verified":true,"production_overwritten":false,"restore_database_kept":%s,"credentials_omitted":true}\n' "$kept"
