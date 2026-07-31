import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { Client, type ClientBase, type ClientConfig } from "pg";

import { postgresDsn, postgresSslEnabled } from "./config";
import {
  computeSchemaFingerprint,
  type SchemaFingerprintReceipt,
} from "./schemaFingerprint";
import {
  EXPECTED_POSTGRES_SCHEMA_FINGERPRINT,
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
  type MigrationDefinition,
} from "./schemaManifest";

export {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "./schemaManifest";

export type SchemaCommand = "migrate" | "check";

type LoadedMigration = MigrationDefinition & Readonly<{ sql: string }>;

export type SchemaReceipt = Readonly<{
  contract: "agentops_postgres_schema_readiness_v1";
  ok: true;
  operation: SchemaCommand;
  schema_contract: string;
  manifest_count: number;
  applied_count: number;
  current_count: number;
  lock_acquired: true;
  read_only: boolean;
  schema_fingerprint_contract: string;
  schema_fingerprint_verified: true;
  schema_object_count: number;
  credentials_omitted: true;
  sql_omitted: true;
  row_data_omitted: true;
}>;

export class SchemaReadinessError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "SchemaReadinessError";
    this.code = code;
  }
}

const REQUIRED_RELATIONS = Object.freeze([
  "users",
  "agents",
  "tasks",
  "agent_plans",
  "runs",
  "tool_calls",
  "approvals",
  "prepared_actions",
  "prepared_action_execution_leases",
  "prepared_action_execution_receipts",
  "knowledge_documents",
  "knowledge_chunks",
  "memories",
  "evaluations",
  "artifacts",
  "audit_logs",
  "runtime_connectors",
  "runtime_events",
  "agent_gateway_tokens",
  "agent_gateway_sessions",
  "agent_gateway_enrollment_requests",
  "plan_evidence_manifests",
  "workspace_memberships",
  "human_login_credentials",
  "human_sessions",
  "human_login_throttle",
  "human_memory_review_requests",
  "human_approval_decision_requests",
  "workspace_entitlements",
  "idx_audit_logs_workspace_created",
  "idx_approvals_customer_delivery_run_unique",
  "idx_workspace_entitlements_updated_by_v9",
  "idx_gateway_tokens_workspace_usage_v9",
  "idx_gateway_sessions_workspace_usage_v9",
  "idx_runs_workspace_monthly_usage_v9",
]);

const REQUIRED_LEDGER_COLUMNS = Object.freeze([
  "component",
  "version",
  "schema_contract",
  "checksum",
  "applied_at",
]);

const REQUIRED_COLUMNS = Object.freeze([
  ["approvals", "approval_kind"],
  ["memories", "workspace_id"],
  ["memories", "run_id"],
  ["runtime_events", "workspace_id"],
  ["workspace_entitlements", "workspace_id"],
  ["workspace_entitlements", "edition"],
  ["workspace_entitlements", "status"],
  ["workspace_entitlements", "capabilities_json"],
  ["workspace_entitlements", "max_agents"],
  ["workspace_entitlements", "max_active_enrollments"],
  ["workspace_entitlements", "max_active_sessions_per_agent"],
  ["workspace_entitlements", "max_monthly_runs"],
  ["workspace_entitlements", "max_monthly_cost_usd"],
  ["workspace_entitlements", "effective_at"],
  ["workspace_entitlements", "expires_at"],
  ["workspace_entitlements", "created_at"],
  ["workspace_entitlements", "updated_at"],
  ["workspace_entitlements", "updated_by_user_id"],
] as const);

const MIGRATION_ROOT = resolve(process.cwd(), "../../migrations/postgres");
const ADVISORY_LOCK_KEY = "7157544864185932631";

function sha256(value: string) {
  return createHash("sha256").update(value).digest("hex");
}

async function loadManifest(): Promise<readonly LoadedMigration[]> {
  const loaded: LoadedMigration[] = [];
  for (const migration of POSTGRES_MIGRATION_MANIFEST) {
    let sql: string;
    try {
      sql = await readFile(join(MIGRATION_ROOT, migration.filename), "utf8");
    } catch {
      throw new SchemaReadinessError("migration_file_missing");
    }
    if (sha256(sql) !== migration.checksum) {
      throw new SchemaReadinessError("migration_file_checksum_mismatch");
    }
    loaded.push({ ...migration, sql });
  }
  return loaded;
}

function clientConfig(connectionString?: string): ClientConfig {
  let resolvedConnectionString = connectionString;
  if (!resolvedConnectionString) {
    try {
      resolvedConnectionString = postgresDsn();
    } catch {
      throw new SchemaReadinessError("postgres_dsn_required");
    }
  }
  return {
    connectionString: resolvedConnectionString,
    application_name: "agentops-commercial-schema-runner",
    ssl: postgresSslEnabled() ? { rejectUnauthorized: true } : undefined,
  };
}

async function acquireTransactionLock(client: Client) {
  await client.query("SET LOCAL lock_timeout = '5s'");
  await client.query("SET LOCAL statement_timeout = '45s'");
  await client.query("SELECT pg_advisory_xact_lock($1::bigint)", [ADVISORY_LOCK_KEY]);
}

async function ledgerExists(client: Client) {
  const result = await client.query<{ relation: string | null }>(
    "SELECT to_regclass('agentops_schema_migrations')::text AS relation",
  );
  return result.rows[0]?.relation !== null;
}

async function assertLedgerShape(client: Client) {
  const result = await client.query<{ column_name: string }>(
    `SELECT column_name
       FROM information_schema.columns
      WHERE table_schema=current_schema()
        AND table_name='agentops_schema_migrations'`,
  );
  const actual = new Set(result.rows.map((row) => row.column_name));
  if (REQUIRED_LEDGER_COLUMNS.some((column) => !actual.has(column))) {
    throw new SchemaReadinessError("schema_ledger_shape_mismatch");
  }
}

