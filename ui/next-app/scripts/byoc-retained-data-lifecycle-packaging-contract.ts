import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";

async function source(path: string) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

const [cli, state, schemaIdentity, packageJson, readme, backup, restore] =
  await Promise.all([
    source("../../../deploy/byoc/retained-data-lifecycle.mjs"),
    source("../../../deploy/byoc/retained-data-lifecycle-state.mjs"),
    source("./byoc-schema-identity.ts"),
    source("../package.json"),
    source("../../../deploy/byoc/README.md"),
    source("../../../deploy/byoc/backup.sh"),
    source("../../../deploy/byoc/restore-drill.sh"),
  ]);

assert.match(cli, /plan", "status", "apply", "rollback"/);
assert.match(cli, /--confirm-restore-from-backup/);
assert.match(cli, /lifecycle_active_runs_must_be_drained/);
assert.match(cli, /configurationSnapshot/);
assert.match(cli, /runBackup\(context, backupPath\)/);
assert.match(cli, /validateBackupBundle/);
assert.match(cli, /runRestoreDrill/);
assert.match(cli, /rollback_authority: "backup_restore"/);
assert.match(cli, /down_migration_performed: false/);
assert.doesNotMatch(cli, /down migration|down-migration|migrate:down|schema:down/i);
assert.match(cli, /IMAGE_DIGEST_REFERENCE/);
assert.match(cli, /migration_manifest_sha256/);
assert.match(cli, /schema_fingerprint_sha256/);
assert.match(cli, /database_change_started/);
assert.match(cli, /recovery_required/);
assert.match(cli, /state_promoted: false/);
assert.match(cli, /credentials_omitted: true/);
assert.match(cli, /sql_omitted: true/);
assert.match(cli, /row_data_omitted: true/);

assert.match(state, /LIFECYCLE_STATE_CONTRACT/);
assert.match(state, /\.state\.\$\{process\.pid\}/);
assert.match(state, /await handle\.sync\(\)/);
assert.match(state, /await rename\(temporary, target\)/);
assert.match(state, /await directory\.sync\(\)/);
assert.match(state, /\.operation\.lock/);
assert.match(state, /open\(temporary, "wx", 0o600\)/);
assert.match(state, /open\(join\(lock, "owner\.json"\), "wx", 0o600\)/);
assert.match(state, /mode: 0o700/);

assert.match(schemaIdentity, /SCHEMA_CONTRACT/);
assert.match(schemaIdentity, /EXPECTED_POSTGRES_SCHEMA_FINGERPRINT/);
assert.match(schemaIdentity, /POSTGRES_MIGRATION_MANIFEST/);
assert.match(schemaIdentity, /migration_manifest_sha256/);
assert.match(schemaIdentity, /database_contacted: false/);

const scripts = JSON.parse(packageJson).scripts as Record<string, string>;
assert.equal(
  scripts["byoc:schema-identity"],
  "tsx scripts/byoc-schema-identity.ts",
);
assert.equal(
  scripts["test:byoc-retained-data-lifecycle-behavior-contract"],
  "tsx scripts/byoc-retained-data-lifecycle-behavior-contract.ts",
);
assert.equal(
  scripts["test:byoc-retained-data-lifecycle-packaging-contract"],
  "tsx scripts/byoc-retained-data-lifecycle-packaging-contract.ts",
);

assert.match(readme, /Retained-data lifecycle/);
assert.match(readme, /retained-data-lifecycle\.mjs plan/);
assert.match(readme, /retained-data-lifecycle\.mjs status/);
assert.match(readme, /retained-data-lifecycle\.mjs apply/);
assert.match(readme, /retained-data-lifecycle\.mjs rollback/);
assert.match(readme, /--confirm-restore-from-backup/);
assert.match(readme, /backup restore is authoritative/i);
assert.match(readme, /does not perform an\s+in-place down migration/i);
assert.match(readme, /offline behavior and packaging evidence/i);
assert.match(
  readme,
  /do not prove a real Docker\/Compose upgrade or rollback/i,
);

assert.match(backup, /COMMITTED\.pending/);
assert.match(backup, /mv "\$staging\/COMMITTED\.pending" "\$output\/COMMITTED"/);
assert.match(restore, /restore_database_must_not_be_production/);
assert.match(restore, /restore_provisioning_completed/);

const executable = await stat(
  new URL("../../../deploy/byoc/retained-data-lifecycle.mjs", import.meta.url),
);
assert.ok((executable.mode & 0o111) !== 0, "lifecycle CLI must be executable");

console.log(JSON.stringify({
  contract: "agentops_byoc_retained_data_lifecycle_packaging_contract_v1",
  ok: true,
  operator_commands_packaged: true,
  atomic_state_packaged: true,
  immutable_image_binding_packaged: true,
  schema_identity_binding_packaged: true,
  backup_commit_binding_packaged: true,
  restore_authoritative_rollback_packaged: true,
  in_place_down_migration_forbidden: true,
  runtime_claims_omitted: true,
  credentials_omitted: true,
}));
