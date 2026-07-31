import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

import { Client, type ClientBase, type ClientConfig } from "pg";

import {
  isProductionDeployment,
  postgresApplicationSchema,
  postgresDsn,
  postgresEntitlementAdminRole,
  postgresMigratorDsn,
  postgresRuntimeApiSchema,
  postgresRuntimeRole,
  postgresSslEnabled,
  secretEnvironmentValue,
} from "./config";
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
  database_role_boundary_verified: boolean | null;
  runtime_role_omitted: true;
  credentials_omitted: true;
  sql_omitted: true;
  row_data_omitted: true;
}>;

export type RuntimeRoleBoundaryReceipt = Readonly<{
  contract: "agentops_postgres_runtime_role_boundary_v1";
  ok: true;
  protected_relation_owner_excluded: true;
  reservation_direct_dml_forbidden: true;
  entitlement_direct_dml_forbidden: true;
  entitlement_challenge_direct_access_forbidden: true;
  migration_ledger_direct_dml_forbidden: true;
  approved_cost_functions_executable: true;
  approved_cost_function_integrity_verified: true;
  application_function_execute_allowlist_verified: true;
  original_cost_functions_not_executable: true;
  entitlement_challenge_issue_executable: true;
  entitlement_challenge_issue_integrity_verified: true;
  entitlement_challenge_admin_functions_not_executable: true;
  function_owner_restricted: true;
  runtime_api_function_set_verified: true;
  normal_application_operations_allowed: true;
  entitlement_read_allowed: true;
  superuser_forbidden: true;
  bypass_rls_forbidden: true;
  set_role_membership_forbidden: true;
  pg_temp_shadowing_forbidden: true;
  runtime_role_omitted: true;
  credentials_omitted: true;
  sql_omitted: true;
  row_data_omitted: true;
}>;

export type EntitlementAdminRoleBoundaryReceipt = Readonly<{
  contract: "agentops_postgres_entitlement_admin_role_boundary_v1";
  ok: true;
  application_schema_access_forbidden: true;
  application_relation_access_forbidden: true;
  application_function_execute_forbidden: true;
  runtime_api_usage_allowed: true;
  plan_apply_executable: true;
  issue_not_executable: true;
  wrapper_integrity_verified: true;
  function_owner_restricted: true;
  runtime_api_function_set_verified: true;
  superuser_forbidden: true;
  bypass_rls_forbidden: true;
  set_role_membership_forbidden: true;
  credentials_omitted: true;
  sql_omitted: true;
  row_data_omitted: true;
}>;

export type SchemaCommandOptions = Readonly<{
  connectionString?: string;
  applicationSchema?: string;
  runtimeApiSchema?: string;
  runtimeRole?: string;
  runtimePassword?: string;
  entitlementAdminRole?: string;
  entitlementAdminPassword?: string;
  enforceRuntimeBoundary?: boolean;
  provisionRoleBoundary?: boolean;
}>;

type ResolvedRoleProvisioningContext = Readonly<{
  applicationSchema: string;
  runtimeApiSchema: string;
  runtimeRole: string;
  runtimePassword: string;
  entitlementAdminRole: string;
  entitlementAdminPassword: string;
}>;

type PreparedMigrationFunctionOwner = Readonly<{
  migratorRole: string;
  functionOwnerRole: string;
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
  "run_cost_reservations",
  "entitlement_admin_challenges",
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
  ["workspace_entitlements", "max_concurrent_runs"],
  ["workspace_entitlements", "effective_at"],
  ["workspace_entitlements", "expires_at"],
  ["workspace_entitlements", "created_at"],
  ["workspace_entitlements", "updated_at"],
  ["workspace_entitlements", "updated_by_user_id"],
  ["runs", "billing_class"],
  ["runs", "cost_usd"],
  ["run_cost_reservations", "billing_class"],
  ["run_cost_reservations", "billing_month_utc"],
  ["run_cost_reservations", "estimated_cost_usd"],
  ["run_cost_reservations", "observed_cost_usd"],
  ["run_cost_reservations", "settled_cost_usd"],
  ["run_cost_reservations", "state"],
  ["entitlement_admin_challenges", "challenge_id"],
  ["entitlement_admin_challenges", "token_sha256"],
  ["entitlement_admin_challenges", "request_json"],
  ["entitlement_admin_challenges", "request_sha256"],
  ["entitlement_admin_challenges", "bound_admin_role"],
  ["entitlement_admin_challenges", "expires_at"],
  ["entitlement_admin_challenges", "consumed_at"],
] as const);

const MIGRATION_ROOT = resolve(process.cwd(), "../../migrations/postgres");
const ADVISORY_LOCK_KEY = "7157544864185932631";
const SAFE_IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const PROTECTED_COST_RELATIONS = Object.freeze([
  "agentops_schema_migrations",
  "workspace_entitlements",
  "runs",
  "run_cost_reservations",
  "entitlement_admin_challenges",
] as const);
const APPROVED_COST_FUNCTIONS = Object.freeze([
  {
    name: "agentops_reserve_run_cost_v10",
    arguments: "text,text,numeric,text,text,interval",
    parameters:
      "p_workspace_id text,p_run_id text,p_estimated_cost_usd numeric,"
      + "p_idempotency_key_hash text,p_request_hash text,p_ttl interval",
    invocation: "$1,$2,$3,$4,$5,$6",
    returnType: "run_cost_reservations",
  },
  {
    name: "agentops_heartbeat_run_cost_v10",
    arguments: "text,text,numeric,interval",
    parameters:
      "p_workspace_id text,p_run_id text,p_observed_cost_usd numeric,"
      + "p_ttl interval",
    invocation: "$1,$2,$3,$4",
    returnType: "run_cost_reservations",
  },
  {
    name: "agentops_settle_run_cost_v10",
    arguments: "text,text,numeric,text,text",
    parameters:
      "p_workspace_id text,p_run_id text,p_actual_cost_usd numeric,"
      + "p_idempotency_key_hash text,p_request_hash text",
    invocation: "$1,$2,$3,$4,$5",
    returnType: "run_cost_reservations",
  },
  {
    name: "agentops_release_run_cost_v10",
    arguments: "text,text,text,text,text",
    parameters:
      "p_workspace_id text,p_run_id text,p_reason text,"
      + "p_idempotency_key_hash text,p_request_hash text",
    invocation: "$1,$2,$3,$4,$5",
    returnType: "run_cost_reservations",
  },
  {
    name: "agentops_expire_run_cost_reservations_v10",
    arguments: "text",
    parameters: "p_workspace_id text",
    invocation: "$1",
    returnType: "bigint",
  },
] as const);
const RUNTIME_APPLICATION_FUNCTIONS = Object.freeze([
  {
    name: "agentops_assert_approval_kind_binding",
    arguments: "text",
  },
  {
    name: "agentops_assert_prepared_action_execution_lease_v6",
    arguments: "text",
  },
] as const);
const ENTITLEMENT_CHALLENGE_FUNCTIONS = Object.freeze({
  issue: {
    publicName: "agentops_issue_workspace_entitlement_admin_challenge_v11",
    coreName: "agentops_issue_entitlement_admin_challenge_core_v11",
    arguments: "text,text,text,text,jsonb,text,interval",
    parameters:
      "p_human_session_id text,p_workspace_id text,p_operator_user_id text,"
      + "p_mode text,p_request jsonb,p_token_sha256 text,p_ttl interval",
    invocation: "$1,$2,$3,$4,$5,$6,$7",
  },
  plan: {
    publicName: "agentops_plan_workspace_entitlement_v11",
    coreName: "agentops_plan_workspace_entitlement_core_v11",
    arguments: "text,text,jsonb",
    parameters:
      "p_challenge_id text,p_challenge_token text,p_request jsonb",
    invocation: "$1,$2,$3",
  },
  apply: {
    publicName: "agentops_apply_workspace_entitlement_v11",
    coreName: "agentops_apply_workspace_entitlement_core_v11",
    arguments: "text,text,jsonb",
    parameters:
      "p_challenge_id text,p_challenge_token text,p_request jsonb",
    invocation: "$1,$2,$3",
  },
} as const);

function safeIdentifier(value: string, errorCode: string) {
  if (!SAFE_IDENTIFIER.test(value)) throw new SchemaReadinessError(errorCode);
  return value;
}

function quotedIdentifier(value: string) {
  return `"${safeIdentifier(value, "postgres_identifier_invalid")}"`;
}

function qualifiedIdentifier(schema: string, object: string) {
  return `${quotedIdentifier(schema)}.${quotedIdentifier(object)}`;
}

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