type LedgerRow = {
  component: string;
  version: string;
  schema_contract: string;
  checksum: string;
};

async function readLedger(client: Client) {
  const components = POSTGRES_MIGRATION_MANIFEST.map((migration) => migration.component);
  const result = await client.query<LedgerRow>(
    `SELECT component,version,schema_contract,checksum
       FROM agentops_schema_migrations
      WHERE component=ANY($1::text[])
      ORDER BY component`,
    [components],
  );
  return new Map(result.rows.map((row) => [row.component, row]));
}

function assertLedgerEntry(migration: MigrationDefinition, row: LedgerRow | undefined) {
  if (!row) return;
  if (
    row.version !== migration.version
    || row.schema_contract !== migration.schemaContract
    || row.checksum !== migration.checksum
  ) {
    throw new SchemaReadinessError("schema_ledger_mismatch");
  }
}

async function recordMigration(client: Client, migration: MigrationDefinition) {
  await client.query(
    `INSERT INTO agentops_schema_migrations(
       component,version,schema_contract,checksum,applied_at
     ) VALUES(
       $1,$2,$3,$4,
       to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
     )`,
    [
      migration.component,
      migration.version,
      migration.schemaContract,
      migration.checksum,
    ],
  );
}

async function assertSchemaRelations(client: Client) {
  const result = await client.query<{ relation_name: string; relation: string | null }>(
    `SELECT relation_name,to_regclass(relation_name)::text AS relation
       FROM unnest($1::text[]) AS relation_name`,
    [REQUIRED_RELATIONS],
  );
  if (result.rows.some((row) => row.relation === null)) {
    throw new SchemaReadinessError("schema_relation_missing");
  }

  for (const [tableName, columnName] of REQUIRED_COLUMNS) {
    const column = await client.query<{ present: boolean }>(
      `SELECT EXISTS(
         SELECT 1
           FROM information_schema.columns
          WHERE table_schema=current_schema()
            AND table_name=$1
            AND column_name=$2
       ) AS present`,
      [tableName, columnName],
    );
    if (!column.rows[0]?.present) {
      throw new SchemaReadinessError("schema_column_missing");
    }
  }
}

export async function assertExpectedSchemaFingerprint(
  client: ClientBase,
): Promise<SchemaFingerprintReceipt> {
  let actual: SchemaFingerprintReceipt;
  try {
    actual = await computeSchemaFingerprint(client);
  } catch {
    throw new SchemaReadinessError("schema_fingerprint_check_failed");
  }
  if (
    actual.contract !== EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.contract
    || actual.sha256 !== EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.sha256
    || actual.object_count !== EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.objectCount
  ) {
    throw new SchemaReadinessError("schema_fingerprint_mismatch");
  }
  return actual;
}

async function migrate(client: Client, manifest: readonly LoadedMigration[]) {
  let appliedCount = 0;
  let currentCount = 0;

  for (const migration of manifest) {
    let rows = new Map<string, LedgerRow>();
    if (await ledgerExists(client)) {
      await assertLedgerShape(client);
      rows = await readLedger(client);
    }

    const recorded = rows.get(migration.component);
    assertLedgerEntry(migration, recorded);
    if (recorded) {
      currentCount += 1;
      continue;
    }

    await client.query(migration.sql);
    if (!(await ledgerExists(client))) {
      throw new SchemaReadinessError("schema_ledger_missing_after_migration");
    }
    await assertLedgerShape(client);
    await recordMigration(client, migration);
    appliedCount += 1;
  }

  return { appliedCount, currentCount };
}

async function check(client: Client) {
  if (!(await ledgerExists(client))) {
    throw new SchemaReadinessError("schema_ledger_missing");
  }
  await assertLedgerShape(client);
  const rows = await readLedger(client);
  for (const migration of POSTGRES_MIGRATION_MANIFEST) {
    const recorded = rows.get(migration.component);
    if (!recorded) {
      throw new SchemaReadinessError("schema_ledger_behind");
    }
    assertLedgerEntry(migration, recorded);
  }
  return { appliedCount: 0, currentCount: rows.size };
}

export async function runPostgresSchemaCommand(
  operation: SchemaCommand,
  options: Readonly<{ connectionString?: string }> = {},
): Promise<SchemaReceipt> {
  const manifest = await loadManifest();
  const client = new Client(clientConfig(options.connectionString));
  await client.connect();
  try {
    await client.query(operation === "check" ? "BEGIN READ ONLY" : "BEGIN");
    try {
      await acquireTransactionLock(client);
      const counts = operation === "check"
        ? await check(client)
        : await migrate(client, manifest);
      await assertSchemaRelations(client);
      const fingerprint = await assertExpectedSchemaFingerprint(client);
      await client.query(operation === "check" ? "ROLLBACK" : "COMMIT");
      return {
        contract: "agentops_postgres_schema_readiness_v1",
        ok: true,
        operation,
        schema_contract: SCHEMA_CONTRACT,
        manifest_count: manifest.length,
        applied_count: counts.appliedCount,
        current_count: counts.currentCount,
        lock_acquired: true,
        read_only: operation === "check",
        schema_fingerprint_contract: fingerprint.contract,
        schema_fingerprint_verified: true,
        schema_object_count: fingerprint.object_count,
        credentials_omitted: true,
        sql_omitted: true,
        row_data_omitted: true,
      };
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      if (error instanceof SchemaReadinessError) throw error;
      throw new SchemaReadinessError(
        operation === "check" ? "schema_check_failed" : "schema_migration_failed",
      );
    }
  } finally {
    await client.end().catch(() => undefined);
  }
}
