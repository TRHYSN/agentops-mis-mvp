#!/usr/bin/env node

import { randomBytes } from "node:crypto";
import { lstat, readFile, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  LIFECYCLE_STATE_CONTRACT,
  lifecycleLockStatus,
  lifecycleStateDirectory,
  readLifecycleState,
  recoverStaleLifecycleLock,
  registerLifecycleChildProcess,
  releaseLifecycleChildProcess,
  sha256,
  sha256File,
  withLifecycleLock,
  writeLifecycleState,
} from "./retained-data-lifecycle-state.mjs";

const RECEIPT_CONTRACT = "agentops_byoc_retained_data_lifecycle_v1";
const SCHEMA_IDENTITY_CONTRACT = "agentops_byoc_schema_identity_v1";
const DATABASE_IDENTITY_CONTRACT = "agentops_byoc_database_identity_v1";
const SCHEMA_READINESS_CONTRACT = "agentops_postgres_schema_readiness_v1";
const IMAGE_DIGEST_REFERENCE = /@sha256:[0-9a-f]{64}$/;
const IMAGE_ID = /^sha256:[0-9a-f]{64}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const SAFE_DATABASE_IDENTIFIER = /^[a-z][a-z0-9_]{0,62}$/;
const OPERATION_ID = /^byoc_lifecycle_[0-9a-f]{20}$/;
const POSTGRES_SYSTEM_IDENTIFIER = /^[1-9][0-9]{9,24}$/;
const POSTGRES_DATABASE_OID = /^[1-9][0-9]{0,9}$/;
const RESTORE_DATABASE_MARKER =
  /^agentops_byoc_restore_v1:byoc_lifecycle_[0-9a-f]{20}:[0-9a-f]{64}$/;
const DATABASE_OPERATION_ADVISORY_LOCK_KEY = "7157544864185932631";
const TERMINAL_PHASES = new Set(["applied", "rolled_back"]);
const DATABASE_SWAP_PHASES = new Set([
  "restore_intent",
  "restore_verified",
  "production_rename_started",
  "production_quarantined",
  "restore_promotion_started",
  "restore_promoted",
  "rollback_verified",
  "cleanup_complete",
]);
const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const defaultRepositoryRoot = resolve(moduleDirectory, "../..");

export class LifecycleError extends Error {
  constructor(code, stage = "unknown") {
    super(code);
    this.name = "LifecycleError";
    this.code = code;
    this.stage = stage;
  }
}

function errorCode(error) {
  if (error instanceof LifecycleError) return error.code;
  const message = String(error?.message || "");
  return /^[a-z0-9_]+$/.test(message)
    ? message
    : "lifecycle_operation_failed";
}

function asLifecycleError(error, stage) {
  if (error instanceof LifecycleError) return error;
  return new LifecycleError(errorCode(error), stage);
}

function terminateChildProcessGroup(child) {
  if (!child.pid) return;
  try {
    if (process.platform === "win32") child.kill("SIGKILL");
    else process.kill(-child.pid, "SIGKILL");
  } catch {
    child.kill("SIGKILL");
  }
}

async function defaultRunner(command, args, options = {}) {
  const detached = process.platform !== "win32";
  const trackLifecycleLease = options.trackLifecycleLease !== false;
  const gated = process.platform !== "win32" && trackLifecycleLease;
  const child = spawn(
    gated ? "/bin/sh" : command,
    gated
      ? [
          "-ceu",
          "IFS= read -r gate; [ \"$gate\" = agentops_child_lease_ready_v1 ]; exec \"$@\"",
          "agentops-lifecycle-child",
          command,
          ...args,
        ]
      : args,
    {
    cwd: options.cwd,
    env: options.env,
    detached,
    stdio: ["pipe", "pipe", "pipe"],
    },
  );
  let stdout = "";
  let stderr = "";
  let overflow = false;
  const maxBuffer = 8 * 1024 * 1024;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    if (stdout.length + chunk.length > maxBuffer) {
      overflow = true;
      terminateChildProcessGroup(child);
      return;
    }
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    if (stderr.length + chunk.length > maxBuffer) {
      overflow = true;
      terminateChildProcessGroup(child);
      return;
    }
    stderr += chunk;
  });
  const completion = new Promise((resolveCompletion) => {
    let resolved = false;
    const finish = (status, error = null) => {
      if (resolved) return;
      resolved = true;
      resolveCompletion({ status, error });
    };
    child.once("error", (error) => finish(127, error));
    child.once("close", (code) => finish(code ?? 1));
  });
  let lease = null;
  if (trackLifecycleLease) {
    try {
      lease = await registerLifecycleChildProcess(
        options.stateDirectory,
        child.pid,
        detached ? child.pid : child.pid,
      );
    } catch {
      terminateChildProcessGroup(child);
      await completion;
      return {
        status: 127,
        stdout: "",
        stderr: "",
      };
    }
  }
  const timeout = setTimeout(() => {
    terminateChildProcessGroup(child);
  }, options.timeout || 15 * 60 * 1000);
  child.stdin.on("error", () => undefined);
  if (gated) child.stdin.write("agentops_child_lease_ready_v1\n");
  if (options.input !== undefined) child.stdin.end(options.input);
  else child.stdin.end();
  const completed = await completion;
  clearTimeout(timeout);
  if (lease !== null) {
    try {
      await releaseLifecycleChildProcess(options.stateDirectory, lease);
    } catch {
      return { status: 127, stdout: "", stderr: "" };
    }
  }
  return {
    status: overflow ? 1 : completed.status,
    stdout,
    stderr,
  };
}

function checked(result, code, stage) {
  if (result.status !== 0) throw new LifecycleError(code, stage);
  return result.stdout.trim();
}

function receipt(operation, fields = {}) {
  return {
    contract: RECEIPT_CONTRACT,
    ok: true,
    operation,
    ...fields,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  };
}

function failureReceipt(operation, error) {
  const failure = asLifecycleError(error, "unknown");
  return {
    contract: RECEIPT_CONTRACT,
    ok: false,
    operation,
    error_code: failure.code,
    failure_stage: failure.stage,
    state_promoted: false,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  };
}

function event(stage, outcome, now) {
  return { stage, outcome, recorded_at: now().toISOString() };
}

function appendEvent(operation, stage, outcome, now) {
  return {
    ...operation,
    events: [...operation.events, event(stage, outcome, now)],
  };
}

function rollbackCleanupComplete(operation) {
  return operation?.phase === "rolled_back"
    && operation.quarantine_cleanup_pending === false
    && typeof operation.quarantine_removed_at === "string"
    && operation.quarantine_removed_at.length > 0
    && operation.database_swap?.phase === "cleanup_complete"
    && typeof operation.database_swap.updated_at === "string"
    && operation.database_swap.updated_at.length > 0;
}

function parseArguments(arguments_) {
  const [command, ...tokens] = arguments_;
  if (!new Set([
    "plan",
    "status",
    "apply",
    "rollback",
    "cleanup",
    "recover-lock",
  ]).has(command)) {
    throw new LifecycleError("lifecycle_command_invalid", "arguments");
  }
  const values = {};
  for (let index = 0; index < tokens.length; index += 2) {
    const key = tokens[index];
    const value = tokens[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new LifecycleError("lifecycle_arguments_invalid", "arguments");
    }
    if (Object.hasOwn(values, key)) {
      throw new LifecycleError("lifecycle_arguments_invalid", "arguments");
    }
    values[key] = value;
  }
  const allowed = command === "plan"
    ? new Set(["--to-image"])
    : command === "apply"
      ? new Set(["--plan-id"])
      : command === "rollback"
        ? new Set(["--confirm-restore-from-backup"])
        : command === "cleanup"
          ? new Set(["--confirm-operation-id"])
          : command === "recover-lock"
            ? new Set(["--confirm-operation-id"])
            : new Set();
  if (Object.keys(values).some((key) => !allowed.has(key))) {
    throw new LifecycleError("lifecycle_arguments_invalid", "arguments");
  }
  return { command, values };
}

