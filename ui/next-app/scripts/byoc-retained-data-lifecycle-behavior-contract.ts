import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  chmod,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// @ts-expect-error The operator CLI is intentionally plain ESM.
import { LifecycleError, runLifecycle } from "../../../deploy/byoc/retained-data-lifecycle.mjs";
// @ts-expect-error The lifecycle state helper is intentionally plain ESM.
import { writeLifecycleState } from "../../../deploy/byoc/retained-data-lifecycle-state.mjs";

const FROM_REFERENCE = `registry.example/agentops@sha256:${"a".repeat(64)}`;
const TO_REFERENCE = `registry.example/agentops@sha256:${"b".repeat(64)}`;
const FROM_IMAGE_ID = `sha256:${"1".repeat(64)}`;
const TO_IMAGE_ID = `sha256:${"2".repeat(64)}`;
const SECRET_CANARY = "raw_secret_canary_must_never_escape";

type SchemaIdentity = Readonly<{
  contract: string;
  fingerprint_contract: string;
  fingerprint_sha256: string;
  object_count: number;
  migration_manifest_sha256: string;
  migration_count: number;
}>;

const FROM_SCHEMA: SchemaIdentity = Object.freeze({
  contract: "agentops_commercial_postgres_v10",
  fingerprint_contract: "agentops_postgres_schema_fingerprint_v1",
  fingerprint_sha256: "3".repeat(64),
  object_count: 850,
  migration_manifest_sha256: "4".repeat(64),
  migration_count: 12,
});

const TO_SCHEMA: SchemaIdentity = Object.freeze({
  contract: "agentops_commercial_postgres_v11",
  fingerprint_contract: "agentops_postgres_schema_fingerprint_v1",
  fingerprint_sha256: "5".repeat(64),
  object_count: 861,
  migration_manifest_sha256: "6".repeat(64),
  migration_count: 13,
});

function schemaIdentity(schema: SchemaIdentity) {
  return JSON.stringify({
    contract: "agentops_byoc_schema_identity_v1",
    ok: true,
    schema_contract: schema.contract,
    schema_fingerprint_contract: schema.fingerprint_contract,
    schema_fingerprint_sha256: schema.fingerprint_sha256,
    schema_object_count: schema.object_count,
    migration_manifest_sha256: schema.migration_manifest_sha256,
    migration_count: schema.migration_count,
    static_manifest_only: true,
    database_contacted: false,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  });
}

function databaseIdentity(database: string) {
  return JSON.stringify({
    contract: "agentops_byoc_database_identity_v1",
    ok: true,
    authority_database: database,
    runtime_role_verified: true,
    database_contacted: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  });
}

function readiness(schema: SchemaIdentity, operation = "check") {
  return JSON.stringify({
    contract: "agentops_postgres_schema_readiness_v1",
    ok: true,
    operation,
    schema_contract: schema.contract,
    manifest_count: schema.migration_count,
    applied_count: 0,
    current_count: schema.migration_count,
    lock_acquired: true,
    read_only: operation === "check",
    schema_fingerprint_contract: schema.fingerprint_contract,
    schema_fingerprint_verified: true,
    schema_object_count: schema.object_count,
    database_role_boundary_verified: true,
    runtime_role_omitted: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  });
}

type FakeDriver = ReturnType<typeof createFakeDriver>;

