#!/usr/bin/env node

import { constants, openSync, closeSync, fstatSync, readFileSync } from "node:fs";
import { isAbsolute } from "node:path";
import { Client } from "pg";

const SAFE_DATABASE = /^[a-z][a-z0-9_]{0,62}$/;
const SAFE_ROLE = /^[A-Za-z_][A-Za-z0-9_.-]{0,62}$/;

function secretValue(name) {
  const direct = String(process.env[name] || "");
  const path = String(process.env[`${name}_FILE`] || "").trim();
  if (direct && path) throw new Error("database_identity_secret_ambiguous");
  if (!path) return direct;
  if (!isAbsolute(path)) throw new Error("database_identity_secret_path_invalid");
  const descriptor = openSync(
    path,
    constants.O_RDONLY | (constants.O_NOFOLLOW || 0),
  );
  try {
    const metadata = fstatSync(descriptor);
    if (!metadata.isFile() || metadata.size < 1 || metadata.size > 16 * 1024) {
      throw new Error("database_identity_secret_file_invalid");
    }
    const value = readFileSync(descriptor, "utf8").replace(/\r?\n$/, "");
    if (!value || value.includes("\0")) {
      throw new Error("database_identity_secret_value_invalid");
    }
    return value;
  } finally {
    closeSync(descriptor);
  }
}

function connection() {
  const direct = secretValue("AGENTOPS_POSTGRES_DSN").trim();
  if (direct) {
    return {
      connectionString: direct,
      expectedRole: String(process.env.AGENTOPS_POSTGRES_USER || "").trim(),
    };
  }
  const host = String(process.env.AGENTOPS_POSTGRES_HOST || "").trim();
  const port = Number(process.env.AGENTOPS_POSTGRES_PORT || 5432);
  const database = String(process.env.AGENTOPS_POSTGRES_DATABASE || "").trim();
  const user = String(process.env.AGENTOPS_POSTGRES_USER || "").trim();
  const password = secretValue("AGENTOPS_POSTGRES_PASSWORD");
  if (
    !/^[A-Za-z0-9.-]+$/.test(host)
    || !Number.isInteger(port)
    || port < 1
    || port > 65535
    || !SAFE_DATABASE.test(database)
    || !SAFE_ROLE.test(user)
    || !password
  ) {
    throw new Error("database_identity_configuration_invalid");
  }
  return {
    connectionString: `postgresql://${encodeURIComponent(user)}`
      + `:${encodeURIComponent(password)}@${host}:${port}`
      + `/${encodeURIComponent(database)}`,
    expectedRole: user,
  };
}

try {
  const configured = connection();
  if (!SAFE_ROLE.test(configured.expectedRole)) {
    throw new Error("database_identity_role_invalid");
  }
  const client = new Client({
    connectionString: configured.connectionString,
    application_name: "agentops-byoc-historical-database-identity",
    ssl: ["1", "true", "require", "required", "on"].includes(
      String(process.env.AGENTOPS_POSTGRES_SSL || "").trim().toLowerCase(),
    ) ? { rejectUnauthorized: true } : undefined,
  });
  await client.connect();
  try {
    await client.query("BEGIN READ ONLY");
    const result = await client.query(
      "SELECT current_database() AS authority_database,current_user AS runtime_role",
    );
    await client.query("ROLLBACK");
    const row = result.rows[0];
    if (
      !row
      || !SAFE_DATABASE.test(row.authority_database)
      || row.authority_database === "postgres"
      || row.runtime_role !== configured.expectedRole
    ) {
      throw new Error("database_identity_invalid");
    }
    console.log(JSON.stringify({
      contract: "agentops_byoc_database_identity_v1",
      ok: true,
      historical_adapter_contract: "agentops_byoc_historical_schema_adapter_v1",
      authority_database: row.authority_database,
      runtime_role_verified: true,
      database_contacted: true,
      credentials_omitted: true,
      sql_omitted: true,
      row_data_omitted: true,
    }));
  } finally {
    await client.query("ROLLBACK").catch(() => undefined);
    await client.end();
  }
} catch {
  console.error(JSON.stringify({
    contract: "agentops_byoc_database_identity_v1",
    ok: false,
    error_code: "historical_database_identity_unavailable",
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
}
