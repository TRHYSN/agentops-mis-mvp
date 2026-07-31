#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 1 ]; then
  printf '%s\n' "restore_provision_usage_invalid" >&2
  exit 65
fi

restore_database=$1
case "$restore_database" in
  *[!A-Za-z0-9_]*|"") printf '%s\n' "restore_provision_database_invalid" >&2; exit 65 ;;
esac

lib_dir=${AGENTOPS_BYOC_LIB_DIR:-/usr/local/lib/agentops}
next_app_root=${AGENTOPS_BYOC_NEXT_APP_ROOT:-/opt/agentops/ui/next-app}
case "$lib_dir:$next_app_root" in
  /*:/*) ;;
  *) printf '%s\n' "restore_provision_path_invalid" >&2; exit 65 ;;
esac
if [ ! -d "$lib_dir" ] || [ -L "$lib_dir" ] ||
  [ ! -d "$next_app_root" ] || [ -L "$next_app_root" ]
then
  printf '%s\n' "restore_provision_path_invalid" >&2
  exit 65
fi

derived_dsn_files=
migration_receipt=
remove_derived_dsn_files() {
  status=$?
  trap - 0 1 2 15
  if [ -n "$migration_receipt" ]; then
    rm -f "$migration_receipt" || status=75
    migration_receipt=
  fi
  for derived_dsn_file in $derived_dsn_files; do
    rm -f "$derived_dsn_file" || status=75
  done
  exit "$status"
}
trap remove_derived_dsn_files 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

if [ -n "${AGENTOPS_POSTGRES_MIGRATOR_DSN:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_DSN:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_MIGRATOR_PASSWORD:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_PASSWORD:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD:-}" ] ||
  [ -n "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD:-}" ]
then
  exit 65
fi

migrator_dsn_file=${AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE:-}
migrator_host=${AGENTOPS_POSTGRES_MIGRATOR_HOST:-}
migrator_password_file=${AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE:-}
runtime_password_file=${AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:-}
admin_password_file=${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}
runtime_role=${AGENTOPS_POSTGRES_RUNTIME_ROLE:-}
admin_role=${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE:-}

if [ -n "$migrator_dsn_file" ]; then
  if [ -n "$migrator_host" ] ||
    [ -n "${AGENTOPS_POSTGRES_MIGRATOR_PORT:-}" ] ||
    [ -n "${AGENTOPS_POSTGRES_MIGRATOR_DATABASE:-}" ] ||
    [ -n "${AGENTOPS_POSTGRES_MIGRATOR_USER:-}" ] ||
    [ -n "$migrator_password_file" ] ||
    [ -z "$runtime_password_file" ] || [ -z "$admin_password_file" ] ||
    [ -z "$runtime_role" ] || [ -z "$admin_role" ]
  then
    exit 65
  fi
  dsn_source_file=$migrator_dsn_file
elif [ -n "$migrator_host" ] &&
  [ -n "${AGENTOPS_POSTGRES_MIGRATOR_DATABASE:-}" ] &&
  [ -n "$migrator_password_file" ] &&
  [ -n "${AGENTOPS_POSTGRES_MIGRATOR_USER:-}" ] &&
  [ -n "$runtime_password_file" ] &&
  [ -n "$admin_password_file" ] &&
  [ -n "$runtime_role" ] &&
  [ -n "$admin_role" ]
then
  dsn_source_file=$migrator_password_file
else
  exit 65
fi

migrator_restore_dsn="${dsn_source_file}.restore-migrator.$$"
runtime_restore_dsn="${dsn_source_file}.restore-runtime.$$"
admin_restore_dsn="${dsn_source_file}.restore-admin.$$"
derived_dsn_files="$migrator_restore_dsn $runtime_restore_dsn $admin_restore_dsn"

AGENTOPS_POSTGRES_USER=$runtime_role
AGENTOPS_POSTGRES_PASSWORD_FILE=$runtime_password_file
AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER=$admin_role
export AGENTOPS_POSTGRES_USER AGENTOPS_POSTGRES_PASSWORD_FILE
export AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER

node "$lib_dir/postgres-dsn-for-restore.mjs" \
  "$restore_database" "$migrator_restore_dsn" migrator migrator || exit 65
node "$lib_dir/postgres-dsn-for-restore.mjs" \
  "$restore_database" "$runtime_restore_dsn" migrator runtime || exit 65
node "$lib_dir/postgres-dsn-for-restore.mjs" \
  "$restore_database" "$admin_restore_dsn" migrator entitlement-admin || exit 65

unset AGENTOPS_POSTGRES_MIGRATOR_DSN AGENTOPS_POSTGRES_DSN
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN
unset AGENTOPS_POSTGRES_MIGRATOR_PASSWORD AGENTOPS_POSTGRES_PASSWORD
unset AGENTOPS_POSTGRES_RUNTIME_PASSWORD
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD
AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE=$migrator_restore_dsn
export AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE
unset AGENTOPS_POSTGRES_DSN_FILE
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE
unset AGENTOPS_POSTGRES_MIGRATOR_HOST
unset AGENTOPS_POSTGRES_MIGRATOR_PORT
unset AGENTOPS_POSTGRES_MIGRATOR_DATABASE
unset AGENTOPS_POSTGRES_MIGRATOR_USER
unset AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE
unset AGENTOPS_POSTGRES_HOST AGENTOPS_POSTGRES_PORT
unset AGENTOPS_POSTGRES_DATABASE AGENTOPS_POSTGRES_USER
unset AGENTOPS_POSTGRES_PASSWORD_FILE
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_HOST
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PORT
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DATABASE
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER

cd "$next_app_root"
if ! migration_receipt=$(
  mktemp "${TMPDIR:-/tmp}/agentops-restore-migration.XXXXXXXX"
); then
  printf '%s\n' "restore_provision_migration_failed:receipt_unavailable" >&2
  exit 71
fi
chmod 600 "$migration_receipt"
if ! npm run migrate:postgres >"$migration_receipt" 2>&1; then
  migration_code=$(
    sed -n \
      's/.*"error_code":"\([a-z0-9_][a-z0-9_]*\)".*/\1/p' \
      "$migration_receipt" |
      tail -n 1
  )
  case "$migration_code" in
    ""|*[!a-z0-9_]*) migration_code=unknown ;;
  esac
  printf '%s\n' \
    "restore_provision_migration_failed:$migration_code" >&2
  rm -f "$migration_receipt" || exit 75
  migration_receipt=
  exit 71