function createFakeDriver() {
  const calls: Array<Readonly<{
    command: string;
    args: string[];
    image: string;
  }>> = [];
  const state = {
    activeRuns: 0,
    configVersion: "v1",
    currentReference: FROM_REFERENCE,
    currentImageId: FROM_IMAGE_ID,
    failMigration: false,
    failMigrationWithCanary: false,
    failPostStopActiveRunCheck: false,
    failTargetReadiness: false,
    failControlPlaneStart: false,
    failDatabaseTerminationOnce: false,
    failRestorePromotionRenameOnce: false,
    failAfterProductionRenameOnce: false,
    failAfterRestorePromotionRenameOnce: false,
    failRestoreDrillAfterCreate: false,
    failRestoreDatabaseDrop: false,
    failQuarantineDrop: false,
    replaceDatabaseBeforeDestructiveOnce: false,
    replaceClusterBeforeDestructiveOnce: false,
    controlPlaneRunning: true,
    productionDatabase: "agentops",
    runtimeDatabase: "agentops",
    clusterSystemIdentifier: "7390012345678901234",
    databases: new Set(["agentops"]),
    databaseOids: new Map([["agentops", "16384"]]),
    databaseMarkers: new Map<string, string>([
      ["agentops", "customer_managed_database_comment"],
    ]),
    nextDatabaseOid: 16385,
    restoreDatabases: new Set<string>(),
    sql: [] as string[],
  };

  const imageIdFor = (image: string) => {
    if (image === FROM_REFERENCE || image === FROM_IMAGE_ID) return FROM_IMAGE_ID;
    if (image === TO_REFERENCE || image === TO_IMAGE_ID) return TO_IMAGE_ID;
    return "";
  };
  const schemaFor = (image: string) =>
    imageIdFor(image) === FROM_IMAGE_ID ? FROM_SCHEMA : TO_SCHEMA;

  const runner = async (
    command: string,
    args: string[],
    options: { env?: NodeJS.ProcessEnv } = {},
  ) => {
    const image = String(options.env?.AGENTOPS_IMAGE || "");
    calls.push({ command, args: [...args], image });
    const joined = args.join(" ");
    const ok = (stdout = "") => ({ status: 0, stdout, stderr: "" });
    const failed = (stderr = SECRET_CANARY) => ({ status: 1, stdout: "", stderr });

    if (command === "docker" && args[0] === "image" && args[1] === "inspect") {
      const id = imageIdFor(args.at(-1) || "");
      return id ? ok(`${id}\n`) : failed();
    }
    if (command === "docker" && args[0] === "run") {
      const requested = args.find((value) => imageIdFor(value));
      return requested ? ok(`${schemaIdentity(schemaFor(requested))}\n`) : failed();
    }
    if (command === "docker" && args[0] === "inspect") {
      if (joined.includes(".Config.Image")) {
        return state.controlPlaneRunning
          ? ok(`${state.currentReference}\t${state.currentImageId}\n`)
          : failed();
      }
      if (joined.includes("State.Health")) {
        return state.controlPlaneRunning ? ok("healthy\n") : ok("exited\n");
      }
    }
    if (
      command === "/bin/sh"
      && args[0]?.endsWith("/postgres-destructive-database.sh")
    ) {
      const operation = args[3] || "";
      const database = args[4] || "";
      const targetDatabase = args[5] || "";
      const expectedOid = args[6] || "";
      const expectedCluster = args[7] || "";
      const markerMode = args[8] || "";
      const expectedMarker = args[9] || "";
      state.sql.push(
        `destructive:${operation}:${database}:${targetDatabase}:${expectedOid}:${expectedCluster}:${markerMode}:${expectedMarker}`,
      );
      if (!["ignore", "exact"].includes(markerMode)) {
        return failed("destructive_marker_mode_invalid");
      }
      if (state.replaceDatabaseBeforeDestructiveOnce) {
        state.replaceDatabaseBeforeDestructiveOnce = false;
        state.databaseOids.set(database, String(state.nextDatabaseOid++));
        state.databaseMarkers.set(database, "replacement_object");
      }
      if (state.replaceClusterBeforeDestructiveOnce) {
        state.replaceClusterBeforeDestructiveOnce = false;
        state.clusterSystemIdentifier = "7390088888888888888";
      }
      if (
        state.clusterSystemIdentifier !== expectedCluster
        || state.databaseOids.get(database) !== expectedOid
        || (
          markerMode === "exact"
          && (state.databaseMarkers.get(database) || "") !== expectedMarker
        )
      ) {
        return failed("destructive_identity_changed");
      }
      if (state.failDatabaseTerminationOnce) {
        state.failDatabaseTerminationOnce = false;
        return failed("database_termination_failed");
      }
      if (
        state.failQuarantineDrop
        && operation === "drop"
        && database.startsWith("agentops_quarantine_")
      ) {
        return failed("quarantine_drop_failed");
      }
      if (
        state.failRestoreDatabaseDrop
        && operation === "drop"
        && database.startsWith("agentops_restore_")
      ) {
        return failed("restore_cleanup_failed");
      }
      if (
        state.failRestorePromotionRenameOnce
        && operation === "rename"
        && database.startsWith("agentops_restore_")
        && targetDatabase === "agentops"
      ) {
        state.failRestorePromotionRenameOnce = false;
        return failed("restore_promotion_failed");
      }
      if (operation === "rename") {
        if (
          !state.databases.has(database)
          || state.databases.has(targetDatabase)
        ) {
          return failed("rename_state_invalid");
        }
        state.databases.delete(database);
        state.databases.add(targetDatabase);
        const oid = state.databaseOids.get(database);
        if (!oid) return failed("rename_identity_missing");
        state.databaseOids.delete(database);
        state.databaseOids.set(targetDatabase, oid);
        const marker = state.databaseMarkers.get(database);
        state.databaseMarkers.delete(database);
        if (marker) state.databaseMarkers.set(targetDatabase, marker);
        if (
          state.failAfterProductionRenameOnce
          && database === "agentops"
          && targetDatabase.startsWith("agentops_quarantine_")
        ) {
          state.failAfterProductionRenameOnce = false;
          return failed("production_rename_interrupted");
        }
        if (
          state.failAfterRestorePromotionRenameOnce
          && database.startsWith("agentops_restore_")
          && targetDatabase === "agentops"
        ) {
          state.failAfterRestorePromotionRenameOnce = false;
          return failed("restore_promotion_interrupted");
        }
        return ok();
      }
      if (operation === "drop") {
        state.databases.delete(database);
        state.databaseOids.delete(database);
        state.databaseMarkers.delete(database);
        return ok();
      }
      return failed("destructive_operation_invalid");
    }
    if (command === "docker" && args[0] === "compose") {
      if (joined.includes(" config --quiet")) return ok();
      if (joined.endsWith(" config")) return ok(`rendered-compose-${state.configVersion}\n`);
      if (joined.includes(" ps -q control-plane")) {
        return state.controlPlaneRunning ? ok("container-control-plane\n") : ok();
      }
      if (joined.includes(" exec -T control-plane npm run byoc:schema-identity")) {
        return ok(`${schemaIdentity(schemaFor(state.currentImageId))}\n`);
      }
      if (
        joined.includes(
          " exec -T control-plane node /usr/local/lib/agentops/node-secret-entrypoint.mjs --postgres-runtime -- npm run byoc:database-identity",
        )
      ) {
        return ok(`${databaseIdentity(state.runtimeDatabase)}\n`);
      }
      if (
        joined.includes(" exec -T postgres")
        && joined.includes("pg_try_advisory_lock(7157544864185932631")
        && joined.includes("pg_advisory_unlock(7157544864185932631")
      ) {
        return ok("t\n");
      }
      if (joined.includes(" exec -T postgres") && joined.includes("SELECT count(*)")) {
        if (
          state.failPostStopActiveRunCheck
          && !state.controlPlaneRunning
        ) {
          state.failPostStopActiveRunCheck = false;
          return failed("active_run_check_failed");
        }
        return ok(`${state.activeRuns}\n`);
      }
      if (joined.includes(" exec -T postgres") && joined.includes("printf '%s'")) {
        return ok(state.productionDatabase);
      }
      if (joined.includes(" exec -T postgres") && joined.includes("--command \"$1\"")) {
        const sql = args.at(-1) || "";
        state.sql.push(sql);
        if (
          state.failDatabaseTerminationOnce
          && sql.includes("pg_terminate_backend")
        ) {
          state.failDatabaseTerminationOnce = false;
          return failed("database_termination_failed");
        }
        if (
          state.failQuarantineDrop
          && sql.includes("DROP DATABASE IF EXISTS \"agentops_quarantine_")
        ) {
          return failed("quarantine_drop_failed");
        }
        if (
          state.failRestorePromotionRenameOnce
          && sql.includes("ALTER DATABASE \"agentops_restore_")
          && sql.endsWith(" RENAME TO \"agentops\"")
        ) {
          state.failRestorePromotionRenameOnce = false;
          return failed("restore_promotion_failed");
        }
        if (
          state.failRestoreDatabaseDrop
          && sql.includes("DROP DATABASE IF EXISTS \"agentops_restore_")
        ) {
          return failed("restore_cleanup_failed");
        }
        if (sql.startsWith("SELECT datname FROM pg_database")) {
          return ok(
            [...state.databases].filter((database) => sql.includes(`'${database}'`))
              .sort()
              .join("\n") + "\n",
          );
        }
        if (sql === "SELECT system_identifier::text FROM pg_control_system()") {
          return ok(`${state.clusterSystemIdentifier}\n`);
        }
        if (sql.startsWith("SELECT oid::text || '|'")) {
          const match = sql.match(/WHERE datname='([^']+)'$/);
          const database = match?.[1] || "";
          const oid = state.databaseOids.get(database);
          return oid
            ? ok(`${oid}|${state.databaseMarkers.get(database) || ""}\n`)
            : ok();
        }
        const rename = sql.match(
          /^ALTER DATABASE "([^"]+)" RENAME TO "([^"]+)"$/,
        );
        if (rename) {
          const [, from, to] = rename;
          if (!state.databases.has(from) || state.databases.has(to)) {
            return failed("rename_state_invalid");
          }
          state.databases.delete(from);
          state.databases.add(to);
          const oid = state.databaseOids.get(from);
          if (!oid) return failed("rename_identity_missing");
          state.databaseOids.delete(from);
          state.databaseOids.set(to, oid);
          const marker = state.databaseMarkers.get(from);
          state.databaseMarkers.delete(from);
          if (marker) state.databaseMarkers.set(to, marker);
          if (
            state.failAfterProductionRenameOnce
            && from === "agentops"
            && to.startsWith("agentops_quarantine_")
          ) {
            state.failAfterProductionRenameOnce = false;
            return failed("production_rename_interrupted");
          }
          if (
            state.failAfterRestorePromotionRenameOnce
            && from.startsWith("agentops_restore_")
            && to === "agentops"
          ) {
            state.failAfterRestorePromotionRenameOnce = false;
            return failed("restore_promotion_interrupted");
          }
          return ok();
        }
        const drop = sql.match(/^DROP DATABASE IF EXISTS "([^"]+)"$/);
        if (drop) {
          state.databases.delete(drop[1]);
          state.databaseOids.delete(drop[1]);
          state.databaseMarkers.delete(drop[1]);
          return ok();
        }
        return ok();
      }
      if (joined.includes(" stop control-plane")) {
        state.controlPlaneRunning = false;
        return ok();
      }
      if (joined.includes(" up --detach")) {
        if (state.failControlPlaneStart) return failed("start_failed");
        const id = imageIdFor(image);
        if (!id) return failed();
        state.currentReference = image;
        state.currentImageId = id;
        state.controlPlaneRunning = true;
        return ok();
      }
      if (joined.includes(
        " exec -T control-plane npm run check:postgres-schema",
      )) {
        if (
          state.failTargetReadiness
          && state.currentImageId === TO_IMAGE_ID
        ) {
          return failed("target_readiness_failed");
        }
        return ok(`${readiness(schemaFor(state.currentImageId))}\n`);
      }
      if (joined.includes(" run --rm --no-deps migrate")) {
        if (state.failMigration) {
          return failed(state.failMigrationWithCanary ? SECRET_CANARY : "migration_failed");
        }
        return ok(`${readiness(schemaFor(image), "migrate")}\n`);
      }
    }
    if (command === "/bin/sh" && args[0]?.endsWith("/backup.sh")) {
      const bundle = args[1];
      const dump = Buffer.from("retained-authority-data\n");
      const hash = createHash("sha256").update(dump).digest("hex");
      await mkdir(bundle, { recursive: false, mode: 0o700 });
      await writeFile(join(bundle, "database.dump"), dump, { mode: 0o600 });
      await writeFile(join(bundle, "SHA256SUMS"), `${hash}  database.dump\n`, { mode: 0o600 });
      await writeFile(
        join(bundle, "COMMITTED"),
        "agentops_byoc_backup_bundle_v2\n",
        { mode: 0o600 },
      );
      return ok(JSON.stringify({ ok: true }));
    }
    if (command === "/bin/sh" && args[0]?.endsWith("/restore-drill.sh")) {
      assert.equal(options.env?.AGENTOPS_RESTORE_KEEP, "true");
      assert.equal(image, FROM_IMAGE_ID);
      const restoreDatabase = String(options.env?.AGENTOPS_RESTORE_DATABASE || "");
      const restoreMarker = String(
        options.env?.AGENTOPS_RESTORE_OPERATION_MARKER || "",
      );
      assert.match(restoreDatabase, /^agentops_restore_[0-9a-f]{12}$/);
      assert.match(
        restoreMarker,
        /^agentops_byoc_restore_v1:byoc_lifecycle_[0-9a-f]{20}:[0-9a-f]{64}$/,
      );
      state.restoreDatabases.add(restoreDatabase);
      state.databases.add(restoreDatabase);
      state.databaseOids.set(
        restoreDatabase,
        String(state.nextDatabaseOid++),
      );
      state.databaseMarkers.set(restoreDatabase, restoreMarker);
      if (state.failRestoreDrillAfterCreate) {
        state.failRestoreDrillAfterCreate = false;
        return failed("restore_drill_interrupted");
      }
      return ok(JSON.stringify({
        ok: true,
        contract: "agentops_byoc_restore_drill_v4",
      }));
    }
    return failed(`unexpected command: ${command} ${joined}`);
  };
  return { calls, runner, state };
}