function clientConfig(
  operation: SchemaCommand,
  connectionString?: string,
): ClientConfig {
  let resolvedConnectionString = connectionString;
  if (!resolvedConnectionString) {
    try {
      resolvedConnectionString = operation === "migrate"
        ? postgresMigratorDsn()
        : postgresDsn();
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

async function selectedApplicationSchema(
  client: ClientBase,
  configured?: string,
) {
  const explicit = String(
    configured || process.env.AGENTOPS_POSTGRES_SCHEMA || "",
  ).trim();
  if (explicit) return safeIdentifier(explicit, "postgres_schema_invalid");
  const result = await client.query<{ schema_name: string }>(
    "SELECT current_schema() AS schema_name",
  );
  return safeIdentifier(
    String(result.rows[0]?.schema_name || ""),
    "postgres_schema_invalid",
  );
}

function selectedRuntimeApiSchema(configured?: string) {
  if (configured) {
    return safeIdentifier(configured, "postgres_runtime_api_schema_invalid");
  }
  try {
    return postgresRuntimeApiSchema();
  } catch {
    throw new SchemaReadinessError("postgres_runtime_api_schema_invalid");
  }
}

function selectedRuntimeRole(configured: string | undefined, required: boolean) {
  if (configured) {
    return safeIdentifier(configured, "postgres_runtime_role_invalid");
  }
  try {
    return postgresRuntimeRole(required);
  } catch {
    throw new SchemaReadinessError("postgres_runtime_role_invalid");
  }
}

function selectedEntitlementAdminRole(
  configured: string | undefined,
  required: boolean,
) {
  if (configured) {
    return safeIdentifier(
      configured,
      "postgres_entitlement_admin_role_invalid",
    );
  }
  try {
    return postgresEntitlementAdminRole(required);
  } catch {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_role_invalid",
    );
  }
}

export function derivedPostgresFunctionOwnerRole(
  applicationSchema: string,
  runtimeApiSchema: string,
) {
  const application = safeIdentifier(
    applicationSchema,
    "postgres_schema_invalid",
  );
  const api = safeIdentifier(
    runtimeApiSchema,
    "postgres_runtime_api_schema_invalid",
  );
  const suffix = createHash("sha256")
    .update(`${application}\u0000${api}`, "utf8")
    .digest("hex")
    .slice(0, 24);
  return `agentops_fn_${suffix}`;
}

async function setLocalSearchPath(
  client: ClientBase,
  schemas: readonly string[],
) {
  const value = schemas
    .map((schema) => quotedIdentifier(schema))
    .join(", ");
  await client.query("SELECT set_config('search_path',$1,true)", [value]);
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
           FROM pg_attribute attribute_row
           JOIN pg_class relation_row
             ON relation_row.oid=attribute_row.attrelid
           JOIN pg_namespace namespace_row
             ON namespace_row.oid=relation_row.relnamespace
          WHERE namespace_row.nspname=current_schema()
            AND relation_row.relname=$1
            AND attribute_row.attname=$2
            AND attribute_row.attnum>0
            AND NOT attribute_row.attisdropped
       ) AS present`,
      [tableName, columnName],
    );
    if (!column.rows[0]?.present) {
      throw new SchemaReadinessError("schema_column_missing");
    }
  }
}

type RuntimeBoundaryContext = Readonly<{
  applicationSchema: string;
  runtimeApiSchema: string;
  runtimeRole?: string;
}>;

async function currentDatabasePrincipal(client: ClientBase) {
  const result = await client.query<{
    role_name: string;
    database_name: string;
    superuser: boolean;
    bypass_rls: boolean;
    create_role: boolean;
    create_db: boolean;
    replication: boolean;
    inherit: boolean;
  }>(
    `SELECT
       current_user AS role_name,
       current_database() AS database_name,
       role_row.rolsuper AS superuser,
       role_row.rolbypassrls AS bypass_rls,
       role_row.rolcreaterole AS create_role,
       role_row.rolcreatedb AS create_db,
       role_row.rolreplication AS replication,
       role_row.rolinherit AS inherit
     FROM pg_roles role_row
     WHERE role_row.rolname=current_user`,
  );
  const row = result.rows[0];
  if (!row) throw new SchemaReadinessError("postgres_principal_missing");
  return row;
}

async function roleHasMembership(
  client: ClientBase,
  roleName: string,
) {
  const result = await client.query<{ any_role_membership: boolean }>(
    `SELECT EXISTS(
       SELECT 1
       FROM pg_auth_members membership
       JOIN pg_roles member_role ON member_role.oid=membership.member
       JOIN pg_roles granted_role ON granted_role.oid=membership.roleid
       WHERE member_role.rolname=$1
          OR granted_role.rolname=$1
     ) AS any_role_membership`,
    [roleName],
  );
  return result.rows[0]?.any_role_membership === true;
}

async function restrictedFunctionOwnerRole(
  client: ClientBase,
  roleName: string,
) {
  const result = await client.query<{
    login: boolean;
    superuser: boolean;
    create_role: boolean;
    create_db: boolean;
    replication: boolean;
    bypass_rls: boolean;
    inherit: boolean;
  }>(
    `SELECT
       rolcanlogin AS login,
       rolsuper AS superuser,
       rolcreaterole AS create_role,
       rolcreatedb AS create_db,
       rolreplication AS replication,
       rolbypassrls AS bypass_rls,
       rolinherit AS inherit
     FROM pg_roles
     WHERE rolname=$1`,
    [roleName],
  );
  const role = result.rows[0];
  return Boolean(
    role
    && !role.login
    && !role.superuser
    && !role.create_role
    && !role.create_db
    && !role.replication
    && !role.bypass_rls
    && !role.inherit
    && !(await roleHasMembership(client, roleName))
  );
}

async function restrictedFunctionOwnerBoundary(
  client: ClientBase,
  roleName: string,
  applicationSchema: string,
  runtimeApiSchema: string,
) {
  if (!(await restrictedFunctionOwnerRole(client, roleName))) return false;
  const result = await client.query<{
    app_usage: boolean;
    app_create: boolean;
    api_usage: boolean;
    api_create: boolean;
    owns_schema: boolean;
    owns_relation: boolean;
    application_security_definer_owner_drift: boolean;
    application_security_definer_execute_acl_drift: boolean;
    function_ownership_outside_boundary: boolean;
    unexpected_owned_object: boolean;
  }>(
    `SELECT
       has_schema_privilege($1,$2,'USAGE') AS app_usage,
       has_schema_privilege($1,$2,'CREATE') AS app_create,
       has_schema_privilege($1,$3,'USAGE') AS api_usage,
       has_schema_privilege($1,$3,'CREATE') AS api_create,
       EXISTS(
         SELECT 1
         FROM pg_namespace namespace_row
         JOIN pg_roles owner_role
           ON owner_role.oid=namespace_row.nspowner
         WHERE owner_role.rolname=$1
       ) AS owns_schema,
       EXISTS(
         SELECT 1
         FROM pg_class relation_row
         JOIN pg_roles owner_role
           ON owner_role.oid=relation_row.relowner
         WHERE owner_role.rolname=$1
       ) AS owns_relation,
       EXISTS(
         SELECT 1
         FROM pg_proc function_row
         JOIN pg_namespace namespace_row
           ON namespace_row.oid=function_row.pronamespace
         JOIN pg_roles owner_role
           ON owner_role.oid=function_row.proowner
         WHERE namespace_row.nspname=$2
           AND function_row.prosecdef
           AND owner_role.rolname<>$1
       ) AS application_security_definer_owner_drift,
       EXISTS(
         SELECT 1
         FROM pg_proc function_row
         JOIN pg_namespace namespace_row
           ON namespace_row.oid=function_row.pronamespace
         CROSS JOIN LATERAL aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE namespace_row.nspname=$2
           AND function_row.prosecdef
           AND privilege_row.privilege_type='EXECUTE'
           AND (
             privilege_row.grantee=0
             OR privilege_row.grantee<>function_row.proowner
           )
       ) AS application_security_definer_execute_acl_drift,
       EXISTS(
         SELECT 1
         FROM pg_proc function_row
         JOIN pg_namespace namespace_row
           ON namespace_row.oid=function_row.pronamespace
         JOIN pg_roles owner_role
           ON owner_role.oid=function_row.proowner
         WHERE owner_role.rolname=$1
           AND (
             namespace_row.nspname NOT IN ($2,$3)
             OR (
               namespace_row.nspname=$2
               AND NOT function_row.prosecdef
             )
           )
       ) AS function_ownership_outside_boundary,
       EXISTS(
         SELECT 1
         FROM pg_shdepend ownership_dependency
         JOIN pg_roles owner_role
           ON owner_role.oid=ownership_dependency.refobjid
         WHERE ownership_dependency.refclassid='pg_authid'::regclass
           AND ownership_dependency.deptype='o'
           AND owner_role.rolname=$1
           AND NOT (
             ownership_dependency.dbid=(
               SELECT database_row.oid
               FROM pg_database database_row
               WHERE database_row.datname=current_database()
             )
             AND ownership_dependency.classid='pg_proc'::regclass
             AND EXISTS(
               SELECT 1
               FROM pg_proc function_row
               JOIN pg_namespace namespace_row
                 ON namespace_row.oid=function_row.pronamespace
               WHERE function_row.oid=ownership_dependency.objid
                 AND function_row.proowner=owner_role.oid
                 AND (
                   (
                     namespace_row.nspname=$2
                     AND function_row.prosecdef
                   )
                   OR namespace_row.nspname=$3
                 )
             )
           )
       ) AS unexpected_owned_object`,
    [roleName, applicationSchema, runtimeApiSchema],
  );
  const boundary = result.rows[0];
  return Boolean(
    boundary?.app_usage
    && !boundary.app_create
    && boundary.api_usage
    && !boundary.api_create
    && !boundary.owns_schema
    && !boundary.owns_relation
    && !boundary.application_security_definer_owner_drift
    && !boundary.application_security_definer_execute_acl_drift
    && !boundary.function_ownership_outside_boundary
    && !boundary.unexpected_owned_object
  );
}

async function relationOwners(
  client: ClientBase,
  applicationSchema: string,
) {
  const result = await client.query<{
    relation_name: string;
    owner_name: string;
  }>(
    `SELECT
       relation_row.relname AS relation_name,
       pg_get_userbyid(relation_row.relowner) AS owner_name
     FROM pg_class relation_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=relation_row.relnamespace
     WHERE namespace_row.nspname=$1
       AND relation_row.relname=ANY($2::text[])`,
    [applicationSchema, PROTECTED_COST_RELATIONS],
  );
  return new Map(
    result.rows.map((row) => [row.relation_name, row.owner_name]),
  );
}

async function directDmlPrivileges(
  client: ClientBase,
  applicationSchema: string,
  relationName: string,
) {
  const result = await client.query<{
    insert_allowed: boolean;
    update_allowed: boolean;
    delete_allowed: boolean;
    truncate_allowed: boolean;
  }>(
    `SELECT
       has_table_privilege(
         current_user,relation_row.oid,'INSERT'
       ) AS insert_allowed,
       has_table_privilege(
         current_user,relation_row.oid,'UPDATE'
       ) AS update_allowed,
       has_table_privilege(
         current_user,relation_row.oid,'DELETE'
       ) AS delete_allowed,
       has_table_privilege(
         current_user,relation_row.oid,'TRUNCATE'
       ) AS truncate_allowed
     FROM pg_class relation_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=relation_row.relnamespace
     WHERE namespace_row.nspname=$1
       AND relation_row.relname=$2`,
    [applicationSchema, relationName],
  );
  const row = result.rows[0];
  return Boolean(
    row?.insert_allowed
    || row?.update_allowed
    || row?.delete_allowed
    || row?.truncate_allowed,
  );
}

async function anyRelationPrivileges(
  client: ClientBase,
  applicationSchema: string,
  relationName: string,
) {
  const result = await client.query<{ any_privilege: boolean }>(
    `SELECT (
       has_table_privilege(current_user,relation_row.oid,'SELECT')
       OR has_table_privilege(current_user,relation_row.oid,'INSERT')
       OR has_table_privilege(current_user,relation_row.oid,'UPDATE')
       OR has_table_privilege(current_user,relation_row.oid,'DELETE')
       OR has_table_privilege(current_user,relation_row.oid,'TRUNCATE')
       OR has_table_privilege(current_user,relation_row.oid,'REFERENCES')
       OR has_table_privilege(current_user,relation_row.oid,'TRIGGER')
     ) AS any_privilege
     FROM pg_class relation_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=relation_row.relnamespace
     WHERE namespace_row.nspname=$1
       AND relation_row.relname=$2`,
    [applicationSchema, relationName],
  );
  return result.rows[0]?.any_privilege === true;
}

async function functionExecutable(
  client: ClientBase,
  schema: string,
  functionName: string,
  arguments_: string,
) {
  const identity = `${qualifiedIdentifier(schema, functionName)}(${arguments_})`;
  const result = await client.query<{ executable: boolean }>(
    `SELECT has_function_privilege(
       current_user,to_regprocedure($1),'EXECUTE'
     ) AS executable`,
    [identity],
  );
  return result.rows[0]?.executable === true;
}

async function exactRuntimeApplicationFunctionAllowlist(
  client: ClientBase,
  applicationSchema: string,
) {
  const result = await client.query<{
    function_name: string;
    arguments: string;
  }>(
    `SELECT
       function_row.proname AS function_name,
       oidvectortypes(function_row.proargtypes) AS arguments
     FROM pg_proc function_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=function_row.pronamespace
     WHERE namespace_row.nspname=$1
       AND has_function_privilege(
         current_user,function_row.oid,'EXECUTE'
       )
     ORDER BY function_row.proname,oidvectortypes(function_row.proargtypes)`,
    [applicationSchema],
  );
  const actual = result.rows.map((row) => ({
    name: row.function_name,
    arguments: row.arguments.replaceAll(" ", ""),
  }));
  const expected = RUNTIME_APPLICATION_FUNCTIONS
    .map((function_) => ({ ...function_ }))
    .sort((left, right) => (
      left.name.localeCompare(right.name)
      || left.arguments.localeCompare(right.arguments)
    ));
  return JSON.stringify(actual) === JSON.stringify(expected);
}

async function approvedWrapperIntegrity(
  client: ClientBase,
  runtimeApiSchema: string,
  applicationSchema: string,
  function_: (typeof APPROVED_COST_FUNCTIONS)[number],
  expectedOwner: string,
  expectedGrantee: string,
) {
  const identity = `${
    qualifiedIdentifier(runtimeApiSchema, function_.name)
  }(${function_.arguments})`;
  const expectedSource = `SELECT ${
    qualifiedIdentifier(applicationSchema, function_.name)
  }(${function_.invocation})`;
  const expectedSearchPath =
    `search_path=pg_catalog,${applicationSchema},pg_temp`;
  const result = await client.query<{
    owner_name: string;
    language_name: string;
    security_definer: boolean;
    normalized_search_path: string;
    normalized_source: string;
    public_execute: boolean;
    execute_grantees: string[];
  }>(
    `SELECT
       pg_get_userbyid(function_row.proowner) AS owner_name,
       language_row.lanname AS language_name,
       function_row.prosecdef AS security_definer,
       regexp_replace(
         COALESCE(
           (
             SELECT setting
             FROM unnest(function_row.proconfig) AS setting
             WHERE setting LIKE 'search_path=%'
             LIMIT 1
           ),
           ''
         ),
         '[[:space:]]+',
         '',
         'g'
       ) AS normalized_search_path,
       btrim(
         regexp_replace(
           function_row.prosrc,
           '[[:space:]]+',
           ' ',
           'g'
         )
       ) AS normalized_source,
       EXISTS(
         SELECT 1
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee=0
           AND privilege_row.privilege_type='EXECUTE'
       ) AS public_execute,
       ARRAY(
         SELECT DISTINCT pg_get_userbyid(privilege_row.grantee)
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee<>0
           AND privilege_row.privilege_type='EXECUTE'
         ORDER BY pg_get_userbyid(privilege_row.grantee)
       )::text[] AS execute_grantees
     FROM pg_proc function_row
     JOIN pg_language language_row
       ON language_row.oid=function_row.prolang
     WHERE function_row.oid=to_regprocedure($1)`,
    [identity],
  );
  const row = result.rows[0];
  return Boolean(
    row
    && row.owner_name === expectedOwner
    && row.language_name === "sql"
    && row.security_definer
    && row.normalized_search_path === expectedSearchPath
    && row.normalized_source === expectedSource
    && !row.public_execute
    && JSON.stringify(row.execute_grantees)
      === JSON.stringify([expectedGrantee, expectedOwner].sort())
  );
}

async function normalApplicationPrivileges(
  client: ClientBase,
  applicationSchema: string,
) {
  const result = await client.query<{
    relation_name: string;
    select_allowed: boolean;
    insert_allowed: boolean;
    update_allowed: boolean;
  }>(
    `SELECT
       relation_row.relname AS relation_name,
       has_table_privilege(
         current_user,relation_row.oid,'SELECT'
       ) AS select_allowed,
       has_table_privilege(
         current_user,relation_row.oid,'INSERT'
       ) AS insert_allowed,
       has_table_privilege(
         current_user,relation_row.oid,'UPDATE'
       ) AS update_allowed
     FROM pg_class relation_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=relation_row.relnamespace
     WHERE namespace_row.nspname=$1
       AND relation_row.relname=ANY($2::text[])`,
    [
      applicationSchema,
      ["users", "tasks", "runs", "workspace_entitlements"],
    ],
  );
  return (
    result.rows.length === 4
    && result.rows.every((row) => (
      row.select_allowed
      && (
        row.relation_name === "workspace_entitlements"
        || (row.insert_allowed && row.update_allowed)
      )
    ))
  );
}

async function anyApplicationRelationPrivileges(
  client: ClientBase,
  applicationSchema: string,
) {
  const result = await client.query<{ any_privilege: boolean }>(
    `SELECT EXISTS(
       SELECT 1
       FROM pg_class relation_row
       JOIN pg_namespace namespace_row
         ON namespace_row.oid=relation_row.relnamespace
       WHERE namespace_row.nspname=$1
         AND relation_row.relkind IN ('r','p','v','m','f')
         AND (
           has_table_privilege(current_user,relation_row.oid,'SELECT')
           OR has_table_privilege(current_user,relation_row.oid,'INSERT')
           OR has_table_privilege(current_user,relation_row.oid,'UPDATE')
           OR has_table_privilege(current_user,relation_row.oid,'DELETE')
           OR has_table_privilege(current_user,relation_row.oid,'TRUNCATE')
           OR has_table_privilege(current_user,relation_row.oid,'REFERENCES')
           OR has_table_privilege(current_user,relation_row.oid,'TRIGGER')
         )
     ) OR EXISTS(
       SELECT 1
       FROM pg_class relation_row
       JOIN pg_namespace namespace_row
         ON namespace_row.oid=relation_row.relnamespace
       WHERE namespace_row.nspname=$1
         AND relation_row.relkind='S'
         AND (
           has_sequence_privilege(current_user,relation_row.oid,'USAGE')
           OR has_sequence_privilege(current_user,relation_row.oid,'SELECT')
           OR has_sequence_privilege(current_user,relation_row.oid,'UPDATE')
         )
     ) AS any_privilege`,
    [applicationSchema],
  );
  return result.rows[0]?.any_privilege === true;
}

async function anyApplicationFunctionExecute(
  client: ClientBase,
  applicationSchema: string,
) {
  const result = await client.query<{ executable: boolean }>(
    `SELECT EXISTS(
       SELECT 1
       FROM pg_proc function_row
       JOIN pg_namespace namespace_row
         ON namespace_row.oid=function_row.pronamespace
       WHERE namespace_row.nspname=$1
         AND has_function_privilege(
           current_user,function_row.oid,'EXECUTE'
         )
     ) AS executable`,
    [applicationSchema],
  );
  return result.rows[0]?.executable === true;
}

async function entitlementApiWrapperIntegrity(
  client: ClientBase,
  context: Readonly<{
    runtimeApiSchema: string;
    applicationSchema: string;
    functionName: string;
    arguments: string;
    expectedOwner: string;
    expectedGrantee: string;
    expectedSource: string;
  }>,
) {
  const identity = `${
    qualifiedIdentifier(context.runtimeApiSchema, context.functionName)
  }(${context.arguments})`;
  const expectedSearchPath =
    `search_path=pg_catalog,${context.applicationSchema},pg_temp`;
  const result = await client.query<{
    owner_name: string;
    language_name: string;
    security_definer: boolean;
    normalized_search_path: string;
    normalized_source: string;
    public_execute: boolean;
    execute_grantees: string[];
  }>(
    `SELECT
       pg_get_userbyid(function_row.proowner) AS owner_name,
       language_row.lanname AS language_name,
       function_row.prosecdef AS security_definer,
       regexp_replace(
         COALESCE(
           (
             SELECT setting
             FROM unnest(function_row.proconfig) AS setting
             WHERE setting LIKE 'search_path=%'
             LIMIT 1
           ),
           ''
         ),
         '[[:space:]]+',
         '',
         'g'
       ) AS normalized_search_path,
       btrim(regexp_replace(
         function_row.prosrc,
         '[[:space:]]+',
         ' ',
         'g'
       )) AS normalized_source,
       EXISTS(
         SELECT 1
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee=0
           AND privilege_row.privilege_type='EXECUTE'
       ) AS public_execute,
       ARRAY(
         SELECT DISTINCT pg_get_userbyid(privilege_row.grantee)
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee<>0
           AND privilege_row.privilege_type='EXECUTE'
         ORDER BY pg_get_userbyid(privilege_row.grantee)
       )::text[] AS execute_grantees
     FROM pg_proc function_row
     JOIN pg_language language_row
       ON language_row.oid=function_row.prolang
     WHERE function_row.oid=to_regprocedure($1)`,
    [identity],
  );
  const row = result.rows[0];
  return Boolean(
    row
    && row.owner_name === context.expectedOwner
    && row.language_name === "sql"
    && row.security_definer
    && row.normalized_search_path === expectedSearchPath
    && row.normalized_source === context.expectedSource
    && !row.public_execute
    && JSON.stringify(row.execute_grantees)
      === JSON.stringify([
        context.expectedGrantee,
        context.expectedOwner,
      ].sort())
  );
}

async function entitlementIssueWrapperRoles(
  client: ClientBase,
  context: Readonly<{
    runtimeApiSchema: string;
    applicationSchema: string;
    expectedOwner: string;
  }>,
) {
  const definition = ENTITLEMENT_CHALLENGE_FUNCTIONS.issue;
  const identity = `${
    qualifiedIdentifier(context.runtimeApiSchema, definition.publicName)
  }(${definition.arguments})`;
  const result = await client.query<{
    owner_name: string;
    language_name: string;
    security_definer: boolean;
    normalized_search_path: string;
    normalized_source: string;
    public_execute: boolean;
    execute_grantees: string[];
  }>(
    `SELECT
       pg_get_userbyid(function_row.proowner) AS owner_name,
       language_row.lanname AS language_name,
       function_row.prosecdef AS security_definer,
       regexp_replace(
         COALESCE(
           (
             SELECT setting
             FROM unnest(function_row.proconfig) AS setting
             WHERE setting LIKE 'search_path=%'
             LIMIT 1
           ),
           ''
         ),
         '[[:space:]]+',
         '',
         'g'
       ) AS normalized_search_path,
       btrim(regexp_replace(
         function_row.prosrc,
         '[[:space:]]+',
         ' ',
         'g'
       )) AS normalized_source,
       EXISTS(
         SELECT 1
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee=0
           AND privilege_row.privilege_type='EXECUTE'
       ) AS public_execute,
       ARRAY(
         SELECT DISTINCT pg_get_userbyid(privilege_row.grantee)
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee<>0
           AND privilege_row.privilege_type='EXECUTE'
         ORDER BY pg_get_userbyid(privilege_row.grantee)
       )::text[] AS execute_grantees
     FROM pg_proc function_row
     JOIN pg_language language_row
       ON language_row.oid=function_row.prolang
     WHERE function_row.oid=to_regprocedure($1)`,
    [identity],
  );
  const row = result.rows[0];
  const expectedSearchPath =
    `search_path=pg_catalog,${context.applicationSchema},pg_temp`;
  const prefix = `SELECT ${
    qualifiedIdentifier(context.applicationSchema, definition.coreName)
  }(${definition.invocation},'`;
  const suffix = "'::text)";
  if (
    !row
    || row.owner_name !== context.expectedOwner
    || row.language_name !== "sql"
    || !row.security_definer
    || row.normalized_search_path !== expectedSearchPath
    || row.public_execute
    || !row.normalized_source.startsWith(prefix)
    || !row.normalized_source.endsWith(suffix)
  ) {
    return null;
  }
  const boundRole = row.normalized_source.slice(
    prefix.length,
    -suffix.length,
  );
  const executeRoles = row.execute_grantees.filter(
    (roleName) => roleName !== context.expectedOwner,
  );
  const executeRole = executeRoles[0];
  if (
    !SAFE_IDENTIFIER.test(boundRole)
    || !executeRole
    || !SAFE_IDENTIFIER.test(executeRole)
    || executeRoles.length !== 1
    || boundRole === context.expectedOwner
    || executeRole === context.expectedOwner
    || executeRole === boundRole
    || await roleHasMembership(client, boundRole)
    || await roleHasMembership(client, executeRole)
    || JSON.stringify(row.execute_grantees)
      !== JSON.stringify([executeRole, context.expectedOwner].sort())
  ) {
    return null;
  }
  const roles = await client.query<{
    role_name: string;
    login: boolean;
    superuser: boolean;
    create_role: boolean;
    create_db: boolean;
    replication: boolean;
    bypass_rls: boolean;
    inherit: boolean;
  }>(
    `SELECT
       rolname AS role_name,
       rolcanlogin AS login,
       rolsuper AS superuser,
       rolcreaterole AS create_role,
       rolcreatedb AS create_db,
       rolreplication AS replication,
       rolbypassrls AS bypass_rls,
       rolinherit AS inherit
     FROM pg_roles
     WHERE rolname=ANY($1::text[])`,
    [[boundRole, executeRole]],
  );
  if (
    roles.rowCount !== 2
    || roles.rows.some((role) => (
      !role.login
      || role.superuser
      || role.create_role
      || role.create_db
      || role.replication
      || role.bypass_rls
      || role.inherit
    ))
  ) {
    return null;
  }
  return Object.freeze({
    boundAdminRole: boundRole,
    executeRole,
  });
}

async function exactRuntimeApiFunctionSet(
  client: ClientBase,
  context: Readonly<{
    runtimeApiSchema: string;
    functionOwnerRole: string;
    runtimeRole: string;
    entitlementAdminRole: string;
  }>,
) {
  const result = await client.query<{
    function_name: string;
    arguments: string;
    owner_name: string;
    security_definer: boolean;
    public_execute: boolean;
    execute_grantees: string[];
  }>(
    `SELECT
       function_row.proname AS function_name,
       oidvectortypes(function_row.proargtypes) AS arguments,
       pg_get_userbyid(function_row.proowner) AS owner_name,
       function_row.prosecdef AS security_definer,
       EXISTS(
         SELECT 1
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee=0
           AND privilege_row.privilege_type='EXECUTE'
       ) AS public_execute,
       ARRAY(
         SELECT DISTINCT pg_get_userbyid(privilege_row.grantee)
         FROM aclexplode(
           COALESCE(
             function_row.proacl,
             acldefault('f',function_row.proowner)
           )
         ) privilege_row
         WHERE privilege_row.grantee<>0
           AND privilege_row.privilege_type='EXECUTE'
         ORDER BY pg_get_userbyid(privilege_row.grantee)
       )::text[] AS execute_grantees
     FROM pg_proc function_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=function_row.pronamespace
     WHERE namespace_row.nspname=$1
     ORDER BY function_row.proname,oidvectortypes(function_row.proargtypes)`,
    [context.runtimeApiSchema],
  );
  const expected = [
    ...APPROVED_COST_FUNCTIONS.map((function_) => ({
      name: function_.name,
      arguments: function_.arguments,
      grantee: context.runtimeRole,
    })),
    {
      name: ENTITLEMENT_CHALLENGE_FUNCTIONS.issue.publicName,
      arguments: ENTITLEMENT_CHALLENGE_FUNCTIONS.issue.arguments,
      grantee: context.runtimeRole,
    },
    {
      name: ENTITLEMENT_CHALLENGE_FUNCTIONS.plan.publicName,
      arguments: ENTITLEMENT_CHALLENGE_FUNCTIONS.plan.arguments,
      grantee: context.entitlementAdminRole,
    },
    {
      name: ENTITLEMENT_CHALLENGE_FUNCTIONS.apply.publicName,
      arguments: ENTITLEMENT_CHALLENGE_FUNCTIONS.apply.arguments,
      grantee: context.entitlementAdminRole,
    },
  ];
  if (result.rows.length !== expected.length) return false;
  const actualByIdentity = new Map(
    result.rows.map((row) => [
      `${row.function_name}(${row.arguments.replaceAll(" ", "")})`,
      row,
    ]),
  );
  return expected.every((entry) => {
    const row = actualByIdentity.get(
      `${entry.name}(${entry.arguments})`,
    );
    return Boolean(
      row
      && row.owner_name === context.functionOwnerRole
      && row.security_definer
      && !row.public_execute
      && JSON.stringify(row.execute_grantees)
        === JSON.stringify([
          context.functionOwnerRole,
          entry.grantee,
        ].sort())
    );
  });
}

async function runtimeBoundaryStep<T>(
  code: string,
  work: () => Promise<T>,
) {
  try {
    return await work();
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(`postgres_runtime_boundary_${code}_failed`);
  }
}

export async function assertPostgresRuntimeRoleBoundary(
  client: ClientBase,
  context: Partial<RuntimeBoundaryContext> = {},
): Promise<RuntimeRoleBoundaryReceipt> {
  const applicationSchema = context.applicationSchema
    ? safeIdentifier(
        context.applicationSchema,
        "postgres_schema_invalid",
      )
    : await selectedApplicationSchema(client);
  const runtimeApiSchema = selectedRuntimeApiSchema(context.runtimeApiSchema);
  const expectedRole = context.runtimeRole
    ? selectedRuntimeRole(context.runtimeRole, true)
    : selectedRuntimeRole(undefined, false);
  const principal = await runtimeBoundaryStep(
    "principal",
    () => currentDatabasePrincipal(client),
  );
  if (principal.superuser) {
    throw new SchemaReadinessError("postgres_runtime_superuser_forbidden");
  }
  if (principal.bypass_rls) {
    throw new SchemaReadinessError("postgres_runtime_bypassrls_forbidden");
  }
  if (
    principal.create_role
    || principal.create_db
    || principal.replication
    || principal.inherit
  ) {
    throw new SchemaReadinessError("postgres_runtime_role_privileged");
  }
  if (expectedRole && principal.role_name !== expectedRole) {
    throw new SchemaReadinessError("postgres_runtime_role_mismatch");
  }
  if (
    await runtimeBoundaryStep(
      "role_membership",
      () => roleHasMembership(client, principal.role_name),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_set_role_membership_forbidden",
    );
  }
  if (
    !(await runtimeBoundaryStep(
      "application_function_allowlist",
      () => exactRuntimeApplicationFunctionAllowlist(
        client,
        applicationSchema,
      ),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_application_function_allowlist_invalid",
    );
  }

  const owners = await runtimeBoundaryStep(
    "owners",
    () => relationOwners(client, applicationSchema),
  );
  if (
    PROTECTED_COST_RELATIONS.some(
      (relation) => !owners.has(relation),
    )
  ) {
    throw new SchemaReadinessError("postgres_protected_relation_missing");
  }
  if (
    PROTECTED_COST_RELATIONS.some(
      (relation) => owners.get(relation) === principal.role_name,
    )
  ) {
    throw new SchemaReadinessError("postgres_runtime_relation_owner_forbidden");
  }
  const protectedOwners = new Set(owners.values());
  if (protectedOwners.size !== 1) {
    throw new SchemaReadinessError(
      "postgres_protected_relation_owner_drift",
    );
  }
  const protectedOwner = [...protectedOwners][0];
  if (!protectedOwner) {
    throw new SchemaReadinessError("postgres_protected_relation_owner_missing");
  }
  const functionOwner = derivedPostgresFunctionOwnerRole(
    applicationSchema,
    runtimeApiSchema,
  );
  if (
    functionOwner === protectedOwner
    || functionOwner === principal.role_name
    || !(await runtimeBoundaryStep(
      "function_owner",
      () => restrictedFunctionOwnerBoundary(
        client,
        functionOwner,
        applicationSchema,
        runtimeApiSchema,
      ),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_function_owner_restriction_invalid",
    );
  }
  if (
    await runtimeBoundaryStep(
      "reservation_privileges",
      () => directDmlPrivileges(
        client,
        applicationSchema,
        "run_cost_reservations",
      ),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_reservation_dml_forbidden",
    );
  }
  if (
    await runtimeBoundaryStep(
      "entitlement_privileges",
      () => directDmlPrivileges(
        client,
        applicationSchema,
        "workspace_entitlements",
      ),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_entitlement_dml_forbidden",
    );
  }
  if (
    await runtimeBoundaryStep(
      "entitlement_challenge_privileges",
      () => anyRelationPrivileges(
        client,
        applicationSchema,
        "entitlement_admin_challenges",
      ),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_entitlement_challenge_access_forbidden",
    );
  }
  if (
    await runtimeBoundaryStep(
      "ledger_privileges",
      () => directDmlPrivileges(
        client,
        applicationSchema,
        "agentops_schema_migrations",
      ),
    )
  ) {
    throw new SchemaReadinessError("postgres_runtime_ledger_dml_forbidden");
  }

  for (const function_ of APPROVED_COST_FUNCTIONS) {
    if (
      !(await runtimeBoundaryStep(
        "approved_function",
        () => functionExecutable(
          client,
          runtimeApiSchema,
          function_.name,
          function_.arguments,
        ),
      ))
    ) {
      throw new SchemaReadinessError(
        "postgres_runtime_cost_function_missing",
      );
    }
    if (
      !(await runtimeBoundaryStep(
        "approved_function_integrity",
        () => approvedWrapperIntegrity(
          client,
          runtimeApiSchema,
          applicationSchema,
          function_,
          functionOwner,
          principal.role_name,
        ),
      ))
    ) {
      throw new SchemaReadinessError(
        "postgres_runtime_cost_function_integrity_invalid",
      );
    }
    if (
      await runtimeBoundaryStep(
        "original_function",
        () => functionExecutable(
          client,
          applicationSchema,
          function_.name,
          function_.arguments,
        ),
      )
    ) {
      throw new SchemaReadinessError(
        "postgres_runtime_original_cost_function_executable",
      );
    }
  }
  if (
    !(await runtimeBoundaryStep(
      "entitlement_challenge_issue_function",
      () => functionExecutable(
        client,
        runtimeApiSchema,
        ENTITLEMENT_CHALLENGE_FUNCTIONS.issue.publicName,
        ENTITLEMENT_CHALLENGE_FUNCTIONS.issue.arguments,
      ),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_entitlement_challenge_issue_missing",
    );
  }
  const issueRoles = await runtimeBoundaryStep(
    "entitlement_challenge_issue_roles",
    () => entitlementIssueWrapperRoles(client, {
      runtimeApiSchema,
      applicationSchema,
      expectedOwner: functionOwner,
    }),
  );
  if (
    !issueRoles
    || issueRoles.executeRole !== principal.role_name
    || issueRoles.boundAdminRole === principal.role_name
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_entitlement_challenge_issue_integrity_invalid",
    );
  }
  if (
    !(await runtimeBoundaryStep(
      "runtime_api_function_set",
      () => exactRuntimeApiFunctionSet(client, {
        runtimeApiSchema,
        functionOwnerRole: functionOwner,
        runtimeRole: principal.role_name,
        entitlementAdminRole: issueRoles.boundAdminRole,
      }),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_api_function_set_invalid",
    );
  }
  for (const function_ of [
    ENTITLEMENT_CHALLENGE_FUNCTIONS.plan,
    ENTITLEMENT_CHALLENGE_FUNCTIONS.apply,
  ]) {
    if (
      await runtimeBoundaryStep(
        "entitlement_admin_function",
        () => functionExecutable(
          client,
          runtimeApiSchema,
          function_.publicName,
          function_.arguments,
        ),
      )
    ) {
      throw new SchemaReadinessError(
        "postgres_runtime_entitlement_admin_function_executable",
      );
    }
  }
  if (
    !(await runtimeBoundaryStep(
      "application_privileges",
      () => normalApplicationPrivileges(client, applicationSchema),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_runtime_application_privileges_missing",
    );
  }

  return {
    contract: "agentops_postgres_runtime_role_boundary_v1",
    ok: true,
    protected_relation_owner_excluded: true,
    reservation_direct_dml_forbidden: true,
    entitlement_direct_dml_forbidden: true,
    entitlement_challenge_direct_access_forbidden: true,
    migration_ledger_direct_dml_forbidden: true,
    approved_cost_functions_executable: true,
    approved_cost_function_integrity_verified: true,
    application_function_execute_allowlist_verified: true,
    original_cost_functions_not_executable: true,
    entitlement_challenge_issue_executable: true,
    entitlement_challenge_issue_integrity_verified: true,
    entitlement_challenge_admin_functions_not_executable: true,
    function_owner_restricted: true,
    runtime_api_function_set_verified: true,
    normal_application_operations_allowed: true,
    entitlement_read_allowed: true,
    superuser_forbidden: true,
    bypass_rls_forbidden: true,
    set_role_membership_forbidden: true,
    pg_temp_shadowing_forbidden: true,
    runtime_role_omitted: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  };
}

export async function assertPostgresEntitlementAdminRoleBoundary(
  client: ClientBase,
  context: Readonly<{
    applicationSchema?: string;
    runtimeApiSchema?: string;
    entitlementAdminRole?: string;
  }> = {},
): Promise<EntitlementAdminRoleBoundaryReceipt> {
  const applicationSchema = context.applicationSchema
    ? safeIdentifier(context.applicationSchema, "postgres_schema_invalid")
    : await selectedApplicationSchema(client);
  const runtimeApiSchema = selectedRuntimeApiSchema(context.runtimeApiSchema);
  const expectedRole = context.entitlementAdminRole
    ? selectedEntitlementAdminRole(context.entitlementAdminRole, true)
    : selectedEntitlementAdminRole(undefined, false);
  const principal = await runtimeBoundaryStep(
    "entitlement_admin_principal",
    () => currentDatabasePrincipal(client),
  );
  if (principal.superuser) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_superuser_forbidden",
    );
  }
  if (principal.bypass_rls) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_bypassrls_forbidden",
    );
  }
  if (
    principal.create_role
    || principal.create_db
    || principal.replication
    || principal.inherit
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_role_privileged",
    );
  }
  if (expectedRole && principal.role_name !== expectedRole) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_role_mismatch",
    );
  }
  if (
    await runtimeBoundaryStep(
      "entitlement_admin_role_membership",
      () => roleHasMembership(client, principal.role_name),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_set_role_membership_forbidden",
    );
  }
  const schemaPrivileges = await runtimeBoundaryStep(
    "entitlement_admin_schema_privileges",
    () => client.query<{
      app_usage: boolean;
      app_create: boolean;
      api_usage: boolean;
      api_create: boolean;
    }>(
      `SELECT
         has_schema_privilege(current_user,$1,'USAGE') AS app_usage,
         has_schema_privilege(current_user,$1,'CREATE') AS app_create,
         has_schema_privilege(current_user,$2,'USAGE') AS api_usage,
         has_schema_privilege(current_user,$2,'CREATE') AS api_create`,
      [applicationSchema, runtimeApiSchema],
    ),
  );
  const schemaRow = schemaPrivileges.rows[0];
  if (
    schemaRow?.app_usage
    || schemaRow?.app_create
    || !schemaRow?.api_usage
    || schemaRow?.api_create
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_schema_privileges_invalid",
    );
  }
  if (
    await runtimeBoundaryStep(
      "entitlement_admin_relation_privileges",
      () => anyApplicationRelationPrivileges(client, applicationSchema),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_relation_access_forbidden",
    );
  }
  if (
    await runtimeBoundaryStep(
      "entitlement_admin_application_functions",
      () => anyApplicationFunctionExecute(client, applicationSchema),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_application_function_forbidden",
    );
  }
  const owners = await runtimeBoundaryStep(
    "entitlement_admin_owners",
    () => relationOwners(client, applicationSchema),
  );
  const protectedOwners = new Set(owners.values());
  if (
    PROTECTED_COST_RELATIONS.some((relation) => !owners.has(relation))
    || protectedOwners.size !== 1
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_protected_owner_invalid",
    );
  }
  const relationOwner = [...protectedOwners][0];
  if (!relationOwner || relationOwner === principal.role_name) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_relation_owner_forbidden",
    );
  }
  const functionOwner = derivedPostgresFunctionOwnerRole(
    applicationSchema,
    runtimeApiSchema,
  );
  if (
    functionOwner === relationOwner
    || functionOwner === principal.role_name
    || !(await runtimeBoundaryStep(
      "entitlement_admin_function_owner",
      () => restrictedFunctionOwnerBoundary(
        client,
        functionOwner,
        applicationSchema,
        runtimeApiSchema,
      ),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_function_owner_restriction_invalid",
    );
  }
  const issue = ENTITLEMENT_CHALLENGE_FUNCTIONS.issue;
  if (
    await runtimeBoundaryStep(
      "entitlement_admin_issue_function",
      () => functionExecutable(
        client,
        runtimeApiSchema,
        issue.publicName,
        issue.arguments,
      ),
    )
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_issue_function_executable",
    );
  }
  const issueRoles = await runtimeBoundaryStep(
    "entitlement_admin_issue_integrity",
    () => entitlementIssueWrapperRoles(client, {
      runtimeApiSchema,
      applicationSchema,
      expectedOwner: functionOwner,
    }),
  );
  if (
    !issueRoles
    || issueRoles.boundAdminRole !== principal.role_name
    || issueRoles.executeRole === principal.role_name
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_issue_function_integrity_invalid",
    );
  }
  if (
    !(await runtimeBoundaryStep(
      "entitlement_admin_api_function_set",
      () => exactRuntimeApiFunctionSet(client, {
        runtimeApiSchema,
        functionOwnerRole: functionOwner,
        runtimeRole: issueRoles.executeRole,
        entitlementAdminRole: principal.role_name,
      }),
    ))
  ) {
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_api_function_set_invalid",
    );
  }
  const definitions = [
    {
      definition: issue,
      expectedSource: `SELECT ${
        qualifiedIdentifier(applicationSchema, issue.coreName)
      }(${issue.invocation},'${principal.role_name}'::text)`,
      shouldExecute: false,
    },
    ...([
      ENTITLEMENT_CHALLENGE_FUNCTIONS.plan,
      ENTITLEMENT_CHALLENGE_FUNCTIONS.apply,
    ].map((definition) => ({
      definition,
      expectedSource: `SELECT ${
        qualifiedIdentifier(applicationSchema, definition.coreName)
      }(${definition.invocation})`,
      shouldExecute: true,
    }))),
  ];
  for (const entry of definitions) {
    const executable = await runtimeBoundaryStep(
      "entitlement_admin_api_function",
      () => functionExecutable(
        client,
        runtimeApiSchema,
        entry.definition.publicName,
        entry.definition.arguments,
      ),
    );
    if (executable !== entry.shouldExecute) {
      throw new SchemaReadinessError(
        "postgres_entitlement_admin_api_function_privileges_invalid",
      );
    }
    if (
      !(await runtimeBoundaryStep(
        "entitlement_admin_api_integrity",
        () => entitlementApiWrapperIntegrity(client, {
          runtimeApiSchema,
          applicationSchema,
          functionName: entry.definition.publicName,
          arguments: entry.definition.arguments,
          expectedOwner: functionOwner,
          expectedGrantee: entry.shouldExecute
            ? principal.role_name
            : issueRoles.executeRole,
          expectedSource: entry.expectedSource,
        }),
      ))
    ) {
      throw new SchemaReadinessError(
        "postgres_entitlement_admin_api_function_integrity_invalid",
      );
    }
  }
  return {
    contract: "agentops_postgres_entitlement_admin_role_boundary_v1",
    ok: true,
    application_schema_access_forbidden: true,
    application_relation_access_forbidden: true,
    application_function_execute_forbidden: true,
    runtime_api_usage_allowed: true,
    plan_apply_executable: true,
    issue_not_executable: true,
    wrapper_integrity_verified: true,
    function_owner_restricted: true,
    runtime_api_function_set_verified: true,
    superuser_forbidden: true,
    bypass_rls_forbidden: true,
    set_role_membership_forbidden: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  };
}