function jsonReceipt(output, expectedContract, code, stage) {
  const lines = output.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (const line of lines.reverse()) {
    try {
      const parsed = JSON.parse(line);
      if (parsed?.contract === expectedContract && parsed?.ok === true) {
        return parsed;
      }
    } catch {
      // Non-JSON package-manager preamble is ignored.
    }
  }
  throw new LifecycleError(code, stage);
}

function assertSchemaIdentity(identity, stage) {
  if (
    identity.contract !== SCHEMA_IDENTITY_CONTRACT
    || identity.ok !== true
    || typeof identity.schema_contract !== "string"
    || typeof identity.schema_fingerprint_contract !== "string"
    || !SHA256.test(String(identity.schema_fingerprint_sha256 || ""))
    || !SHA256.test(String(identity.migration_manifest_sha256 || ""))
    || !Number.isSafeInteger(identity.schema_object_count)
    || !Number.isSafeInteger(identity.migration_count)
    || identity.static_manifest_only !== true
    || identity.database_contacted !== false
  ) {
    throw new LifecycleError("lifecycle_schema_identity_invalid", stage);
  }
  return {
    contract: identity.schema_contract,
    fingerprint_contract: identity.schema_fingerprint_contract,
    fingerprint_sha256: identity.schema_fingerprint_sha256,
    object_count: identity.schema_object_count,
    migration_manifest_sha256: identity.migration_manifest_sha256,
    migration_count: identity.migration_count,
  };
}

function sameSchema(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function composeArguments(context, tail) {
  return [
    "compose",
    "--env-file",
    context.envFile,
    "-f",
    context.composeFile,
    ...tail,
  ];
}

function commandEnvironment(context, image) {
  return {
    ...context.environment,
    ...(image ? { AGENTOPS_IMAGE: image } : {}),
  };
}

async function regularFile(path, code, stage) {
  let metadata;
  try {
    metadata = await lstat(path);
  } catch {
    throw new LifecycleError(code, stage);
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new LifecycleError(code, stage);
  }
}

async function configurationSnapshot(context) {
  const stage = "configuration_preflight";
  await regularFile(
    context.composeFile,
    "lifecycle_compose_file_invalid",
    stage,
  );
  await regularFile(context.envFile, "lifecycle_env_file_invalid", stage);
  checked(
    await context.runner(
      "docker",
      composeArguments(context, ["config", "--quiet"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_compose_configuration_invalid",
    stage,
  );
  const rendered = checked(
    await context.runner(
      "docker",
      composeArguments(context, ["config"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_compose_configuration_invalid",
    stage,
  );
  return {
    compose_file_sha256: await sha256File(context.composeFile),
    env_file_sha256: await sha256File(context.envFile),
    rendered_compose_sha256: sha256(rendered),
    compose_validated: true,
    secret_values_omitted: true,
  };
}

async function imageId(context, reference, stage) {
  const output = checked(
    await context.runner(
      "docker",
      ["image", "inspect", "--format", "{{.Id}}", reference],
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_image_unavailable",
    stage,
  );
  if (!IMAGE_ID.test(output)) {
    throw new LifecycleError("lifecycle_image_identity_invalid", stage);
  }
  return output;
}

async function runningInstallation(context) {
  const stage = "running_installation_preflight";
  const container = checked(
    await context.runner(
      "docker",
      composeArguments(context, ["ps", "-q", "control-plane"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_control_plane_not_running",
    stage,
  );
  if (!/^[A-Za-z0-9_.-]+$/.test(container)) {
    throw new LifecycleError("lifecycle_control_plane_identity_invalid", stage);
  }
  const inspected = checked(
    await context.runner(
      "docker",
      ["inspect", "--format", "{{.Config.Image}}\t{{.Image}}", container],
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_control_plane_inspect_failed",
    stage,
  );
  const [reference, id] = inspected.split("\t");
  if (!reference || !IMAGE_ID.test(String(id || ""))) {
    throw new LifecycleError("lifecycle_control_plane_identity_invalid", stage);
  }
  return { reference, image_id: id };
}

async function runningSchemaIdentity(context) {
  const stage = "running_schema_identity";
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "control-plane",
        "npm",
        "run",
        "byoc:schema-identity",
        "--silent",
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_running_schema_identity_unavailable",
    stage,
  );
  return assertSchemaIdentity(
    jsonReceipt(
      output,
      SCHEMA_IDENTITY_CONTRACT,
      "lifecycle_running_schema_identity_invalid",
      stage,
    ),
    stage,
  );
}

async function runningAuthorityDatabase(context) {
  const stage = "running_database_identity";
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "control-plane",
        "node",
        "/usr/local/lib/agentops/node-secret-entrypoint.mjs",
        "--postgres-runtime",
        "--",
        "npm",
        "run",
        "byoc:database-identity",
        "--silent",
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_running_database_identity_unavailable",
    stage,
  );
  const parsed = jsonReceipt(
    output,
    DATABASE_IDENTITY_CONTRACT,
    "lifecycle_running_database_identity_invalid",
    stage,
  );
  const database = String(parsed.authority_database || "");
  if (
    !SAFE_DATABASE_IDENTIFIER.test(database)
    || database === "postgres"
    || parsed.runtime_role_verified !== true
    || parsed.database_contacted !== true
  ) {
    throw new LifecycleError(
      "lifecycle_running_database_identity_invalid",
      stage,
    );
  }
  return database;
}

async function imageSchemaIdentity(context, image, stage) {
  const output = checked(
    await context.runner(
      "docker",
      [
        "run",
        "--rm",
        "--entrypoint",
        "npm",
        image,
        "run",
        "byoc:schema-identity",
        "--silent",
      ],
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_target_schema_identity_unavailable",
    stage,
  );
  return assertSchemaIdentity(
    jsonReceipt(
      output,
      SCHEMA_IDENTITY_CONTRACT,
      "lifecycle_target_schema_identity_invalid",
      stage,
    ),
    stage,
  );
}

async function schemaReadiness(context, image, expected, operation) {
  const stage = `${operation}_schema_readiness`;
  const expectedImageId = await imageId(context, image, stage);
  const running = await runningInstallation(context);
  if (running.image_id !== expectedImageId) {
    throw new LifecycleError("lifecycle_running_image_mismatch", stage);
  }
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "control-plane",
        "npm",
        "run",
        "check:postgres-schema",
        "--silent",
      ]),
      {
        cwd: context.repositoryRoot,
        env: commandEnvironment(context),
      },
    ),
    "lifecycle_schema_readiness_failed",
    stage,
  );
  const parsed = jsonReceipt(
    output,
    SCHEMA_READINESS_CONTRACT,
    "lifecycle_schema_readiness_receipt_invalid",
    stage,
  );
  if (
    parsed.schema_contract !== expected.contract
    || parsed.schema_fingerprint_contract !== expected.fingerprint_contract
    || parsed.schema_fingerprint_verified !== true
    || parsed.schema_object_count !== expected.object_count
    || parsed.database_role_boundary_verified !== true
  ) {
    throw new LifecycleError("lifecycle_schema_readiness_mismatch", stage);
  }
}

async function activeRunCount(context) {
  const stage = "active_run_preflight";
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ceu",
        "psql --username \"$POSTGRES_USER\" --dbname \"$POSTGRES_DB\" --no-psqlrc --tuples-only --no-align --command \"SELECT count(*) FROM runs WHERE status IN ('running','waiting_approval')\"",
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_active_run_preflight_failed",
    stage,
  );
  if (!/^[0-9]+$/.test(output)) {
    throw new LifecycleError("lifecycle_active_run_receipt_invalid", stage);
  }
  return Number(output);
}

async function assertNoActiveRuns(context) {
  const count = await activeRunCount(context);
  if (count !== 0) {
    throw new LifecycleError("lifecycle_active_runs_must_be_drained", "active_run_preflight");
  }
  return count;
}

async function runBackup(context, output) {
  checked(
    await context.runner(
      "/bin/sh",
      [join(context.repositoryRoot, "deploy/byoc/backup.sh"), output],
      {
        cwd: context.repositoryRoot,
        env: {
          ...commandEnvironment(context),
          AGENTOPS_BYOC_COMPOSE_FILE: context.composeFile,
          AGENTOPS_BYOC_ENV_FILE: context.envFile,
        },
      },
    ),
    "lifecycle_backup_failed",
    "backup",
  );
}

async function validateBackupBundle(bundle) {
  const stage = "backup_validation";
  for (const name of ["database.dump", "SHA256SUMS", "COMMITTED"]) {
    await regularFile(join(bundle, name), "lifecycle_backup_bundle_invalid", stage);
  }
  const commit = (await readFile(join(bundle, "COMMITTED"), "utf8")).trim();
  if (commit !== "agentops_byoc_backup_bundle_v2") {
    throw new LifecycleError("lifecycle_backup_uncommitted", stage);
  }
  const checksumLine = (await readFile(join(bundle, "SHA256SUMS"), "utf8")).trim();
  const match = checksumLine.match(/^([0-9a-fA-F]{64})  database\.dump$/);
  if (!match) throw new LifecycleError("lifecycle_backup_checksum_invalid", stage);
  const actual = await sha256File(join(bundle, "database.dump"));
  if (actual !== match[1].toLowerCase()) {
    throw new LifecycleError("lifecycle_backup_checksum_mismatch", stage);
  }
  return {
    contract: "agentops_byoc_backup_bundle_v2",
    path: bundle,
    database_dump_sha256: actual,
    commit_marker: "agentops_byoc_backup_bundle_v2",
  };
}

function plannedBackupPath(context, operation) {
  const expected = join(
    context.stateDirectory,
    "backups",
    `${operation.operation_id}.bundle`,
  );
  if (operation.backup && resolve(operation.backup.path) !== resolve(expected)) {
    throw new LifecycleError("lifecycle_backup_path_binding_invalid", "backup_validation");
  }
  return expected;
}

async function stopControlPlane(context) {
  checked(
    await context.runner(
      "docker",
      composeArguments(context, ["stop", "control-plane"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_control_plane_stop_failed",
    "control_plane_stop",
  );
}

async function startControlPlane(context, image, stage) {
  checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "up",
        "--detach",
        "--no-deps",
        "--no-build",
        "--force-recreate",
        "control-plane",
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context, image) },
    ),
    "lifecycle_control_plane_start_failed",
    stage,
  );
}

async function waitForHealth(context, image, stage) {
  const timeout = Number(
    context.environment.AGENTOPS_BYOC_LIFECYCLE_HEALTH_TIMEOUT_SEC || 120,
  );
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 900) {
    throw new LifecycleError("lifecycle_health_timeout_invalid", stage);
  }
  for (let attempt = 0; attempt < timeout; attempt += 1) {
    const container = await context.runner(
      "docker",
      composeArguments(context, ["ps", "-q", "control-plane"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context, image) },
    );
    if (container.status === 0 && container.stdout.trim()) {
      const health = await context.runner(
        "docker",
        [
          "inspect",
          "--format",
          "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
          container.stdout.trim(),
        ],
        { cwd: context.repositoryRoot, env: commandEnvironment(context, image) },
      );
      if (health.status === 0 && health.stdout.trim() === "healthy") return;
    }
    await context.wait(1000);
  }
  throw new LifecycleError("lifecycle_control_plane_health_failed", stage);
}

async function migrateTarget(context, image) {
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, ["run", "--rm", "--no-deps", "migrate"]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context, image) },
    ),
    "lifecycle_target_migration_failed",
    "target_migration",
  );
  jsonReceipt(
    output,
    SCHEMA_READINESS_CONTRACT,
    "lifecycle_target_migration_receipt_invalid",
    "target_migration",
  );
}