function isLifecycleError(error: unknown, code: string) {
  return error instanceof Error
    && "code" in error
    && String(error.code) === code;
}

async function expectFailure(
  operation: () => Promise<unknown>,
  code: string,
) {
  await assert.rejects(operation, (error: unknown) => isLifecycleError(error, code));
}

async function lifecycleOptions(
  root: string,
  stateDirectory: string,
  driver: FakeDriver,
) {
  const envFile = join(root, "byoc.env");
  await writeFile(envFile, "AGENTOPS_ALLOWED_ORIGINS=https://mis.example.test\n", {
    mode: 0o600,
  });
  return {
    repositoryRoot: resolve(new URL("../../..", import.meta.url).pathname),
    stateDirectory,
    environment: {
      AGENTOPS_BYOC_COMPOSE_FILE: resolve(
        new URL("../../../deploy/byoc/compose.yaml", import.meta.url).pathname,
      ),
      AGENTOPS_BYOC_ENV_FILE: envFile,
      AGENTOPS_BYOC_LIFECYCLE_HEALTH_TIMEOUT_SEC: "2",
    },
    runner: driver.runner,
    randomHex: () => "contract-nonce",
    now: (() => {
      let second = 0;
      return () => new Date(Date.UTC(2026, 6, 31, 12, 0, second++));
    })(),
    wait: async () => undefined,
  };
}

