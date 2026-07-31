#!/bin/sh
set -eu

umask 077

required_environment='AGENTOPS_CROSS_SCHEMA_OLD_ENV_FILE
AGENTOPS_CROSS_SCHEMA_TARGET_ENV_FILE
AGENTOPS_CROSS_SCHEMA_OLD_IMAGE
AGENTOPS_CROSS_SCHEMA_OLD_IMAGE_ID
AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE
AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE_ID
AGENTOPS_CROSS_SCHEMA_BACKUP_BUNDLE
AGENTOPS_CROSS_SCHEMA_RESTORE_DATABASE
AGENTOPS_CROSS_SCHEMA_QUARANTINE_DATABASE
AGENTOPS_CROSS_SCHEMA_AUTHORITY_ID
AGENTOPS_CROSS_SCHEMA_PROBE_SUFFIX'
for name in $required_environment; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    printf '%s\n' "cross_schema_environment_missing:$name" >&2
    exit 2
  fi
done

old_compose_file=${AGENTOPS_CROSS_SCHEMA_OLD_COMPOSE_FILE:-deploy/byoc/compose.historical-v9.yaml}
target_compose_file=${AGENTOPS_CROSS_SCHEMA_TARGET_COMPOSE_FILE:-deploy/byoc/compose.yaml}

case "$AGENTOPS_CROSS_SCHEMA_OLD_IMAGE" in
  *@sha256:????????????????????????????????????????????????????????????????) ;;
  *) printf '%s\n' 'cross_schema_old_digest_required' >&2; exit 2 ;;
esac
case "$AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE" in
  *@sha256:????????????????????????????????????????????????????????????????) ;;
  *) printf '%s\n' 'cross_schema_target_digest_required' >&2; exit 2 ;;
esac
case "$AGENTOPS_CROSS_SCHEMA_OLD_IMAGE_ID:$AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE_ID" in
  sha256:????????????????????????????????????????????????????????????????:sha256:????????????????????????????????????????????????????????????????) ;;
  *) printf '%s\n' 'cross_schema_image_id_invalid' >&2; exit 2 ;;
esac
if [ "$AGENTOPS_CROSS_SCHEMA_OLD_IMAGE_ID" = "$AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE_ID" ]; then
  printf '%s\n' 'cross_schema_images_must_differ' >&2
  exit 2
fi

old_compose() {
  docker compose \
    --env-file "$AGENTOPS_CROSS_SCHEMA_OLD_ENV_FILE" \
    -f "$old_compose_file" "$@"
}

target_compose() {
  docker compose \
    --env-file "$AGENTOPS_CROSS_SCHEMA_TARGET_ENV_FILE" \
    -f "$target_compose_file" "$@"
}

health_receipt() {
  output=$1
  expected_schema=$2
  attempt=0
  while [ "$attempt" -lt 30 ]; do
    attempt=$((attempt + 1))
    if curl \
      --fail \
      --silent \
      --show-error \
      --max-time 10 \
      --output "$output" \
      http://127.0.0.1:3001/api/mis/health \
      && jq -e --arg schema "$expected_schema" '
        .ok == true
        and .status == "ready"
        and .control_plane == "typescript_postgres"
        and .schema_contract == $schema
        and .schema_ready == true
        and .schema_fingerprint_verified == true
        and .python_proxy_performed == false
        and .sqlite_used == false
        and .credentials_omitted == true
      ' "$output" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  printf '%s\n' "cross_schema_health_not_ready:$expected_schema" >&2
  printf 'health_response_bytes=%s\n' "$(wc -c <"$output" | tr -d ' ')" >&2
  printf 'health_response_sha256=%s\n' "$(sha256sum "$output" | awk '{print $1}')" >&2
  return 1
}

temporary_root=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
old_health="$temporary_root/agentops-cross-schema-old-health.json"
target_health="$temporary_root/agentops-cross-schema-target-health.json"
restored_health="$temporary_root/agentops-cross-schema-restored-health.json"
migration_output="$temporary_root/agentops-cross-schema-migration.log"
migration_receipt="$temporary_root/agentops-cross-schema-migration.json"
old_check_output="$temporary_root/agentops-cross-schema-old-check.log"
old_check_receipt="$temporary_root/agentops-cross-schema-old-check.json"
old_migration_output="$temporary_root/agentops-cross-schema-old-migration.log"
old_migration_receipt="$temporary_root/agentops-cross-schema-old-migration.json"

old_compose config --quiet
target_compose config --quiet

old_compose up \
  --detach \
  --no-build \
  --wait \
  --wait-timeout 300 \
  postgres
if ! old_compose run --rm --no-deps migrate >"$old_migration_output"; then
  cat "$old_migration_output" >&2
  exit 1