async function productionDatabase(context) {
  const output = checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ceu",
        "printf '%s' \"$POSTGRES_DB\"",
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    "lifecycle_production_database_unknown",
    "rollback_database_preflight",
  );
  if (!SAFE_DATABASE_IDENTIFIER.test(output) || output === "postgres") {
    throw new LifecycleError(
      "lifecycle_production_database_invalid",
      "rollback_database_preflight",
    );
  }
  return output;
}

async function boundAuthorityDatabase(context) {
  const [serviceDatabase, runtimeDatabase] = await Promise.all([
    productionDatabase(context),
    runningAuthorityDatabase(context),
  ]);
  if (serviceDatabase !== runtimeDatabase) {
    throw new LifecycleError(
      "lifecycle_authority_database_mismatch",
      "database_identity_preflight",
    );
  }
  return runtimeDatabase;
}

async function assertBoundAuthorityDatabase(context, expected) {
  if (
    !SAFE_DATABASE_IDENTIFIER.test(String(expected || ""))
    || expected === "postgres"
    || await boundAuthorityDatabase(context) !== expected
  ) {
    throw new LifecycleError(
      "lifecycle_authority_database_changed",
      "database_identity_preflight",
    );
  }
}

function quoteDatabaseIdentifier(value) {
  if (!SAFE_DATABASE_IDENTIFIER.test(value)) {
    throw new LifecycleError("lifecycle_database_identifier_invalid", "database_swap");
  }
  return `"${value}"`;
}

function quoteDatabaseLiteral(value) {
  if (!SAFE_DATABASE_IDENTIFIER.test(value)) {
    throw new LifecycleError("lifecycle_database_identifier_invalid", "database_swap");
  }
  return `'${value}'`;
}

async function postgresQuery(context, sql, code, stage) {
  return checked(
    await context.runner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ceu",
        "psql --username \"$POSTGRES_USER\" --dbname postgres --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align --command \"$1\"",
        "sh",
        sql,
      ]),
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    code,
    stage,
  );
}

async function postgresClusterSystemIdentifier(context, stage) {
  const identifier = await postgresQuery(
    context,
    "SELECT system_identifier::text FROM pg_control_system()",
    "lifecycle_postgres_cluster_identity_failed",
    stage,
  );
  if (!POSTGRES_SYSTEM_IDENTIFIER.test(identifier)) {
    throw new LifecycleError(
      "lifecycle_postgres_cluster_identity_invalid",
      stage,
    );
  }
  return identifier;
}

async function assertPostgresClusterIdentity(context, expected, stage) {
  if (
    !POSTGRES_SYSTEM_IDENTIFIER.test(String(expected || ""))
    || await postgresClusterSystemIdentifier(context, stage) !== expected
  ) {
    throw new LifecycleError(
      "lifecycle_postgres_cluster_identity_changed",
      stage,
    );
  }
}

async function databaseObjectIdentity(context, database, stage) {
  const output = await postgresQuery(
    context,
    `SELECT oid::text || '|' || COALESCE(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname=${quoteDatabaseLiteral(database)}`,
    "lifecycle_database_object_identity_failed",
    stage,
  );
  if (!output) return null;
  const lines = output.split(/\r?\n/).filter(Boolean);
  if (lines.length !== 1) {
    throw new LifecycleError("lifecycle_database_object_identity_invalid", stage);
  }
  const separator = lines[0].indexOf("|");
  const oid = separator === -1 ? "" : lines[0].slice(0, separator);
  const marker = separator === -1 ? "" : lines[0].slice(separator + 1);
  if (!POSTGRES_DATABASE_OID.test(oid)) {
    throw new LifecycleError("lifecycle_database_object_identity_invalid", stage);
  }
  return { oid, marker };
}

async function assertDatabaseObjectIdentity(
  context,
  database,
  expectedOid,
  expectedMarker,
  stage,
) {
  const identity = await databaseObjectIdentity(context, database, stage);
  if (
    !identity
    || (expectedOid !== null && (
      !POSTGRES_DATABASE_OID.test(String(expectedOid || ""))
      || identity.oid !== expectedOid
    ))
    || (expectedMarker !== null && (
      !RESTORE_DATABASE_MARKER.test(String(expectedMarker || ""))
      || identity.marker !== expectedMarker
    ))
  ) {
    throw new LifecycleError("lifecycle_database_object_identity_changed", stage);
  }
  return identity;
}

