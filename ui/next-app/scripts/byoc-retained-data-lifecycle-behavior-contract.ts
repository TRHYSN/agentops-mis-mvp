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
    controlPlaneRunning: true,
    productionDatabase: "agentops",
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
    if (command === "docker" && args[0] === "compose") {
      if (joined.includes(" config --quiet")) return ok();
      if (joined.endsWith(" config")) return ok(`rendered-compose-${state.configVersion}\n`);
      if (joined.includes(" ps -q control-plane")) {
        return state.controlPlaneRunning ? ok("container-control-plane\n") : ok();
      }
      if (joined.includes(" exec -T control-plane npm run byoc:schema-identity")) {
        return ok(`${schemaIdentity(schemaFor(state.currentImageId))}\n`);
      }
      if (joined.includes(" exec -T postgres") && joined.includes("SELECT count(*)")) {
        return ok(`${state.activeRuns}\n`);
      }
      if (joined.includes(" exec -T postgres") && joined.includes("printf '%s'")) {
        return ok(state.productionDatabase);
      }
      if (joined.includes(" exec -T postgres") && joined.includes("--command \"$1\"")) {
        const sql = args.at(-1) || "";
        state.sql.push(sql);
        return ok();
      }
      if (joined.includes(" stop control-plane")) {
        state.controlPlaneRunning = false;
        return ok();
      }
      if (joined.includes(" up --detach")) {
        const id = imageIdFor(image);
        if (!id) return failed();
        state.currentReference = image;
        state.currentImageId = id;
        state.controlPlaneRunning = true;
        return ok();
      }
      if (joined.includes(" run --rm --no-deps migrate npm run check:postgres-schema")) {
        return ok(`${readiness(schemaFor(image))}\n`);
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
      assert.match(restoreDatabase, /^agentops_restore_[0-9a-f]{12}$/);
      state.restoreDatabases.add(restoreDatabase);
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
  assert.ok(driver.state.sql.some((sql) => sql.includes("ALTER DATABASE")));

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
  const serialized = JSON.stringify(status);
  assert.doesNotMatch(serialized, /raw_secret_canary|postgres:\/\//);
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
  await proveFailureDoesNotPromote(root);
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
