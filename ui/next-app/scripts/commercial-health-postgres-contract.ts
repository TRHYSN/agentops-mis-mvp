import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { Client } from "pg";

import { GET as commercialHealth } from "../app/api/mis/health/route";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schema =
  `commercial_health_${randomUUID().replaceAll("-", "")}`;

function quotedIdentifier(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn() {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

async function responseBody(response: Response) {
  return await response.json() as Record<string, unknown>;
}

async function run() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const original = {
    dsn: process.env.AGENTOPS_POSTGRES_DSN,
    dsnFile: process.env.AGENTOPS_POSTGRES_DSN_FILE,
    deployment: process.env.AGENTOPS_DEPLOYMENT_MODE,
    mode: process.env.AGENTOPS_CONTROL_PLANE_MODE,
  };
  const admin = new Client({ connectionString: baseDsn });
  await admin.connect();
  let schemaCreated = false;
  try {
    await admin.query(`CREATE SCHEMA ${quotedIdentifier(schema)}`);
    schemaCreated = true;
    const connectionString = scopedDsn();
    await runPostgresSchemaCommand("migrate", { connectionString });
    process.env.AGENTOPS_POSTGRES_DSN = connectionString;
    delete process.env.AGENTOPS_POSTGRES_DSN_FILE;
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";

    const readyResponse = await commercialHealth();
    const readyBody = await responseBody(readyResponse);
    assert.equal(readyResponse.status, 200);
    assert.equal(readyBody.ok, true);
    assert.equal(readyBody.status, "ready");
    assert.equal(readyBody.control_plane, "typescript_postgres");
    assert.equal(readyBody.schema_contract, SCHEMA_CONTRACT);
    assert.equal(readyBody.schema_ready, true);
    assert.equal(readyBody.schema_fingerprint_verified, true);
    assert.equal(readyBody.python_proxy_performed, false);
    assert.equal(readyBody.sqlite_used, false);

    const driftClient = new Client({ connectionString });
    await driftClient.connect();
    try {
      await driftClient.query(
        "DROP TRIGGER runtime_events_append_only_v8 ON runtime_events",
      );
    } finally {
      await driftClient.end().catch(() => undefined);
    }

    const driftResponse = await commercialHealth();
    const driftBody = await responseBody(driftResponse);
    assert.equal(driftResponse.status, 503);
    assert.equal(driftBody.ok, false);
    assert.equal(driftBody.status, "not_ready");
    assert.equal(driftBody.error, "schema_not_ready");
    assert.equal(driftBody.python_proxy_performed, false);
    assert.equal(driftBody.sqlite_used, false);

    console.log(JSON.stringify({
      ok: true,
      contract: "agentops_commercial_health_postgres_contract_v1",
      exact_catalog_ready: true,
      catalog_drift_returns_503: true,
      schema_contract: SCHEMA_CONTRACT,
      schema_fingerprint_verified: true,
      typescript_postgres_owner: true,
      python_used: false,
      sqlite_used: false,
      credentials_omitted: true,
      row_data_omitted: true,
    }));
  } finally {
    await closeControlPlanePoolForTests();
    if (schemaCreated) {
      await admin.query(
        `DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`,
      );
    }
    await admin.end().catch(() => undefined);
    const restore = (key: string, value: string | undefined) => {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    };
    restore("AGENTOPS_POSTGRES_DSN", original.dsn);
    restore("AGENTOPS_POSTGRES_DSN_FILE", original.dsnFile);
    restore("AGENTOPS_DEPLOYMENT_MODE", original.deployment);
    restore("AGENTOPS_CONTROL_PLANE_MODE", original.mode);
  }
}

run().catch(() => {
  console.log(JSON.stringify({
    ok: false,
    contract: "agentops_commercial_health_postgres_contract_v1",
    error_code: "commercial_health_contract_failed",
    python_used: false,
    sqlite_used: false,
    credentials_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
});
