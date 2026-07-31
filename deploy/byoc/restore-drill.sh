#!/bin/sh
set -eu
umask 077
module_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
destructive_database_helper="$module_directory/postgres-destructive-database.sh"

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

staging=
validation_log=
remove_validation_log() {
  if [ -n "$validation_log" ]; then
    rm -f "$validation_log" || return 1
    validation_log=
  fi
}

remove_restore_staging() {
  if [ -n "$staging" ]; then
    if [ -d "$staging" ] && [ ! -L "$staging" ]; then
      chmod 700 "$staging" || return 1
    fi
    rm -rf "$staging"
    staging=
  fi
}

cleanup_staging_on_exit() {
  status=$?
  trap - 0 1 2 15
  remove_validation_log || status=1
  remove_restore_staging || status=1
  exit "$status"
}
trap cleanup_staging_on_exit 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

if ! staging=$(mktemp -d "${TMPDIR:-/tmp}/agentops-byoc-restore.XXXXXXXX"); then
  printf '%s\n' "restore_staging_failed" >&2
  exit 1
fi
chmod 700 "$staging"
if ! cp -P "$backup" "$staging/database.dump" ||
  ! cp -P "$checksum_file" "$staging/SHA256SUMS" ||
  ! cp -P "$commit_file" "$staging/COMMITTED"
then
  printf '%s\n' "restore_staging_failed" >&2
  exit 1
fi

bundle=$staging
backup="$bundle/database.dump"
checksum_file="$bundle/SHA256SUMS"
commit_file="$bundle/COMMITTED"
for staged_file in "$backup" "$checksum_file" "$commit_file"; do
  if [ ! -f "$staged_file" ] || [ -L "$staged_file" ]; then
    printf '%s\n' "restore_staging_invalid" >&2
    exit 1
  fi
done
chmod 400 "$backup" "$checksum_file" "$commit_file"
chmod 500 "$staging"
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
restore_operation_marker=${AGENTOPS_RESTORE_OPERATION_MARKER:-}
if [ -n "$restore_operation_marker" ] &&
  ! printf '%s\n' "$restore_operation_marker" |
    grep -Eq '^agentops_byoc_restore_v1:byoc_lifecycle_[0-9a-f]{20}:[0-9a-f]{64}$'
then
  printf '%s\n' "restore_operation_marker_invalid" >&2
  exit 2
fi

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
restore_identity_bound=false
restore_database_oid=
restore_cluster_identifier=
restore_expected_marker=

drop_restore_database() {
  if [ "$restore_identity_bound" != true ]; then
    return 1
  fi
  /bin/sh "$destructive_database_helper" \
    "$compose_file" \
    "$env_file" \
    drop \
    "$restore_database" \
    - \
    "$restore_database_oid" \
    "$restore_cluster_identifier" \
    exact \
    "$restore_expected_marker" \
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
      status=1
    fi
  fi
  if ! remove_restore_staging; then
    printf '%s\n' "restore_staging_cleanup_failed" >&2
    status=1
  fi
  if ! remove_validation_log; then
    printf '%s\n' "restore_validation_log_cleanup_failed" >&2
    status=1
  fi
  exit "$status"
}
trap 'cleanup_on_exit "$?"' 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu 'printf "%s\n" "SELECT pg_advisory_lock(7157544864185932631);" "CREATE DATABASE :\"database\";" | psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set ON_ERROR_STOP=1 --set database="$1" >/dev/null' \
  sh "$restore_database"
created=true
restore_identity=$(
  docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    sh -ceu 'psql --username "$POSTGRES_USER" --dbname "$1" --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align --command "SELECT (SELECT system_identifier::text FROM pg_control_system()) || chr(124) || (SELECT oid::text FROM pg_database WHERE datname=current_database())"' \
    sh "$restore_database"
)
case "$restore_identity" in
  *'|'*)
    restore_cluster_identifier=${restore_identity%%|*}
    restore_database_oid=${restore_identity#*|}
    ;;
  *)
    printf '%s\n' "restore_database_identity_invalid" >&2
    exit 1
    ;;
esac
case "$restore_cluster_identifier" in
  ""|*[!0-9]*) printf '%s\n' "restore_cluster_identity_invalid" >&2; exit 1 ;;
esac
case "$restore_database_oid" in
  ""|*[!0-9]*) printf '%s\n' "restore_database_identity_invalid" >&2; exit 1 ;;
esac
restore_identity_bound=true
if [ -n "$restore_operation_marker" ]; then
  docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
    sh -ceu 'printf "%s\n" "SELECT pg_advisory_lock(7157544864185932631);" "COMMENT ON DATABASE :\"database\" IS :'\''marker'\'';" | psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set ON_ERROR_STOP=1 --set database="$1" --set marker="$2" >/dev/null' \
    sh "$restore_database" "$restore_operation_marker"
  restore_expected_marker=$restore_operation_marker
