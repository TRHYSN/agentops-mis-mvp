import { createRequire } from "node:module";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const nextAppRoot = resolve(process.cwd());
const requireFromNextApp = createRequire(resolve(nextAppRoot, "package.json"));
const { Client } = requireFromNextApp("pg") as typeof import("pg");
const config = await import(pathToFileURL(resolve(
  nextAppRoot,
  "src/server/controlPlane/config.ts",
)).href);
const readiness = await import(pathToFileURL(resolve(
  nextAppRoot,
  "src/server/controlPlane/schemaReadiness.ts",
)).href);

const boundary = process.argv[2];

try {
  if (boundary !== "runtime" && boundary !== "entitlement-admin") {
    throw new Error("boundary_invalid");
  }
  if (
    process.env.AGENTOPS_POSTGRES_DSN
    || process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN
    || process.env.AGENTOPS_POSTGRES_PASSWORD
    || process.env.AGENTOPS_POSTGRES_RUNTIME_PASSWORD
    || process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD
  ) {
    throw new Error("direct_database_secret_forbidden");
  }
  const client = new Client({
    connectionString: boundary === "runtime"
      ? config.postgresDsn()
      : config.postgresEntitlementAdminDsn(),
    application_name: "agentops-byoc-restore-boundary-check",
    ssl: config.postgresSslEnabled()
      ? { rejectUnauthorized: true }
      : undefined,
  });
  await client.connect();
  try {
    await client.query("BEGIN READ ONLY");
    const context = {
      applicationSchema: config.postgresApplicationSchema(),
      runtimeApiSchema: config.postgresRuntimeApiSchema(),
    };
    if (boundary === "runtime") {
      const receipt = await readiness.assertPostgresRuntimeRoleBoundary(client, {
        ...context,
        runtimeRole: config.postgresRuntimeRole(true),
      });
      if (receipt.function_owner_restricted !== true) {
        throw new Error("function_owner_boundary_missing");
      }
    } else {
      const receipt =
        await readiness.assertPostgresEntitlementAdminRoleBoundary(client, {
        ...context,
        entitlementAdminRole: config.postgresEntitlementAdminRole(true),
      });
      if (receipt.function_owner_restricted !== true) {
        throw new Error("function_owner_boundary_missing");
      }
    }
    await client.query("ROLLBACK");
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // The outer fail-closed receipt deliberately omits database error details.
    }
    throw error;
  } finally {
    await client.end();
  }
  console.log(JSON.stringify({
    ok: true,
    contract: "agentops_byoc_restore_role_boundary_v1",
    boundary,
    database_role_boundary_verified: true,
    function_owner_boundary_verified: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
} catch {
  console.error(JSON.stringify({
    ok: false,
    contract: "agentops_byoc_restore_role_boundary_v1",
    error: "restore_role_boundary_check_failed",
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
}
