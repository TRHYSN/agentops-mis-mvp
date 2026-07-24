#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: deploy/byoc/restore-drill.sh BACKUP.bundle" >&2
  exit 2
fi

bundle=$1
if [ ! -d "$bundle" ] || [ -L "$bundle" ]; then
  printf '%s\n' "restore_bundle_invalid" >&2
  exit 1
fi

backup="$bundle/database.dump"
checksum_file="$bundle/SHA256SUMS"
commit_file="$bundle/COMMITTED"
for required_file in "$backup" "$checksum_file" "$commit_file"; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    printf '%s\n' "restore_bundle_incomplete" >&2
    exit 1
  fi
done
if [ ! -s "$backup" ] || [ ! -s "$checksum_file" ]; then
  printf '%s\n' "restore_bundle_incomplete" >&2
  exit 1
fi
if [ "$(cat "$commit_file")" != "agentops_byoc_backup_bundle_v2" ]; then
  printf '%s\n' "restore_bundle_uncommitted" >&2
  exit 1
fi

expected=$(
  awk '
    NF == 2 &&
    $2 == "database.dump" &&
    length($1) == 64 &&
    $1 ~ /^[0-9A-Fa-f]+$/ {
      value = tolower($1)
    }
    END {
      if (NR == 1 && value != "") {
        print value
      }
    }
  ' "$checksum_file"
)
if [ -z "$expected" ]; then
  printf '%s\n' "restore_checksum_invalid" >&2
  exit 1
fi

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk 'NR == 1 {print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk 'NR == 1 {print $1}'
  else
    return 127
  fi
}

if ! actual=$(sha256_file "$backup"); then
  printf '%s\n' "restore_checksum_unavailable" >&2
  exit 1
fi
if [ "$expected" != "$actual" ]; then
  printf '%s\n' "restore_checksum_mismatch" >&2
  exit 1
fi

compose_file=${AGENTOPS_BYOC_COMPOSE_FILE:-deploy/byoc/compose.yaml}
env_file=${AGENTOPS_BYOC_ENV_FILE:-deploy/byoc/.env}
restore_database=${AGENTOPS_RESTORE_DATABASE:-agentops_restore_$(date -u +%Y%m%d%H%M%S)_$$}
case "$restore_database" in
  *[!A-Za-z0-9_]*|"") printf '%s\n' "restore_database_invalid" >&2; exit 2 ;;
esac

keep_requested=${AGENTOPS_RESTORE_KEEP:-false}
case "$keep_requested" in
  true|false) ;;
  *)
    printf '%s\n' "restore_keep_invalid" >&2
    exit 2
    ;;
esac

production_database=$(
  docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    sh -ceu 'printf "%s" "$POSTGRES_DB"'
)
if [ -z "$production_database" ]; then
  printf '%s\n' "restore_production_database_unknown" >&2
  exit 1
fi
if [ "$restore_database" = "$production_database" ]; then
  printf '%s\n' "restore_database_must_not_be_production" >&2
  exit 1
fi

created=false
workflow_complete=false
cleanup_failure_reported=false

drop_restore_database() {
  docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    sh -ceu 'dropdb --username "$POSTGRES_USER" --if-exists "$1"' sh "$restore_database" \
    >/dev/null 2>&1
}

cleanup_on_exit() {
  status=$1
  trap - 0 1 2 15
  if [ "$created" = true ]; then
    if [ "$workflow_complete" = true ] && [ "$keep_requested" = true ]; then
      :
    elif drop_restore_database; then
      created=false
    else
      if [ "$cleanup_failure_reported" != true ]; then
        printf '%s\n' "restore_cleanup_failed" >&2
      fi
      exit 1
    fi
  fi
  exit "$status"
}
trap 'cleanup_on_exit "$?"' 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'createdb --username "$POSTGRES_USER" "$1"' sh "$restore_database"
created=true

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'pg_restore --username "$POSTGRES_USER" --dbname "$1" --no-owner --no-privileges --exit-on-error' sh "$restore_database" \
  < "$backup"

if ! docker compose --env-file "$env_file" -f "$compose_file" run --rm \
  migrate sh -ceu '
    base=${AGENTOPS_POSTGRES_DSN%/*}
    AGENTOPS_POSTGRES_DSN="${base}/$1"
    export AGENTOPS_POSTGRES_DSN
    npm run check:postgres-schema
  ' sh "$restore_database" >/dev/null 2>&1
then
  printf '%s\n' "restore_manifest_check_failed" >&2
  exit 1
fi

workflow_complete=true
kept=false
cleanup_confirmed=false
if [ "$keep_requested" = true ]; then
  kept=true
else
  if ! drop_restore_database; then
    cleanup_failure_reported=true
    printf '%s\n' "restore_cleanup_failed" >&2
    exit 1
  fi
  created=false
  cleanup_confirmed=true
fi

trap - 0 1 2 15
printf '{"ok":true,"contract":"agentops_byoc_restore_drill_v2","checksum_verified":true,"migration_manifest_verified":true,"production_overwritten":false,"restore_database_kept":%s,"cleanup_confirmed":%s,"restore_disposition_confirmed":true,"credentials_omitted":true}\n' "$kept" "$cleanup_confirmed"
