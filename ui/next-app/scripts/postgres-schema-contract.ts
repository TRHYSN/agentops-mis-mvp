import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { Client } from "pg";

import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SchemaReadinessError,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schemas: string[] = [];
const startScript = fileURLToPath(new URL("./start.mjs", import.meta.url));
const migrationScript = fileURLToPath(new URL("./migrate-postgres.ts", import.meta.url));
const tsxCli = fileURLToPath(new URL("../node_modules/tsx/dist/cli.mjs", import.meta.url));

function quotedIdentifier(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn(schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

function schemaName(label: string) {
  return `agentops_${label}_${randomBytes(6).toString("hex")}`;
}

async function createSchema(admin: Client, label: string) {
  const schema = schemaName(label);
  await admin.query(`CREATE SCHEMA ${quotedIdentifier(schema)}`);
  schemas.push(schema);
  return { connectionString: scopedDsn(schema) };
}

async function expectSchemaError(
  action: () => Promise<unknown>,
  expectedCode: string,
) {
  await assert.rejects(action, (error: unknown) => (
    error instanceof SchemaReadinessError && error.code === expectedCode
  ));
}

async function ledgerSnapshot(connectionString: string) {
  const client = new Client({ connectionString });
  await client.connect();
  try {
    const result = await client.query(
      `SELECT component,version,schema_contract,checksum,applied_at
         FROM agentops_schema_migrations
        ORDER BY component`,
    );
    return JSON.stringify(result.rows);
  } finally {
    await client.end();
  }
}

async function verifyGovernedKnowledgeIndex(connectionString: string) {
  const client = new Client({ connectionString });
  await client.connect();
  try {
    const sourceHash = "a".repeat(64);
    const now = "2026-07-24T00:00:00.000Z";
    for (const [docId, workspaceId, accessLevel] of [
      ["kdoc_global_contract", "global", "internal"],
      ["kdoc_workspace_a_contract", "ws_contract_a", "private"],
      ["kdoc_workspace_b_contract", "ws_contract_b", "private"],
    ]) {
      await client.query(
        `INSERT INTO knowledge_documents(
          doc_id,workspace_id,project_id,access_level,path,title,category,scope,
          source_hash,content_summary,indexed_at,updated_at
        ) VALUES($1,$2,'agentops-mis',$3,'docs/worker-contract.md',
          'Governed worker contract','docs','project',$4,
          'READ PLAN RETRIEVE COMPARE EXECUTE VERIFY RECORD',$5,$5)`,
        [docId, workspaceId, accessLevel, sourceHash, now],
      );
      await client.query(
        `INSERT INTO knowledge_chunks(
          chunk_id,doc_id,workspace_id,project_id,access_level,path,title,
          heading,heading_path,heading_level,chunk_index,source_hash,
          content_summary,indexed_at,updated_at
        ) VALUES($1,$2,$3,'agentops-mis',$4,'docs/worker-contract.md',
          'Governed worker contract','Execution method','Execution method',1,1,
          $5,'governed worker execution evidence',$6,$6)`,
        [
          `kchunk_${workspaceId}_contract`,
          docId,
          workspaceId,
          accessLevel,
          sourceHash,
          now,
        ],
      );
    }

    const visible = await client.query<{ workspace_id: string }>(
      `SELECT workspace_id
      FROM knowledge_chunks
      WHERE workspace_id IN ('global',$1)
        AND search_document @@ plainto_tsquery('simple'::regconfig,$2)
      ORDER BY workspace_id`,
      ["ws_contract_a", "governed worker"],
    );
    assert.deepEqual(
      visible.rows.map((row) => row.workspace_id),
      ["global", "ws_contract_a"],
    );

    await assert.rejects(
      client.query(
        `INSERT INTO knowledge_documents(
          doc_id,workspace_id,access_level,path,title,category,scope,source_hash,
          indexed_at,updated_at
        ) VALUES(
          'kdoc_duplicate_contract','ws_contract_a','private',
          'docs/worker-contract.md','Duplicate','docs','project',$1,$2,$2
        )`,
        [sourceHash, now],
      ),
      (error: unknown) => (error as { code?: string }).code === "23505",
    );
    await assert.rejects(
      client.query(
        `INSERT INTO knowledge_documents(
          doc_id,workspace_id,access_level,path,title,category,scope,source_hash,
          indexed_at,updated_at
        ) VALUES(
          'kdoc_bad_hash_contract','ws_contract_a','private',
          'docs/bad-hash.md','Bad hash','docs','project','not-a-hash',$1,$1
        )`,
        [now],
      ),
      (error: unknown) => (error as { code?: string }).code === "23514",
    );
    await assert.rejects(
      client.query(
        `INSERT INTO knowledge_chunks(
          chunk_id,doc_id,workspace_id,project_id,access_level,path,title,
          heading,heading_path,heading_level,chunk_index,source_hash,
          indexed_at,updated_at
        ) VALUES(
          'kchunk_cross_workspace_contract','kdoc_workspace_a_contract',
          'ws_contract_b','agentops-mis','private','docs/worker-contract.md',
          'Governed worker contract','Cross workspace','Cross workspace',1,2,
          $1,$2,$2
        )`,
        [sourceHash, now],
      ),
      (error: unknown) => (error as { code?: string }).code === "23503",
    );

    const columns = await client.query<{ table_name: string; column_name: string }>(
      `SELECT table_name,column_name
      FROM information_schema.columns
      WHERE table_schema=current_schema()
        AND table_name IN ('knowledge_documents','knowledge_chunks')`,
    );
    assert.equal(
      columns.rows.some((row) => (
        /(^|_)(raw|prompt|response|transcript|credential|token)(_|$)/.test(
          row.column_name,
        )
        || row.column_name === "content"
      )),
      false,
    );
    const searchIndexes = await client.query<{ count: string }>(
      `SELECT COUNT(*)::text AS count
      FROM pg_indexes
      WHERE schemaname=current_schema()
        AND indexname IN (
          'idx_knowledge_documents_search_v7',
          'idx_knowledge_chunks_search_v7'
        )`,
    );
    assert.equal(searchIndexes.rows[0]?.count, "2");
  } finally {
    await client.end();
  }
}

function startCheck(connectionString?: string) {
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    AGENTOPS_DEPLOYMENT_MODE: "production",
    AGENTOPS_CONTROL_PLANE_MODE: "postgres",
    AGENTOPS_NEXT_HOST: "127.0.0.1",
    AGENTOPS_API_BASE: "http://127.0.0.1:1/api",
  };
  if (connectionString) environment.AGENTOPS_POSTGRES_DSN = connectionString;
  else delete environment.AGENTOPS_POSTGRES_DSN;
  return spawnSync(process.execPath, [startScript, "--check"], {
    encoding: "utf8",
    env: environment,
    maxBuffer: 64 * 1024,
  });
}

function migrationCommand(connectionString?: string, checkOnly = false) {
  const environment: NodeJS.ProcessEnv = { ...process.env };
  if (connectionString) environment.AGENTOPS_POSTGRES_DSN = connectionString;
  else delete environment.AGENTOPS_POSTGRES_DSN;
  return spawnSync(
    process.execPath,
    [tsxCli, migrationScript, ...(checkOnly ? ["--check"] : [])],
    {
      encoding: "utf8",
      env: environment,
      maxBuffer: 64 * 1024,
    },
  );
}

function assertBoundedProcessOutput(result: ReturnType<typeof spawnSync>) {
  const output = `${String(result.stdout || "")}${String(result.stderr || "")}`;
  assert.ok(output.length <= 64 * 1024);
  assert.equal(output.includes(baseDsn), false);
  assert.equal(output.includes("postgresql://"), false);
  assert.equal(output.includes("CREATE TABLE"), false);
}

async function runContract() {
  if (!baseDsn) {
    throw new SchemaReadinessError("postgres_dsn_required");
  }
  const admin = new Client({ connectionString: baseDsn });
  await admin.connect();
  try {
    const versionResult = await admin.query<{ server_version_num: string }>(
      "SHOW server_version_num",
    );
    assert.equal(
      Math.floor(Number(versionResult.rows[0]?.server_version_num) / 10_000),
      16,
    );
    const packageJson = JSON.parse(
      await readFile(new URL("../package.json", import.meta.url), "utf8"),
    ) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    assert.equal(packageJson.dependencies?.tsx, "4.23.1");
    assert.equal(packageJson.devDependencies?.tsx, undefined);

    const missingDsnStart = startCheck();
    assert.notEqual(missingDsnStart.status, 0);
    assertBoundedProcessOutput(missingDsnStart);
    const missingDsnMigration = migrationCommand(undefined, true);
    assert.notEqual(missingDsnMigration.status, 0);
    assertBoundedProcessOutput(missingDsnMigration);

    const cli = await createSchema(admin, "cli");
    const cliMigration = migrationCommand(cli.connectionString);
    assert.equal(cliMigration.status, 0);
    assertBoundedProcessOutput(cliMigration);
    const cliMigrationReceipt = JSON.parse(String(cliMigration.stdout).trim()) as {
      applied_count?: number;
    };
    assert.equal(cliMigrationReceipt.applied_count, POSTGRES_MIGRATION_MANIFEST.length);
    const cliCheck = migrationCommand(cli.connectionString, true);
    assert.equal(cliCheck.status, 0);
    assertBoundedProcessOutput(cliCheck);

    const fresh = await createSchema(admin, "fresh");
    const freshReceipt = await runPostgresSchemaCommand(
      "migrate",
      { connectionString: fresh.connectionString },
    );
    assert.equal(freshReceipt.applied_count, POSTGRES_MIGRATION_MANIFEST.length);
    assert.equal(freshReceipt.current_count, 0);
    await verifyGovernedKnowledgeIndex(fresh.connectionString);

    const readyStart = startCheck(fresh.connectionString);
    assert.equal(readyStart.status, 0);
    assertBoundedProcessOutput(readyStart);
    const startReceipt = JSON.parse(String(readyStart.stdout).trim()) as {
      contract?: string;
      schema_ready?: boolean;
      production_python_fallback?: boolean;
    };
    assert.equal(startReceipt.contract, "nextjs_start_boundary_v2");
    assert.equal(startReceipt.schema_ready, true);
    assert.equal(
      (
        startReceipt as typeof startReceipt & {
          schema_fingerprint_verified?: boolean;
        }
      ).schema_fingerprint_verified,
      true,
    );
    assert.equal(startReceipt.production_python_fallback, false);

    const beforeCheck = await ledgerSnapshot(fresh.connectionString);
    const checkReceipt = await runPostgresSchemaCommand(
      "check",
      { connectionString: fresh.connectionString },
    );
    assert.equal(checkReceipt.read_only, true);
    assert.equal(await ledgerSnapshot(fresh.connectionString), beforeCheck);

    const reapplyReceipt = await runPostgresSchemaCommand(
      "migrate",
      { connectionString: fresh.connectionString },
    );
    assert.equal(reapplyReceipt.applied_count, 0);
    assert.equal(reapplyReceipt.current_count, POSTGRES_MIGRATION_MANIFEST.length);

    const tamperClient = new Client({ connectionString: fresh.connectionString });
    await tamperClient.connect();
    try {
      const target = POSTGRES_MIGRATION_MANIFEST[2];
      await tamperClient.query(
        `UPDATE agentops_schema_migrations
            SET checksum=$1
          WHERE component=$2`,
        ["0".repeat(64), target.component],
      );
      await expectSchemaError(
        () => runPostgresSchemaCommand("check", { connectionString: fresh.connectionString }),
        "schema_ledger_mismatch",
      );
      await expectSchemaError(
        () => runPostgresSchemaCommand("migrate", { connectionString: fresh.connectionString }),
        "schema_ledger_mismatch",
      );
      const driftedStart = startCheck(fresh.connectionString);
      assert.notEqual(driftedStart.status, 0);
      assertBoundedProcessOutput(driftedStart);
      await tamperClient.query(
        `UPDATE agentops_schema_migrations
            SET checksum=$1
          WHERE component=$2`,
        [target.checksum, target.component],
      );

      const finalMigration = POSTGRES_MIGRATION_MANIFEST.at(-1);
      assert.ok(finalMigration);
      await tamperClient.query(
        "DELETE FROM agentops_schema_migrations WHERE component=$1",
        [finalMigration.component],
      );
      await expectSchemaError(
        () => runPostgresSchemaCommand("check", { connectionString: fresh.connectionString }),
        "schema_ledger_behind",
      );
    } finally {
      await tamperClient.end();
    }

    const repairedReceipt = await runPostgresSchemaCommand(
      "migrate",
      { connectionString: fresh.connectionString },
    );
    assert.equal(repairedReceipt.applied_count, 1);
    await runPostgresSchemaCommand("check", { connectionString: fresh.connectionString });

    const concurrent = await createSchema(admin, "concurrent");
    const concurrentReceipts = await Promise.all([
      runPostgresSchemaCommand("migrate", { connectionString: concurrent.connectionString }),
      runPostgresSchemaCommand("migrate", { connectionString: concurrent.connectionString }),
    ]);
    assert.deepEqual(
      concurrentReceipts.map((receipt) => receipt.applied_count).sort((a, b) => a - b),
      [0, POSTGRES_MIGRATION_MANIFEST.length],
    );
    await runPostgresSchemaCommand("check", { connectionString: concurrent.connectionString });

    const catalogDrift = await createSchema(admin, "catalog_drift");
    await runPostgresSchemaCommand(
      "migrate",
      { connectionString: catalogDrift.connectionString },
    );
    const catalogDriftClient = new Client({
      connectionString: catalogDrift.connectionString,
    });
    await catalogDriftClient.connect();
    try {
      await catalogDriftClient.query(
        "DROP TRIGGER runtime_events_append_only_v8 ON runtime_events",
      );
    } finally {
      await catalogDriftClient.end();
    }
    await expectSchemaError(
      () => runPostgresSchemaCommand(
        "check",
        { connectionString: catalogDrift.connectionString },
      ),
      "schema_fingerprint_mismatch",
    );
    await expectSchemaError(
      () => runPostgresSchemaCommand(
        "migrate",
        { connectionString: catalogDrift.connectionString },
      ),
      "schema_fingerprint_mismatch",
    );
    const catalogDriftStart = startCheck(catalogDrift.connectionString);
    assert.notEqual(catalogDriftStart.status, 0);
    assertBoundedProcessOutput(catalogDriftStart);

    const missing = await createSchema(admin, "missing");
    const behindStart = startCheck(missing.connectionString);
    assert.notEqual(behindStart.status, 0);
    assertBoundedProcessOutput(behindStart);
    await expectSchemaError(
      () => runPostgresSchemaCommand("check", { connectionString: missing.connectionString }),
      "schema_ledger_missing",
    );
    await runPostgresSchemaCommand("migrate", { connectionString: missing.connectionString });
    const missingClient = new Client({ connectionString: missing.connectionString });
    await missingClient.connect();
    try {
      await missingClient.query("DROP TABLE human_approval_decision_requests");
    } finally {
      await missingClient.end();
    }
    await expectSchemaError(
      () => runPostgresSchemaCommand("check", { connectionString: missing.connectionString }),
      "schema_relation_missing",
    );
    const missingRelationStart = startCheck(missing.connectionString);
    assert.notEqual(missingRelationStart.status, 0);
    assertBoundedProcessOutput(missingRelationStart);

    console.log(JSON.stringify({
      contract: "agentops_postgres_schema_runner_contract_v1",
      ok: true,
      postgres_major: 16,
      manifest_count: POSTGRES_MIGRATION_MANIFEST.length,
      fresh_bootstrap: true,
      idempotent_reapply: true,
      read_only_check: true,
      tampered_ledger_fail_closed: true,
      missing_migration_fail_closed: true,
      missing_relation_fail_closed: true,
      catalog_fingerprint_drift_fail_closed: true,
      concurrent_serialization: true,
      governed_knowledge_workspace_visibility: true,
      governed_knowledge_full_text_index: true,
      governed_knowledge_raw_content_columns_absent: true,
      production_dependency_available: true,
      migration_cli_bounded_receipts: true,
      production_start_fail_closed: true,
      production_start_drift_fail_closed: true,
      production_start_schema_ready: true,
      production_python_fallback: false,
      credentials_omitted: true,
      sql_omitted: true,
      row_data_omitted: true,
    }));
  } finally {
    for (const schema of schemas.reverse()) {
      await admin.query(`DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`);
    }
    await admin.end();
  }
}

runContract().catch(() => {
  console.log(JSON.stringify({
    contract: "agentops_postgres_schema_runner_contract_v1",
    ok: false,
    error_code: "contract_failed",
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
});
