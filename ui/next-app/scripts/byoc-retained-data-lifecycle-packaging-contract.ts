import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";

async function source(path: string) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

const [
  cli,
  state,
  schemaIdentity,
  databaseIdentity,
  packageJson,
  readme,
  backup,
  restore,
  destructiveDatabase,
] =
  await Promise.all([
    source("../../../deploy/byoc/retained-data-lifecycle.mjs"),
    source("../../../deploy/byoc/retained-data-lifecycle-state.mjs"),
    source("./byoc-schema-identity.ts"),
    source("./byoc-database-identity.ts"),
    source("../package.json"),
    source("../../../deploy/byoc/README.md"),
    source("../../../deploy/byoc/backup.sh"),
    source("../../../deploy/byoc/restore-drill.sh"),
    source("../../../deploy/byoc/postgres-destructive-database.sh"),
  ]);

assert.match(cli, /"recover-lock"/);
assert.match(cli, /--confirm-restore-from-backup/);
assert.match(cli, /--confirm-operation-id/);
assert.match(cli, /recoverStaleLifecycleLock/);
assert.match(cli, /lifecycle_cleanup_required/);
assert.match(cli, /lifecycle_plan_id_required/);
assert.match(cli, /lifecycle_active_runs_must_be_drained/);
assert.match(cli, /configurationSnapshot/);
assert.match(
  cli,
  /\"exec\",[\s\S]*?\"control-plane\",[\s\S]*?\"check:postgres-schema\"/,
);
assert.doesNotMatch(
  cli,
  /\"run\",[\s\S]*?\"migrate\",[\s\S]*?\"check:postgres-schema\"/,
);
assert.match(cli, /runBackup\(context, backupPath\)/);
assert.match(cli, /validateBackupBundle/);
assert.match(cli, /runRestoreDrill/);
assert.match(cli, /rollback_authority: "backup_restore"/);
assert.match(cli, /down_migration_performed: false/);
assert.match(cli, /quarantine_cleanup_pending: true/);
assert.match(cli, /boundAuthorityDatabase/);
assert.match(cli, /postgresClusterSystemIdentifier/);
assert.match(cli, /pg_control_system/);
assert.match(cli, /shobj_description/);
assert.match(cli, /authority_database_oid/);
assert.match(cli, /restore_database_marker/);
assert.match(cli, /dropBoundDatabase/);
assert.match(cli, /postgres-destructive-database\.sh/);
assert.match(cli, /registerLifecycleChildProcess/);
assert.match(cli, /releaseLifecycleChildProcess/);
assert.match(cli, /agentops_child_lease_ready_v1/);
assert.match(cli, /rollbackCleanupComplete/);
assert.match(cli, /databasePresence/);
assert.match(cli, /restore_intent/);
assert.match(cli, /production_rename_started/);
assert.match(cli, /production_quarantined/);
assert.match(cli, /restore_promotion_started/);
assert.match(cli, /restore_promoted/);
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
assert.match(state, /agentops_byoc_lifecycle_lock_v2/);
assert.match(state, /recoverStaleLifecycleLock/);
assert.match(state, /lifecycle_lock_recovery_cross_host_refused/);
assert.match(state, /agentops_byoc_lifecycle_child_v1/);
assert.match(state, /registerLifecycleChildProcess/);
assert.match(state, /lifecycle_lock_recovery_child_alive/);

assert.match(schemaIdentity, /SCHEMA_CONTRACT/);
assert.match(schemaIdentity, /EXPECTED_POSTGRES_SCHEMA_FINGERPRINT/);
assert.match(schemaIdentity, /POSTGRES_MIGRATION_MANIFEST/);
assert.match(schemaIdentity, /migration_manifest_sha256/);
assert.match(schemaIdentity, /database_contacted: false/);
assert.match(databaseIdentity, /current_database\(\)/);
assert.match(databaseIdentity, /runtime_role_verified: true/);
assert.match(databaseIdentity, /database_contacted: true/);