async function assertMigrationAuthority(
  client: ClientBase,
  applicationSchema: string,
) {
  const principal = await currentDatabasePrincipal(client);
  const owners = await relationOwners(client, applicationSchema);
  if (
    PROTECTED_COST_RELATIONS.some(
      (relation) => owners.get(relation) !== principal.role_name,
    )
  ) {
    throw new SchemaReadinessError("postgres_migration_role_not_owner");
  }
  return principal;
}

async function assertExistingMigrationAuthority(
  client: ClientBase,
  applicationSchema: string,
) {
  const principal = await currentDatabasePrincipal(client);
  const owners = await relationOwners(client, applicationSchema);
  if (
    [...owners.values()].some((owner) => owner !== principal.role_name)
  ) {
    throw new SchemaReadinessError("postgres_migration_role_not_owner");
  }
  return principal;
}

async function ensureRuntimeRole(
  client: ClientBase,
  migratorRole: string,
  runtimeRole: string,
  runtimePassword: string,
) {
  if (runtimeRole === migratorRole) {
    throw new SchemaReadinessError("postgres_roles_must_be_distinct");
  }
  if (!runtimePassword) {
    throw new SchemaReadinessError("postgres_runtime_password_required");
  }
  const role = await client.query<{
    exists: boolean;
    superuser: boolean | null;
    create_role: boolean | null;
    create_db: boolean | null;
    replication: boolean | null;
    bypass_rls: boolean | null;
    any_role_membership: boolean;
  }>(
    `SELECT
       EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1) AS exists,
       (SELECT rolsuper FROM pg_roles WHERE rolname=$1) AS superuser,
       (SELECT rolcreaterole FROM pg_roles WHERE rolname=$1) AS create_role,
       (SELECT rolcreatedb FROM pg_roles WHERE rolname=$1) AS create_db,
       (SELECT rolreplication FROM pg_roles WHERE rolname=$1) AS replication,
       (SELECT rolbypassrls FROM pg_roles WHERE rolname=$1) AS bypass_rls,
       EXISTS(
         SELECT 1
         FROM pg_auth_members membership
         JOIN pg_roles member_role ON member_role.oid=membership.member
         JOIN pg_roles granted_role ON granted_role.oid=membership.roleid
         WHERE member_role.rolname=$1
            OR granted_role.rolname=$1
       ) AS any_role_membership`,
    [runtimeRole],
  );
  const existing = role.rows[0];
  if (
    existing?.exists
    && (
      existing.superuser
      || existing.create_role
      || existing.create_db
      || existing.replication
      || existing.bypass_rls
      || existing.any_role_membership
    )
  ) {
    throw new SchemaReadinessError("postgres_runtime_role_privileged");
  }
  const statement = await client.query<{ statement: string }>(
    `SELECT format(
       CASE WHEN $3::boolean
         THEN 'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L'
         ELSE 'CREATE ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L'
       END,
       $1::text,$2::text
     ) AS statement`,
    [runtimeRole, runtimePassword, existing?.exists === true],
  );
  const sql = statement.rows[0]?.statement;
  if (!sql) throw new SchemaReadinessError("postgres_runtime_role_provision_failed");
  await client.query(sql);
}

