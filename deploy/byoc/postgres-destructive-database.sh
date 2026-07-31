#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 8 ]; then
  printf '%s\n' "usage: postgres-destructive-database.sh COMPOSE ENV OP DATABASE TARGET OID CLUSTER MARKER" >&2
  exit 2
fi

compose_file=$1
env_file=$2
operation=$3
database=$4
target_database=$5
expected_oid=$6
expected_cluster=$7
expected_marker=$8

case "$operation" in
  rename|drop) ;;
  *) printf '%s\n' "database_destructive_operation_invalid" >&2; exit 2 ;;
esac
case "$database" in
  [A-Za-z_]*)
    case "$database" in *[!A-Za-z0-9_]*) exit 2 ;; esac
    ;;
  *) exit 2 ;;
esac
if [ "$operation" = rename ]; then
  case "$target_database" in
    [A-Za-z_]*)
      case "$target_database" in *[!A-Za-z0-9_]*) exit 2 ;; esac
      ;;
    *) exit 2 ;;
  esac
elif [ "$target_database" != "-" ]; then
  exit 2
fi
case "$expected_oid" in
  ""|*[!0-9]*) exit 2 ;;
esac
case "$expected_cluster" in
  ""|*[!0-9]*) exit 2 ;;
esac
if [ -n "$expected_marker" ] &&
  ! printf '%s\n' "$expected_marker" |
    grep -Eq '^agentops_byoc_restore_v1:byoc_lifecycle_[0-9a-f]{20}:[0-9a-f]{64}$'
then
  exit 2
fi

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  sh -s -- \
    "$operation" \
    "$database" \
    "$target_database" \
    "$expected_oid" \
    "$expected_cluster" \
    "$expected_marker" <<'CONTAINER_SCRIPT'
set -eu

operation=$1
database=$2
target_database=$3
expected_oid=$4
expected_cluster=$5
expected_marker=$6

preflight=$(cat <<'SQL'
SELECT 1 / (pg_try_advisory_lock(hashtextextended('agentops_byoc_retained_data_lifecycle_v1', 0))::integer);
SELECT 1 / (EXISTS (
  SELECT 1
  FROM pg_database AS database
  CROSS JOIN pg_control_system() AS control
  WHERE database.datname = :'database'
    AND database.oid::text = :'expected_oid'
    AND control.system_identifier::text = :'expected_cluster'
    AND COALESCE(shobj_description(database.oid, 'pg_database'), '') = :'expected_marker'
)::integer);
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'database'
  AND pid <> pg_backend_pid();
SELECT 1 / (EXISTS (
  SELECT 1
  FROM pg_database AS database
  CROSS JOIN pg_control_system() AS control
  WHERE database.datname = :'database'
    AND database.oid::text = :'expected_oid'
    AND control.system_identifier::text = :'expected_cluster'
    AND COALESCE(shobj_description(database.oid, 'pg_database'), '') = :'expected_marker'
)::integer);
SQL
)

case "$operation" in
  rename) ddl='ALTER DATABASE :"database" RENAME TO :"target_database"' ;;
  drop) ddl='DROP DATABASE :"database" WITH (FORCE)' ;;
  *) exit 2 ;;
esac

printf '%s\n%s;\n' "$preflight" "$ddl" |
  psql \
    --username "$POSTGRES_USER" \
    --dbname postgres \
    --no-psqlrc \
    --set ON_ERROR_STOP=1 \
    --set database="$database" \
    --set target_database="$target_database" \
    --set expected_oid="$expected_oid" \
    --set expected_cluster="$expected_cluster" \
    --set expected_marker="$expected_marker" \
    >/dev/null
CONTAINER_SCRIPT