fi
awk '/^\{.*\}$/{line=$0} END{if(line) print line}' \
  "$old_migration_output" >"$old_migration_receipt"
jq -e '
  .ok == true
  and .contract == "agentops_postgres_schema_readiness_v1"
  and .operation == "migrate"
  and .schema_contract == "agentops_commercial_postgres_v9"
  and .manifest_count == 10
  and .applied_count == 10
  and .current_count == 0
  and .schema_fingerprint_verified == true
  and .schema_object_count == 745
' "$old_migration_receipt" >/dev/null
old_compose up \
  --detach \
  --no-build \
  --wait \
  --wait-timeout 300 \
  control-plane
health_receipt "$old_health" agentops_commercial_postgres_v9

old_container=$(old_compose ps -q control-plane)
test -n "$old_container"
test "$(docker inspect --format '{{.Image}}' "$old_container")" = \
  "$AGENTOPS_CROSS_SCHEMA_OLD_IMAGE_ID"

old_schema_identity=$(
  old_compose exec -T --user 1000:1000 \
    control-plane npm run byoc:schema-identity --silent
)
printf '%s\n' "$old_schema_identity" | jq -e '
  .ok == true
  and .contract == "agentops_byoc_schema_identity_v1"
  and .historical_adapter_contract
    == "agentops_byoc_historical_schema_adapter_v1"
  and .historical_build_compatibility_patch
    == "migration_root_runtime_resolution_v1"
  and .historical_source_revision
    == "f55def1233403a503a39d9af92371a71770c23f7"
  and .schema_contract == "agentops_commercial_postgres_v9"
  and .schema_object_count == 745
  and .migration_count == 10
  and .static_manifest_only == true
  and .database_contacted == false
' >/dev/null

old_database_identity=$(
  old_compose exec -T control-plane \
    node /usr/local/lib/agentops/historical-v9-secret-entrypoint.mjs \
      --postgres -- npm run byoc:database-identity --silent
)
printf '%s\n' "$old_database_identity" | jq -e '
  .ok == true
  and .contract == "agentops_byoc_database_identity_v1"
  and .historical_adapter_contract
    == "agentops_byoc_historical_schema_adapter_v1"
  and .authority_database == "agentops"
  and .runtime_role_verified == true
  and .database_contacted == true
' >/dev/null

volume_name=$(
  docker volume ls \
    --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
    --filter 'label=com.docker.compose.volume=agentops_postgres_data' \
    --format '{{.Name}}'
)
test -n "$volume_name"
test "$(printf '%s\n' "$volume_name" | wc -l | tr -d ' ')" = '1'
volume_identity_before=$(
  docker volume inspect \
    --format '{{.Name}}|{{.Mountpoint}}|{{.CreatedAt}}' \
    "$volume_name"
)
cluster_identifier_before=$(
  old_compose exec -T postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --command "SELECT system_identifier FROM pg_control_system()"'
)
test -n "$cluster_identifier_before"

old_compose exec -T \
  -e "ACCEPTANCE_AUTHORITY_ID=$AGENTOPS_CROSS_SCHEMA_AUTHORITY_ID" \
  postgres sh -ceu \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --set=ON_ERROR_STOP=1 --set=authority_id="$ACCEPTANCE_AUTHORITY_ID"' \
  >/dev/null <<'SQL'
INSERT INTO audit_logs(
  audit_id,actor_type,actor_id,action,entity_type,entity_id,
  metadata_json,created_at
) VALUES (
  :'authority_id','system','byoc-cross-schema-acceptance',
  'cross_schema_v9_authority','acceptance',:'authority_id',
  '{"authority":"pre_backup_v9"}','2026-07-31T00:00:00.000Z'
);
SQL

active_runs=$(
  old_compose exec -T postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --command "SELECT count(*) FROM runs WHERE status IN ('\''running'\'','\''waiting_approval'\'')"'
)
test "$active_runs" = '0'

old_compose stop control-plane
AGENTOPS_BYOC_COMPOSE_FILE="$old_compose_file" \
AGENTOPS_BYOC_ENV_FILE="$AGENTOPS_CROSS_SCHEMA_OLD_ENV_FILE" \
  deploy/byoc/backup.sh "$AGENTOPS_CROSS_SCHEMA_BACKUP_BUNDLE" \
  | jq -e '
      .ok == true
      and .contract == "agentops_byoc_backup_v2"
      and .bundle_committed == true
      and .backup_created == true
      and .checksum_created == true
    ' >/dev/null
test "$(cat "$AGENTOPS_CROSS_SCHEMA_BACKUP_BUNDLE/COMMITTED")" = \
  'agentops_byoc_backup_bundle_v2'