async function proveClosedLoop(root: string) {
  const stateDirectory = join(root, "closed-loop-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);

  driver.state.activeRuns = 2;
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  assert.equal(planned.operation, "plan");
  assert.equal(planned.apply_blocked, true);
  assert.equal(planned.from_image_id, FROM_IMAGE_ID);
  assert.equal(planned.to_image_id, TO_IMAGE_ID);
  assert.match(planned.operation_id, /^byoc_lifecycle_[0-9a-f]{20}$/);

  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_active_runs_must_be_drained",
  );
  let status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "planned");
  assert.equal(status.state.operation.backup, null);
  assert.equal(status.state.operation.recovery_required, false);

  driver.state.activeRuns = 0;
  await expectFailure(
    () => runLifecycle(["apply"], options),
    "lifecycle_plan_id_required",
  );
  const applied = await runLifecycle(
    ["apply", "--plan-id", planned.operation_id],
    options,
  );
  assert.equal(applied.phase, "applied");
  assert.equal(applied.image_id, TO_IMAGE_ID);
  assert.equal(applied.schema_contract, TO_SCHEMA.contract);
  assert.equal(applied.backup_commit_marker, "agentops_byoc_backup_bundle_v2");
  assert.equal(applied.active_run_preflight_passed, true);
  assert.equal(applied.configuration_preflight_passed, true);
  const stopIndex = driver.calls.findIndex(
    (call) => call.args.join(" ").includes(" stop control-plane"),
  );
  const backupIndex = driver.calls.findIndex(
    (call) => call.args[0]?.endsWith("/backup.sh"),
  );
  const migrationIndex = driver.calls.findIndex(
    (call) => call.args.join(" ").includes(" run --rm --no-deps migrate"),
  );
  assert.ok(stopIndex >= 0);
  assert.ok(stopIndex < backupIndex);
  assert.ok(backupIndex < migrationIndex);

  await expectFailure(
    () => runLifecycle(["rollback"], options),
    "lifecycle_rollback_confirmation_required",
  );
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      "byoc_lifecycle_00000000000000000000",
    ], options),
    "lifecycle_rollback_confirmation_required",
  );
  status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "applied");

  const rolledBack = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    planned.operation_id,
  ], options);
  assert.equal(rolledBack.phase, "rolled_back");
  assert.equal(rolledBack.image_id, FROM_IMAGE_ID);
  assert.equal(rolledBack.backup_restore_authoritative, true);
  assert.equal(rolledBack.down_migration_performed, false);
  assert.equal(rolledBack.explicit_confirmation_verified, true);
  assert.equal(rolledBack.quarantine_removed, true);
  assert.equal(rolledBack.quarantine_cleanup_pending, false);
  assert.ok(driver.state.sql.some((sql) => sql.startsWith("destructive:rename:")));

  status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "rolled_back");
  assert.equal(status.state.operation.rollback_authority, "backup_restore");
  assert.equal(status.state.operation.down_migration_performed, false);
  assert.equal(status.state.installation.image_id, FROM_IMAGE_ID);
  const stateText = await readFile(join(stateDirectory, "state.json"), "utf8");
  assert.doesNotMatch(stateText, /raw_secret_canary|postgres:\/\//);
  assert.equal((await stat(join(stateDirectory, "state.json"))).mode & 0o077, 0);
  assert.deepEqual(
    (await readdir(stateDirectory)).filter((name) => name.endsWith(".tmp")),
    [],
  );
}