async function databasePresence(context, databases) {
  const expected = new Set(databases);
  const output = await postgresQuery(
    context,
    `SELECT datname FROM pg_database WHERE datname IN (${
      databases.map(quoteDatabaseLiteral).join(",")
    }) ORDER BY datname`,
    "lifecycle_database_presence_failed",
    "database_swap_recovery",
  );
  const present = new Set(
    output.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
  );
  if ([...present].some((database) => !expected.has(database))) {
    throw new LifecycleError(
      "lifecycle_database_presence_invalid",
      "database_swap_recovery",
    );
  }
  return present;
}

async function destructiveDatabaseOperation(
  context,
  database,
  targetDatabase,
  expectedOid,
  expectedMarker,
  expectedClusterIdentifier,
  operation,
  code,
  stage,
) {
  if (
    !POSTGRES_DATABASE_OID.test(String(expectedOid || ""))
    || !POSTGRES_SYSTEM_IDENTIFIER.test(String(expectedClusterIdentifier || ""))
    || (
      expectedMarker !== null
      && !RESTORE_DATABASE_MARKER.test(String(expectedMarker || ""))
    )
  ) {
    throw new LifecycleError(
      "lifecycle_database_destructive_identity_invalid",
      stage,
    );
  }
  checked(
    await context.runner(
      "/bin/sh",
      [
        join(
          context.repositoryRoot,
          "deploy/byoc/postgres-destructive-database.sh",
        ),
        context.composeFile,
        context.envFile,
        operation,
        database,
        targetDatabase,
        expectedOid,
        expectedClusterIdentifier,
        expectedMarker === null ? "ignore" : "exact",
        expectedMarker ?? "",
      ],
      { cwd: context.repositoryRoot, env: commandEnvironment(context) },
    ),
    code,
    stage,
  );
}

async function renameDatabase(
  context,
  from,
  to,
  expectedOid,
  expectedMarker,
  expectedClusterIdentifier,
  stage = "database_swap",
) {
  await destructiveDatabaseOperation(
    context,
    from,
    to,
    expectedOid,
    expectedMarker,
    expectedClusterIdentifier,
    "rename",
    "lifecycle_database_rename_failed",
    stage,
  );
}

async function dropBoundDatabase(
  context,
  database,
  expectedOid,
  expectedMarker,
  expectedClusterIdentifier,
  stage = "database_cleanup",
) {
  const identity = await assertDatabaseObjectIdentity(
    context,
    database,
    expectedOid,
    expectedMarker,
    stage,
  );
  await destructiveDatabaseOperation(
    context,
    database,
    "-",
    expectedOid ?? identity.oid,
    expectedMarker,
    expectedClusterIdentifier,
    "drop",
    "lifecycle_database_drop_failed",
    stage,
  );
}

async function dropBoundDatabaseIfPresent(
  context,
  database,
  expectedOid,
  expectedMarker,
  expectedClusterIdentifier,
  stage = "database_cleanup",
) {
  const identity = await databaseObjectIdentity(context, database, stage);
  if (!identity) return false;
  await dropBoundDatabase(
    context,
    database,
    expectedOid,
    expectedMarker,
    expectedClusterIdentifier,
    stage,
  );
  return true;
}

async function runRestoreDrill(
  context,
  bundle,
  image,
  restoreDatabase,
  restoreMarker,
) {
  checked(
    await context.runner(
      "/bin/sh",
      [join(context.repositoryRoot, "deploy/byoc/restore-drill.sh"), bundle],
      {
        cwd: context.repositoryRoot,
        env: {
          ...commandEnvironment(context, image),
          AGENTOPS_BYOC_COMPOSE_FILE: context.composeFile,
          AGENTOPS_BYOC_ENV_FILE: context.envFile,
          AGENTOPS_RESTORE_DATABASE: restoreDatabase,
          AGENTOPS_RESTORE_KEEP: "true",
          AGENTOPS_RESTORE_OPERATION_MARKER: restoreMarker,
        },
      },
    ),
    "lifecycle_backup_restore_verification_failed",
    "rollback_restore_verification",
  );
}

function validatedDatabaseSwap(operation) {
  const swap = operation.database_swap;
  if (swap === null || swap === undefined) return null;
  if (
    typeof swap !== "object"
    || swap.authority_database !== operation.authority_database
    || !SAFE_DATABASE_IDENTIFIER.test(String(swap.authority_database || ""))
    || !SAFE_DATABASE_IDENTIFIER.test(String(swap.restore_database || ""))
    || !SAFE_DATABASE_IDENTIFIER.test(String(swap.quarantine_database || ""))
    || swap.cluster_system_identifier
      !== operation.postgres_cluster_system_identifier
    || swap.authority_database_oid !== operation.authority_database_oid
    || !RESTORE_DATABASE_MARKER.test(String(swap.restore_database_marker || ""))
    || (
      swap.restore_database_oid !== null
      && !POSTGRES_DATABASE_OID.test(String(swap.restore_database_oid || ""))
    )
    || (
      swap.phase !== "restore_intent"
      && !POSTGRES_DATABASE_OID.test(String(swap.restore_database_oid || ""))
    )
    || !DATABASE_SWAP_PHASES.has(String(swap.phase || ""))
  ) {
    throw new LifecycleError(
      "lifecycle_database_swap_checkpoint_invalid",
      "database_swap_recovery",
    );
  }
  return swap;
}

async function recordDatabaseSwapPhase(context, state, swap, phase) {
  const recordedAt = context.now().toISOString();
  const next = {
    ...state,
    generation: state.generation + 1,
    operation: appendEvent({
      ...state.operation,
      database_swap: {
        ...swap,
        phase,
        updated_at: recordedAt,
      },
      recovery_required: true,
      last_failure: null,
    }, `database_swap_${phase}`, "checkpointed", context.now),
    updated_at: recordedAt,
  };
  await writeLifecycleState(context.stateDirectory, next);
  return next;
}

function installation(image, schema, verifiedAt) {
  return {
    image_reference: image.reference,
    image_id: image.image_id,
    schema,
    verified_at: verifiedAt,
  };
}

function archivedOperation(operation) {
  return {
    operation_id: operation.operation_id,
    phase: operation.phase,
    from_image_id: operation.from.image_id,
    to_image_id: operation.to.image_id,
    completed_at: operation.completed_at || null,
    backup_commit_marker: operation.backup?.commit_marker || null,
  };
}

async function recordFailure(context, state, error, recoveryRequired) {
  if (!state?.operation) return state;
  const failure = asLifecycleError(error, "unknown");
  const next = {
    ...state,
    generation: state.generation + 1,
    operation: appendEvent({
      ...state.operation,
      last_failure: {
        error_code: failure.code,
        failure_stage: failure.stage,
        recorded_at: context.now().toISOString(),
      },
      recovery_required: recoveryRequired,
    }, failure.stage, "failed", context.now),
  };
  await writeLifecycleState(context.stateDirectory, next);
  return next;
}