async function ensureFunctionOwnerRole(
  client: ClientBase,
  migratorRole: string,
  functionOwnerRole: string,
  excludedRoles: readonly string[],
) {
  if (
    functionOwnerRole === migratorRole
    || excludedRoles.includes(functionOwnerRole)
  ) {
    throw new SchemaReadinessError("postgres_roles_must_be_distinct");
  }
  const role = await client.query<{ exists: boolean }>(
    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1) AS exists",
    [functionOwnerRole],
  );
  if (role.rows[0]?.exists) {
    if (!(await restrictedFunctionOwnerRole(client, functionOwnerRole))) {
      throw new SchemaReadinessError(
        "postgres_function_owner_restriction_invalid",
      );
    }
  } else {
    const statement = await client.query<{ statement: string }>(
      `SELECT format(
         'CREATE ROLE %I WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
         $1::text
       ) AS statement`,
      [functionOwnerRole],
    );
    const sql = statement.rows[0]?.statement;
    if (!sql) {
      throw new SchemaReadinessError(
        "postgres_function_owner_provision_failed",
      );
    }
    await client.query(sql);
  }
  const clearPassword = await client.query<{ statement: string }>(
    "SELECT format('ALTER ROLE %I PASSWORD NULL',$1::text) AS statement",
    [functionOwnerRole],
  );
  const clearPasswordSql = clearPassword.rows[0]?.statement;
  if (!clearPasswordSql) {
    throw new SchemaReadinessError(
      "postgres_function_owner_provision_failed",
    );
  }
  await client.query(clearPasswordSql);
}