async function proveApplyCompensation(root: string) {
  const stateDirectory = join(root, "apply-compensation-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  driver.state.failPostStopActiveRunCheck = true;
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_active_run_preflight_failed",
  );
  let status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, true);
  assert.equal(driver.state.currentImageId, FROM_IMAGE_ID);
  assert.equal(status.state.operation.recovery_required, false);

  driver.state.failPostStopActiveRunCheck = true;
  driver.state.failControlPlaneStart = true;
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_active_run_preflight_failed",
  );
  status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, false);
  assert.equal(status.state.operation.recovery_required, true);
}

async function provePostMigrationFailureStopsTarget(root: string) {
  const stateDirectory = join(root, "post-migration-failure-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  driver.state.failTargetReadiness = true;
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_schema_readiness_failed",
  );
  const status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, false);
  assert.equal(status.state.operation.phase, "backup_ready");
  assert.equal(status.state.operation.database_change_started, true);
  assert.equal(status.state.operation.recovery_required, true);
}

async function proveRollbackPreSwapCheckpointResume(root: string) {
  const stateDirectory = join(root, "rollback-pre-swap-checkpoint-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  await runLifecycle(["apply", "--plan-id", planned.operation_id], options);
  driver.state.failDatabaseTerminationOnce = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      planned.operation_id,
    ], options),
    "lifecycle_database_rename_failed",
  );
  let status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, false);
  assert.equal(driver.state.currentImageId, TO_IMAGE_ID);
  assert.equal(status.state.operation.phase, "applied");
  assert.equal(status.state.operation.recovery_required, true);
  assert.equal(
    status.state.operation.database_swap.phase,
    "production_rename_started",
  );
  const resumed = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    planned.operation_id,
  ], options);
  assert.equal(resumed.phase, "rolled_back");
  status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "rolled_back");
}