fi

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -ceu '
    lease_key=7157544864185932631
    lease_ready=$(mktemp)
    lease_pid=
    lease_acquired=false
    restore_pid=
    restore_input_open=true
    exec 3<&0
    wait_for_lease_release() {
      [ "$lease_acquired" = true ] || return 0
      attempt=0
      while [ "$attempt" -lt 100 ]; do
        released=$(
          psql \
            --username "$POSTGRES_USER" \
            --dbname postgres \
            --no-psqlrc \
            --set ON_ERROR_STOP=1 \
            --tuples-only \
            --no-align \
            --command "SELECT CASE WHEN pg_try_advisory_lock($lease_key) THEN pg_advisory_unlock($lease_key) ELSE false END" \
            2>/dev/null || :
        )
        if [ "$released" = t ]; then
          lease_acquired=false
          return 0
        fi
        attempt=$((attempt + 1))
        sleep 0.1
      done
      printf "%s\n" restore_database_lease_release_failed >&2
      return 1
    }
    cleanup_lease() {
      cleanup_status=0
      if [ -n "$restore_pid" ]; then
        kill "$restore_pid" 2>/dev/null || :
        wait "$restore_pid" 2>/dev/null || :
        restore_pid=
      fi
      if [ "$restore_input_open" = true ]; then
        exec 3<&-
        restore_input_open=false
      fi
      if [ -n "$lease_pid" ]; then
        kill "$lease_pid" 2>/dev/null || :
        wait "$lease_pid" 2>/dev/null || :
        lease_pid=
      fi
      wait_for_lease_release || cleanup_status=1
      rm -f "$lease_ready"
      return "$cleanup_status"
    }
    trap cleanup_lease 0
    trap "exit 129" 1
    trap "exit 130" 2
    trap "exit 143" 15
    psql \
      --username "$POSTGRES_USER" \
      --dbname postgres \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 >/dev/null <<SQL &
SELECT pg_advisory_lock($lease_key);
\! printf "%s\n" ready > "$lease_ready"
SELECT pg_sleep(86400);
SQL
    lease_pid=$!
    attempt=0
    while [ ! -s "$lease_ready" ]; do
      if ! kill -0 "$lease_pid" 2>/dev/null; then
        printf "%s\n" restore_database_lease_acquire_failed >&2
        exit 1
      fi
      attempt=$((attempt + 1))
      if [ "$attempt" -ge 300 ]; then
        printf "%s\n" restore_database_lease_acquire_timeout >&2
        exit 1
      fi
      sleep 0.1
    done
    lease_acquired=true
    pg_restore --username "$POSTGRES_USER" --dbname "$1" --no-owner --no-privileges --exit-on-error <&3 &
    restore_pid=$!
    restore_status=0
    wait "$restore_pid" || restore_status=$?
    restore_pid=
    if [ "$restore_status" -ne 0 ]; then
      exit "$restore_status"
    fi
    cleanup_lease
    trap - 0 1 2 15
  ' sh "$restore_database" \
  < "$backup"

if ! validation_log=$(
  mktemp "${TMPDIR:-/tmp}/agentops-byoc-restore-validation.XXXXXXXX"
); then
  printf '%s\n' "restore_validation_log_failed" >&2
  exit 1
fi
chmod 600 "$validation_log"
validation_status=0
docker compose --env-file "$env_file" -f "$compose_file" run --rm \
  migrate sh /usr/local/lib/agentops/restore-provision.sh \
  "$restore_database" >/dev/null 2>"$validation_log" || validation_status=$?
validation_detail=$(
  awk '
    /^restore_provision_(migration|runtime_boundary|entitlement_admin_boundary)_failed:[a-z0-9_]+$/ {
      value = $0
    }
    END {
      if (value != "") print value
    }
  ' "$validation_log"
)
if ! remove_validation_log; then
  printf '%s\n' "restore_validation_log_cleanup_failed" >&2
  exit 1
fi
if [ -n "$validation_detail" ]; then
  printf '%s\n' "$validation_detail" >&2
fi

case "$validation_status" in
  0) ;;
  65)
    printf '%s\n' "restore_database_configuration_invalid" >&2
    exit 1
    ;;
  71)
    printf '%s\n' "restore_provisioning_failed" >&2
    exit 1
    ;;
  72)
    printf '%s\n' "restore_manifest_check_failed" >&2
    exit 1
    ;;
  73)
    printf '%s\n' "restore_runtime_role_boundary_failed" >&2
    exit 1
    ;;
  74)
    printf '%s\n' "restore_entitlement_admin_role_boundary_failed" >&2
    exit 1
    ;;
  75)
    printf '%s\n' "restore_credential_cleanup_failed" >&2
    exit 1
    ;;
  *)
    printf '%s\n' "restore_validation_failed" >&2
    exit 1
    ;;
esac

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

if ! remove_restore_staging; then
  printf '%s\n' "restore_staging_cleanup_failed" >&2
  exit 1
fi
trap - 0 1 2 15
printf '{"ok":true,"contract":"agentops_byoc_restore_drill_v4","staged_object_verified":true,"staged_object_read_only":true,"checksum_verified":true,"restore_provisioning_completed":true,"migration_manifest_verified":true,"schema_fingerprint_verified":true,"runtime_role_boundary_verified":true,"entitlement_admin_role_boundary_verified":true,"function_owner_boundary_verified":true,"isolated_role_dsns_file_backed":true,"production_overwritten":false,"restore_database_kept":%s,"cleanup_confirmed":%s,"restore_disposition_confirmed":true,"credentials_omitted":true}\n' "$kept" "$cleanup_confirmed"