target_compose run --rm migrate >"$migration_output"
awk '/^\{.*\}$/{line=$0} END{if(line) print line}' \
  "$migration_output" >"$migration_receipt"
jq -e '
  .ok == true
  and .contract == "agentops_postgres_schema_readiness_v1"
  and .operation == "migrate"
  and .schema_contract == "agentops_commercial_postgres_v11"
  and .manifest_count == 13
  and .applied_count == 3
  and .current_count == 10
  and .schema_fingerprint_verified == true
  and .schema_object_count == 861
' "$migration_receipt" >/dev/null

target_compose up \
  --detach \
  --no-build \
  --wait \
  --wait-timeout 300 \
  control-plane
health_receipt "$target_health" agentops_commercial_postgres_v11

target_container=$(target_compose ps -q control-plane)
test -n "$target_container"
test "$(docker inspect --format '{{.Image}}' "$target_container")" = \
  "$AGENTOPS_CROSS_SCHEMA_TARGET_IMAGE_ID"

target_schema_identity=$(
  target_compose exec -T control-plane npm run byoc:schema-identity --silent
)
printf '%s\n' "$target_schema_identity" | jq -e '
  .ok == true
  and .contract == "agentops_byoc_schema_identity_v1"
  and .schema_contract == "agentops_commercial_postgres_v11"
  and .schema_object_count == 861
  and .migration_count == 13
' >/dev/null

probe_user="usr_cross_schema_$AGENTOPS_CROSS_SCHEMA_PROBE_SUFFIX"
probe_session="hss_cross_schema_$AGENTOPS_CROSS_SCHEMA_PROBE_SUFFIX"
probe_challenge="entc_$AGENTOPS_CROSS_SCHEMA_PROBE_SUFFIX"
target_compose exec -T \
  -e "PROBE_USER=$probe_user" \
  -e "PROBE_SESSION=$probe_session" \
  -e "PROBE_CHALLENGE=$probe_challenge" \
  postgres sh -ceu \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --set=ON_ERROR_STOP=1 --set=probe_user="$PROBE_USER" --set=probe_session="$PROBE_SESSION" --set=probe_challenge="$PROBE_CHALLENGE"' \
  >/dev/null <<'SQL'
INSERT INTO users(user_id,name,email,role,created_at)
VALUES (
  :'probe_user','Cross Schema Probe','cross-schema-probe@example.invalid',
  'admin','2026-07-31T00:01:00.000Z'
);
INSERT INTO human_sessions(
  session_id,user_id,session_hash,status,created_at,expires_at
) VALUES (
  :'probe_session',:'probe_user',repeat('a',64),'active',
  '2026-07-31T00:01:00.000Z','2026-07-31T01:01:00.000Z'
);
INSERT INTO entitlement_admin_challenges(
  challenge_id,token_sha256,request_json,request_sha256,workspace_id,
  operator_user_id,human_session_id,mode,bound_admin_role,issued_at,expires_at
) VALUES (
  :'probe_challenge',repeat('b',64),'{}',repeat('c',64),
  'ws_cross_schema_probe',:'probe_user',:'probe_session','plan',
  'agentops_entitlement_admin','2026-07-31T00:01:00.000Z',
  '2026-07-31T00:02:00.000Z'
);
SQL

probe_count=$(
  target_compose exec -T \
    -e "PROBE_CHALLENGE=$probe_challenge" \
    postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --set=probe_challenge="$PROBE_CHALLENGE"' <<'SQL'
SELECT count(*)
FROM entitlement_admin_challenges
WHERE challenge_id=:'probe_challenge';
SQL
)
test "$probe_count" = '1'

target_compose stop control-plane
target_compose exec -T \
  -e "RESTORE_DATABASE=$AGENTOPS_CROSS_SCHEMA_RESTORE_DATABASE" \
  postgres sh -ceu '
    dropdb --if-exists --username "$POSTGRES_USER" "$RESTORE_DATABASE"
    createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" "$RESTORE_DATABASE"
  '
target_compose exec -T \
  -e "RESTORE_DATABASE=$AGENTOPS_CROSS_SCHEMA_RESTORE_DATABASE" \
  postgres sh -ceu \
  'pg_restore --exit-on-error --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$RESTORE_DATABASE"' \
  <"$AGENTOPS_CROSS_SCHEMA_BACKUP_BUNDLE/database.dump"

old_compose run --rm --no-deps \
  -e "AGENTOPS_POSTGRES_DATABASE=$AGENTOPS_CROSS_SCHEMA_RESTORE_DATABASE" \
  migrate npm run check:postgres-schema >"$old_check_output"
awk '/^\{.*\}$/{line=$0} END{if(line) print line}' \
  "$old_check_output" >"$old_check_receipt"