async function proveRollbackRenameCheckpointResume(root: string) {
  const stateDirectory = join(root, "rollback-rename-checkpoint-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  await runLifecycle(["apply", "--plan-id", planned.operation_id], options);
  driver.state.failRestorePromotionRenameOnce = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      planned.operation_id,
    ], options),
    "lifecycle_database_rename_failed",
  );
  let status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, false);
  assert.equal(driver.state.currentImageId, TO_IMAGE_ID);
  assert.equal(status.state.operation.phase, "applied");
  assert.equal(status.state.operation.recovery_required, true);
  assert.equal(
    status.state.operation.database_swap.phase,
    "restore_promotion_started",
  );
  assert.equal(driver.state.databases.has("agentops"), false);
  const resumed = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    planned.operation_id,
  ], options);
  assert.equal(resumed.phase, "rolled_back");
  status = await runLifecycle(["status"], options);
  assert.equal(driver.state.controlPlaneRunning, true);
  assert.equal(driver.state.currentImageId, FROM_IMAGE_ID);
  assert.equal(status.state.operation.phase, "rolled_back");
}

async function proveRollbackPostRenameCrashResume(root: string) {
  const productionStateDirectory = join(
    root,
    "rollback-production-post-rename-state",
  );
  const productionDriver = createFakeDriver();
  const productionOptions = await lifecycleOptions(
    root,
    productionStateDirectory,
    productionDriver,
  );
  const productionPlan = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    productionOptions,
  );
  await runLifecycle(
    ["apply", "--plan-id", productionPlan.operation_id],
    productionOptions,
  );
  productionDriver.state.failAfterProductionRenameOnce = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      productionPlan.operation_id,
    ], productionOptions),
    "lifecycle_database_rename_failed",
  );
  let status = await runLifecycle(["status"], productionOptions);
  assert.equal(
    status.state.operation.database_swap.phase,
    "production_rename_started",
  );
  assert.equal(productionDriver.state.databases.has("agentops"), false);
  assert.ok(
    [...productionDriver.state.databases].some((database) =>
      database.startsWith("agentops_quarantine_")),
  );
  const productionResumed = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    productionPlan.operation_id,
  ], productionOptions);
  assert.equal(productionResumed.phase, "rolled_back");

  const promotionStateDirectory = join(
    root,
    "rollback-promotion-post-rename-state",
  );
  const promotionDriver = createFakeDriver();
  const promotionOptions = await lifecycleOptions(
    root,
    promotionStateDirectory,
    promotionDriver,
  );
  const promotionPlan = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    promotionOptions,
  );
  await runLifecycle(
    ["apply", "--plan-id", promotionPlan.operation_id],
    promotionOptions,
  );
  promotionDriver.state.failAfterRestorePromotionRenameOnce = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      promotionPlan.operation_id,
    ], promotionOptions),
    "lifecycle_database_rename_failed",
  );
  status = await runLifecycle(["status"], promotionOptions);
  assert.equal(
    status.state.operation.database_swap.phase,
    "restore_promotion_started",
  );
  assert.equal(promotionDriver.state.databases.has("agentops"), true);
  assert.ok(
    [...promotionDriver.state.databases].some((database) =>
      database.startsWith("agentops_quarantine_")),
  );
  assert.equal(
    [...promotionDriver.state.databases].some((database) =>
      database.startsWith("agentops_restore_")),
    false,
  );
  const promotionResumed = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    promotionPlan.operation_id,
  ], promotionOptions);
  assert.equal(promotionResumed.phase, "rolled_back");
}

async function proveRestoreIntentOrphanRecovery(root: string) {
  const stateDirectory = join(root, "restore-intent-orphan-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    options,
  );
  await runLifecycle(
    ["apply", "--plan-id", planned.operation_id],
    options,
  );
  driver.state.failRestoreDrillAfterCreate = true;
  driver.state.failRestoreDatabaseDrop = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      planned.operation_id,
    ], options),
    "lifecycle_backup_restore_verification_failed",
  );
  let status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.database_swap.phase, "restore_intent");
  assert.equal(status.state.operation.recovery_required, true);
  assert.ok(
    [...driver.state.databases].some((database) =>
      database.startsWith("agentops_restore_")),
  );

  driver.state.failRestoreDatabaseDrop = false;
  const orphanRestore = [...driver.state.databases].find((database) =>
    database.startsWith("agentops_restore_"));
  assert.ok(orphanRestore);
  const restoreMarker = driver.state.databaseMarkers.get(orphanRestore);
  assert.ok(restoreMarker);
  driver.state.databaseMarkers.set(orphanRestore, "untrusted_restore_object");
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      planned.operation_id,
    ], options),
    "lifecycle_database_object_identity_changed",
  );
  driver.state.databaseMarkers.set(orphanRestore, restoreMarker);
  const resumed = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    planned.operation_id,
  ], options);
  assert.equal(resumed.phase, "rolled_back");
  status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.database_swap.phase, "cleanup_complete");
  assert.equal(status.state.operation.quarantine_cleanup_pending, false);
}

