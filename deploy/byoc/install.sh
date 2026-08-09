#!/bin/sh
set -eu

umask 077

module_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(basename -- "$module_directory")" in
  byoc) release_root=$(CDPATH= cd -- "$module_directory/../.." && pwd) ;;
  *) release_root=$module_directory ;;
esac

compose_file="$release_root/deploy/byoc/compose.yaml"
environment_example="$release_root/deploy/byoc/.env.example"
environment_file=${AGENTOPS_BYOC_ENV_FILE:-"$release_root/deploy/byoc/.env"}
image_file="$release_root/release-image.env"
manifest_file="$release_root/release-manifest.json"
checksum_file="$release_root/SHA256SUMS"
commit_file="$release_root/COMMITTED"
lock_directory="$release_root/.install.lock"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--verify-only" ]; }; then
  printf '%s\n' "usage: install.sh [--verify-only]" >&2
  exit 2
fi
verify_only=false
if [ "$#" -eq 1 ]; then
  verify_only=true
fi

for path in "$compose_file" "$environment_example" "$image_file" \
  "$manifest_file" "$checksum_file" "$commit_file"; do
  [ -f "$path" ] && [ ! -L "$path" ] || fail "release_bundle_incomplete"
done

unexpected_entry=$(
  cd "$release_root"
  find . -mindepth 1 ! -type d ! -type f -print -quit
)
[ -z "$unexpected_entry" ] || fail "release_entry_invalid"

expected_files=$(
  {
    awk '{ print $2 }' "$checksum_file"
    printf '%s\n' SHA256SUMS COMMITTED
    if [ "$verify_only" = false ]; then
      for generated in \
        deploy/byoc/.env \
        deploy/byoc/secrets/postgres-migrator-password \
        deploy/byoc/secrets/postgres-runtime-password \
        deploy/byoc/secrets/postgres-entitlement-admin-password \
        deploy/byoc/secrets/entitlement-operator-password \
        deploy/byoc/secrets/human-session-hmac-key; do
        [ ! -f "$release_root/$generated" ] || printf '%s\n' "$generated"
      done
    fi
  } | LC_ALL=C sort
)
actual_files=$(
  cd "$release_root"
  find . -mindepth 1 -type f -print |
    sed 's#^\./##' |
    LC_ALL=C sort
)
[ "$actual_files" = "$expected_files" ] || fail "release_unmanifested_file"

[ "$(cat "$commit_file")" = "agentops_byoc_release_bundle_v1:committed" ] ||
  fail "release_bundle_uncommitted"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$release_root" && sha256sum --check --strict SHA256SUMS >/dev/null) ||
    fail "release_checksum_mismatch"
elif command -v shasum >/dev/null 2>&1; then
  (cd "$release_root" && shasum -a 256 --check SHA256SUMS >/dev/null) ||
    fail "release_checksum_mismatch"
else
  fail "release_checksum_unavailable"
fi

image_line=$(cat "$image_file")
case "$image_line" in
  AGENTOPS_IMAGE=*@sha256:????????????????????????????????????????????????????????????????) ;;
  *) fail "release_image_digest_required" ;;
esac
image=${image_line#AGENTOPS_IMAGE=}
printf '%s\n' "$image" |
  grep -Eq '^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$' ||
  fail "release_image_digest_required"

manifest_image=$(sed -n 's/^  "image": "\([^"]*\)",$/\1/p' "$manifest_file")
source_revision=$(sed -n 's/^  "source_revision": "\([0-9a-f]*\)",$/\1/p' "$manifest_file")
[ "$manifest_image" = "$image" ] || fail "release_image_manifest_mismatch"
printf '%s\n' "$source_revision" | grep -Eq '^[0-9a-f]{40}$' ||
  fail "release_source_revision_invalid"

grep -Eq '^[[:space:]]+build:' "$compose_file" &&
  fail "release_compose_build_forbidden"
grep -Eq '(Dockerfile|\.\./\.\.|/var/run/docker\.sock|python|sqlite)' "$compose_file" &&
  fail "release_compose_boundary_invalid"

if [ "$verify_only" = true ]; then
  printf '{"ok":true,"contract":"agentops_byoc_customer_install_v1","operation":"verify","source_revision":"%s","image_digest_verified":true,"repository_checkout_required":false}\n' \
    "$source_revision"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "docker_required"
docker compose version >/dev/null 2>&1 || fail "docker_compose_required"
command -v openssl >/dev/null 2>&1 || fail "openssl_required"
command -v curl >/dev/null 2>&1 || fail "curl_required"

mkdir "$lock_directory" 2>/dev/null || fail "install_in_progress"
stack_start_attempted=false
install_complete=false
health_receipt=
cleanup_install() {
  status=$?
  trap - 0 1 2 15
  if [ -n "$health_receipt" ]; then
    rm -f "$health_receipt"
  fi
  if [ "$stack_start_attempted" = true ] && [ "$install_complete" = false ]; then
    docker compose --env-file "$environment_file" -f "$compose_file" \
      stop --timeout 10 >/dev/null 2>&1 || true
  fi
  if ! rmdir "$lock_directory" 2>/dev/null && [ "$status" -eq 0 ]; then
    status=1
  fi
  exit "$status"
}
trap cleanup_install 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

secrets_directory="$release_root/deploy/byoc/secrets"
if [ ! -e "$secrets_directory" ]; then
  mkdir -m 700 "$secrets_directory"
fi
[ -d "$secrets_directory" ] && [ ! -L "$secrets_directory" ] ||
  fail "secrets_directory_invalid"
chmod 700 "$secrets_directory"

create_secret() {
  name=$1
  encoding=$2
  target="$secrets_directory/$name"
  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -f "$target" ] && [ ! -L "$target" ] || fail "secret_file_invalid"
  else
    case "$encoding" in
      base64) openssl rand -base64 32 > "$target" ;;
      hex) openssl rand -hex 32 > "$target" ;;
      *) fail "secret_encoding_invalid" ;;
    esac
  fi
  chmod 600 "$target"
}

