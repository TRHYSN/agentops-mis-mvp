import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";

import { Client } from "pg";

import {
  derivedPostgresFunctionOwnerRole,
  runPostgresSchemaCommand,
  type SchemaReceipt,
} from "../src/server/controlPlane/schemaReadiness";

const ROLE_BOUNDARY_ENVIRONMENT_KEYS = Object.freeze([
  "AGENTOPS_DEPLOYMENT_MODE",
  "AGENTOPS_CONTROL_PLANE_MODE",
  "AGENTOPS_POSTGRES_DSN",
  "AGENTOPS_POSTGRES_DSN_FILE",
  "AGENTOPS_POSTGRES_SCHEMA",
  "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA",
  "AGENTOPS_POSTGRES_RUNTIME_ROLE",
] as const);

function quotedIdentifier(value: string) {
  assert.match(value, /^[A-Za-z_][A-Za-z0-9_]{0,62}$/);
  return `"${value}"`;
}

function scopedDsn(
  baseDsn: string,
  input: Readonly<{
    role?: string;
    password?: string;
    searchPath: readonly string[];
  }>,
) {
  const parsed = new URL(baseDsn);
  if (input.role) parsed.username = input.role;
  if (input.password) parsed.password = input.password;
  parsed.searchParams.set(
    "options",
    `-csearch_path=${input.searchPath.join(",")}`,
  );
  return parsed.toString();
}

export async function cleanupPostgresRoleBoundaryFixture(
  baseOwner: Client,
  clients: readonly (Client | undefined)[],
  createdSchemas: readonly string[],
  createdRoles: readonly string[],
  functionOwnerRole: string,
) {
  const errors: unknown[] = [];
  for (const client of [...clients].reverse()) {
    if (!client) continue;
    try {
      await client.end();
    } catch (error) {
      errors.push(error);
    }
  }
  for (const schema of [...createdSchemas].reverse()) {
    try {
      await baseOwner.query(
        `DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`,
      );
    } catch (error) {
      errors.push(error);
    }
  }
  for (const role of [...createdRoles].reverse()) {
    try {
      await baseOwner.query(`DROP OWNED BY ${quotedIdentifier(role)}`);
    } catch (error) {
      errors.push(error);
    }
    if (role === functionOwnerRole) {
      try {
        await baseOwner.query(
          `ALTER DEFAULT PRIVILEGES
           FOR ROLE ${quotedIdentifier(functionOwnerRole)}
           GRANT EXECUTE ON FUNCTIONS TO PUBLIC`,
        );
      } catch (error) {
        errors.push(error);
      }
    }
    try {
      const defaultAcl = await baseOwner.query<{ count: string }>(
        `SELECT count(*)::text AS count
         FROM pg_default_acl default_acl
         JOIN pg_roles owner_role
           ON owner_role.oid=default_acl.defaclrole
         WHERE owner_role.rolname=$1`,
        [role],
      );
      if (defaultAcl.rows[0]?.count !== "0") {
        errors.push(
          new Error("postgres_role_boundary_fixture_default_acl_residue"),
        );
      }
    } catch (error) {
      errors.push(error);
    }
    try {
      await baseOwner.query(
        `DROP ROLE IF EXISTS ${quotedIdentifier(role)}`,
      );
    } catch (error) {
      errors.push(error);
    }
  }
  try {
    const residue = await baseOwner.query<{
      schema_count: string;
      role_count: string;
      default_acl_count: string;
    }>(
      `SELECT
         (
           SELECT count(*)::text
           FROM pg_namespace
           WHERE nspname=ANY($1::text[])
         ) AS schema_count,
         (
           SELECT count(*)::text
           FROM pg_roles
           WHERE rolname=ANY($2::text[])
         ) AS role_count,
         (
           SELECT count(*)::text
           FROM pg_default_acl default_acl
           JOIN pg_roles owner_role
             ON owner_role.oid=default_acl.defaclrole
           WHERE owner_role.rolname=ANY($2::text[])
         ) AS default_acl_count`,
      [createdSchemas, createdRoles],
    );
    if (
      residue.rows[0]?.schema_count !== "0"
      || residue.rows[0]?.role_count !== "0"
      || residue.rows[0]?.default_acl_count !== "0"
    ) {
      errors.push(new Error("postgres_role_boundary_fixture_residue"));
    }
  } catch (error) {
    errors.push(error);
  }
  try {
    await baseOwner.end();
  } catch (error) {
    errors.push(error);
  }
  if (errors.length > 0) {
    throw new AggregateError(
      errors,
      "postgres_role_boundary_fixture_cleanup_failed",
    );
  }
}