async function plan(context, values) {
  const toReference = String(values["--to-image"] || "").trim();
  if (!IMAGE_DIGEST_REFERENCE.test(toReference)) {
    throw new LifecycleError("lifecycle_target_image_digest_required", "arguments");
  }
  return withLifecycleLock(context.stateDirectory, async () => {
    const existing = await readLifecycleState(context.stateDirectory);
    if (
      existing?.operation?.phase === "rolled_back"
      && !rollbackCleanupComplete(existing.operation)
    ) {
      throw new LifecycleError("lifecycle_cleanup_required", "plan");
    }
    if (existing?.operation && !TERMINAL_PHASES.has(existing.operation.phase)) {
      throw new LifecycleError("lifecycle_operation_already_active", "plan");
    }
    const configuration = await configurationSnapshot(context);
    const currentImage = await runningInstallation(context);
    const currentSchema = await runningSchemaIdentity(context);
    const authorityDatabase = await boundAuthorityDatabase(context);
    const postgresClusterIdentity = await postgresClusterSystemIdentifier(
      context,
      "plan_database_identity",
    );
    const authorityDatabaseIdentity = await databaseObjectIdentity(
      context,
      authorityDatabase,
      "plan_database_identity",
    );
    if (!authorityDatabaseIdentity) {
      throw new LifecycleError(
        "lifecycle_authority_database_changed",
        "plan_database_identity",
      );
    }
    await schemaReadiness(
      context,
      currentImage.image_id,
      currentSchema,
      "plan_current",
    );
    const toImageId = await imageId(context, toReference, "target_image_preflight");
    if (toImageId === currentImage.image_id) {
      throw new LifecycleError("lifecycle_target_image_unchanged", "target_image_preflight");
    }
    const targetSchema = await imageSchemaIdentity(
      context,
      toReference,
      "target_schema_identity",
    );
    const activeRuns = await activeRunCount(context);
    const operationId = `byoc_lifecycle_${sha256(JSON.stringify({
      from: currentImage.image_id,
      to: toImageId,
      schema: targetSchema,
      nonce: context.randomHex(),
    })).slice(0, 20)}`;
    const createdAt = context.now().toISOString();
    const operation = {
      operation_id: operationId,
      phase: "planned",
      created_at: createdAt,
      completed_at: null,
      from: installation(currentImage, currentSchema, createdAt),
      to: installation(
        { reference: toReference, image_id: toImageId },
        targetSchema,
        null,
      ),
      configuration,
      authority_database: authorityDatabase,
      authority_database_oid: authorityDatabaseIdentity.oid,
      postgres_cluster_system_identifier: postgresClusterIdentity,
      plan_preflight: {
        active_run_count: activeRuns,
        apply_blocked: activeRuns !== 0,
        current_schema_verified: true,
        target_image_present: true,
      },
      backup: null,
      database_swap: null,
      database_change_started: false,
      recovery_required: false,
      last_failure: null,
      events: [event("plan", "succeeded", context.now)],
    };
    const state = {
      contract: LIFECYCLE_STATE_CONTRACT,
      generation: (existing?.generation || 0) + 1,
      installation: operation.from,
      operation,
      history: existing
        ? [
            ...existing.history,
            ...(existing.operation ? [archivedOperation(existing.operation)] : []),
          ].slice(-50)
        : [],
      updated_at: createdAt,
    };
    await writeLifecycleState(context.stateDirectory, state);
    return receipt("plan", {
      operation_id: operationId,
      phase: operation.phase,
      from_image_reference: currentImage.reference,
      from_image_id: currentImage.image_id,
      to_image_reference: toReference,
      to_image_id: toImageId,
      from_schema_contract: currentSchema.contract,
      to_schema_contract: targetSchema.contract,
      active_run_count: activeRuns,
      apply_blocked: activeRuns !== 0,
      authority_database_bound: true,
      authority_database_oid_bound: true,
      postgres_cluster_identity_bound: true,
      state_generation: state.generation,
    });
  });
}

async function status(context) {
  const state = await readLifecycleState(context.stateDirectory);
  return receipt("status", {
    state_present: state !== null,
    operation_locked: await lifecycleLockStatus(context.stateDirectory),
    state,
  });
}

async function assertDatabaseLifecycleLeaseReleased(context) {
  const stage = "lock_recovery";
  const output = checked(
    await context.recoveryRunner(
      "docker",
      composeArguments(context, [
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ceu",
        `psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align --command "SELECT CASE WHEN pg_try_advisory_lock(${DATABASE_OPERATION_ADVISORY_LOCK_KEY}) THEN pg_advisory_unlock(${DATABASE_OPERATION_ADVISORY_LOCK_KEY}) ELSE false END"`,
      ]),
      {
        cwd: context.repositoryRoot,
        env: commandEnvironment(context),
        timeout: 30_000,
      },
    ),
    "lifecycle_database_operation_lease_unavailable",
    stage,
  );
  if (!["t", "true"].includes(output.toLowerCase())) {
    throw new LifecycleError(
      "lifecycle_database_operation_lease_active",
      stage,
    );
  }
}

async function recoverLock(context, values) {
  const confirmation = String(
    values["--confirm-operation-id"] || "",
  ).trim();
  if (!OPERATION_ID.test(confirmation)) {
    throw new LifecycleError(
      "lifecycle_lock_recovery_confirmation_required",
      "lock_recovery",
    );
  }
  try {
    const assertExternalLeaseReleased = () =>
      assertDatabaseLifecycleLeaseReleased(context);
    const recovered = await recoverStaleLifecycleLock(
      context.stateDirectory,
      confirmation,
      {
        assertExternalLeaseReleased,
      },
    );
    return receipt("recover-lock", {
      operation_id: recovered.operation_id,
      stale_lock_recovered: recovered.recovered === true,
      owner_identity_verified_stale: true,
      database_operation_lease_verified_released: true,
    });
  } catch (error) {
    throw asLifecycleError(error, "lock_recovery");
  }
}

async function apply(context, values) {
  return withLifecycleLock(context.stateDirectory, async () => {
    let state = await readLifecycleState(context.stateDirectory);
    const requestedPlan = String(values["--plan-id"] || "").trim();
    if (!state?.operation) {
      throw new LifecycleError("lifecycle_apply_not_planned", "apply_preflight");
    }
    if (state.operation.database_change_started) {
      throw new LifecycleError("lifecycle_rollback_required", "apply_preflight");
    }
    if (state.operation.phase !== "planned") {
      throw new LifecycleError("lifecycle_apply_not_planned", "apply_preflight");
    }
    if (!OPERATION_ID.test(requestedPlan)) {
      throw new LifecycleError("lifecycle_plan_id_required", "apply_preflight");
    }
    if (requestedPlan !== state.operation.operation_id) {
      throw new LifecycleError("lifecycle_plan_id_mismatch", "apply_preflight");
    }
    let controlPlaneStopAttempted = false;
    try {
      const configuration = await configurationSnapshot(context);
      if (JSON.stringify(configuration) !== JSON.stringify(state.operation.configuration)) {
        throw new LifecycleError("lifecycle_configuration_changed", "configuration_preflight");
      }
      const current = await runningInstallation(context);
      if (current.image_id !== state.operation.from.image_id) {
        throw new LifecycleError("lifecycle_running_image_changed", "apply_preflight");
      }
      await assertBoundAuthorityDatabase(
        context,
        state.operation.authority_database,
      );
      await assertPostgresClusterIdentity(
        context,
        state.operation.postgres_cluster_system_identifier,
        "apply_database_identity",
      );
      await assertDatabaseObjectIdentity(
        context,
        state.operation.authority_database,
        state.operation.authority_database_oid,
        null,
        "apply_database_identity",
      );
      await schemaReadiness(
        context,
        state.operation.from.image_id,
        state.operation.from.schema,
        "apply_current",
      );
      if (await imageId(context, state.operation.to.image_reference, "apply_preflight")
        !== state.operation.to.image_id) {
        throw new LifecycleError("lifecycle_target_image_changed", "apply_preflight");
      }
      const targetSchema = await imageSchemaIdentity(
        context,
        state.operation.to.image_reference,
        "apply_preflight",
      );
      if (!sameSchema(targetSchema, state.operation.to.schema)) {
        throw new LifecycleError("lifecycle_target_schema_changed", "apply_preflight");
      }
      await assertNoActiveRuns(context);

      controlPlaneStopAttempted = true;
      await stopControlPlane(context);
      await assertNoActiveRuns(context);
      if (
        await productionDatabase(context)
        !== state.operation.authority_database
      ) {
        throw new LifecycleError(
          "lifecycle_authority_database_changed",
          "database_identity_preflight",
        );
      }
      await assertPostgresClusterIdentity(
        context,
        state.operation.postgres_cluster_system_identifier,
        "backup_database_identity",
      );
      await assertDatabaseObjectIdentity(
        context,
        state.operation.authority_database,
        state.operation.authority_database_oid,
        null,
        "backup_database_identity",
      );
      const backupPath = plannedBackupPath(context, state.operation);
      await runBackup(context, backupPath);
      const backup = await validateBackupBundle(backupPath);
      const backupRecorded = appendEvent({
        ...state.operation,
        phase: "backup_ready",
        backup: {
          ...backup,
          created_at: context.now().toISOString(),
          source_image_id: state.operation.from.image_id,
          source_schema_contract: state.operation.from.schema.contract,
          authority_database: state.operation.authority_database,
        },
        last_failure: null,
      }, "backup", "succeeded", context.now);
      state = {
        ...state,
        generation: state.generation + 1,
        operation: appendEvent({
          ...backupRecorded,
          database_change_started: true,
          recovery_required: true,
          last_failure: null,
        }, "target_migration", "started", context.now),
        updated_at: context.now().toISOString(),
      };
      await writeLifecycleState(context.stateDirectory, state);

      await migrateTarget(context, state.operation.to.image_reference);
      await startControlPlane(
        context,
        state.operation.to.image_reference,
        "target_control_plane_start",
      );
      await waitForHealth(
        context,
        state.operation.to.image_reference,
        "target_health",
      );
      await schemaReadiness(
        context,
        state.operation.to.image_reference,
        state.operation.to.schema,
        "apply_target",
      );
      const completedAt = context.now().toISOString();
      const targetInstallation = {
        ...state.operation.to,
        verified_at: completedAt,
      };
      state = {
        ...state,
        generation: state.generation + 1,
        installation: targetInstallation,
        operation: appendEvent({
          ...state.operation,
          phase: "applied",
          to: targetInstallation,
          completed_at: completedAt,
          recovery_required: false,
          last_failure: null,
        }, "apply", "succeeded", context.now),
        updated_at: completedAt,
      };
      await writeLifecycleState(context.stateDirectory, state);
      return receipt("apply", {
        operation_id: state.operation.operation_id,
        phase: state.operation.phase,
        image_reference: state.installation.image_reference,
        image_id: state.installation.image_id,
        schema_contract: state.installation.schema.contract,
        schema_fingerprint_sha256:
          state.installation.schema.fingerprint_sha256,
        backup_bundle: state.operation.backup.path,
        backup_commit_marker: state.operation.backup.commit_marker,
        active_run_preflight_passed: true,
        configuration_preflight_passed: true,
        state_generation: state.generation,
      });
    } catch (error) {
      const databaseChanged = state?.operation?.database_change_started === true;
      let compensationFailed = false;
      if (databaseChanged) {
        await stopControlPlane(context).catch(() => {
          compensationFailed = true;
        });
      } else if (controlPlaneStopAttempted) {
        const backupPath = plannedBackupPath(context, state.operation);
        try {
          await rm(backupPath, { recursive: true, force: true });
        } catch {
          compensationFailed = true;
        }
        try {
          await startControlPlane(
            context,
            state.operation.from.image_id,
            "apply_compensation_start",
          );
          await waitForHealth(
            context,
            state.operation.from.image_id,
            "apply_compensation_health",
          );
        } catch {
          compensationFailed = true;
        }
      }
      await recordFailure(
        context,
        state,
        error,
        databaseChanged || compensationFailed,
      );
      throw error;
    }
  });
}

