import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const repositoryRoot = resolve(process.cwd(), "../..");
const historicalRevision = "f55def1233403a503a39d9af92371a71770c23f7";
const oldManifestHash =
  "8cf59998821a27c37b949bcdb94897ad3b61ce4341b79f37cc0e93365d750838";
const packageLockHash =
  "22ae9970d43e8a9896ee61b71de9834fb1512f42c770d80784191b5dad2caaba";

function sha256(value: string | Buffer) {
  return createHash("sha256").update(value).digest("hex");
}

function git(...arguments_: string[]) {
  const result = spawnSync("git", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `git_failed:${arguments_.join("_")}`);
  return result.stdout;
}

const [
  workflow,
  acceptance,
  historicalCompose,
  historicalDockerfile,
  adapterInstaller,
  schemaAdapter,
  databaseAdapter,
  secretAdapter,
  identityRaw,
  currentManifest,
  currentLock,
] = await Promise.all([
  readFile(resolve(
    repositoryRoot,
    ".github/workflows/byoc-cross-schema-v9-v11-acceptance.yml",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/cross-schema-v9-v11-acceptance.sh",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/compose.historical-v9.yaml",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9.Dockerfile",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9-adapter/install.mjs",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9-adapter/schema-identity.mjs",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9-adapter/database-identity.mjs",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9-adapter/secret-entrypoint.mjs",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "deploy/byoc/historical-v9-adapter/identity.json",
  ), "utf8"),
  readFile(resolve(
    repositoryRoot,
    "ui/next-app/src/server/controlPlane/schemaManifest.ts",
  ), "utf8"),
  readFile(resolve(repositoryRoot, "ui/next-app/package-lock.json")),
]);

assert.doesNotThrow(() => JSON.parse(identityRaw));
const identity = JSON.parse(identityRaw);
assert.deepEqual(identity, {
  contract: "agentops_byoc_historical_schema_adapter_v1",
  source_revision: historicalRevision,
  schema_contract: "agentops_commercial_postgres_v9",
  schema_fingerprint_contract: "agentops_postgres_schema_fingerprint_v1",
  schema_fingerprint_sha256:
    "eb482da9beb9a5c4cd1afdadaf114d3828991285107f71d0ad620a02d377f36e",
  schema_object_count: 745,
  migration_manifest_sha256:
    "122a4b9a9b7a384a6a323daf10ee0f034dc9df15ca4f011efe0f178de08eaf37",
  migration_count: 10,
  schema_manifest_file_sha256: oldManifestHash,
  schema_readiness_file_sha256:
    "947a414c3c7e1411e71168a29604b7a195773b876a76418245c36c56fe734ccd",
  build_compatibility_patch: "migration_root_runtime_resolution_v1",
  package_lock_sha256: packageLockHash,
});

git("cat-file", "-e", `${historicalRevision}^{commit}`);
git("merge-base", "--is-ancestor", historicalRevision, "HEAD");
const oldManifest = git(
  "show",
  `${historicalRevision}:ui/next-app/src/server/controlPlane/schemaManifest.ts`,
);
const oldLock = git(
  "show",
  `${historicalRevision}:ui/next-app/package-lock.json`,
);
const oldReadiness = git(
  "show",
  `${historicalRevision}:ui/next-app/src/server/controlPlane/schemaReadiness.ts`,
);
assert.equal(sha256(oldManifest), oldManifestHash);
assert.equal(sha256(oldLock), packageLockHash);
assert.equal(
  sha256(oldReadiness),
  "947a414c3c7e1411e71168a29604b7a195773b876a76418245c36c56fe734ccd",
);
assert.equal(sha256(currentLock), packageLockHash);
assert.match(oldManifest, /agentops_commercial_postgres_v9/);
assert.match(oldManifest, /objectCount:\s*745/);
assert.equal((oldManifest.match(/^    component:/gm) || []).length, 10);
assert.match(currentManifest, /agentops_commercial_postgres_v11/);
assert.match(currentManifest, /objectCount:\s*861/);
assert.equal((currentManifest.match(/^    component:/gm) || []).length, 13);

assert.match(historicalDockerfile, /FROM node:22-bookworm-slim@sha256:/);
assert.match(historicalDockerfile, /historical-source\/ui\/next-app/);
assert.match(
  historicalDockerfile,
  /historical-source\/migrations\/postgres \/opt\/agentops\/migrations\/postgres/,
);
assert.match(historicalDockerfile, /npm ci --ignore-scripts/);
assert.match(historicalDockerfile, /npm run build/);
assert.match(historicalDockerfile, /AGENTOPS_HISTORICAL_SOURCE_REVISION/);
assert.match(historicalDockerfile, /agentops_commercial_postgres_v9/);
assert.match(historicalDockerfile, /agentops_byoc_historical_schema_adapter_v1/);
assert.match(historicalDockerfile, /historical-v9-secret-entrypoint\.mjs/);
assert.match(adapterInstaller, /historical_adapter_source_mismatch/);
assert.match(adapterInstaller, /historical_adapter_command_collision/);
assert.match(adapterInstaller, /historical_adapter_build_patch_mismatch/);
assert.match(adapterInstaller, /migration_root_runtime_resolution_v1/);
assert.match(adapterInstaller, /replace\(oldImport, newImport\)/);
assert.match(adapterInstaller, /byoc:schema-identity/);
assert.match(adapterInstaller, /byoc:database-identity/);
assert.match(schemaAdapter, /static_manifest_only:\s*true/);
assert.match(schemaAdapter, /database_contacted:\s*false/);
assert.match(
  schemaAdapter,
  /historical_build_compatibility_patch:\s*identity\.build_compatibility_patch/,
);
assert.match(databaseAdapter, /BEGIN READ ONLY/);
assert.match(databaseAdapter, /current_database\(\)/);
assert.match(databaseAdapter, /runtime_role_verified:\s*true/);
assert.doesNotMatch(databaseAdapter, /console\.log\([^)]*(password|dsn)/i);
assert.match(secretAdapter, /agentops_byoc_historical_secret_adapter_v1/);
assert.match(secretAdapter, /process\.setuid\(NODE_UID\)/);
assert.match(secretAdapter, /NoNewPrivs/);
assert.match(secretAdapter, /credentials_omitted:\s*true/);
assert.doesNotMatch(secretAdapter, /console\.log\([^)]*(password|dsn)/i);

