import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { Client } from "pg";

import {
  computeSchemaFingerprint,
  SCHEMA_FINGERPRINT_CONTRACT,
} from "../src/server/controlPlane/schemaFingerprint";
import { runPostgresSchemaCommand } from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schema = `schema_fingerprint_${randomUUID().replaceAll("-", "")}`;
const comparisonSchema =
  `schema_fingerprint_${randomUUID().replaceAll("-", "")}`;

function quotedIdentifier(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

function scopedDsn(schemaName = schema) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schemaName}`);
  return parsed.toString();
}

async function run() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const admin = new Client({ connectionString: baseDsn });
  await admin.connect();
  const createdSchemas: string[] = [];
  try {
    await admin.query(`CREATE SCHEMA ${quotedIdentifier(schema)}`);
    createdSchemas.push(schema);
    const connectionString = scopedDsn();
    const migration = await runPostgresSchemaCommand(
      "migrate",
      { connectionString },
    );
    const client = new Client({ connectionString });
    await client.connect();
    try {
      const baseline = await computeSchemaFingerprint(client);
      const repeated = await computeSchemaFingerprint(client);
      assert.equal(baseline.contract, SCHEMA_FINGERPRINT_CONTRACT);
      assert.match(baseline.sha256, /^[a-f0-9]{64}$/);
      assert.deepEqual(repeated, baseline);
      assert((baseline.object_counts.trigger || 0) > 0);
      assert((baseline.object_counts.function || 0) > 0);
      assert((baseline.object_counts.constraint || 0) > 0);

      await client.query(
        `INSERT INTO users(user_id,name,email,role,created_at)
        VALUES('usr_schema_fingerprint','Fingerprint Contract',
          'fingerprint@example.invalid','owner',
          '2026-07-24T00:00:00.000Z')`,
      );
      const afterRowWrite = await computeSchemaFingerprint(client);
      assert.deepEqual(afterRowWrite, baseline);

      await client.query("BEGIN");
      try {
        await client.query(
          "DROP TRIGGER runtime_events_append_only_v8 ON runtime_events",
        );
        const missingAppendOnlyTrigger = await computeSchemaFingerprint(client);
        assert.notEqual(missingAppendOnlyTrigger.sha256, baseline.sha256);
        assert.equal(
          missingAppendOnlyTrigger.object_counts.trigger,
          baseline.object_counts.trigger - 1,
        );
      } finally {
        await client.query("ROLLBACK");
      }
      const restored = await computeSchemaFingerprint(client);
      assert.deepEqual(restored, baseline);

      await admin.query(`CREATE SCHEMA ${quotedIdentifier(comparisonSchema)}`);
      createdSchemas.push(comparisonSchema);
      const comparisonConnectionString = scopedDsn(comparisonSchema);
      await runPostgresSchemaCommand(
        "migrate",
        { connectionString: comparisonConnectionString },
      );
      const comparisonClient = new Client({
        connectionString: comparisonConnectionString,
      });
      await comparisonClient.connect();
      try {
        const comparison = await computeSchemaFingerprint(comparisonClient);
        assert.deepEqual(comparison, baseline);
      } finally {
        await comparisonClient.end().catch(() => undefined);
      }

      const receipt = {
        ok: true,
        contract: "agentops_schema_fingerprint_postgres_contract_v1",
        postgres_major: 16,
        schema_contract: migration.schema_contract,
        fingerprint_contract: baseline.contract,
        object_count: baseline.object_count,
        deterministic: true,
        schema_name_independent: true,
        row_data_independent: true,
        append_only_trigger_drift_detected: true,
        catalog_only: baseline.catalog_only,
        row_data_omitted: baseline.row_data_omitted,
        credentials_omitted: baseline.credentials_omitted,
        sql_omitted: baseline.sql_omitted,
        python_used: false,
        sqlite_used: false,
      };
      const serialized = JSON.stringify(receipt);
      assert.equal(serialized.includes(baseDsn), false);
      assert.equal(serialized.includes("postgresql://"), false);
      assert.equal(serialized.includes(schema), false);
      console.log(serialized);
    } finally {
      await client.end().catch(() => undefined);
    }
  } finally {
    for (const schemaName of createdSchemas.reverse()) {
      await admin.query(
        `DROP SCHEMA IF EXISTS ${quotedIdentifier(schemaName)} CASCADE`,
      );
    }
    await admin.end().catch(() => undefined);
  }
}

run().catch((error: unknown) => {
  console.log(JSON.stringify({
    ok: false,
    contract: "agentops_schema_fingerprint_postgres_contract_v1",
    error: error instanceof Error ? error.message : "contract_failed",
    credentials_omitted: true,
    row_data_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