create_secret postgres-migrator-password hex
create_secret postgres-runtime-password hex
create_secret postgres-entitlement-admin-password hex
create_secret entitlement-operator-password base64
create_secret human-session-hmac-key hex

if [ -e "$environment_file" ] || [ -L "$environment_file" ]; then
  [ -f "$environment_file" ] && [ ! -L "$environment_file" ] ||
    fail "environment_file_invalid"
  configured_image=$(awk -F= '/^AGENTOPS_IMAGE=/ { value=substr($0, index($0, "=") + 1) } END { print value }' "$environment_file")
  [ "$configured_image" = "$image" ] || fail "environment_image_mismatch"
else
  environment_parent=$(dirname -- "$environment_file")
  [ -d "$environment_parent" ] && [ ! -L "$environment_parent" ] ||
    fail "environment_parent_invalid"
  temporary_environment="$environment_file.pending.$$"
  awk '!/^AGENTOPS_IMAGE=/' "$environment_example" > "$temporary_environment"
  printf 'AGENTOPS_IMAGE=%s\n' "$image" >> "$temporary_environment"
  chmod 600 "$temporary_environment"
  mv "$temporary_environment" "$environment_file"
fi
chmod 600 "$environment_file"

docker compose --env-file "$environment_file" -f "$compose_file" config --quiet
running_services=$(
  docker compose --env-file "$environment_file" -f "$compose_file" \
    ps --services --status running
)
[ -z "$running_services" ] || fail "install_already_running"
docker pull "$image" >/dev/null
revision_label=$(docker image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
[ "$revision_label" = "$source_revision" ] || fail "release_image_revision_mismatch"

timeout_command=
if command -v timeout >/dev/null 2>&1; then
  timeout_command="timeout 10m"
fi
# shellcheck disable=SC2086
stack_start_attempted=true
$timeout_command docker compose --env-file "$environment_file" -f "$compose_file" \
  up --detach --no-build --pull never --wait --wait-timeout 300 control-plane

port=$(awk -F= '/^AGENTOPS_PORT=/ { value=$2 } END { print value }' "$environment_file")
case "$port" in
  "") port=3001 ;;
  *[!0-9]*) fail "environment_port_invalid" ;;
esac
health_receipt="$release_root/.install-health.$$"
if ! curl --fail --silent --show-error --retry 20 --retry-all-errors \
  --retry-delay 2 --output "$health_receipt" \
  "http://127.0.0.1:$port/api/mis/health"; then
  fail "customer_health_failed"
fi

if ! docker run --rm --interactive --entrypoint node "$image" -e '
  const fs = require("node:fs");
  const health = JSON.parse(fs.readFileSync(0, "utf8"));
  if (!(health.ok === true
    && health.status === "ready"
    && health.control_plane === "typescript_postgres"
    && health.schema_ready === true
    && health.schema_fingerprint_verified === true
    && health.python_proxy_performed === false
    && health.sqlite_used === false
    && health.credentials_omitted === true)) process.exit(1);
' < "$health_receipt"; then
  fail "customer_health_boundary_invalid"
fi
rm -f "$health_receipt"
health_receipt=
install_complete=true

printf '{"ok":true,"contract":"agentops_byoc_customer_install_v1","operation":"install","source_revision":"%s","image_digest_verified":true,"repository_checkout_required":false,"compose_build_performed":false,"control_plane":"typescript_postgres","schema_ready":true,"credentials_omitted":true}\n' \
  "$source_revision"
