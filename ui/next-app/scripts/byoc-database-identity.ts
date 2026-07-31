import { Client } from "pg";

import {
  postgresDsn,
  postgresRuntimeRole,
  postgresSslEnabled,
} from "../src/server/controlPlane/config";

const CONTRACT = "agentops_byoc_database_identity_v1";
const SAFE_DATABASE = /^[a-z][a-z0-9_]{0,62}$/;

try {
  const expectedRole = postgresRuntimeRole(true);
  const client = new Client({
    connectionString: postgresDsn(),
    application_name: "agentops-byoc-database-identity",
    ssl: postgresSslEnabled() ? { rejectUnauthorized: true } : undefined,
  });
  await client.connect();
  try {
    await client.query("BEGIN READ ONLY");
    const result = await client.query<{
      authority_database: string;
      runtime_role: string;
    }>(
      `SELECT
         current_database() AS authority_database,
         current_user AS runtime_role`,
    );
    await client.query("ROLLBACK");
    const row = result.rows[0];
    if (
      !row
      || !SAFE_DATABASE.test(row.authority_database)
      || row.authority_database === "postgres"
      || row.runtime_role !== expectedRole
    ) {
      throw new Error("database_identity_invalid");
    }
    console.log(JSON.stringify({
      contract: CONTRACT,
      ok: true,
      authority_database: row.authority_database,
      runtime_role_verified: true,
      database_contacted: true,
      credentials_omitted: true,
      sql_omitted: true,
      row_data_omitted: true,
    }));
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    await client.end();
  }
} catch {
  console.error(JSON.stringify({
    contract: CONTRACT,
    ok: false,
    error_code: "database_identity_unavailable",
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
}