async function proveQuarantineCleanupIsRecoverable(root: string) {
  const stateDirectory = join(root, "quarantine-cleanup-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  await runLifecycle(["apply", "--plan-id", planned.operation_id], options);
  driver.state.failQuarantineDrop = true;
  const rolledBack = await runLifecycle([
    "rollback",
    "--confirm-restore-from-backup",
    planned.operation_id,
  ], options);
  assert.equal(rolledBack.phase, "rolled_back");
  assert.equal(rolledBack.quarantine_removed, false);
  assert.equal(rolledBack.quarantine_cleanup_pending, true);
  const status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "rolled_back");
  assert.equal(status.state.operation.quarantine_cleanup_pending, true);
  assert.match(
    status.state.operation.quarantine_database,
    /^agentops_quarantine_[0-9a-f]{12}$/,
  );
  await expectFailure(
    () => runLifecycle(["plan", "--to-image", TO_REFERENCE], options),
    "lifecycle_cleanup_required",
  );
  await writeLifecycleState(stateDirectory, {
    ...status.state,
    generation: status.state.generation + 1,
    operation: {
      ...status.state.operation,
      quarantine_cleanup_pending: false,
    },
  });
  await expectFailure(
    () => runLifecycle(["plan", "--to-image", TO_REFERENCE], options),
    "lifecycle_cleanup_required",
  );
  await writeLifecycleState(stateDirectory, {
    ...status.state,
    generation: status.state.generation + 2,
    operation: {
      ...status.state.operation,
      quarantine_cleanup_pending: false,
      database_swap: {
        ...status.state.operation.database_swap,
        phase: "cleanup_complete",
      },
    },
  });
  await expectFailure(
    () => runLifecycle(["plan", "--to-image", TO_REFERENCE], options),
    "lifecycle_cleanup_required",
  );
  await writeLifecycleState(stateDirectory, {
    ...status.state,
    generation: status.state.generation + 3,
  });
  await expectFailure(
    () => runLifecycle([
      "cleanup",
      "--confirm-operation-id",
      "byoc_lifecycle_00000000000000000000",
    ], options),
    "lifecycle_cleanup_confirmation_required",
  );
  driver.state.failQuarantineDrop = false;
  const quarantineDatabase = status.state.operation.quarantine_database;
  const quarantineOid = driver.state.databaseOids.get(quarantineDatabase);
  assert.ok(quarantineOid);
  driver.state.databaseOids.set(quarantineDatabase, "99999");
  await expectFailure(
    () => runLifecycle([
      "cleanup",
      "--confirm-operation-id",
      planned.operation_id,
    ], options),
    "lifecycle_database_object_identity_changed",
  );
  driver.state.databaseOids.set(quarantineDatabase, quarantineOid);
  const cleaned = await runLifecycle([
    "cleanup",
    "--confirm-operation-id",
    planned.operation_id,
  ], options);
  assert.equal(cleaned.phase, "rolled_back");
  assert.equal(cleaned.quarantine_removed, true);
  assert.equal(cleaned.quarantine_cleanup_pending, false);
  assert.equal(cleaned.cleanup_idempotent, false);
  const repeated = await runLifecycle([
    "cleanup",
    "--confirm-operation-id",
    planned.operation_id,
  ], options);
  assert.equal(repeated.cleanup_idempotent, true);
}

async function proveFailureDoesNotPromote(root: string) {
  const stateDirectory = join(root, "migration-failure-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  driver.state.failMigration = true;
  driver.state.failMigrationWithCanary = true;
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_target_migration_failed",
  );
  const status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "backup_ready");
  assert.equal(status.state.operation.database_change_started, true);
  assert.equal(status.state.operation.recovery_required, true);
  assert.equal(status.state.installation.image_id, FROM_IMAGE_ID);
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_rollback_required",
  );
  const serialized = JSON.stringify(status);
  assert.doesNotMatch(serialized, /raw_secret_canary|postgres:\/\//);
}

async function proveAuthorityDatabaseBinding(root: string) {
  const mismatchState = join(root, "authority-plan-mismatch-state");
  const mismatchDriver = createFakeDriver();
  const mismatchOptions = await lifecycleOptions(
    root,
    mismatchState,
    mismatchDriver,
  );
  mismatchDriver.state.runtimeDatabase = "other_authority";
  await expectFailure(
    () => runLifecycle(["plan", "--to-image", TO_REFERENCE], mismatchOptions),
    "lifecycle_authority_database_mismatch",
  );

  const driftState = join(root, "authority-apply-drift-state");
  const driftDriver = createFakeDriver();
  const driftOptions = await lifecycleOptions(root, driftState, driftDriver);
  const planned = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    driftOptions,
  );
  driftDriver.state.runtimeDatabase = "other_authority";
  await expectFailure(
    () => runLifecycle(
      ["apply", "--plan-id", planned.operation_id],
      driftOptions,
    ),
    "lifecycle_authority_database_mismatch",
  );
  const status = await runLifecycle(["status"], driftOptions);
  assert.equal(status.state.operation.phase, "planned");
  assert.equal(status.state.operation.backup, null);
}

