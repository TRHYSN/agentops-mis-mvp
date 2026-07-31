import assert from "node:assert/strict";
import {
  randomBytes,
  randomUUID,
  scryptSync,
} from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { Client } from "pg";

import {
  cleanupPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";
import {
  HUMAN_SCRYPT_PARAMS,
} from "../src/server/controlPlane/humanPasswordPolicy";
import {
  assertPostgresEntitlementAdminRoleBoundary,
  assertPostgresRuntimeRoleBoundary,
  derivedPostgresFunctionOwnerRole,
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SchemaReadinessError,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
const applicationSchema = `role_boundary_${suffix}`;
const runtimeApiSchema = `role_api_${suffix}`;
const runtimeRole = `role_runtime_${suffix}`;
const entitlementAdminRole = `role_ent_admin_${suffix}`;
const functionOwnerRole = derivedPostgresFunctionOwnerRole(
  applicationSchema,
  runtimeApiSchema,
);
const functionOwnerMembershipProbe = `role_fn_probe_${suffix}`;
const crossDatabaseName = `role_restore_${suffix}`;
const runtimePassword = `${randomBytes(24).toString("base64url")}R1!`;
const entitlementAdminPassword =
  `${randomBytes(24).toString("base64url")}A1!`;
const functionOwnerPasswordProbe =
  `${randomBytes(24).toString("base64url")}F1!`;
const operatorPassword = `${randomBytes(24).toString("base64url")}O1!`;
const operatorId = `usr_role_operator_${suffix}`;
const workspaceId = `ws_role_boundary_${suffix}`;
const agentId = `agt_role_boundary_${suffix}`;
const taskId = `tsk_role_boundary_${suffix}`;
const runId = `run_role_boundary_${suffix}`;
const startScript = fileURLToPath(new URL("./start.mjs", import.meta.url));
const schemaReadinessSource = fileURLToPath(
  new URL(
    "../src/server/controlPlane/schemaReadiness.ts",
    import.meta.url,
  ),
);
let activeCheck = "initialize";

function quotedIdentifier(value: string) {
  assert.match(value, /^[A-Za-z_][A-Za-z0-9_]{0,62}$/);
  return `"${value}"`;
}

function dsnFor(
  role: string,
  password: string,
  searchPath: readonly string[],
) {
  const parsed = new URL(baseDsn);
  parsed.username = role;
  parsed.password = password;
  parsed.searchParams.set(
    "options",
    `-csearch_path=${searchPath.join(",")}`,
  );
  return parsed.toString();
}

function ownerDsn() {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${applicationSchema}`);
  return parsed.toString();
}

function boundedOutput(result: ReturnType<typeof spawnSync>) {
  const output = `${String(result.stdout || "")}${String(result.stderr || "")}`;
  assert(output.length < 64 * 1024);
  assert.equal(output.includes(baseDsn), false);
  assert.equal(output.includes(runtimePassword), false);
  assert.equal(output.includes(entitlementAdminPassword), false);
  assert.equal(output.includes(functionOwnerPasswordProbe), false);
  assert.equal(output.includes("postgresql://"), false);
  return output;
}

function assertPreMigrationFunctionOwnerHandoffSourceContract() {
  const source = readFileSync(schemaReadinessSource, "utf8");
  const commandStart = source.indexOf(
    "export async function runPostgresSchemaCommand(",
  );
  const commandEnd = source.indexOf(
    "\nexport async function ",
    commandStart + 1,
  );
  assert.ok(commandStart >= 0);
  const commandSource = source.slice(
    commandStart,
    commandEnd >= 0 ? commandEnd : undefined,
  );
  const prepareIndex = commandSource.indexOf(
    "await prepareMigrationFunctionOwner(",
  );
  const migrateIndex = commandSource.indexOf(
    "await migrate(client, manifest)",
  );
  const finalizeIndex = commandSource.indexOf(
    "await finalizeFunctionOwner(",
    migrateIndex,
  );
  const provisionIndex = commandSource.indexOf(
    "await provisionPostgresRuntimeRoleBoundary(",
    finalizeIndex,
  );
  assert.ok(prepareIndex >= 0);
  assert.ok(prepareIndex < migrateIndex);
  assert.ok(migrateIndex < finalizeIndex);
  assert.ok(finalizeIndex < provisionIndex);

  const prepareStart = source.indexOf(
    "async function prepareMigrationFunctionOwner(",
  );
  const prepareEnd = source.indexOf(
    "\nasync function installRuntimeCostApi(",
    prepareStart,
  );
  assert.ok(prepareStart >= 0);
  assert.ok(prepareEnd > prepareStart);
  const prepareSource = source.slice(prepareStart, prepareEnd);
  const roleIndex = prepareSource.indexOf("await ensureFunctionOwnerRole(");
  const capabilityIndex = prepareSource.indexOf("await prepareFunctionOwner(");
  assert.ok(roleIndex >= 0);
  assert.ok(roleIndex < capabilityIndex);

  const capabilityStart = source.indexOf(
    "async function prepareFunctionOwner(",
  );
  const capabilityEnd = source.indexOf(
    "\nasync function transferApplicationSecurityDefiners(",
    capabilityStart,
  );
  assert.ok(capabilityStart >= 0);
  assert.ok(capabilityEnd > capabilityStart);
  assert.match(
    source.slice(capabilityStart, capabilityEnd),
    /GRANT \$\{functionOwner\} TO \$\{migrator\}/,
  );
}

function startCheck(
  connectionString: string,
  expectedRuntimeRole?: string,
) {
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    AGENTOPS_DEPLOYMENT_MODE: "production",
    AGENTOPS_CONTROL_PLANE_MODE: "postgres",
    AGENTOPS_NEXT_HOST: "127.0.0.1",
    AGENTOPS_POSTGRES_DSN: connectionString,
    AGENTOPS_POSTGRES_SCHEMA: applicationSchema,
    AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA: runtimeApiSchema,
  };
  delete environment.AGENTOPS_POSTGRES_DSN_FILE;
  delete environment.AGENTOPS_POSTGRES_MIGRATOR_DSN;
  delete environment.AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE;
  if (expectedRuntimeRole) {
    environment.AGENTOPS_POSTGRES_RUNTIME_ROLE = expectedRuntimeRole;
  } else {
    delete environment.AGENTOPS_POSTGRES_RUNTIME_ROLE;
  }
  return spawnSync(process.execPath, [startScript, "--check"], {
    encoding: "utf8",
    env: environment,
    maxBuffer: 64 * 1024,
  });
}

async function expectPermissionDenied(
  client: Client,
  statement: string,
) {
  await assert.rejects(
    client.query(statement),
    (error: unknown) => (
      error !== null
      && typeof error === "object"
      && "code" in error
      && error.code === "42501"
    ),
  );
}

async function expectFunctionOwnerReadinessDenied(
  runtime: Client,
  entitlementAdmin: Client,
) {
  for (const readiness of [
    () => assertPostgresRuntimeRoleBoundary(runtime, {
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
    }),
    () => assertPostgresEntitlementAdminRoleBoundary(
      entitlementAdmin,
      {
        applicationSchema,
        runtimeApiSchema,
        entitlementAdminRole,
      },
    ),
  ]) {
    await assert.rejects(
      readiness(),
      (error: unknown) => (
        error instanceof SchemaReadinessError
        && error.code === "postgres_function_owner_restriction_invalid"
      ),
    );
  }
}

async function expectRuntimeApiFunctionSetDenied(
  runtime: Client,
  entitlementAdmin: Client,
) {
  await assert.rejects(
    assertPostgresRuntimeRoleBoundary(runtime, {
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
    }),
    (error: unknown) => (
      error instanceof SchemaReadinessError
      && error.code === "postgres_runtime_api_function_set_invalid"
    ),
  );
  await assert.rejects(
    assertPostgresEntitlementAdminRoleBoundary(
      entitlementAdmin,
      {
        applicationSchema,
        runtimeApiSchema,
        entitlementAdminRole,
      },
    ),
    (error: unknown) => (
      error instanceof SchemaReadinessError
      && error.code ===
        "postgres_entitlement_admin_api_function_set_invalid"
    ),
  );
}

async function seedOperator(owner: Client) {
  const now = new Date().toISOString();
  const salt = randomBytes(16);
  const passwordHash = scryptSync(
    operatorPassword,
    salt,
    HUMAN_SCRYPT_PARAMS.keylen,
    {
      N: HUMAN_SCRYPT_PARAMS.n,
      r: HUMAN_SCRYPT_PARAMS.r,
      p: HUMAN_SCRYPT_PARAMS.p,
      maxmem: 128 * 1024 * 1024,
    },
  ).toString("hex");
  await owner.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
     VALUES($1,'Role Boundary Operator',$2,'operator',$3)`,
    [operatorId, `${operatorId}@operator.invalid`, now],
  );
  await owner.query(
    `INSERT INTO workspace_memberships(
       workspace_id,user_id,role,status,created_at,updated_at
     ) VALUES($1,$2,'operator','active',$3,$3)`,
    [workspaceId, operatorId, now],
  );
  await owner.query(
    `INSERT INTO human_login_credentials(
       credential_id,user_id,username,password_hash,password_salt,
       password_params_json,status,created_at,updated_at,last_login_at
     ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)`,
    [
      `credential_${operatorId}`,
      operatorId,
      `entitlement-${operatorId}`,
      passwordHash,
      salt.toString("hex"),
      JSON.stringify(HUMAN_SCRYPT_PARAMS),
      now,
    ],
  );
}

async function proveRuntimeOperations(runtime: Client) {
  const now = new Date().toISOString();
  await runtime.query("BEGIN");
  try {
    await runtime.query(
      `INSERT INTO agents(
         agent_id,name,role,description,runtime_type,model_provider,model_name,
         status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
         created_at,updated_at
       ) VALUES(
         $1,'Role Boundary Worker','worker',NULL,'mock','external',
         'role-contract','idle','standard','[]',0,$2,$3,$3
       )`,
      [agentId, operatorId, now],
    );
    await runtime.query(
      `INSERT INTO tasks(
         task_id,workspace_id,title,description,requester_id,owner_agent_id,
         collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
         risk_level,budget_limit_usd,created_at,updated_at
       ) VALUES(
         $1,$2,'Role boundary contract','Bounded fixture.',$3,$4,'[]',
         'planned','high',NULL,'Verify database roles.','medium',0,$5,$5
       )`,
      [taskId, workspaceId, operatorId, agentId, now],
    );
    await runtime.query(
      `SELECT agentops_reserve_run_cost_v10(
         $1,$2,1.000000::numeric,$3,$4,interval '1 hour'
       )`,
      [workspaceId, runId, "a".repeat(64), "b".repeat(64)],
    );
    await runtime.query(
      `INSERT INTO runs(
         run_id,workspace_id,task_id,agent_id,runtime_type,status,billing_class,
         started_at,ended_at,duration_ms,input_summary,output_summary,
         model_provider,model_name,input_tokens,output_tokens,reasoning_tokens,
         cost_usd,error_type,error_message,trace_id,parent_run_id,delegation_id,
         approval_required,agent_plan_id,plan_hash,created_at
       ) VALUES(
         $1,$2,$3,$4,'mock','running','metered_execution',$5,NULL,NULL,
         NULL,NULL,'external','role-contract',0,0,0,0.000000,NULL,NULL,
         NULL,NULL,NULL,0,NULL,NULL,$5
       )`,
      [runId, workspaceId, taskId, agentId, now],
    );
    await runtime.query(
      `SELECT agentops_heartbeat_run_cost_v10(
         $1,$2,0.500000::numeric,interval '1 hour'
       )`,
      [workspaceId, runId],
    );
    await runtime.query(
      `SELECT agentops_settle_run_cost_v10(
         $1,$2,0.500000::numeric,$3,$4
       )`,
      [workspaceId, runId, "c".repeat(64), "d".repeat(64)],
    );
    await runtime.query(
      "UPDATE runs SET status='completed',ended_at=$2 WHERE run_id=$1",
      [runId, now],
    );
    await runtime.query("COMMIT");
  } catch (error) {
    await runtime.query("ROLLBACK");
    throw error;
  }
}

async function run() {
  activeCheck = "base_dsn";
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const owner = new Client({ connectionString: baseDsn });
  const createdRoles: string[] = [];
  const createdSchemas: string[] = [];
  const createdDatabases: string[] = [];
  const cleanupClients: Client[] = [];
  let contractError: unknown;
  let contractFailed = false;
  let successReceipt: Record<string, unknown> | undefined;
  try {
    await owner.connect();
    const version = await owner.query<{ server_version_num: string }>(
      "SHOW server_version_num",
    );
    assert.equal(
      Math.floor(Number(version.rows[0]?.server_version_num) / 10_000),
      16,
    );
    await owner.query(
      `CREATE SCHEMA ${quotedIdentifier(applicationSchema)}`,
    );
    createdSchemas.push(applicationSchema);

    activeCheck = "migrate_and_provision";
    const migration = await runPostgresSchemaCommand("migrate", {
      connectionString: ownerDsn(),
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
      runtimePassword,
      entitlementAdminRole,
      entitlementAdminPassword,
    });
    createdRoles.push(
      runtimeRole,
      entitlementAdminRole,
      functionOwnerRole,
    );
    await owner.query(
      `CREATE ROLE ${quotedIdentifier(functionOwnerMembershipProbe)}
       NOLOGIN NOINHERIT`,
    );
    createdRoles.push(functionOwnerMembershipProbe);
    createdSchemas.push(runtimeApiSchema);
    assert.equal(
      migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );

    const runtimeDsn = dsnFor(
      runtimeRole,
      runtimePassword,
      ["pg_catalog", runtimeApiSchema, applicationSchema, "pg_temp"],
    );
    const entitlementAdminDsn = dsnFor(
      entitlementAdminRole,
      entitlementAdminPassword,
      ["pg_catalog", runtimeApiSchema, "pg_temp"],
    );
    const scopedOwner = new Client({ connectionString: ownerDsn() });
    const runtime = new Client({ connectionString: runtimeDsn });
    const entitlementAdmin = new Client({
      connectionString: entitlementAdminDsn,
    });
    await scopedOwner.connect();
    cleanupClients.push(scopedOwner);
    await runtime.connect();
    cleanupClients.push(runtime);
    await entitlementAdmin.connect();
    cleanupClients.push(entitlementAdmin);
      activeCheck = "function_owner_role_restricted";
      const functionOwner = await scopedOwner.query<{
        login: boolean;
        superuser: boolean;
        create_role: boolean;
        create_db: boolean;
        replication: boolean;
        bypass_rls: boolean;
        inherit: boolean;
        any_role_membership: boolean;
      }>(
        `SELECT
           role_row.rolcanlogin AS login,
           role_row.rolsuper AS superuser,
           role_row.rolcreaterole AS create_role,
           role_row.rolcreatedb AS create_db,
           role_row.rolreplication AS replication,
           role_row.rolbypassrls AS bypass_rls,
           role_row.rolinherit AS inherit,
           EXISTS(
             SELECT 1
             FROM pg_auth_members membership
             WHERE membership.member=role_row.oid
                OR membership.roleid=role_row.oid
           ) AS any_role_membership
         FROM pg_roles role_row
         WHERE role_row.rolname=$1`,
        [functionOwnerRole],
      );
      assert.deepEqual(functionOwner.rows[0], {
        login: false,
        superuser: false,
        create_role: false,
        create_db: false,
        replication: false,
        bypass_rls: false,
        inherit: false,
        any_role_membership: false,
      });
      const relationOwner = await scopedOwner.query<{
        owner_name: string;
      }>(
        `SELECT pg_get_userbyid(relation_row.relowner) AS owner_name
         FROM pg_class relation_row
         JOIN pg_namespace namespace_row
           ON namespace_row.oid=relation_row.relnamespace
         WHERE namespace_row.nspname=$1
           AND relation_row.relname='run_cost_reservations'`,
        [applicationSchema],
      );
      assert.ok(relationOwner.rows[0]?.owner_name);
      assert.notEqual(functionOwnerRole, runtimeRole);
      assert.notEqual(functionOwnerRole, entitlementAdminRole);
      assert.notEqual(
        functionOwnerRole,
        relationOwner.rows[0]?.owner_name,
      );

      activeCheck = "runtime_readiness";
      const runtimeReadiness = await runPostgresSchemaCommand("check", {
        connectionString: runtimeDsn,
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
        enforceRuntimeBoundary: true,
      });
      assert.equal(runtimeReadiness.database_role_boundary_verified, true);
      const boundary = await assertPostgresRuntimeRoleBoundary(runtime, {
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
      });
      assert.equal(boundary.entitlement_direct_dml_forbidden, true);
      assert.equal(boundary.entitlement_read_allowed, true);
      assert.equal(
        boundary.approved_cost_function_integrity_verified,
        true,
      );
      assert.equal(
        boundary.application_function_execute_allowlist_verified,
        true,
      );
      assert.equal(boundary.function_owner_restricted, true);
      assert.equal(boundary.runtime_api_function_set_verified, true);
      assert.equal(boundary.set_role_membership_forbidden, true);
      assert.equal(boundary.pg_temp_shadowing_forbidden, true);

      activeCheck = "pre_migration_function_owner_handoff_source_contract";
      assertPreMigrationFunctionOwnerHandoffSourceContract();
      activeCheck = "function_owner_idempotent_reprovision";
      const setFunctionOwnerPassword = await scopedOwner.query<{
        statement: string;
      }>(
        "SELECT format('ALTER ROLE %I PASSWORD %L',$1::text,$2::text) AS statement",
        [functionOwnerRole, functionOwnerPasswordProbe],
      );
      assert.ok(setFunctionOwnerPassword.rows[0]?.statement);
      await scopedOwner.query(
        String(setFunctionOwnerPassword.rows[0]?.statement),
      );
      const reprovision = await runPostgresSchemaCommand("migrate", {
        connectionString: ownerDsn(),
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
        runtimePassword,
        entitlementAdminRole,
        entitlementAdminPassword,
        provisionRoleBoundary: true,
      });
      assert.equal(reprovision.applied_count, 0);
      assert.equal(
        reprovision.current_count,
        POSTGRES_MIGRATION_MANIFEST.length,
      );
      const clearedPassword = await scopedOwner.query<{
        password_cleared: boolean;
      }>(
        `SELECT rolpassword IS NULL AS password_cleared
         FROM pg_authid
         WHERE rolname=$1`,
        [functionOwnerRole],
      );
      assert.equal(clearedPassword.rows[0]?.password_cleared, true);
      const postMigrationMembership = await scopedOwner.query<{
        membership_count: string;
      }>(
        `SELECT count(*)::text AS membership_count
         FROM pg_auth_members membership
         JOIN pg_roles member_role ON member_role.oid=membership.member
         JOIN pg_roles granted_role ON granted_role.oid=membership.roleid
         WHERE member_role.rolname=$1
           AND granted_role.rolname=$2`,
        [String(relationOwner.rows[0]?.owner_name), functionOwnerRole],
      );
      assert.equal(
        postMigrationMembership.rows[0]?.membership_count,
        "0",
      );
      const runtimeAfterReprovision =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      const adminAfterReprovision =
        await assertPostgresEntitlementAdminRoleBoundary(
          entitlementAdmin,
          {
            applicationSchema,
            runtimeApiSchema,
            entitlementAdminRole,
          },
        );
      assert.equal(
        runtimeAfterReprovision.function_owner_restricted,
        true,
      );
      assert.equal(
        adminAfterReprovision.function_owner_restricted,
        true,
      );

      activeCheck = "function_owner_cross_database_restore";
      await owner.query(
        `CREATE DATABASE ${quotedIdentifier(crossDatabaseName)}`,
      );
      createdDatabases.push(crossDatabaseName);
      const crossDatabaseDsn = new URL(baseDsn);
      crossDatabaseDsn.pathname = `/${crossDatabaseName}`;
      crossDatabaseDsn.searchParams.set(
        "options",
        `-csearch_path=${applicationSchema}`,
      );
      const crossDatabaseOwner = new Client({
        connectionString: crossDatabaseDsn.toString(),
      });
      await crossDatabaseOwner.connect();
      cleanupClients.push(crossDatabaseOwner);
      await crossDatabaseOwner.query(
        `CREATE SCHEMA ${quotedIdentifier(applicationSchema)}`,
      );
      const crossDatabaseMigration = await runPostgresSchemaCommand(
        "migrate",
        {
          connectionString: crossDatabaseDsn.toString(),
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
          runtimePassword,
          entitlementAdminRole,
          entitlementAdminPassword,
          provisionRoleBoundary: true,
        },
      );
      assert.equal(
        crossDatabaseMigration.applied_count,
        POSTGRES_MIGRATION_MANIFEST.length,
      );
      const baseRuntimeWithCrossDatabaseOwner =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      assert.equal(
        baseRuntimeWithCrossDatabaseOwner.function_owner_restricted,
        true,
      );
      const crossDatabaseDomain =
        `${quotedIdentifier(applicationSchema)}.` +
        "\"function_owner_cross_database_probe\"";
      await crossDatabaseOwner.query(
        `CREATE DOMAIN ${crossDatabaseDomain} AS text`,
      );
      await crossDatabaseOwner.query(
        `ALTER DOMAIN ${crossDatabaseDomain}
         OWNER TO ${quotedIdentifier(functionOwnerRole)}`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      } finally {
        await crossDatabaseOwner.query(
          `DROP DOMAIN ${crossDatabaseDomain}`,
        );
      }
      const baseRuntimeAfterCrossDatabaseDrift =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      assert.equal(
        baseRuntimeAfterCrossDatabaseDrift.function_owner_restricted,
        true,
      );

      activeCheck = "function_owner_application_function_set_closed";
      const unexpectedApplicationFunction =
        `${quotedIdentifier(applicationSchema)}.` +
        "\"function_owner_unexpected_probe\"()";
      await scopedOwner.query(
        `CREATE FUNCTION ${unexpectedApplicationFunction}
         RETURNS void
         LANGUAGE sql
         SECURITY DEFINER
         SET search_path=pg_catalog,pg_temp
         AS 'SELECT'`,
      );
      await scopedOwner.query(
        `ALTER FUNCTION ${unexpectedApplicationFunction}
         OWNER TO ${quotedIdentifier(functionOwnerRole)}`,
      );
      await scopedOwner.query(
        `REVOKE ALL ON FUNCTION ${unexpectedApplicationFunction}
         FROM PUBLIC`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      } finally {
        await scopedOwner.query(
          `DROP FUNCTION ${unexpectedApplicationFunction}`,
        );
      }
      const runtimeAfterUnexpectedFunction =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      assert.equal(
        runtimeAfterUnexpectedFunction.function_owner_restricted,
        true,
      );

      activeCheck = "function_owner_stale_execute_acl_repaired";
      const entitlementIssueCore = `${
        quotedIdentifier(applicationSchema)
      }."agentops_issue_entitlement_admin_challenge_core_v11"(` +
        "text,text,text,text,jsonb,text,interval,text)";
      activeCheck = "function_owner_stale_execute_acl_grant";
      const migratorRole = String(relationOwner.rows[0]?.owner_name);
      activeCheck = "function_owner_stale_execute_membership_grant";
      await scopedOwner.query(
        `GRANT ${quotedIdentifier(functionOwnerRole)}
         TO ${quotedIdentifier(migratorRole)}`,
      );
      try {
        await scopedOwner.query(
          `SET ROLE ${quotedIdentifier(functionOwnerRole)}`,
        );
        try {
          activeCheck = "function_owner_stale_execute_acl_grant";
          await scopedOwner.query(
            `GRANT EXECUTE ON FUNCTION ${entitlementIssueCore}
             TO ${quotedIdentifier(functionOwnerMembershipProbe)}`,
          );
        } finally {
          await scopedOwner.query("RESET ROLE");
        }
      } finally {
        activeCheck = "function_owner_stale_execute_membership_revoke";
        await scopedOwner.query(
          `REVOKE ${quotedIdentifier(functionOwnerRole)}
           FROM ${quotedIdentifier(migratorRole)}`,
        );
      }
      activeCheck = "function_owner_stale_execute_acl_denied";
      await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      activeCheck = "function_owner_stale_execute_acl_reprovision";
      const aclRepair = await runPostgresSchemaCommand("migrate", {
        connectionString: ownerDsn(),
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
        runtimePassword,
        entitlementAdminRole,
        entitlementAdminPassword,
        provisionRoleBoundary: true,
      });
      assert.equal(aclRepair.applied_count, 0);
      activeCheck = "function_owner_stale_execute_acl_revoked";
      const staleAcl = await scopedOwner.query<{ executable: boolean }>(
        `SELECT has_function_privilege(
           $1,
           to_regprocedure($2),
           'EXECUTE'
         ) AS executable`,
        [
          functionOwnerMembershipProbe,
          `${applicationSchema}.` +
            "agentops_issue_entitlement_admin_challenge_core_v11(" +
            "text,text,text,text,jsonb,text,interval,text)",
        ],
      );
      assert.equal(staleAcl.rows[0]?.executable, false);
      activeCheck = "function_owner_stale_execute_acl_restored";
      await assertPostgresRuntimeRoleBoundary(runtime, {
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
      });
      await assertPostgresEntitlementAdminRoleBoundary(
        entitlementAdmin,
        {
          applicationSchema,
          runtimeApiSchema,
          entitlementAdminRole,
        },
      );

      activeCheck = "runtime_api_function_set_closed";
      const unexpectedApiFunction = `${
        quotedIdentifier(runtimeApiSchema)
      }."agentops_unexpected_runtime_api_probe_v1"()`;
      await scopedOwner.query(
        `CREATE FUNCTION ${unexpectedApiFunction}
         RETURNS text
         LANGUAGE sql
         SECURITY DEFINER
         SET search_path=pg_catalog,pg_temp
         AS 'SELECT current_user::text'`,
      );
      await scopedOwner.query(
        `REVOKE ALL ON FUNCTION ${unexpectedApiFunction} FROM PUBLIC`,
      );
      await scopedOwner.query(
        `GRANT EXECUTE ON FUNCTION ${unexpectedApiFunction}
         TO ${quotedIdentifier(runtimeRole)}`,
      );
      await expectRuntimeApiFunctionSetDenied(runtime, entitlementAdmin);
      await assert.rejects(
        runPostgresSchemaCommand("migrate", {
          connectionString: ownerDsn(),
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
          runtimePassword,
          entitlementAdminRole,
          entitlementAdminPassword,
          provisionRoleBoundary: true,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code === "postgres_runtime_api_function_set_invalid"
        ),
      );
      await scopedOwner.query(`DROP FUNCTION ${unexpectedApiFunction}`);
      await assertPostgresRuntimeRoleBoundary(runtime, {
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
      });
      await assertPostgresEntitlementAdminRoleBoundary(
        entitlementAdmin,
        {
          applicationSchema,
          runtimeApiSchema,
          entitlementAdminRole,
        },
      );

      activeCheck = "function_owner_login_drift_denied";
      await scopedOwner.query(
        `ALTER ROLE ${quotedIdentifier(functionOwnerRole)} LOGIN`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      } finally {
        await scopedOwner.query(
          `ALTER ROLE ${quotedIdentifier(functionOwnerRole)} NOLOGIN`,
        );
      }
      const restoredRuntimeAfterLogin =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      const restoredAdminAfterLogin =
        await assertPostgresEntitlementAdminRoleBoundary(
          entitlementAdmin,
          {
            applicationSchema,
            runtimeApiSchema,
            entitlementAdminRole,
          },
        );
      assert.equal(
        restoredRuntimeAfterLogin.function_owner_restricted,
        true,
      );
      assert.equal(
        restoredAdminAfterLogin.function_owner_restricted,
        true,
      );

      activeCheck = "function_owner_membership_drift_denied";
      await scopedOwner.query(
        `GRANT ${quotedIdentifier(functionOwnerMembershipProbe)}
         TO ${quotedIdentifier(functionOwnerRole)}`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      } finally {
        await scopedOwner.query(
          `REVOKE ${quotedIdentifier(functionOwnerMembershipProbe)}
           FROM ${quotedIdentifier(functionOwnerRole)}`,
        );
      }
      await scopedOwner.query(
        `GRANT ${quotedIdentifier(functionOwnerRole)}
         TO ${quotedIdentifier(functionOwnerMembershipProbe)}`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
      } finally {
        await scopedOwner.query(
          `REVOKE ${quotedIdentifier(functionOwnerRole)}
           FROM ${quotedIdentifier(functionOwnerMembershipProbe)}`,
        );
      }
      const restoredRuntimeAfterMembership =
        await assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        });
      const restoredAdminAfterMembership =
        await assertPostgresEntitlementAdminRoleBoundary(
          entitlementAdmin,
          {
            applicationSchema,
            runtimeApiSchema,
            entitlementAdminRole,
          },
        );
      assert.equal(
        restoredRuntimeAfterMembership.function_owner_restricted,
        true,
      );
      assert.equal(
        restoredAdminAfterMembership.function_owner_restricted,
        true,
      );

      activeCheck = "function_owner_non_function_object_drift_denied";
      const ownedObjectSchema = `role_owned_object_${suffix}`;
      const ownedDomain = `${
        quotedIdentifier(ownedObjectSchema)
      }."function_owner_domain_probe"`;
      await scopedOwner.query(
        `CREATE SCHEMA ${quotedIdentifier(ownedObjectSchema)}`,
      );
      createdSchemas.push(ownedObjectSchema);
      await scopedOwner.query(
        `CREATE DOMAIN ${ownedDomain} AS text`,
      );
      await scopedOwner.query(
        `ALTER DOMAIN ${ownedDomain}
         OWNER TO ${quotedIdentifier(functionOwnerRole)}`,
      );
      try {
        await expectFunctionOwnerReadinessDenied(runtime, entitlementAdmin);
        await assert.rejects(
          runPostgresSchemaCommand("migrate", {
            connectionString: ownerDsn(),
            applicationSchema,
            runtimeApiSchema,
            runtimeRole,
            runtimePassword,
            entitlementAdminRole,
            entitlementAdminPassword,
            provisionRoleBoundary: true,
          }),
          (error: unknown) => (
            error instanceof SchemaReadinessError
            && error.code ===
              "postgres_function_owner_restriction_invalid"
          ),
        );
      } finally {
        await scopedOwner.query(`DROP DOMAIN ${ownedDomain}`);
        await scopedOwner.query(
          `DROP SCHEMA ${quotedIdentifier(ownedObjectSchema)}`,
        );
      }
      const ownedObjectResidue = await scopedOwner.query<{
        schema_count: string;
        type_count: string;
      }>(
        `SELECT
           (
             SELECT count(*)::text
             FROM pg_namespace
             WHERE nspname=$1
           ) AS schema_count,
           (
             SELECT count(*)::text
             FROM pg_type type_row
             JOIN pg_namespace namespace_row
               ON namespace_row.oid=type_row.typnamespace
             WHERE namespace_row.nspname=$1
           ) AS type_count`,
        [ownedObjectSchema],
      );
      assert.deepEqual(ownedObjectResidue.rows[0], {
        schema_count: "0",
        type_count: "0",
      });
      await assertPostgresRuntimeRoleBoundary(runtime, {
        applicationSchema,
        runtimeApiSchema,
        runtimeRole,
      });
      await assertPostgresEntitlementAdminRoleBoundary(
        entitlementAdmin,
        {
          applicationSchema,
          runtimeApiSchema,
          entitlementAdminRole,
        },
      );

      activeCheck = "runtime_future_function_default_acl_denied";
      const futureFunction = `${
        quotedIdentifier(applicationSchema)
      }."agentops_default_acl_probe_v1"()`;
      await scopedOwner.query(
        `CREATE FUNCTION ${futureFunction}
         RETURNS integer
         LANGUAGE sql
         SET search_path=pg_catalog,${quotedIdentifier(applicationSchema)},pg_temp
         AS 'SELECT 1'`,
      );
      await expectPermissionDenied(runtime, `SELECT ${futureFunction}`);
      await scopedOwner.query(
        `GRANT EXECUTE ON FUNCTION ${futureFunction}
         TO ${quotedIdentifier(runtimeRole)}`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_application_function_allowlist_invalid"
        ),
      );
      await scopedOwner.query(
        `REVOKE EXECUTE ON FUNCTION ${futureFunction}
         FROM ${quotedIdentifier(runtimeRole)}`,
      );
      await scopedOwner.query(`DROP FUNCTION ${futureFunction}`);

      activeCheck = "runtime_set_role_membership_denied";
      await scopedOwner.query(
        `GRANT ${quotedIdentifier(entitlementAdminRole)}
         TO ${quotedIdentifier(runtimeRole)}`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_set_role_membership_forbidden"
        ),
      );
      await scopedOwner.query(
        `REVOKE ${quotedIdentifier(entitlementAdminRole)}
         FROM ${quotedIdentifier(runtimeRole)}`,
      );
      await scopedOwner.query(
        `GRANT ${quotedIdentifier(runtimeRole)}
         TO ${quotedIdentifier(entitlementAdminRole)}`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_set_role_membership_forbidden"
        ),
      );
      await scopedOwner.query(
        `REVOKE ${quotedIdentifier(runtimeRole)}
         FROM ${quotedIdentifier(entitlementAdminRole)}`,
      );

      activeCheck = "runtime_wrapper_integrity_drift_denied";
      const expireIdentity = `${
        quotedIdentifier(runtimeApiSchema)
      }."agentops_expire_run_cost_reservations_v10"(text)`;
      await scopedOwner.query(
        `ALTER FUNCTION ${expireIdentity} SECURITY INVOKER`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_cost_function_integrity_invalid"
        ),
      );
      await scopedOwner.query(
        `ALTER FUNCTION ${expireIdentity} SECURITY DEFINER`,
      );
      const restoredBoundary = await assertPostgresRuntimeRoleBoundary(
        runtime,
        {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        },
      );
      assert.equal(
        restoredBoundary.approved_cost_function_integrity_verified,
        true,
      );
      activeCheck = "runtime_wrapper_extra_acl_denied";
      await scopedOwner.query(
        `GRANT EXECUTE ON FUNCTION ${expireIdentity}
         TO ${quotedIdentifier(entitlementAdminRole)}`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_cost_function_integrity_invalid"
        ),
      );
      await scopedOwner.query(
        `REVOKE EXECUTE ON FUNCTION ${expireIdentity}
         FROM ${quotedIdentifier(entitlementAdminRole)}`,
      );
      activeCheck = "bound_admin_elevated_role_denied";
      await scopedOwner.query(
        `ALTER ROLE ${quotedIdentifier(entitlementAdminRole)} CREATEDB`,
      );
      await assert.rejects(
        assertPostgresRuntimeRoleBoundary(runtime, {
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code ===
            "postgres_runtime_entitlement_challenge_issue_integrity_invalid"
        ),
      );
      await scopedOwner.query(
        `ALTER ROLE ${quotedIdentifier(entitlementAdminRole)} NOCREATEDB`,
      );

      activeCheck = "seed_operator";
      await seedOperator(scopedOwner);
      activeCheck = "admin_role_boundary";
      const adminBoundary = await assertPostgresEntitlementAdminRoleBoundary(
        entitlementAdmin,
        {
          applicationSchema,
          runtimeApiSchema,
          entitlementAdminRole,
        },
      );
      assert.equal(adminBoundary.application_relation_access_forbidden, true);
      assert.equal(adminBoundary.plan_apply_executable, true);
      assert.equal(adminBoundary.function_owner_restricted, true);
      assert.equal(adminBoundary.runtime_api_function_set_verified, true);
      activeCheck = "owner_entitlement_seed";
      const now = new Date();
      await scopedOwner.query(
        `INSERT INTO workspace_entitlements(
           workspace_id,edition,status,capabilities_json,max_agents,
           max_active_enrollments,max_active_sessions_per_agent,
           max_concurrent_runs,max_monthly_runs,max_monthly_cost_usd,
           effective_at,expires_at,updated_by_user_id
         ) VALUES(
           $1,'team_governance','active',$2::jsonb,10,10,5,3,100,
           100.000000,$3,$4,$5
         )`,
        [
          workspaceId,
          JSON.stringify({
            enrollment_issue: true,
            session_issue: true,
            run_start: true,
          }),
          new Date(now.getTime() - 60_000).toISOString(),
          new Date(now.getTime() + 86_400_000).toISOString(),
          operatorId,
        ],
      );
      const runtimeEntitlementRead = await runtime.query<{ status: string }>(
        `SELECT status
         FROM workspace_entitlements
         WHERE workspace_id=$1`,
        [workspaceId],
      );
      assert.equal(runtimeEntitlementRead.rows[0]?.status, "active");

      activeCheck = "runtime_entitlement_dml_denied";
      await expectPermissionDenied(
        runtime,
        `UPDATE workspace_entitlements
         SET max_monthly_cost_usd=999999999999.999999
         WHERE workspace_id='${workspaceId}'`,
      );
      await expectPermissionDenied(
        runtime,
        "INSERT INTO workspace_entitlements DEFAULT VALUES",
      );
      await expectPermissionDenied(
        runtime,
        "DELETE FROM workspace_entitlements WHERE false",
      );
      await expectPermissionDenied(
        runtime,
        "TRUNCATE workspace_entitlements",
      );
      await expectPermissionDenied(
        runtime,
        "UPDATE run_cost_reservations SET state=state WHERE false",
      );
      await expectPermissionDenied(
        runtime,
        "UPDATE agentops_schema_migrations SET checksum=checksum WHERE false",
      );

      activeCheck = "runtime_normal_and_cost_operations";
      await runtime.query(
        "CREATE TEMP TABLE run_cost_reservations(shadow_marker text)",
      );
      const tempShadowResult = await runtime.query<{ expired_count: string }>(
        `SELECT agentops_expire_run_cost_reservations_v10($1)::text
           AS expired_count`,
        [workspaceId],
      );
      assert.equal(tempShadowResult.rows[0]?.expired_count, "0");
      await runtime.query("DROP TABLE pg_temp.run_cost_reservations");
      await proveRuntimeOperations(runtime);

      activeCheck = "admin_sensitive_dml_denied";
      const application = quotedIdentifier(applicationSchema);
      for (const statement of [
        `UPDATE ${application}.run_cost_reservations
         SET state=state WHERE false`,
        `UPDATE ${application}.agentops_schema_migrations
         SET checksum=checksum WHERE false`,
        `UPDATE ${application}.runs SET status=status WHERE false`,
        `DELETE FROM ${application}.run_cost_reservations WHERE false`,
        `TRUNCATE ${application}.run_cost_reservations`,
      ]) {
        await expectPermissionDenied(entitlementAdmin, statement);
      }
      activeCheck = "runtime_migration_denied";
      await assert.rejects(
        runPostgresSchemaCommand("migrate", {
          connectionString: runtimeDsn,
          applicationSchema,
          runtimeApiSchema,
          runtimeRole,
          runtimePassword,
          entitlementAdminRole,
          entitlementAdminPassword,
        }),
        (error: unknown) => (
          error instanceof SchemaReadinessError
          && error.code === "postgres_migration_role_not_owner"
        ),
      );

      activeCheck = "startup_owner_denied";
      const ownerStart = startCheck(ownerDsn());
      assert.notEqual(ownerStart.status, 0);
      boundedOutput(ownerStart);
      activeCheck = "startup_runtime_ready";
      const runtimeStart = startCheck(runtimeDsn, runtimeRole);
      assert.equal(runtimeStart.status, 0, boundedOutput(runtimeStart));
      const startReceipt = JSON.parse(
        String(runtimeStart.stdout || "").trim(),
      ) as {
        database_role_boundary_verified?: boolean;
      };
      assert.equal(startReceipt.database_role_boundary_verified, true);

      successReceipt = {
        contract: "agentops_postgres_role_boundary_v1",
        ok: true,
        postgres_major: 16,
        migrator_runtime_distinct: true,
        runtime_relation_owner_excluded: true,
        runtime_reservation_direct_dml_forbidden: true,
        runtime_entitlement_direct_dml_forbidden: true,
        runtime_ledger_direct_dml_forbidden: true,
        runtime_approved_cost_functions_executed: true,
        runtime_cost_function_integrity_verified: true,
        runtime_application_function_allowlist_verified: true,
        function_owner_restricted: true,
        function_owner_distinct: true,
        function_owner_idempotent_reprovision: true,
        pre_migration_owner_handoff_source_order_verified: true,
        post_migration_owner_membership_zero: true,
        function_owner_password_cleared: true,
        function_owner_stale_execute_acl_repaired: true,
        runtime_api_function_set_closed: true,
        function_owner_login_drift_denied: true,
        function_owner_membership_drift_denied: true,
        function_owner_cross_database_restore_allowed: true,
        function_owner_cross_database_non_function_drift_denied: true,
        function_owner_application_function_set_closed: true,
        function_owner_non_function_object_drift_denied: true,
        function_owner_non_function_object_drift_restored: true,
        function_owner_readiness_restored: true,
        runtime_pg_temp_shadowing_forbidden: true,
        runtime_set_role_membership_forbidden: true,
        runtime_normal_application_operations_executed: true,
        runtime_entitlement_read_allowed: true,
        runtime_migration_forbidden: true,
        entitlement_admin_distinct: true,
        entitlement_admin_role_boundary_verified: true,
        entitlement_admin_plan_apply_only: true,
        entitlement_admin_application_access_forbidden: true,
        owner_entitlement_seeded_for_runtime_read: true,
        entitlement_admin_reservation_dml_forbidden: true,
        entitlement_admin_ledger_dml_forbidden: true,
        entitlement_admin_runs_dml_forbidden: true,
        owner_startup_fail_closed: true,
        restricted_runtime_startup_ready: true,
        credentials_omitted: true,
        role_names_omitted: true,
        sql_omitted: true,
        row_data_omitted: true,
        python_used: false,
        sqlite_used: false,
      };
  } catch (error) {
    contractFailed = true;
    contractError = error;
  }
  try {
    await cleanupPostgresRoleBoundaryFixture(
      owner,
      cleanupClients,
      createdSchemas,
      createdRoles,
      functionOwnerRole,
      createdDatabases,
    );
  } catch (cleanupError) {
    if (contractFailed) {
      throw new AggregateError(
        [contractError, cleanupError],
        "postgres_role_boundary_contract_and_cleanup_failed",
      );
    }
    throw cleanupError;
  }
  if (contractFailed) throw contractError;
  assert.ok(successReceipt);
  successReceipt.cleanup_catalog_zero = true;
  console.log(JSON.stringify(successReceipt));
}

run().catch((error: unknown) => {
  console.log(JSON.stringify({
    contract: "agentops_postgres_role_boundary_v1",
    ok: false,
    error_code: "postgres_role_boundary_contract_failed",
    failed_check: activeCheck,
    detail_code: error instanceof SchemaReadinessError
      ? error.code
      : "contract_assertion_failed",
    credentials_omitted: true,
    role_names_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
