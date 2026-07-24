import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

let activeCheck = "load_sources";

async function source(path: string) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

function serviceBlock(compose: string, service: string) {
  const escaped = service.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = compose.match(
    new RegExp(
      `^  ${escaped}:\\n[\\s\\S]*?(?=^  [A-Za-z0-9_-]+:\\n|^(?:volumes|networks|secrets):\\n|(?![\\s\\S]))`,
      "m",
    ),
  );
  assert.ok(match, `missing ${service} service`);
  return match[0];
}

async function run() {
  const [
    dockerfile,
    compose,
    environmentExample,
    readme,
    healthRoute,
    backupScript,
    restoreScript,
    behaviorContract,
  ] = await Promise.all([
    source("../../../deploy/byoc/Dockerfile"),
    source("../../../deploy/byoc/compose.yaml"),
    source("../../../deploy/byoc/.env.example"),
    source("../../../deploy/byoc/README.md"),
    source("../app/api/mis/health/route.ts"),
    source("../../../deploy/byoc/backup.sh"),
    source("../../../deploy/byoc/restore-drill.sh"),
    source("./byoc-backup-restore-behavior-contract.ts"),
  ]);

  activeCheck = "container_image";
  const pinnedNodeImage =
    "node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3";
  assert.match(
    dockerfile,
    new RegExp(`FROM ${pinnedNodeImage} AS dependencies`),
  );
  assert.match(
    dockerfile,
    new RegExp(`FROM ${pinnedNodeImage} AS runtime`),
  );
  assert.match(dockerfile, /npm ci --ignore-scripts/);
  assert.match(dockerfile, /npm run build/);
  assert.match(dockerfile, /npm prune --omit=dev --ignore-scripts/);
  assert.match(dockerfile, /USER node/);
  assert.match(dockerfile, /CMD \["node", "scripts\/start\.mjs"\]/);
  assert.doesNotMatch(dockerfile, /python|sqlite|curl\s|wget\s/i);

  activeCheck = "compose_baseline";
  assert.match(
    compose,
    /image: postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777/,
  );
  assert.match(compose, /command: \["npm", "run", "migrate:postgres"\]/);
  assert.match(compose, /condition: service_healthy/);
  assert.match(compose, /condition: service_completed_successfully/);
  assert.match(compose, /AGENTOPS_DEPLOYMENT_MODE: production/);
  assert.match(compose, /AGENTOPS_CONTROL_PLANE_MODE: postgres/);
  assert.match(compose, /AGENTOPS_BIND_ADDRESS:-127\.0\.0\.1/);
  assert.match(compose, /\/api\/mis\/health/);
  assert.doesNotMatch(
    compose,
    /privileged:|network_mode:\s*host|docker\.sock|python|sqlite/i,
  );

  activeCheck = "docker_secret_service_blocks";
  const postgresService = serviceBlock(compose, "postgres");
  const migrateService = serviceBlock(compose, "migrate");
  const controlPlaneService = serviceBlock(compose, "control-plane");
  activeCheck = "docker_secret_env_paths";
  assert.match(compose, /\$\{AGENTOPS_ALLOWED_ORIGINS:\?/);
  assert.match(environmentExample, /^AGENTOPS_ALLOWED_ORIGINS=/m);
  assert.match(
    environmentExample,
    /^AGENTOPS_POSTGRES_PASSWORD_FILE=\.\/secrets\/postgres-password$/m,
  );
  assert.match(
    environmentExample,
    /^AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE=\.\/secrets\/human-session-hmac-key$/m,
  );
  assert.doesNotMatch(
    environmentExample,
    /^(?:AGENTOPS_POSTGRES_PASSWORD|AGENTOPS_HUMAN_SESSION_HMAC_KEY)=/m,
  );
  activeCheck = "docker_secret_raw_value_absence";
  assert.doesNotMatch(
    compose,
    /^\s+(?:POSTGRES_PASSWORD|AGENTOPS_POSTGRES_PASSWORD|AGENTOPS_HUMAN_SESSION_HMAC_KEY):/m,
  );
  assert.doesNotMatch(
    compose,
    /\$\{(?:AGENTOPS_POSTGRES_PASSWORD|AGENTOPS_HUMAN_SESSION_HMAC_KEY)(?=[:}])/,
  );
  activeCheck = "docker_secret_top_level_files";
  assert.match(
    compose,
    /postgres_password:\s*\n\s+file:\s*\$\{AGENTOPS_POSTGRES_PASSWORD_FILE:\?[^}]+\}/,
  );
  assert.match(
    compose,
    /human_session_hmac_key:\s*\n\s+file:\s*\$\{AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE:\?[^}]+\}/,
  );
  activeCheck = "docker_secret_postgres_mount";
  assert.match(
    postgresService,
    /POSTGRES_PASSWORD_FILE:\s*\/run\/secrets\/postgres_password/,
  );
  assert.match(postgresService, /-\s*postgres_password/);
  assert.doesNotMatch(postgresService, /human_session_hmac_key|HMAC_KEY/);
  activeCheck = "docker_secret_migrator_mount";
  assert.match(
    migrateService,
    /AGENTOPS_POSTGRES_PASSWORD_FILE:\s*\/run\/secrets\/postgres_password/,
  );
  assert.match(migrateService, /-\s*postgres_password/);
  assert.doesNotMatch(
    migrateService,
    /human_session_hmac_key|AGENTOPS_HUMAN_SESSION_HMAC_KEY/,
  );
  activeCheck = "docker_secret_control_plane_mount";
  assert.match(
    controlPlaneService,
    /(?:AGENTOPS_POSTGRES_PASSWORD_FILE:\s*\/run\/secrets\/postgres_password|<<:\s*\*control_plane_database_environment)/,
  );
  assert.match(
    controlPlaneService,
    /AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE:\s*\/run\/secrets\/human_session_hmac_key/,
  );
  assert.match(controlPlaneService, /-\s*postgres_password/);
  assert.match(controlPlaneService, /-\s*human_session_hmac_key/);
  activeCheck = "docker_secret_readme";
  assert.match(readme, /For secret settings, keep only file paths in `.env`/);
  assert.match(readme, /migrator cannot read the Human Session HMAC key/);
  activeCheck = "docker_secret_example_scan";
  assert.doesNotMatch(
    environmentExample,
    /agtok_|agtsess_|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|postgresql:\/\//,
  );

  activeCheck = "health_owner";
  assert.match(healthRoute, /withPostgresTransaction/);
  assert.match(healthRoute, /POSTGRES_MIGRATION_MANIFEST/);
  assert.match(healthRoute, /control_plane: "typescript_postgres"/);
  assert.match(healthRoute, /python_proxy_performed: false/);
  assert.match(healthRoute, /sqlite_used: false/);
  assert.doesNotMatch(
    healthRoute,
    /proxyControlPlaneRequest|legacyPythonProxyAllowed|child_process|\.py\b/,
  );

  activeCheck = "backup_restore_packaging";
  assert.match(readme, /Next\.js\/TypeScript/);
  assert.match(readme, /PostgreSQL 16/);
  assert.match(readme, /Do not run `docker compose down --volumes`/);
  assert.match(readme, /COMMITTED/);
  assert.match(readme, /concurrent writers and symlinked/);
  assert.match(backupScript, /pg_dump/);
  assert.match(backupScript, /SHA256SUMS/);
  assert.match(backupScript, /COMMITTED\.pending/);
  assert.match(backupScript, /mktemp -d/);
  assert.match(backupScript, /mkdir -m 700/);
  assert.match(backupScript, /backup_output_exists/);
  assert.doesNotMatch(
    backupScript,
    /\.partial\.\$\$|--clean|DROP DATABASE|python|sqlite/i,
  );
  assert.match(restoreScript, /restore_bundle_incomplete/);
  assert.match(restoreScript, /restore_checksum_mismatch/);
  assert.match(restoreScript, /restore_database_must_not_be_production/);
  assert.match(restoreScript, /restore_cleanup_failed/);
  assert.match(restoreScript, /restore_manifest_check_failed/);
  assert.match(restoreScript, /pg_restore/);
  assert.match(restoreScript, /AGENTOPS_POSTGRES_PASSWORD_FILE/);
  assert.match(restoreScript, /AGENTOPS_POSTGRES_DATABASE=\$1/);
  assert.match(restoreScript, /npm run check:postgres-schema/);
  assert.match(restoreScript, /production_overwritten":false/);
  assert.match(restoreScript, /migration_manifest_verified":true/);
  assert.match(restoreScript, /restore_disposition_confirmed":true/);
  assert.doesNotMatch(restoreScript, /schema_verified":true/);
  assert.doesNotMatch(restoreScript, /--clean|--create|python|sqlite/i);
  assert.match(behaviorContract, /spawn\("sh"/);
  assert.match(behaviorContract, /concurrent_backup_fail_closed/);
  assert.match(behaviorContract, /restore_failure_cleanup/);
  assert.match(behaviorContract, /drop_failure_fail_closed/);

  activeCheck = "receipt";
  console.log(JSON.stringify({
    ok: true,
    contract: "agentops_byoc_packaging_v1",
    node_major: 22,
    postgres_major: 16,
    base_image_digests_pinned: true,
    non_root_runtime: true,
    migration_before_start: true,
    loopback_bind_default: true,
    required_secrets_fail_closed: true,
    docker_secrets_only: true,
    migrator_hmac_omitted: true,
    readiness_owner: "typescript_postgres",
    atomic_backup_bundle_commit: true,
    isolated_restore_schema_drill: true,
    executable_backup_restore_behavior_contract: true,
    python_used: false,
    sqlite_used: false,
    credentials_omitted: true,
  }));
}

run().catch(() => {
  console.error(JSON.stringify({
    ok: false,
    contract: "agentops_byoc_packaging_v1",
    error: "byoc_packaging_contract_failed",
    failed_check: activeCheck,
    credentials_omitted: true,
  }));
  process.exitCode = 1;
});
