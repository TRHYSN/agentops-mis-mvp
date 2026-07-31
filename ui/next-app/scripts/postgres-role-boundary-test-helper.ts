import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";

import { Client } from "pg";

import {
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

export type PostgresRoleBoundaryFixture = Readonly<{
  applicationSchema: string;
  runtimeApiSchema: string;
  runtimeRole: string;
  entitlementAdminRole: string;
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
  const runtimePassword = `${randomBytes(24).toString("base64url")}R1!`;
  const entitlementAdminPassword =
    `${randomBytes(24).toString("base64url")}A1!`;
  const baseOwner = new Client({ connectionString: baseDsn });
  const createdSchemas: string[] = [];
  const createdRoles: string[] = [];
  let owner: Client | undefined;
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
    createdRoles.push(runtimeRole, entitlementAdminRole);
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
      await owner?.end().catch(() => undefined);
      for (const schema of createdSchemas.reverse()) {
        await baseOwner.query(
          `DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`,
        ).catch(() => undefined);
      }
      for (const role of createdRoles.reverse()) {
        await baseOwner.query(
          `DROP OWNED BY ${quotedIdentifier(role)}`,
        ).catch(() => undefined);
        await baseOwner.query(
          `DROP ROLE IF EXISTS ${quotedIdentifier(role)}`,
        ).catch(() => undefined);
      }
      await baseOwner.end().catch(() => undefined);
    };
    return {
      applicationSchema,
      runtimeApiSchema,
      runtimeRole,
      entitlementAdminRole,
      ownerDsn,
      runtimeDsn,
      entitlementAdminDsn,
      owner,
      migration,
      activateRuntimeEnvironment,
      cleanup,
    };
  } catch (error) {
    await owner?.end().catch(() => undefined);
    for (const schema of createdSchemas.reverse()) {
      await baseOwner.query(
        `DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`,
      ).catch(() => undefined);
    }
    for (const role of createdRoles.reverse()) {
      await baseOwner.query(
        `DROP OWNED BY ${quotedIdentifier(role)}`,
      ).catch(() => undefined);
      await baseOwner.query(
        `DROP ROLE IF EXISTS ${quotedIdentifier(role)}`,
      ).catch(() => undefined);
    }
    await baseOwner.end().catch(() => undefined);
    throw error;
  }
}