assert.match(historicalCompose, /postgres:16-alpine@sha256:/);
assert.match(historicalCompose, /AGENTOPS_POSTGRES_MIGRATOR_USER/);
assert.match(historicalCompose, /AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE/);
assert.match(historicalCompose, /AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE/);
assert.match(historicalCompose, /historical-v9-secret-entrypoint\.mjs/);
assert.match(historicalCompose, /no-new-privileges:true/);
assert.match(historicalCompose, /cap_drop:\s*\n\s*- ALL/);
assert.match(historicalCompose, /process\.setuid\(NODE_UID\)|user:\s*"0:0"/);
assert.match(historicalCompose, /npm.*migrate:postgres/);
assert.doesNotMatch(historicalCompose, /AGENTOPS_POSTGRES_RUNTIME_USER/);
assert.doesNotMatch(historicalCompose, /build:/);

assert.match(workflow, new RegExp(historicalRevision));
assert.match(workflow, /path:\s*historical-source/);
assert.match(workflow, /historical-v9\.Dockerfile/);
assert.match(workflow, /deploy\/byoc\/Dockerfile/);
assert.match(workflow, /registry:2@sha256:/);
assert.match(workflow, /grep -E '\^v22\\\.'/);
assert.match(workflow, /cross-schema-v9-v11-acceptance\.sh/);
assert.match(workflow, /forward_migrations_applied == 3/);
assert.match(workflow, /backup_restore_authoritative == true/);
assert.doesNotMatch(workflow, /\bpython(?:3)?\s+/i);
assert.doesNotMatch(workflow, /\bsqlite3?\s+/i);
assert.doesNotMatch(workflow, /mock[_ -]docker\s*[:=]\s*true/i);

const backupPosition = acceptance.indexOf("deploy/byoc/backup.sh");
const migrationPosition = acceptance.indexOf("target_compose run --rm migrate");
const probePosition = acceptance.indexOf("INSERT INTO entitlement_admin_challenges");
const restorePosition = acceptance.indexOf("pg_restore --exit-on-error");
const quarantinePosition = acceptance.indexOf("RENAME TO :\"quarantine\"");
const promotionPosition = acceptance.indexOf("RENAME TO :\"production\"");
const restoredStartPosition = acceptance.lastIndexOf("old_compose up");
assert.ok(backupPosition > 0);
assert.ok(migrationPosition > backupPosition);
assert.ok(probePosition > migrationPosition);
assert.ok(restorePosition > probePosition);
assert.ok(quarantinePosition > restorePosition);
assert.ok(promotionPosition > quarantinePosition);
assert.ok(restoredStartPosition > promotionPosition);
assert.match(acceptance, /applied_count == 10/);
assert.match(acceptance, /applied_count == 3/);
assert.match(acceptance, /current_count == 10/);
assert.match(acceptance, /schema_object_count == 861/);
assert.match(acceptance, /schema_object_count == 745/);
assert.doesNotMatch(acceptance, /--command\s+"[^"]*:'[a-z_]+/);
assert.match(
  acceptance,
  /historical-v9-secret-entrypoint\.mjs[\s\\]*\n\s*--postgres -- npm run byoc:database-identity/,
);
assert.match(acceptance, /exec -T --user 1000:1000/);
assert.match(acceptance, /to_regclass\('entitlement_admin_challenges'\)/);
assert.match(acceptance, /DROP ROLE %I/);
assert.match(acceptance, /target_role_count.*'3'/s);
assert.equal(
  (acceptance.match(/>\/dev\/null <<'SQL'/g) || []).length,
  4,
  "mutating_psql_status_must_not_pollute_machine_receipt",
);
assert.match(acceptance, /down_migration_performed\":false/);
assert.doesNotMatch(acceptance, /\bpython(?:3)?\s+/i);
assert.doesNotMatch(acceptance, /\bsqlite3?\s+/i);

console.log(JSON.stringify({
  ok: true,
  contract: "agentops_byoc_cross_schema_v9_v11_packaging_v1",
  historical_source_revision: historicalRevision,
  historical_source_is_current_ancestor: true,
  historical_node22_image_build_required: true,
  historical_schema_identity_audited: true,
  historical_database_identity_read_only: true,
  source_schema_contract: "agentops_commercial_postgres_v9",
  target_schema_contract: "agentops_commercial_postgres_v11",
  source_migration_count: 10,
  forward_manifest_delta: 3,
  backup_precedes_migration: true,
  v11_only_data_probe_required: true,
  backup_restore_database_swap_required: true,
  old_image_restart_required: true,
  target_role_cleanup_required: true,
  existing_same_schema_workflow_unchanged: true,
  python_runtime_forbidden: true,
  sqlite_runtime_forbidden: true,
  mock_docker_forbidden: true,
  credentials_omitted: true,
}));