async function rollback(context, values) {
  return withLifecycleLock(context.stateDirectory, async () => {
    let state = await readLifecycleState(context.stateDirectory);
    const confirmation = String(
      values["--confirm-restore-from-backup"] || "",
    ).trim();
    if (!state?.operation || !new Set(["applied", "backup_ready"]).has(state.operation.phase)) {
      throw new LifecycleError("lifecycle_rollback_unavailable", "rollback_preflight");
    }
    if (
      state.operation.phase === "backup_ready"
      && state.operation.database_change_started !== true
    ) {
      throw new LifecycleError("lifecycle_rollback_not_required", "rollback_preflight");
    }
    if (!OPERATION_ID.test(confirmation) || confirmation !== state.operation.operation_id) {
      throw new LifecycleError(
        "lifecycle_rollback_confirmation_required",
        "rollback_preflight",
      );
    }
    let controlPlaneStopAttempted = false;
    let swapMutationArmed = false;
    try {
      const configuration = await configurationSnapshot(context);
      if (JSON.stringify(configuration) !== JSON.stringify(state.operation.configuration)) {
        throw new LifecycleError("lifecycle_configuration_changed", "configuration_preflight");
      }
      plannedBackupPath(context, state.operation);
      await validateBackupBundle(state.operation.backup.path);
      if (
        state.operation.backup.authority_database
        !== state.operation.authority_database
      ) {
        throw new LifecycleError(
          "lifecycle_backup_database_binding_invalid",
          "rollback_preflight",
        );
      }
      if (await imageId(context, state.operation.from.image_id, "rollback_preflight")
        !== state.operation.from.image_id) {
        throw new LifecycleError("lifecycle_rollback_image_changed", "rollback_preflight");
      }
      const fromSchema = await imageSchemaIdentity(
        context,
        state.operation.from.image_id,
        "rollback_preflight",
      );
      if (!sameSchema(fromSchema, state.operation.from.schema)) {
        throw new LifecycleError("lifecycle_rollback_schema_changed", "rollback_preflight");
      }
      let swap = validatedDatabaseSwap(state.operation);
      if (!swap && state.operation.phase === "applied") {
        const current = await runningInstallation(context);
        if (current.image_id !== state.installation.image_id) {
          throw new LifecycleError("lifecycle_running_image_changed", "rollback_preflight");
        }
        await schemaReadiness(
          context,
          state.installation.image_id,
          state.installation.schema,
          "rollback_current",
        );
        await assertBoundAuthorityDatabase(
          context,
          state.operation.authority_database,
        );
        await assertNoActiveRuns(context);
      }
      const authorityDatabase = state.operation.authority_database;
      await assertPostgresClusterIdentity(
        context,
        state.operation.postgres_cluster_system_identifier,
        "rollback_database_preflight",
      );
      if (
        !SAFE_DATABASE_IDENTIFIER.test(String(authorityDatabase || ""))
        || authorityDatabase === "postgres"
        || await productionDatabase(context) !== authorityDatabase
      ) {
        throw new LifecycleError(
          "lifecycle_authority_database_changed",
          "rollback_database_preflight",
        );
      }
      controlPlaneStopAttempted = true;
      await stopControlPlane(context);

      if (!swap) {
        await assertDatabaseObjectIdentity(
          context,
          authorityDatabase,
          state.operation.authority_database_oid,
          null,
          "rollback_database_preflight",
        );
        await assertNoActiveRuns(context);
        const suffix = state.operation.operation_id.slice(-12);
        const restoreDatabase = `agentops_restore_${suffix}`;
        const quarantineDatabase = `agentops_quarantine_${suffix}`;
        const beforeIntent = await databasePresence(context, [
          authorityDatabase,
          restoreDatabase,
          quarantineDatabase,
        ]);
        if (
          !beforeIntent.has(authorityDatabase)
          || beforeIntent.has(restoreDatabase)
          || beforeIntent.has(quarantineDatabase)
        ) {
          throw new LifecycleError(
            "lifecycle_database_swap_state_invalid",
            "rollback_restore_preflight",
          );
        }
        swap = {
          authority_database: authorityDatabase,
          authority_database_oid: state.operation.authority_database_oid,
          cluster_system_identifier:
            state.operation.postgres_cluster_system_identifier,
          restore_database: restoreDatabase,
          restore_database_marker:
            `agentops_byoc_restore_v1:${state.operation.operation_id}:${state.operation.backup.database_dump_sha256}`,
          restore_database_oid: null,
          quarantine_database: quarantineDatabase,
          phase: "restore_intent",
          updated_at: context.now().toISOString(),
        };
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "restore_intent",
        );
        swap = validatedDatabaseSwap(state.operation);
      }

      if (swap.phase === "restore_intent") {
        const beforeRestore = await databasePresence(context, [
          authorityDatabase,
          swap.restore_database,
          swap.quarantine_database,
        ]);
        if (
          !beforeRestore.has(authorityDatabase)
          || beforeRestore.has(swap.quarantine_database)
        ) {
          throw new LifecycleError(
            "lifecycle_database_swap_state_invalid",
            "rollback_restore_preflight",
          );
        }
        if (beforeRestore.has(swap.restore_database)) {
          await dropBoundDatabase(
            context,
            swap.restore_database,
            swap.restore_database_oid,
            swap.restore_database_marker,
            swap.cluster_system_identifier,
            "rollback_orphan_restore_cleanup",
          );
        }
        await runRestoreDrill(
          context,
          state.operation.backup.path,
          state.operation.from.image_id,
          swap.restore_database,
          swap.restore_database_marker,
        );
        const afterRestore = await databasePresence(context, [
          authorityDatabase,
          swap.restore_database,
          swap.quarantine_database,
        ]);
        if (
          !afterRestore.has(authorityDatabase)
          || !afterRestore.has(swap.restore_database)
          || afterRestore.has(swap.quarantine_database)
        ) {
          throw new LifecycleError(
            "lifecycle_database_swap_state_invalid",
            "rollback_restore_verification",
          );
        }
        const restoreIdentity = await assertDatabaseObjectIdentity(
          context,
          swap.restore_database,
          null,
          swap.restore_database_marker,
          "rollback_restore_verification",
        );
        swap = {
          ...swap,
          restore_database_oid: restoreIdentity.oid,
        };
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "restore_verified",
        );
        swap = validatedDatabaseSwap(state.operation);
      }

      const production = swap.authority_database;
      const restoreDatabase = swap.restore_database;
      const quarantineDatabase = swap.quarantine_database;
      let present = await databasePresence(context, [
        production,
        restoreDatabase,
        quarantineDatabase,
      ]);
      const beforeProductionRename = present.has(production)
        && present.has(restoreDatabase)
        && !present.has(quarantineDatabase);
      const productionQuarantined = !present.has(production)
        && present.has(restoreDatabase)
        && present.has(quarantineDatabase);
      const restorePromoted = present.has(production)
        && !present.has(restoreDatabase)
        && present.has(quarantineDatabase);
      if (
        !beforeProductionRename
        && !productionQuarantined
        && !restorePromoted
      ) {
        throw new LifecycleError(
          "lifecycle_database_swap_state_invalid",
          "database_swap_recovery",
        );
      }
      if (productionQuarantined || restorePromoted) {
        await assertDatabaseObjectIdentity(
          context,
          quarantineDatabase,
          swap.authority_database_oid,
          null,
          "database_swap_recovery",
        );
      }
      if (productionQuarantined) {
        await assertDatabaseObjectIdentity(
          context,
          restoreDatabase,
          swap.restore_database_oid,
          swap.restore_database_marker,
          "database_swap_recovery",
        );
      }
      if (restorePromoted) {
        await assertDatabaseObjectIdentity(
          context,
          production,
          swap.restore_database_oid,
          swap.restore_database_marker,
          "database_swap_recovery",
        );
      }

      if (beforeProductionRename) {
        await assertDatabaseObjectIdentity(
          context,
          production,
          swap.authority_database_oid,
          null,
          "production_rename_identity",
        );
        await assertDatabaseObjectIdentity(
          context,
          restoreDatabase,
          swap.restore_database_oid,
          swap.restore_database_marker,
          "production_rename_identity",
        );
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "production_rename_started",
        );
        swap = validatedDatabaseSwap(state.operation);
        swapMutationArmed = true;
        await renameDatabase(
          context,
          production,
          quarantineDatabase,
          swap.authority_database_oid,
          null,
          swap.cluster_system_identifier,
        );
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "production_quarantined",
        );
        swap = validatedDatabaseSwap(state.operation);
        present = await databasePresence(context, [
          production,
          restoreDatabase,
          quarantineDatabase,
        ]);
        await assertDatabaseObjectIdentity(
          context,
          quarantineDatabase,
          swap.authority_database_oid,
          null,
          "production_quarantine_identity",
        );
      } else {
        swapMutationArmed = true;
      }

      if (
        !present.has(production)
        && present.has(restoreDatabase)
        && present.has(quarantineDatabase)
      ) {
        await assertDatabaseObjectIdentity(
          context,
          quarantineDatabase,
          swap.authority_database_oid,
          null,
          "restore_promotion_identity",
        );
        await assertDatabaseObjectIdentity(
          context,
          restoreDatabase,
          swap.restore_database_oid,
          swap.restore_database_marker,
          "restore_promotion_identity",
        );
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "restore_promotion_started",
        );
        swap = validatedDatabaseSwap(state.operation);
        await renameDatabase(
          context,
          restoreDatabase,
          production,
          swap.restore_database_oid,
          swap.restore_database_marker,
          swap.cluster_system_identifier,
        );
        state = await recordDatabaseSwapPhase(
          context,
          state,
          swap,
          "restore_promoted",
        );
        swap = validatedDatabaseSwap(state.operation);
        present = await databasePresence(context, [
          production,
          restoreDatabase,
          quarantineDatabase,
        ]);
        await assertDatabaseObjectIdentity(
          context,
          production,
          swap.restore_database_oid,
          swap.restore_database_marker,
          "restore_promoted_identity",
        );
      }
      if (
        !present.has(production)
        || present.has(restoreDatabase)
        || !present.has(quarantineDatabase)
      ) {
        throw new LifecycleError(
          "lifecycle_database_swap_state_invalid",
          "database_swap_recovery",
        );
      }

      await startControlPlane(
        context,
        state.operation.from.image_id,
        "rollback_control_plane_start",
      );
      await waitForHealth(
        context,
        state.operation.from.image_id,
        "rollback_health",
      );
      await schemaReadiness(
        context,
        state.operation.from.image_id,
        state.operation.from.schema,
        "rollback_restored",
      );
      if (await runningAuthorityDatabase(context) !== production) {
        throw new LifecycleError(
          "lifecycle_authority_database_changed",
          "rollback_restored",
        );
      }
      const completedAt = context.now().toISOString();
      const restoredInstallation = {
        ...state.operation.from,
        verified_at: completedAt,
      };
      state = {
        ...state,
        generation: state.generation + 1,
        installation: restoredInstallation,
        operation: appendEvent({
          ...state.operation,
          phase: "rolled_back",
          completed_at: completedAt,
          rollback_authority: "backup_restore",
          down_migration_performed: false,
          recovery_required: false,
          quarantine_database: quarantineDatabase,
          quarantine_cleanup_pending: true,
          database_swap: {
            ...swap,
            phase: "rollback_verified",
            updated_at: completedAt,
          },
          last_failure: null,
        }, "rollback", "succeeded", context.now),
        updated_at: completedAt,
      };
      await writeLifecycleState(context.stateDirectory, state);
      swapMutationArmed = false;

      let quarantineRemoved = false;
      let cleanupStatePersisted = false;
      try {
        await dropBoundDatabase(
          context,
          quarantineDatabase,
          swap.authority_database_oid,
          null,
          swap.cluster_system_identifier,
        );
        quarantineRemoved = true;
        const cleanupRecordedAt = context.now().toISOString();
        const cleanupState = {
          ...state,
          generation: state.generation + 1,
          operation: appendEvent({
            ...state.operation,
            quarantine_cleanup_pending: false,
            quarantine_removed_at: cleanupRecordedAt,
            database_swap: {
              ...state.operation.database_swap,
              phase: "cleanup_complete",
              updated_at: cleanupRecordedAt,
            },
          }, "rollback_quarantine_cleanup", "succeeded", context.now),
          updated_at: cleanupRecordedAt,
        };
        try {
          await writeLifecycleState(context.stateDirectory, cleanupState);
          state = cleanupState;
          cleanupStatePersisted = true;
        } catch {
          // The persisted pending marker remains conservative after cleanup.
        }
      } catch {
        // Rollback is complete; status exposes the retained quarantine for
        // explicit operator cleanup instead of undoing a verified rollback.
      }
      return receipt("rollback", {
        operation_id: state.operation.operation_id,
        phase: state.operation.phase,
        image_reference: state.installation.image_reference,
        image_id: state.installation.image_id,
        schema_contract: state.installation.schema.contract,
        backup_bundle: state.operation.backup.path,
        backup_restore_authoritative: true,
        down_migration_performed: false,
        explicit_confirmation_verified: true,
        quarantine_removed: quarantineRemoved,
        quarantine_cleanup_pending:
          !quarantineRemoved || !cleanupStatePersisted,
        state_generation: state.generation,
      });
    } catch (error) {
      let compensationFailed = false;
      if (swapMutationArmed) {
        await stopControlPlane(context).catch(() => {
          compensationFailed = true;
        });
      } else {
        const checkpoint = validatedDatabaseSwap(state?.operation || {});
        const restoreDatabase = checkpoint?.restore_database;
        if (restoreDatabase) {
          try {
            await assertPostgresClusterIdentity(
              context,
              checkpoint.cluster_system_identifier,
              "rollback_compensation",
            );
            await dropBoundDatabaseIfPresent(
              context,
              restoreDatabase,
              checkpoint.restore_database_oid,
              checkpoint.restore_database_marker,
              checkpoint.cluster_system_identifier,
              "rollback_compensation",
            );
          } catch {
            compensationFailed = true;
          }
        }
        if (controlPlaneStopAttempted) {
          try {
            await startControlPlane(
              context,
              state.installation.image_id,
              "rollback_compensation",
            );
            await waitForHealth(
              context,
              state.installation.image_id,
              "rollback_compensation",
            );
          } catch {
            compensationFailed = true;
          }
        }
        if (!compensationFailed && checkpoint) {
          state = {
            ...state,
            operation: {
              ...state.operation,
              database_swap: null,
            },
          };
        }
      }
      await recordFailure(
        context,
        state,
        error,
        swapMutationArmed || compensationFailed,
      );
      throw error;
    }
  });
}