jq -e '
  .ok == true
  and .contract == "agentops_postgres_schema_readiness_v1"
  and .operation == "check"
  and .schema_contract == "agentops_commercial_postgres_v9"
  and .manifest_count == 10
  and .current_count == 10
  and .schema_fingerprint_verified == true
  and .schema_object_count == 745
' "$old_check_receipt" >/dev/null

target_compose exec -T \
  -e "RESTORE_DATABASE=$AGENTOPS_CROSS_SCHEMA_RESTORE_DATABASE" \
  -e "QUARANTINE_DATABASE=$AGENTOPS_CROSS_SCHEMA_QUARANTINE_DATABASE" \
  postgres sh -ceu \
  'psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set=ON_ERROR_STOP=1 --set=production="$POSTGRES_DB" --set=restore="$RESTORE_DATABASE" --set=quarantine="$QUARANTINE_DATABASE"' \
  >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname IN (:'production',:'restore',:'quarantine')
  AND pid<>pg_backend_pid();
DROP DATABASE IF EXISTS :"quarantine";
ALTER DATABASE :"production" RENAME TO :"quarantine";
ALTER DATABASE :"restore" RENAME TO :"production";
SQL

old_compose up \
  --detach \
  --no-build \
  --wait \
  --wait-timeout 300 \
  control-plane
health_receipt "$restored_health" agentops_commercial_postgres_v9

restored_container=$(old_compose ps -q control-plane)
test -n "$restored_container"
test "$(docker inspect --format '{{.Image}}' "$restored_container")" = \
  "$AGENTOPS_CROSS_SCHEMA_OLD_IMAGE_ID"

rollback_evidence=$(
  old_compose exec -T \
    -e "ACCEPTANCE_AUTHORITY_ID=$AGENTOPS_CROSS_SCHEMA_AUTHORITY_ID" \
    postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --set=authority_id="$ACCEPTANCE_AUTHORITY_ID"' <<'SQL'
SELECT (
  SELECT count(*) FROM audit_logs WHERE audit_id=:'authority_id'
)::text
  || '|'
  || (to_regclass('entitlement_admin_challenges') IS NULL)::text
  || '|'
  || (SELECT count(*) FROM agentops_schema_migrations)::text;
SQL
)
test "$rollback_evidence" = '1|true|10'

old_compose exec -T \
  -e "QUARANTINE_DATABASE=$AGENTOPS_CROSS_SCHEMA_QUARANTINE_DATABASE" \
  postgres sh -ceu \
  'dropdb --if-exists --username "$POSTGRES_USER" "$QUARANTINE_DATABASE"'

target_role_count=$(
  old_compose exec -T postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --tuples-only --no-align --command "SELECT count(*) FROM pg_roles WHERE rolname IN ('\''agentops_runtime'\'','\''agentops_entitlement_admin'\'') OR rolname LIKE '\''agentops_fn_%'\''"'
)
test "$target_role_count" = '3'
old_compose exec -T postgres sh -ceu \
  'psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set=ON_ERROR_STOP=1' \
  >/dev/null <<'SQL'
SELECT format('DROP ROLE %I',rolname)
FROM pg_roles
WHERE rolname IN ('agentops_runtime','agentops_entitlement_admin')
   OR rolname LIKE 'agentops_fn_%'
ORDER BY rolname
\gexec
SQL
remaining_target_roles=$(
  old_compose exec -T postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --tuples-only --no-align --command "SELECT count(*) FROM pg_roles WHERE rolname IN ('\''agentops_runtime'\'','\''agentops_entitlement_admin'\'') OR rolname LIKE '\''agentops_fn_%'\''"'
)
test "$remaining_target_roles" = '0'

test "$(docker volume inspect --format '{{.Name}}|{{.Mountpoint}}|{{.CreatedAt}}' "$volume_name")" = \
  "$volume_identity_before"
test "$(
  old_compose exec -T postgres sh -ceu \
    'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --command "SELECT system_identifier FROM pg_control_system()"'
)" = "$cluster_identifier_before"

printf '%s\n' '{"ok":true,"contract":"agentops_byoc_cross_schema_v9_v11_acceptance_v1","old_source_revision":"f55def1233403a503a39d9af92371a71770c23f7","source_schema_contract":"agentops_commercial_postgres_v9","target_schema_contract":"agentops_commercial_postgres_v11","source_migrations_applied":10,"forward_migrations_applied":3,"v11_data_probe_written":true,"backup_restore_authoritative":true,"down_migration_performed":false,"old_authority_preserved":true,"target_probe_removed":true,"old_image_restored":true,"postgres_volume_preserved":true,"postgres_cluster_identity_preserved":true,"target_roles_cleaned":true,"python_used":false,"sqlite_used":false,"mock_docker_used":false,"credentials_omitted":true,"row_data_omitted":true}'