async function ensureRuntimeApiSchema(
  client: ClientBase,
  runtimeApiSchema: string,
  migratorRole: string,
) {
  const api = quotedIdentifier(runtimeApiSchema);
  const migrator = quotedIdentifier(migratorRole);
  await client.query(
    `CREATE SCHEMA IF NOT EXISTS ${api} AUTHORIZATION ${migrator}`,
  );
  const apiOwner = await client.query<{ owner_name: string }>(
    `SELECT pg_get_userbyid(namespace_row.nspowner) AS owner_name
     FROM pg_namespace namespace_row
     WHERE namespace_row.nspname=$1`,
    [runtimeApiSchema],
  );
  if (apiOwner.rows[0]?.owner_name !== migratorRole) {
    throw new SchemaReadinessError("postgres_runtime_api_owner_mismatch");
  }
}

async function prepareFunctionOwner(
  client: ClientBase,
  applicationSchema: string,
  runtimeApiSchema: string,
  migratorRole: string,
  functionOwnerRole: string,
) {
  const app = quotedIdentifier(applicationSchema);
  const api = quotedIdentifier(runtimeApiSchema);
  const migrator = quotedIdentifier(migratorRole);
  const functionOwner = quotedIdentifier(functionOwnerRole);
  await client.query(`GRANT ${functionOwner} TO ${migrator}`);
  await client.query(
    `GRANT USAGE,CREATE ON SCHEMA ${app},${api} TO ${functionOwner}`,
  );
  await client.query(
    `GRANT SELECT,INSERT,UPDATE,DELETE
       ON ALL TABLES IN SCHEMA ${app}
       TO ${functionOwner}`,
  );
  await client.query(
    `GRANT USAGE,SELECT,UPDATE
       ON ALL SEQUENCES IN SCHEMA ${app}
       TO ${functionOwner}`,
  );
  await client.query(
    `GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ${app}
       TO ${functionOwner}`,
  );
  // The restricted owner must not retain a pg_default_acl ownership object.
  await client.query(
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${functionOwner}
       GRANT EXECUTE ON FUNCTIONS TO PUBLIC`,
  );
}

async function transferApplicationSecurityDefiners(
  client: ClientBase,
  applicationSchema: string,
  functionOwnerRole: string,
) {
  const statements = await client.query<{ statement: string }>(
    `SELECT format(
       'ALTER FUNCTION %I.%I(%s) OWNER TO %I',
       namespace_row.nspname,
       function_row.proname,
       pg_get_function_identity_arguments(function_row.oid),
       $2::text
     ) AS statement
     FROM pg_proc function_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=function_row.pronamespace
     WHERE namespace_row.nspname=$1
       AND function_row.prosecdef
     ORDER BY function_row.proname,function_row.oid`,
    [applicationSchema, functionOwnerRole],
  );
  for (const row of statements.rows) {
    await client.query(row.statement);
  }
}

async function restrictApplicationSecurityDefinerExecute(
  client: ClientBase,
  applicationSchema: string,
) {
  const statements = await client.query<{ statement: string }>(
    `SELECT DISTINCT format(
       'REVOKE EXECUTE ON FUNCTION %I.%I(%s) FROM %I',
       namespace_row.nspname,
       function_row.proname,
       pg_get_function_identity_arguments(function_row.oid),
       grantee_role.rolname
     ) AS statement
     FROM pg_proc function_row
     JOIN pg_namespace namespace_row
       ON namespace_row.oid=function_row.pronamespace
     CROSS JOIN LATERAL aclexplode(
       COALESCE(
         function_row.proacl,
         acldefault('f',function_row.proowner)
       )
     ) privilege_row
     JOIN pg_roles grantee_role
       ON grantee_role.oid=privilege_row.grantee
     WHERE namespace_row.nspname=$1
       AND function_row.prosecdef
       AND privilege_row.privilege_type='EXECUTE'
       AND privilege_row.grantee<>function_row.proowner
     ORDER BY statement`,
    [applicationSchema],
  );
  const app = quotedIdentifier(applicationSchema);
  await client.query(
    `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA ${app} FROM PUBLIC`,
  );
  for (const row of statements.rows) {
    await client.query(row.statement);
  }
}

async function finalizeFunctionOwner(
  client: ClientBase,
  applicationSchema: string,
  runtimeApiSchema: string,
  migratorRole: string,
  functionOwnerRole: string,
) {
  const app = quotedIdentifier(applicationSchema);
  const api = quotedIdentifier(runtimeApiSchema);
  const migrator = quotedIdentifier(migratorRole);
  const functionOwner = quotedIdentifier(functionOwnerRole);
  await client.query(
    `REVOKE CREATE ON SCHEMA ${app},${api} FROM ${functionOwner}`,
  );
  await client.query(`REVOKE ${functionOwner} FROM ${migrator}`);
  if (!(await restrictedFunctionOwnerRole(client, functionOwnerRole))) {
    throw new SchemaReadinessError(
      "postgres_function_owner_restriction_invalid",
    );
  }
}

function resolvedRoleProvisioningContext(
  options: SchemaCommandOptions,
  applicationSchema: string,
  runtimeApiSchema: string,
): ResolvedRoleProvisioningContext | null {
  const provisionRoleBoundary = options.provisionRoleBoundary ?? Boolean(
    options.runtimeRole
    || options.entitlementAdminRole
    || (!options.connectionString && isProductionDeployment()),
  );
  if (!provisionRoleBoundary) return null;

  const runtimeRole = selectedRuntimeRole(options.runtimeRole, true);
  let runtimePassword = String(options.runtimePassword || "");
  if (!runtimePassword) {
    try {
      runtimePassword = secretEnvironmentValue(
        "AGENTOPS_POSTGRES_RUNTIME_PASSWORD",
      );
    } catch {
      throw new SchemaReadinessError("postgres_runtime_password_required");
    }
  }
  const entitlementAdminRole = selectedEntitlementAdminRole(
    options.entitlementAdminRole,
    isProductionDeployment(),
  );
  let entitlementAdminPassword = String(
    options.entitlementAdminPassword || "",
  );
  if (!entitlementAdminPassword) {
    try {
      entitlementAdminPassword = secretEnvironmentValue(
        "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD",
      );
    } catch {
      throw new SchemaReadinessError(
        "postgres_entitlement_admin_password_required",
      );
    }
  }
  return {
    applicationSchema,
    runtimeApiSchema,
    runtimeRole,
    runtimePassword,
    entitlementAdminRole,
    entitlementAdminPassword,
  };
}

async function prepareMigrationFunctionOwner(
  client: ClientBase,
  context: ResolvedRoleProvisioningContext,
): Promise<PreparedMigrationFunctionOwner> {
  const principal = await assertExistingMigrationAuthority(
    client,
    context.applicationSchema,
  );
  const functionOwnerRole = derivedPostgresFunctionOwnerRole(
    context.applicationSchema,
    context.runtimeApiSchema,
  );
  await ensureFunctionOwnerRole(
    client,
    principal.role_name,
    functionOwnerRole,
    [context.runtimeRole, context.entitlementAdminRole],
  );
  await ensureRuntimeApiSchema(
    client,
    context.runtimeApiSchema,
    principal.role_name,
  );
  await prepareFunctionOwner(
    client,
    context.applicationSchema,
    context.runtimeApiSchema,
    principal.role_name,
    functionOwnerRole,
  );
  return {
    migratorRole: principal.role_name,
    functionOwnerRole,
  };
}

async function installRuntimeCostApi(
  client: ClientBase,
  applicationSchema: string,
  runtimeApiSchema: string,
  migratorRole: string,
  runtimeRole: string,
  functionOwnerRole: string,
) {
  const app = quotedIdentifier(applicationSchema);
  const api = quotedIdentifier(runtimeApiSchema);
  const runtime = quotedIdentifier(runtimeRole);
  const migrator = quotedIdentifier(migratorRole);

  const functionOwner = quotedIdentifier(functionOwnerRole);

  await client.query(`REVOKE ALL ON SCHEMA ${api} FROM PUBLIC`);
  await client.query(`REVOKE CREATE,USAGE ON SCHEMA ${app} FROM PUBLIC`);
  await client.query(
    `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA ${app} FROM PUBLIC`,
  );
  await client.query(
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${migrator}
       REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`,
  );
  for (const function_ of RUNTIME_APPLICATION_FUNCTIONS) {
    await client.query(
      `GRANT EXECUTE ON FUNCTION ${
        app
      }.${quotedIdentifier(function_.name)}(${function_.arguments})
       TO ${runtime}`,
    );
  }
  await client.query(`REVOKE CREATE ON SCHEMA ${app} FROM ${runtime}`);
  await client.query(`GRANT USAGE ON SCHEMA ${app},${api} TO ${runtime}`);
  await client.query(
    `GRANT SELECT,INSERT,UPDATE,DELETE
       ON ALL TABLES IN SCHEMA ${app}
       TO ${runtime}`,
  );
  await client.query(
    `GRANT USAGE,SELECT
       ON ALL SEQUENCES IN SCHEMA ${app}
       TO ${runtime}`,
  );
  await client.query(
    `REVOKE INSERT,UPDATE,DELETE,TRUNCATE
       ON ${app}."run_cost_reservations",
          ${app}."agentops_schema_migrations",
          ${app}."workspace_entitlements"
       FROM ${runtime}`,
  );
  await client.query(
    `REVOKE ALL PRIVILEGES
       ON ${app}."entitlement_admin_challenges"
       FROM ${runtime}`,
  );

  for (const function_ of APPROVED_COST_FUNCTIONS) {
    const original = `${app}.${quotedIdentifier(function_.name)}`;
    const wrapper = `${api}.${quotedIdentifier(function_.name)}`;
    const returnType = function_.returnType === "run_cost_reservations"
      ? `${app}."run_cost_reservations"`
      : function_.returnType;
    await client.query(
      `CREATE OR REPLACE FUNCTION ${wrapper}(${function_.parameters})
       RETURNS ${returnType}
       LANGUAGE sql
       SECURITY DEFINER
       SET search_path=pg_catalog,${app},pg_temp
       AS $agentops_runtime_api$
         SELECT ${original}(${function_.invocation})
       $agentops_runtime_api$`,
    );
    await client.query(
      `ALTER FUNCTION ${wrapper}(${function_.arguments})
       OWNER TO ${functionOwner}`,
    );
    await client.query(
      `REVOKE ALL ON FUNCTION ${wrapper}(${function_.arguments})
       FROM PUBLIC`,
    );
    await client.query(
      `REVOKE ALL ON FUNCTION ${original}(${function_.arguments})
       FROM PUBLIC,${runtime}`,
    );
    await client.query(
      `GRANT EXECUTE ON FUNCTION ${wrapper}(${function_.arguments})
       TO ${runtime}`,
    );
  }
}

