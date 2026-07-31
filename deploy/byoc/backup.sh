#!/bin/sh
set -eu

umask 077

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: deploy/byoc/backup.sh OUTPUT.bundle" >&2
  exit 2
fi

requested_output=$1
case "$requested_output" in
  "")
    printf '%s\n' "backup_output_invalid" >&2
    exit 2
    ;;
esac

case "$requested_output" in
  */*)
    output_parent=${requested_output%/*}
    output_name=${requested_output##*/}
    if [ -z "$output_parent" ]; then
      output_parent=/
    fi
    ;;
  *)
    output_parent=.
    output_name=$requested_output
    ;;
esac

case "$output_name" in
  ""|"."|".."|"-"*)
    printf '%s\n' "backup_output_invalid" >&2
    exit 2
    ;;
esac
if [ ! -d "$output_parent" ] || [ -L "$output_parent" ]; then
  printf '%s\n' "backup_output_parent_invalid" >&2
  exit 1
fi

output="$output_parent/$output_name"
if [ -e "$output" ] || [ -L "$output" ]; then
  printf '%s\n' "backup_output_exists" >&2
  exit 1
fi

compose_file=${AGENTOPS_BYOC_COMPOSE_FILE:-deploy/byoc/compose.yaml}
env_file=${AGENTOPS_BYOC_ENV_FILE:-deploy/byoc/.env}
bundle_owned=false
published=false
staging=

cleanup_on_exit() {
  status=$?
  trap - 0 1 2 15
  if [ "$published" != true ] && [ "$bundle_owned" = true ]; then
    rm -rf "$output"
  elif [ -n "$staging" ]; then
    rm -rf "$staging"
  fi
  exit "$status"
}
trap cleanup_on_exit 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

if ! mkdir -m 700 "$output" 2>/dev/null; then
  printf '%s\n' "backup_output_exists" >&2
  exit 1
fi
bundle_owned=true

if ! staging=$(mktemp -d "$output/.staging.XXXXXXXX"); then
  printf '%s\n' "backup_staging_failed" >&2
  exit 1
fi

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$staging/database.dump"

if [ ! -s "$staging/database.dump" ]; then
  printf '%s\n' "backup_empty" >&2
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

if ! hash=$(sha256_file "$staging/database.dump"); then
  printf '%s\n' "backup_checksum_unavailable" >&2
  exit 1
fi
if ! printf '%s\n' "$hash" |
  awk 'length($0) == 64 && $0 ~ /^[0-9A-Fa-f]+$/ {valid = 1} END {exit valid ? 0 : 1}'
then
  printf '%s\n' "backup_checksum_invalid" >&2
  exit 1
fi

printf '%s  database.dump\n' "$hash" > "$staging/SHA256SUMS"
printf '%s\n' "agentops_byoc_backup_bundle_v2" > "$staging/COMMITTED.pending"

fsync_path() {
  node -e '
    const fs = require("node:fs");
    const descriptor = fs.openSync(process.argv[1], "r");
    try {
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
  ' "$1"
}

if ! fsync_path "$staging/database.dump" ||
  ! fsync_path "$staging/SHA256SUMS" ||
  ! fsync_path "$staging/COMMITTED.pending"
then
  printf '%s\n' "backup_fsync_failed" >&2
  exit 1
fi

mv "$staging/database.dump" "$output/database.dump"
mv "$staging/SHA256SUMS" "$output/SHA256SUMS"
if ! fsync_path "$output"; then
  printf '%s\n' "backup_fsync_failed" >&2
  exit 1
fi
mv "$staging/COMMITTED.pending" "$output/COMMITTED"
rmdir "$staging"
staging=
if ! fsync_path "$output" || ! fsync_path "$output_parent"; then
  printf '%s\n' "backup_fsync_failed" >&2
  exit 1
fi
published=true
trap - 0 1 2 15

printf '{"ok":true,"contract":"agentops_byoc_backup_v2","bundle_committed":true,"backup_created":true,"checksum_created":true,"credentials_omitted":true}\n'