async function cleanup(context, values) {
  return withLifecycleLock(context.stateDirectory, async () => {
    let state = await readLifecycleState(context.stateDirectory);
    const confirmation = String(
      values["--confirm-operation-id"] || "",
    ).trim();
    if (
      !state?.operation
      || state.operation.phase !== "rolled_back"
      || !OPERATION_ID.test(confirmation)
      || confirmation !== state.operation.operation_id
    ) {
      throw new LifecycleError(
        "lifecycle_cleanup_confirmation_required",
        "cleanup_preflight",
      );
    }
    const swap = validatedDatabaseSwap(state.operation);
    if (
      !swap
      || swap.authority_database !== state.operation.authority_database
      || swap.quarantine_database !== state.operation.quarantine_database
      || !new Set(["rollback_verified", "cleanup_complete"]).has(swap.phase)
    ) {
      throw new LifecycleError(
        "lifecycle_cleanup_state_invalid",
        "cleanup_preflight",
      );
    }
    const configuration = await configurationSnapshot(context);
    if (JSON.stringify(configuration) !== JSON.stringify(state.operation.configuration)) {
      throw new LifecycleError(
        "lifecycle_configuration_changed",
        "configuration_preflight",
      );
    }
    const running = await runningInstallation(context);
    if (running.image_id !== state.installation.image_id) {
      throw new LifecycleError(
        "lifecycle_running_image_changed",
        "cleanup_preflight",
      );
    }
    await schemaReadiness(
      context,
      state.installation.image_id,
      state.installation.schema,
      "cleanup_current",
    );
    await assertBoundAuthorityDatabase(
      context,
      state.operation.authority_database,
    );
    await assertPostgresClusterIdentity(
      context,
      state.operation.postgres_cluster_system_identifier,
      "cleanup_preflight",
    );
    await assertDatabaseObjectIdentity(
      context,
      swap.authority_database,
      swap.restore_database_oid,
      swap.restore_database_marker,
      "cleanup_preflight",
    );
    let present = await databasePresence(context, [
      swap.authority_database,
      swap.restore_database,
      swap.quarantine_database,
    ]);
    if (
      !present.has(swap.authority_database)
      || present.has(swap.restore_database)
    ) {
      throw new LifecycleError(
        "lifecycle_cleanup_state_invalid",
        "cleanup_preflight",
      );
    }
    const quarantinePresent = present.has(swap.quarantine_database);
    if (
      state.operation.quarantine_cleanup_pending !== true
      && quarantinePresent
    ) {
      throw new LifecycleError(
        "lifecycle_cleanup_state_invalid",
        "cleanup_preflight",
      );
    }
    if (quarantinePresent) {
      await dropBoundDatabase(
        context,
        swap.quarantine_database,
        swap.authority_database_oid,
        null,
        swap.cluster_system_identifier,
        "rollback_quarantine_cleanup",
      );
    }
    present = await databasePresence(context, [
      swap.authority_database,
      swap.restore_database,
      swap.quarantine_database,
    ]);
    if (
      !present.has(swap.authority_database)
      || present.has(swap.restore_database)
      || present.has(swap.quarantine_database)
    ) {
      throw new LifecycleError(
        "lifecycle_cleanup_incomplete",
        "cleanup_verification",
      );
    }
    const completedAt = context.now().toISOString();
    if (
      state.operation.quarantine_cleanup_pending === true
      || swap.phase !== "cleanup_complete"
    ) {
      state = {
        ...state,
        generation: state.generation + 1,
        operation: appendEvent({
          ...state.operation,
          quarantine_cleanup_pending: false,
          quarantine_removed_at:
            state.operation.quarantine_removed_at || completedAt,
          database_swap: {
            ...swap,
            phase: "cleanup_complete",
            updated_at: completedAt,
          },
          last_failure: null,
        }, "rollback_quarantine_cleanup", "succeeded", context.now),
        updated_at: completedAt,
      };
      await writeLifecycleState(context.stateDirectory, state);
    }
    return receipt("cleanup", {
      operation_id: state.operation.operation_id,
      phase: state.operation.phase,
      quarantine_removed: true,
      quarantine_cleanup_pending: false,
      cleanup_idempotent: !quarantinePresent,
      state_generation: state.generation,
    });
  });
}