const scripts = JSON.parse(packageJson).scripts as Record<string, string>;
assert.equal(
  scripts["byoc:schema-identity"],
  "tsx scripts/byoc-schema-identity.ts",
);
assert.equal(
  scripts["byoc:database-identity"],
  "tsx scripts/byoc-database-identity.ts",
);
assert.equal(
  scripts["test:byoc-retained-data-lifecycle-behavior-contract"],
  "tsx scripts/byoc-retained-data-lifecycle-behavior-contract.ts",
);
assert.equal(
  scripts["test:byoc-retained-data-lifecycle-lock-behavior-contract"],
  "tsx scripts/byoc-retained-data-lifecycle-lock-behavior-contract.ts",
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
assert.match(readme, /retained-data-lifecycle\.mjs cleanup/);
assert.match(readme, /retained-data-lifecycle\.mjs recover-lock/);
assert.match(readme, /--confirm-restore-from-backup/);
assert.match(readme, /--confirm-operation-id/);
assert.match(readme, /runtime connection's actual\s+authority database/);
assert.match(readme, /stops the\s+control plane/);
assert.match(readme, /fsyncs a committed backup bundle/);
assert.match(readme, /production_rename_started/);
assert.match(readme, /restore_intent/);
assert.match(readme, /backup restore is authoritative/i);
assert.match(readme, /quarantine_cleanup_pending=true/);
assert.match(readme, /does not perform an\s+in-place down migration/i);
assert.match(readme, /offline injected Docker driver/i);
assert.match(
  readme,
  /does not prove a forward\s+upgrade across Schema versions/i,
);

assert.match(backup, /COMMITTED\.pending/);
assert.match(backup, /mv "\$staging\/COMMITTED\.pending" "\$output\/COMMITTED"/);
assert.match(backup, /fs\.fsyncSync/);
assert.match(backup, /fsync_path "\$output"/);
assert.match(backup, /fsync_path "\$output_parent"/);
assert.match(restore, /restore_database_must_not_be_production/);
assert.match(restore, /AGENTOPS_RESTORE_OPERATION_MARKER/);
assert.match(restore, /COMMENT ON DATABASE/);
assert.match(restore, /restore_provisioning_completed/);
assert.doesNotMatch(restore, /\bdropdb\b/);
assert.match(restore, /postgres-destructive-database\.sh/);
assert.match(destructiveDatabase, /pg_try_advisory_lock/);
assert.match(destructiveDatabase, /system_identifier/);
assert.match(destructiveDatabase, /shobj_description/);
assert.match(destructiveDatabase, /DROP DATABASE/);
assert.match(destructiveDatabase, /ALTER DATABASE/);
assert.match(destructiveDatabase, /printf '%s\\n%s;\\n' "\$preflight" "\$ddl"/);

const executable = await stat(
  new URL("../../../deploy/byoc/retained-data-lifecycle.mjs", import.meta.url),
);
assert.ok((executable.mode & 0o111) !== 0, "lifecycle CLI must be executable");
const destructiveExecutable = await stat(
  new URL(
    "../../../deploy/byoc/postgres-destructive-database.sh",
    import.meta.url,
  ),
);
assert.ok(
  (destructiveExecutable.mode & 0o111) !== 0,
  "destructive database helper must be executable",
);

console.log(JSON.stringify({
  contract: "agentops_byoc_retained_data_lifecycle_packaging_contract_v1",
  ok: true,
  operator_commands_packaged: true,
  atomic_state_packaged: true,
  immutable_image_binding_packaged: true,
  schema_identity_binding_packaged: true,
  backup_commit_binding_packaged: true,
  restore_authoritative_rollback_packaged: true,
  orphan_restore_recovery_packaged: true,
  explicit_cleanup_retry_packaged: true,
  stale_lock_recovery_packaged: true,
  pending_cleanup_blocks_new_plan: true,
  postgres_cluster_and_database_oid_binding_packaged: true,
  restore_operation_marker_packaged: true,
  destructive_database_advisory_lock_packaged: true,
  lifecycle_child_process_leases_packaged: true,
  strict_cleanup_completion_gate_packaged: true,
  in_place_down_migration_forbidden: true,
  runtime_claims_omitted: true,
  credentials_omitted: true,
}));
