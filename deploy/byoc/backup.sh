#!/bin/sh
set -eu

umask 077

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: deploy/byoc/backup.sh OUTPUT.dump" >&2
  exit 2
fi

output=$1
case "$output" in
  ""|"-"*) printf '%s\n' "backup_output_invalid" >&2; exit 2 ;;
esac
if [ -e "$output" ] || [ -e "${output}.sha256" ]; then
  printf '%s\n' "backup_output_exists" >&2
  exit 1
fi

compose_file=${AGENTOPS_BYOC_COMPOSE_FILE:-deploy/byoc/compose.yaml}
env_file=${AGENTOPS_BYOC_ENV_FILE:-deploy/byoc/.env}
temporary="${output}.partial.$$"
checksum_temporary="${output}.sha256.partial.$$"

cleanup() {
  rm -f "$temporary" "$checksum_temporary"
}
trap cleanup EXIT HUP INT TERM

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$temporary"

if [ ! -s "$temporary" ]; then
  printf '%s\n' "backup_empty" >&2
  exit 1
fi

hash=$(shasum -a 256 "$temporary" | awk '{print $1}')
printf '%s  %s\n' "$hash" "$(basename "$output")" > "$checksum_temporary"
mv "$temporary" "$output"
mv "$checksum_temporary" "${output}.sha256"
trap - EXIT HUP INT TERM

printf '{"ok":true,"contract":"agentops_byoc_backup_v1","backup_created":true,"checksum_created":true,"credentials_omitted":true}\n'