async function installEntitlementAdminPrivileges(
  client: ClientBase,
  applicationSchema: string,
  runtimeApiSchema: string,
  entitlementAdminRole: string,
) {
  const app = quotedIdentifier(applicationSchema);
  const api = quotedIdentifier(runtimeApiSchema);
  const admin = quotedIdentifier(entitlementAdminRole);
  await client.query(`REVOKE ALL ON SCHEMA ${app} FROM ${admin}`);
  await client.query(`GRANT USAGE ON SCHEMA ${api} TO ${admin}`);
  await client.query(
    `REVOKE ALL PRIVILEGES
       ON ALL TABLES IN SCHEMA ${app}
       FROM ${admin}`,
  );
  await client.query(
    `REVOKE ALL PRIVILEGES
       ON ALL SEQUENCES IN SCHEMA ${app}
       FROM ${admin}`,
  );
  await client.query(
    `REVOKE ALL PRIVILEGES
       ON ALL FUNCTIONS IN SCHEMA ${app}
       FROM ${admin}`,
  );
}

async function installEntitlementChallengeApi(
  client: ClientBase,
  applicationSchema: string,
  runtimeApiSchema: string,
  runtimeRole: string,
  entitlementAdminRole: string,
  functionOwnerRole: string,
) {
  const app = quotedIdentifier(applicationSchema);
  const api = quotedIdentifier(runtimeApiSchema);
  const runtime = quotedIdentifier(runtimeRole);
  const admin = quotedIdentifier(entitlementAdminRole);
  const functionOwner = quotedIdentifier(functionOwnerRole);
  const definitions = [
    {
      ...ENTITLEMENT_CHALLENGE_FUNCTIONS.issue,
      coreArguments: "text,text,text,text,jsonb,text,interval,text",
      coreInvocation:
        `${ENTITLEMENT_CHALLENGE_FUNCTIONS.issue.invocation},`
        + `'${entitlementAdminRole}'::text`,
      grantedRole: runtime,
      revokedRole: admin,
    },
    {
      ...ENTITLEMENT_CHALLENGE_FUNCTIONS.plan,
      coreArguments: ENTITLEMENT_CHALLENGE_FUNCTIONS.plan.arguments,
      coreInvocation: ENTITLEMENT_CHALLENGE_FUNCTIONS.plan.invocation,
      grantedRole: admin,
      revokedRole: runtime,
    },
    {
      ...ENTITLEMENT_CHALLENGE_FUNCTIONS.apply,
      coreArguments: ENTITLEMENT_CHALLENGE_FUNCTIONS.apply.arguments,
      coreInvocation: ENTITLEMENT_CHALLENGE_FUNCTIONS.apply.invocation,
      grantedRole: admin,
      revokedRole: runtime,
    },
  ] as const;

  for (const definition of definitions) {
    const wrapper = `${api}.${quotedIdentifier(definition.publicName)}`;
    const core = `${app}.${quotedIdentifier(definition.coreName)}`;
    const applicationPublic = `${
      app
    }.${quotedIdentifier(definition.publicName)}`;
    await client.query(
      `CREATE OR REPLACE FUNCTION ${wrapper}(${definition.parameters})
       RETURNS jsonb
       LANGUAGE sql
       SECURITY DEFINER
       SET search_path=pg_catalog,${app},pg_temp
       AS $agentops_entitlement_api$
         SELECT ${core}(${definition.coreInvocation})
       $agentops_entitlement_api$`,
    );
    await client.query(
      `ALTER FUNCTION ${wrapper}(${definition.arguments})
       OWNER TO ${functionOwner}`,
    );
    await client.query(
      `REVOKE ALL ON FUNCTION ${wrapper}(${definition.arguments})
       FROM PUBLIC,${runtime},${admin}`,
    );
    await client.query(
      `GRANT EXECUTE ON FUNCTION ${wrapper}(${definition.arguments})
       TO ${definition.grantedRole}`,
    );
    await client.query(
      `REVOKE ALL ON FUNCTION ${applicationPublic}(${definition.arguments})
       FROM PUBLIC,${runtime},${admin}`,
    );
    await client.query(
      `REVOKE ALL ON FUNCTION ${core}(${definition.coreArguments})
       FROM PUBLIC,${runtime},${admin}`,
    );
    await client.query(
      `REVOKE EXECUTE ON FUNCTION ${wrapper}(${definition.arguments})
       FROM ${definition.revokedRole}`,
    );
  }
}

