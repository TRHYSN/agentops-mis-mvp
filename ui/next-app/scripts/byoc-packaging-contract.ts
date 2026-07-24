import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

async function source(path: string) {
  return readFile(new URL(path, import.meta.url), "utf8");
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
  ] = await Promise.all([
    source("../../../deploy/byoc/Dockerfile"),
    source("../../../deploy/byoc/compose.yaml"),
    source("../../../deploy/byoc/.env.example"),
    source("../../../deploy/byoc/README.md"),
    source("../app/api/mis/health/route.ts"),
    source("../../../deploy/byoc/backup.sh"),
    source("../../../deploy/byoc/restore-drill.sh"),
  ]);

  assert.match(dockerfile, /FROM node:22-bookworm-slim AS dependencies/);
  assert.match(dockerfile, /npm ci --ignore-scripts/);
  assert.match(dockerfile, /npm run build/);
  assert.match(dockerfile, /npm prune --omit=dev --ignore-scripts/);
  assert.match(dockerfile, /USER node/);
  assert.match(dockerfile, /CMD \["node", "scripts\/start\.mjs"\]/);
  assert.doesNotMatch(dockerfile, /python|sqlite|curl\s|wget\s/i);

  assert.match(compose, /image: postgres:16-alpine/);
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

  for (const key of [
    "AGENTOPS_ALLOWED_ORIGINS",
    "AGENTOPS_POSTGRES_PASSWORD",
    "AGENTOPS_HUMAN_SESSION_HMAC_KEY",
  ]) {
    assert.match(compose, new RegExp(`\\$\\{${key}:\\?`));
    assert.match(environmentExample, new RegExp(`^${key}=`, "m"));
  }
  assert.match(environmentExample, /replace_with_a_url_safe_random_password/);
  assert.match(environmentExample, /replace_with_at_least_32_random_bytes/);
  assert.doesNotMatch(
    environmentExample,
    /agtok_|agtsess_|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|postgresql:\/\//,
  );

  assert.match(healthRoute, /withPostgresTransaction/);
  assert.match(healthRoute, /POSTGRES_MIGRATION_MANIFEST/);
  assert.match(healthRoute, /control_plane: "typescript_postgres"/);
  assert.match(healthRoute, /python_proxy_performed: false/);
  assert.match(healthRoute, /sqlite_used: false/);
  assert.doesNotMatch(
    healthRoute,
    /proxyControlPlaneRequest|legacyPythonProxyAllowed|child_process|\.py\b/,
  );

  assert.match(readme, /Next\.js\/TypeScript/);
  assert.match(readme, /PostgreSQL 16/);
  assert.match(readme, /Do not run `docker compose down --volumes`/);
  assert.match(backupScript, /pg_dump/);
  assert.match(backupScript, /shasum -a 256/);
  assert.match(backupScript, /backup_output_exists/);
  assert.doesNotMatch(backupScript, /--clean|DROP DATABASE|python|sqlite/i);
  assert.match(restoreScript, /restore_checksum_mismatch/);
  assert.match(restoreScript, /restore_database_must_not_be_production/);
  assert.match(restoreScript, /pg_restore/);
  assert.match(restoreScript, /npm run check:postgres-schema/);
  assert.match(restoreScript, /production_overwritten":false/);
  assert.doesNotMatch(restoreScript, /--clean|--create|python|sqlite/i);

  console.log(JSON.stringify({
    ok: true,
    contract: "agentops_byoc_packaging_v1",
    node_major: 22,
    postgres_major: 16,
    non_root_runtime: true,
    migration_before_start: true,
    loopback_bind_default: true,
    required_secrets_fail_closed: true,
    readiness_owner: "typescript_postgres",
    atomic_backup_with_checksum: true,
    isolated_restore_schema_drill: true,
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
    credentials_omitted: true,
  }));
  process.exitCode = 1;
});