export type PostgresRoleBoundaryFixture = Readonly<{
  applicationSchema: string;
  runtimeApiSchema: string;
  runtimeRole: string;
  entitlementAdminRole: string;
  functionOwnerRole: string;
  ownerDsn: string;
  runtimeDsn: string;
  entitlementAdminDsn: string;
  owner: Client;
  migration: SchemaReceipt;
  activateRuntimeEnvironment: () => () => void;
  cleanup: () => Promise<void>;
}>;

export async function createPostgresRoleBoundaryFixture(
  baseDsn: string,
  label: string,
): Promise<PostgresRoleBoundaryFixture> {
  assert.ok(baseDsn, "PostgreSQL contract DSN is required");
  const safeLabel = label.toLowerCase().replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "").slice(0, 16) || "contract";
  const suffix = randomUUID().replaceAll("-", "").slice(0, 10);
  const applicationSchema = `rb_${safeLabel}_${suffix}`;
  const runtimeApiSchema = `rb_api_${suffix}`;
  const runtimeRole = `rb_runtime_${suffix}`;
  const entitlementAdminRole = `rb_ent_admin_${suffix}`;
  const functionOwnerRole = derivedPostgresFunctionOwnerRole(
    applicationSchema,
    runtimeApiSchema,
  );
  const runtimePassword = `${randomBytes(24).toString("base64url")}R1!`;
  const entitlementAdminPassword =
    `${randomBytes(24).toString("base64url")}A1!`;
  const baseOwner = new Client({ connectionString: baseDsn });
  const createdSchemas: string[] = [];
  const createdRoles: string[] = [];
  let owner: Client | undefined;
  let cleaned = false;
  await baseOwner.connect();
  try {
    await baseOwner.query(
      `CREATE SCHEMA ${quotedIdentifier(applicationSchema)}`,
    );
    createdSchemas.push(applicationSchema);
    const ownerDsn = scopedDsn(baseDsn, {
      searchPath: [applicationSchema],
    });
    const migration = await runPostgresSchemaCommand("migrate", {
      connectionString: ownerDsn,
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
      runtimePassword,
      entitlementAdminRole,
      entitlementAdminPassword,
      provisionRoleBoundary: true,
    });
    createdSchemas.push(runtimeApiSchema);
    createdRoles.push(
      runtimeRole,
      entitlementAdminRole,
      functionOwnerRole,
    );
    owner = new Client({ connectionString: ownerDsn });
    await owner.connect();
    const runtimeDsn = scopedDsn(baseDsn, {
      role: runtimeRole,
      password: runtimePassword,
      searchPath: [
        "pg_catalog",
        runtimeApiSchema,
        applicationSchema,
        "pg_temp",
      ],
    });
    const entitlementAdminDsn = scopedDsn(baseDsn, {
      role: entitlementAdminRole,
      password: entitlementAdminPassword,
      searchPath: ["pg_catalog", runtimeApiSchema, "pg_temp"],
    });
    const activateRuntimeEnvironment = () => {
      const original = Object.fromEntries(
        ROLE_BOUNDARY_ENVIRONMENT_KEYS.map((key) => [
          key,
          process.env[key],
        ]),
      );
      process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
      process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
      process.env.AGENTOPS_POSTGRES_DSN = runtimeDsn;
      delete process.env.AGENTOPS_POSTGRES_DSN_FILE;
      process.env.AGENTOPS_POSTGRES_SCHEMA = applicationSchema;
      process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA = runtimeApiSchema;
      process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE = runtimeRole;
      return () => {
        for (const key of ROLE_BOUNDARY_ENVIRONMENT_KEYS) {
          const value = original[key];
          if (value === undefined) delete process.env[key];
          else process.env[key] = value;
        }
      };
    };
    const cleanup = async () => {
      if (cleaned) return;
      cleaned = true;
      await cleanupPostgresRoleBoundaryFixture(
        baseOwner,
        [owner],
        createdSchemas,
        createdRoles,
        functionOwnerRole,
      );
    };
    return {
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
      entitlementAdminRole,
      functionOwnerRole,
      ownerDsn,
      runtimeDsn,
      entitlementAdminDsn,
      owner,
      migration,
      activateRuntimeEnvironment,
      cleanup,
    };
  } catch (error) {
    try {
      await cleanupPostgresRoleBoundaryFixture(
        baseOwner,
        [owner],
        createdSchemas,
        createdRoles,
        functionOwnerRole,
      );
    } catch (cleanupError) {
      throw new AggregateError(
        [error, cleanupError],
        "postgres_role_boundary_fixture_setup_and_cleanup_failed",
      );
    }
    throw error;
  }
}