export async function runLifecycle(arguments_, options = {}) {
  const parsed = parseArguments(arguments_);
  const environment = { ...process.env, ...(options.environment || {}) };
  const repositoryRoot = resolve(options.repositoryRoot || defaultRepositoryRoot);
  const stateDirectory = resolve(
    options.stateDirectory || lifecycleStateDirectory(environment),
  );
  const context = {
    repositoryRoot,
    composeFile: resolve(
      repositoryRoot,
      environment.AGENTOPS_BYOC_COMPOSE_FILE || "deploy/byoc/compose.yaml",
    ),
    envFile: resolve(
      repositoryRoot,
      environment.AGENTOPS_BYOC_ENV_FILE || "deploy/byoc/.env",
    ),
    stateDirectory,
    environment,
    runner: options.runner || ((command, args, runnerOptions = {}) =>
      defaultRunner(command, args, {
        ...runnerOptions,
        stateDirectory,
      })),
    recoveryRunner:
      options.recoveryRunner
      || options.runner
      || ((command, args, runnerOptions = {}) =>
        defaultRunner(command, args, {
          ...runnerOptions,
          stateDirectory,
          trackLifecycleLease: false,
        })),
    now: options.now || (() => new Date()),
    randomHex: options.randomHex || (() => randomBytes(16).toString("hex")),
    wait: options.wait || ((milliseconds) => new Promise((resolveWait) => {
      setTimeout(resolveWait, milliseconds);
    })),
  };
  if (parsed.command === "plan") return plan(context, parsed.values);
  if (parsed.command === "status") return status(context);
  if (parsed.command === "recover-lock") {
    return recoverLock(context, parsed.values);
  }
  if (parsed.command === "apply") return apply(context, parsed.values);
  if (parsed.command === "rollback") return rollback(context, parsed.values);
  return cleanup(context, parsed.values);
}

const invokedAsMain = process.argv[1]
  && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;

if (invokedAsMain) {
  const operation = process.argv[2] || "unknown";
  try {
    console.log(JSON.stringify(await runLifecycle(process.argv.slice(2))));
  } catch (error) {
    console.log(JSON.stringify(failureReceipt(operation, error)));
    process.exitCode = 1;
  }
}