fi
rm -f "$migration_receipt" || exit 75
migration_receipt=
unset AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE
rm -f "$migrator_restore_dsn" || exit 75
derived_dsn_files="$runtime_restore_dsn $admin_restore_dsn"
unset AGENTOPS_POSTGRES_DSN AGENTOPS_POSTGRES_PASSWORD
unset AGENTOPS_POSTGRES_RUNTIME_PASSWORD
unset AGENTOPS_POSTGRES_HOST AGENTOPS_POSTGRES_PORT
unset AGENTOPS_POSTGRES_DATABASE AGENTOPS_POSTGRES_USER
unset AGENTOPS_POSTGRES_PASSWORD_FILE
unset AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE
AGENTOPS_POSTGRES_DSN_FILE=$runtime_restore_dsn
export AGENTOPS_POSTGRES_DSN_FILE

unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_HOST
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PORT
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DATABASE
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER
unset AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE
AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE=$admin_restore_dsn
export AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE
npm run check:postgres-schema >/dev/null 2>&1 || exit 72
./node_modules/.bin/tsx \
  "$lib_dir/postgres-role-boundary-check.ts" runtime \
  >/dev/null 2>&1 || exit 73
./node_modules/.bin/tsx \
  "$lib_dir/postgres-role-boundary-check.ts" entitlement-admin \
  >/dev/null 2>&1 || exit 74