export async function provisionPostgresRuntimeRoleBoundary(
  client: ClientBase,
  context: RuntimeBoundaryContext & Readonly<{
    runtimePassword: string;
    entitlementAdminRole: string;
    entitlementAdminPassword: string;
  }>,
): Promise<void> {
  const applicationSchema = safeIdentifier(
    context.applicationSchema,
    "postgres_schema_invalid",
  );
  const runtimeApiSchema = safeIdentifier(
    context.runtimeApiSchema,
    "postgres_runtime_api_schema_invalid",
  );
  const runtimeRole = safeIdentifier(
    String(context.runtimeRole || ""),
    "postgres_runtime_role_invalid",
  );
  const entitlementAdminRole = safeIdentifier(
    context.entitlementAdminRole,
    "postgres_entitlement_admin_role_invalid",
  );
  if (runtimeRole === entitlementAdminRole) {
    throw new SchemaReadinessError("postgres_runtime_and_admin_roles_must_differ");
  }
  const functionOwnerRole = derivedPostgresFunctionOwnerRole(
    applicationSchema,
    runtimeApiSchema,
  );
  let principal: Awaited<ReturnType<typeof currentDatabasePrincipal>>;
  try {
    principal = await assertMigrationAuthority(client, applicationSchema);
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError("postgres_migration_authority_check_failed");
  }
  try {
    await ensureRuntimeRole(
      client,
      principal.role_name,
      runtimeRole,
      context.runtimePassword,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError("postgres_runtime_role_provision_failed");
  }
  try {
    await ensureRuntimeRole(
      client,
      principal.role_name,
      entitlementAdminRole,
      context.entitlementAdminPassword,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_role_provision_failed",
    );
  }
  try {
    await ensureFunctionOwnerRole(
      client,
      principal.role_name,
      functionOwnerRole,
      [runtimeRole, entitlementAdminRole],
    );
    await ensureRuntimeApiSchema(
      client,
      runtimeApiSchema,
      principal.role_name,
    );
    await prepareFunctionOwner(
      client,
      applicationSchema,
      runtimeApiSchema,
      principal.role_name,
      functionOwnerRole,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(
      "postgres_function_owner_provision_failed",
    );
  }
  try {
    await installRuntimeCostApi(
      client,
      applicationSchema,
      runtimeApiSchema,
      principal.role_name,
      runtimeRole,
      functionOwnerRole,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError("postgres_runtime_cost_api_install_failed");
  }
  try {
    await installEntitlementChallengeApi(
      client,
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
      entitlementAdminRole,
      functionOwnerRole,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(
      "postgres_entitlement_challenge_api_install_failed",
    );
  }
  try {
    await transferApplicationSecurityDefiners(
      client,
      applicationSchema,
      functionOwnerRole,
    );
    await restrictApplicationSecurityDefinerExecute(
      client,
      applicationSchema,
    );
    await installEntitlementAdminPrivileges(
      client,
      applicationSchema,
      runtimeApiSchema,
      entitlementAdminRole,
    );
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(
      "postgres_entitlement_admin_grants_failed",
    );
  }
  try {
    await finalizeFunctionOwner(
      client,
      applicationSchema,
      runtimeApiSchema,
      principal.role_name,
      functionOwnerRole,
    );
    if (
      !(await restrictedFunctionOwnerBoundary(
        client,
        functionOwnerRole,
        applicationSchema,
        runtimeApiSchema,
      ))
    ) {
      throw new SchemaReadinessError(
        "postgres_function_owner_restriction_invalid",
      );
    }
    if (
      !(await exactRuntimeApiFunctionSet(client, {
        runtimeApiSchema,
        functionOwnerRole,
        runtimeRole,
        entitlementAdminRole,
      }))
    ) {
      throw new SchemaReadinessError(
        "postgres_runtime_api_function_set_invalid",
      );
    }
  } catch (error) {
    if (error instanceof SchemaReadinessError) throw error;
    throw new SchemaReadinessError(
      "postgres_function_owner_finalize_failed",
    );
  }
}

export async function assertExpectedSchemaFingerprint(
  client: ClientBase,
  applicationSchema?: string,
): Promise<SchemaFingerprintReceipt> {
  const previousSearchPath = await client.query<{ search_path: string }>(
    "SELECT current_setting('search_path') AS search_path",
  );
  const schema = applicationSchema
    || String(process.env.AGENTOPS_POSTGRES_SCHEMA || "").trim()
    || undefined;
  let actual: SchemaFingerprintReceipt;
  try {
    if (schema) await setLocalSearchPath(client, [schema]);
    actual = await computeSchemaFingerprint(client);
  } catch {
    throw new SchemaReadinessError("schema_fingerprint_check_failed");
  } finally {
    const previous = previousSearchPath.rows[0]?.search_path;
    if (previous) {
      await client.query("SELECT set_config('search_path',$1,true)", [previous]);
    }
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
  options: SchemaCommandOptions = {},
): Promise<SchemaReceipt> {
  const manifest = await loadManifest();
  const client = new Client(clientConfig(operation, options.connectionString));
  await client.connect();
  try {
    await client.query(operation === "check" ? "BEGIN READ ONLY" : "BEGIN");
    let failurePhase = "initialize";
    try {
      failurePhase = "select_schema";
      const applicationSchema = await selectedApplicationSchema(
        client,
        options.applicationSchema,
      );
      const runtimeApiSchema = selectedRuntimeApiSchema(
        options.runtimeApiSchema,
      );
      await setLocalSearchPath(client, [applicationSchema]);
      failurePhase = "advisory_lock";
      await acquireTransactionLock(client);
      failurePhase = "manifest";
      const roleProvisioningContext = operation === "migrate"
        ? resolvedRoleProvisioningContext(
            options,
            applicationSchema,
            runtimeApiSchema,
          )
        : null;
      let preparedMigrationFunctionOwner:
        PreparedMigrationFunctionOwner | null = null;
      if (roleProvisioningContext) {
        failurePhase = "migration_function_owner_prepare";
        preparedMigrationFunctionOwner = await prepareMigrationFunctionOwner(
          client,
          roleProvisioningContext,
        );
      }
      failurePhase = "manifest";
      const counts = operation === "check"
        ? await check(client)
        : await migrate(client, manifest);
      if (preparedMigrationFunctionOwner) {
        failurePhase = "migration_function_owner_finalize";
        await finalizeFunctionOwner(
          client,
          applicationSchema,
          runtimeApiSchema,
          preparedMigrationFunctionOwner.migratorRole,
          preparedMigrationFunctionOwner.functionOwnerRole,
        );
      }
      failurePhase = "relations";
      await assertSchemaRelations(client);
      failurePhase = "fingerprint";
      const fingerprint = await assertExpectedSchemaFingerprint(
        client,
        applicationSchema,
      );
      let databaseRoleBoundaryVerified: boolean | null = null;
      if (operation === "migrate") {
        if (roleProvisioningContext) {
          failurePhase = "role_provision";
          await provisionPostgresRuntimeRoleBoundary(
            client,
            roleProvisioningContext,
          );
        }
      } else if (
        options.enforceRuntimeBoundary ?? isProductionDeployment()
      ) {
        failurePhase = "runtime_role_boundary";
        await assertPostgresRuntimeRoleBoundary(client, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole: selectedRuntimeRole(options.runtimeRole, false)
            || undefined,
        });
        databaseRoleBoundaryVerified = true;
      }
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
        database_role_boundary_verified: databaseRoleBoundaryVerified,
        runtime_role_omitted: true,
        credentials_omitted: true,
        sql_omitted: true,
        row_data_omitted: true,
      };
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      if (error instanceof SchemaReadinessError) throw error;
      throw new SchemaReadinessError(
        operation === "check"
          ? `schema_check_${failurePhase}_failed`
          : `schema_migration_${failurePhase}_failed`,
      );
    }
  } finally {
    await client.end().catch(() => undefined);
  }
}