async function provePostgresObjectIdentityBinding(root: string) {
  const clusterState = join(root, "postgres-cluster-drift-state");
  const clusterDriver = createFakeDriver();
  const clusterOptions = await lifecycleOptions(root, clusterState, clusterDriver);
  const clusterPlan = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    clusterOptions,
  );
  clusterDriver.state.clusterSystemIdentifier = "7390099999999999999";
  await expectFailure(
    () => runLifecycle(
      ["apply", "--plan-id", clusterPlan.operation_id],
      clusterOptions,
    ),
    "lifecycle_postgres_cluster_identity_changed",
  );

  const oidState = join(root, "authority-database-oid-drift-state");
  const oidDriver = createFakeDriver();
  const oidOptions = await lifecycleOptions(root, oidState, oidDriver);
  const oidPlan = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    oidOptions,
  );
  oidDriver.state.databaseOids.set("agentops", "99998");
  await expectFailure(
    () => runLifecycle(
      ["apply", "--plan-id", oidPlan.operation_id],
      oidOptions,
    ),
    "lifecycle_database_object_identity_changed",
  );

  const raceState = join(root, "destructive-database-identity-race-state");
  const raceDriver = createFakeDriver();
  const raceOptions = await lifecycleOptions(root, raceState, raceDriver);
  const racePlan = await runLifecycle(
    ["plan", "--to-image", TO_REFERENCE],
    raceOptions,
  );
  await runLifecycle(
    ["apply", "--plan-id", racePlan.operation_id],
    raceOptions,
  );
  raceDriver.state.replaceDatabaseBeforeDestructiveOnce = true;
  await expectFailure(
    () => runLifecycle([
      "rollback",
      "--confirm-restore-from-backup",
      racePlan.operation_id,
    ], raceOptions),
    "lifecycle_database_rename_failed",
  );
  assert.equal(raceDriver.state.databases.has("agentops"), true);
  assert.equal(
    [...raceDriver.state.databases].some((database) =>
      database.startsWith("agentops_quarantine_")),
    false,
  );
}

async function proveConfigurationDriftFailsClosed(root: string) {
  const stateDirectory = join(root, "configuration-drift-state");
  const driver = createFakeDriver();
  const options = await lifecycleOptions(root, stateDirectory, driver);
  const planned = await runLifecycle(["plan", "--to-image", TO_REFERENCE], options);
  driver.state.configVersion = "v2";
  await expectFailure(
    () => runLifecycle(["apply", "--plan-id", planned.operation_id], options),
    "lifecycle_configuration_changed",
  );
  const status = await runLifecycle(["status"], options);
  assert.equal(status.state.operation.phase, "planned");
  assert.equal(status.state.operation.backup, null);
}

const root = await mkdtemp(join(tmpdir(), "agentops-byoc-lifecycle-contract-"));
try {
  await chmod(root, 0o700);
  await proveClosedLoop(root);
  await proveApplyCompensation(root);
  await provePostMigrationFailureStopsTarget(root);
  await proveRollbackPreSwapCheckpointResume(root);
  await proveRollbackRenameCheckpointResume(root);
  await proveRollbackPostRenameCrashResume(root);
  await proveRestoreIntentOrphanRecovery(root);
  await proveQuarantineCleanupIsRecoverable(root);
  await proveFailureDoesNotPromote(root);
  await proveAuthorityDatabaseBinding(root);
  await provePostgresObjectIdentityBinding(root);
  await proveConfigurationDriftFailsClosed(root);
  console.log(JSON.stringify({
    contract: "agentops_byoc_retained_data_lifecycle_behavior_contract_v1",
    ok: true,
    executable_lifecycle_logic_exercised: true,
    plan_status_apply_rollback_verified: true,
    active_run_preflight_verified: true,
    configuration_preflight_verified: true,
    backup_before_migration_verified: true,
    atomic_state_verified: true,
    image_and_schema_bindings_verified: true,
    failure_state_not_promoted: true,
    pre_migration_compensation_verified: true,
    compensation_failure_marks_recovery_required: true,
    post_migration_failure_stops_target: true,
    rollback_pre_swap_checkpoint_resume_verified: true,
    rollback_rename_checkpoint_resume_verified: true,
    rollback_post_rename_crash_resume_verified: true,
    restore_intent_orphan_recovery_verified: true,
    authority_database_binding_verified: true,
    postgres_cluster_identity_binding_verified: true,
    database_oid_binding_verified: true,
    restore_operation_marker_binding_verified: true,
    destructive_cleanup_identity_guard_verified: true,
    destructive_ddl_toctou_guard_verified: true,
    authority_database_comment_ignored_safely: true,
    stopped_backup_order_verified: true,
    cleanup_failure_does_not_block_restart: true,
    quarantine_cleanup_pending_is_recoverable: true,
    pending_cleanup_blocks_new_plan: true,
    incomplete_cleanup_state_blocks_new_plan: true,
    explicit_cleanup_retry_verified: true,
    backup_restore_rollback_verified: true,
    explicit_rollback_confirmation_verified: true,
    in_place_down_migration_forbidden: true,
    docker_runtime_used: false,
    offline_driver_only: true,
    credentials_omitted: true,
  }));
} finally {
  await rm(root, { recursive: true, force: true });
}
